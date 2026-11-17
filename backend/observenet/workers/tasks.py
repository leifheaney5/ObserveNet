"""Background actors. Every actor is bounded (time limit), retryable and idempotent."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import wraps
from typing import Any, ParamSpec, TypeVar

import dramatiq
import httpx
from sqlalchemy import select

import observenet.workers.broker  # noqa: F401 - configures the global broker
from observenet.core.config import get_config
from observenet.core.db import session_scope
from observenet.core.logging import get_logger
from observenet.core.redis import INGEST_QUEUE, MAINT_QUEUE, WORKER_HEARTBEAT_KEY, get_redis
from observenet.core.telemetry import INGEST_BATCHES, TASK_DURATION, TASK_RUNS, tracer
from observenet.core.time import utcnow
from observenet.models import Alert, Outage
from observenet.schemas.ingest import IngestPayload
from observenet.security import engine
from observenet.services import alerts as alert_service
from observenet.services import ingest as ingest_service
from observenet.services import timeseries
from observenet.services.connectivity import internet_status
from observenet.services.settings import get_runtime_settings

log = get_logger("worker")
P = ParamSpec("P")
R = TypeVar("R")


def instrumented(name: str) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    def deco(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start = time.perf_counter()
            with tracer.start_as_current_span(f"task.{name}"):
                try:
                    result = await fn(*args, **kwargs)
                except Exception:
                    TASK_RUNS.labels(task=name, outcome="error").inc()
                    log.exception("task_failed", task=name)
                    raise
            TASK_RUNS.labels(task=name, outcome="ok").inc()
            TASK_DURATION.labels(task=name).observe(time.perf_counter() - start)
            return result

        return wrapper

    return deco


@dramatiq.actor(
    queue_name=INGEST_QUEUE, max_retries=8, min_backoff=500, max_backoff=30_000, time_limit=30_000
)
@instrumented("process_ingest")
async def process_ingest(source_id: int, payload: dict[str, Any], received_at: str) -> None:
    parsed = IngestPayload.model_validate(payload)
    async with session_scope() as session:
        result = await ingest_service.process_batch(
            session, source_id=source_id, payload=parsed, received_at=datetime.fromisoformat(received_at)
        )
    INGEST_BATCHES.labels(outcome="duplicate" if result.get("duplicate") else "processed").inc()


@dramatiq.actor(queue_name=MAINT_QUEUE, max_retries=2, time_limit=120_000, max_age=120_000)
@instrumented("rollups")
async def run_rollups() -> None:
    async with session_scope() as session:
        stats = await timeseries.run_rollups(session, now=utcnow())
    log.debug("rollups_done", **stats)


@dramatiq.actor(queue_name=MAINT_QUEUE, max_retries=2, time_limit=300_000, max_age=600_000)
@instrumented("retention")
async def run_retention() -> None:
    now = utcnow()
    async with session_scope() as session:
        created = await timeseries.ensure_partitions(session, now=now)
    async with session_scope() as session:
        settings = await get_runtime_settings(session)
        stats = await timeseries.apply_retention(session, now=now, retention=settings.retention)
    log.info("retention_done", partitions_created=created, **stats)


@dramatiq.actor(queue_name=MAINT_QUEUE, max_retries=0, time_limit=30_000, max_age=20_000)
@instrumented("evaluate")
async def evaluate() -> None:
    """Alert rules, verification deadlines, agent staleness, outage tracking."""
    now = utcnow()
    await get_redis().set(WORKER_HEARTBEAT_KEY.format(name="worker"), now.isoformat(), ex=90)
    async with session_scope() as session:
        settings = await get_runtime_settings(session)
        await engine.expire_verification_runs(session, now)
        await engine.evaluate_agent_staleness(
            session, disconnected_after_s=settings.freshness.disconnected_after_s, now=now
        )
    async with session_scope() as session:
        await alert_service.evaluate_rules(session, now=now)
    async with session_scope() as session:
        status = await internet_status(session, now=now)
        open_outage = (
            (
                await session.execute(
                    select(Outage).where(Outage.ended_at.is_(None)).order_by(Outage.started_at.desc())
                )
            )
            .scalars()
            .first()
        )
        if status.status == "unreachable" and open_outage is None:
            session.add(Outage(kind="internet", started_at=now, evidence={"reason": status.reason,
                                                                          "signals": status.signals_total}))  # fmt: skip
        elif status.status in ("reachable", "degraded") and status.signals_ok > 0 and open_outage is not None:
            open_outage.ended_at = now
            open_outage.evidence = {**open_outage.evidence, "recovery": status.reason}


@dramatiq.actor(queue_name=MAINT_QUEUE, max_retries=3, min_backoff=5_000, time_limit=20_000)
@instrumented("webhook")
async def send_webhook(alert_id: int, event: str) -> None:
    """Optional outbound notification. Only runs when the operator configured a URL."""
    cfg = get_config()
    if not cfg.alert_webhook_url:
        return
    async with session_scope() as session:
        alert = await session.get(Alert, alert_id)
        if alert is None:
            return
        body = json.dumps({"event": event, "id": alert.id, "kind": alert.kind, "severity": alert.severity,
                           "title": alert.title, "summary": alert.summary, "state": alert.state,
                           "started_at": alert.started_at.isoformat()}).encode()  # fmt: skip
    headers = {"Content-Type": "application/json", "User-Agent": "ObserveNet"}
    if cfg.alert_webhook_secret:
        headers["X-ObserveNet-Signature"] = hmac.new(
            cfg.alert_webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        (await client.post(cfg.alert_webhook_url, content=body, headers=headers)).raise_for_status()


@dramatiq.actor(queue_name=MAINT_QUEUE, max_retries=1, time_limit=120_000, max_age=300_000)
@instrumented("graph_sync")
async def sync_graph() -> None:
    if not get_config().neo4j_uri:
        return
    from observenet.services.graph_sync import sync_topology_to_neo4j

    async with session_scope() as session:
        stats = await sync_topology_to_neo4j(session)
    log.info("graph_sync_done", **stats)
