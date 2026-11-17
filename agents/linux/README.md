# ObserveNet Linux collector

A small, read-only Python process that reports what one Linux host can observe about
itself and its immediate network. It reads `/proc` and `psutil`, sends ICMP echo / TCP
handshakes / DNS lookups to a short validated target list, and uploads one batch per
second to the ObserveNet backend. It never changes the host and executes nothing the
backend sends.

## What it collects

| Data | How | Default interval |
|---|---|---|
| NIC throughput per interface (`net.rx_bps`, `net.tx_bps`, `net.rx_pps`, `net.tx_pps`) | cumulative counters → deltas over a monotonic clock | 1 s |
| CPU / memory % | psutil | 1 s |
| Interfaces, addresses, default gateway | psutil, `/proc/net/route` | 30 s |
| Neighbour sightings (IPv4) | `/proc/net/arp` | 30 s |
| Listening TCP / bound UDP sockets | `/proc/net/{tcp,tcp6,udp,udp6}` | 300 s, and when the backend requests a recheck |
| Probe results | ICMP echo, TCP handshake, DNS lookup — targets come from the backend and are re-validated locally | per target |
| Docker published ports | **opt-in**, via a read-only socket proxy | with posture |

Limits worth knowing:

- It measures **this host's** traffic only. It cannot see other devices' bandwidth or
  WAN throughput.
- The neighbour cache lists only devices this host recently talked to.
- Process names for listening sockets are available only for processes the agent's user
  may inspect. As an unprivileged user (or in a container with its own PID namespace)
  most names are missing; ports and bind addresses are still complete.
- ICMP needs either `net.ipv4.ping_group_range` to include the agent's group or
  `CAP_NET_RAW`. Without either the gateway probe reports `unavailable` with the reason;
  it is not counted as packet loss.

Rate formula and reset/gap policy: `observenet_agent/rates.py` and
`docs/TELEMETRY_SOURCES.md`.

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `OBSERVENET_URL` | `http://127.0.0.1:8080` | Backend base URL |
| `OBSERVENET_AGENT_TOKEN` | — | Collector bearer token (`onet_…`) |
| `OBSERVENET_AGENT_TOKEN_FILE` | — | File containing the token (preferred over the variable) |
| `OBSERVENET_AGENT_STATE_DIR` | unset | Directory for the persisted random `agent_uid`. If unset or unwritable the id is derived from a hash of `/etc/machine-id`. |
| `OBSERVENET_AGENT_HOSTNAME` | system hostname | Name shown in the UI |
| `OBSERVENET_EXCLUDE_INTERFACES` | `lo,veth,ifb,dummy` | Interface name prefixes to skip |
| `OBSERVENET_DOCKER_API_URL` | unset (disabled) | Read-only Docker API proxy, e.g. `http://127.0.0.1:2375` |
| `OBSERVENET_TLS_VERIFY` | `true` | Set `false` only for a private CA you cannot install |
| `OBSERVENET_ALLOW_PLAINTEXT` | unset | Must be `1` to send the token over `http://` to a non-loopback host. Avoid. |
| `OBSERVENET_LOG_LEVEL` | `INFO` | |

The agent exits at startup if no token is configured, or if the URL is plain HTTP to a
non-loopback host without `OBSERVENET_ALLOW_PLAINTEXT=1`.

## Option A — bundled container (default)

`docker compose up -d` starts the `collector` service when `COMPOSE_PROFILES=collector`
is set in `.env` (the default written by `make init`). It runs with host networking,
as a non-root user, with a read-only root filesystem and all capabilities dropped, and
posts to `http://127.0.0.1:${OBSERVENET_PORT:-8080}` using
`OBSERVENET_BOOTSTRAP_AGENT_TOKEN`.

```bash
docker compose ps collector
```

```bash
docker compose logs -f collector
```

## Option B — native systemd service

Use this if you prefer not to run the collector in a container, or want process names
for sockets owned by the service user. Requires Python 3.11+. The unit file has not
been run in the build environment; review it first.

Create the user and install the package into a virtualenv:

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin observenet-agent
```

```bash
sudo python3 -m venv /opt/observenet-agent/venv
```

```bash
sudo /opt/observenet-agent/venv/bin/pip install /path/to/ObserveNet/agents/linux
```

Issue a dedicated token (Settings → Tokens in the UI, or):

```bash
docker compose exec api python -m observenet.cli create-agent-token --name linux-leif-native --ttl-days 90
```

Store it and the configuration:

```bash
sudo install -d -m 0750 -o root -g observenet-agent /etc/observenet-agent
```

```bash
sudo sh -c 'umask 027; cat > /etc/observenet-agent/token'   # paste the token, then Ctrl-D
```

```bash
sudo chgrp observenet-agent /etc/observenet-agent/token
```

```bash
sudo tee /etc/observenet-agent/agent.env >/dev/null <<'ENV'
OBSERVENET_URL=http://127.0.0.1:8080
OBSERVENET_AGENT_TOKEN_FILE=/etc/observenet-agent/token
ENV
```

Install and start the unit:

```bash
sudo install -m 0644 /path/to/ObserveNet/agents/linux/observenet-agent.service /etc/systemd/system/
```

```bash
sudo systemctl daemon-reload
```

```bash
sudo systemctl enable --now observenet-agent
```

```bash
journalctl -u observenet-agent -f
```

If you run the native agent, disable the bundled one (remove `collector` from
`COMPOSE_PROFILES` in `.env`), otherwise the same host reports twice under two agent
ids. A token is bound to the first agent that uses it, so the native agent needs its
own token.

## Enabling ICMP for the gateway probe

Check the current setting (it is per network namespace; with host networking the host's
value applies):

```bash
sysctl net.ipv4.ping_group_range
```

`1 0` means unprivileged ICMP is disabled. Many current distributions already ship
`0 2147483647`. To allow it for all groups, persistently:

```bash
echo 'net.ipv4.ping_group_range = 0 2147483647' | sudo tee /etc/sysctl.d/60-observenet-ping.conf
```

```bash
sudo sysctl --system
```

Alternative for the native service: grant `CAP_NET_RAW` as an ambient capability (see
the comment in the unit file). For the non-root container, prefer the sysctl: a
capability added to a container is not effective for a non-root process unless it is
made ambient.

## Development

```bash
uv sync
```

```bash
uv run pytest
```

```bash
uv run ruff check . && uv run mypy observenet_agent
```

Run against a local backend:

```bash
OBSERVENET_URL=http://127.0.0.1:8080 OBSERVENET_AGENT_TOKEN=onet_... uv run python -m observenet_agent
```
