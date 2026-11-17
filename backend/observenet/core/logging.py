from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from opentelemetry import trace

_SENSITIVE = ("password", "token", "secret", "authorization", "cookie")


def _redact(_: Any, __: str, event: dict[str, Any]) -> dict[str, Any]:
    for key in list(event):
        if any(s in key.lower() for s in _SENSITIVE):
            event[key] = "[redacted]"
    return event


def _add_trace(_: Any, __: str, event: dict[str, Any]) -> dict[str, Any]:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event["trace_id"] = format(ctx.trace_id, "032x")
        event["span_id"] = format(ctx.span_id, "016x")
    return event


def configure_logging(level: str = "INFO", json: bool = True, service: str = "api") -> None:
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_trace,
        _redact,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)
    logging.basicConfig(level=logging.WARNING, stream=sys.stdout)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
