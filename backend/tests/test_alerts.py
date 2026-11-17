"""DB-backed alert evaluation: debounce, hysteresis, dedup, maintenance, event alerts, endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from observenet.core.db import session_scope
from observenet.core.time import utcnow
from observenet.models import (
    Alert,
    AlertRule,
    DataSource,
    Event,
    MaintenanceWindow,
    Notification,
    ProbeResult,
)
from observenet.services import alerts as alert_service
from tests import helpers as h

pytestmark = pytest.mark.usefixtures("default_rules")


async def tune(kind: str, enabled: bool | None = None, **params: Any) -> None:
    async with session_scope() as session:
        rule = (await session.execute(select(AlertRule).where(AlertRule.kind == kind))).scalar_one()
        rule.params = {**rule.params, **params}
        if enabled is not None:
            rule.enabled = enabled


async def only(*kinds: str) -> None:
    """Disable every threshold rule except the ones under test."""
    async with session_scope() as session:
        for rule in (await session.execute(select(AlertRule))).scalars():
            rule.enabled = rule.kind in kinds


async def probes(target: int, source: int, points: list[tuple[datetime, float | None]]) -> None:
    async with session_scope() as session:
        for ts, latency in points:
            session.add(ProbeResult(target_id=target, source_id=source, ts=ts, success=latency is not None,
                                    latency_ms=latency, method="fixture"))  # fmt: skip


async def evaluate(now: datetime) -> dict[str, int]:
    async with session_scope() as session:
        return await alert_service.evaluate_rules(session, now=now)


async def alerts() -> list[Alert]:
    async with session_scope() as session:
        return list((await session.execute(select(Alert).order_by(Alert.id))).scalars())


async def test_default_rules_are_seeded_once_with_safe_defaults() -> None:
    async with session_scope() as session:
        await alert_service.ensure_default_rules(session)  # idempotent
        rules = {r.kind: r for r in (await session.execute(select(AlertRule))).scalars()}
    assert set(rules) == {"gateway_latency_high", "packet_loss_high", "collector_heartbeat_missing",
                          "interface_bandwidth_high", "new_device", "stale_source", "posture_changed"}  # fmt: skip
    assert rules["interface_bandwidth_high"].enabled is False  # needs a per-link threshold
    lat = rules["gateway_latency_high"].params
    assert lat["clear_threshold_ms"] < lat["threshold_ms"] and lat["for_s"] > 0 and lat["cooldown_s"] > 0


async def test_latency_alert_debounce_dedup_hysteresis_and_resolution() -> None:
    await only("gateway_latency_high")
    await tune("gateway_latency_high", threshold_ms=50, clear_threshold_ms=35, window_s=60, for_s=20, clear_for_s=20,
               cooldown_s=0, min_samples=3)  # fmt: skip
    sid, target = await h.make_source(), await h.make_target()
    t0 = utcnow().replace(microsecond=0)
    await probes(target, sid, [(t0 - timedelta(seconds=s), 80.0) for s in range(0, 60, 5)])

    assert await evaluate(t0) == {"fired": 0, "resolved": 0}  # breach seen, debounce running
    assert await alerts() == []
    assert await evaluate(t0 + timedelta(seconds=10)) == {"fired": 0, "resolved": 0}
    assert await evaluate(t0 + timedelta(seconds=20)) == {"fired": 1, "resolved": 0}
    assert await evaluate(t0 + timedelta(seconds=25)) == {"fired": 0, "resolved": 0}  # deduplicated
    (alert,) = await alerts()
    assert alert.kind == "gateway_latency_high" and alert.state == "firing"
    assert alert.dedup_key == f"gateway_latency_high:{target}"
    assert alert.source_ts == t0  # timestamp of the newest measurement behind it
    assert alert.details["latency_avg_ms"] == pytest.approx(80.0) and alert.details["sample_size"] >= 3
    assert "exceeds 50 ms" in alert.summary

    # 40 ms: under the fire threshold but above the clear threshold -> stays firing
    await probes(target, sid, [(t0 + timedelta(seconds=s), 40.0) for s in range(30, 150, 5)])
    for s in (100, 120, 145):
        assert await evaluate(t0 + timedelta(seconds=s)) == {"fired": 0, "resolved": 0}
    assert (await alerts())[0].state == "firing"

    # 10 ms sustained: clears only after clear_for_s
    await probes(target, sid, [(t0 + timedelta(seconds=s), 10.0) for s in range(150, 300, 5)])
    assert await evaluate(t0 + timedelta(seconds=215)) == {"fired": 0, "resolved": 0}  # clear streak starts
    assert await evaluate(t0 + timedelta(seconds=225)) == {"fired": 0, "resolved": 0}
    assert await evaluate(t0 + timedelta(seconds=235)) == {"fired": 0, "resolved": 1}
    (alert,) = await alerts()
    assert alert.state == "resolved" and alert.resolved_reason == "condition cleared"
    async with session_scope() as session:
        notes = [
            (n.channel, n.event, n.status)
            for n in (await session.execute(select(Notification).order_by(Notification.id))).scalars()
        ]
        kinds = [e.kind for e in (await session.execute(select(Event).order_by(Event.id))).scalars()]
    assert notes == [("in_app", "fired", "delivered"), ("in_app", "resolved", "delivered")]
    assert kinds == ["alert.fired", "alert.resolved"]


async def test_one_second_blip_does_not_alert() -> None:
    await only("gateway_latency_high")
    await tune("gateway_latency_high", window_s=60, for_s=60, min_samples=20)
    sid, target = await h.make_source(), await h.make_target()
    t0 = utcnow().replace(microsecond=0)
    points = [(t0 - timedelta(seconds=s), 2.0) for s in range(1, 60)] + [(t0, 900.0)]  # a single spike
    await probes(target, sid, points)
    for s in range(0, 120, 10):
        assert (await evaluate(t0 + timedelta(seconds=s)))["fired"] == 0
    assert await alerts() == []


async def test_missing_data_neither_fires_nor_resolves() -> None:
    await only("packet_loss_high")
    await tune(
        "packet_loss_high",
        threshold_pct=5,
        clear_threshold_pct=2,
        window_s=60,
        for_s=0,
        clear_for_s=0,
        cooldown_s=0,
        min_samples=5,
    )
    sid, target = await h.make_source(), await h.make_target()
    t0 = utcnow().replace(microsecond=0)
    assert await evaluate(t0) == {"fired": 0, "resolved": 0}  # no probes at all: unknown, not "0% loss"
    await probes(target, sid, [(t0 - timedelta(seconds=s), None) for s in range(0, 4)])
    assert await evaluate(t0) == {"fired": 0, "resolved": 0}  # 4 samples < min_samples: still unknown
    await probes(target, sid, [(t0 - timedelta(seconds=s), None) for s in range(4, 10)])
    assert await evaluate(t0) == {"fired": 1, "resolved": 0}  # 100% loss on enough samples
    (alert,) = await alerts()
    assert "ICMP-filtering gateway looks the same" in alert.summary
    # the collector goes silent: no data in the window -> the alert must NOT auto-resolve
    assert await evaluate(t0 + timedelta(minutes=10)) == {"fired": 0, "resolved": 0}
    assert (await alerts())[0].state == "firing"


async def test_database_allows_only_one_active_alert_per_dedup_key() -> None:
    async with session_scope() as session:
        session.add(Alert(kind="new_device", dedup_key="k1", state="firing", title="a", summary="a"))
    with pytest.raises(IntegrityError):
        async with session_scope() as session:
            session.add(
                Alert(kind="new_device", dedup_key="k1", state="acknowledged", title="b", summary="b")
            )
    async with session_scope() as session:
        (await session.execute(select(Alert))).scalar_one().state = "resolved"
    async with session_scope() as session:  # once resolved, the key may alert again
        session.add(Alert(kind="new_device", dedup_key="k1", state="firing", title="c", summary="c"))
    assert [a.state for a in await alerts()] == ["resolved", "firing"]


async def test_maintenance_window_suppresses_firing() -> None:
    await only("collector_heartbeat_missing")
    await tune("collector_heartbeat_missing", threshold_s=90, for_s=0, clear_for_s=0, cooldown_s=0)
    sid = await h.make_source()
    now = utcnow()
    async with session_scope() as session:
        (await session.get(DataSource, sid)).last_seen_at = now - timedelta(minutes=10)  # type: ignore[union-attr]
        session.add(MaintenanceWindow(name="router upgrade", starts_at=now - timedelta(minutes=5),
                                      ends_at=now + timedelta(minutes=30), alert_kinds=[]))  # fmt: skip
    assert await evaluate(now) == {"fired": 0, "resolved": 0}
    assert await alerts() == []
    after = now + timedelta(minutes=31)
    assert await evaluate(after) == {"fired": 1, "resolved": 0}  # still silent once the window ends -> alert
    (alert,) = await alerts()
    assert alert.severity == "critical" and alert.source_id == sid


async def test_maintenance_window_can_be_scoped_to_alert_kinds() -> None:
    now = utcnow()
    async with session_scope() as session:
        session.add(MaintenanceWindow(name="w", starts_at=now - timedelta(minutes=1), ends_at=now + timedelta(minutes=1),
                                      alert_kinds=["packet_loss_high"]))  # fmt: skip
    async with session_scope() as session:
        assert await alert_service.in_maintenance(session, "packet_loss_high", now) is True
        assert await alert_service.in_maintenance(session, "new_device", now) is False
        assert (
            await alert_service.in_maintenance(session, "packet_loss_high", now + timedelta(minutes=5))
            is False
        )


async def test_heartbeat_alert_resolves_when_the_collector_reports_again() -> None:
    await only("collector_heartbeat_missing")
    await tune("collector_heartbeat_missing", threshold_s=90, for_s=0, clear_for_s=10, cooldown_s=0)
    sid = await h.make_source()
    now = utcnow()
    async with session_scope() as session:
        (await session.get(DataSource, sid)).last_seen_at = now - timedelta(minutes=5)  # type: ignore[union-attr]
    assert (await evaluate(now))["fired"] == 1
    async with session_scope() as session:
        (await session.get(DataSource, sid)).last_seen_at = now + timedelta(seconds=5)  # type: ignore[union-attr]
    assert await evaluate(now + timedelta(seconds=6)) == {"fired": 0, "resolved": 0}  # clear must hold 10 s
    assert await evaluate(now + timedelta(seconds=17)) == {"fired": 0, "resolved": 1}


async def test_bandwidth_rule_is_off_by_default_and_fires_when_enabled() -> None:
    sid = await h.make_source()
    now = utcnow().replace(microsecond=0)
    await h.process(
        sid, h.payload(samples=[h.sample("net.rx_bps", 900e6, now - timedelta(seconds=s)) for s in range(30)])
    )
    await only()  # defaults: bandwidth rule disabled
    await tune("interface_bandwidth_high", enabled=False, for_s=0, min_samples=5)
    assert await evaluate(now) == {"fired": 0, "resolved": 0}
    await tune("interface_bandwidth_high", enabled=True)
    assert await evaluate(now) == {"fired": 1, "resolved": 0}
    (alert,) = await alerts()
    assert "measured at host_nic" in alert.summary and alert.details["label"] == "eth0"


async def test_stale_source_alert_when_an_alive_agent_reports_collector_errors() -> None:
    await only("stale_source")
    await tune("stale_source", for_s=0, clear_for_s=0, cooldown_s=0)
    sid = await h.make_source()
    now = utcnow()
    await h.process(
        sid,
        h.payload(
            heartbeat={"ts": now.isoformat(), "errors": {"neighbor_cache": "EACCES"}, "capabilities": []}
        ),
    )
    assert await evaluate(now) == {"fired": 1, "resolved": 0}
    (alert,) = await alerts()
    assert "neighbor_cache (EACCES)" in alert.summary
    later = now + timedelta(seconds=10)
    await h.process(sid, h.payload(heartbeat={"ts": later.isoformat(), "errors": {}, "capabilities": []}))
    assert await evaluate(later) == {"fired": 0, "resolved": 1}


async def test_new_device_alert_is_suppressed_during_the_learning_period_then_deduplicated() -> None:
    sid = await h.make_source()
    t0 = utcnow() - timedelta(minutes=30)
    first = h.inventory(
        t0, [("192.168.1.20", "3c:22:fb:00:00:01"), ("192.168.1.21", "3c:22:fb:00:00:02")], gateway=None
    )
    await h.process(sid, h.payload(inventory=first))  # first inventory: everything is "new"
    assert await alerts() == []
    async with session_scope() as session:  # ...but the sightings are still recorded as events
        assert (
            len(
                (await session.execute(select(Event).where(Event.kind == "device.first_seen")))
                .scalars()
                .all()
            )
            == 2
        )

    still_learning = h.inventory(
        t0 + timedelta(minutes=5), [("192.168.1.22", "3c:22:fb:00:00:03")], gateway=None
    )
    await h.process(sid, h.payload(inventory=still_learning))
    assert await alerts() == []

    newcomer = [("192.168.1.20", "3c:22:fb:00:00:01"), ("192.168.1.77", "3c:22:fb:00:00:77")]
    await h.process(sid, h.payload(inventory=h.inventory(t0 + timedelta(minutes=11), newcomer, gateway=None)))
    await h.process(sid, h.payload(inventory=h.inventory(t0 + timedelta(minutes=12), newcomer, gateway=None)))
    (alert,) = await alerts()  # exactly one: known devices and repeat sightings do not alert
    assert alert.kind == "new_device" and alert.details["ip"] == "192.168.1.77"
    assert "not a threat verdict" in alert.summary and alert.severity == "info"


async def test_new_device_alert_respects_disabled_rule() -> None:
    await tune("new_device", enabled=False)
    sid = await h.make_source()
    t0 = utcnow() - timedelta(minutes=30)
    await h.process(sid, h.payload(inventory=h.inventory(t0, [], gateway=None)))
    await h.process(
        sid,
        h.payload(
            inventory=h.inventory(
                t0 + timedelta(minutes=11), [("192.168.1.77", "3c:22:fb:00:00:77")], gateway=None
            )
        ),
    )
    assert await alerts() == []


async def test_event_alerts_auto_close_after_their_configured_age() -> None:
    now = utcnow()
    async with session_scope() as session:
        rule = (await session.execute(select(AlertRule).where(AlertRule.kind == "new_device"))).scalar_one()
        session.add(Alert(rule_id=rule.id, kind="new_device", dedup_key="new_device:1", title="old", summary="s",
                          started_at=now - timedelta(hours=25)))  # fmt: skip
        session.add(Alert(rule_id=rule.id, kind="new_device", dedup_key="new_device:2", title="recent", summary="s",
                          started_at=now - timedelta(hours=1)))  # fmt: skip
    assert (await evaluate(now))["resolved"] == 1
    states = {a.title: (a.state, a.resolved_reason) for a in await alerts()}
    assert states == {"old": ("resolved", "auto-closed after 24 h"), "recent": ("firing", None)}


# ---------------------------------------------------------------- endpoints


async def seed_alert(**kw: Any) -> int:
    async with session_scope() as session:
        alert = Alert(
            kind="new_device",
            dedup_key=kw.pop("dedup_key", "new_device:9"),
            title="New device",
            summary="s",
            **kw,
        )
        session.add(alert)
        await session.flush()
        return alert.id


async def test_acknowledge_snooze_resolve_endpoints(
    admin_client: httpx.AsyncClient, viewer_client: httpx.AsyncClient
) -> None:
    alert_id = await seed_alert()
    assert [a["id"] for a in (await viewer_client.get("/api/v1/alerts")).json()] == [alert_id]
    assert (await viewer_client.post(f"/api/v1/alerts/{alert_id}/acknowledge")).status_code == 403

    acked = await admin_client.post(f"/api/v1/alerts/{alert_id}/acknowledge")
    assert (
        acked.status_code == 200
        and acked.json()["state"] == "acknowledged"
        and acked.json()["acknowledged_at"]
    )
    assert (await admin_client.post(f"/api/v1/alerts/{alert_id}/acknowledge")).json()[
        "state"
    ] == "acknowledged"  # idempotent

    past = (utcnow() - timedelta(minutes=1)).isoformat()
    assert (
        await admin_client.post(f"/api/v1/alerts/{alert_id}/snooze", json={"until": past})
    ).status_code == 422
    until = (utcnow() + timedelta(hours=2)).isoformat()
    assert (await admin_client.post(f"/api/v1/alerts/{alert_id}/snooze", json={"until": until})).json()[
        "snoozed_until"
    ]

    closed = await admin_client.post(f"/api/v1/alerts/{alert_id}/resolve")
    assert (
        closed.json()["state"] == "resolved"
        and "closed manually by admin" in closed.json()["resolved_reason"]
    )
    assert (await admin_client.post(f"/api/v1/alerts/{alert_id}/acknowledge")).status_code == 409
    assert (await admin_client.get("/api/v1/alerts")).json() == []
    assert len((await admin_client.get("/api/v1/alerts", params={"state": "resolved"})).json()) == 1
    assert (await admin_client.post("/api/v1/alerts/999999/acknowledge")).status_code == 404

    events = [
        e["kind"] for e in (await admin_client.get("/api/v1/events", params={"kind_prefix": "alert."})).json()
    ]
    assert events == [
        "alert.resolved",
        "alert.snoozed",
        "alert.acknowledged",
    ]  # history preserved, newest first
    audit = {a["action"] for a in (await admin_client.get("/api/v1/audit")).json()}
    assert {"alert.acknowledge", "alert.snooze", "alert.resolve"} <= audit


async def test_alert_rule_patch_validates_hysteresis_and_unknown_params(
    admin_client: httpx.AsyncClient,
) -> None:
    rules = {r["kind"]: r for r in (await admin_client.get("/api/v1/alert-rules")).json()}
    rid = rules["gateway_latency_high"]["id"]
    bad = await admin_client.patch(
        f"/api/v1/alert-rules/{rid}", json={"params": {"threshold_ms": 20, "clear_threshold_ms": 30}}
    )
    assert bad.status_code == 422 and "hysteresis" in bad.json()["detail"]
    unknown = await admin_client.patch(f"/api/v1/alert-rules/{rid}", json={"params": {"nonsense": 1}})
    assert unknown.status_code == 422
    assert (
        await admin_client.patch(f"/api/v1/alert-rules/{rid}", json={"severity": "apocalyptic"})
    ).status_code == 422
    ok = await admin_client.patch(
        f"/api/v1/alert-rules/{rid}", json={"enabled": False, "params": {"threshold_ms": 80, "for_s": 120}}
    )
    assert ok.status_code == 200
    assert (
        ok.json()["enabled"] is False
        and ok.json()["params"]["threshold_ms"] == 80
        and ok.json()["params"]["for_s"] == 120
    )
    assert ok.json()["params"]["clear_threshold_ms"] == 35  # untouched params are kept


async def test_maintenance_window_endpoints(
    admin_client: httpx.AsyncClient, viewer_client: httpx.AsyncClient
) -> None:
    now = utcnow()
    body = {
        "name": "ISP work",
        "starts_at": now.isoformat(),
        "ends_at": (now + timedelta(hours=2)).isoformat(),
        "alert_kinds": [],
    }
    assert (await viewer_client.post("/api/v1/maintenance-windows", json=body)).status_code == 403
    created = await admin_client.post("/api/v1/maintenance-windows", json=body)
    assert created.status_code == 201
    inverted = {**body, "ends_at": (now - timedelta(hours=1)).isoformat()}
    assert (await admin_client.post("/api/v1/maintenance-windows", json=inverted)).status_code == 422
    too_long = {**body, "ends_at": (now + timedelta(days=30)).isoformat()}
    assert (await admin_client.post("/api/v1/maintenance-windows", json=too_long)).status_code == 422
    assert (
        await admin_client.post("/api/v1/maintenance-windows", json={**body, "alert_kinds": ["bogus"]})
    ).status_code == 422
    assert len((await viewer_client.get("/api/v1/maintenance-windows")).json()) == 1
    assert (
        await admin_client.delete(f"/api/v1/maintenance-windows/{created.json()['id']}")
    ).status_code == 204
    assert (await admin_client.get("/api/v1/maintenance-windows")).json() == []
