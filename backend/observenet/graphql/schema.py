"""GraphQL read API for graph-shaped inventory queries.

Why GraphQL here and REST elsewhere: inventory is a graph (device → identities, addresses,
interfaces, services, findings, relationships) and screens need different slices of it, so
client-selected fields + batched loaders avoid both over-fetching and N+1 round trips.
Commands, ingestion and time-series stay on REST (simple contracts, cacheable, streamable).

Safety: session-authenticated (+CSRF header), read-only, depth/alias/token limits,
capped page sizes, introspection off in production, no GraphiQL in production.
"""

from __future__ import annotations

import base64
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import strawberry
from fastapi import HTTPException, Request
from sqlalchemy import func, select, tuple_
from strawberry.dataloader import DataLoader
from strawberry.extensions import DisableIntrospection, MaxAliasesLimiter, MaxTokensLimiter, QueryDepthLimiter
from strawberry.fastapi import BaseContext, GraphQLRouter
from strawberry.permission import BasePermission
from strawberry.types import Info

from observenet.auth import service
from observenet.auth.crypto import constant_time_equals
from observenet.core.config import get_config
from observenet.core.db import session_scope
from observenet.core.time import utcnow
from observenet.models import (
    AddressObservation,
    Alert,
    Assessment,
    Asset,
    AssetIdentity,
    AuditEvent,
    CheckResult,
    DataSource,
    Interface,
    NetworkSegment,
    ObservedService,
    User,
)
from observenet.models import Finding as FindingModel
from observenet.security.engine import ACTIVE_STATUSES
from observenet.services import views
from observenet.services.connectivity import internet_status
from observenet.services.settings import get_runtime_settings

MAX_PAGE = 100
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


# ------------------------------------------------------------------ context + auth


class Context(BaseContext):
    def __init__(self, user: User | None) -> None:
        super().__init__()
        self.user = user
        self.loaders = Loaders()


class IsAuthenticated(BasePermission):
    message = "authentication required"

    def has_permission(self, source: Any, info: Info[Context, Any], **kwargs: Any) -> bool:
        return info.context.user is not None


class IsAdmin(BasePermission):
    message = "administrator role required"

    def has_permission(self, source: Any, info: Info[Context, Any], **kwargs: Any) -> bool:
        return info.context.user is not None and info.context.user.role == "admin"


async def get_context(request: Request) -> Context:
    """Query-level authorization: no valid session, no GraphQL at all (not even errors-as-data)."""
    async with session_scope() as db:
        resolved = await service.resolve_session(db, request.cookies.get(service.SESSION_COOKIE))
    if resolved is None:
        raise HTTPException(401, "authentication required")
    sess, user = resolved
    if request.method == "POST":
        sent = request.headers.get(service.CSRF_HEADER, "")
        if not sent or not constant_time_equals(sent, sess.csrf_token):
            raise HTTPException(403, "missing or invalid CSRF token")
    return Context(user)


# ------------------------------------------------------------------ batched loaders (no N+1)


def _group_loader(model: Any, order_by: Any, where: Any = None) -> DataLoader[int, list[Any]]:
    async def load(asset_ids: list[int]) -> list[list[Any]]:
        async with session_scope() as db:
            stmt = select(model).where(model.asset_id.in_(asset_ids)).order_by(order_by)
            if where is not None:
                stmt = stmt.where(where)
            grouped: dict[int, list[Any]] = defaultdict(list)
            for row in (await db.execute(stmt)).scalars():
                grouped[row.asset_id].append(row)
        return [grouped.get(i, []) for i in asset_ids]

    return DataLoader(load_fn=load)


class Loaders:
    def __init__(self) -> None:
        self.identities = _group_loader(AssetIdentity, AssetIdentity.id)
        self.addresses = _group_loader(AddressObservation, AddressObservation.last_seen.desc())
        self.interfaces = _group_loader(Interface, Interface.name)
        self.services = _group_loader(ObservedService, ObservedService.port, ObservedService.active.is_(True))
        self.findings = _group_loader(FindingModel, FindingModel.last_observed_at.desc())
        self.assets: DataLoader[int, Asset | None] = DataLoader(load_fn=self._load_assets)
        self.source_names: DataLoader[int, str | None] = DataLoader(load_fn=self._load_source_names)
        self.segments: DataLoader[int, NetworkSegment | None] = DataLoader(load_fn=self._load_segments)

    @staticmethod
    async def _load_assets(ids: list[int]) -> list[Asset | None]:
        async with session_scope() as db:
            rows = {a.id: a for a in (await db.execute(select(Asset).where(Asset.id.in_(ids)))).scalars()}
        return [rows.get(i) for i in ids]

    @staticmethod
    async def _load_source_names(ids: list[int]) -> list[str | None]:
        async with session_scope() as db:
            rows = dict((await db.execute(select(DataSource.id, DataSource.name).where(DataSource.id.in_(ids)))).all())
        return [rows.get(i) for i in ids]

    @staticmethod
    async def _load_segments(ids: list[int]) -> list[NetworkSegment | None]:
        async with session_scope() as db:
            rows = {s.id: s for s in (await db.execute(select(NetworkSegment).where(NetworkSegment.id.in_(ids)))).scalars()}
        return [rows.get(i) for i in ids]


# ------------------------------------------------------------------ types


@strawberry.type
class Presence:
    state: str
    label: str
    basis: str


@strawberry.type
class Identity:
    kind: str
    value: str
    confidence: str
    first_seen: datetime
    last_seen: datetime


@strawberry.type
class AddressSighting:
    ip: str
    mac: str | None
    hostname: str | None
    evidence: str
    first_seen: datetime
    last_seen: datetime
    source_id: strawberry.Private[int]

    @strawberry.field
    async def source(self, info: Info[Context, Any]) -> str | None:
        return await info.context.loaders.source_names.load(self.source_id)


@strawberry.type
class NetworkInterface:
    name: str
    mac: str | None
    is_up: bool | None
    link_speed_mbps: int | None
    addresses: list[str]


@strawberry.type
class Service:
    origin: str
    proto: str
    address: str
    port: int
    process: str | None
    container: str | None
    last_seen: datetime


@strawberry.type
class Relationship:
    """An observed relationship. ``evidence`` says what it is based on; none implies reachability."""

    kind: str
    target_type: str
    target_id: str
    target_label: str
    evidence: str


@strawberry.type
class Finding:
    id: int
    asset_id: int
    rule_id: str
    rule_version: int
    title: str
    observation: str
    inference: str | None
    classification: str
    severity: str
    severity_rationale: str
    confidence: str
    status: str
    verification_status: str
    category: str
    first_observed_at: datetime
    last_observed_at: datetime
    resolved_at: datetime | None

    @strawberry.field
    async def device(self, info: Info[Context, Any]) -> Device | None:
        asset = await info.context.loaders.assets.load(self.asset_id)
        return _device(asset) if asset else None


def _finding(f: FindingModel) -> Finding:
    return Finding(
        id=f.id, asset_id=f.asset_id, rule_id=f.rule_id, rule_version=f.rule_version, title=f.title,
        observation=f.observation, inference=f.inference, classification=f.classification, severity=f.severity,
        severity_rationale=f.severity_rationale, confidence=f.confidence, status=f.status,
        verification_status=f.verification_status, category=f.category, first_observed_at=f.first_observed_at,
        last_observed_at=f.last_observed_at, resolved_at=f.resolved_at,
    )  # fmt: skip


@strawberry.type
class AuditEntry:
    ts: datetime
    actor: str
    action: str


@strawberry.type
class Device:
    id: int
    name: str
    hostname: str | None
    asset_type: str
    group_name: str | None
    tags: list[str]
    vendor: str | None
    confidence: str
    confidence_reason: str | None
    mac_randomized: bool
    managed: bool
    first_seen: datetime | None
    last_seen: datetime | None
    model: strawberry.Private[Asset]

    @strawberry.field
    async def presence(self) -> Presence:
        async with session_scope() as db:
            settings = await get_runtime_settings(db)
            agent_seen = (
                await db.execute(select(func.max(DataSource.last_seen_at)).where(DataSource.asset_id == self.id))
            ).scalar()
        return Presence(**views.presence(self.model, utcnow(), settings.freshness, agent_seen))

    @strawberry.field
    async def identities(self, info: Info[Context, Any]) -> list[Identity]:
        rows = await info.context.loaders.identities.load(self.id)
        return [Identity(kind=i.kind, value=i.value.split(":", 1)[1] if i.kind == "ip_only" else i.value,
                         confidence=i.confidence, first_seen=i.first_seen, last_seen=i.last_seen) for i in rows]  # fmt: skip

    @strawberry.field
    async def addresses(self, info: Info[Context, Any]) -> list[AddressSighting]:
        rows = await info.context.loaders.addresses.load(self.id)
        return [AddressSighting(ip=o.ip, mac=o.mac or None, hostname=o.hostname, evidence=o.kind, first_seen=o.first_seen,
                                last_seen=o.last_seen, source_id=o.source_id) for o in rows]  # fmt: skip

    @strawberry.field
    async def interfaces(self, info: Info[Context, Any]) -> list[NetworkInterface]:
        rows = await info.context.loaders.interfaces.load(self.id)
        return [NetworkInterface(name=i.name, mac=i.mac, is_up=i.is_up, link_speed_mbps=i.link_speed_mbps,
                                 addresses=[a["address"] for a in i.addresses]) for i in rows]  # fmt: skip

    @strawberry.field
    async def services(self, info: Info[Context, Any]) -> list[Service]:
        rows = await info.context.loaders.services.load(self.id)
        return [Service(origin=s.origin, proto=s.proto, address=s.address, port=s.port, process=s.process,
                        container=s.container, last_seen=s.last_seen) for s in rows]  # fmt: skip

    @strawberry.field
    async def findings(self, info: Info[Context, Any], active_only: bool = True) -> list[Finding]:
        rows = await info.context.loaders.findings.load(self.id)
        return [_finding(f) for f in rows if not active_only or f.status in ACTIVE_STATUSES]

    @strawberry.field(description="Observed asset ↔ interface/subnet/service relationships (not attack paths).")
    async def relationships(self, info: Info[Context, Any]) -> list[Relationship]:
        loaders = info.context.loaders
        out: list[Relationship] = []
        for i in await loaders.interfaces.load(self.id):
            out.append(Relationship(kind="HAS_INTERFACE", target_type="interface", target_id=str(i.id),
                                    target_label=i.name, evidence="agent inventory"))  # fmt: skip
        seen: set[int] = set()
        for o in await loaders.addresses.load(self.id):
            if o.segment_id is None or o.segment_id in seen:
                continue
            seen.add(o.segment_id)
            seg = await loaders.segments.load(o.segment_id)
            if seg is not None:
                out.append(Relationship(kind="SEEN_ON", target_type="segment", target_id=str(seg.id),
                                        target_label=seg.cidr, evidence=o.kind.replace("_", " ")))  # fmt: skip
        for s in await loaders.services.load(self.id):
            out.append(Relationship(kind="LISTENS_ON", target_type="service", target_id=str(s.id),
                                    target_label=f"{s.proto}/{s.port}", evidence=s.origin.replace("_", " ")))  # fmt: skip
        return out

    @strawberry.field(permission_classes=[IsAdmin], description="Admin only: who changed this device and when.")
    async def audit_trail(self) -> list[AuditEntry]:
        async with session_scope() as db:
            rows = (
                await db.execute(
                    select(AuditEvent).where(AuditEvent.target_type == "asset", AuditEvent.target_id == str(self.id))
                    .order_by(AuditEvent.ts.desc()).limit(50)  # fmt: skip
                )
            ).scalars()
            return [AuditEntry(ts=r.ts, actor=r.actor_label, action=r.action) for r in rows]


def _device(a: Asset) -> Device:
    return Device(
        id=a.id, name=a.display_name or a.hostname or f"Device #{a.id}", hostname=a.hostname, asset_type=a.asset_type,
        group_name=a.group_name, tags=list(a.tags), vendor=a.vendor, confidence=a.confidence,
        confidence_reason=a.confidence_reason, mac_randomized=a.mac_randomized, managed=a.managed,
        first_seen=a.first_seen, last_seen=a.last_seen, model=a,
    )  # fmt: skip


@strawberry.type
class PageInfo:
    has_next_page: bool
    end_cursor: str | None


@strawberry.type
class DeviceEdge:
    cursor: str
    node: Device


@strawberry.type
class DeviceConnection:
    edges: list[DeviceEdge]
    page_info: PageInfo
    total_count: int


@strawberry.type
class FindingEdge:
    cursor: str
    node: Finding


@strawberry.type
class FindingConnection:
    edges: list[FindingEdge]
    page_info: PageInfo
    total_count: int


@strawberry.type
class CheckOutcome:
    rule_id: str
    rule_version: int
    status: str
    summary: str


@strawberry.type
class AssessmentRun:
    id: int
    asset_id: int
    trigger: str
    engine_version: str
    started_at: datetime
    finished_at: datetime | None
    results: list[CheckOutcome]


@strawberry.type
class SeverityCount:
    severity: str
    count: int


@strawberry.type
class SourceHealth:
    name: str
    kind: str
    freshness: str
    last_seen_at: datetime | None


@strawberry.type
class NetworkSummary:
    generated_at: datetime
    demo_mode: bool
    device_count: int
    managed_device_count: int
    open_findings: list[SeverityCount]
    active_alerts: int
    internet_status: str
    internet_status_reason: str
    sources: list[SourceHealth]
    inventory_coverage: str


@strawberry.input
class DeviceFilter:
    search: str | None = None
    asset_type: str | None = None
    managed: bool | None = None
    tag: str | None = None


@strawberry.input
class FindingFilter:
    status: str | None = None  # "active" (default) | a concrete status | "all"
    severity: str | None = None
    asset_id: int | None = None
    category: str | None = None


def _encode(*parts: Any) -> str:
    return base64.urlsafe_b64encode("|".join(str(p) for p in parts).encode()).decode()


def _decode(cursor: str, n: int) -> list[str]:
    try:
        parts = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
    except Exception as exc:  # noqa: BLE001
        raise ValueError("invalid cursor") from exc
    if len(parts) != n:
        raise ValueError("invalid cursor")
    return parts


def _page_size(first: int) -> int:
    if first < 1 or first > MAX_PAGE:
        raise ValueError(f"'first' must be between 1 and {MAX_PAGE}")
    return first


@strawberry.type
class Query:
    @strawberry.field(permission_classes=[IsAuthenticated], description="Keyset-paginated inventory, newest sighting first.")
    async def devices(self, filter: DeviceFilter | None = None, after: str | None = None, first: int = 25) -> DeviceConnection:  # noqa: A002
        size = _page_size(first)
        sort_key = func.coalesce(Asset.last_seen, EPOCH)
        base = select(Asset).where(Asset.merged_into_id.is_(None))
        if filter:
            if filter.asset_type:
                base = base.where(Asset.asset_type == filter.asset_type)
            if filter.managed is not None:
                base = base.where(Asset.managed.is_(filter.managed))
            if filter.tag:
                base = base.where(Asset.tags.contains([filter.tag]))
            if filter.search:
                like = f"%{filter.search.lower()[:100]}%"
                base = base.where(func.lower(func.coalesce(Asset.display_name, Asset.hostname, "")).like(like))
        stmt = base
        if after:
            ts, asset_id = _decode(after, 2)
            stmt = stmt.where(tuple_(sort_key, Asset.id) < tuple_(datetime.fromisoformat(ts), int(asset_id)))
        async with session_scope() as db:
            total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
            rows = list((await db.execute(stmt.order_by(sort_key.desc(), Asset.id.desc()).limit(size + 1))).scalars())
        page = rows[:size]
        edges = [DeviceEdge(cursor=_encode((a.last_seen or EPOCH).isoformat(), a.id), node=_device(a)) for a in page]
        return DeviceConnection(edges=edges, total_count=total,
                                page_info=PageInfo(has_next_page=len(rows) > size, end_cursor=edges[-1].cursor if edges else None))  # fmt: skip

    @strawberry.field(permission_classes=[IsAuthenticated])
    async def device(self, info: Info[Context, Any], id: int) -> Device | None:  # noqa: A002
        asset = await info.context.loaders.assets.load(id)
        return _device(asset) if asset else None

    @strawberry.field(permission_classes=[IsAuthenticated])
    async def findings(self, filter: FindingFilter | None = None, after: str | None = None, first: int = 25) -> FindingConnection:  # noqa: A002
        size = _page_size(first)
        base = select(FindingModel)
        status = (filter.status if filter else None) or "active"
        if status == "active":
            base = base.where(FindingModel.status.in_(ACTIVE_STATUSES))
        elif status != "all":
            base = base.where(FindingModel.status == status)
        if filter:
            if filter.severity:
                base = base.where(FindingModel.severity == filter.severity)
            if filter.asset_id:
                base = base.where(FindingModel.asset_id == filter.asset_id)
            if filter.category:
                base = base.where(FindingModel.category == filter.category)
        stmt = base
        if after:
            (last_id,) = _decode(after, 1)
            stmt = stmt.where(FindingModel.id < int(last_id))
        async with session_scope() as db:
            total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
            rows = list((await db.execute(stmt.order_by(FindingModel.id.desc()).limit(size + 1))).scalars())
        page = rows[:size]
        edges = [FindingEdge(cursor=_encode(f.id), node=_finding(f)) for f in page]
        return FindingConnection(edges=edges, total_count=total,
                                 page_info=PageInfo(has_next_page=len(rows) > size, end_cursor=edges[-1].cursor if edges else None))  # fmt: skip

    @strawberry.field(permission_classes=[IsAuthenticated], description="Assessment history for one asset, newest first.")
    async def assessments(self, asset_id: int, first: int = 20) -> list[AssessmentRun]:
        size = _page_size(first)
        async with session_scope() as db:
            runs = list(
                (await db.execute(select(Assessment).where(Assessment.asset_id == asset_id)
                                  .order_by(Assessment.started_at.desc()).limit(size))).scalars()  # fmt: skip
            )
            results: dict[int, list[CheckOutcome]] = defaultdict(list)
            if runs:
                for r in (await db.execute(select(CheckResult).where(CheckResult.assessment_id.in_([a.id for a in runs]))
                                           .order_by(CheckResult.rule_id))).scalars():  # fmt: skip
                    results[r.assessment_id].append(CheckOutcome(rule_id=r.rule_id, rule_version=r.rule_version,
                                                                 status=r.status, summary=r.summary))  # fmt: skip
        return [AssessmentRun(id=a.id, asset_id=a.asset_id, trigger=a.trigger, engine_version=a.engine_version,
                              started_at=a.started_at, finished_at=a.finished_at, results=results[a.id]) for a in runs]  # fmt: skip

    @strawberry.field(permission_classes=[IsAuthenticated])
    async def network_summary(self) -> NetworkSummary:
        now = utcnow()
        async with session_scope() as db:
            settings = await get_runtime_settings(db)
            devices = (await db.execute(select(func.count()).select_from(Asset).where(Asset.merged_into_id.is_(None)))).scalar_one()
            managed = (await db.execute(select(func.count()).select_from(Asset).where(
                Asset.merged_into_id.is_(None), Asset.managed.is_(True)))).scalar_one()  # fmt: skip
            sev = (await db.execute(select(FindingModel.severity, func.count()).where(
                FindingModel.status.in_(ACTIVE_STATUSES)).group_by(FindingModel.severity))).all()  # fmt: skip
            alerts = (await db.execute(select(func.count()).select_from(Alert).where(Alert.state != "resolved"))).scalar_one()
            inet = await internet_status(db, now=now)
            sources = list((await db.execute(select(DataSource).order_by(DataSource.id))).scalars())
        return NetworkSummary(
            generated_at=now, demo_mode=get_config().demo_mode, device_count=devices, managed_device_count=managed,
            open_findings=[SeverityCount(severity=s, count=n) for s, n in sev], active_alerts=alerts,
            internet_status=inet.status, internet_status_reason=inet.reason,
            sources=[SourceHealth(name=s.name, kind=s.kind, last_seen_at=s.last_seen_at,
                                  freshness=views.freshness_state(s.last_seen_at, now, settings.freshness, s.expected_interval_s))
                     for s in sources],
            inventory_coverage="partial: passive sightings and agents only",
        )  # fmt: skip


def build_schema() -> strawberry.Schema:
    cfg = get_config()
    extensions: list[Any] = [
        QueryDepthLimiter(max_depth=cfg.graphql_max_depth),
        MaxAliasesLimiter(max_alias_count=15),
        MaxTokensLimiter(max_token_count=2000),
    ]
    if not cfg.introspection_enabled:
        extensions.append(DisableIntrospection())
    return strawberry.Schema(query=Query, extensions=extensions)


def build_graphql_router() -> GraphQLRouter[Context, None]:
    cfg = get_config()
    return GraphQLRouter(
        build_schema(),
        context_getter=get_context,
        graphql_ide="graphiql" if cfg.env == "development" else None,
        allow_queries_via_get=False,
    )
