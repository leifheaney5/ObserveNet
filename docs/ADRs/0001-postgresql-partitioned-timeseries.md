# ADR 0001 — PostgreSQL with partitioned raw samples instead of a TSDB

Status: accepted

## Context

ObserveNet stores 1-second samples (NIC rates, probe results, host resources) plus
inventory, evidence, alerts and auth data. Expected scale: one to a handful of agents,
roughly 20 series each → about 1.7 M raw rows per agent per day. The brief asks for
PostgreSQL 16 as the operational database with time-bucketed downsampling and bounded
growth.

## Decision

Keep time series in PostgreSQL:

- `metric_series` — one row per (source, metric, label) carrying unit, observation
  point, quality, interval and coverage.
- `metric_samples (series_id, ts, value)` — narrow fact table, primary key
  `(series_id, ts)`, `PARTITION BY RANGE (ts)` with one partition per UTC day plus a
  default partition as a safety net.
- `metric_rollups_1m` and `metric_rollups_1h` — `(series_id, bucket)` with
  avg/min/max/sum/count, maintained by an idempotent upsert job with a watermark and a
  120 s late-data grace window.
- Retention: drop whole day-partitions for raw data (48 h default); range deletes for
  rollups (90 d / 365 d).
- Reads choose the table from the requested bucket width, so coarse queries never scan
  raw samples; responses are capped at 1500 points per series.

## Alternatives

- **TimescaleDB** — hypertables, compression and continuous aggregates would replace
  the hand-written partition/rollup code. Rejected for now: an extension image to
  maintain and licence terms to track, for a volume plain PostgreSQL handles.
- **Prometheus / VictoriaMetrics / InfluxDB** — better storage efficiency, but a second
  datastore and query language, and joins between metrics and inventory/evidence move
  into application code.

## Consequences

- One datastore to back up and operate; relational integrity between series and assets.
- No compression: 48 h of raw data is about 3.5 M rows per agent. Fine here; it would
  not be at hundreds of agents or sub-second sampling.
- Hand-written partition management must run (the `run_retention` actor). If it does
  not, rows land in the default partition and are deleted by range instead of by drop —
  slower, still bounded.
- Rollups are averages of 1 s samples; percentiles are not preserved beyond raw
  retention.
- Storage access is confined to `services/timeseries.py`, which is the seam for a later
  move to TimescaleDB or a TSDB.
