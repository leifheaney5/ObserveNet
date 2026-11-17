"""Ingestion pipeline.

API side  (``accept``):  authenticate -> validate -> publish live frame (Redis pub/sub)
                         -> enqueue a durable worker task. No heavy DB work in the request.
Worker    (``process_batch``): one transaction that applies the batch exactly once
                         (ledger row + all writes commit together, so a retry is a no-op).
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from observenet.core.config import get_config
from observenet.core.redis import CURRENT_HASH, LIVE_CHANNEL
from observenet.core.telemetry import COLLECTOR_FAILURES, INGEST_LAG, INGEST_SAMPLES
from observenet.core.time import utcnow
from observenet.models import (
    ApiToken,
    Asset,
    Capability,
    CollectorHeartbeat,
    DataSource,
    Finding,
    IngestBatch,
    Interface,
    NetworkSegment,
    ObservedService,
    PostureSnapshot,
    ProbeResult,
    ProbeTarget,
)
from observenet.schemas.ingest import (
    METRIC_UNITS,
    IngestAccepted,
    IngestPayload,
    Inventory,
    Posture,
    Sample,
    observation_point_for,
)
from observenet.security import engine
from observenet.services import alerts as alert_service
from observenet.services import identity
from observenet.services.events import add_event
from observenet.services.netscope import is_private_lan
from observenet.services.settings import RuntimeSettings, get_runtime_settings
from observenet.services.timeseries import ensure_series, insert_samples

MAX_FUTURE_SKEW = timedelta(seconds=120)
MAX_SAMPLE_AGE = timedelta(hours=24)
VIRTUAL_IFACE_PREFIXES = ("lo", "docker", "br-", "veth", "virbr", "tun", "tap", "wg", "tailscale", "zt", "ifb", "dummy")
COVERAGE = {
    "host_nic": "Traffic through this host's own interface only — not network-wide and not per-device.",
    "gateway_wan": "All traffic crossing the gateway's WAN interface, as counted by the gateway.",
    "gateway_per_device": "Per-client accounting reported by the gateway.",
    "host": "Resource usage of the host running the collector.",
    "active_probe": "Round trip measured from this collector to the target; other vantage points may differ.",
    "capacity_test": "Opt-in capacity test result; not continuous utilization.",
}
PLATFORM_KIND = {"linux": "linux_agent", "windows": "windows_agent", "router": "router_snmp", "demo": "demo"}

Enqueue = Callable[[int, dict[str, Any], str], Awaitable[None]]


class IngestRejected(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def derive_probe_samples(payload: IngestPayload) -> list[Sample]:
    """Probe results also become metric series so charts/rollups share one code path.

    ``probe.success`` averaged over a bucket is the delivery ratio, so loss % for any
    bucket is ``(1 - avg) * 100`` with ``count`` as the sample size.
    """
    out: list[Sample] = []
    for r in payload.probe_results:
        label = f"target:{r.target_id}"
        out.append(Sample(metric="probe.success", label=label, ts=r.ts, value=1.0 if r.success else 0.0, unit="bool"))
        if r.success and r.latency_ms is not None:
            out.append(Sample(metric="probe.latency_ms", label=label, ts=r.ts, value=r.latency_ms, unit="ms"))
        if r.jitter_ms is not None:
            out.append(Sample(metric="probe.jitter_ms", label=label, ts=r.ts, value=r.jitter_ms, unit="ms",
                              quality="derived"))  # fmt: skip
    return out


async def _bind_source(session: AsyncSession, token: ApiToken, payload: IngestPayload) -> DataSource:
    agent = payload.agent
    source = (
        await session.execute(select(DataSource).where(DataSource.agent_uid == agent.agent_uid))
    ).scalar_one_or_none()
    if source is None:
        if token.source_id is not None:
            raise IngestRejected(403, "this token is bound to a different agent")
        source = DataSource(
            kind=PLATFORM_KIND[agent.platform], name=agent.hostname, agent_uid=agent.agent_uid,
            platform=agent.emulates if agent.platform == "demo" else agent.platform, version=agent.agent_version,
            observation_point="gateway" if (agent.emulates or agent.platform) == "router" else "host",
            is_demo=agent.platform == "demo",
        )  # fmt: skip
        session.add(source)
        await session.flush()
    if token.source_id is None:
        token.source_id = source.id  # first use binds the token to exactly one agent
    elif token.source_id != source.id:
        raise IngestRejected(403, "this token is bound to a different agent")
    if not source.enabled:
        raise IngestRejected(403, "this data source is disabled")
    if source.version != agent.agent_version:
        source.version = agent.agent_version
    return source


async def accept(
    session: AsyncSession, redis: Redis, *, token: ApiToken, payload: IngestPayload, enqueue: Enqueue,
    now: datetime | None = None,
) -> IngestAccepted:  # fmt: skip
    now = now or utcnow()
    cfg = get_config()
    if cfg.demo_mode and payload.agent.platform != "demo":
        raise IngestRejected(409, "server is running in DEMO_MODE; real collectors are rejected so demo and real data never mix")
    if not cfg.demo_mode and payload.agent.platform == "demo":
        raise IngestRejected(409, "synthetic demo data is only accepted in DEMO_MODE")
    source = await _bind_source(session, token, payload)

    all_ts = [s.ts for s in payload.samples] + [p.ts for p in payload.probe_results]
    if all_ts and max(all_ts) > now + MAX_FUTURE_SKEW:
        skew = int((max(all_ts) - now).total_seconds())
        source.last_error = f"clock skew: agent timestamps are {skew}s ahead of the server; batch rejected"
        await session.commit()
        raise IngestRejected(422, source.last_error)
    cutoff = now - MAX_SAMPLE_AGE
    payload.samples = [s for s in payload.samples if s.ts >= cutoff]
    payload.probe_results = [p for p in payload.probe_results if p.ts >= cutoff]
    for s in payload.samples:
        if s.unit != METRIC_UNITS[s.metric]:
            raise IngestRejected(422, f"metric {s.metric} must use unit {METRIC_UNITS[s.metric]!r}, got {s.unit!r}")
        if cfg.demo_mode is False and s.quality == "synthetic":
            raise IngestRejected(422, "synthetic samples are not accepted outside DEMO_MODE")

    samples = payload.samples + derive_probe_samples(payload)
    frame = {
        "type": "metrics",
        "source_id": source.id,
        "ts": now.isoformat(),
        "samples": [
            {"m": s.metric, "l": s.label, "v": s.value, "u": s.unit, "t": s.ts.isoformat(), "q": s.quality}
            for s in samples
        ],
    }
    await session.commit()  # release the DB connection before talking to Redis
    try:
        if samples:
            latest: dict[str, str] = {}
            for s in sorted(samples, key=lambda x: x.ts):
                latest[f"{source.id}|{s.metric}|{s.label}"] = json.dumps(
                    {"v": s.value, "t": s.ts.isoformat(), "u": s.unit, "q": s.quality}
                )
            await redis.hset(CURRENT_HASH, mapping=latest)  # type: ignore[misc]
            await redis.publish(LIVE_CHANNEL, json.dumps(frame))
        await enqueue(source.id, payload.model_dump(mode="json"), now.isoformat())
    except IngestRejected:
        raise
    except Exception as exc:  # noqa: BLE001 - broker unavailable: tell the agent to retry the same batch
        raise IngestRejected(503, f"queue unavailable, retry this batch: {type(exc).__name__}") from exc
    return IngestAccepted(batch_id=payload.batch_id, samples=len(samples), config_version="")


# ---------------------------------------------------------------- worker side


async def process_batch(
    session: AsyncSession, *, source_id: int, payload: IngestPayload, received_at: datetime
) -> dict[str, Any]:
    """Apply one batch exactly once. Must run inside a single transaction.

    Batches of one source are serialized with a transaction-scoped advisory lock: workers
    run concurrently, and two first-ever batches of a new agent would otherwise race to
    create its asset/identity rows. Different sources still process in parallel.
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(:ns, :source)"), {"ns": 7041, "source": source_id})
    ledger = await session.execute(
        pg_insert(IngestBatch)
        .values(source_id=source_id, batch_id=payload.batch_id, received_at=received_at, sample_count=len(payload.samples))
        .on_conflict_do_nothing()
        .returning(IngestBatch.batch_id)
    )
    if ledger.first() is None:
        return {"duplicate": True, "samples": 0}
    source = await session.get(DataSource, source_id)
    if source is None:
        return {"skipped": "source no longer exists", "samples": 0}

    now = utcnow()
    asset = await identity.resolve_agent_asset(
        session, source=source, agent_uid=payload.agent.agent_uid, hostname=payload.agent.hostname,
        platform=source.platform or payload.agent.platform, now=max(received_at, source.last_seen_at or received_at),
    )  # fmt: skip
    settings = await get_runtime_settings(session)

    valid_targets = set((await session.execute(select(ProbeTarget.id))).scalars())
    payload.probe_results = [p for p in payload.probe_results if p.target_id in valid_targets]

    rows = []
    for s in payload.samples + derive_probe_samples(payload):
        point = observation_point_for(s.metric)
        target_id = int(s.label.split(":", 1)[1]) if s.label.startswith("target:") else None
        series_id = await ensure_series(
            session, source_id=source.id, metric=s.metric, label=s.label, unit=s.unit, observation_point=point,
            quality=s.quality, interval_s=s.interval_s, asset_id=asset.id, coverage=COVERAGE[point],
            probe_target_id=target_id,
        )  # fmt: skip
        rows.append({"series_id": series_id, "ts": s.ts, "value": s.value})
    inserted = await insert_samples(session, rows)

    if payload.probe_results:
        await session.execute(
            pg_insert(ProbeResult)
            .values([
                {"target_id": p.target_id, "source_id": source.id, "ts": p.ts, "success": p.success,
                 "latency_ms": p.latency_ms, "method": p.method, "resolved_host": p.resolved_host, "error": p.error}
                for p in payload.probe_results
            ])  # fmt: skip
            .on_conflict_do_nothing(index_elements=["target_id", "source_id", "ts"])
        )

    if payload.heartbeat is not None:
        hb = payload.heartbeat
        session.add(CollectorHeartbeat(source_id=source.id, ts=hb.ts, cpu_pct=hb.cpu_pct, mem_pct=hb.mem_pct,
                                       uptime_s=hb.uptime_s, agent_version=payload.agent.agent_version, errors=hb.errors))  # fmt: skip
        for name in hb.errors:
            COLLECTOR_FAILURES.labels(source=source.name, collector=name).inc()
        source.last_error = "; ".join(f"{k}: {v}" for k, v in sorted(hb.errors.items())) or None
        for cap in hb.capabilities:
            stmt = pg_insert(Capability).values(source_id=source.id, name=cap.name, status=cap.status, detail=cap.detail)
            await session.execute(
                stmt.on_conflict_do_update(index_elements=["source_id", "name"],
                                           set_={"status": cap.status, "detail": cap.detail, "updated_at": now})  # fmt: skip
            )
    if source.last_seen_at is None or received_at > source.last_seen_at:
        source.last_seen_at = received_at  # GREATEST: an out-of-order batch never moves it back

    if payload.inventory is not None:
        await apply_inventory(session, source=source, asset=asset, inv=payload.inventory, settings=settings)
    if payload.posture is not None:
        await apply_posture(session, source=source, asset=asset, posture=payload.posture, settings=settings)

    INGEST_SAMPLES.inc(inserted)
    INGEST_LAG.observe(max(0.0, (utcnow() - received_at).total_seconds()))
    return {"duplicate": False, "samples": inserted}


def _is_virtual(name: str) -> bool:
    return name.startswith(VIRTUAL_IFACE_PREFIXES)


async def apply_inventory(
    session: AsyncSession, *, source: DataSource, asset: Asset, inv: Inventory, settings: RuntimeSettings
) -> None:
    now = inv.collected_at
    gw_iface = inv.default_gateway.interface if inv.default_gateway else None
    for iface in inv.interfaces:
        mac = identity.normalize_mac(iface.mac)
        values = {
            "source_id": source.id, "mac": mac, "is_up": iface.is_up, "link_speed_mbps": iface.link_speed_mbps or None,
            "mtu": iface.mtu, "addresses": [a.model_dump() for a in iface.addresses],
            "is_default_route": iface.name == gw_iface, "last_seen": now,
        }  # fmt: skip
        await session.execute(
            pg_insert(Interface).values(asset_id=asset.id, name=iface.name, first_seen=now, **values)
            .on_conflict_do_update(index_elements=["asset_id", "name"], set_=values)  # fmt: skip
        )
        for a in iface.addresses:
            try:
                addr = ipaddress.ip_address(a.address)
            except ValueError:
                continue
            if addr.is_loopback or addr.is_link_local:
                continue
            if a.prefix_len is not None and is_private_lan(addr) and a.prefix_len < addr.max_prefixlen:
                net = ipaddress.ip_network(f"{a.address}/{a.prefix_len}", strict=False)
                seg = pg_insert(NetworkSegment).values(cidr=str(net), name=iface.name, origin="observed",
                                                       first_seen=now, last_seen=now, is_demo=source.is_demo)  # fmt: skip
                await session.execute(seg.on_conflict_do_update(index_elements=["cidr"], set_={"last_seen": now}))
            await identity.upsert_address_observation(session, asset_id=asset.id, source_id=source.id,
                                                      kind="agent_self", ip=str(addr), mac=mac, hostname=None, now=now)  # fmt: skip
        if mac and not _is_virtual(iface.name):
            await identity.claim_agent_mac(session, asset=asset, source=source, mac=mac, now=now)

    if inv.neighbors_status != "ok":
        return
    learning_until = source.config.get("learning_until")
    if learning_until is None:
        rule_minutes = 10
        learning_until = (now + timedelta(minutes=rule_minutes)).isoformat()
        source.config = {**source.config, "learning_until": learning_until}
    learning = now < datetime.fromisoformat(learning_until)
    own_ips = {a.address for i in inv.interfaces for a in i.addresses}
    for n in inv.neighbors:
        if n.ip in own_ips or (n.interface and _is_virtual(n.interface)):
            continue  # containers/VMs on this host's virtual bridges are not LAN devices
        sighting = await identity.reconcile_sighting(session, source=source, ip=n.ip, mac=n.mac, hostname=None,
                                                     kind="neighbor_cache", now=now)  # fmt: skip
        if sighting is None:
            continue
        if inv.default_gateway and n.ip == inv.default_gateway.ip and sighting.asset.asset_type == "unknown":
            sighting.asset.asset_type = "router"
            sighting.asset.confidence_reason = (
                (sighting.asset.confidence_reason or "")
                + f" Typed as 'router' by inference: it is the default gateway of '{source.name}'."
            ).strip()
        if sighting.created and not learning:
            await alert_service.raise_event_alert(
                session, "new_device",
                alert_service.Condition(
                    dedup_key=f"new_device:{sighting.asset.id}", breached=True, cleared=False,
                    title=f"New device observed at {n.ip}",
                    summary=f"A device not seen before appeared at {n.ip}" + (f" (MAC {n.mac})" if n.mac else "")
                    + f", observed in the neighbour cache of '{source.name}'. This is an observation, not a threat verdict.",
                    source_ts=now, asset_id=sighting.asset.id, source_id=source.id,
                    details={"ip": n.ip, "mac": n.mac, "confidence": sighting.asset.confidence}, is_demo=source.is_demo,
                ),
                now=utcnow(),
            )  # fmt: skip


def _listener_keys(posture: Posture | dict[str, Any]) -> set[str] | None:
    data = posture.model_dump(mode="json") if isinstance(posture, Posture) else posture
    section = data.get("listening")
    if not section or section.get("status") != "ok":
        return None
    return {f"{i['proto']}/{i['address']}:{i['port']}" for i in section["items"]}


async def _sync_services(session: AsyncSession, *, asset: Asset, source: DataSource, posture: Posture, now: datetime) -> None:
    async def sync(origin: str, items: list[dict[str, Any]]) -> None:
        seen_ids = []
        for it in items:
            stmt = pg_insert(ObservedService).values(asset_id=asset.id, source_id=source.id, origin=origin,
                                                     first_seen=now, last_seen=now, active=True, **it)  # fmt: skip
            stmt = stmt.on_conflict_do_update(
                constraint="uq_observed_service",
                set_={"last_seen": now, "active": True, "process": it.get("process"), "container": it.get("container")},
            ).returning(ObservedService.id)
            seen_ids.append((await session.execute(stmt)).scalar_one())
        await session.execute(
            update(ObservedService)
            .where(ObservedService.asset_id == asset.id, ObservedService.origin == origin,
                   ObservedService.active.is_(True), ObservedService.id.notin_(seen_ids or [0]))  # fmt: skip
            .values(active=False)
        )

    if posture.listening and posture.listening.status == "ok":
        dedup = {(s.proto, s.address, s.port): s for s in posture.listening.items}
        await sync("host_socket", [{"proto": s.proto, "address": s.address, "port": s.port, "process": s.process,
                                    "container": None} for s in dedup.values()])  # fmt: skip
    if posture.docker and posture.docker.status == "ok":
        dedup_d = {(p.proto, p.host_address, p.host_port): p for p in posture.docker.published_ports}
        await sync("docker_published", [{"proto": p.proto, "address": p.host_address, "port": p.host_port,
                                         "process": None, "container": p.container} for p in dedup_d.values()])  # fmt: skip


async def apply_posture(
    session: AsyncSession, *, source: DataSource, asset: Asset, posture: Posture, settings: RuntimeSettings
) -> None:
    previous = (
        await session.execute(
            select(PostureSnapshot).where(PostureSnapshot.source_id == source.id)
            .order_by(PostureSnapshot.collected_at.desc()).limit(1)  # fmt: skip
        )
    ).scalar_one_or_none()
    last_known = max(previous.collected_at, previous.last_confirmed_at or previous.collected_at) if previous else None
    if last_known is not None and posture.collected_at <= last_known:
        return  # out-of-order snapshot: never let older evidence drive state transitions

    data = posture.model_dump(mode="json", exclude={"collected_at"})
    dig = engine.digest(data)
    verify_pending = source.posture_requested_at is not None
    if previous is not None and previous.digest == dig and not verify_pending:
        # Nothing changed: re-confirm the existing evidence instead of storing a duplicate snapshot.
        previous.last_confirmed_at = posture.collected_at
        await session.execute(
            update(Finding)
            .where(Finding.asset_id == asset.id, Finding.source_id == source.id,
                   Finding.status.in_(engine.ACTIVE_STATUSES), Finding.rule_id != "OBS-STALE-001")  # fmt: skip
            .values(last_observed_at=posture.collected_at)
        )
        return

    snapshot = PostureSnapshot(source_id=source.id, asset_id=asset.id, collected_at=posture.collected_at,
                               digest=dig, data=data)  # fmt: skip
    session.add(snapshot)
    await session.flush()
    await _sync_services(session, asset=asset, source=source, posture=posture, now=posture.collected_at)

    if previous is not None:
        before, after = _listener_keys(previous.data), _listener_keys(posture)
        if before is not None and after is not None and before != after:
            added, removed = sorted(after - before), sorted(before - after)
            add_event(
                session, "posture.services_changed",
                f"Listening services changed on {asset.display_name or asset.hostname}: +{len(added)} / -{len(removed)}",
                asset_id=asset.id, source_id=source.id, data={"added": added, "removed": removed}, is_demo=source.is_demo,
            )  # fmt: skip

    trigger = "verify" if verify_pending else "scheduled"
    assessment = await engine.assess(session, source=source, asset=asset, snapshot=snapshot, posture=posture,
                                     settings=settings.posture, trigger=trigger)  # fmt: skip
    if source.posture_requested_at is not None and posture.collected_at > source.posture_requested_at:
        source.posture_requested_at = None

    now = utcnow()
    for finding_id in assessment.summary.get("opened_finding_ids", []):
        finding = await session.get(Finding, finding_id)
        if finding is None:
            continue
        await alert_service.raise_event_alert(
            session, "posture_changed",
            alert_service.Condition(
                dedup_key=f"posture_changed:{finding.id}", breached=True, cleared=False,
                title=f"Posture finding: {finding.title}",
                summary=f"{finding.observation} ({finding.classification.replace('_', ' ').lower()}; not an exploitability claim)",
                source_ts=posture.collected_at, asset_id=asset.id, source_id=source.id,
                details={"rule_id": finding.rule_id, "severity": finding.severity}, is_demo=source.is_demo,
            ),
            now=now, finding_id=finding.id,
        )  # fmt: skip
    for finding_id in assessment.summary.get("resolved_finding_ids", []):
        active = await alert_service._active_alert(session, f"posture_changed:{finding_id}")
        if active is not None:
            await alert_service.resolve_alert(session, active, now=now, reason="finding verified resolved")
