"""Alerting: a pure debounce/hysteresis/cooldown state machine + DB-backed evaluators.

A threshold alert fires only after the breach has held for ``for_s`` seconds, clears only
after the value has stayed below ``clear_threshold`` for ``clear_for_s`` seconds, and
cannot re-fire for ``cooldown_s`` seconds after resolving. Missing data is "unknown":
it neither fires nor clears.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from observenet.core.redis import LIVE_CHANNEL, get_redis
from observenet.core.telemetry import ALERT_TRANSITIONS
from observenet.models import (
    Alert,
    AlertRule,
    CollectorHeartbeat,
    DataSource,
    MaintenanceWindow,
    MetricSample,
    MetricSeries,
    Notification,
    ProbeTarget,
)
from observenet.services.connectivity import compute_window_stats, load_points
from observenet.services.events import add_event

DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "kind": "gateway_latency_high", "name": "Gateway latency sustained high", "severity": "warning",
        "description": "Average gateway round-trip time over the window stays above the threshold.",
        "params": {"threshold_ms": 50, "clear_threshold_ms": 35, "window_s": 60, "for_s": 60, "clear_for_s": 60,
                   "cooldown_s": 300, "min_samples": 20},
    },
    {
        "kind": "packet_loss_high", "name": "Gateway packet loss", "severity": "warning",
        "description": "Share of unanswered gateway probes over the window stays above the threshold.",
        "params": {"threshold_pct": 5, "clear_threshold_pct": 2, "window_s": 60, "for_s": 30, "clear_for_s": 60,
                   "cooldown_s": 300, "min_samples": 20},
    },
    {
        "kind": "collector_heartbeat_missing", "name": "Collector heartbeat missing", "severity": "critical",
        "description": "An agent has not reported for longer than the threshold.",
        "params": {"threshold_s": 90, "for_s": 0, "clear_for_s": 10, "cooldown_s": 120},
    },
    {
        "kind": "interface_bandwidth_high", "name": "Interface throughput above threshold", "severity": "info",
        "enabled": False,
        "description": "Average measured throughput of an interface stays above the threshold. "
                       "Disabled by default: pick a threshold that fits your link.",
        "params": {"threshold_bps": 500_000_000, "clear_threshold_bps": 400_000_000, "window_s": 60, "for_s": 120,
                   "clear_for_s": 60, "cooldown_s": 600, "min_samples": 20},
    },
    {
        "kind": "new_device", "name": "Newly observed device", "severity": "info",
        "description": "A device identity that has never been seen before appeared (after the source's learning period).",
        "params": {"auto_resolve_hours": 24, "learning_minutes": 10},
    },
    {
        "kind": "stale_source", "name": "Data source degraded", "severity": "warning",
        "description": "A source is alive but one of its collectors keeps reporting errors.",
        "params": {"for_s": 120, "clear_for_s": 60, "cooldown_s": 600},
    },
    {
        "kind": "posture_changed", "name": "Posture finding changed", "severity": "warning",
        "description": "A read-only posture check opened or re-opened a finding.",
        "params": {"auto_resolve_hours": 72},
    },
]  # fmt: skip


@dataclass
class KeyState:
    pending_since: datetime | None = None
    clear_since: datetime | None = None
    last_resolved_at: datetime | None = None

    def to_json(self) -> dict[str, str | None]:
        return {k: (v.isoformat() if v else None) for k, v in self.__dict__.items()}

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> KeyState:
        data = data or {}
        return cls(**{k: (datetime.fromisoformat(v) if v else None) for k, v in data.items() if k in cls.__annotations__})


def step(
    state: KeyState,
    *,
    breached: bool | None,
    cleared: bool | None,
    active: bool,
    now: datetime,
    for_s: float,
    clear_for_s: float,
    cooldown_s: float,
) -> tuple[KeyState, str | None]:
    """Advance the alert state machine one evaluation. Returns (state, 'fire'|'resolve'|None)."""
    if breached is None:  # unknown: hold everything
        return state, None
    if not active:
        state.clear_since = None
        if not breached:
            state.pending_since = None
            return state, None
        if state.pending_since is None:
            state.pending_since = now
        in_cooldown = (
            state.last_resolved_at is not None and (now - state.last_resolved_at).total_seconds() < cooldown_s
        )
        if (now - state.pending_since).total_seconds() >= for_s and not in_cooldown:
            state.pending_since = None
            return state, "fire"
        return state, None
    # active alert: only a sustained *clear* (hysteresis) resolves it
    state.pending_since = None
    if not cleared:
        state.clear_since = None
        return state, None
    if state.clear_since is None:
        state.clear_since = now
    if (now - state.clear_since).total_seconds() >= clear_for_s:
        state.clear_since = None
        state.last_resolved_at = now
        return state, "resolve"
    return state, None


@dataclass
class Condition:
    dedup_key: str
    breached: bool | None
    cleared: bool | None
    title: str
    summary: str
    source_ts: datetime | None = None
    asset_id: int | None = None
    source_id: int | None = None
    details: dict[str, Any] = field(default_factory=dict)
    is_demo: bool = False


async def ensure_default_rules(session: AsyncSession) -> None:
    existing = set((await session.execute(select(AlertRule.kind))).scalars())
    for spec in DEFAULT_RULES:
        if spec["kind"] not in existing:
            session.add(AlertRule(kind=spec["kind"], name=spec["name"], description=spec["description"],
                                  severity=spec["severity"], params=spec["params"], enabled=spec.get("enabled", True)))  # fmt: skip
    await session.flush()


async def in_maintenance(session: AsyncSession, kind: str, now: datetime) -> bool:
    windows = (
        await session.execute(
            select(MaintenanceWindow).where(MaintenanceWindow.starts_at <= now, MaintenanceWindow.ends_at > now)
        )
    ).scalars()
    return any(not w.alert_kinds or kind in w.alert_kinds for w in windows)


async def _publish(payload: dict[str, Any]) -> None:
    try:
        await get_redis().publish(LIVE_CHANNEL, json.dumps(payload, default=str))
    except Exception:  # noqa: BLE001, S110 - live fan-out is best effort; the alert is already durable
        pass


async def _active_alert(session: AsyncSession, dedup_key: str) -> Alert | None:
    return (
        await session.execute(select(Alert).where(Alert.dedup_key == dedup_key, Alert.state != "resolved"))
    ).scalar_one_or_none()


async def fire_alert(
    session: AsyncSession, *, rule: AlertRule, cond: Condition, now: datetime, finding_id: int | None = None
) -> Alert | None:
    """Create a deduplicated alert. Returns None when one is already active or suppressed."""
    if await _active_alert(session, cond.dedup_key) is not None:
        return None
    suppressed = await in_maintenance(session, rule.kind, now)
    if suppressed:
        return None
    alert = Alert(
        rule_id=rule.id, kind=rule.kind, dedup_key=cond.dedup_key, state="firing", severity=rule.severity,
        title=cond.title, summary=cond.summary, details=cond.details, asset_id=cond.asset_id,
        source_id=cond.source_id, finding_id=finding_id, source_ts=cond.source_ts, started_at=now,
        last_evaluated_at=now, is_demo=cond.is_demo,
    )  # fmt: skip
    session.add(alert)
    await session.flush()
    session.add(Notification(alert_id=alert.id, channel="in_app", event="fired", status="delivered"))
    add_event(session, "alert.fired", cond.title, severity=rule.severity, asset_id=cond.asset_id,
              source_id=cond.source_id, alert_id=alert.id, is_demo=cond.is_demo)  # fmt: skip
    ALERT_TRANSITIONS.labels(kind=rule.kind, to="firing").inc()
    await _publish({"type": "alert", "event": "fired", "id": alert.id, "kind": rule.kind, "severity": rule.severity,
                    "title": cond.title, "ts": now})  # fmt: skip
    return alert


async def resolve_alert(session: AsyncSession, alert: Alert, *, now: datetime, reason: str) -> None:
    if alert.state == "resolved":
        return
    alert.state = "resolved"
    alert.resolved_at = now
    alert.resolved_reason = reason
    session.add(Notification(alert_id=alert.id, channel="in_app", event="resolved", status="delivered"))
    add_event(session, "alert.resolved", f"Resolved: {alert.title}", asset_id=alert.asset_id,
              source_id=alert.source_id, alert_id=alert.id, data={"reason": reason}, is_demo=alert.is_demo)  # fmt: skip
    ALERT_TRANSITIONS.labels(kind=alert.kind, to="resolved").inc()
    await _publish({"type": "alert", "event": "resolved", "id": alert.id, "kind": alert.kind, "title": alert.title, "ts": now})


async def raise_event_alert(session: AsyncSession, kind: str, cond: Condition, *, now: datetime,
                            finding_id: int | None = None) -> Alert | None:  # fmt: skip
    """Event-driven alerts (new device, posture change): no threshold, still deduplicated."""
    rule = (await session.execute(select(AlertRule).where(AlertRule.kind == kind))).scalar_one_or_none()
    if rule is None or not rule.enabled:
        return None
    return await fire_alert(session, rule=rule, cond=cond, now=now, finding_id=finding_id)


# ---------------------------------------------------------------- condition builders


async def _latency_conditions(session: AsyncSession, rule: AlertRule, now: datetime) -> list[Condition]:
    p = rule.params
    out = []
    targets = (
        await session.execute(select(ProbeTarget).where(ProbeTarget.scope == "gateway", ProbeTarget.enabled.is_(True)))
    ).scalars()
    for t in targets:
        pts = await load_points(session, target_id=t.id, since=now - timedelta(seconds=p["window_s"]))
        stats = compute_window_stats(pts, now=now, window_s=p["window_s"])
        enough = stats.sample_size >= p.get("min_samples", 1)
        details = {"window_s": p["window_s"], "sample_size": stats.sample_size, "target": t.name}
        if rule.kind == "gateway_latency_high":
            value = stats.latency_avg_ms
            known = enough and value is not None
            out.append(Condition(
                dedup_key=f"gateway_latency_high:{t.id}",
                breached=(value > p["threshold_ms"]) if known else None,  # type: ignore[operator]
                cleared=(value < p["clear_threshold_ms"]) if known else None,  # type: ignore[operator]
                title=f"Gateway latency high ({t.name})",
                summary=(f"Average round-trip time {value:.1f} ms over the last {p['window_s']} s "
                         f"({stats.sample_size} probes) exceeds {p['threshold_ms']} ms.") if known else "insufficient data",
                source_ts=stats.last_ts, details={**details, "latency_avg_ms": value, "threshold_ms": p["threshold_ms"]},
                is_demo=t.is_demo,
            ))  # fmt: skip
        else:
            value = stats.loss_pct
            known = enough and value is not None
            out.append(Condition(
                dedup_key=f"packet_loss_high:{t.id}",
                breached=(value > p["threshold_pct"]) if known else None,  # type: ignore[operator]
                cleared=(value < p["clear_threshold_pct"]) if known else None,  # type: ignore[operator]
                title=f"Gateway packet loss ({t.name})",
                summary=(f"{value:.1f}% of {stats.sample_size} gateway probes in the last {p['window_s']} s went "
                         f"unanswered (threshold {p['threshold_pct']}%). An ICMP-filtering gateway looks the same.")
                if known else "insufficient data",
                source_ts=stats.last_ts, details={**details, "loss_pct": value, "threshold_pct": p["threshold_pct"]},
                is_demo=t.is_demo,
            ))  # fmt: skip
    return out


async def _heartbeat_conditions(session: AsyncSession, rule: AlertRule, now: datetime) -> list[Condition]:
    out = []
    sources = (
        await session.execute(
            select(DataSource).where(DataSource.enabled.is_(True), DataSource.last_seen_at.is_not(None),
                                     DataSource.kind.in_(("linux_agent", "windows_agent", "demo")))  # fmt: skip
        )
    ).scalars()
    for s in sources:
        assert s.last_seen_at is not None
        age = (now - s.last_seen_at).total_seconds()
        breached = age > rule.params["threshold_s"]
        out.append(Condition(
            dedup_key=f"collector_heartbeat_missing:{s.id}", breached=breached, cleared=not breached,
            title=f"Collector '{s.name}' stopped reporting",
            summary=f"No data from '{s.name}' for {int(age)} s (threshold {rule.params['threshold_s']} s).",
            source_ts=s.last_seen_at, source_id=s.id, asset_id=s.asset_id,
            details={"age_s": int(age), "last_seen_at": s.last_seen_at.isoformat()}, is_demo=s.is_demo,
        ))  # fmt: skip
    return out


async def _bandwidth_conditions(session: AsyncSession, rule: AlertRule, now: datetime) -> list[Condition]:
    p = rule.params
    since = now - timedelta(seconds=p["window_s"])
    rows = (
        await session.execute(
            select(MetricSeries, func.avg(MetricSample.value), func.count(), func.max(MetricSample.ts))
            .join(MetricSample, MetricSample.series_id == MetricSeries.id)
            .where(MetricSeries.metric.in_(("net.rx_bps", "net.tx_bps", "wan.rx_bps", "wan.tx_bps")),
                   MetricSample.ts > since)  # fmt: skip
            .group_by(MetricSeries.id)
        )
    ).all()
    out = []
    for series, avg, n, last_ts in rows:
        known = n >= p.get("min_samples", 1)
        out.append(Condition(
            dedup_key=f"interface_bandwidth_high:{series.id}",
            breached=(avg > p["threshold_bps"]) if known else None,
            cleared=(avg < p["clear_threshold_bps"]) if known else None,
            title=f"High throughput on {series.label or series.metric}",
            summary=f"{series.metric} on '{series.label}' averaged {avg / 1e6:.1f} Mbit/s over {p['window_s']} s "
                    f"(threshold {p['threshold_bps'] / 1e6:.0f} Mbit/s, measured at {series.observation_point}).",
            source_ts=last_ts, source_id=series.source_id, asset_id=series.asset_id,
            details={"avg_bps": avg, "sample_size": n, "metric": series.metric, "label": series.label},
        ))  # fmt: skip
    return out


async def _stale_source_conditions(session: AsyncSession, rule: AlertRule, now: datetime) -> list[Condition]:
    out = []
    sources = (await session.execute(select(DataSource).where(DataSource.enabled.is_(True)))).scalars()
    for s in sources:
        hb = (
            await session.execute(
                select(CollectorHeartbeat).where(CollectorHeartbeat.source_id == s.id)
                .order_by(CollectorHeartbeat.ts.desc()).limit(1)  # fmt: skip
            )
        ).scalar_one_or_none()
        if hb is None or (now - hb.ts).total_seconds() > 60:
            continue  # silence is the heartbeat rule's job
        failing = sorted(hb.errors)
        out.append(Condition(
            dedup_key=f"stale_source:{s.id}", breached=bool(failing), cleared=not failing,
            title=f"Source '{s.name}' is reporting collector errors",
            summary="Collectors failing: " + ", ".join(f"{k} ({hb.errors[k]})" for k in failing) if failing else "ok",
            source_ts=hb.ts, source_id=s.id, asset_id=s.asset_id, details={"errors": hb.errors}, is_demo=s.is_demo,
        ))  # fmt: skip
    return out


_BUILDERS = {
    "gateway_latency_high": _latency_conditions,
    "packet_loss_high": _latency_conditions,
    "collector_heartbeat_missing": _heartbeat_conditions,
    "interface_bandwidth_high": _bandwidth_conditions,
    "stale_source": _stale_source_conditions,
}


async def evaluate_rules(session: AsyncSession, *, now: datetime) -> dict[str, int]:
    """One evaluation pass over every enabled threshold rule + housekeeping for event alerts."""
    fired = resolved = 0
    rules = list((await session.execute(select(AlertRule))).scalars())
    for rule in rules:
        builder = _BUILDERS.get(rule.kind)
        if builder is None:
            hours = rule.params.get("auto_resolve_hours")
            if hours:
                old = (
                    await session.execute(
                        select(Alert).where(Alert.kind == rule.kind, Alert.state != "resolved",
                                            Alert.started_at < now - timedelta(hours=hours))  # fmt: skip
                    )
                ).scalars()
                for alert in old:
                    await resolve_alert(session, alert, now=now, reason=f"auto-closed after {hours} h")
                    resolved += 1
            continue
        if not rule.enabled:
            continue
        state = dict(rule.state)
        seen: set[str] = set()
        for cond in await builder(session, rule, now):
            seen.add(cond.dedup_key)
            active = await _active_alert(session, cond.dedup_key)
            ks, action = step(
                KeyState.from_json(state.get(cond.dedup_key)), breached=cond.breached, cleared=cond.cleared,
                active=active is not None, now=now, for_s=rule.params.get("for_s", 0),
                clear_for_s=rule.params.get("clear_for_s", 0), cooldown_s=rule.params.get("cooldown_s", 0),
            )  # fmt: skip
            state[cond.dedup_key] = ks.to_json()
            if active is not None:
                active.last_evaluated_at = now
            if action == "fire":
                if await fire_alert(session, rule=rule, cond=cond, now=now) is not None:
                    fired += 1
            elif action == "resolve" and active is not None:
                await resolve_alert(session, active, now=now, reason="condition cleared")
                resolved += 1
        rule.state = {k: v for k, v in state.items() if k in seen}
    return {"fired": fired, "resolved": resolved}
