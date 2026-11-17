# Threat model

Scope: the ObserveNet stack on one home Linux host (`linux-leif`), its agents, and the
documented remote-access path (`https://observenet.leafslab.com` through an outbound
tunnel with an edge access policy). The remote path is designed and documented, **not
deployed or verified**.

## Assets

| Asset | Why it matters |
|---|---|
| Device inventory (IPs, MACs, hostnames, presence history) | A map of the home network and of when people are home |
| Posture data (listening services, firewall/SMB state, findings) | A to-do list for an attacker already on the LAN |
| Admin session / credentials | Can change scope, probe targets, tokens, users |
| Agent tokens | Can inject telemetry for one agent |
| `OBSERVENET_SECRET_KEY`, database password, tunnel token | Root of session/token digests; DB access; ability to serve the public hostname |
| Integrity of metrics/findings | Decisions are made from them |
| The agents' position on the LAN | A process that can send probes from inside the network |

## Trust boundaries

1. Internet ↔ edge (Cloudflare) ↔ tunnel ↔ `web` container.
2. Browser ↔ API (cookie session).
3. Agent ↔ API (bearer token; plain HTTP on loopback for the bundled collector, HTTPS
   for remote agents).
4. API/worker ↔ PostgreSQL/Redis (internal Docker networks, password for PostgreSQL,
   none for Redis).
5. Backend ↔ agent configuration channel (backend tells agents what to probe).
6. Host OS ↔ containers.

## Threats and mitigations

| # | STRIDE | Threat | Mitigations | Residual risk |
|---|---|---|---|---|
| 1 | S | Stranger reaches the login page from the internet and guesses passwords | Not published by default (127.0.0.1). Remote design puts Cloudflare Access in front; app login behind it; Argon2id; 12+ char minimum; 8 attempts/min/IP; audit of failures | No MFA or lockout in the app itself. If the Access policy is misconfigured or skipped, the app login is the only barrier. Rate limit is per IP only. |
| 2 | S | Stolen session cookie | HttpOnly, SameSite=Lax, Secure over HTTPS; 12 h TTL; server-side revocation; CSP without inline scripts | A stolen cookie works until expiry or revocation; sessions are not bound to IP or device. |
| 3 | S | Stolen agent token used to impersonate an agent | Token bound to one `agent_uid`, scope `ingest`, expiry, revocation, rate limit; stored hashed | Token sits in `.env` / a file on the agent host; anyone with root there can read it. Whoever holds the token *and* knows the agent_uid can inject false telemetry for that host until revoked. The bootstrap token has no expiry. |
| 4 | T | Forged telemetry hides or fabricates findings | Strict schema, fixed units, future-timestamp rejection, synthetic quality refused in real mode; evidence stores source and collection time | A compromised agent host can lie about itself. ObserveNet has no independent vantage point to cross-check (by design: it does not scan). |
| 5 | T | CSRF against admin actions | SameSite=Lax + per-session CSRF token header on every mutation and on GraphQL POST | — |
| 6 | T | SQL injection | SQLAlchemy bound parameters; validated dynamic DDL names | Hand-written SQL in rollups/insights takes only bound parameters; future changes need review. |
| 7 | T | XSS via hostnames or process names reported by agents/imports | React escapes by default; CSP `script-src 'self'`; length limits on all reported strings | CSV exports prefix cells starting with `=`, `+`, `-`, `@`, tab or CR with `'`; no other spreadsheet hardening. |
| 8 | R | Admin denies an action | `audit_events` with actor, IP, details; `events` timeline for findings/alerts | Audit rows are in the same database an admin-level attacker could alter; no external log shipping by default. |
| 9 | I | Inventory exposed to the internet | Three unauthenticated endpoints that return no inventory; trusted-host check; no CORS; OpenAPI/GraphiQL off in production; metrics on a separate unpublished listener | `/api/v1/meta` reveals product name, version and whether setup is pending. |
| 10 | I | Secrets leak through logs or UI | Redacting log processor; tokens shown once; exports exclude secrets; `.env` git-ignored | `.env` is plain text on disk with whatever permissions the operator leaves. Database backups contain password hashes and all inventory. |
| 11 | I | Tunnel provider sees traffic | — | TLS terminates at Cloudflare: the provider can technically read dashboard traffic and session cookies. Accepted trade-off for not opening an inbound port; alternatives in `DOMAIN_AND_AUTH.md`. |
| 12 | I | Local user on `linux-leif` reads data | Ports bound to loopback / internal networks | Any local user can reach `127.0.0.1:8080` (still needs credentials) and, with the `docker-inventory` profile, the container list on `127.0.0.1:2375` without credentials. Members of the `docker` group effectively have root. |
| 13 | D | Ingest flood / oversized batches | Per-token rate limit, list and string caps, bounded live queues, message age limits | No request-body size limit beyond the reverse proxy's. A valid token can still fill the raw table up to its 48 h retention. |
| 14 | D | Expensive GraphQL or history queries | Depth/alias/token limits, page caps, bucket/point caps, authentication required | An authenticated viewer can still issue many moderately expensive queries; there is no per-user API rate limit. |
| 15 | D | Worker or Redis outage | Agents buffer and retry; idempotent replay; status page shows it | Redis without persistence loses queued batches on restart; the agent's buffer (300 batches ≈ 5 min) bounds what can be replayed. |
| 16 | E | Admin (or attacker with admin) turns agents into scanners | Probe targets validated server-side and again on the agent: private, approved CIDR, ICMP or 4 TCP ports only, min intervals, ≤ 20 targets; discovery capped (≤ 1024 addresses, ≤ 10/s, attached subnets only); the agent executes no commands | Within those limits an admin can still learn which approved-range hosts answer ping or accept TCP on 53/80/443/853. |
| 17 | E | Container escape / over-privileged collector | Non-root, read-only rootfs, capabilities dropped, no Docker socket | The collector shares the host network namespace (it must, to see host interfaces). `NET_RAW`, if granted, allows crafting raw ICMP. |
| 18 | E | Viewer performs admin actions | Role checks on every mutating route; GraphQL is read-only | Viewer can read all inventory and findings (intended). |
| 19 | S/T | DNS or zone takeover of `observenet.leafslab.com` | Requires control of the Cloudflare account/zone | Out of the application's control. Protect the registrar and Cloudflare accounts with MFA. |
| 20 | T | Supply chain (Python/npm/base images) | Lockfiles (`uv.lock`, `package-lock.json`), pinned image tags, CI audit jobs | Audits are advisory in CI; images are pinned by tag, not digest. |

## Assumptions

- `linux-leif` itself is trusted and patched; its root user is trusted.
- The home LAN is semi-trusted: agents on the LAN talk to the API over HTTP only on
  loopback; remote agents (e.g. Windows) must use HTTPS through the public hostname or a
  LAN TLS endpoint.
- The operator configures the edge access policy before pointing DNS at the tunnel.

## Out of scope

- Detecting malicious devices or traffic. A "new device" alert is an observation, not a
  verdict.
- Protecting against a malicious admin.
- Physical access to the host.

## Things to do before relying on it remotely

1. Deploy the tunnel + Access policy and run the verification commands in
   `DOMAIN_AND_AUTH.md`.
2. `chmod 600 .env`; keep backups encrypted.
3. Rotate the bootstrap agent token to an expiring one.
4. Decide whether an external log/audit destination is needed.
