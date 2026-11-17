# 5-minute interview demo

A timed walkthrough for a Senior Full-Stack Engineer (Autonomous Defense) conversation.
Two ways to run it:

- **Real mode** (preferred when on `linux-leif` or any Linux box): genuine NIC
  throughput, gateway latency and a real finding you create and fix live.
- **Demo mode** (`make demo`): synthetic WAN, per-device traffic and Windows findings,
  all labelled. Use it when no real host is at hand, and say out loud that it is
  synthetic.

ObserveNet is an independent project. It is not affiliated with Horizon3.ai, does not
replicate NodeZero, and proves nothing about exploitability.

## Before the call (2 minutes of prep)

Real mode:

```bash
docker compose up --build -d
```

```bash
curl -s http://127.0.0.1:8080/api/v1/ready
```

Log in, confirm the header says **Live** and Overview shows host NIC throughput. Open a
second terminal on the agent host for the fix-and-verify step. Decide the throwaway
port (9999 below) and make sure nothing uses it.

Demo mode:

```bash
make demo
```

Confirm the demo banner is visible.

## Script

### 0:00 – 0:45 · Live dashboard

- Overview: "Every card has a value, a timestamp and a source. This one is `linux-leif`'s
  own NIC, sampled every second from kernel counters; the rate is
  `8 × delta / elapsed` on a monotonic clock."
- Point at the WAN cards (real mode): "These say *Unsupported*. One LAN host cannot see
  WAN or per-device traffic; that needs the gateway. I would rather show that than a
  plausible-looking number." (Demo mode: "these are synthetic — see the banner.")
- Header indicator: "WebSocket, one coalesced frame per second from a single Redis
  subscription per API process. No per-browser database polling."
- Switch the period to 24 h: "Same endpoint, but now it reads minute or hour rollups
  instead of raw samples."

### 0:45 – 1:30 · One asset's history

- Devices → open the agent host.
- "Identity is conservative: agent id is strong, a globally administered MAC is medium,
  a randomized MAC or an IP alone is low and never auto-merged. Presence says *seen
  recently* / *not seen recently*, not online/offline, because a neighbour cache cannot
  prove a device is gone."
- Show address history, interfaces, observed services and the throughput history for
  that host. Mention this detail view is the GraphQL use case: one query, batched
  loaders for identities/addresses/services/findings.

### 1:30 – 2:15 · A finding with evidence

Real mode — create one, in the terminal on the agent host:

```bash
python3 -m http.server 9999 --bind 0.0.0.0
```

Posture snapshots are sent every 5 minutes; to avoid waiting, Settings → Data sources →
**Test** on the agent requests a fresh snapshot (arrives within ~10 s).

- Security → the new finding "TCP port 9999 is listening on all interfaces".
- Read it out: rule id and version, **observation** (from the host's socket table, with
  collection time and method), **inference** labelled as inference ("may be reachable;
  not tested"), severity with its rationale, classification `POTENTIAL_EXPOSURE`.
- "There is no 'exploitable' label anywhere in the schema. We read configuration; we do
  not attack."

Demo mode: open "Windows Firewall is disabled for the Public profile" or "TCP port 6379
… (Redis)".

### 2:15 – 3:15 · Fix and verify

Real mode — stop the listener (Ctrl-C in that terminal), then in the UI press
**Verify fix** on the finding.

- "Verify does not trust me. It asks the agent for a new snapshot, and only evidence
  collected *after* the request counts. The same rule runs again."
- Within ~10 s: status **Resolved**, classification `VERIFIED_RESOLUTION`, with a
  before/after diff and both evidence records.
- Show the alternative: "If I had clicked *Close* instead, it would say *Closed — not
  verified*. If the agent had been offline, the run would end as *Unknown* after the
  deadline. Neither is ever reported as fixed."
- Optional: restart the listener to show the finding re-opening on the next snapshot.

Demo mode: use the demo-only **Simulate fix** control on the Security page (it calls
`POST /api/v1/demo/fix`, which flips the synthetic host's setting) for the firewall
item, then **Verify fix**; same outcome path through the real engine.

### 3:15 – 4:15 · Backend architecture

Open `docs/ARCHITECTURE.md` (the diagram) or draw it:

- Agent → `POST /ingest` → API validates, publishes to Redis pub/sub for the live path
  and enqueues a Dramatiq task → worker applies the batch in one transaction →
  PostgreSQL.
- Idempotency: `(source, batch_id)` ledger + `(series, ts)` primary key; a per-source
  advisory lock serializes one agent's batches while others run in parallel. "I found
  that race for real: two workers creating the same asset on an agent's first batches."
- Time series: narrow fact table partitioned by day, dropped whole after 48 h; minute
  and hour rollups as idempotent upserts with a watermark.
- Alerts: debounce, hysteresis, cooldown; missing data is unknown, not zero.
- Observability of the system itself: structured logs with trace ids, Prometheus
  metrics on an internal listener (ingest lag, queue depth, data freshness, WS clients),
  OpenTelemetry spans.

### 4:15 – 5:00 · How this differs from NodeZero (be candid)

- "NodeZero performs autonomous pentesting: it safely exploits weaknesses, chains them
  and proves impact, then lets you re-run to verify a fix. That is *exploitation
  validation*."
- "ObserveNet does *configuration verification*: it reads what a host reports about
  itself, flags observed configurations and potential exposures, and re-reads to
  verify a change. It borrows the Find → Fix → Verify discipline and the insistence on
  evidence — not the capability. It cannot tell you a weakness is exploitable,
  reachable, or part of an attack path, and it does not pretend to."
- "What I wanted to show is the engineering around that loop: provenance on every
  number, honest unknowns, idempotent pipelines, and verification that cannot be faked
  by clicking a button."

## Talking points tied to the role

1. **Python backend (FastAPI, Pydantic v2, typed boundaries).** Strict ingest schema
   with `extra="forbid"`, fixed units per metric, explicit section status; services are
   plain async functions over a session, routes stay thin.
2. **PostgreSQL.** Declarative range partitioning for raw samples, rollup upserts,
   partial unique index for alert deduplication, advisory locks, keyset pagination.
   Trade-off acknowledged: not a TSDB (ADR 0001).
3. **Asynchronous processing.** Redis + Dramatiq with async actors; bounded, retryable,
   idempotent tasks; message age limits so backlogs do not replay; separate scheduler.
4. **React + TypeScript.** A typed API client over the backend's response models,
   TanStack Query for server state, a reconnecting 1 Hz WebSocket, and explicit
   loading / empty / stale / unsupported states instead of zeros.
5. **GraphQL where it fits.** Read-only inventory graph with dataloaders, cursor
   pagination, depth/alias/token limits, field-level authorization; REST for commands,
   ingest and time series (ADR 0003).
6. **Security telemetry with integrity.** Evidence records with source, method and
   collection time; observed vs. inferred; verification bound to fresh evidence; no
   exploitability claims.
7. **Security of the product itself.** Argon2id, server-side sessions, CSRF, scoped
   revocable agent tokens bound to one agent, probe-target allowlisting to keep agents
   from becoming scanners (ADR 0004), metrics off the public listener, edge access +
   app login.
8. **Pragmatic ownership.** Things deliberately *not* built or not claimed: router
   adapters without hardware to test, Neo4j kept as an optional projection rather than a dependency, a Windows agent
   marked untested, a public hostname documented but not deployed. `LIMITATIONS.md`
   lists them in priority order.

## If asked "what would you do next?"

A gateway source (SNMPv3 WAN counters first), run and fix the Windows agent on a real
PC, deploy the tunnel and Access policy and run the verification checklist, then load
tests and a restore drill.
