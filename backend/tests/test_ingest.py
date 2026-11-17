"""Ingestion: validation at the edge, exactly-once application in the worker, ordering."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import dramatiq
import httpx
import pytest
from sqlalchemy import func, select

from observenet.core.db import session_scope
from observenet.core.redis import CURRENT_HASH, get_redis
from observenet.core.time import utcnow
from observenet.models import (
    Capability,
    CollectorHeartbeat,
    DataSource,
    Finding,
    IngestBatch,
    MetricSample,
    MetricSeries,
    PostureSnapshot,
    ProbeResult,
)
from observenet.workers import tasks
from observenet.workers.broker import broker
from tests import helpers as h

URL = "/api/v1/ingest/agent"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def count(model: Any) -> int:
    async with session_scope() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# ---------------------------------------------------------------- worker side (process_batch)


async def test_replaying_the_same_batch_is_a_noop() -> None:
    sid = await h.make_source()
    now = utcnow()
    body = h.payload(batch_id="batch-0001", samples=[
        h.sample("net.rx_bps", 1000.0, h.ago(2, now)), h.sample("net.rx_bps", 2000.0, h.ago(1, now)),
        h.sample("net.tx_bps", 500.0, h.ago(1, now)),
    ])  # fmt: skip
    first = await h.process(sid, body)
    second = await h.process(sid, body)  # retry / duplicate delivery
    assert first == {"duplicate": False, "samples": 3}
    assert second == {"duplicate": True, "samples": 0}
    assert await count(MetricSample) == 3
    assert await count(IngestBatch) == 1
    assert await count(MetricSeries) == 2


async def test_same_samples_in_a_different_batch_are_deduplicated_by_series_and_timestamp() -> None:
    sid = await h.make_source()
    ts = h.ago(1)
    await h.process(sid, h.payload(samples=[h.sample("net.rx_bps", 1000.0, ts)]))
    again = await h.process(sid, h.payload(samples=[h.sample("net.rx_bps", 1000.0, ts)]))
    assert again == {"duplicate": False, "samples": 0}
    assert await count(MetricSample) == 1


async def test_series_carry_provenance() -> None:
    sid = await h.make_source()
    await h.process(sid, h.payload(samples=[h.sample("net.rx_bps", 8.0, h.ago(1), interval_s=1.0)]))
    async with session_scope() as session:
        series = (await session.execute(select(MetricSeries))).scalar_one()
    assert (series.metric, series.label, series.unit) == ("net.rx_bps", "eth0", "bit/s")
    assert series.observation_point == "host_nic"
    assert series.quality == "measured"
    assert series.interval_s == 1.0
    assert series.source_id == sid and series.asset_id == await h.source_asset_id(sid)
    assert "not network-wide" in (series.coverage or "")


async def test_out_of_order_batch_never_moves_last_seen_backwards() -> None:
    sid = await h.make_source()
    now = utcnow()
    await h.process(sid, h.payload(samples=[h.sample("net.rx_bps", 1.0, now)]), received_at=now)
    await h.process(sid, h.payload(samples=[h.sample("net.rx_bps", 1.0, h.ago(60, now))]),
                    received_at=now - timedelta(seconds=60))  # fmt: skip
    async with session_scope() as session:
        source = await session.get(DataSource, sid)
        assert source is not None and source.last_seen_at == now
    assert await count(MetricSample) == 2  # the late samples themselves are still stored


async def test_probe_results_become_series_and_unknown_target_ids_are_dropped() -> None:
    sid = await h.make_source()
    target = await h.make_target()
    now = utcnow()
    body = h.payload(probe_results=[
        {"target_id": target, "ts": h.ago(2, now).isoformat(), "success": True, "latency_ms": 2.5, "jitter_ms": 0.4, "method": "icmp_dgram"},
        {"target_id": target, "ts": h.ago(1, now).isoformat(), "success": False, "latency_ms": None, "method": "icmp_dgram", "error": "timeout"},
        {"target_id": 99999, "ts": h.ago(1, now).isoformat(), "success": True, "latency_ms": 1.0, "method": "icmp_dgram"},
    ])  # fmt: skip
    await h.process(sid, body)
    async with session_scope() as session:
        results = list((await session.execute(select(ProbeResult).order_by(ProbeResult.ts))).scalars())
        series = {s.metric: s for s in (await session.execute(select(MetricSeries))).scalars()}
        samples = {
            m: [v for (v,) in (await session.execute(select(MetricSample.value).where(MetricSample.series_id == s.id)
                                                     .order_by(MetricSample.ts))).all()]
            for m, s in series.items()
        }  # fmt: skip
    assert [(r.target_id, r.success, r.latency_ms) for r in results] == [
        (target, True, 2.5),
        (target, False, None),
    ]
    assert all(
        s.label == f"target:{target}" and s.observation_point == "active_probe" for s in series.values()
    )
    assert samples["probe.success"] == [1.0, 0.0]  # avg over a bucket = delivery ratio
    assert samples["probe.latency_ms"] == [2.5]  # a timeout has no latency sample
    assert samples["probe.jitter_ms"] == [0.4]
    assert series["probe.jitter_ms"].quality == "derived"


async def test_heartbeat_records_errors_and_capabilities() -> None:
    sid = await h.make_source()
    now = utcnow()
    body = h.payload(heartbeat={
        "ts": now.isoformat(), "cpu_pct": 12.5, "mem_pct": 40.0, "uptime_s": 99, "errors": {"neighbor_cache": "EACCES"},
        "capabilities": [{"name": "nic_counters", "status": "supported", "detail": "x"},
                         {"name": "docker_published_ports", "status": "disabled", "detail": "opt-in"}],
    })  # fmt: skip
    await h.process(sid, body)
    await h.process(sid, h.payload(heartbeat={"ts": now.isoformat(), "errors": {}, "capabilities": [
        {"name": "nic_counters", "status": "unavailable", "detail": "gone"}]}))  # fmt: skip
    async with session_scope() as session:
        caps = {c.name: c.status for c in (await session.execute(select(Capability))).scalars()}
        source = await session.get(DataSource, sid)
        assert source is not None and source.last_error is None  # cleared once the collector recovered
    assert caps == {
        "nic_counters": "unavailable",
        "docker_published_ports": "disabled",
    }  # upserted, not duplicated
    assert await count(CollectorHeartbeat) == 2


async def test_out_of_order_posture_snapshot_never_drives_transitions() -> None:
    sid = await h.make_source()
    now = utcnow()
    open_redis = h.posture(h.ago(60, now), listening=h.listening(("tcp", "0.0.0.0", 6379)))
    await h.process(sid, h.payload(posture=open_redis))
    stale_clean = h.posture(h.ago(600, now), listening=h.listening())  # older evidence arriving late
    await h.process(sid, h.payload(posture=stale_clean))
    async with session_scope() as session:
        finding = (await session.execute(select(Finding))).scalar_one()
        assert finding.status == "open" and finding.resolved_at is None
    assert await count(PostureSnapshot) == 1  # the stale snapshot is not even stored
    fresh_clean = h.posture(h.ago(1, now), listening=h.listening())
    await h.process(sid, h.payload(posture=fresh_clean))
    async with session_scope() as session:
        finding = (await session.execute(select(Finding))).scalar_one()
        assert finding.status == "resolved"


async def test_identical_consecutive_snapshots_are_not_stored_twice() -> None:
    sid = await h.make_source()
    now = utcnow()
    sockets = h.listening(("tcp", "0.0.0.0", 8080))
    await h.process(sid, h.payload(posture=h.posture(h.ago(300, now), listening=sockets)))
    await h.process(sid, h.payload(posture=h.posture(h.ago(1, now), listening=sockets)))
    async with session_scope() as session:
        snapshot = (await session.execute(select(PostureSnapshot))).scalar_one()  # exactly one row
        finding = (await session.execute(select(Finding))).scalar_one()
    assert snapshot.collected_at == h.ago(300, now)
    assert snapshot.last_confirmed_at == h.ago(1, now)
    assert finding.last_observed_at == h.ago(1, now)  # still truthfully "last observed" just now


async def test_batch_for_a_deleted_source_is_skipped() -> None:
    sid = await h.make_source()
    async with session_scope() as session:
        source = await session.get(DataSource, sid)
        await session.delete(source)
    result = await h.process(sid, h.payload(samples=[h.sample("net.rx_bps", 1.0, utcnow())]))
    assert result == {"skipped": "source no longer exists", "samples": 0}  # dropped, not retried forever
    assert await count(MetricSample) == 0 and await count(IngestBatch) == 0


# ---------------------------------------------------------------- API side (accept)


async def test_accept_validates_publishes_live_and_enqueues(
    client: httpx.AsyncClient, agent_token: str
) -> None:
    now = utcnow()
    body = h.payload(samples=[h.sample("net.rx_bps", 4242.0, now)])
    resp = await client.post(URL, json=body, headers=auth(agent_token))
    assert resp.status_code == 202, resp.text
    assert resp.json()["samples"] == 1

    live = await get_redis().hgetall(CURRENT_HASH)
    assert len(live) == 1
    key = next(iter(live))
    assert key.endswith("|net.rx_bps|eth0")

    message = dramatiq.Message.decode(broker.queues["ingest"].get_nowait())
    assert message.actor_name == "process_ingest"
    assert await count(MetricSample) == 0  # nothing persisted in the request path

    await tasks.process_ingest.fn.__wrapped__(*message.args)  # what the worker would run
    await tasks.process_ingest.fn.__wrapped__(*message.args)  # redelivery is harmless
    assert await count(MetricSample) == 1


async def test_missing_or_bad_token_is_401(client: httpx.AsyncClient) -> None:
    body = h.payload()
    assert (await client.post(URL, json=body)).status_code == 401
    assert (await client.post(URL, json=body, headers=auth("onet_not-a-real-token"))).status_code == 401
    assert (await client.post(URL, json=body, headers={"Authorization": "Basic abc"})).status_code == 401


async def test_future_skewed_timestamps_rejected_with_422_and_reason_recorded(
    client: httpx.AsyncClient, agent_token: str
) -> None:
    future = utcnow() + timedelta(minutes=10)
    resp = await client.post(
        URL, json=h.payload(samples=[h.sample("net.rx_bps", 1.0, future)]), headers=auth(agent_token)
    )
    assert resp.status_code == 422
    assert "clock skew" in resp.json()["detail"]
    async with session_scope() as session:
        source = (await session.execute(select(DataSource))).scalar_one()
    assert "clock skew" in (source.last_error or "")
    assert broker.queues["ingest"].empty()


async def test_small_clock_skew_is_tolerated(client: httpx.AsyncClient, agent_token: str) -> None:
    soon = utcnow() + timedelta(seconds=30)
    resp = await client.post(
        URL, json=h.payload(samples=[h.sample("net.rx_bps", 1.0, soon)]), headers=auth(agent_token)
    )
    assert resp.status_code == 202


async def test_samples_older_than_a_day_are_dropped_not_rejected(
    client: httpx.AsyncClient, agent_token: str
) -> None:
    now = utcnow()
    body = h.payload(
        samples=[h.sample("net.rx_bps", 1.0, now - timedelta(hours=30)), h.sample("net.rx_bps", 2.0, now)]
    )
    resp = await client.post(URL, json=body, headers=auth(agent_token))
    assert resp.status_code == 202 and resp.json()["samples"] == 1


async def test_wrong_unit_rejected(client: httpx.AsyncClient, agent_token: str) -> None:
    body = h.payload(samples=[h.sample("net.rx_bps", 1.0, utcnow(), unit="byte/s")])
    resp = await client.post(URL, json=body, headers=auth(agent_token))
    assert resp.status_code == 422 and "bit/s" in resp.json()["detail"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b["samples"].append(
            {"metric": "made.up", "label": "", "ts": utcnow().isoformat(), "value": 1, "unit": "x"}
        ),
        lambda b: b["samples"].append(h.sample("net.rx_bps", float("nan"), utcnow())),
        lambda b: b.update(schema_version=2),
        lambda b: b.update(unexpected_field=True),
        lambda b: b["agent"].update(agent_uid="bad uid with spaces"),
        lambda b: b.update(batch_id="x"),
    ],
)
async def test_malformed_payloads_rejected_by_schema(
    client: httpx.AsyncClient, agent_token: str, mutate: Any
) -> None:
    body = h.payload(samples=[])
    mutate(body)
    import json

    resp = await client.post(
        URL,
        content=json.dumps(body, allow_nan=True),
        headers={**auth(agent_token), "content-type": "application/json"},
    )
    assert resp.status_code == 422


async def test_synthetic_samples_rejected_in_real_mode(client: httpx.AsyncClient, agent_token: str) -> None:
    body = h.payload(samples=[h.sample("net.rx_bps", 1.0, utcnow(), quality="synthetic")])
    resp = await client.post(URL, json=body, headers=auth(agent_token))
    assert resp.status_code == 422 and "synthetic" in resp.json()["detail"]


async def test_demo_payload_rejected_in_real_mode(client: httpx.AsyncClient, agent_token: str) -> None:
    body = h.payload(platform="demo", emulates="linux", samples=[])
    resp = await client.post(URL, json=body, headers=auth(agent_token))
    assert resp.status_code == 409
    assert await count(DataSource) == 0


async def test_real_payload_rejected_in_demo_mode(
    client: httpx.AsyncClient, agent_token: str, set_config: Any
) -> None:
    set_config(demo_mode="true")
    resp = await client.post(URL, json=h.payload(samples=[]), headers=auth(agent_token))
    assert resp.status_code == 409 and "DEMO_MODE" in resp.json()["detail"]
    assert await count(DataSource) == 0


async def test_token_is_bound_to_the_first_agent_that_uses_it(
    client: httpx.AsyncClient, agent_token: str
) -> None:
    ok = await client.post(URL, json=h.payload(agent_uid="lnx-agent-aaaa"), headers=auth(agent_token))
    assert ok.status_code == 202
    other = await client.post(URL, json=h.payload(agent_uid="lnx-agent-bbbb"), headers=auth(agent_token))
    assert other.status_code == 403 and "different agent" in other.json()["detail"]
    again = await client.post(URL, json=h.payload(agent_uid="lnx-agent-aaaa"), headers=auth(agent_token))
    assert again.status_code == 202
    assert await count(DataSource) == 1


async def test_second_token_cannot_impersonate_an_existing_agent(
    client: httpx.AsyncClient, agent_token: str
) -> None:
    assert (
        await client.post(URL, json=h.payload(agent_uid="lnx-agent-aaaa"), headers=auth(agent_token))
    ).status_code == 202
    other_token, _ = await h.issue_token("other")
    assert (
        await client.post(URL, json=h.payload(agent_uid="lnx-agent-bbbb"), headers=auth(other_token))
    ).status_code == 202
    hijack = await client.post(URL, json=h.payload(agent_uid="lnx-agent-aaaa"), headers=auth(other_token))
    assert hijack.status_code == 403


async def test_disabled_source_is_refused(client: httpx.AsyncClient, agent_token: str) -> None:
    assert (await client.post(URL, json=h.payload(), headers=auth(agent_token))).status_code == 202
    async with session_scope() as session:
        (await session.execute(select(DataSource))).scalar_one().enabled = False
    assert (await client.post(URL, json=h.payload(), headers=auth(agent_token))).status_code == 403


async def test_broker_unavailable_returns_503_so_the_agent_retries(
    client: httpx.AsyncClient, agent_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disconnected worker/queue: the batch is NOT acknowledged, so the agent keeps and resends it."""

    def down(*_a: Any, **_k: Any) -> None:
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(tasks.process_ingest, "send", down)
    body = h.payload(batch_id="retry-me-0001", samples=[h.sample("net.rx_bps", 1.0, utcnow())])
    resp = await client.post(URL, json=body, headers=auth(agent_token))
    assert resp.status_code == 503 and "retry" in resp.json()["detail"]
    monkeypatch.undo()
    retry = await client.post(URL, json=body, headers=auth(agent_token))  # same batch_id
    assert retry.status_code == 202
    message = dramatiq.Message.decode(broker.queues["ingest"].get_nowait())
    assert message.args[1]["batch_id"] == "retry-me-0001"


async def test_redis_publish_failure_returns_503(
    client: httpx.AsyncClient, agent_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(*_a: Any, **_k: Any) -> None:
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(get_redis(), "publish", down)
    resp = await client.post(
        URL, json=h.payload(samples=[h.sample("net.rx_bps", 1.0, utcnow())]), headers=auth(agent_token)
    )
    assert resp.status_code == 503
    assert broker.queues["ingest"].empty()


async def test_worker_down_batches_queue_up_and_apply_later_in_any_order(
    client: httpx.AsyncClient, agent_token: str
) -> None:
    now = utcnow()
    for i in range(3):
        body = h.payload(samples=[h.sample("net.rx_bps", float(i), h.ago(3 - i, now))])
        assert (await client.post(URL, json=body, headers=auth(agent_token))).status_code == 202
    assert await count(MetricSample) == 0  # accepted and durable in the queue, not yet applied
    messages = []
    while not broker.queues["ingest"].empty():
        messages.append(dramatiq.Message.decode(broker.queues["ingest"].get_nowait()))
    assert len(messages) == 3
    for message in reversed(messages):  # worker comes back; delivery order is not guaranteed
        await tasks.process_ingest.fn.__wrapped__(*message.args)
    async with session_scope() as session:
        values = [
            v for (v,) in (await session.execute(select(MetricSample.value).order_by(MetricSample.ts))).all()
        ]
    assert values == [0.0, 1.0, 2.0]  # ordered by sample timestamp regardless of processing order


async def test_ingest_rate_limit(client: httpx.AsyncClient, agent_token: str, set_config: Any) -> None:
    set_config(ingest_rate_limit_per_minute="2")
    codes = [
        (await client.post(URL, json=h.payload(), headers=auth(agent_token))).status_code for _ in range(3)
    ]
    assert codes == [202, 202, 429]


async def test_agent_config_requires_token_and_lists_only_enabled_targets(
    client: httpx.AsyncClient, agent_token: str
) -> None:
    assert (await client.get("/api/v1/agent/config")).status_code == 401
    resp = await client.get("/api/v1/agent/config", headers=auth(agent_token))
    assert resp.status_code == 200
    cfg = resp.json()
    assert [p["kind"] for p in cfg["probes"]] == ["gateway_icmp", "tcp", "tcp", "dns"]  # seeded defaults
    assert cfg["sample_interval_s"] == 1.0
    assert cfg["discovery"]["enabled"] is False and cfg["speedtest"]["enabled"] is False
    assert all(p["interval_s"] >= 10 for p in cfg["probes"] if p["kind"] != "gateway_icmp")
