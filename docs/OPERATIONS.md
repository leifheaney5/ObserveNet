# Operations runbook

Day-to-day operation of the Compose stack on `linux-leif`. Commands assume the
repository root as working directory.

## Is it healthy?

| Check | Command | Healthy |
|---|---|---|
| Containers | `docker compose ps` | all `running`/`healthy`; `migrate` exited 0 |
| Liveness | `curl -s http://127.0.0.1:8080/api/v1/health` | `{"status":"ok"}` |
| Readiness | `curl -s http://127.0.0.1:8080/api/v1/ready` | `{"status":"ready",…}`; HTTP 503 lists the failing dependency |
| Components | UI → Overview "Service health", or `GET /api/v1/status` (needs a session) | API, PostgreSQL, Redis, Worker, Scheduler, Live stream all `ok` |
| Sources | UI → Settings → Data sources | each source `Live` |

`/api/v1/status` logic: the worker heartbeat (written by the `evaluate` task every
10 s) must be younger than 45 s; the scheduler heartbeat younger than 15 s; an ingest
backlog over 100 batches is reported as degraded.

## Logs

```bash
docker compose logs -f api
```

```bash
docker compose logs -f worker scheduler
```

```bash
docker compose logs -f collector
```

API and scheduler write structured JSON lines (structlog) with `service`, `level`,
`timestamp` and, inside a traced request, `trace_id`/`span_id`. Fields whose name
contains `password`, `token`, `secret`, `authorization` or `cookie` are redacted.
A failing background task logs `task_failed` with the task name, followed by Dramatiq's
retry line.

## Metrics

Each backend process serves Prometheus metrics on its own internal listener
(`OBSERVENET_METRICS_PORT`, 9464 in Compose). The listener is not part of the HTTP app
and is not reachable through nginx or the tunnel. The optional `observability` Compose
profile adds a Prometheus that scrapes them plus an OTLP trace backend. After enabling
it, confirm in Prometheus → Status → Targets that each of `api`, `worker` and
`scheduler` is actually up before relying on a metric.

| Metric | Labels | Emitted by | Meaning |
|---|---|---|---|
| `observenet_http_request_duration_seconds` | `method`, `route`, `status` | api | Request latency histogram |
| `observenet_ingest_batches_total` | `outcome` = `accepted`, `rate_limited`, `rejected_<code>` (api); `processed`, `duplicate` (worker) | api, worker | Batch outcomes |
| `observenet_ingest_samples_total` | — | worker | Samples persisted (new rows only) |
| `observenet_ingest_lag_seconds` | — | worker | API receipt → persisted |
| `observenet_collector_failures_total` | `source`, `collector` | worker | Errors reported inside agent heartbeats |
| `observenet_queue_depth` | `queue` = `ingest`, `maintenance` | scheduler | Messages waiting |
| `observenet_source_data_age_seconds` | `source` | scheduler | Seconds since each source last reported |
| `observenet_ws_clients` | — | api | Connected live-stream clients |
| `observenet_ws_dropped_clients_total` | — | api | Clients dropped for backpressure |
| `observenet_task_runs_total` | `task`, `outcome` | worker | Background task executions (`process_ingest`, `rollups`, `retention`, `evaluate`, `webhook`, `graph_sync`) |
| `observenet_task_duration_seconds` | `task` | worker | Task duration histogram |
| `observenet_alert_transitions_total` | `kind`, `to` | worker | Alerts firing/resolving |

Useful expressions:

- Ingest lag p95: `histogram_quantile(0.95, rate(observenet_ingest_lag_seconds_bucket[5m]))`
- Backlog: `observenet_queue_depth{queue="ingest"}`
- Silent source: `observenet_source_data_age_seconds > 90`
- Failing tasks: `rate(observenet_task_runs_total{outcome="error"}[5m]) > 0`
- Rejected batches: `rate(observenet_ingest_batches_total{outcome=~"rejected_.*"}[5m])`

### Traces

Set `OBSERVENET_OTEL_EXPORTER_OTLP_ENDPOINT` (e.g. `http://otel:4318`) to export spans
over OTLP/HTTP: FastAPI requests (health/ready excluded), SQLAlchemy statements and a
`task.<name>` span per background task. Unset means spans are created but not exported.

## Queue lag and backlog

Symptoms: charts stop advancing while the live numbers still move; "Ingest queue"
appears on the status page.

```bash
docker compose exec redis redis-cli llen observenet:ingest
```

```bash
docker compose exec redis redis-cli llen observenet:maintenance
```

Dead-lettered messages (retries exhausted):

```bash
docker compose exec redis redis-cli zcard observenet:ingest.XQ
```

- Worker stopped → `docker compose up -d worker`. Queued batches are processed in order;
  replays are safe (idempotent ledger).
- Worker running but slow → check `observenet_task_duration_seconds{task="process_ingest"}`
  and PostgreSQL load. Workers can be scaled: `docker compose up -d --scale worker=2`.
  Batches of one source stay serialized by an advisory lock; different sources run in
  parallel. Keep exactly **one** scheduler.
- Periodic tasks never pile up: they carry a maximum age and are dropped if stale.

## Retention and disk

Settings → Retention (admin). Defaults and bounds:

| Setting | Default | Bounds |
|---|---|---|
| Raw 1 s samples | 48 h | 1 h – 14 d |
| 1-minute rollups | 90 d | 1 – 730 d |
| 1-hour rollups | 365 d | 7 – 3650 d |
| Probe results | 48 h | 1 h – 30 d |
| Events | 365 d | 7 – 3650 d |
| Posture snapshots | 90 d | 1 – 3650 d (snapshots referenced by finding evidence are kept) |

The retention task runs every 15 minutes: it creates the next days' partitions and
drops day-partitions older than the raw window.

Table sizes:

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt+"'
```

Partitions:

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\d+ metric_samples"'
```

If `metric_samples_default` holds many rows, the day partitions were not created in
time (worker or scheduler was down across midnight UTC). They are still deleted by the
retention window; nothing needs fixing by hand.

Lowering raw retention takes effect at the next retention run. Raising it cannot bring
back dropped data.

## Tokens

Rotate an agent token:

1. Settings → Agent tokens → New token (or
   `docker compose exec api python -m observenet.cli create-agent-token --name <name> --ttl-days 90`).
2. Put the new token on the agent host and restart the agent.
3. Revoke the old token (Settings → Agent tokens). Revocation is immediate.

A token binds to the first agent id that uses it. A new token presented by an existing
agent binds to that agent's existing source, so history continues.

Bundled collector token (`OBSERVENET_BOOTSTRAP_AGENT_TOKEN`): put a new random value
(32+ characters) in `.env`, then

```bash
docker compose up -d api collector
```

The API registers the new value at startup; revoke the old one in the UI afterwards. A
revoked bootstrap token is never re-enabled automatically.

`OBSERVENET_SECRET_KEY` rotation invalidates **all** sessions and **all** agent tokens
(their stored digests are keyed with it). Users log in again; agent tokens must be
re-issued (the bootstrap token re-registers itself on API start).

## Users

```bash
make create-admin
```

Other accounts: Settings → Users. Disabling a user or resetting their password revokes
their sessions. The last active admin cannot be demoted or disabled.

Locked out with no admin password: `docker compose exec api python -m observenet.cli
create-admin --username recovery` creates a second admin.

## Backup and restore

See `DEPLOY_LINUX_LEIF.md` §11. Back up the database dump **and** `.env`. Test a restore
at least once before you need it.

## Demo data

- Demo mode (`OBSERVENET_DEMO_MODE=true`) rejects real agents; real mode rejects demo
  agents and synthetic samples.
- When the API starts in real mode it deletes every row flagged `is_demo` (sources,
  assets, segments, probe targets, findings, alerts, events) and the matching live
  cache keys, and logs `demo_data_purged`. The synthetic raw samples and rollups
  of demo sources are deleted explicitly (those tables have no foreign key), then the
  series rows go with their sources.
- To start completely clean: `docker compose down -v` (destroys all data), then
  `docker compose up --build -d`.

## Changing configuration

| Kind | Where | Applies |
|---|---|---|
| Process configuration (`OBSERVENET_*` in `.env`) | `.env` | after `docker compose up -d` (recreates changed services) |
| Runtime settings (retention, intervals, scope, allowlists, discovery, capacity test) | Settings UI / `PUT /api/v1/settings` | immediately; agents pick up changes within ~5 s |
| Alert thresholds | Alerts → Rules | next evaluation (≤ 10 s) |
| Probe targets | Settings → Probe targets | agents within ~5 s |

All of the UI changes are written to the audit log.

## Common failures

| Symptom | Cause | Action |
|---|---|---|
| Dashboard shows "Disconnected" but containers are up | WebSocket blocked or session expired | Reload; check `docker compose logs web api`; through a proxy, confirm WebSocket upgrade is allowed |
| All sources `Stale`/`Disconnected` | Collector cannot reach the API, or token rejected | `docker compose logs collector` — look for `upload failed` (network) vs `batch rejected (4xx)` (auth/schema) |
| Live values move, history is flat | Worker down or failing | `docker compose logs worker`; queue depth; `task_failed` lines |
| History has gaps of a few seconds | Agent could not derive a rate (reset/gap) or was restarted | Expected; gaps are shown rather than interpolated |
| Gateway latency `unavailable` | ICMP not permitted for the collector | `DEPLOY_LINUX_LEIF.md` → Enable ICMP |
| Packet-loss alert while the internet works | Gateway rate-limits or filters ICMP | Raise the threshold, lengthen the window, or probe a different internal target |
| "New device" alerts for known devices | MAC randomization (phones) creates new identities | Merge manually, or disable private addressing for the home SSID on that device |
| Verification ends as `unknown` | No fresh posture snapshot arrived within the timeout (default 90 s): agent offline, or the section was unreadable | Check the source is Live; retry |
| `clock skew` in source error | Agent clock > 120 s ahead | Fix NTP on that host |
| API refuses to start: secret key | `OBSERVENET_SECRET_KEY` shorter than 32 chars in production | `make init` or set a proper value |
| 400 "Invalid host header" | Host not in `OBSERVENET_ALLOWED_HOSTS` | Add it, `docker compose up -d api` |
| `graph_sync` task errors every 5 min | `OBSERVENET_NEO4J_URI` set but Neo4j is unreachable or `NEO4J_PASSWORD` is wrong | Start the `graph` profile / fix the credentials, or unset the URI (ADR 0006) |

## Capacity notes

Single node, no HA. One agent produces roughly 1.7 M raw rows/day (bounded by raw
retention) plus ~29 k minute-rollup rows/day. Each additional agent adds the same. The
stack has not been load-tested.
