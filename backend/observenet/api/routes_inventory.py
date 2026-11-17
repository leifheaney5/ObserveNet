"""Devices, findings, alerts, probes, settings, overview, insights, topology, exports."""

from __future__ import annotations

import csv
import io
import json
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from observenet.api import schemas as S
from observenet.auth.deps import AdminUser, CurrentUser, DbSession
from observenet.core.config import get_config
from observenet.core.time import ensure_utc, utcnow
from observenet.models import (
    AddressObservation,
    Alert,
    AlertRule,
    Assessment,
    Asset,
    AssetIdentity,
    AssetMerge,
    AuditEvent,
    Check,
    CheckResult,
    DataSource,
    Event,
    Finding,
    FindingEvidence,
    Interface,
    MaintenanceWindow,
    MetricSeries,
    ObservedService,
    Outage,
    ProbeResult,
    ProbeTarget,
    RemediationStep,
    VerificationDiff,
    VerificationRun,
)
from observenet.models.inventory import ASSET_TYPES
from observenet.providers.client_import import (
    ClientListImportProvider,
    ImportError_,
    apply_import,
    parse_client_list,
)
from observenet.security import engine
from observenet.services import alerts as alert_service
from observenet.services import identity, views
from observenet.services.connectivity import compute_window_stats, internet_status, load_points
from observenet.services.events import add_audit, add_event
from observenet.services.insights import build_insights
from observenet.services.netscope import ScopeError, validate_probe_target
from observenet.services.settings import RuntimeSettings, get_runtime_settings, save_runtime_settings

router = APIRouter(prefix="/api/v1")
DISCLAIMER = (
    "ObserveNet reads configuration and state that a device's own agent reports. It does not scan, exploit or "
    "test reachability, so a finding is an observed configuration or a potential exposure — never proof of exploitability."
)
COVERAGE_NOTE = (
    "Inventory is built from passive sightings (agent neighbour caches, agents themselves, imported client lists). "
    "It is not guaranteed complete, and 'not seen recently' does not mean offline."
)


# ------------------------------------------------------------------ devices


async def _device_rows(db: AsyncSession, assets: list[Asset]) -> list[S.DeviceOut]:
    if not assets:
        return []
    settings = await get_runtime_settings(db)
    now = utcnow()
    ids = [a.id for a in assets]
    sources = {s.id: s for s in (await db.execute(select(DataSource))).scalars()}
    agent_seen = {s.asset_id: s.last_seen_at for s in sources.values() if s.asset_id}
    obs: dict[int, AddressObservation] = {}
    for row in (await db.execute(select(AddressObservation).where(AddressObservation.asset_id.in_(ids))
                                 .order_by(AddressObservation.last_seen))).scalars():  # fmt: skip
        if ":" in row.ip and row.asset_id in obs and ":" not in obs[row.asset_id].ip:
            continue  # prefer showing an IPv4 address as the primary one
        obs[row.asset_id] = row
    open_counts: dict[int, int] = {
        asset_id: n
        for asset_id, n in (
            await db.execute(
                select(Finding.asset_id, func.count())
                .where(Finding.asset_id.in_(ids), Finding.status.in_(engine.ACTIVE_STATUSES))
                .group_by(Finding.asset_id)
            )
        ).all()  # fmt: skip
    }
    # For hosts with an agent, the address on the default-route interface is the primary one
    # (not a container bridge that happens to have been reported last).
    primary_ip: dict[int, tuple[str, str | None]] = {}
    default_ifaces = await db.execute(
        select(Interface).where(Interface.asset_id.in_(ids), Interface.is_default_route.is_(True))
    )
    for iface in default_ifaces.scalars():
        v4 = next((addr["address"] for addr in iface.addresses if addr.get("family") == "ipv4"), None)
        if v4:
            primary_ip[iface.asset_id] = (v4, iface.mac)
    out = []
    for a in assets:
        o: AddressObservation | None = obs.get(a.id)
        src = sources.get(a.last_seen_source_id) if a.last_seen_source_id else None
        out.append(S.DeviceOut(
            id=a.id, name=a.display_name or a.hostname or (o.hostname if o else None) or (o.ip if o else f"Device #{a.id}"),
            display_name=a.display_name, hostname=a.hostname or (o.hostname if o else None), asset_type=a.asset_type,
            group_name=a.group_name, owner=a.owner, tags=a.tags, notes=a.notes, favorite=a.favorite, vendor=a.vendor,
            vendor_method=a.vendor_method, confidence=a.confidence, confidence_reason=a.confidence_reason,
            mac_randomized=a.mac_randomized, managed=a.managed, is_demo=a.is_demo, first_seen=a.first_seen,
            last_seen=a.last_seen, last_seen_source=src.name if src else None,
            presence=S.Presence(**views.presence(a, now, settings.freshness, agent_seen.get(a.id))),
            primary_ip=primary_ip[a.id][0] if a.id in primary_ip else (o.ip if o else None),
            primary_mac=primary_ip[a.id][1] if a.id in primary_ip else ((o.mac or None) if o else None),
            open_findings=open_counts.get(a.id, 0), merged_into_id=a.merged_into_id,
        ))  # fmt: skip
    return out


@router.get("/devices", tags=["devices"], response_model=S.DevicePage)
async def list_devices(
    _: CurrentUser,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=100)] = None,
    asset_type: str | None = None,
    group: str | None = None,
    tag: str | None = None,
    favorite: bool | None = None,
    managed: bool | None = None,
    include_merged: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> S.DevicePage:
    stmt = select(Asset)
    if not include_merged:
        stmt = stmt.where(Asset.merged_into_id.is_(None))
    if asset_type:
        stmt = stmt.where(Asset.asset_type == asset_type)
    if group:
        stmt = stmt.where(Asset.group_name == group)
    if tag:
        stmt = stmt.where(Asset.tags.contains([tag]))
    if favorite is not None:
        stmt = stmt.where(Asset.favorite.is_(favorite))
    if managed is not None:
        stmt = stmt.where(Asset.managed.is_(managed))
    if q:
        like = f"%{q.lower()}%"
        matching = select(AddressObservation.asset_id).where(
            or_(
                AddressObservation.ip.ilike(like),
                AddressObservation.mac.ilike(like),
                AddressObservation.hostname.ilike(like),
            )
        )
        stmt = stmt.where(or_(func.lower(Asset.display_name).like(like), func.lower(Asset.hostname).like(like),
                              func.lower(Asset.vendor).like(like), Asset.id.in_(matching)))  # fmt: skip
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = list(
        (
            await db.execute(
                stmt.order_by(Asset.favorite.desc(), Asset.last_seen.desc().nulls_last(), Asset.id)
                .limit(limit)
                .offset(offset)
            )
        ).scalars()  # fmt: skip
    )
    return S.DevicePage(items=await _device_rows(db, rows), total=total, limit=limit, offset=offset,
                        coverage_note=COVERAGE_NOTE)  # fmt: skip


def _finding_out(f: Finding, asset_name: str) -> S.FindingOut:
    return S.FindingOut(
        id=f.id, asset_id=f.asset_id, asset_name=asset_name, source_id=f.source_id, rule_id=f.rule_id,
        rule_version=f.rule_version, subject=f.subject, category=f.category, title=f.title, observation=f.observation,
        inference=f.inference, impact=f.impact, classification=f.classification, severity=f.severity,
        severity_rationale=f.severity_rationale, confidence=f.confidence, status=f.status,
        verification_status=f.verification_status, first_observed_at=f.first_observed_at,
        last_observed_at=f.last_observed_at, resolved_at=f.resolved_at, snoozed_until=f.snoozed_until, is_demo=f.is_demo,
    )  # fmt: skip


@router.get("/devices/{device_id}", tags=["devices"], response_model=S.DeviceDetail)
async def get_device(device_id: int, _: CurrentUser, db: DbSession) -> S.DeviceDetail:
    asset = await db.get(Asset, device_id)
    if asset is None:
        raise HTTPException(404, "device not found")
    device = (await _device_rows(db, [asset]))[0]
    sources = {s.id: s.name for s in (await db.execute(select(DataSource))).scalars()}

    def src(i: int | None) -> str | None:
        return sources.get(i) if i else None

    identities = [
        {"kind": i.kind, "value": i.value.split(":", 1)[1] if i.kind == "ip_only" else i.value, "confidence": i.confidence,
         "source": src(i.source_id), "first_seen": i.first_seen, "last_seen": i.last_seen,
         "locally_administered": i.kind == "mac" and identity.is_locally_administered(i.value),
         "merged_from": i.original_asset_id}
        for i in (await db.execute(select(AssetIdentity).where(AssetIdentity.asset_id == asset.id).order_by(AssetIdentity.id))).scalars()
    ]  # fmt: skip
    addresses = [
        {"ip": o.ip, "mac": o.mac or None, "hostname": o.hostname, "evidence": o.kind, "source": src(o.source_id),
         "first_seen": o.first_seen, "last_seen": o.last_seen, "merged_from": o.original_asset_id}
        for o in (await db.execute(select(AddressObservation).where(AddressObservation.asset_id == asset.id)
                                   .order_by(AddressObservation.last_seen.desc()).limit(50))).scalars()
    ]  # fmt: skip
    interfaces = [
        {"id": i.id, "name": i.name, "mac": i.mac, "is_up": i.is_up, "link_speed_mbps": i.link_speed_mbps, "mtu": i.mtu,
         "addresses": i.addresses, "is_default_route": i.is_default_route, "last_seen": i.last_seen}
        for i in (await db.execute(select(Interface).where(Interface.asset_id == asset.id).order_by(Interface.name))).scalars()
    ]  # fmt: skip
    services = [
        {"id": s.id, "origin": s.origin, "proto": s.proto, "address": s.address, "port": s.port, "process": s.process,
         "container": s.container, "active": s.active, "first_seen": s.first_seen, "last_seen": s.last_seen}
        for s in (await db.execute(select(ObservedService).where(ObservedService.asset_id == asset.id)
                                   .order_by(ObservedService.active.desc(), ObservedService.port))).scalars()
    ]  # fmt: skip
    series = list((await db.execute(select(MetricSeries).where(MetricSeries.asset_id == asset.id)
                                    .order_by(MetricSeries.metric, MetricSeries.label))).scalars())  # fmt: skip
    findings = [
        _finding_out(f, device.name).model_dump()
        for f in (
            await db.execute(
                select(Finding).where(Finding.asset_id == asset.id).order_by(Finding.last_observed_at.desc())
            )
        ).scalars()
    ]
    alerts = [
        S.AlertOut.model_validate(a).model_dump()
        for a in (
            await db.execute(
                select(Alert).where(Alert.asset_id == asset.id).order_by(Alert.started_at.desc()).limit(20)
            )
        ).scalars()
    ]
    events = [
        S.EventOut.model_validate(e).model_dump()
        for e in (
            await db.execute(
                select(Event).where(Event.asset_id == asset.id).order_by(Event.ts.desc()).limit(50)
            )
        ).scalars()
    ]
    merges = [
        {"id": m.id, "source_asset_id": m.source_asset_id, "target_asset_id": m.target_asset_id, "merged_at": m.merged_at,
         "undone_at": m.undone_at, "reason": m.reason, "automatic": m.merged_by is None}
        for m in (await db.execute(select(AssetMerge).where(or_(AssetMerge.source_asset_id == asset.id,
                                                                AssetMerge.target_asset_id == asset.id))
                                   .order_by(AssetMerge.merged_at.desc()))).scalars()
    ]  # fmt: skip
    return S.DeviceDetail(device=device, identities=identities, addresses=addresses, interfaces=interfaces,
                          services=services, series=[S.SeriesOut.model_validate(s) for s in series], findings=findings,
                          alerts=alerts, events=events, merges=merges)  # fmt: skip


@router.patch("/devices/{device_id}", tags=["devices"], response_model=S.DeviceOut)
async def patch_device(device_id: int, body: S.DevicePatch, admin: AdminUser, db: DbSession) -> S.DeviceOut:
    asset = await db.get(Asset, device_id)
    if asset is None:
        raise HTTPException(404, "device not found")
    changes = body.model_dump(exclude_unset=True)
    if "asset_type" in changes and changes["asset_type"] not in ASSET_TYPES:
        raise HTTPException(422, f"asset_type must be one of {list(ASSET_TYPES)}")
    before = {k: getattr(asset, k) for k in changes}
    for k, v in changes.items():
        setattr(asset, k, (v or None) if isinstance(v, str) and k != "asset_type" else v)
    if changes:
        add_audit(db, "asset.update", actor_user_id=admin.user.id, actor_label=admin.user.username, target_type="asset",
                  target_id=asset.id, details={"before": before, "after": changes})  # fmt: skip
        add_event(db, "device.edited", f"Device details edited by {admin.user.username}", asset_id=asset.id,
                  data={"fields": sorted(changes)})  # fmt: skip
    await db.flush()
    return (await _device_rows(db, [asset]))[0]


@router.post("/devices/{device_id}/merge", tags=["devices"])
async def merge_device(
    device_id: int, body: S.MergeRequest, admin: AdminUser, db: DbSession
) -> dict[str, Any]:
    """Merge *this* device into ``into_id`` (explicit, audited, undoable)."""
    source_asset, target = await db.get(Asset, device_id), await db.get(Asset, body.into_id)
    if source_asset is None or target is None:
        raise HTTPException(404, "device not found")
    try:
        merge = await identity.merge_assets(db, source_asset=source_asset, target_asset=target, user_id=admin.user.id,
                                            reason=body.reason or "manual merge")  # fmt: skip
    except identity.MergeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"merge_id": merge.id, "target_asset_id": target.id}


@router.post("/devices/merges/{merge_id}/undo", tags=["devices"])
async def undo_merge(merge_id: int, admin: AdminUser, db: DbSession) -> dict[str, Any]:
    merge = await db.get(AssetMerge, merge_id)
    if merge is None:
        raise HTTPException(404, "merge not found")
    try:
        await identity.unmerge(db, merge=merge, user_id=admin.user.id)
    except identity.MergeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"merge_id": merge.id, "restored_asset_id": merge.source_asset_id}


@router.post("/devices/import", tags=["devices"])
async def import_clients(body: S.ImportRequest, admin: AdminUser, db: DbSession) -> dict[str, Any]:
    """Import a router's client/DHCP list (CSV with header, or JSON array)."""
    now = utcnow()
    try:
        records, errors = parse_client_list(body.content, now)
    except ImportError_ as exc:
        raise HTTPException(422, str(exc)) from exc
    stats = await apply_import(db, ClientListImportProvider(records), now=now, is_demo=get_config().demo_mode)
    add_audit(db, "inventory.import", actor_user_id=admin.user.id, actor_label=admin.user.username,
              details={"rows": len(records), "errors": len(errors)})  # fmt: skip
    return {**stats, "rows": len(records), "row_errors": errors[:50]}


# ------------------------------------------------------------------ findings


async def _asset_names(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = (
        await db.execute(select(Asset.id, Asset.display_name, Asset.hostname).where(Asset.id.in_(ids)))
    ).all()
    return {i: d or h or f"Device #{i}" for i, d, h in rows}


@router.get("/findings", tags=["security"], response_model=list[S.FindingOut])
async def list_findings(
    _: CurrentUser,
    db: DbSession,
    status: Literal[
        "active", "open", "acknowledged", "snoozed", "resolved", "closed_unverified", "all"
    ] = "active",
    severity: str | None = None,
    asset_id: int | None = None,
    category: str | None = None,
) -> list[S.FindingOut]:
    stmt = select(Finding)
    if status == "active":
        stmt = stmt.where(Finding.status.in_(engine.ACTIVE_STATUSES))
    elif status != "all":
        stmt = stmt.where(Finding.status == status)
    if severity:
        stmt = stmt.where(Finding.severity == severity)
    if asset_id:
        stmt = stmt.where(Finding.asset_id == asset_id)
    if category:
        stmt = stmt.where(Finding.category == category)
    rows = list((await db.execute(stmt.order_by(Finding.last_observed_at.desc()).limit(500))).scalars())
    names = await _asset_names(db, {f.asset_id for f in rows})
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    rows.sort(key=lambda f: (f.status == "resolved", order.get(f.severity, 9)))
    return [_finding_out(f, names.get(f.asset_id, "?")) for f in rows]


@router.get("/findings/{finding_id}", tags=["security"], response_model=S.FindingDetail)
async def get_finding(finding_id: int, _: CurrentUser, db: DbSession) -> S.FindingDetail:
    f = await db.get(Finding, finding_id)
    if f is None:
        raise HTTPException(404, "finding not found")
    names = await _asset_names(db, {f.asset_id})
    evidence = list((await db.execute(select(FindingEvidence).where(FindingEvidence.finding_id == f.id)
                                      .order_by(FindingEvidence.collected_at, FindingEvidence.id))).scalars())  # fmt: skip
    steps = list((await db.execute(select(RemediationStep).where(RemediationStep.finding_id == f.id)
                                   .order_by(RemediationStep.position))).scalars())  # fmt: skip
    runs = list((await db.execute(select(VerificationRun).where(VerificationRun.finding_id == f.id)
                                  .order_by(VerificationRun.requested_at.desc()).limit(25))).scalars())  # fmt: skip
    diffs = {d.verification_run_id: d for d in (await db.execute(
        select(VerificationDiff).where(VerificationDiff.verification_run_id.in_([r.id for r in runs] or [0])))).scalars()}  # fmt: skip
    history = [
        {"ts": e.ts, "kind": e.kind, "message": e.message, "data": e.data}
        for e in (
            await db.execute(
                select(Event).where(Event.finding_id == f.id).order_by(Event.ts.desc()).limit(50)
            )
        ).scalars()
    ]
    return S.FindingDetail(
        finding=_finding_out(f, names.get(f.asset_id, "?")),
        evidence=[S.EvidenceOut.model_validate(e) for e in evidence],
        remediation=[S.RemediationOut.model_validate(s) for s in steps],
        verifications=[
            S.VerificationOut(
                id=r.id, trigger=r.trigger, status=r.status, outcome=r.outcome, outcome_reason=r.outcome_reason,
                algorithm_version=r.algorithm_version, requested_at=r.requested_at, deadline_at=r.deadline_at,
                completed_at=r.completed_at, before_evidence_id=r.before_evidence_id, after_evidence_id=r.after_evidence_id,
                diff={"summary": diffs[r.id].summary, "before": diffs[r.id].before, "after": diffs[r.id].after,
                      "changes": diffs[r.id].changes} if r.id in diffs else None,
            ) for r in runs
        ],
        history=history, disclaimer=DISCLAIMER,
    )  # fmt: skip


@router.post(
    "/findings/{finding_id}/verify", tags=["security"], response_model=S.VerificationOut, status_code=202
)
async def verify_finding(finding_id: int, admin: AdminUser, db: DbSession) -> S.VerificationOut:
    """Re-run the same read-only check on fresh evidence. The result arrives asynchronously:
    ``resolved`` only if evidence collected *after this request* no longer shows the condition."""
    f = await db.get(Finding, finding_id)
    if f is None:
        raise HTTPException(404, "finding not found")
    if f.rule_id == "OBS-STALE-001":
        raise HTTPException(409, "this finding resolves automatically when the agent reports again")
    if f.source_id is None:
        raise HTTPException(
            409, "the source that produced this finding no longer exists; it cannot be re-checked"
        )
    pending = (await db.execute(select(VerificationRun).where(VerificationRun.finding_id == f.id,
                                                              VerificationRun.status == "pending"))).scalars().first()  # fmt: skip
    if pending is None:
        settings = await get_runtime_settings(db)
        pending = await engine.request_verification(db, finding=f, user_id=admin.user.id,
                                                    timeout_s=settings.posture.verify_timeout_s)  # fmt: skip
        add_audit(db, "finding.verify", actor_user_id=admin.user.id, actor_label=admin.user.username,
                  target_type="finding", target_id=f.id)  # fmt: skip
    return S.VerificationOut(
        id=pending.id, trigger=pending.trigger, status=pending.status, outcome=pending.outcome,
        outcome_reason=pending.outcome_reason, algorithm_version=pending.algorithm_version,
        requested_at=pending.requested_at, deadline_at=pending.deadline_at, completed_at=pending.completed_at,
        before_evidence_id=None, after_evidence_id=None, diff=None,
    )  # fmt: skip


@router.post("/findings/{finding_id}/status", tags=["security"], response_model=S.FindingOut)
async def finding_action(
    finding_id: int, body: S.FindingAction, admin: AdminUser, db: DbSession
) -> S.FindingOut:
    """Acknowledge / snooze / close / reopen. ``close`` yields ``closed_unverified`` — only a
    successful re-check can produce ``resolved``."""
    f = await db.get(Finding, finding_id)
    if f is None:
        raise HTTPException(404, "finding not found")
    now = utcnow()
    before = f.status
    if body.action == "acknowledge":
        if f.status not in ("open", "snoozed"):
            raise HTTPException(409, f"cannot acknowledge a finding that is {f.status}")
        f.status, f.snoozed_until = "acknowledged", None
    elif body.action == "snooze":
        if body.until is None or ensure_utc(body.until) <= now:
            raise HTTPException(422, "snooze needs a future 'until' timestamp")
        if f.status not in engine.ACTIVE_STATUSES:
            raise HTTPException(409, f"cannot snooze a finding that is {f.status}")
        f.status, f.snoozed_until = "snoozed", ensure_utc(body.until)
    elif body.action == "close":
        if f.status not in engine.ACTIVE_STATUSES:
            raise HTTPException(409, f"finding is already {f.status}")
        f.status, f.snoozed_until = "closed_unverified", None
    else:
        if f.status in ("open",):
            raise HTTPException(409, "finding is already open")
        if f.status == "resolved":
            raise HTTPException(
                409, "a verified-resolved finding reopens by itself if the condition is observed again"
            )
        f.status, f.snoozed_until = "open", None
    f.status_changed_at = now
    names = await _asset_names(db, {f.asset_id})
    add_event(db, f"finding.{body.action}", f"Finding {body.action}d by {admin.user.username}: {f.title}"
              + (" (closed without verification)" if body.action == "close" else ""), asset_id=f.asset_id,
              finding_id=f.id, data={"from": before, "to": f.status, "note": body.note}, is_demo=f.is_demo)  # fmt: skip
    add_audit(db, f"finding.{body.action}", actor_user_id=admin.user.id, actor_label=admin.user.username,
              target_type="finding", target_id=f.id, details={"from": before, "to": f.status, "note": body.note})  # fmt: skip
    return _finding_out(f, names.get(f.asset_id, "?"))


@router.get("/checks", tags=["security"])
async def list_checks(_: CurrentUser, db: DbSession) -> list[dict[str, Any]]:
    """Rule catalog with the most recent result per rule (including Unsupported/Unknown)."""
    checks = list((await db.execute(select(Check).order_by(Check.rule_id))).scalars())
    latest: dict[str, dict[str, Any]] = {}
    rows = (
        await db.execute(
            select(CheckResult, Assessment.finished_at, Assessment.asset_id)
            .join(Assessment, Assessment.id == CheckResult.assessment_id)
            .where(Assessment.started_at > utcnow() - timedelta(days=7))
            .order_by(Assessment.started_at.desc())
            .limit(2000)  # fmt: skip
        )
    ).all()
    for result, finished, asset_id in rows:
        key = f"{result.rule_id}:{asset_id}"
        if key not in latest:
            latest[key] = {"asset_id": asset_id, "status": result.status, "summary": result.summary, "at": finished,
                           "rule_id": result.rule_id}  # fmt: skip
    names = await _asset_names(db, {v["asset_id"] for v in latest.values()})
    out = []
    for c in checks:
        results = [
            {**v, "asset_name": names.get(v["asset_id"], "?")}
            for v in latest.values()
            if v["rule_id"] == c.rule_id
        ]
        statuses = {r["status"] for r in results}
        if c.rule_id == "OBS-STALE-001":
            coverage = "continuous"  # evaluated by the scheduler from heartbeats, not from posture snapshots
        elif not results:
            coverage = "unsupported" if c.platform == "router" else "not_evaluated"
        elif statuses & {"pass", "fail"}:
            coverage = "evaluated"
        elif "unknown" in statuses:
            coverage = "unknown"  # ran, but its input could not be read anywhere
        else:
            coverage = "unsupported"
        out.append({"rule_id": c.rule_id, "version": c.version, "title": c.title, "description": c.description,
                    "platform": c.platform, "category": c.category, "default_severity": c.default_severity,
                    "classification": c.classification, "coverage": coverage, "results": results})  # fmt: skip
    return out


@router.get("/assessments", tags=["security"])
async def list_assessments(_: CurrentUser, db: DbSession, asset_id: int | None = None,
                           limit: Annotated[int, Query(ge=1, le=200)] = 30) -> list[dict[str, Any]]:  # fmt: skip
    stmt = select(Assessment).order_by(Assessment.started_at.desc()).limit(limit)
    if asset_id:
        stmt = stmt.where(Assessment.asset_id == asset_id)
    rows = list((await db.execute(stmt)).scalars())
    results: dict[int, list[dict[str, Any]]] = {}
    for r in (
        await db.execute(
            select(CheckResult).where(CheckResult.assessment_id.in_([a.id for a in rows] or [0]))
        )
    ).scalars():
        results.setdefault(r.assessment_id, []).append(
            {"rule_id": r.rule_id, "rule_version": r.rule_version, "status": r.status, "summary": r.summary}
        )
    return [
        {"id": a.id, "asset_id": a.asset_id, "source_id": a.source_id, "trigger": a.trigger, "engine_version": a.engine_version,
         "started_at": a.started_at, "finished_at": a.finished_at, "summary": a.summary, "results": results.get(a.id, [])}
        for a in rows
    ]  # fmt: skip


# ------------------------------------------------------------------ alerts


@router.get("/alerts", tags=["alerts"], response_model=list[S.AlertOut])
async def list_alerts(_: CurrentUser, db: DbSession, state: Literal["active", "resolved", "all"] = "active",
                      limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[Alert]:  # fmt: skip
    stmt = select(Alert).order_by(Alert.started_at.desc()).limit(limit)
    if state == "active":
        stmt = stmt.where(Alert.state != "resolved")
    elif state == "resolved":
        stmt = stmt.where(Alert.state == "resolved")
    return list((await db.execute(stmt)).scalars())


async def _alert_or_404(db: AsyncSession, alert_id: int) -> Alert:
    alert = await db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(404, "alert not found")
    return alert


@router.post("/alerts/{alert_id}/acknowledge", tags=["alerts"], response_model=S.AlertOut)
async def acknowledge_alert(alert_id: int, admin: AdminUser, db: DbSession) -> Alert:
    alert = await _alert_or_404(db, alert_id)
    if alert.state == "resolved":
        raise HTTPException(409, "alert is already resolved")
    if alert.state != "acknowledged":
        alert.state, alert.acknowledged_at, alert.acknowledged_by = "acknowledged", utcnow(), admin.user.id
        add_event(db, "alert.acknowledged", f"Acknowledged by {admin.user.username}: {alert.title}", alert_id=alert.id,
                  asset_id=alert.asset_id, is_demo=alert.is_demo)  # fmt: skip
        add_audit(db, "alert.acknowledge", actor_user_id=admin.user.id, actor_label=admin.user.username,
                  target_type="alert", target_id=alert.id)  # fmt: skip
    return alert


@router.post("/alerts/{alert_id}/snooze", tags=["alerts"], response_model=S.AlertOut)
async def snooze_alert(alert_id: int, body: S.SnoozeRequest, admin: AdminUser, db: DbSession) -> Alert:
    alert = await _alert_or_404(db, alert_id)
    until = ensure_utc(body.until)
    if until <= utcnow():
        raise HTTPException(422, "'until' must be in the future")
    alert.snoozed_until = until
    add_event(db, "alert.snoozed", f"Snoozed until {until.isoformat(timespec='minutes')}: {alert.title}", alert_id=alert.id,
              is_demo=alert.is_demo)  # fmt: skip
    add_audit(db, "alert.snooze", actor_user_id=admin.user.id, actor_label=admin.user.username, target_type="alert",
              target_id=alert.id, details={"until": until.isoformat()})  # fmt: skip
    return alert


@router.post("/alerts/{alert_id}/resolve", tags=["alerts"], response_model=S.AlertOut)
async def close_alert(alert_id: int, admin: AdminUser, db: DbSession) -> Alert:
    alert = await _alert_or_404(db, alert_id)
    await alert_service.resolve_alert(
        db, alert, now=utcnow(), reason=f"closed manually by {admin.user.username}"
    )
    add_audit(db, "alert.resolve", actor_user_id=admin.user.id, actor_label=admin.user.username, target_type="alert",
              target_id=alert.id)  # fmt: skip
    return alert


@router.get("/alert-rules", tags=["alerts"], response_model=list[S.AlertRuleOut])
async def list_alert_rules(_: CurrentUser, db: DbSession) -> list[AlertRule]:
    return list((await db.execute(select(AlertRule).order_by(AlertRule.id))).scalars())


@router.patch("/alert-rules/{rule_id}", tags=["alerts"], response_model=S.AlertRuleOut)
async def patch_alert_rule(
    rule_id: int, body: S.AlertRulePatch, admin: AdminUser, db: DbSession
) -> AlertRule:
    rule = await db.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(404, "rule not found")
    if body.enabled is not None:
        rule.enabled = body.enabled
    if body.severity is not None:
        rule.severity = body.severity
    if body.params is not None:
        unknown = set(body.params) - set(rule.params)
        if unknown:
            raise HTTPException(422, f"unknown parameter(s) for this rule: {sorted(unknown)}")
        merged = {**rule.params, **body.params}
        for hi, lo in (("threshold_ms", "clear_threshold_ms"), ("threshold_pct", "clear_threshold_pct"),
                       ("threshold_bps", "clear_threshold_bps")):  # fmt: skip
            if hi in merged and merged[lo] > merged[hi]:
                raise HTTPException(422, f"{lo} must not exceed {hi} (hysteresis)")
        rule.params = merged
    add_audit(db, "alert_rule.update", actor_user_id=admin.user.id, actor_label=admin.user.username,
              target_type="alert_rule", target_id=rule.id, details=body.model_dump(exclude_none=True))  # fmt: skip
    return rule


@router.get("/maintenance-windows", tags=["alerts"], response_model=list[S.MaintenanceOut])
async def list_windows(_: CurrentUser, db: DbSession) -> list[MaintenanceWindow]:
    return list((await db.execute(select(MaintenanceWindow).where(MaintenanceWindow.ends_at > utcnow() - timedelta(days=7))
                                  .order_by(MaintenanceWindow.starts_at.desc()))).scalars())  # fmt: skip


@router.post("/maintenance-windows", tags=["alerts"], response_model=S.MaintenanceOut, status_code=201)
async def create_window(body: S.MaintenanceIn, admin: AdminUser, db: DbSession) -> MaintenanceWindow:
    start, end = ensure_utc(body.starts_at), ensure_utc(body.ends_at)
    if end <= start or end - start > timedelta(days=14):
        raise HTTPException(422, "window must end after it starts and last at most 14 days")
    from observenet.models.alerts import ALERT_KINDS

    if set(body.alert_kinds) - set(ALERT_KINDS):
        raise HTTPException(422, "unknown alert kind")
    window = MaintenanceWindow(name=body.name, starts_at=start, ends_at=end, alert_kinds=body.alert_kinds,
                               created_by=admin.user.id)  # fmt: skip
    db.add(window)
    await db.flush()
    add_audit(db, "maintenance.create", actor_user_id=admin.user.id, actor_label=admin.user.username,
              target_type="maintenance_window", target_id=window.id)  # fmt: skip
    return window


@router.delete("/maintenance-windows/{window_id}", tags=["alerts"], status_code=204)
async def delete_window(window_id: int, admin: AdminUser, db: DbSession) -> None:
    window = await db.get(MaintenanceWindow, window_id)
    if window is None:
        raise HTTPException(404, "window not found")
    await db.delete(window)
    add_audit(db, "maintenance.delete", actor_user_id=admin.user.id, actor_label=admin.user.username,
              target_type="maintenance_window", target_id=window_id)  # fmt: skip


@router.get("/events", tags=["alerts"], response_model=list[S.EventOut])
async def list_events(_: CurrentUser, db: DbSession, kind_prefix: Annotated[str | None, Query(max_length=48)] = None,
                      asset_id: int | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[Event]:  # fmt: skip
    stmt = select(Event).order_by(Event.ts.desc(), Event.id.desc()).limit(limit)
    if kind_prefix:
        stmt = stmt.where(Event.kind.like(f"{kind_prefix}%"))
    if asset_id:
        stmt = stmt.where(Event.asset_id == asset_id)
    return list((await db.execute(stmt)).scalars())


# ------------------------------------------------------------------ probes + connectivity


async def _validated(db: AsyncSession, body: S.ProbeTargetIn) -> None:
    settings = await get_runtime_settings(db)
    try:
        validate_probe_target(kind=body.kind, scope=body.scope, host=body.host, port=body.port,
                              approved_cidrs=settings.approved_cidrs)  # fmt: skip
    except ScopeError as exc:
        raise HTTPException(422, str(exc)) from exc
    floor = 1.0 if body.scope != "external" else 10.0
    if body.interval_s < floor:
        raise HTTPException(422, f"{body.scope} targets may not be probed more often than every {floor:g} s")


@router.get("/probe-targets", tags=["connectivity"], response_model=list[S.ProbeTargetOut])
async def list_targets(_: CurrentUser, db: DbSession) -> list[ProbeTarget]:
    return list((await db.execute(select(ProbeTarget).order_by(ProbeTarget.id))).scalars())


@router.post("/probe-targets", tags=["connectivity"], response_model=S.ProbeTargetOut, status_code=201)
async def create_target(body: S.ProbeTargetIn, admin: AdminUser, db: DbSession) -> ProbeTarget:
    await _validated(db, body)
    count = (await db.execute(select(func.count()).select_from(ProbeTarget))).scalar_one()
    if count >= 20:
        raise HTTPException(409, "at most 20 probe targets are allowed")
    target = ProbeTarget(**{**body.model_dump(), "host": body.host.strip().lower()})
    db.add(target)
    await db.flush()
    add_audit(db, "probe_target.create", actor_user_id=admin.user.id, actor_label=admin.user.username,
              target_type="probe_target", target_id=target.id, details=body.model_dump())  # fmt: skip
    return target


@router.patch("/probe-targets/{target_id}", tags=["connectivity"], response_model=S.ProbeTargetOut)
async def update_target(
    target_id: int, body: S.ProbeTargetIn, admin: AdminUser, db: DbSession
) -> ProbeTarget:
    target = await db.get(ProbeTarget, target_id)
    if target is None:
        raise HTTPException(404, "probe target not found")
    await _validated(db, body)
    for k, v in body.model_dump().items():
        setattr(target, k, v.strip().lower() if k == "host" else v)
    add_audit(db, "probe_target.update", actor_user_id=admin.user.id, actor_label=admin.user.username,
              target_type="probe_target", target_id=target.id, details=body.model_dump())  # fmt: skip
    return target


@router.delete("/probe-targets/{target_id}", tags=["connectivity"], status_code=204)
async def delete_target(target_id: int, admin: AdminUser, db: DbSession) -> None:
    target = await db.get(ProbeTarget, target_id)
    if target is None:
        raise HTTPException(404, "probe target not found")
    await db.delete(target)
    add_audit(db, "probe_target.delete", actor_user_id=admin.user.id, actor_label=admin.user.username,
              target_type="probe_target", target_id=target_id)  # fmt: skip


@router.get("/connectivity/summary", tags=["connectivity"], response_model=S.ConnectivityOut)
async def connectivity_summary(_: CurrentUser, db: DbSession,
                               window_s: Annotated[int, Query(ge=10, le=3600)] = 60) -> S.ConnectivityOut:  # fmt: skip
    now = utcnow()
    targets = list((await db.execute(select(ProbeTarget).order_by(ProbeTarget.id))).scalars())
    summaries = []
    for t in targets:
        win = max(window_s, int(t.interval_s * 10))
        pts = await load_points(db, target_id=t.id, since=now - timedelta(seconds=win))
        st = compute_window_stats(pts, now=now, window_s=win)
        last = (await db.execute(select(ProbeResult).where(ProbeResult.target_id == t.id)
                                 .order_by(ProbeResult.ts.desc()).limit(1))).scalar_one_or_none()  # fmt: skip
        note = None
        if not t.enabled:
            state = "disabled"
        elif st.sample_size == 0:
            state, note = "no_data", f"No results in the last {win} s."
        elif st.successes == 0:
            state = "not_responding"
            note = ("No reply. A gateway that filters ICMP looks identical to one that is down; check the external signals."
                    if t.scope == "gateway" else "No reply. The endpoint may be down or filtered; this alone is not an outage.")  # fmt: skip
        elif st.loss_pct and st.loss_pct > 0:
            state = "lossy"
        else:
            state = "ok"
        summaries.append(S.TargetSummary(
            target=S.ProbeTargetOut.model_validate(t), window_s=win, sample_size=st.sample_size, loss_pct=st.loss_pct,
            latency_avg_ms=st.latency_avg_ms, latency_min_ms=st.latency_min_ms, latency_max_ms=st.latency_max_ms,
            latency_p95_ms=st.latency_p95_ms, jitter_ms=st.jitter_ms, last_ts=st.last_ts or (last.ts if last else None),
            last_method=last.method if last else None, last_error=last.error if last else None,
            resolved_host=last.resolved_host if last else None, state=state, note=note,
        ))  # fmt: skip
    inet = await internet_status(db, now=now)
    outages = [
        {"id": o.id, "kind": o.kind, "started_at": o.started_at, "ended_at": o.ended_at, "evidence": o.evidence,
         "duration_s": ((o.ended_at or now) - o.started_at).total_seconds()}
        for o in (await db.execute(select(Outage).order_by(Outage.started_at.desc()).limit(20))).scalars()
    ]  # fmt: skip
    return S.ConnectivityOut(
        generated_at=now, targets=summaries, outages=outages,
        internet={"status": inet.status, "reason": inet.reason, "signals_total": inet.signals_total, "signals_ok": inet.signals_ok},
        wifi={"status": "unsupported", "detail": "Wi-Fi signal data needs an access-point/controller provider; none is implemented."},
    )  # fmt: skip


# ------------------------------------------------------------------ settings, audit, overview, insights, topology


@router.get("/settings", tags=["settings"], response_model=RuntimeSettings)
async def get_settings(_: CurrentUser, db: DbSession) -> RuntimeSettings:
    return await get_runtime_settings(db)


@router.put("/settings", tags=["settings"], response_model=RuntimeSettings)
async def put_settings(body: RuntimeSettings, admin: AdminUser, db: DbSession) -> RuntimeSettings:
    before = await get_runtime_settings(db)
    try:
        saved = await save_runtime_settings(db, body, admin.user.id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    changed = sorted(k for k in saved.model_dump() if saved.model_dump()[k] != before.model_dump()[k])
    add_audit(db, "settings.update", actor_user_id=admin.user.id, actor_label=admin.user.username,
              details={"changed_sections": changed})  # fmt: skip
    return saved


@router.get("/settings/deployment", tags=["settings"])
async def deployment_info(_: AdminUser) -> dict[str, Any]:
    """Non-secret deployment facts for the Settings → Domain page. Reports configuration only;
    it does not (and cannot) confirm that the public hostname is actually reachable."""
    cfg = get_config()
    return {
        "public_url": cfg.public_url, "allowed_hosts": cfg.allowed_host_list, "cookie_secure": cfg.cookie_secure_effective,
        "https_configured": cfg.public_url.startswith("https://"), "env": cfg.env, "demo_mode": cfg.demo_mode,
        "graphql_introspection": cfg.introspection_enabled, "otel_export": bool(cfg.otel_exporter_otlp_endpoint),
        "neo4j": bool(cfg.neo4j_uri), "webhook": bool(cfg.alert_webhook_url), "oui_file": bool(cfg.oui_file),
        "note": "These are configured values. ObserveNet cannot verify DNS, the tunnel or the edge access policy from inside.",
    }  # fmt: skip


@router.get("/audit", tags=["settings"], response_model=list[S.AuditOut])
async def list_audit(
    _: AdminUser, db: DbSession, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[AuditEvent]:
    return list(
        (
            await db.execute(
                select(AuditEvent).order_by(AuditEvent.ts.desc(), AuditEvent.id.desc()).limit(limit)
            )
        ).scalars()
    )


@router.get("/overview", tags=["overview"], response_model=S.OverviewOut)
async def overview(_: CurrentUser, db: DbSession) -> dict[str, Any]:
    return await views.build_overview(db, now=utcnow(), settings=await get_runtime_settings(db))


@router.get("/insights", tags=["overview"])
async def insights(_: CurrentUser, db: DbSession) -> dict[str, Any]:
    return await build_insights(db, now=utcnow())


@router.get("/topology", tags=["overview"])
async def topology(_: CurrentUser, db: DbSession) -> dict[str, Any]:
    return await views.build_topology(db)


# ------------------------------------------------------------------ exports (no secrets, no tokens)


def _csv_cell(value: Any) -> Any:
    """Neutralise spreadsheet formula injection: hostnames and notes are attacker-influenced
    (a device chooses its own hostname), so a leading = + - @ must not become a formula."""
    if isinstance(value, list):
        value = "; ".join(str(v) for v in value)
    if value is None:
        return ""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for r in rows:
        writer.writerow([_csv_cell(r.get(c)) for c in columns])
    return buf.getvalue()


@router.get("/export/devices", tags=["export"])
async def export_devices(admin: AdminUser, db: DbSession, format: Literal["csv", "json"] = "csv") -> Response:  # noqa: A002
    assets = list(
        (await db.execute(select(Asset).where(Asset.merged_into_id.is_(None)).order_by(Asset.id))).scalars()
    )
    rows = [
        {**d.model_dump(mode="json", exclude={"presence"}), "presence": d.presence.label}
        for d in await _device_rows(db, assets)
    ]
    add_audit(
        db,
        "export.devices",
        actor_user_id=admin.user.id,
        actor_label=admin.user.username,
        details={"format": format},
    )
    if format == "json":
        return Response(content=json.dumps(rows, indent=2), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="observenet-devices.json"'})  # fmt: skip
    cols = ["id", "name", "hostname", "asset_type", "group_name", "owner", "tags", "vendor", "primary_ip", "primary_mac",
            "confidence", "mac_randomized", "managed", "presence", "first_seen", "last_seen", "last_seen_source",
            "open_findings", "notes"]  # fmt: skip
    return Response(content=_csv(rows, cols), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="observenet-devices.csv"'})  # fmt: skip


@router.get("/export/findings", tags=["export"])
async def export_findings(
    admin: AdminUser, db: DbSession, format: Literal["csv", "json"] = "csv"
) -> Response:  # noqa: A002
    findings = list((await db.execute(select(Finding).order_by(Finding.id))).scalars())
    names = await _asset_names(db, {f.asset_id for f in findings})
    rows = [_finding_out(f, names.get(f.asset_id, "?")).model_dump(mode="json") for f in findings]
    add_audit(
        db,
        "export.findings",
        actor_user_id=admin.user.id,
        actor_label=admin.user.username,
        details={"format": format},
    )
    if format == "json":
        return Response(content=json.dumps(rows, indent=2), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="observenet-findings.json"'})  # fmt: skip
    cols = ["id", "asset_name", "rule_id", "rule_version", "title", "classification", "severity", "confidence", "status",
            "verification_status", "observation", "inference", "first_observed_at", "last_observed_at", "resolved_at"]  # fmt: skip
    return Response(content=_csv(rows, cols), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="observenet-findings.csv"'})  # fmt: skip


@router.get("/traffic/top-devices", tags=["metrics"])
async def top_devices(
    _: CurrentUser, db: DbSession, window_s: Annotated[int, Query(ge=30, le=3600)] = 300
) -> dict[str, Any]:
    """Top devices by traffic — only when a source provides trustworthy per-device counters.
    Without one this returns ``supported: false`` with guidance instead of an estimate."""
    now = utcnow()
    from observenet.models import MetricSample

    rows = (
        await db.execute(
            select(MetricSeries.label, MetricSeries.metric, MetricSeries.quality, MetricSeries.source_id,
                   func.avg(MetricSample.value), func.count(), func.max(MetricSample.ts))
            .join(MetricSample, MetricSample.series_id == MetricSeries.id)
            .where(MetricSeries.metric.in_(("device.rx_bps", "device.tx_bps")), MetricSample.ts > now - timedelta(seconds=window_s))
            .group_by(MetricSeries.id)
        )
    ).all()  # fmt: skip
    if not rows:
        return {
            "supported": False,
            "guidance": views.PER_DEVICE_GUIDANCE,
            "window_s": window_s,
            "devices": [],
        }
    by_mac: dict[str, dict[str, Any]] = {}
    for label, metric, quality, source_id, avg, n, last in rows:
        d = by_mac.setdefault(label, {"mac": label, "rx_bps": 0.0, "tx_bps": 0.0, "samples": 0, "last_ts": last,
                                      "quality": quality, "source_id": source_id})  # fmt: skip
        d["rx_bps" if metric == "device.rx_bps" else "tx_bps"] = float(avg)
        d["samples"] = max(d["samples"], int(n))
    idents = (
        await db.execute(
            select(AssetIdentity.value, Asset.id, Asset.display_name, Asset.hostname)
            .join(Asset, Asset.id == AssetIdentity.asset_id)
            .where(AssetIdentity.kind == "mac", AssetIdentity.value.in_(list(by_mac)))
        )
    ).all()
    last_ip: dict[int, str] = {}
    asset_ids = [asset_id for _mac, asset_id, _d, _h in idents]
    if asset_ids:
        seen = await db.execute(
            select(AddressObservation.asset_id, AddressObservation.ip)
            .where(AddressObservation.asset_id.in_(asset_ids))
            .order_by(AddressObservation.last_seen)
        )
        last_ip = {asset_id: ip for asset_id, ip in seen.all()}
    for mac, asset_id, display, host in idents:
        by_mac[mac].update(asset_id=asset_id, name=display or host or last_ip.get(asset_id) or mac)
    devices = sorted(by_mac.values(), key=lambda d: d["rx_bps"] + d["tx_bps"], reverse=True)[:10]
    sources = {s.id: s.name for s in (await db.execute(select(DataSource))).scalars()}
    for d in devices:
        d.setdefault("name", d["mac"])
        d["source"] = sources.get(d.pop("source_id"))
    return {"supported": True, "window_s": window_s, "devices": devices,
            "coverage": "Per-client counters as reported by the gateway source; devices it does not account for are absent."}  # fmt: skip


@router.get("/topology/peers/{asset_id}", tags=["overview"])
async def topology_peers(asset_id: int, _: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Devices observed on the same subnet(s). Answered by Neo4j when the optional graph
    profile is configured, by PostgreSQL otherwise (``backend`` says which)."""
    from observenet.services.graph_sync import GraphUnavailable, segment_peers

    if await db.get(Asset, asset_id) is None:
        raise HTTPException(404, "device not found")
    try:
        return await segment_peers(db, asset_id)
    except GraphUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - Neo4j down: say so instead of failing opaquely
        raise HTTPException(503, f"graph backend unavailable: {type(exc).__name__}") from exc
