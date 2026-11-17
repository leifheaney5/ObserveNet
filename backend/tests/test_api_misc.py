"""Probe targets, runtime settings, service status, connectivity, insights, topology, observability."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from observenet.core import logging as onet_logging
from observenet.core.db import session_scope
from observenet.core.redis import WORKER_HEARTBEAT_KEY, get_redis
from observenet.core.time import utcnow
from observenet.models import MetricRollup1h, MetricRollup1m, Outage, ProbeResult, ProbeTarget
from observenet.services import timeseries as ts
from observenet.services.connectivity import internet_status
from tests import helpers as h

TARGETS = "/api/v1/probe-targets"


def target(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "t",
        "kind": "tcp",
        "scope": "external",
        "host": "1.1.1.1",
        "port": 443,
        "interval_s": 30,
        **overrides,
    }


# ---------------------------------------------------------------- probe targets (SSRF / scope)


@pytest.mark.parametrize(
    "body",
    [
        target(host="127.0.0.1"),
        target(host="169.254.169.254", port=80),  # cloud metadata address
        target(host="localhost"),
        target(host="nas.local"),
        target(host="db.internal"),
        target(host="192.168.1.10"),  # private address under external scope
        target(scope="internal", host="192.168.1.10"),  # internal but no CIDR approved yet
        target(scope="internal", host="8.8.8.8"),
        target(port=22),
        target(port=6379),
        target(port=None),
        target(kind="icmp", scope="internal", host="8.8.8.8", port=None),
        target(kind="gateway_icmp", scope="external", host="auto", port=None),
        target(kind="dns", scope="external", host="10.0.0.1", port=None),
        target(interval_s=5),  # external targets: not more often than every 10 s
    ],
)
async def test_unsafe_probe_targets_are_rejected_with_422(
    admin_client: httpx.AsyncClient, body: dict[str, Any]
) -> None:
    before = len((await admin_client.get(TARGETS)).json())
    resp = await admin_client.post(TARGETS, json=body)
    assert resp.status_code == 422, f"{body} -> {resp.status_code} {resp.text}"
    assert len((await admin_client.get(TARGETS)).json()) == before


async def test_schema_level_probe_validation(admin_client: httpx.AsyncClient) -> None:
    for body in (
        target(kind="http"),
        target(scope="everywhere"),
        target(interval_s=0.1),
        target(timeout_ms=60000),
        target(port=70000),
    ):
        assert (await admin_client.post(TARGETS, json=body)).status_code == 422


async def test_internal_target_allowed_only_inside_an_approved_cidr(admin_client: httpx.AsyncClient) -> None:
    internal = target(name="nas", kind="icmp", scope="internal", host="192.168.1.10", port=None, interval_s=5)
    assert (await admin_client.post(TARGETS, json=internal)).status_code == 422
    settings = (await admin_client.get("/api/v1/settings")).json()
    settings["approved_cidrs"] = ["192.168.1.0/24"]
    assert (await admin_client.put("/api/v1/settings", json=settings)).status_code == 200
    created = await admin_client.post(TARGETS, json=internal)
    assert created.status_code == 201 and created.json()["host"] == "192.168.1.10"
    outside = {**internal, "host": "192.168.2.10"}
    assert (await admin_client.post(TARGETS, json=outside)).status_code == 422
    # editing an existing target is validated the same way
    assert (await admin_client.patch(f"{TARGETS}/{created.json()['id']}", json=outside)).status_code == 422
    ok = await admin_client.patch(f"{TARGETS}/{created.json()['id']}", json={**internal, "enabled": False})
    assert ok.status_code == 200 and ok.json()["enabled"] is False
    assert (await admin_client.delete(f"{TARGETS}/{created.json()['id']}")).status_code == 204


async def test_default_targets_and_target_limit(admin_client: httpx.AsyncClient) -> None:
    defaults = (await admin_client.get(TARGETS)).json()
    assert [(t["kind"], t["scope"], t["host"], t["interval_s"]) for t in defaults] == [
        ("gateway_icmp", "gateway", "auto", 1.0), ("tcp", "external", "1.1.1.1", 30.0),
        ("tcp", "external", "9.9.9.9", 30.0), ("dns", "external", "example.com", 30.0),
    ]  # fmt: skip
    for i in range(16):
        assert (await admin_client.post(TARGETS, json=target(name=f"t{i}"))).status_code == 201
    assert (await admin_client.post(TARGETS, json=target(name="one too many"))).status_code == 409


# ---------------------------------------------------------------- runtime settings


async def test_settings_validation_and_audit(admin_client: httpx.AsyncClient) -> None:
    settings = (await admin_client.get("/api/v1/settings")).json()
    assert settings["discovery"]["enabled"] is False and settings["speedtest"]["enabled"] is False

    async def put(**changes: Any) -> httpx.Response:
        return await admin_client.put("/api/v1/settings", json={**settings, **changes})

    assert (await put(approved_cidrs=["8.8.8.0/24"])).status_code == 422  # public space can never be approved
    assert (await put(approved_cidrs=["10.0.0.0/8"])).status_code == 422  # too broad
    assert (
        await put(discovery={**settings["discovery"], "enabled": True})
    ).status_code == 422  # no sweep CIDR
    assert (await put(approved_cidrs=["192.168.1.0/24"],
                      discovery={**settings["discovery"], "enabled": True, "cidrs": ["192.168.9.0/24"]})).status_code == 422  # fmt: skip
    assert (await put(discovery={**settings["discovery"], "max_probes_per_second": 500})).status_code == 422
    assert (await put(speedtest={**settings["speedtest"], "enabled": True})).status_code == 422  # no URL
    assert (await put(speedtest={**settings["speedtest"], "interval_hours": 0.5})).status_code == 422
    assert (await put(retention={**settings["retention"], "raw_hours": 0})).status_code == 422
    assert (await put(collection={**settings["collection"], "sample_interval_s": 0.1})).status_code == 422

    ok = await put(approved_cidrs=["192.168.1.0/24"], retention={**settings["retention"], "raw_hours": 72},
                   discovery={**settings["discovery"], "enabled": True, "cidrs": ["192.168.1.0/26"]})  # fmt: skip
    assert ok.status_code == 200 and ok.json()["retention"]["raw_hours"] == 72
    assert (await admin_client.get("/api/v1/settings")).json()["discovery"]["cidrs"] == ["192.168.1.0/26"]
    audit = next(
        a for a in (await admin_client.get("/api/v1/audit")).json() if a["action"] == "settings.update"
    )
    assert audit["details"]["changed_sections"] == ["approved_cidrs", "discovery", "retention"]


async def test_deployment_info_reports_configuration_without_claiming_reachability(
    admin_client: httpx.AsyncClient, set_config: Any
) -> None:
    set_config(public_url="https://observenet.leifslab.com", alert_webhook_secret="hunter2-hunter2")  # noqa: S106
    info = (await admin_client.get("/api/v1/settings/deployment")).json()
    assert info["public_url"] == "https://observenet.leifslab.com" and info["https_configured"] is True
    assert info["cookie_secure"] is True
    assert "cannot verify" in info["note"]
    assert "hunter2" not in str(info) and "secret" not in str(info).lower()


# ---------------------------------------------------------------- health / status


async def test_ready_reports_dependency_failures(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok = await client.get("/api/v1/ready")
    assert ok.status_code == 200 and ok.json() == {
        "status": "ready",
        "checks": {"database": "ok", "redis": "ok"},
    }

    async def down() -> None:
        raise ConnectionError("no redis")

    monkeypatch.setattr(get_redis(), "ping", down)
    bad = await client.get("/api/v1/ready")
    assert bad.status_code == 503 and bad.json()["checks"]["redis"].startswith("error")
    assert (await client.get("/api/v1/health")).status_code == 200  # liveness does not depend on Redis


async def test_status_shows_worker_and_scheduler_heartbeats(admin_client: httpx.AsyncClient) -> None:
    status = (await admin_client.get("/api/v1/status")).json()
    components = {c["name"]: c for c in status["components"]}
    assert components["PostgreSQL"]["state"] == "ok" and components["Redis"]["state"] == "ok"
    assert (
        components["Worker"]["state"] == "down" and components["Scheduler"]["state"] == "down"
    )  # nothing reported yet
    assert status["queues"] == {"ingest": 0, "maintenance": 0}

    now = utcnow()
    await get_redis().set(WORKER_HEARTBEAT_KEY.format(name="worker"), now.isoformat())
    await get_redis().set(
        WORKER_HEARTBEAT_KEY.format(name="scheduler"), (now - timedelta(seconds=40)).isoformat()
    )
    await get_redis().rpush("observenet:ingest", *[f"m{i}" for i in range(150)])
    components = {c["name"]: c for c in (await admin_client.get("/api/v1/status")).json()["components"]}
    assert components["Worker"]["state"] == "ok"
    assert components["Scheduler"]["state"] == "degraded"  # late heartbeat
    assert components["Ingest queue"] == {
        "name": "Ingest queue",
        "state": "degraded",
        "detail": "150 batches waiting",
    }


# ---------------------------------------------------------------- connectivity summary + outages


async def add_results(
    target_id: int, source_id: int, points: list[tuple[float, bool, float | None]], method: str = "icmp_dgram"
) -> None:
    now = utcnow()
    async with session_scope() as session:
        for seconds_ago, success, latency in points:
            session.add(ProbeResult(target_id=target_id, source_id=source_id, ts=now - timedelta(seconds=seconds_ago),
                                    success=success, latency_ms=latency, method=method, error=None if success else "timeout"))  # fmt: skip


async def test_connectivity_summary_states_and_sample_sizes(admin_client: httpx.AsyncClient) -> None:
    sid = await h.make_source()
    targets = {t["name"]: t["id"] for t in (await admin_client.get(TARGETS)).json()}
    gw = targets["Default gateway"]
    await add_results(
        gw, sid, [(5, True, 2.0), (4, True, 4.0), (3, False, None), (2, True, 3.0), (1, True, 3.0)]
    )
    await add_results(targets["Cloudflare DNS (TCP 443)"], sid, [(10, True, 15.0)], method="tcp_connect")
    await add_results(targets["Quad9 DNS (TCP 443)"], sid, [(10, False, None)], method="tcp_connect")
    body = (await admin_client.get("/api/v1/connectivity/summary", params={"window_s": 60})).json()
    by_name = {t["target"]["name"]: t for t in body["targets"]}
    gateway = by_name["Default gateway"]
    assert gateway["state"] == "lossy" and gateway["sample_size"] == 5 and gateway["window_s"] == 60
    assert gateway["loss_pct"] == pytest.approx(20.0) and gateway["latency_avg_ms"] == pytest.approx(3.0)
    assert (
        gateway["jitter_ms"] == pytest.approx((2.0 + 1.0 + 0.0) / 3)
        and gateway["last_method"] == "icmp_dgram"
    )
    assert by_name["Cloudflare DNS (TCP 443)"]["state"] == "ok"
    quad9 = by_name["Quad9 DNS (TCP 443)"]
    assert (
        quad9["state"] == "not_responding"
        and "not an outage" in quad9["note"]
        and quad9["latency_avg_ms"] is None
    )
    assert by_name["DNS resolution (example.com)"]["state"] == "no_data"
    # one of two fresh external signals failing: degraded, never "unreachable"
    assert body["internet"]["status"] == "degraded" and body["outages"] == []


async def test_gateway_that_ignores_icmp_is_not_reported_as_an_internet_outage(
    admin_client: httpx.AsyncClient,
) -> None:
    sid = await h.make_source()
    targets = {t["name"]: t["id"] for t in (await admin_client.get(TARGETS)).json()}
    await add_results(
        targets["Default gateway"], sid, [(s, False, None) for s in range(1, 30)]
    )  # gateway filters ICMP
    for name in ("Cloudflare DNS (TCP 443)", "Quad9 DNS (TCP 443)", "DNS resolution (example.com)"):
        await add_results(targets[name], sid, [(40, True, 12.0), (10, True, 12.0)], method="tcp_connect")
    body = (await admin_client.get("/api/v1/connectivity/summary")).json()
    gateway = next(t for t in body["targets"] if t["target"]["scope"] == "gateway")
    assert gateway["state"] == "not_responding" and gateway["loss_pct"] == 100.0
    assert "filters ICMP looks identical" in gateway["note"]
    assert body["internet"]["status"] == "reachable"
    packet_loss = next(
        k
        for k in (await admin_client.get("/api/v1/overview")).json()["kpis"]
        if k["key"] == "gateway_latency"
    )
    assert (
        packet_loss["value"] is None
        and packet_loss["state"] == "unknown"
        and "not an outage" in packet_loss["reason"]
    )


async def test_outage_is_opened_only_on_multi_signal_failure_and_closed_on_recovery(
    admin_client: httpx.AsyncClient,
) -> None:
    from observenet.workers import tasks

    sid = await h.make_source()
    targets = {t["name"]: t["id"] for t in (await admin_client.get(TARGETS)).json()}
    external = [
        targets[n]
        for n in ("Cloudflare DNS (TCP 443)", "Quad9 DNS (TCP 443)", "DNS resolution (example.com)")
    ]

    await add_results(
        external[0], sid, [(70, False, None), (40, False, None), (10, False, None)], method="tcp_connect"
    )
    await add_results(
        external[1], sid, [(70, True, 9.0), (40, True, 9.0), (10, True, 9.0)], method="tcp_connect"
    )
    await tasks.evaluate.fn.__wrapped__()
    async with session_scope() as session:
        assert (await session.execute(select(Outage))).first() is None  # one endpoint down is not an outage

    async with session_scope() as session:
        for row in (
            await session.execute(select(ProbeResult).where(ProbeResult.target_id == external[1]))
        ).scalars():
            row.success, row.latency_ms = False, None
    await add_results(
        external[2], sid, [(70, False, None), (40, False, None), (10, False, None)], method="system_resolver"
    )
    async with session_scope() as session:
        assert (await internet_status(session, now=utcnow())).status == "unreachable"
    await tasks.evaluate.fn.__wrapped__()
    await tasks.evaluate.fn.__wrapped__()  # a second tick must not open a second outage
    async with session_scope() as session:
        (outage,) = (await session.execute(select(Outage))).scalars().all()
        assert outage.ended_at is None and outage.evidence["signals"] == 3

    await add_results(external[0], sid, [(1, True, 10.0)], method="tcp_connect")
    await tasks.evaluate.fn.__wrapped__()
    listed = (await admin_client.get("/api/v1/connectivity/summary")).json()["outages"]
    assert len(listed) == 1 and listed[0]["ended_at"] is not None and "recovery" in listed[0]["evidence"]
    assert (
        await get_redis().get(WORKER_HEARTBEAT_KEY.format(name="worker")) is not None
    )  # the worker heartbeat was written


# ---------------------------------------------------------------- insights + topology


async def series_id(source_id: int, metric: str, label: str, unit: str, point: str) -> int:
    async with session_scope() as session:
        return await ts.ensure_series(session, source_id=source_id, metric=metric, label=label, unit=unit,
                                      observation_point=point, quality="measured", interval_s=1.0, asset_id=None)  # fmt: skip


async def test_insights_state_their_method_and_never_claim_causation(admin_client: httpx.AsyncClient) -> None:
    sid = await h.make_source()
    gw = next(t["id"] for t in (await admin_client.get(TARGETS)).json() if t["scope"] == "gateway")
    rx = await series_id(sid, "net.rx_bps", "eth0", "bit/s", "host_nic")
    lat = await series_id(sid, "probe.latency_ms", f"target:{gw}", "ms", "active_probe")
    now = utcnow()
    hour0 = now.replace(minute=0, second=0, microsecond=0)
    minute0 = now.replace(second=0, microsecond=0)
    async with session_scope() as session:
        for hrs in range(1, 47):  # last 24 h twice as busy/slow as the 24 h before
            recent = hrs <= 23
            session.add(MetricRollup1h(series_id=rx, bucket=hour0 - timedelta(hours=hrs), avg=2e6 if recent else 1e6, min=0, max=9e6,
                                       sum=(2e6 if recent else 1e6) * 3600, count=3600))  # fmt: skip
            session.add(MetricRollup1h(series_id=lat, bucket=hour0 - timedelta(hours=hrs), avg=6.0 if recent else 3.0, min=1, max=50,
                                       sum=(6.0 if recent else 3.0) * 3600, count=3600))  # fmt: skip
        for m in range(1, 301):  # every 10th minute: traffic burst together with slow gateway replies
            busy = m % 10 == 0
            session.add(MetricRollup1m(series_id=rx, bucket=minute0 - timedelta(minutes=m), avg=90e6 if busy else 1e6, min=0, max=95e6,
                                       sum=(90e6 if busy else 1e6) * 60, count=60))  # fmt: skip
            session.add(MetricRollup1m(series_id=lat, bucket=minute0 - timedelta(minutes=m), avg=40.0 if busy else 2.0, min=1, max=60,
                                       sum=(40.0 if busy else 2.0) * 60, count=60))  # fmt: skip
    await h.process(
        sid, h.payload(inventory=h.inventory(now, [("192.168.1.20", "3c:22:fb:00:00:0a")], gateway=None))
    )

    body = (await admin_client.get("/api/v1/insights")).json()
    by_key = {i["key"]: i for i in body["insights"]}
    assert {"latency_trend", "rx_trend", "peak_hour", "traffic_latency", "new_devices"} <= set(by_key)
    for insight in body["insights"]:
        assert insight["method"] and insight["summary"] and insight["data"] is not None  # no opaque score
        assert "risk score" not in insight["summary"].lower()
    trend = by_key["latency_trend"]
    assert "higher than" in trend["summary"] and "+100%" in trend["summary"]
    assert trend["data"]["samples"] == [23 * 3600, 23 * 3600] or trend["data"]["samples"][0] > 0
    assert "samples" in trend["method"]
    corr = by_key["traffic_latency"]
    assert corr["data"]["both"] == corr["data"]["busy"] == 30
    assert "does not prove" in corr["summary"]
    assert "Correlation only" in corr["method"] and "causation is not established" in corr["method"]
    assert "not threat verdicts" in by_key["new_devices"]["summary"]
    assert "Host NIC traffic only" in by_key["peak_hour"]["method"]


async def test_insights_report_what_could_not_be_computed(admin_client: httpx.AsyncClient) -> None:
    sid = await h.make_source()
    await h.process(sid, h.payload(samples=[h.sample("net.rx_bps", 1.0, utcnow())]))
    body = (await admin_client.get("/api/v1/insights")).json()
    skipped = {s["key"]: s["reason"] for s in body["not_computed"]}
    assert "rx_trend" in skipped and "Needs at least 6 h" in skipped["rx_trend"]
    assert "latency_trend" in skipped
    assert not any(i["key"] in ("rx_trend", "latency_trend", "traffic_latency") for i in body["insights"])


async def test_topology_shows_observed_relationships_only(admin_client: httpx.AsyncClient) -> None:
    sid = await h.make_source()
    now = utcnow()
    await h.process(sid, h.payload(inventory=h.inventory(now, [("192.168.1.1", "3c:22:fb:00:00:01"), ("192.168.1.20", "3c:22:fb:00:00:0a")]),
                                   posture=h.posture(now, listening=h.listening(("tcp", "0.0.0.0", 22), ("tcp", "0.0.0.0", 8080)))))  # fmt: skip
    topo = (await admin_client.get("/api/v1/topology")).json()
    types = [n["type"] for n in topo["nodes"]]
    assert (
        types.count("segment"),
        types.count("asset"),
        types.count("interface"),
        types.count("service"),
    ) == (1, 3, 1, 2)
    kinds = {e["kind"] for e in topo["edges"]}
    assert kinds == {"HAS_INTERFACE", "ATTACHED_TO", "SEEN_ON", "LISTENS_ON"}
    assert all(e["evidence"] for e in topo["edges"])
    node_ids = {n["id"] for n in topo["nodes"]}
    assert all(e["source"] in node_ids and e["target"] in node_ids for e in topo["edges"])
    assert "not an attack-path graph" in topo["note"]
    # nothing connects one neighbour to another: reachability between devices is never inferred
    asset_to_asset = [
        e for e in topo["edges"] if e["source"].startswith("asset:") and e["target"].startswith("asset:")
    ]
    assert asset_to_asset == []


# ---------------------------------------------------------------- observability plumbing


def test_log_redaction_processor_masks_secrets() -> None:
    event = {"event": "login", "password": "hunter2", "agent_token": "onet_x", "Authorization": "Bearer y", "cookie": "c",
             "username": "leif"}  # fmt: skip
    out = onet_logging._redact(None, "info", dict(event))
    assert out["username"] == "leif" and out["event"] == "login"
    assert out["password"] == out["agent_token"] == out["Authorization"] == out["cookie"] == "[redacted]"


def test_required_metrics_are_registered() -> None:
    from prometheus_client import REGISTRY

    names = {m.name for m in REGISTRY.collect()}
    for required in (
        "observenet_http_request_duration_seconds", "observenet_ingest_batches", "observenet_ingest_samples",
        "observenet_ingest_lag_seconds", "observenet_collector_failures", "observenet_queue_depth", "observenet_ws_clients",
        "observenet_ws_dropped_clients", "observenet_source_data_age_seconds", "observenet_task_runs",
        "observenet_task_duration_seconds", "observenet_alert_transitions",
    ):  # fmt: skip
        assert required in names, required


async def test_api_latency_and_ingest_outcomes_are_instrumented(
    client: httpx.AsyncClient, agent_token: str
) -> None:
    from prometheus_client import REGISTRY

    def value(name: str, **labels: str) -> float:
        return REGISTRY.get_sample_value(name, labels) or 0.0

    before_http = value(
        "observenet_http_request_duration_seconds_count", method="GET", route="/api/v1/health", status="200"
    )
    before_ok = value("observenet_ingest_batches_total", outcome="accepted")
    before_rejected = value("observenet_ingest_batches_total", outcome="rejected_409")
    await client.get("/api/v1/health")
    headers = {"Authorization": f"Bearer {agent_token}"}
    await client.post("/api/v1/ingest/agent", json=h.payload(), headers=headers)
    await client.post(
        "/api/v1/ingest/agent", json=h.payload(platform="demo", emulates="linux"), headers=headers
    )
    assert (
        value(
            "observenet_http_request_duration_seconds_count",
            method="GET",
            route="/api/v1/health",
            status="200",
        )
        == before_http + 1
    )
    assert value("observenet_ingest_batches_total", outcome="accepted") == before_ok + 1
    assert value("observenet_ingest_batches_total", outcome="rejected_409") == before_rejected + 1


async def test_metrics_are_never_served_by_the_public_app(admin_client: httpx.AsyncClient) -> None:
    for path in ("/metrics", "/api/v1/metrics", "/internal/metrics", "/api/metrics"):
        resp = await admin_client.get(path)
        assert resp.status_code == 404 and "observenet_http_request_duration" not in resp.text


async def test_scheduler_queue_and_freshness_gauges() -> None:
    from prometheus_client import REGISTRY

    from observenet.workers.scheduler import _observe_queues

    sid = await h.make_source(name="gauge-host")
    await h.process(sid, h.payload(), received_at=utcnow() - timedelta(seconds=30))
    await get_redis().rpush("observenet:ingest", "a", "b", "c")
    await _observe_queues()
    assert REGISTRY.get_sample_value("observenet_queue_depth", {"queue": "ingest"}) == 3
    age = REGISTRY.get_sample_value("observenet_source_data_age_seconds", {"source": "gauge-host"})
    assert age is not None and 29 <= age <= 40


async def test_periodic_maintenance_actors_run_end_to_end() -> None:
    """The actor bodies the scheduler enqueues: partitions+retention and rollups are callable and idempotent."""
    from observenet.workers import tasks

    await (
        tasks.run_retention.fn.__wrapped__()
    )  # the scheduler's first act: today's partition exists before data arrives
    sid = await h.make_source()
    past = utcnow().replace(second=0, microsecond=0) - timedelta(minutes=5)
    await h.process(
        sid,
        h.payload(
            samples=[
                h.sample("net.rx_bps", 10.0, past),
                h.sample("net.rx_bps", 30.0, past + timedelta(seconds=30)),
            ]
        ),
    )
    for _ in range(2):
        await tasks.run_retention.fn.__wrapped__()
        await tasks.run_rollups.fn.__wrapped__()
    async with session_scope() as session:
        (rollup,) = (await session.execute(select(MetricRollup1m))).scalars().all()
        targets = (await session.execute(select(ProbeTarget))).scalars().all()
    assert (rollup.avg, rollup.count) == (20.0, 2) and targets == []
    names = {n async for n in _partitions()}
    assert {ts.partition_name(utcnow() + timedelta(days=d)) for d in (1, 2)} <= names
    if past.date() == utcnow().date():  # (not across midnight) today's samples sit in today's partition
        assert ts.partition_name(utcnow()) in names


async def _partitions() -> Any:
    from sqlalchemy import text

    async with session_scope() as session:
        for name in (await session.execute(text(
            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid JOIN pg_class p ON p.oid = i.inhparent "
            "WHERE p.relname = 'metric_samples'"))).scalars():  # fmt: skip
            yield name
