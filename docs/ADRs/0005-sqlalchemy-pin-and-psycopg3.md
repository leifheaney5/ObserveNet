# ADR 0005 — SQLAlchemy pinned below 2.1; psycopg 3 for both API and workers

Status: accepted (records a substitution from "latest")

## Context

1. `uv` resolved SQLAlchemy 2.1.x. At startup
   `opentelemetry-instrumentation-sqlalchemy` logged that it only instruments
   `sqlalchemy >= 1.0.0, < 2.1.0` and instrumented nothing. Database spans are part of
   the required observability.
2. The API is async; Dramatiq actors are async via the AsyncIO middleware. Using asyncpg
   for the API and a sync driver for workers would mean two drivers and two code paths.

## Decision

- Pin `sqlalchemy[asyncio]>=2.0.36,<2.1` in `backend/pyproject.toml`. This still
  satisfies "SQLAlchemy 2.x" (typed `Mapped[...]` models, 2.0-style queries).
- Use **psycopg 3** (`postgresql+psycopg://`) everywhere: the async engine for API and
  workers, the sync engine for Alembic. `Config` rewrites a bare `postgresql://` URL to
  the psycopg dialect.

## Consequences

- SQL spans are emitted when an OTLP endpoint is configured.
- The pin must be lifted once the instrumentation supports 2.1; nothing in the code
  depends on 2.1 features.
- One driver to patch. psycopg 3's async mode is somewhat slower than asyncpg on
  micro-benchmarks; irrelevant at this volume.
- Observed quirk: `rowcount` is unreliable for multi-row `INSERT … ON CONFLICT` under
  psycopg, so sample inserts count rows with `RETURNING` instead.
