"""Read-side helpers: freshness, presence, capability matrix, overview, topology."""

from __future__ import annotations

import ipaddress
import json
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from observenet.core.config import get_config
from observenet.core.redis import CURRENT_HASH, get_redis
from observenet.models import (
    AddressObservation,
    Alert,
    Asset,
    Capability,
    DataSource,
    Event,
    Finding,
    Interface,
    MetricSeries,
    NetworkSegment,
    ObservedService,
    ProbeTarget,
)
from observenet.services.connectivity import compute_window_stats, internet_status, load_points
from observenet.services.settings import FreshnessSettings, RuntimeSettings

PER_DEVICE_GUIDANCE = (
    "Per-device traffic requires a supported gateway integration. A host on the LAN only sees its own "
    "traffic; attributing bandwidth to each device needs per-client counters or flow export from the "
    "router, a managed switch/AP, or an agent on each device. No such source is connected."
)
WAN_GUIDANCE = (
    "WAN throughput needs counters from the gateway's WAN interface (for example read-only SNMPv3 IF-MIB). "
    "No gateway source is connected, so WAN utilization is not shown. The host NIC series is a different metric."
)


def freshness_state(
    last: datetime | None, now: datetime, fresh: FreshnessSettings, expected_interval_s: float = 1.0
) -> str:
    """live | stale | disconnected | never — scaled so slow sources are not flagged stale."""
    if last is None:
        return "never"
    age = (now - last).total_seconds()
    if age <= max(fresh.stale_after_s, expected_interval_s * 3):
        return "live"
    if age <= max(fresh.disconnected_after_s, expected_interval_s * 10):
        return "stale"
    return "disconnected"


def presence(
    asset: Asset, now: datetime, fresh: FreshnessSettings, agent_last_seen: datetime | None = None
) -> dict[str, str]:
    """Source-dependent presence. Deliberately never says 'offline': not being seen by a
    passive source does not prove a device is disconnected."""
    if asset.managed:
        state = freshness_state(agent_last_seen, now, fresh)
        if state == "live":
            return {
                "state": "reporting",
                "label": "Agent reporting",
                "basis": "Its agent sent data in the last few seconds.",
            }
        if state == "stale":
            return {
                "state": "reporting_stale",
                "label": "Agent delayed",
                "basis": "Its agent's last report is late.",
            }
        return {"state": "agent_silent", "label": "Agent silent",
                "basis": "Its agent stopped reporting. The host may be off, asleep, disconnected, or the agent stopped."}  # fmt: skip
    if asset.last_seen is None:
        return {"state": "unknown", "label": "Unknown", "basis": "Never observed by a live source."}
    age = (now - asset.last_seen).total_seconds()
    if age <= fresh.device_seen_recently_s:
        return {"state": "seen_recently", "label": "Seen recently",
                "basis": "Present in a collector's neighbour cache recently. Cache entries can outlive a departed device by a few minutes."}  # fmt: skip
    return {"state": "not_seen_recently", "label": "Not seen recently",
            "basis": "No passive sighting lately. This is not proof it is offline: quiet devices drop out of neighbour caches."}  # fmt: skip


class Kpi(BaseModel):
    key: str
    label: str
    value: float | None
    unit: str
    state: str  # ok | stale | unknown | unavailable | unsupported
    ts: datetime | None = None
    source: str | None = None
    observation_point: str | None = None
    reason: str | None = None
    detail: dict[str, Any] = {}


class NetCapability(BaseModel):
    key: str
    title: str
    status: str  # supported | partial | unsupported | unavailable | disabled
    detail: str
    sources: list[str] = []
    guidance: str | None = None


async def current_values() -> dict[str, dict[str, Any]]:
    try:
        raw = await get_redis().hgetall(CURRENT_HASH)
    except Exception:  # noqa: BLE001 - Redis down => no live values, surfaced as 'unavailable'
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = json.loads(v)
        except ValueError:
            continue
    return out


async def network_capabilities(session: AsyncSession, now: datetime) -> list[NetCapability]:
    sources = list((await session.execute(select(DataSource).where(DataSource.enabled.is_(True)))).scalars())
    by_id = {s.id: s for s in sources}
    recent = now - timedelta(minutes=10)
    series = (
        await session.execute(
            select(MetricSeries.metric, MetricSeries.source_id)
            .where(MetricSeries.last_ts > recent)
            .distinct()
        )
    ).all()

    def having(prefix: str) -> list[str]:
        return sorted(
            {by_id[sid].name for metric, sid in series if metric.startswith(prefix) and sid in by_id}
        )

    caps = {
        (c.source_id, c.name): c
        for c in (await session.execute(select(Capability))).scalars()
        if c.source_id in by_id
    }

    def agent_cap(name: str) -> tuple[str, str, list[str]] | None:
        rows = [(by_id[sid].name, c) for (sid, n), c in caps.items() if n == name]
        if not rows:
            return None
        order = {"supported": 0, "unavailable": 1, "disabled": 2, "unsupported": 3}
        rows.sort(key=lambda r: order[r[1].status])
        best = rows[0][1]
        return best.status, best.detail or "", [n for n, c in rows if c.status == best.status]

    out: list[NetCapability] = []
    nic = having("net.")
    out.append(NetCapability(
        key="host_nic_throughput", title="Host NIC throughput (1 s)", status="supported" if nic else "unavailable",
        detail="Interface byte counters of hosts running an agent. Covers only those hosts' own traffic."
        if nic else "No agent has reported interface counters in the last 10 minutes.", sources=nic,
    ))  # fmt: skip
    wan = having("wan.")
    out.append(NetCapability(
        key="wan_throughput", title="WAN throughput", status="supported" if wan else "unsupported",
        detail="Gateway WAN interface counters." if wan else "No gateway source connected.", sources=wan,
        guidance=None if wan else WAN_GUIDANCE,
    ))  # fmt: skip
    dev = having("device.")
    out.append(NetCapability(
        key="per_device_traffic", title="Per-device traffic", status="supported" if dev else "unsupported",
        detail="Per-client counters from the gateway." if dev else "No source can attribute traffic to individual devices.",
        sources=dev, guidance=None if dev else PER_DEVICE_GUIDANCE,
    ))  # fmt: skip
    neigh = agent_cap("neighbor_cache")
    imports = sorted(s.name for s in sources if s.kind == "client_import")
    inv_sources = (neigh[2] if neigh and neigh[0] == "supported" else []) + imports
    out.append(NetCapability(
        key="device_inventory", title="Device inventory", status="partial" if inv_sources else "unavailable",
        detail=("Passive sightings only (neighbour cache of agent hosts" + (", imported client list" if imports else "")
                + "). Devices that never talk to an agent host are not seen; this is not a complete inventory.")
        if inv_sources else "No inventory source yet. Start an agent or import your router's client list.",
        sources=inv_sources,
        guidance="For authoritative inventory, import the router's DHCP/client list (Devices → Import client list) "
                 "or connect a gateway provider that reports leases.",
    ))  # fmt: skip
    probe = having("probe.")
    out.append(NetCapability(
        key="active_probes", title="Gateway latency & external reachability",
        status="supported" if probe else "unavailable",
        detail="Probes run from agent hosts; results describe the path from that host."
        if probe else "No probe results in the last 10 minutes.", sources=probe,
    ))  # fmt: skip
    for key, title, cap_name in (
        ("linux_posture", "Linux host posture (listening sockets)", "listening_sockets"),
        ("windows_posture", "Windows host posture (firewall, SMB, listeners)", "windows_firewall"),
        ("docker_inventory", "Docker published ports", "docker_published_ports"),
    ):
        got = agent_cap(cap_name)
        out.append(NetCapability(
            key=key, title=title, status=got[0] if got else "unavailable",
            detail=got[1] if got and got[1] else ("Reported by agent." if got else "No agent reports this yet."),
            sources=got[2] if got else [],
            guidance="Opt-in: enable the read-only Docker socket proxy profile (see docs/TELEMETRY_SOURCES.md)."
            if key == "docker_inventory" and (not got or got[0] != "supported") else None,
        ))  # fmt: skip
    for key, title, why in (
        ("wifi_clients_signal", "Wi-Fi clients & signal", "Needs an access-point/controller provider; none is implemented."),
        ("router_settings", "Router settings (UPnP, port forwards)", "Needs a read-only router provider that exposes them; none is implemented."),
        ("flow_data", "Flow records (NetFlow/IPFIX/sFlow)", "No flow collector is implemented."),
    ):  # fmt: skip
        out.append(NetCapability(key=key, title=title, status="unsupported", detail=why))
    speed = having("speedtest.")
    out.append(NetCapability(
        key="capacity_test", title="Internet capacity test (opt-in)", status="supported" if speed else "disabled",
        detail="Separate from utilization: an occasional, opt-in download test." if speed
        else "Off by default. Enable in Settings → Network scope if you want periodic capacity tests.", sources=speed,
    ))  # fmt: skip
    return out


async def primary_selection(session: AsyncSession) -> dict[str, Any]:
    """Which live series the Overview should chart."""
    source = (
        await session.execute(
            select(DataSource)
            .where(
                DataSource.kind.in_(("linux_agent", "demo", "windows_agent")), DataSource.enabled.is_(True)
            )
            .order_by(DataSource.kind != "linux_agent", DataSource.id)
            .limit(1)  # fmt: skip
        )
    ).scalar_one_or_none()
    out: dict[str, Any] = {"source_id": None, "source_name": None, "interface": None, "gateway_target_id": None,
                           "wan_source_id": None, "wan_label": None}  # fmt: skip
    if source is not None:
        out["source_id"], out["source_name"] = source.id, source.name
        if source.asset_id:
            ifaces = list(
                (
                    await session.execute(select(Interface).where(Interface.asset_id == source.asset_id))
                ).scalars()
            )
            chosen = next((i for i in ifaces if i.is_default_route), None) or next(
                (i for i in ifaces if i.is_up and not i.name.startswith(("lo", "docker", "br-", "veth"))),
                None,
            )
            out["interface"] = chosen.name if chosen else None
        if out["interface"] is None:
            out["interface"] = (
                await session.execute(
                    select(MetricSeries.label)
                    .where(
                        MetricSeries.source_id == source.id,
                        MetricSeries.metric == "net.rx_bps",
                        MetricSeries.label != "lo",
                    )
                    .order_by(MetricSeries.id)
                    .limit(1)  # fmt: skip
                )
            ).scalar_one_or_none()
    gw = (
        await session.execute(
            select(ProbeTarget.id)
            .where(ProbeTarget.scope == "gateway", ProbeTarget.enabled.is_(True))
            .order_by(ProbeTarget.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    out["gateway_target_id"] = gw
    wan = (
        await session.execute(
            select(MetricSeries).where(MetricSeries.metric == "wan.rx_bps").order_by(MetricSeries.id).limit(1)
        )
    ).scalar_one_or_none()
    if wan is not None:
        out["wan_source_id"], out["wan_label"] = wan.source_id, wan.label
    return out


def _live_kpi(key: str, label: str, cur: dict[str, Any] | None, *, now: datetime, fresh: FreshnessSettings,
              source: str | None, point: str, unit: str, missing_reason: str, missing_state: str = "unavailable") -> Kpi:  # fmt: skip
    if cur is None:
        return Kpi(key=key, label=label, value=None, unit=unit, state=missing_state, reason=missing_reason,
                   source=source, observation_point=point)  # fmt: skip
    ts = datetime.fromisoformat(cur["t"])
    age = (now - ts).total_seconds()
    if age > fresh.disconnected_after_s:
        return Kpi(key=key, label=label, value=None, unit=unit, state="unavailable", ts=ts, source=source,
                   observation_point=point, reason=f"Last sample is {int(age)} s old; the source is not reporting.")  # fmt: skip
    state = "ok" if age <= fresh.stale_after_s else "stale"
    return Kpi(key=key, label=label, value=cur["v"], unit=unit, state=state, ts=ts, source=source, observation_point=point,
               reason=None if state == "ok" else f"Last sample is {int(age)} s old.",
               detail={"synthetic": cur.get("q") == "synthetic"})  # fmt: skip


async def build_overview(
    session: AsyncSession, *, now: datetime, settings: RuntimeSettings
) -> dict[str, Any]:
    fresh = settings.freshness
    cur = await current_values()
    primary = await primary_selection(session)
    sid, iface, src_name = primary["source_id"], primary["interface"], primary["source_name"]
    kpis: list[Kpi] = []

    # Devices
    assets = list((await session.execute(select(Asset).where(Asset.merged_into_id.is_(None)))).scalars())
    agent_seen = {
        s.asset_id: s.last_seen_at
        for s in (await session.execute(select(DataSource))).scalars()
        if s.asset_id
    }
    counts: dict[str, int] = {}
    for a in assets:
        st = presence(a, now, fresh, agent_seen.get(a.id))["state"]
        counts[st] = counts.get(st, 0) + 1
    newest = max((a.last_seen for a in assets if a.last_seen), default=None)
    kpis.append(Kpi(
        key="devices", label="Known devices", value=float(len(assets)) if assets else None, unit="devices",
        state="ok" if assets else "unknown", ts=newest, source="Passive sightings + agents" if assets else None,
        reason=None if assets else "No device has been observed yet.",
        detail={"present": counts.get("seen_recently", 0) + counts.get("reporting", 0) + counts.get("reporting_stale", 0),
                "not_seen_recently": counts.get("not_seen_recently", 0) + counts.get("agent_silent", 0),
                "unknown": counts.get("unknown", 0), "coverage": "partial — passive sightings are not a complete inventory"},
    ))  # fmt: skip

    # WAN (only from a gateway source) — never substituted with host NIC numbers.
    for key, label, metric in (
        ("wan_download", "WAN download", "wan.rx_bps"),
        ("wan_upload", "WAN upload", "wan.tx_bps"),
    ):
        c = (
            cur.get(f"{primary['wan_source_id']}|{metric}|{primary['wan_label']}")
            if primary["wan_source_id"]
            else None
        )
        kpis.append(_live_kpi(key, label, c, now=now, fresh=fresh, source="gateway" if c else None, point="gateway_wan",
                              unit="bit/s", missing_reason=WAN_GUIDANCE, missing_state="unsupported"))  # fmt: skip
    for key, label, metric in (
        ("host_rx", "Host NIC receive", "net.rx_bps"),
        ("host_tx", "Host NIC transmit", "net.tx_bps"),
    ):
        c = cur.get(f"{sid}|{metric}|{iface}") if sid and iface else None
        k = _live_kpi(key, label, c, now=now, fresh=fresh, source=f"{src_name} · {iface}" if c else src_name,
                      point="host_nic", unit="bit/s", missing_reason="No agent is reporting interface counters yet.")  # fmt: skip
        kpis.append(k)

    # Gateway latency / loss over an explicit rolling window
    window_s = 60
    gw_id = primary["gateway_target_id"]
    if gw_id is None:
        for key, label, unit in (
            ("gateway_latency", "Gateway latency", "ms"),
            ("packet_loss", "Packet loss", "percent"),
        ):
            kpis.append(Kpi(key=key, label=label, value=None, unit=unit, state="unavailable",
                            reason="No gateway probe target is enabled (Settings → Probe targets)."))  # fmt: skip
    else:
        pts = await load_points(session, target_id=gw_id, since=now - timedelta(seconds=window_s))
        stats = compute_window_stats(pts, now=now, window_s=window_s)
        detail = {"window_s": window_s, "sample_size": stats.sample_size, "target_id": gw_id,
                  "jitter_ms": stats.jitter_ms, "p95_ms": stats.latency_p95_ms}  # fmt: skip
        if stats.sample_size == 0:
            why = f"No gateway probe results in the last {window_s} s."
            kpis.append(Kpi(key="gateway_latency", label="Gateway latency", value=None, unit="ms", state="unavailable",
                            reason=why, detail=detail))  # fmt: skip
            kpis.append(Kpi(key="packet_loss", label="Packet loss", value=None, unit="percent", state="unavailable",
                            reason=why, detail=detail))  # fmt: skip
        else:
            kpis.append(Kpi(
                key="gateway_latency", label="Gateway latency", value=stats.latency_avg_ms, unit="ms",
                state="ok" if stats.latency_avg_ms is not None else "unknown", ts=stats.last_ts, source=src_name,
                observation_point="active_probe", detail=detail,
                reason=None if stats.latency_avg_ms is not None
                else "The gateway did not answer any probe in the window. It may filter ICMP; this alone is not an outage.",
            ))  # fmt: skip
            kpis.append(Kpi(key="packet_loss", label="Packet loss", value=stats.loss_pct, unit="percent", state="ok",
                            ts=stats.last_ts, source=src_name, observation_point="active_probe", detail=detail))  # fmt: skip

    # Findings
    sev_rows = (
        await session.execute(
            select(Finding.severity, func.count())
            .where(Finding.status.in_(("open", "acknowledged", "snoozed")))
            .group_by(Finding.severity)  # fmt: skip
        )
    ).all()
    by_sev = {s: n for s, n in sev_rows}
    last_finding_ts = (await session.execute(select(func.max(Finding.last_observed_at)))).scalar()
    has_posture = (await session.execute(select(func.count()).select_from(Capability).where(
        Capability.name.in_(("listening_sockets", "windows_firewall")), Capability.status == "supported"))).scalar_one()  # fmt: skip
    kpis.append(Kpi(
        key="open_findings", label="Open findings", value=float(sum(by_sev.values())) if has_posture or by_sev else None,
        unit="findings", state="ok" if has_posture or by_sev else "unknown", ts=last_finding_ts,
        source="Read-only configuration checks", detail={"by_severity": by_sev},
        reason=None if has_posture or by_sev else "No agent has reported posture data yet, so nothing was checked.",
    ))  # fmt: skip

    inet = await internet_status(session, now=now)
    alerts = list(
        (
            await session.execute(
                select(Alert).where(Alert.state != "resolved").order_by(Alert.started_at.desc()).limit(6)
            )
        ).scalars()
    )
    events = list(
        (
            await session.execute(
                select(Event)
                .where(Event.kind.in_(("device.first_seen", "agent.registered", "device.merged")))
                .order_by(Event.ts.desc())
                .limit(8)  # fmt: skip
            )
        ).scalars()
    )
    dev_cap = next(c for c in await network_capabilities(session, now) if c.key == "per_device_traffic")
    return {
        "generated_at": now,
        "demo_mode": get_config().demo_mode,
        "kpis": kpis,
        "primary": primary,
        "internet": {
            "status": inet.status,
            "reason": inet.reason,
            "signals_total": inet.signals_total,
            "signals_ok": inet.signals_ok,
        },  # fmt: skip
        "active_alerts": [
            {
                "id": a.id,
                "kind": a.kind,
                "severity": a.severity,
                "title": a.title,
                "state": a.state,
                "started_at": a.started_at,
            }
            for a in alerts
        ],  # fmt: skip
        "recent_device_events": [
            {"id": e.id, "ts": e.ts, "kind": e.kind, "message": e.message, "asset_id": e.asset_id}
            for e in events
        ],
        "per_device_traffic": {
            "supported": dev_cap.status == "supported",
            "guidance": dev_cap.guidance,
            "sources": dev_cap.sources,
        },  # fmt: skip
        "posture": {
            "open_by_severity": by_sev,
            "last_observed_at": last_finding_ts,
            "checked": bool(has_posture),
        },
    }


async def build_topology(session: AsyncSession) -> dict[str, Any]:
    """Documented relationships only (asset ↔ interface ↔ subnet ↔ service). No attack paths."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    segments = list((await session.execute(select(NetworkSegment))).scalars())
    for s in segments:
        nodes.append({"id": f"segment:{s.id}", "type": "segment", "label": s.cidr, "meta": {"name": s.name}})
    assets = list((await session.execute(select(Asset).where(Asset.merged_into_id.is_(None)))).scalars())
    # Unnamed devices are labelled by their most recently observed address rather than "Device #N".
    last_ip: dict[int, str] = {}
    for obs in (
        await session.execute(select(AddressObservation).order_by(AddressObservation.last_seen))
    ).scalars():
        last_ip[obs.asset_id] = obs.ip
    for a in assets:
        nodes.append({"id": f"asset:{a.id}", "type": "asset",
                      "label": a.display_name or a.hostname or last_ip.get(a.id) or f"Device #{a.id}",
                      "meta": {"asset_type": a.asset_type, "managed": a.managed, "confidence": a.confidence}})  # fmt: skip
    asset_ids = {a.id for a in assets}
    nets = [(s.id, ipaddress.ip_network(s.cidr)) for s in segments]
    for i in (await session.execute(select(Interface))).scalars():
        if i.asset_id not in asset_ids or i.name == "lo":
            continue
        nid = f"interface:{i.id}"
        nodes.append(
            {"id": nid, "type": "interface", "label": i.name, "meta": {"mac": i.mac, "is_up": i.is_up}}
        )
        edges.append(
            {
                "source": f"asset:{i.asset_id}",
                "target": nid,
                "kind": "HAS_INTERFACE",
                "evidence": "agent inventory",
            }
        )
        for a in i.addresses:
            try:
                addr = ipaddress.ip_address(a["address"])
            except (ValueError, KeyError):
                continue
            for seg_id, net in nets:
                if addr.version == net.version and addr in net:
                    edges.append({"source": nid, "target": f"segment:{seg_id}", "kind": "ATTACHED_TO",
                                  "evidence": f"address {addr}"})  # fmt: skip
    managed = {a.id for a in assets if a.managed}
    seen: set[tuple[int, int]] = set()
    for o in (
        await session.execute(select(AddressObservation).where(AddressObservation.segment_id.is_not(None)))
    ).scalars():
        if o.asset_id in managed or o.asset_id not in asset_ids or (o.asset_id, o.segment_id) in seen:
            continue
        seen.add((o.asset_id, o.segment_id))  # type: ignore[arg-type]
        edges.append({"source": f"asset:{o.asset_id}", "target": f"segment:{o.segment_id}", "kind": "SEEN_ON",
                      "evidence": o.kind.replace("_", " ")})  # fmt: skip
    for svc in (
        await session.execute(select(ObservedService).where(ObservedService.active.is_(True)))
    ).scalars():
        if svc.asset_id not in asset_ids:
            continue
        nid = f"service:{svc.id}"
        nodes.append({"id": nid, "type": "service", "label": f"{svc.proto}/{svc.port}",
                      "meta": {"address": svc.address, "process": svc.process or svc.container, "origin": svc.origin}})  # fmt: skip
        edges.append(
            {
                "source": f"asset:{svc.asset_id}",
                "target": nid,
                "kind": "LISTENS_ON",
                "evidence": svc.origin.replace("_", " "),
            }
        )
    return {"nodes": nodes, "edges": edges,
            "note": "Shows observed relationships only. It is not an attack-path graph and implies no reachability."}  # fmt: skip
