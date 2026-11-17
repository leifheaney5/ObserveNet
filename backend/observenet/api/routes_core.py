"""Health, auth, sources/tokens, ingest, metrics, stream."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from observenet import __version__
from observenet.api import schemas as S
from observenet.auth import ratelimit, service
from observenet.auth.deps import AdminUser, AgentToken, CurrentUser, DbSession, client_ip
from observenet.core.config import get_config
from observenet.core.db import session_scope
from observenet.core.redis import INGEST_QUEUE, MAINT_QUEUE, WORKER_HEARTBEAT_KEY, get_redis
from observenet.core.telemetry import INGEST_BATCHES
from observenet.core.time import ensure_utc, utcnow
from observenet.live.hub import Client, hub
from observenet.models import ApiToken, DataSource, MetricSeries, ProbeTarget, Session, User
from observenet.providers.base import PLANNED_ADAPTERS, NullRouterProvider
from observenet.schemas.ingest import AgentConfig, AgentProbeConfig, IngestAccepted, IngestPayload
from observenet.services import ingest as ingest_service
from observenet.services import timeseries, views
from observenet.services.events import add_audit
from observenet.services.settings import get_runtime_settings
from observenet.workers import tasks

router = APIRouter(prefix="/api/v1")


# ------------------------------------------------------------------ health


@router.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness: the process is up. Touches no dependency."""
    return {"status": "ok"}


@router.get("/ready", tags=["health"])
async def ready(response: Response) -> dict[str, Any]:
    """Readiness: PostgreSQL reachable + migrated, Redis reachable."""
    checks: dict[str, str] = {}
    try:
        async with session_scope() as session:
            rev = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
            checks["database"] = "ok" if rev else "not migrated"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ok else "not_ready", "checks": checks}


@router.get("/meta", tags=["health"], response_model=S.Meta)
async def meta(db: DbSession) -> S.Meta:
    """The only unauthenticated product endpoint: what the login page needs, nothing more."""
    cfg = get_config()
    setup = await service.user_count(db) == 0
    return S.Meta(
        version=__version__, demo_mode=cfg.demo_mode, setup_required=setup,
        setup_hint="No administrator exists. Set OBSERVENET_ADMIN_PASSWORD (12+ characters) and restart the API, "
                   "or run: docker compose exec api python -m observenet.cli create-admin" if setup else None,
    )  # fmt: skip


@router.get("/status", tags=["health"], response_model=S.StatusOut)
async def service_status(_: CurrentUser, db: DbSession) -> S.StatusOut:
    now = utcnow()
    components: list[dict[str, Any]] = [{"name": "API", "state": "ok", "detail": f"version {__version__}"}]
    try:
        await db.execute(text("SELECT 1"))
        components.append({"name": "PostgreSQL", "state": "ok", "detail": "reachable"})
    except Exception as exc:  # noqa: BLE001
        components.append({"name": "PostgreSQL", "state": "down", "detail": type(exc).__name__})
    queues: dict[str, int | None] = {INGEST_QUEUE: None, MAINT_QUEUE: None}
    try:
        redis = get_redis()
        await redis.ping()
        components.append({"name": "Redis", "state": "ok", "detail": "reachable"})
        for q in queues:
            queues[q] = int(await redis.llen(f"observenet:{q}"))
        for name, label, limit in (("worker", "Worker", 45), ("scheduler", "Scheduler", 15)):
            raw = await redis.get(WORKER_HEARTBEAT_KEY.format(name=name))
            if raw is None:
                components.append({"name": label, "state": "down", "detail": "no heartbeat"})
            else:
                age = (now - datetime.fromisoformat(str(raw))).total_seconds()
                components.append({"name": label, "state": "ok" if age < limit else "degraded",
                                   "detail": f"heartbeat {int(age)} s ago"})  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        components.append({"name": "Redis", "state": "down", "detail": type(exc).__name__})
    components.append({"name": "Live stream", "state": "ok" if hub.connected_to_redis else "degraded",
                       "detail": f"{len(hub.clients)} client(s)"})  # fmt: skip
    backlog = queues.get(INGEST_QUEUE)
    if backlog is not None and backlog > 100:
        components.append(
            {"name": "Ingest queue", "state": "degraded", "detail": f"{backlog} batches waiting"}
        )
    return S.StatusOut(version=__version__, demo_mode=get_config().demo_mode, components=components,
                       queues=queues, ws_clients=len(hub.clients))  # fmt: skip


@router.get("/capabilities", tags=["sources"], response_model=S.CapabilitiesOut)
async def capabilities(_: CurrentUser, db: DbSession) -> S.CapabilitiesOut:
    now = utcnow()
    has_import = (
        await db.execute(
            select(func.count()).select_from(DataSource).where(DataSource.kind == "client_import")
        )
    ).scalar_one()
    provider = NullRouterProvider()
    return S.CapabilitiesOut(
        generated_at=now, demo_mode=get_config().demo_mode, network=await views.network_capabilities(db, now),
        router_provider={
            "configured": "client-list-import" if has_import else "none",
            "capabilities": [
                {"capability": c.capability.value,
                 "status": "supported" if has_import and c.capability.value == "device_inventory" else c.status,
                 "detail": "Point-in-time import of the router's client list." if has_import
                 and c.capability.value == "device_inventory" else c.detail}
                for c in provider.capabilities()
            ],
            "planned_adapters": [
                {"key": p.key, "title": p.title, "would_provide": [c.value for c in p.would_provide],
                 "status": "not_implemented", "blocked_on": p.blocked_on} for p in PLANNED_ADAPTERS
            ],
        },
    )  # fmt: skip


# ------------------------------------------------------------------ auth


def _set_cookies(response: Response, raw: str, csrf: str) -> None:
    cfg = get_config()
    max_age = cfg.session_ttl_hours * 3600
    response.set_cookie(service.SESSION_COOKIE, raw, max_age=max_age, httponly=True, secure=cfg.cookie_secure_effective,
                        samesite="lax", path="/")  # fmt: skip
    response.set_cookie(service.CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=cfg.cookie_secure_effective,
                        samesite="lax", path="/")  # fmt: skip


@router.post("/auth/login", tags=["auth"], response_model=S.MeOut)
async def login(body: S.LoginRequest, request: Request, response: Response, db: DbSession) -> S.MeOut:
    cfg = get_config()
    ip = client_ip(request) or "unknown"
    if not await ratelimit.hit(f"login:{ip}", limit=cfg.login_rate_limit_per_minute):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many login attempts; wait a minute")
    user = await service.authenticate(db, body.username, body.password)
    if user is None:
        add_audit(db, "auth.login_failed", actor_label=body.username[:64], ip=ip)
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid username or password")
    raw, sess = await service.create_session(db, user, ip=ip, user_agent=request.headers.get("user-agent"))
    add_audit(db, "auth.login", actor_user_id=user.id, actor_label=user.username, ip=ip)
    _set_cookies(response, raw, sess.csrf_token)
    return S.MeOut(user=S.UserOut.model_validate(user), csrf_token=sess.csrf_token, demo_mode=cfg.demo_mode)


@router.post("/auth/logout", tags=["auth"], status_code=204)
async def logout(principal: CurrentUser, response: Response, db: DbSession) -> None:
    sess = await db.get(Session, principal.session_id)
    if sess is not None:
        sess.revoked_at = utcnow()
    add_audit(db, "auth.logout", actor_user_id=principal.user.id, actor_label=principal.user.username)
    response.delete_cookie(service.SESSION_COOKIE, path="/")
    response.delete_cookie(service.CSRF_COOKIE, path="/")


@router.get("/auth/me", tags=["auth"], response_model=S.MeOut)
async def me(principal: CurrentUser) -> S.MeOut:
    return S.MeOut(user=S.UserOut.model_validate(principal.user), csrf_token=principal.csrf_token,
                   demo_mode=get_config().demo_mode)  # fmt: skip


@router.post("/auth/password", tags=["auth"], status_code=204)
async def change_password(body: S.PasswordChange, principal: CurrentUser, db: DbSession) -> None:
    from observenet.auth.crypto import hash_password, verify_password

    if not verify_password(body.current_password, principal.user.password_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "current password is incorrect")
    try:
        service.validate_password_strength(body.new_password)
    except service.AuthError as exc:
        raise HTTPException(422, str(exc)) from exc
    principal.user.password_hash = hash_password(body.new_password)
    # Changing the password signs out every other session.
    for sess in (await db.execute(select(Session).where(Session.user_id == principal.user.id, Session.id != principal.session_id,
                                                        Session.revoked_at.is_(None)))).scalars():  # fmt: skip
        sess.revoked_at = utcnow()
    add_audit(
        db, "auth.password_changed", actor_user_id=principal.user.id, actor_label=principal.user.username
    )


@router.get("/users", tags=["auth"], response_model=list[S.UserOut])
async def list_users(_: AdminUser, db: DbSession) -> list[User]:
    return list((await db.execute(select(User).order_by(User.id))).scalars())


@router.post("/users", tags=["auth"], response_model=S.UserOut, status_code=201)
async def create_user(body: S.UserCreate, admin: AdminUser, db: DbSession) -> User:
    try:
        user = await service.create_user(db, username=body.username, password=body.password, role=body.role)
    except service.AuthError as exc:
        raise HTTPException(422, str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(409, "username already exists") from exc
    add_audit(db, "user.create", actor_user_id=admin.user.id, actor_label=admin.user.username, target_type="user",
              target_id=user.id, details={"role": body.role})  # fmt: skip
    return user


@router.patch("/users/{user_id}", tags=["auth"], response_model=S.UserOut)
async def patch_user(user_id: int, body: S.UserPatch, admin: AdminUser, db: DbSession) -> User:
    from observenet.auth.crypto import hash_password

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    demoting = (body.role == "viewer" or body.disabled) and user.role == "admin"
    if demoting:
        admins = (
            await db.execute(
                select(func.count()).select_from(User).where(User.role == "admin", User.disabled.is_(False))
            )
        ).scalar_one()
        if admins <= 1:
            raise HTTPException(409, "cannot remove the last active administrator")
    if body.role is not None:
        user.role = body.role
    if body.disabled is not None:
        user.disabled = body.disabled
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.disabled or body.password is not None:
        for sess in (
            await db.execute(select(Session).where(Session.user_id == user.id, Session.revoked_at.is_(None)))
        ).scalars():
            sess.revoked_at = utcnow()
    add_audit(db, "user.update", actor_user_id=admin.user.id, actor_label=admin.user.username, target_type="user",
              target_id=user.id, details=body.model_dump(exclude={"password"}, exclude_none=True))  # fmt: skip
    return user


# ------------------------------------------------------------------ sources + tokens


def _source_out(s: DataSource, now: datetime, fresh: Any) -> S.SourceOut:
    age = (now - s.last_seen_at).total_seconds() if s.last_seen_at else None
    return S.SourceOut(
        id=s.id, kind=s.kind, name=s.name, platform=s.platform, version=s.version, asset_id=s.asset_id,
        observation_point=s.observation_point, enabled=s.enabled, is_demo=s.is_demo, created_at=s.created_at,
        last_seen_at=s.last_seen_at, last_error=s.last_error, age_s=age,
        freshness=views.freshness_state(s.last_seen_at, now, fresh, s.expected_interval_s),
        capabilities=[S.CapabilityOut.model_validate(c) for c in sorted(s.capabilities, key=lambda c: c.name)],
    )  # fmt: skip


@router.get("/sources", tags=["sources"], response_model=list[S.SourceOut])
async def list_sources(_: CurrentUser, db: DbSession) -> list[S.SourceOut]:
    settings = await get_runtime_settings(db)
    now = utcnow()
    rows = (await db.execute(select(DataSource).order_by(DataSource.id))).scalars()
    return [_source_out(s, now, settings.freshness) for s in rows]


@router.patch("/sources/{source_id}", tags=["sources"], response_model=S.SourceOut)
async def patch_source(source_id: int, body: S.SourcePatch, admin: AdminUser, db: DbSession) -> S.SourceOut:
    source = await db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(404, "source not found")
    if body.name is not None:
        source.name = body.name
    if body.enabled is not None:
        source.enabled = body.enabled
    add_audit(db, "source.update", actor_user_id=admin.user.id, actor_label=admin.user.username, target_type="source",
              target_id=source.id, details=body.model_dump(exclude_none=True))  # fmt: skip
    settings = await get_runtime_settings(db)
    return _source_out(source, utcnow(), settings.freshness)


@router.post("/sources/{source_id}/test", tags=["sources"], response_model=S.SourceTestOut)
async def test_source(source_id: int, admin: AdminUser, db: DbSession) -> S.SourceTestOut:
    """Non-invasive source test: evaluates what the backend already knows and, for agents,
    asks for a fresh posture snapshot on the agent's next config poll. Sends no probe itself."""
    source = await db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(404, "source not found")
    settings = await get_runtime_settings(db)
    now = utcnow()
    state = views.freshness_state(source.last_seen_at, now, settings.freshness, source.expected_interval_s)
    checks: list[dict[str, Any]] = [
        {"name": "enabled", "ok": source.enabled, "detail": "Source is enabled." if source.enabled else "Source is disabled."},
        {"name": "recent_data", "ok": state == "live",
         "detail": f"Last data {int((now - source.last_seen_at).total_seconds())} s ago." if source.last_seen_at
         else "This source has never reported."},
        {"name": "collector_errors", "ok": not source.last_error, "detail": source.last_error or "No collector errors reported."},
    ]  # fmt: skip
    series = (await db.execute(select(func.count()).select_from(MetricSeries).where(
        MetricSeries.source_id == source.id, MetricSeries.last_ts > now - timedelta(minutes=2)))).scalar_one()  # fmt: skip
    if source.kind in ("linux_agent", "windows_agent", "demo"):
        checks.append(
            {
                "name": "active_series",
                "ok": series > 0,
                "detail": f"{series} metric series updated in the last 2 min.",
            }
        )
        source.posture_requested_at = now
        note = "A fresh posture snapshot was requested; it arrives on the agent's next config poll (≤ 10 s)."
    else:
        note = "Imported sources are point-in-time; re-import to refresh."
    add_audit(db, "source.test", actor_user_id=admin.user.id, actor_label=admin.user.username, target_type="source",
              target_id=source.id)  # fmt: skip
    return S.SourceTestOut(
        source_id=source.id, ok=all(c["ok"] for c in checks), freshness=state, checks=checks, note=note
    )


@router.get("/tokens", tags=["sources"], response_model=list[S.TokenOut])
async def list_tokens(_: AdminUser, db: DbSession) -> list[ApiToken]:
    return list((await db.execute(select(ApiToken).order_by(ApiToken.id.desc()))).scalars())


@router.post("/tokens", tags=["sources"], response_model=S.TokenCreated, status_code=201)
async def create_token(body: S.TokenCreate, admin: AdminUser, db: DbSession) -> S.TokenCreated:
    ttl = body.ttl_days or get_config().agent_token_ttl_days
    raw, row = await service.issue_agent_token(db, name=body.name, created_by=admin.user.id, ttl_days=ttl)
    add_audit(db, "token.create", actor_user_id=admin.user.id, actor_label=admin.user.username, target_type="token",
              target_id=row.id, details={"name": body.name, "ttl_days": ttl})  # fmt: skip
    return S.TokenCreated(token=raw, info=S.TokenOut.model_validate(row))


@router.delete("/tokens/{token_id}", tags=["sources"], status_code=204)
async def revoke_token(token_id: int, admin: AdminUser, db: DbSession) -> None:
    token = await db.get(ApiToken, token_id)
    if token is None:
        raise HTTPException(404, "token not found")
    if token.revoked_at is None:
        token.revoked_at = utcnow()
    add_audit(db, "token.revoke", actor_user_id=admin.user.id, actor_label=admin.user.username, target_type="token",
              target_id=token.id)  # fmt: skip


# ------------------------------------------------------------------ ingest


async def _enqueue(source_id: int, payload: dict[str, Any], received_at: str) -> None:
    await asyncio.to_thread(tasks.process_ingest.send, source_id, payload, received_at)


@router.post("/ingest/agent", tags=["ingest"], response_model=IngestAccepted, status_code=202)
async def ingest_agent(payload: IngestPayload, token: AgentToken, db: DbSession) -> IngestAccepted:
    """Authenticated collector data. Validates, fans out live, and queues durable processing."""
    cfg = get_config()
    if not await ratelimit.hit(f"ingest:{token.id}", limit=cfg.ingest_rate_limit_per_minute):
        INGEST_BATCHES.labels(outcome="rate_limited").inc()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "ingest rate limit exceeded")
    try:
        accepted = await ingest_service.accept(
            db, get_redis(), token=token, payload=payload, enqueue=_enqueue
        )
    except ingest_service.IngestRejected as exc:
        INGEST_BATCHES.labels(outcome=f"rejected_{exc.status_code}").inc()
        raise HTTPException(exc.status_code, exc.detail) from exc
    INGEST_BATCHES.labels(outcome="accepted").inc()
    return accepted


@router.get("/agent/config", tags=["ingest"], response_model=AgentConfig)
async def agent_config(token: AgentToken, db: DbSession) -> AgentConfig:
    """What the calling agent should collect. Targets were validated against the network scope."""
    settings = await get_runtime_settings(db)
    source = await db.get(DataSource, token.source_id) if token.source_id else None
    targets = list(
        (
            await db.execute(
                select(ProbeTarget)
                .where(ProbeTarget.enabled.is_(True), ProbeTarget.is_demo.is_(False))
                .order_by(ProbeTarget.id)
            )
        ).scalars()  # fmt: skip
    )
    probes = [AgentProbeConfig(target_id=t.id, kind=t.kind, host=t.host, port=t.port, interval_s=t.interval_s,
                               timeout_ms=t.timeout_ms) for t in targets]  # fmt: skip
    import hashlib

    blob = settings.model_dump_json() + "|".join(f"{t.id}:{t.host}:{t.port}:{t.interval_s}" for t in targets)
    version = hashlib.sha256(blob.encode()).hexdigest()[:12]
    return AgentConfig(
        config_version=version, sample_interval_s=settings.collection.sample_interval_s,
        inventory_interval_s=settings.collection.inventory_interval_s,
        posture_interval_s=settings.collection.posture_interval_s,
        posture_requested_at=source.posture_requested_at if source else None, probes=probes,
        discovery=settings.discovery.model_dump(), speedtest=settings.speedtest.model_dump(),
    )  # fmt: skip


# ------------------------------------------------------------------ metrics


@router.get("/metrics/series", tags=["metrics"], response_model=list[S.SeriesOut])
async def list_series(_: CurrentUser, db: DbSession, metric: str | None = None, source_id: int | None = None,
                      asset_id: int | None = None) -> list[MetricSeries]:  # fmt: skip
    stmt = select(MetricSeries).order_by(MetricSeries.metric, MetricSeries.label)
    if metric:
        stmt = stmt.where(MetricSeries.metric == metric)
    if source_id:
        stmt = stmt.where(MetricSeries.source_id == source_id)
    if asset_id:
        stmt = stmt.where(MetricSeries.asset_id == asset_id)
    return list((await db.execute(stmt)).scalars())


@router.get("/metrics/current", tags=["metrics"], response_model=list[S.CurrentValue])
async def metrics_current(_: CurrentUser, db: DbSession, metric: str | None = None) -> list[S.CurrentValue]:
    """Latest value per series from the live cache, each with timestamp, age and freshness."""
    settings = await get_runtime_settings(db)
    now = utcnow()
    out = []
    for key, cur in (await views.current_values()).items():
        sid, m, label = key.split("|", 2)
        if metric and m != metric:
            continue
        ts = datetime.fromisoformat(cur["t"])
        out.append(S.CurrentValue(key=key, source_id=int(sid), metric=m, label=label, value=cur["v"], unit=cur["u"],
                                  quality=cur.get("q", "measured"), ts=ts, age_s=(now - ts).total_seconds(),
                                  freshness=views.freshness_state(ts, now, settings.freshness)))  # fmt: skip
    return sorted(out, key=lambda c: c.key)


@router.get("/metrics/history", tags=["metrics"], response_model=S.HistoryOut)
async def metrics_history(
    _: CurrentUser,
    db: DbSession,
    metric: Annotated[str, Query(max_length=64)],
    start: Annotated[datetime, Query(alias="from")],
    end: Annotated[datetime, Query(alias="to")],
    bucket: Annotated[int | None, Query(description="Bucket width in seconds; omitted = auto")] = None,
    source_id: int | None = None,
    label: Annotated[str | None, Query(max_length=120)] = None,
    asset_id: int | None = None,
) -> S.HistoryOut:
    """Time-bucketed history. Buckets < 60 s read raw samples (48 h retention); 1 min–1 h
    read minute rollups; ≥ 1 h read hourly rollups. Empty buckets are null, not zero."""
    start, end = ensure_utc(start), ensure_utc(end)
    stmt = select(MetricSeries).where(MetricSeries.metric == metric).order_by(MetricSeries.id).limit(24)
    if source_id is not None:
        stmt = stmt.where(MetricSeries.source_id == source_id)
    if label is not None:
        stmt = stmt.where(MetricSeries.label == label)
    if asset_id is not None:
        stmt = stmt.where(MetricSeries.asset_id == asset_id)
    series = list((await db.execute(stmt)).scalars())
    try:
        res, points = await timeseries.query_history(
            db, series_ids=[s.id for s in series], start=start, end=end, bucket_s=bucket
        )
    except timeseries.HistoryQueryError as exc:
        raise HTTPException(422, str(exc)) from exc
    settings = await get_runtime_settings(db)
    note = None
    if res.name == "raw" and start < utcnow() - timedelta(hours=settings.retention.raw_hours):
        note = f"Raw samples are kept for {settings.retention.raw_hours} h; older parts of this range are empty at this resolution."
    if not series:
        note = "No series matches this metric/filter. Nothing has been measured for it."
    return S.HistoryOut(
        metric=metric, start=start, end=end, bucket_s=res.bucket_s, resolution=res.name,
        series=[S.HistorySeries(series=S.SeriesOut.model_validate(s), points=[S.HistoryPoint(**p) for p in points[s.id]])
                for s in series], note=note,
    )  # fmt: skip


# ------------------------------------------------------------------ live stream


def _origin_allowed(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return False  # browsers always send Origin on WebSocket handshakes
    cfg = get_config()
    host = urlsplit(origin).netloc
    return origin.rstrip("/") == cfg.public_origin or host == ws.headers.get("host")


@router.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    """Live metrics/alerts at ~1 Hz. Auth = the same session cookie as the REST API."""
    if not _origin_allowed(ws):
        await ws.close(code=4403, reason="origin not allowed")
        return
    async with session_scope() as db:
        resolved = await service.resolve_session(db, ws.cookies.get(service.SESSION_COOKIE))
    if resolved is None:
        await ws.close(code=4401, reason="authentication required")
        return
    await ws.accept()
    client = Client(ws, resolved[1].id)
    await ws.send_json({"type": "hello", "ts": utcnow().isoformat(), "tick_s": 1, "demo_mode": get_config().demo_mode,
                        "snapshot": await hub.snapshot()})  # fmt: skip

    async def watch_session() -> None:
        while True:
            await asyncio.sleep(60)
            async with session_scope() as db:
                if await service.resolve_session(db, ws.cookies.get(service.SESSION_COOKIE)) is None:
                    client.dropped = True
                    return

    async def drain_incoming() -> None:
        # Clients only send pings; reading also lets us notice a disconnect promptly.
        while True:
            await ws.receive_text()

    watcher = asyncio.create_task(watch_session())
    reader = asyncio.create_task(drain_incoming())
    server = asyncio.create_task(hub.serve(client))
    try:
        await asyncio.wait({reader, server, watcher}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (watcher, reader, server):
            t.cancel()
        with contextlib.suppress(Exception):
            await ws.close(
                code=4408 if client.dropped else 1000, reason="slow consumer" if client.dropped else ""
            )
    _ = WebSocketDisconnect
