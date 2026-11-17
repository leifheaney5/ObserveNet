# ADR 0007 — Demo and real data never mix

Status: accepted

## Context

A demo needs a populated dashboard (WAN throughput, per-device traffic, Windows
findings) that a fresh real install cannot have. The product rule is "no fake
observability": synthetic values must never be mistaken for measurements.

## Decision

- `OBSERVENET_DEMO_MODE=true` is a process-level switch.
- In demo mode the scheduler runs a generator (`services/demo.py`) that builds ordinary
  ingest payloads for three synthetic agents and pushes them through the **real**
  pipeline (accept → queue → worker → rules → alerts). Nothing is special-cased
  downstream.
- Every synthetic sample has `quality="synthetic"`; synthetic sources, assets, segments,
  probe targets, findings, alerts and events carry `is_demo=true`. Synthetic MACs use
  the IANA documentation range `00:00:5E:00:53:xx`.
- **Mutual exclusion at ingest:** in demo mode real agents are rejected with 409; in
  real mode demo agents and `synthetic` samples are rejected.
- **Purge on start:** when the API starts in real mode it deletes every `is_demo` row
  and the matching live-cache keys.
- The demo-only routes (`/api/v1/demo/*`, "simulate fix") are mounted only in demo mode.
- The UI shows a persistent demo banner; `GET /api/v1/meta` exposes `demo_mode` so the
  login page shows it too.
- A real-mode database starts empty: no seed devices, metrics or findings. Only the
  rule catalog, default alert rules and default probe targets are created.

## Consequences

- The demo exercises the same code as production paths, so it is a meaningful smoke
  test.
- Switching a database from demo to real destroys the demo data (intended). Switching
  real → demo keeps real rows in the database but stops real ingestion; use a separate
  database/volume for demos to avoid a mixed history.
- Demo capabilities (WAN, per-device) show as "supported" in demo mode. The banner and
  the `SYNTHETIC` capability detail are the safeguards against misreading that.
