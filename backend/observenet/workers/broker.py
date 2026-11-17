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
from dramatiq.middleware import AgeLimit, AsyncIO, Callbacks, Pipelines, Retries, ShutdownNotifications, TimeLimit

from observenet.core.config import get_config

_MIDDLEWARE = [AgeLimit, TimeLimit, ShutdownNotifications, Callbacks, Pipelines, Retries]


def build_broker() -> dramatiq.Broker:
    cfg = get_config()
    middleware = [m() for m in _MIDDLEWARE]
    broker: dramatiq.Broker
    if cfg.env == "test" or os.environ.get("OBSERVENET_STUB_BROKER") == "1":
        broker = StubBroker(middleware=middleware)
    else:
        broker = RedisBroker(url=cfg.redis_url, middleware=middleware, namespace="observenet")
    broker.add_middleware(AsyncIO())
    return broker


broker = build_broker()
dramatiq.set_broker(broker)
