# ObserveNet — Know Your Network

A self-hosted, real-time home-network observability platform with a read-only
**Discover → Assess → Prioritize → Fix → Verify → Monitor** workflow.

ObserveNet shows what was actually measured, where it was measured, and how fresh it is.
When something cannot be observed it says **Unknown**, **Unavailable** or **Unsupported**
instead of drawing a number. It never scans, exploits, or changes a device.

> **Status: built and verified locally, not deployed.** The stack runs with
> `docker compose` and was exercised end to end in a build sandbox (results below).
> It has **not** been run on `linux-leif`, and `https://observenet.leafslab.com` is a
> documented procedure, not a live endpoint. See [Known limitations](#known-limitations).

ObserveNet is an independent project. Its Find/Fix/Verify emphasis is loosely inspired by
ideas popularised by autonomous-pentesting products, but it is not affiliated with
Horizon3.ai, does not replicate NodeZero, and **does not prove exploitability** — it
verifies *configuration*, by re-reading it.

---

## What it does

| Area | What you get | Honest caveat |
|---|---|---|
| **Overview** | Live KPIs (devices, WAN, gateway latency, loss, open findings), 1 Hz throughput and latency charts, alerts, service health | WAN and per-device traffic need a gateway source; without one they read *Unsupported* |
| **Devices** | Inventory with identity confidence, presence, addresses, services, history; rename/tag/group; explicit, audited merge/unmerge; client-list import; CSV/JSON export | Inventory is passive (neighbour cache + agents + imports), so it is partial |
| **Traffic** | Host NIC throughput from 1 s counter deltas, history with rollups, period-over-period comparison | Host NIC ≠ WAN ≠ per-device; each is labelled |
| **Connectivity** | Gateway latency/jitter/loss over explicit windows, external TCP + DNS checks, multi-signal internet status, outage log | One failing endpoint is never called an outage |
| **Security** | Deterministic read-only posture checks, evidence with provenance, manual remediation steps, **Verify** re-check with before/after diff | Observed configuration / potential exposure only |
| **Alerts** | Threshold alerts with debounce, hysteresis, cooldown, dedup, ack/snooze, maintenance windows | In-app by default; optional signed webhook |
| **Insights** | Plain comparisons with the method and sample sizes shown; topology graph | Correlation is labelled as correlation |
| **Settings** | Sources and capabilities, agent tokens, probe targets, retention, network scope, users, audit log | — |

## Architecture in one picture

```
 observation point              transport                 processing                 storage                  serving
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐     ┌──────────────────┐
│ Linux collector  │ HTTPS│ FastAPI          │ pub/ │ Redis            │      │ PostgreSQL 16    │     │ REST  /api/v1    │
│ (1 s NIC deltas, ├─────▶│ POST /ingest     ├─────▶│  live fan-out    ├──┐   │  metric_samples  │◀────│ GraphQL /graphql │
│ probes, posture) │bearer│ validate + auth  │ sub  │  Dramatiq queues │  │   │  (daily parts)   │     │ WS /api/v1/stream│
├──────────────────┤      └──────────────────┘      └────────┬─────────┘  │   │  rollups 1m / 1h │     └────────┬─────────┘
│ Windows agent    │                                         │ ingest     │   │  assets, findings│              │
│ (optional)       │                                ┌────────▼─────────┐  │   │  alerts, audit   │     ┌────────▼─────────┐
├──────────────────┤                                │ Dramatiq worker  │──┼──▶│                  │     │ React dashboard  │
│ Router client    │                                │ idempotent apply │  │   └──────────────────┘     │ (TanStack Query, │
│ list import      │                                │ rules · alerts   │  └── 1 Hz coalesced frames ──▶│  Recharts)       │
└──────────────────┘                                └──────────────────┘                               └──────────────────┘
```

Stack: Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 + Alembic · PostgreSQL 16 ·
Redis + Dramatiq · Strawberry GraphQL · OpenTelemetry + Prometheus · React 18 ·
TypeScript · Vite · Tailwind · Radix · Recharts · TanStack Query · Docker Compose.
Details and trade-offs: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Quick start

Requirements: Docker Engine with the Compose plugin, `make`, and `openssl` or `python3`.

```bash
git clone https://github.com/leifheaney5/ObserveNet.git
```

```bash
cd ObserveNet
```

```bash
make init
```

`make init` writes `.env` (mode 600) with random secrets and prints the generated admin
password. There are no default credentials.

```bash
docker compose up --build -d
```

Open <http://127.0.0.1:8080> and sign in as `admin` with the printed password.

By default this starts PostgreSQL, Redis, the API, a worker, the scheduler, the web
front end and the **read-only Linux collector for this host**. Within a few seconds the
Overview shows this host's live NIC throughput and, if ICMP is permitted, gateway latency.

Only the web container is published, on `127.0.0.1:8080`. PostgreSQL, Redis, the API and
the metrics listeners are reachable only on the internal Docker network.

### Demo mode (synthetic, clearly labelled)

```bash
make demo
```

Everything in demo mode is synthetic and marked `DEMO` with a banner on every page. Real
collectors are rejected while it is on, and demo rows are purged automatically the next
time the stack starts in real mode. Stop it and remove its data with `make demo-down`.

### Gateway latency needs ICMP permission

The collector runs unprivileged. For gateway ping it uses unprivileged ICMP sockets, which
the host must allow:

```bash
sudo sysctl -w net.ipv4.ping_group_range="0 2147483647"
```

Without it, the gateway probe reports *Unavailable* with the reason (it does not guess).
Most current Ubuntu/Debian systems already ship this setting. Persist it in
`/etc/sysctl.d/` if needed.

### Useful commands

| Command | Purpose |
|---|---|
| `make ps` / `make logs` | Service status / logs |
| `make down` | Stop (volumes kept) |
| `make create-admin` | Create an administrator interactively |
| `make backup` / `make restore FILE=…` | PostgreSQL dump / restore |
| `make test` | Backend, agent and frontend checks |
| `make e2e` | Playwright smoke tests against a running stack |
| `make openapi` | Regenerate OpenAPI, GraphQL SDL and TypeScript types |

---

## Data sources and what each can measure

| Source | Provides | Cannot provide |
|---|---|---|
| **Linux collector** (`agents/linux`, bundled) | Host NIC throughput (1 s), CPU/memory, interfaces, default gateway, IPv4 neighbour cache sightings, listening sockets, gateway/external probes, opt-in Docker published ports, opt-in capacity test and low-rate discovery | WAN throughput, other devices' traffic, Wi-Fi data |
| **Windows agent** (`agents/windows`, optional) | NIC throughput, firewall profiles, SMBv1/2 state, listening ports | Anything about other devices. **Untested: could not be executed in the build environment** |
| **Router client-list import** (CSV/JSON) | Point-in-time device inventory with hostnames | Live presence, counters, signal |
| **Gateway providers** (interface defined, none implemented) | Would provide WAN counters, leases, per-device traffic, Wi-Fi, UPnP state | — |

The provider interface, the metric glossary and the exact rate formula
(`bits_per_second = 8 × max(0, Δbytes) / Δt` on a monotonic clock, with reset/wrap/gap
handling) are in [`docs/TELEMETRY_SOURCES.md`](docs/TELEMETRY_SOURCES.md).

## Security model in brief

- Private by default: loopback bind, no inbound port forward required.
- App authentication on top of any edge policy: Argon2id passwords, opaque server-side
  sessions in `HttpOnly; SameSite=Lax` cookies (`Secure` on HTTPS), CSRF header on every
  mutation, admin/viewer roles, login rate limiting, audit log.
- Collector tokens are scoped to ingestion, bound to one agent on first use, expiring and
  revocable; only a keyed hash is stored.
- No CORS (same-origin SPA), trusted-host check, CSP and security headers, GraphQL depth /
  alias / token limits with introspection off in production, Prometheus metrics on a
  separate internal listener that the reverse proxy cannot route to.
- Probe targets are validated against an approved private network scope on the server and
  again on the agent (no arbitrary internal targets, small TCP port allowlist).
- The product refuses to do: exploitation, credential guessing, intrusive scanning, packet
  capture, remote command execution, router/firewall changes.

More: [`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md), [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

## Public hostname: `observenet.leafslab.com`

**Not configured.** Nothing was created at any DNS provider or at Cloudflare. The intended
path is an outbound-only Cloudflare Tunnel plus a Cloudflare Access policy in front of the
app's own login. Exact steps, prerequisites (the `leafslab.com` zone must be under your
control on Cloudflare) and alternatives are in
[`docs/DOMAIN_AND_AUTH.md`](docs/DOMAIN_AND_AUTH.md); host bring-up is in
[`docs/DEPLOY_LINUX_LEIF.md`](docs/DEPLOY_LINUX_LEIF.md).

---

## Development

```bash
cd backend && uv sync --all-extras
```

```bash
cd agents/linux && uv sync
```

```bash
cd frontend && npm ci
```

Backend tests need a PostgreSQL 16 database; point `OBSERVENET_TEST_DATABASE_URL` at an
empty one (default `postgresql+psycopg://postgres@127.0.0.1:55432/observenet_test`).

| Check | Command |
|---|---|
| Backend lint / format / types / tests | `cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy observenet && uv run pytest` |
| Agent lint / format / types / tests | `cd agents/linux && uv run ruff check . && uv run ruff format --check . && uv run mypy observenet_agent && uv run pytest` |
| Frontend lint / types / unit / build | `cd frontend && npm run lint && npm run typecheck && npm test && npm run build` |
| End to end | `cd frontend && E2E_PASSWORD=… E2E_MODE=demo npm run e2e` |

Building behind a TLS-inspecting proxy: see the header of
[`compose.proxy.yaml`](compose.proxy.yaml).

## Validation results

VALIDATION_PLACEHOLDER

## Repository layout

```
backend/            FastAPI app, GraphQL schema, services, workers, migrations, tests
agents/linux/       Read-only Linux collector (Python) + Dockerfile + systemd unit
agents/windows/     Optional read-only Windows agent (PowerShell) — untested
frontend/           React + TypeScript dashboard, unit tests, Playwright e2e
infra/              Cloudflare Tunnel notes, Prometheus config, optional AWS note
docs/               Architecture, product spec, telemetry, security, threat model, ADRs, runbooks
compose.yaml        The stack; optional profiles: tunnel, docker-inventory, graph, observability
```

## Documentation

[Architecture](docs/ARCHITECTURE.md) · [Product spec](docs/PRODUCT_SPEC.md) ·
[Telemetry sources](docs/TELEMETRY_SOURCES.md) · [Security model](docs/SECURITY_MODEL.md) ·
[Threat model](docs/THREAT_MODEL.md) · [ADRs](docs/ADRs/) ·
[Deploy on linux-leif](docs/DEPLOY_LINUX_LEIF.md) · [Domain & auth](docs/DOMAIN_AND_AUTH.md) ·
[Operations](docs/OPERATIONS.md) · [Interview demo](docs/INTERVIEW_DEMO.md) ·
[Limitations](docs/LIMITATIONS.md)

## Known limitations

The full, prioritised list is in [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md). The ones
that matter most:

1. **No WAN throughput and no per-device traffic in real mode** — no gateway provider is
   implemented (no SNMP, UniFi, OPNsense or flow collector).
2. **Device inventory is partial** — passive sightings only.
3. **Not deployed** — never run on `linux-leif`; the public hostname does not exist yet.
4. **Windows agent untested**; the systemd unit and the GitHub Actions workflow have not
   been run either.
5. **Single node, PostgreSQL as the time-series store** — fine for a home network, not a
   claim of production readiness.
