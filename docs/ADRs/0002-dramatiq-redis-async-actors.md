# ADR 0002 — Dramatiq on Redis with async actors and a separate scheduler

Status: accepted

## Context

Collector batches must not be persisted inside the API request, and rollups, retention,
alert evaluation and verification deadlines need to run periodically. The brief asks for
Redis plus one explicit worker framework (RQ, Dramatiq or Celery) with bounded,
retryable, observable, idempotent tasks.

## Decision

- **Dramatiq** with `RedisBroker` (namespace `observenet`) and the `AsyncIO` middleware,
  so actors are `async def` and share the same async SQLAlchemy/psycopg code path as
  the API.
- Two queues: `ingest` (collector batches) and `maintenance` (everything periodic).
- Every actor sets `time_limit` and `max_retries`; `process_ingest` retries up to 8
  times with 0.5–30 s backoff. Periodic actors set `max_age` so a backlog accumulated
  while the worker was down is dropped rather than replayed.
- Middleware list is explicit (AgeLimit, TimeLimit, ShutdownNotifications, Callbacks,
  Pipelines, Retries, AsyncIO); Dramatiq's own Prometheus middleware is not used.
  Task counters/histograms come from a small decorator in `workers/tasks.py`.
- A separate **scheduler** process enqueues periodic actors on fixed intervals
  (evaluate 10 s, rollups 60 s, retention 15 min, graph sync 5 min). It holds no state;
  duplicate ticks are harmless because every periodic actor is idempotent.
- Tests use `StubBroker` (`OBSERVENET_ENV=test`).

## Alternatives

- **Celery** — heavier, more configuration surface than this needs.
- **RQ** — simple, but sync-only; would force a second, synchronous data-access path.
- **arq** — async-native, but not one of the frameworks named in the brief.
- **APScheduler inside the worker** — couples scheduling to worker count.

## Consequences

- One code path (async) for API and workers.
- The AsyncIO middleware runs actors on a single event-loop thread per worker process;
  CPU-heavy work would block it. Current actors are I/O-bound.
- Redis is both broker and live bus; if Redis loses its data, queued batches are lost.
  Agents retry only what is still in their own buffer.
- Exactly one scheduler should run. Running two only doubles enqueue volume.
