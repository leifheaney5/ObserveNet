# Deploying on `linux-leif`

Exact steps to run ObserveNet on the home Linux Docker host. The default result is a
dashboard reachable only at `http://127.0.0.1:8080` on that host. Remote access is a
separate, optional step: `DOMAIN_AND_AUTH.md`.

> Status: these commands describe the intended procedure for the Compose stack in this
> repository. They have **not** been run on `linux-leif` (the build had no access to
> it). The root `README.md` records what was actually executed during the build.

## 1. Prerequisites

- 64-bit Linux with Docker Engine 24+ and the Compose plugin v2.
- `git`, `make`, `openssl` (used by `make init` to generate secrets).
- About 2 GB of free RAM and a few GB of disk for the PostgreSQL volume.
- Outbound internet for image pulls and, by default, the external reachability probes.
- **No inbound router port forward is needed or wanted.**

```bash
docker --version
```

```bash
docker compose version
```

## 2. Get the code

```bash
git clone https://github.com/leifheaney5/ObserveNet.git
```

```bash
cd ObserveNet
```

## 3. Generate configuration and secrets

```bash
make init
```

This writes `.env` from `.env.example` with random values for
`OBSERVENET_SECRET_KEY`, `OBSERVENET_ADMIN_PASSWORD`,
`OBSERVENET_BOOTSTRAP_AGENT_TOKEN` and `POSTGRES_PASSWORD`. There are no default
credentials. Keep the file private:

```bash
chmod 600 .env
```

Settings you may want to review in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `OBSERVENET_BIND` | `127.0.0.1` | Address the web port is published on. Leave on loopback unless you add your own TLS front end. |
| `OBSERVENET_PORT` | `8080` | Local port |
| `OBSERVENET_PUBLIC_URL` | `http://localhost:8080` | Set to `https://observenet.leafslab.com` when the tunnel is used (enables `Secure` cookies and HSTS) |
| `OBSERVENET_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Add `observenet.leafslab.com` for remote access |
| `COMPOSE_PROFILES` | `collector` | Starts the bundled Linux collector |
| `OBSERVENET_DEMO_MODE` | `false` | Synthetic data only; never combine with the collector |

## 4. Start

```bash
docker compose up --build -d
```

Services: `postgres`, `redis`, `migrate` (runs `alembic upgrade head` once and exits),
`api`, `worker`, `scheduler`, `web`, and `collector` (profile `collector`).

```bash
docker compose ps
```

## 5. Health checks

```bash
curl -s http://127.0.0.1:8080/api/v1/health
```

Expected: `{"status":"ok"}`

```bash
curl -s http://127.0.0.1:8080/api/v1/ready
```

Expected: `{"status":"ready","checks":{"database":"ok","redis":"ok"}}`

```bash
curl -s http://127.0.0.1:8080/api/v1/meta
```

Expected: `"demo_mode":false,"setup_required":false`. If `setup_required` is `true`, the
admin password in `.env` was missing or rejected (see step 6).

An unauthenticated data request must be refused:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/v1/devices
```

Expected: `401`

## 6. First login

Open `http://127.0.0.1:8080` on `linux-leif`. From another machine on the LAN, use an
SSH tunnel rather than exposing the port:

```bash
ssh -N -L 8080:127.0.0.1:8080 linux-leif
```

Username: `admin` (or `OBSERVENET_ADMIN_USERNAME`). Password:

```bash
grep '^OBSERVENET_ADMIN_PASSWORD=' .env
```

Change it after logging in (account menu → Change password); you can then remove the
variable from `.env`. If no admin exists:

```bash
make create-admin
```

or

```bash
docker compose exec api python -m observenet.cli create-admin --username admin
```

## 7. Verify the bundled collector

```bash
docker compose logs --tail 20 collector
```

In the UI: Settings → Data sources should list `linux-leif` as **Live** within a few
seconds; Overview should show host NIC throughput for the default-route interface.

What to expect on a fresh real-mode install:

- Host NIC throughput, CPU/memory: yes.
- Gateway latency: yes if ICMP is permitted (next step); otherwise the capability shows
  `unavailable` with the reason.
- Devices: `linux-leif` itself plus whatever is in its neighbour cache (partial).
- WAN throughput and per-device traffic: **Unsupported** (no gateway source).
- Findings: any wildcard listeners on `linux-leif` not on the allowlist (`tcp/22` is
  allowlisted by default).

### Approve your LAN range

Gateway probing with target `auto` works out of the box. To add explicit internal probe
targets or enable discovery, first approve your subnet under Settings → Network scope
(e.g. `192.168.1.0/24`). Only private ranges of /16 or smaller are accepted.

### Enable ICMP for the gateway probe

The collector runs as a non-root user in the host network namespace. Check:

```bash
sysctl net.ipv4.ping_group_range
```

If it prints `1	0`, unprivileged ICMP is off. Enable it persistently:

```bash
echo 'net.ipv4.ping_group_range = 0 2147483647' | sudo tee /etc/sysctl.d/60-observenet-ping.conf
```

```bash
sudo sysctl --system
```

The collector picks it up on its next probe; no restart needed. `compose.yaml` does not
add `NET_RAW`: a capability added to a container is not effective for a non-root process
unless it is made ambient, so the sysctl is the supported route for the bundled collector.

### Optional: Docker published ports

Off by default. To let the collector list published container ports through a
read-only socket proxy, add `docker-inventory` to `COMPOSE_PROFILES` and set
`OBSERVENET_DOCKER_API_URL=http://127.0.0.1:2375` in `.env`, then:

```bash
docker compose up -d
```

See `TELEMETRY_SOURCES.md` for what the proxy exposes to local processes.

### Optional: run the collector natively instead

See `agents/linux/README.md` (systemd unit, unprivileged user). Remove `collector` from
`COMPOSE_PROFILES` if you do.

## 8. Add the Windows PC (optional, untested agent)

Requires the HTTPS hostname (or `localhost`); see `agents/windows/README.md`.

```bash
docker compose exec api python -m observenet.cli create-agent-token --name windows-pc --ttl-days 90
```

Copy the printed token to the PC and follow the Windows README. The Windows agent has
not been executed in the build environment.

## 9. Import the router's client list (optional)

Export or copy the client/DHCP table from the router's admin page as CSV
(`ip,mac,hostname` header) or JSON, then use Devices → Import. From the command line,
an authenticated session and CSRF token are required; the UI is simpler. The import is a
point-in-time snapshot; repeat it when you want fresher names.

## 10. Router telemetry

There is no router adapter yet. WAN throughput, per-device traffic, Wi-Fi signal and
UPnP/port-forward checks stay **Unsupported** regardless of router model. The client
list import above is the only router-derived data.

## 11. Backups

```bash
make backup
```

Creates a `pg_dump` of the database under `backups/` (git-ignored). Equivalent manual
command:

```bash
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > backups/observenet-$(date +%F-%H%M).dump
```

The dump contains inventory, findings, password hashes and token digests. Store it
encrypted and off the host. Also keep a copy of `.env`: without the original
`OBSERVENET_SECRET_KEY`, restored sessions and agent tokens are invalid (users can still
log in with their passwords; agent tokens must be re-issued).

Restore:

```bash
make restore FILE=backups/observenet-2026-01-01-0300.dump
```

Manual equivalent (stop writers first):

```bash
docker compose stop api worker scheduler collector
```

```bash
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < backups/observenet-2026-01-01-0300.dump
```

```bash
docker compose up -d
```

A simple nightly cron entry for the user that owns the checkout:

```
15 3 * * * cd /home/leif/ObserveNet && make backup >/dev/null 2>&1
```

Redis holds only queues, live values and rate-limit counters; it does not need backing
up.

## 12. Updates

```bash
make backup
```

```bash
git rev-parse HEAD
```

(note the commit so you can return to it)

```bash
git pull
```

```bash
docker compose up --build -d
```

The `migrate` service applies new Alembic revisions before `api` starts. Check
`docker compose ps` and the health endpoints afterwards.

## 13. Rollback

Images are built locally from the checkout, so a rollback is "check out the previous
commit and rebuild":

```bash
git checkout <previous-commit>
```

```bash
docker compose up --build -d
```

If the update included a schema migration, restore the pre-update dump (section 11)
rather than relying on Alembic downgrades — they are generated but have not been
exercised.

## 14. Stop / remove

```bash
docker compose down
```

Data stays in the `pgdata` and `redisdata` volumes. To delete everything including data:

```bash
docker compose down -v
```

## 15. Demo mode (separate from real data)

```bash
make demo
```

Runs the stack with `OBSERVENET_DEMO_MODE=true` and no collector. Everything shown is
synthetic and labelled. Do not point demo mode at the database that holds your real
history: when the API next starts in real mode it deletes all demo rows, but real
ingestion is rejected while demo mode is on.

## Troubleshooting

| Symptom | Check |
|---|---|
| `api` exits at startup with `OBSERVENET_SECRET_KEY must be set…` | `.env` missing or not generated; run `make init` |
| `/api/v1/meta` shows `setup_required: true` | `OBSERVENET_ADMIN_PASSWORD` missing, shorter than 12 characters, or a placeholder; fix `.env` and `docker compose up -d api`, or `make create-admin` |
| Login returns 400 "Invalid host header" | The hostname you browse to is not in `OBSERVENET_ALLOWED_HOSTS` |
| Login works but every action returns 403 "CSRF" | Cookies blocked, or `OBSERVENET_PUBLIC_URL` is `https://` while you browse over plain HTTP (the `Secure` cookie is not sent) |
| Collector logs `batch rejected (401)` | `OBSERVENET_BOOTSTRAP_AGENT_TOKEN` differs between `api` and `collector`, or the token was revoked in the UI |
| Collector logs `batch rejected (403)` | The token is bound to a different agent id (collector state reset); issue a new token |
| Collector logs `batch rejected (409)` | The API is in `DEMO_MODE` |
| Collector logs `batch rejected (422)` clock skew | Fix NTP on the host |
| Gateway latency "unavailable" | ICMP not permitted; see "Enable ICMP" |
| Charts empty beyond 48 h at 1 s resolution | Expected: raw retention is 48 h; choose a larger bucket |
| Status page: Worker "no heartbeat" | `docker compose logs worker`; batches queue in Redis until it returns |
| Disk growth | Settings → Retention; `docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c '\dt+'` |

More: `OPERATIONS.md`.
