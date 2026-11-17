"""No fake observability: unknown stays unknown, WAN is never substituted, demo never leaks."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from observenet.core.db import session_scope
from observenet.core.redis import CURRENT_HASH, get_redis
from observenet.core.time import utcnow
from observenet.models import (
    Alert,
    Asset,
    DataSource,
    Event,
    Finding,
    MetricSeries,
    NetworkSegment,
    ProbeTarget,
)
from observenet.providers.base import NullRouterProvider, RouterCapability, UnsupportedCapability
from observenet.providers.client_import import ClientListImportProvider, ImportError_, parse_client_list
from observenet.services.settings import FreshnessSettings
from observenet.services.views import freshness_state
from observenet.workers import tasks
from observenet.workers.broker import broker
from tests import helpers as h

INGEST = "/api/v1/ingest/agent"


def kpis(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {k["key"]: k for k in body["kpis"]}


async def drain_queue() -> None:
    import dramatiq

    while not broker.queues["ingest"].empty():
        message = dramatiq.Message.decode(broker.queues["ingest"].get_nowait())
        await tasks.process_ingest.fn.__wrapped__(*message.args)


async def send(client: httpx.AsyncClient, token: str, body: dict[str, Any]) -> None:
    resp = await client.post(INGEST, json=body, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 202, resp.text
    await drain_queue()


# ---------------------------------------------------------------- empty real-mode database


async def test_overview_of_an_empty_real_mode_database_fabricates_nothing(
    admin_client: httpx.AsyncClient,
) -> None:
    body = (await admin_client.get("/api/v1/overview")).json()
    assert body["demo_mode"] is False
    k = kpis(body)
    assert set(k) == {
        "devices",
        "wan_download",
        "wan_upload",
        "host_rx",
        "host_tx",
        "gateway_latency",
        "packet_loss",
        "open_findings",
    }
    for key, kpi in k.items():
        assert kpi["value"] is None, f"{key} shows a number although nothing was measured"
        assert kpi["state"] in ("unknown", "unavailable", "unsupported"), key
        assert kpi["reason"], f"{key} gives no reason for being empty"
    assert k["wan_download"]["state"] == k["wan_upload"]["state"] == "unsupported"
    assert k["devices"]["state"] == "unknown" and k["open_findings"]["state"] == "unknown"
    assert k["host_rx"]["state"] == "unavailable" and k["gateway_latency"]["state"] == "unavailable"
    assert body["internet"]["status"] == "unknown"
    assert (
        body["per_device_traffic"]["supported"] is False
        and "gateway integration" in body["per_device_traffic"]["guidance"]
    )
    assert body["active_alerts"] == [] and body["recent_device_events"] == []
    assert body["posture"]["checked"] is False

    assert (await admin_client.get("/api/v1/devices")).json()["items"] == []
    assert (await admin_client.get("/api/v1/findings", params={"status": "all"})).json() == []
    assert (await admin_client.get("/api/v1/metrics/current")).json() == []
    assert (await admin_client.get("/api/v1/metrics/series")).json() == []
    insights = (await admin_client.get("/api/v1/insights")).json()
    assert insights["insights"] == [] and insights["not_computed"]  # says what could not be computed and why
    connectivity = (await admin_client.get("/api/v1/connectivity/summary")).json()
    assert all(
        t["state"] == "no_data" and t["loss_pct"] is None and t["latency_avg_ms"] is None
        for t in connectivity["targets"]
    )
    assert connectivity["wifi"]["status"] == "unsupported"
    async with session_scope() as session:
        assert (await session.execute(select(func.count()).select_from(MetricSeries))).scalar_one() == 0


async def test_capabilities_are_honest_when_nothing_is_connected(admin_client: httpx.AsyncClient) -> None:
    body = (await admin_client.get("/api/v1/capabilities")).json()
    caps = {c["key"]: c for c in body["network"]}
    assert caps["wan_throughput"]["status"] == "unsupported" and caps["wan_throughput"]["guidance"]
    assert (
        caps["per_device_traffic"]["status"] == "unsupported" and caps["per_device_traffic"]["sources"] == []
    )
    for key in ("wifi_clients_signal", "router_settings", "flow_data"):
        assert caps[key]["status"] == "unsupported"
    assert caps["host_nic_throughput"]["status"] == "unavailable"
    assert caps["capacity_test"]["status"] == "disabled"
    assert caps["device_inventory"]["status"] == "unavailable"
    router = body["router_provider"]
    assert router["configured"] == "none"
    assert all(c["status"] == "unsupported" for c in router["capabilities"])
    assert all(a["status"] == "not_implemented" and a["blocked_on"] for a in router["planned_adapters"])


# ---------------------------------------------------------------- host NIC only


async def test_host_nic_traffic_is_never_presented_as_wan_or_per_device(
    admin_client: httpx.AsyncClient, agent_token: str
) -> None:
    now = utcnow()
    target = (await admin_client.get("/api/v1/probe-targets")).json()[0]["id"]
    body = h.payload(
        samples=[h.sample("net.rx_bps", 42_000_000.0, now), h.sample("net.tx_bps", 3_000_000.0, now)],
        probe_results=[{"target_id": target, "ts": h.ago(s, now).isoformat(), "success": True, "latency_ms": 2.0 + s, "method": "icmp_dgram"}
                       for s in range(3)],
        heartbeat={"ts": now.isoformat(), "errors": {}, "capabilities": [
            {"name": "nic_counters", "status": "supported", "detail": "x"},
            {"name": "neighbor_cache", "status": "supported", "detail": "x"},
            {"name": "listening_sockets", "status": "supported", "detail": "x"}]},
        inventory=h.inventory(now, [("192.168.1.1", "3c:22:fb:00:00:01"), ("192.168.1.20", "3c:22:fb:00:00:0a")]),
    )  # fmt: skip
    await send(admin_client, agent_token, body)

    k = kpis((await admin_client.get("/api/v1/overview")).json())
    assert k["host_rx"]["value"] == 42_000_000.0 and k["host_rx"]["state"] == "ok"
    assert k["host_rx"]["observation_point"] == "host_nic" and k["host_rx"]["source"] == "test-host · eth0"
    assert k["host_rx"]["ts"] is not None and k["host_rx"]["unit"] == "bit/s"
    # the WAN cards stay empty: a LAN host's NIC counters are a different metric
    for key in ("wan_download", "wan_upload"):
        assert k[key]["value"] is None and k[key]["state"] == "unsupported" and "gateway" in k[key]["reason"]
    assert (
        k["gateway_latency"]["value"] == pytest.approx(3.0)
        and k["gateway_latency"]["detail"]["sample_size"] == 3
    )
    assert (
        k["gateway_latency"]["detail"]["window_s"] == 60
        and k["gateway_latency"]["observation_point"] == "active_probe"
    )
    assert k["packet_loss"]["value"] == 0.0
    assert k["devices"]["value"] == 3.0 and "partial" in k["devices"]["detail"]["coverage"]
    assert (
        k["open_findings"]["value"] == 0.0 and k["open_findings"]["state"] == "ok"
    )  # checked and clean ≠ unknown

    top = (await admin_client.get("/api/v1/traffic/top-devices")).json()
    assert (
        top["supported"] is False
        and top["devices"] == []
        and "Per-device traffic requires" in top["guidance"]
    )

    caps = {c["key"]: c for c in (await admin_client.get("/api/v1/capabilities")).json()["network"]}
    assert caps["host_nic_throughput"]["status"] == "supported" and caps["host_nic_throughput"][
        "sources"
    ] == ["test-host"]
    assert (
        caps["wan_throughput"]["status"] == "unsupported"
        and caps["per_device_traffic"]["status"] == "unsupported"
    )
    assert (
        caps["device_inventory"]["status"] == "partial"
        and "not a complete inventory" in caps["device_inventory"]["detail"]
    )
    assert caps["active_probes"]["status"] == "supported"

    current = (await admin_client.get("/api/v1/metrics/current", params={"metric": "net.rx_bps"})).json()
    assert [(c["label"], c["value"], c["unit"], c["freshness"], c["quality"]) for c in current] == [
        ("eth0", 42_000_000.0, "bit/s", "live", "measured")
    ]
    for metric in ("wan.rx_bps", "device.rx_bps"):
        assert (await admin_client.get("/api/v1/metrics/series", params={"metric": metric})).json() == []


async def test_stale_and_disconnected_values_are_labelled_and_then_withheld(
    admin_client: httpx.AsyncClient, agent_token: str
) -> None:
    now = utcnow()
    await send(admin_client, agent_token, h.payload(samples=[h.sample("net.rx_bps", 1000.0, now)]))
    source_id = (await admin_client.get("/api/v1/sources")).json()[0]["id"]
    key = f"{source_id}|net.rx_bps|eth0"

    async def age_value(seconds: float) -> dict[str, Any]:
        await get_redis().hset(CURRENT_HASH, key, json.dumps({"v": 1000.0, "t": (utcnow() - timedelta(seconds=seconds)).isoformat(),
                                                              "u": "bit/s", "q": "measured"}))  # fmt: skip
        return kpis((await admin_client.get("/api/v1/overview")).json())["host_rx"]

    fresh = await age_value(1)
    assert (fresh["state"], fresh["value"]) == ("ok", 1000.0)
    stale = await age_value(30)
    assert (stale["state"], stale["value"]) == ("stale", 1000.0) and "30 s old" in stale["reason"]
    gone = await age_value(600)
    assert gone["state"] == "unavailable" and gone["value"] is None  # an old number is not shown as current
    assert "not reporting" in gone["reason"] and gone["ts"] is not None


def test_freshness_state_thresholds() -> None:
    fresh = FreshnessSettings()  # stale after 10 s, disconnected after 90 s
    now = utcnow()
    assert freshness_state(None, now, fresh) == "never"
    assert freshness_state(now - timedelta(seconds=5), now, fresh) == "live"
    assert freshness_state(now - timedelta(seconds=30), now, fresh) == "stale"
    assert freshness_state(now - timedelta(seconds=120), now, fresh) == "disconnected"
    # a source that reports every 30 s is not "stale" after 60 s
    assert freshness_state(now - timedelta(seconds=60), now, fresh, expected_interval_s=30) == "live"


async def test_sources_endpoint_reports_freshness_and_source_test_is_non_invasive(
    admin_client: httpx.AsyncClient, agent_token: str
) -> None:
    await send(admin_client, agent_token, h.payload(samples=[h.sample("net.rx_bps", 1.0, utcnow())]))
    (source,) = (await admin_client.get("/api/v1/sources")).json()
    assert source["freshness"] == "live" and source["kind"] == "linux_agent" and source["is_demo"] is False
    result = (await admin_client.post(f"/api/v1/sources/{source['id']}/test")).json()
    assert result["ok"] is True and {c["name"] for c in result["checks"]} == {
        "enabled",
        "recent_data",
        "collector_errors",
        "active_series",
    }
    async with session_scope() as session:
        row = await session.get(DataSource, source["id"])
        assert (
            row is not None and row.posture_requested_at is not None
        )  # asks the agent; sends no probe itself
        row.last_seen_at = utcnow() - timedelta(hours=1)
    (source,) = (await admin_client.get("/api/v1/sources")).json()
    assert source["freshness"] == "disconnected"
    assert (await admin_client.post(f"/api/v1/sources/{source['id']}/test")).json()["ok"] is False


# ---------------------------------------------------------------- per-device traffic needs a gateway source


async def test_top_devices_appear_only_with_gateway_per_device_counters(
    admin_client: httpx.AsyncClient, agent_token: str
) -> None:
    now = utcnow()
    mac_a, mac_b = "3c:22:fb:00:00:0a", "3c:22:fb:00:00:0b"
    host = h.payload(
        inventory=h.inventory(now, [("192.168.1.20", mac_a), ("192.168.1.21", mac_b)], gateway=None)
    )
    await send(admin_client, agent_token, host)
    assert (await admin_client.get("/api/v1/traffic/top-devices")).json()["supported"] is False

    gateway_token, _ = await h.issue_token("gateway")
    gw = h.payload(agent_uid="rtr-test-gateway-01", hostname="gateway", platform="router", samples=[
        h.sample("wan.rx_bps", 50e6, now, label="wan0"), h.sample("wan.tx_bps", 5e6, now, label="wan0"),
        h.sample("device.rx_bps", 30e6, now, label=mac_a), h.sample("device.tx_bps", 1e6, now, label=mac_a),
        h.sample("device.rx_bps", 9e6, now, label=mac_b),
        h.sample("device.rx_bps", 1e6, now, label="3c:22:fb:00:00:ff"),  # a client the inventory has not seen
    ])  # fmt: skip
    await send(admin_client, gateway_token, gw)

    top = (await admin_client.get("/api/v1/traffic/top-devices")).json()
    assert top["supported"] is True and "as reported by the gateway" in top["coverage"]
    assert [(d["mac"], d["rx_bps"], d["tx_bps"]) for d in top["devices"]] == [
        (mac_a, 30e6, 1e6), (mac_b, 9e6, 0.0), ("3c:22:fb:00:00:ff", 1e6, 0.0)]  # fmt: skip
    assert top["devices"][0]["source"] == "gateway" and top["devices"][0]["asset_id"] is not None
    assert top["devices"][2]["name"] == "3c:22:fb:00:00:ff" and "asset_id" not in top["devices"][2]

    k = kpis((await admin_client.get("/api/v1/overview")).json())
    assert (
        k["wan_download"]["value"],
        k["wan_download"]["state"],
        k["wan_download"]["observation_point"],
    ) == (50e6, "ok", "gateway_wan")
    assert k["wan_upload"]["value"] == 5e6
    async with session_scope() as session:
        points = dict(
            (
                await session.execute(select(MetricSeries.metric, MetricSeries.observation_point).distinct())
            ).all()
        )
    assert points["wan.rx_bps"] == "gateway_wan" and points["device.rx_bps"] == "gateway_per_device"


# ---------------------------------------------------------------- demo / real separation


async def test_demo_data_is_purged_when_starting_in_real_mode(set_config: Any) -> None:
    from observenet.api.app import purge_demo_data

    real = await h.make_source()
    now = utcnow()
    await h.process(real, h.payload(samples=[h.sample("net.rx_bps", 1.0, now)],
                                    inventory=h.inventory(now, [("192.168.1.20", "3c:22:fb:00:00:0a")], gateway=None),
                                    posture=h.posture(now, listening=h.listening(("tcp", "0.0.0.0", 8080)))))  # fmt: skip
    demo = await h.make_source(
        agent_uid="demo-linux-0001", name="demo-linux-server", kind="demo", is_demo=True
    )
    await h.process(demo, h.payload(
        agent_uid="demo-linux-0001", hostname="demo-linux-server", platform="demo", emulates="linux",
        samples=[h.sample("net.rx_bps", 5e6, now, quality="synthetic")],
        inventory=h.inventory(now, [("192.168.50.10", "00:00:5e:00:53:10")], own_ip="192.168.50.5", own_mac="00:00:5e:00:53:a0", gateway=None),
        posture=h.posture(now, listening=h.listening(("tcp", "0.0.0.0", 6379))),
    ))  # fmt: skip
    async with session_scope() as session:
        session.add(
            ProbeTarget(name="Demo gateway", kind="gateway_icmp", scope="gateway", host="auto", is_demo=True)
        )
        session.add(Alert(kind="new_device", dedup_key="demo", title="demo", summary="s", is_demo=True))
    await get_redis().hset(
        CURRENT_HASH, mapping={f"{demo}|net.rx_bps|eth0": "{}", f"{real}|net.rx_bps|eth0": "{}"}
    )

    async with session_scope() as session:
        assert (
            await session.execute(select(func.count()).select_from(Asset).where(Asset.is_demo.is_(True)))
        ).scalar_one() == 2

    assert await purge_demo_data() > 0
    async with session_scope() as session:
        for model in (Asset, Finding, Event, Alert, NetworkSegment, ProbeTarget, DataSource):
            left = (
                await session.execute(select(func.count()).select_from(model).where(model.is_demo.is_(True)))
            ).scalar_one()
            assert left == 0, f"demo rows left in {model.__tablename__}"
        assert (
            await session.execute(
                select(func.count()).select_from(MetricSeries).where(MetricSeries.quality == "synthetic")
            )
        ).scalar_one() == 0
        assert (
            await session.execute(select(func.count()).select_from(Asset))
        ).scalar_one() == 2  # real host + real neighbour
        assert (await session.execute(select(Finding))).scalar_one().subject == "tcp/*:8080"
    assert list(await get_redis().hkeys(CURRENT_HASH)) == [f"{real}|net.rx_bps|eth0"]
    assert await purge_demo_data() == 0


async def test_demo_controls_are_not_mounted_in_real_mode(admin_client: httpx.AsyncClient) -> None:
    assert (await admin_client.get("/api/v1/demo/state")).status_code == 404
    assert (
        await admin_client.post("/api/v1/demo/fix", json={"item": "smb1", "fixed": True})
    ).status_code == 404


async def test_demo_mode_is_flagged_everywhere_and_its_data_is_marked_synthetic(set_config: Any) -> None:
    from observenet.api.app import bootstrap, create_app
    from observenet.auth import service as auth_service
    from observenet.services import ingest as ingest_service
    from observenet.services.demo import DemoGenerator
    from tests.conftest import ADMIN_PASSWORD, login

    set_config(demo_mode="true")
    await bootstrap()
    app = create_app()
    async with session_scope() as session:
        await auth_service.create_user(session, username="admin", password=ADMIN_PASSWORD, role="admin")
        assert (
            await session.execute(
                select(func.count()).select_from(ProbeTarget).where(ProbeTarget.is_demo.is_(False))
            )
        ).scalar_one() == 0

    gen = DemoGenerator()
    await gen.setup()
    queued: list[tuple[int, dict[str, Any], str]] = []

    async def enqueue(source_id: int, payload: dict[str, Any], received_at: str) -> None:
        queued.append((source_id, payload, received_at))

    now = utcnow()
    payloads = await gen.payloads(now, set())
    assert {p.agent.platform for _uid, p in payloads} == {"demo"}
    assert all(s.quality == "synthetic" for _uid, p in payloads for s in p.samples)
    assert {m for _uid, p in payloads for m in (s.metric for s in p.samples)} >= {
        "wan.rx_bps",
        "device.rx_bps",
        "net.rx_bps",
    }
    for uid, payload in payloads:
        async with session_scope() as session:
            token = await session.merge(gen.tokens[uid])
            await ingest_service.accept(
                session, get_redis(), token=token, payload=payload, enqueue=enqueue, now=now
            )
    for args in queued:
        await tasks.process_ingest.fn.__wrapped__(*args)

    async with session_scope() as session:
        assert all(
            s.is_demo and s.kind == "demo" for s in (await session.execute(select(DataSource))).scalars()
        )
        assert all(a.is_demo for a in (await session.execute(select(Asset))).scalars())
        assert all(f.is_demo for f in (await session.execute(select(Finding))).scalars())
        assert {s.quality for s in (await session.execute(select(MetricSeries))).scalars()} == {"synthetic"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        assert (await c.get("/api/v1/meta")).json()["demo_mode"] is True
        me = await login(c, "admin", ADMIN_PASSWORD)
        assert me.json()["demo_mode"] is True
        overview = (await c.get("/api/v1/overview")).json()
        assert overview["demo_mode"] is True
        assert kpis(overview)["wan_download"]["detail"]["synthetic"] is True
        assert all(d["is_demo"] for d in (await c.get("/api/v1/devices")).json()["items"])
        assert all(f["is_demo"] for f in (await c.get("/api/v1/findings")).json())
        assert all(s["is_demo"] for s in (await c.get("/api/v1/sources")).json())
        state = (await c.get("/api/v1/demo/state")).json()
        assert {i["item"]: i["fixed"] for i in state["items"]} == {
            "windows-firewall": False,
            "smb1": False,
            "redis-bind": False,
        }
        assert (await c.post("/api/v1/demo/fix", json={"item": "smb1", "fixed": True})).status_code == 200
        assert (
            await c.post("/api/v1/demo/fix", json={"item": "everything", "fixed": True})
        ).status_code == 422
    assert await gen.fixed() == {"smb1"}


async def test_demo_generator_refuses_to_run_in_real_mode() -> None:
    import asyncio

    from observenet.services.demo import run_demo_generator

    await asyncio.wait_for(
        run_demo_generator(asyncio.Event()), timeout=1
    )  # returns immediately, emits nothing
    async with session_scope() as session:
        assert (await session.execute(select(func.count()).select_from(DataSource))).scalar_one() == 0


# ---------------------------------------------------------------- router provider layer + client-list import


async def test_null_router_provider_supports_nothing_and_raises_instead_of_inventing() -> None:
    provider = NullRouterProvider()
    assert {c.capability for c in provider.capabilities()} == set(RouterCapability)
    assert not any(provider.supports(c) for c in RouterCapability)
    for call in (provider.read_clients, provider.read_wan_counters, provider.read_per_device_counters,
                 provider.read_wifi_clients, provider.read_settings):  # fmt: skip
        with pytest.raises(UnsupportedCapability):
            await call()


def test_parse_client_list_csv_reports_bad_rows_and_skips_public_addresses() -> None:
    now = utcnow()
    content = (
        "IP Address,MAC Address,Device Name\n"
        "192.168.1.10,AA-BB-CC-00-11-22,Living Room TV\n"
        "192.168.1.11,,printer\n"
        "8.8.8.8,3c:22:fb:00:00:01,public\n"
        "not-an-ip,3c:22:fb:00:00:02,bad\n"
        ",3c:22:fb:00:00:03,no ip\n"
        "127.0.0.1,3c:22:fb:00:00:04,loopback\n"
    )
    records, errors = parse_client_list(content, now)
    assert [(r.ip, r.mac, r.hostname) for r in records] == [
        ("192.168.1.10", "aa:bb:cc:00:11:22", "Living Room TV"), ("192.168.1.11", None, "printer")]  # fmt: skip
    assert all(r.observed_at == now for r in records)
    assert len(errors) == 4
    assert "row 3: 8.8.8.8 is not a private LAN address" in errors[0]
    assert "row 4" in errors[1] and "row 5: no IP address column" in errors[2] and "row 6" in errors[3]


def test_parse_client_list_json_shapes() -> None:
    now = utcnow()
    as_list = json.dumps(
        [
            {"ip": "10.0.0.5", "mac": "3C:22:FB:00:00:01", "hostname": "nas"},
            {"address": "10.0.0.6", "name": "cam"},
        ]
    )
    as_object = json.dumps(
        {"clients": [{"ipv4": "10.0.0.7", "hwaddr": "3c:22:fb:00:00:07", "host": "phone"}]}
    )
    assert [(r.ip, r.mac, r.hostname) for r in parse_client_list(as_list, now)[0]] == [
        ("10.0.0.5", "3c:22:fb:00:00:01", "nas"),
        ("10.0.0.6", None, "cam"),
    ]
    assert [(r.ip, r.mac, r.hostname) for r in parse_client_list(as_object, now)[0]] == [
        ("10.0.0.7", "3c:22:fb:00:00:07", "phone")
    ]
    for bad in ("", "   ", "[1, 2]", "{broken", '{"clients": "nope"}'):
        with pytest.raises(ImportError_):
            parse_client_list(bad, now)
    with pytest.raises(ImportError_, match="too many rows"):
        parse_client_list("ip\n" + "192.168.1.1\n" * 2001, now)


def test_client_import_provider_declares_point_in_time_inventory_only() -> None:
    provider = ClientListImportProvider([])
    assert provider.supports(RouterCapability.DEVICE_INVENTORY)
    assert not provider.supports(RouterCapability.PER_DEVICE_TRAFFIC) and not provider.supports(
        RouterCapability.WAN_COUNTERS
    )
    inventory = next(c for c in provider.capabilities() if c.capability == RouterCapability.DEVICE_INVENTORY)
    assert inventory.quality == "point_in_time" and "Not live" in (inventory.coverage or "")


async def test_client_list_import_endpoint_creates_and_matches_devices(
    admin_client: httpx.AsyncClient,
) -> None:
    sid = await h.make_source()
    await h.process(
        sid, h.payload(inventory=h.inventory(utcnow(), [("192.168.1.10", "aa:bb:cc:00:11:22")], gateway=None))
    )
    content = "ip,mac,hostname\n192.168.1.10,aa:bb:cc:00:11:22,living-room-tv\n192.168.1.30,3c:22:fb:00:00:30,printer\n1.2.3.4,,x\n"
    resp = await admin_client.post("/api/v1/devices/import", json={"content": content})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["created"], body["matched"], body["rows"]) == (1, 1, 2)
    assert body["row_errors"] == ["row 3: 1.2.3.4 is not a private LAN address; skipped"]
    devices = {d["primary_mac"]: d for d in (await admin_client.get("/api/v1/devices")).json()["items"]}
    assert devices["aa:bb:cc:00:11:22"]["hostname"] == "living-room-tv"  # enriched, not duplicated
    assert devices["3c:22:fb:00:00:30"]["name"] == "printer"
    assert (
        await admin_client.post("/api/v1/devices/import", json={"content": "{not json"})
    ).status_code == 422
    sources = {s["kind"]: s for s in (await admin_client.get("/api/v1/sources")).json()}
    assert sources["client_import"]["observation_point"] == "gateway"
    router = (await admin_client.get("/api/v1/capabilities")).json()["router_provider"]
    assert router["configured"] == "client-list-import"
    inventory = next(c for c in router["capabilities"] if c["capability"] == "device_inventory")
    assert inventory["status"] == "supported" and "Point-in-time" in inventory["detail"]
    assert all(
        c["status"] == "unsupported" for c in router["capabilities"] if c["capability"] != "device_inventory"
    )
    again = (await admin_client.post("/api/v1/devices/import", json={"content": content})).json()
    assert (again["created"], again["matched"]) == (0, 2)  # re-import is idempotent
