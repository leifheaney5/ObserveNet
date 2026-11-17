# ADR 0008 — Schema deviations from the brief's table list

Status: accepted

The brief lists tables to design. The implemented schema follows it with these
deliberate differences.

## `metric_series` (added)

The brief describes `metric_samples` rows carrying source, unit, metric type,
asset/interface relationship and quality. Repeating that on every 1-second row would
multiply row width for data that is constant per series.

Decision: provenance lives once in `metric_series` (source, asset, interface, metric,
label, unit, observation point, quality, interval, coverage, first/last timestamp);
`metric_samples` and both rollup tables are `(series_id, ts|bucket, …)`. The same
information is available through one join.

## `checks` = rule catalog; `check_results` (added) = outcome per assessment

`checks` holds one row per rule (id, version, title, description, platform, required
posture section, category, default severity, classification), synced from code at
startup. What each rule concluded in a given assessment — `pass`, `fail`, `unsupported`
or `unknown` with a summary — is stored in `check_results`. This is what lets the
Security page show "Unsupported" and "Unknown" outcomes rather than only failures.

## Other additions

| Table | Purpose |
|---|---|
| `posture_snapshots` | The raw agent-reported posture a finding's evidence refers to. Identical consecutive snapshots are not stored again; `last_confirmed_at` records the re-confirmation. |
| `observed_services` | Listening sockets / published container ports per asset, with first/last seen and an `active` flag. |
| `asset_merges` | Audit trail and undo information for merges. |
| `ingest_batches` | Idempotency ledger `(source_id, batch_id)`. |
| `job_state` | Watermarks for rollups. |
| `maintenance_windows` | Alert suppression windows. |

## Naming

- `events` is the user-facing timeline; `audit_events` records who did what.
- `settings` holds one validated JSON document (`runtime`) rather than a row per
  setting.
- Enumerations are strings with `CHECK` constraints, not PostgreSQL enum types, to keep
  migrations simple.
