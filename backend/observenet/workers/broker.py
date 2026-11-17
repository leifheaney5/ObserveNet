"""Dramatiq broker (Redis) with async actors.

Queues: ``ingest`` (collector batches) and ``maintenance`` (rollups, retention, alerts).
Periodic messages carry a ``max_age`` so a backlog built while the worker was down is
dropped instead of replayed N times.
"""

from __future__ import annotations

import os

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import (
    AgeLimit,
    AsyncIO,
    Callbacks,
    Pipelines,
    Retries,
    ShutdownNotifications,
    TimeLimit,
)

from observenet.core.config import get_config

_MIDDLEWARE = [AgeLimit, TimeLimit, ShutdownNotifications, Callbacks, Pipelines, Retries]


class WorkerObservability(dramatiq.Middleware):
    """Per worker *process*: structured logging, tracing and the internal metrics listener.

    Runs in ``after_process_boot`` so it happens once in each forked worker process and
    never in the API (which only imports this module to enqueue messages).
    """

    def after_process_boot(self, broker: dramatiq.Broker) -> None:
        from observenet.core.logging import configure_logging
        from observenet.core.telemetry import setup_tracing, start_metrics_listener

        cfg = get_config()
        configure_logging(cfg.log_level, cfg.log_json, service="worker")
        setup_tracing("worker")
        start_metrics_listener()


def build_broker() -> dramatiq.Broker:
    cfg = get_config()
    middleware = [m() for m in _MIDDLEWARE]
    broker: dramatiq.Broker
    if cfg.env == "test" or os.environ.get("OBSERVENET_STUB_BROKER") == "1":
        broker = StubBroker(middleware=middleware)
    else:
        broker = RedisBroker(url=cfg.redis_url, middleware=middleware, namespace="observenet")
    broker.add_middleware(AsyncIO())
    broker.add_middleware(WorkerObservability())
    return broker


broker = build_broker()
dramatiq.set_broker(broker)
