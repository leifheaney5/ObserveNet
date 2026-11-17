"""ASGI application factory."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from starlette.middleware.trustedhost import TrustedHostMiddleware

from observenet import __version__
from observenet.api import routes_core, routes_inventory
from observenet.auth import service as auth_service
from observenet.core.config import get_config
from observenet.core.db import dispose_engine, get_engine, session_scope
from observenet.core.logging import configure_logging, get_logger
from observenet.core.redis import CURRENT_HASH, close_redis, get_redis
from observenet.core.telemetry import HTTP_LATENCY, instrument_app, setup_tracing, start_metrics_listener
from observenet.graphql.schema import build_graphql_router
from observenet.live.hub import hub
from observenet.models import (
    Alert,
    Asset,
    DataSource,
    Event,
    Finding,
    MetricRollup1h,
    MetricRollup1m,
    MetricSample,
    MetricSeries,
    NetworkSegment,
    ProbeTarget,
)
from observenet.security.engine import sync_check_catalog
from observenet.services.alerts import ensure_default_rules

log = get_logger("api")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}

DEFAULT_PROBE_TARGETS: list[dict[str, Any]] = [
    {"name": "Default gateway", "kind": "gateway_icmp", "scope": "gateway", "host": "auto", "port": None,
     "interval_s": 1.0, "timeout_ms": 900},
    {"name": "Cloudflare DNS (TCP 443)", "kind": "tcp", "scope": "external", "host": "1.1.1.1", "port": 443,
     "interval_s": 30.0, "timeout_ms": 3000},
    {"name": "Quad9 DNS (TCP 443)", "kind": "tcp", "scope": "external", "host": "9.9.9.9", "port": 443,
     "interval_s": 30.0, "timeout_ms": 3000},
    {"name": "DNS resolution (example.com)", "kind": "dns", "scope": "external", "host": "example.com", "port": None,
     "interval_s": 30.0, "timeout_ms": 3000},
]  # fmt: skip


async def purge_demo_data() -> int:
    """Real mode never shows synthetic rows: anything created by DEMO_MODE is removed at startup."""
    async with session_scope() as session:
        demo_sources = list(
            (await session.execute(select(DataSource.id).where(DataSource.is_demo.is_(True)))).scalars()
        )
        removed = 0
        if demo_sources:
            # metric_samples / rollups have no foreign key (partitioned fact tables), so remove
            # the synthetic points explicitly before their series rows cascade away.
            demo_series = select(MetricSeries.id).where(MetricSeries.source_id.in_(demo_sources))
            for fact in (MetricSample, MetricRollup1m, MetricRollup1h):
                await session.execute(delete(fact).where(fact.series_id.in_(demo_series)))
        for model in (Alert, Event, Finding, Asset, NetworkSegment, ProbeTarget):
            removed += (await session.execute(delete(model).where(model.is_demo.is_(True)))).rowcount or 0  # type: ignore[attr-defined]
        if demo_sources:
            removed += (
                await session.execute(delete(DataSource).where(DataSource.id.in_(demo_sources)))
            ).rowcount or 0  # type: ignore[attr-defined]
    if removed:
        try:
            redis = get_redis()
            stale = [
                str(k)
                for k in await redis.hkeys(CURRENT_HASH)
                if int(str(k).split("|", 1)[0]) in demo_sources
            ]
            if stale:
                await redis.hdel(CURRENT_HASH, *stale)
        except Exception:  # noqa: BLE001, S110
            pass
    return removed


async def bootstrap() -> None:
    cfg = get_config()
    async with session_scope() as session:
        await sync_check_catalog(session)
        await ensure_default_rules(session)
        outcome = await auth_service.bootstrap_admin(session)
        await auth_service.ensure_bootstrap_agent_token(session)
        if not cfg.demo_mode and (await session.execute(select(ProbeTarget.id).limit(1))).first() is None:
            for spec in DEFAULT_PROBE_TARGETS:
                session.add(ProbeTarget(**spec))
    if outcome == "created":
        log.info("admin_bootstrapped", username=cfg.admin_username)
    elif outcome == "missing_password":
        log.warning(
            "no_admin_user",
            hint="set OBSERVENET_ADMIN_PASSWORD or run `python -m observenet.cli create-admin`",
        )
    elif outcome == "weak_password":
        log.error(
            "admin_password_rejected",
            hint="OBSERVENET_ADMIN_PASSWORD must be 12+ characters and not a placeholder",
        )
    if not cfg.demo_mode:
        removed = await purge_demo_data()
        if removed:
            log.warning("demo_data_purged", rows=removed)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = get_config()
    if cfg.env == "production" and len(cfg.secret_key) < 32:
        raise RuntimeError(
            "OBSERVENET_SECRET_KEY must be set to at least 32 random characters (run `make init`)"
        )
    start_metrics_listener()
    await bootstrap()
    await hub.start()
    log.info("api_started", version=__version__, demo_mode=cfg.demo_mode, env=cfg.env)
    yield
    await hub.stop()
    await close_redis()
    await dispose_engine()


def create_app() -> FastAPI:
    cfg = get_config()
    configure_logging(cfg.log_level, cfg.log_json, service="api")
    setup_tracing("api")
    docs = cfg.env == "development"
    app = FastAPI(
        title="ObserveNet API", version=__version__, lifespan=lifespan,
        docs_url="/api/docs" if docs else None, redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )  # fmt: skip

    @app.middleware("http")
    async def security_and_metrics(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        HTTP_LATENCY.labels(
            method=request.method, route=getattr(route, "path", "unmatched"), status=str(response.status_code)
        ).observe(time.perf_counter() - start)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if cfg.cookie_secure_effective:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith(("/api/", "/graphql")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo the rejected input back: it may hold a password or token, and non-finite
        # floats (NaN/Infinity) in it would make the default handler fail with a 500.
        detail = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": detail})

    # No CORS middleware on purpose: the SPA is same-origin, so cross-origin browser reads are refused.
    hosts = cfg.allowed_host_list
    if cfg.env != "test" and "*" not in hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    app.include_router(routes_core.router)
    app.include_router(routes_inventory.router)
    app.include_router(build_graphql_router(), prefix="/graphql", include_in_schema=False)
    if cfg.demo_mode:
        from observenet.api import routes_demo

        app.include_router(routes_demo.router)
    instrument_app(app, get_engine())
    return app
