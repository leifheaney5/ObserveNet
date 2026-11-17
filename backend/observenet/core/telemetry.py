"""Prometheus metrics + OpenTelemetry tracing.

Metrics are exposed on a *separate* internal listener (``OBSERVENET_METRICS_PORT``), never
through the public HTTP app, so the reverse proxy / tunnel cannot route to them.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from observenet.core.config import get_config

HTTP_LATENCY = Histogram(
    "observenet_http_request_duration_seconds",
    "API request latency",
    ["method", "route", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
INGEST_BATCHES = Counter("observenet_ingest_batches_total", "Ingest batches by outcome", ["outcome"])
INGEST_SAMPLES = Counter("observenet_ingest_samples_total", "Metric samples persisted")
INGEST_LAG = Histogram(
    "observenet_ingest_lag_seconds",
    "Delay between API receipt and worker persistence",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
COLLECTOR_FAILURES = Counter(
    "observenet_collector_failures_total", "Collector-reported errors", ["source", "collector"]
)
QUEUE_DEPTH = Gauge("observenet_queue_depth", "Messages waiting in a worker queue", ["queue"])
WS_CLIENTS = Gauge("observenet_ws_clients", "Connected live-stream clients")
WS_DROPPED = Counter("observenet_ws_dropped_clients_total", "Clients dropped for backpressure")
DATA_FRESHNESS = Gauge(
    "observenet_source_data_age_seconds", "Seconds since a source last reported", ["source"]
)
TASK_RUNS = Counter("observenet_task_runs_total", "Background task executions", ["task", "outcome"])
TASK_DURATION = Histogram("observenet_task_duration_seconds", "Background task duration", ["task"])
ALERT_TRANSITIONS = Counter("observenet_alert_transitions_total", "Alert state transitions", ["kind", "to"])

_tracing_configured = False
_instrumented_engine: Any = None
_metrics_started = False


def setup_tracing(service_name: str) -> None:
    """Install a tracer provider. Spans are exported only if an OTLP endpoint is configured."""
    global _tracing_configured
    if _tracing_configured:
        return
    cfg = get_config()
    provider = TracerProvider(resource=Resource.create({"service.name": f"observenet-{service_name}"}))
    if cfg.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint = cfg.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _tracing_configured = True


def instrument_app(app: Any, engine: Any) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    global _instrumented_engine
    FastAPIInstrumentor.instrument_app(app, excluded_urls="/api/v1/health,/api/v1/ready")
    if _instrumented_engine is not engine.sync_engine:  # one engine per process; apps may be rebuilt
        instrumentor = SQLAlchemyInstrumentor()
        if instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.uninstrument()
        instrumentor.instrument(engine=engine.sync_engine)
        _instrumented_engine = engine.sync_engine


def start_metrics_listener() -> None:
    global _metrics_started
    port = get_config().metrics_port
    if port and not _metrics_started:
        start_http_server(port)
        _metrics_started = True


tracer = trace.get_tracer("observenet")
