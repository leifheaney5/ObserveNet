"""Tiny periodic scheduler: enqueues idempotent maintenance actors on fixed intervals.

Kept separate from the worker so task execution can scale horizontally while exactly one
scheduler ticks. Duplicate ticks are harmless (all periodic actors are idempotent).
"""

from __future__ import annotations

import asyncio
import signal
import time

from observenet.core.config import get_config
from observenet.core.db import dispose_engine, session_scope
from observenet.core.logging import configure_logging, get_logger
from observenet.core.redis import INGEST_QUEUE, MAINT_QUEUE, WORKER_HEARTBEAT_KEY, close_redis, get_redis
from observenet.core.telemetry import DATA_FRESHNESS, QUEUE_DEPTH, setup_tracing, start_metrics_listener
from observenet.core.time import utcnow
from observenet.workers import tasks

log = get_logger("scheduler")

SCHEDULE: list[tuple[float, str]] = [
    (10.0, "evaluate"),
    (60.0, "run_rollups"),
    (900.0, "run_retention"),
    (300.0, "sync_graph"),
]


async def _observe_queues() -> None:
    from sqlalchemy import select

    from observenet.models import DataSource

    redis = get_redis()
    for queue in (INGEST_QUEUE, MAINT_QUEUE):
        QUEUE_DEPTH.labels(queue=queue).set(await redis.llen(f"observenet:{queue}"))
    now = utcnow()
    async with session_scope() as session:
        for name, last in (await session.execute(select(DataSource.name, DataSource.last_seen_at))).all():
            if last is not None:
                DATA_FRESHNESS.labels(source=name).set((now - last).total_seconds())


async def run() -> None:
    cfg = get_config()
    configure_logging(cfg.log_level, cfg.log_json, service="scheduler")
    setup_tracing("scheduler")
    start_metrics_listener()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    demo_task: asyncio.Task[None] | None = None
    if cfg.demo_mode:
        from observenet.services.demo import run_demo_generator

        demo_task = asyncio.create_task(run_demo_generator(stop))

    tasks.run_retention.send()  # creates today's partitions right away
    next_due = {name: time.monotonic() + (5.0 if name != "evaluate" else 1.0) for _, name in SCHEDULE}
    log.info("scheduler_started", demo_mode=cfg.demo_mode)
    while not stop.is_set():
        now = time.monotonic()
        for interval, name in SCHEDULE:
            if now >= next_due[name]:
                next_due[name] = now + interval
                try:
                    await asyncio.to_thread(getattr(tasks, name).send)
                except Exception:  # noqa: BLE001 - Redis down: keep ticking, retry next interval
                    log.warning("enqueue_failed", task=name)
        try:
            await get_redis().set(WORKER_HEARTBEAT_KEY.format(name="scheduler"), utcnow().isoformat(), ex=60)
            await _observe_queues()
        except Exception:  # noqa: BLE001
            log.warning("scheduler_housekeeping_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except TimeoutError:
            pass
    if demo_task is not None:
        await demo_task
    await close_redis()
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(run())
