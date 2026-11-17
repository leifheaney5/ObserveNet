# Telemetry sources

What ObserveNet can measure depends entirely on *where* a measurement is taken. A single
ordinary LAN host sees its own traffic and the neighbours it recently talked to — not
the whole network. Network-wide and per-device numbers need the gateway, a managed
switch/AP or a flow exporter. This document says exactly what each source provides and
what stays `Unsupported` until such a source exists.

## Provider model

Every series is stored once in `metric_series` with its provenance:

| Field | Meaning | Values |
|---|---|---|
| `observation_point` | Where the measurement was taken | `host_nic`, `gateway_wan`, `gateway_per_device`, `host`, `active_probe`, `capacity_test` |
| `unit` | Fixed per metric; a batch with the wrong unit is rejected (422) | `bit/s`, `packet/s`, `percent`, `ms`, `bool` |
| `interval_s` | Sample interval the collector actually achieved | 1.0 for NIC counters by default |
| `quality` | How the value came to be | `measured`, `derived`, `synthetic` (demo only; rejected in real mode) |
| `coverage` | Plain-language scope shown in the UI | e.g. "Traffic through this host's own interface only — not network-wide and not per-device." |

Sources (`data_sources`) report **capabilities** (`capabilities` table) with a status of
`supported`, `unsupported`, `unavailable` or `disabled` and a detail string. The
network-level capability matrix (`GET /api/v1/capabilities`) is computed from those rows
and from which series actually received data in the last 10 minutes.

Gateway integrations implement `RouterTelemetryProvider`
(`backend/observenet/providers/base.py`): capability negotiation over
`device_inventory`, `dhcp_leases`, `arp_table`, `wan_interface_counters`,
`wifi_clients_signal`, `per_device_traffic`, `flow_records`,
`router_settings_readonly`. A provider raises `UnsupportedCapability` instead of
returning invented data.

## Metrics that must not be confused

| Concept | ObserveNet metric | Source required | Status in this repository |
|---|---|---|---|
| **Host NIC traffic** — bytes through one host's own interface | `net.rx_bps`, `net.tx_bps`, `net.rx_pps`, `net.tx_pps` (label = interface) | Agent on that host | Implemented (Linux agent; Windows agent emits rx/tx bps, untested) |
| **WAN throughput** — traffic crossing the gateway's WAN port | `wan.rx_bps`, `wan.tx_bps` | Gateway WAN interface counters | **Unsupported**: metric names and UI handling exist, no real provider emits them. Demo mode emits synthetic values. |
| **Per-device traffic** | `device.rx_bps`, `device.tx_bps` (label = MAC) | Per-client accounting on the gateway, or flow export | **Unsupported** in real mode. Demo only. |
| **LAN traffic** between other devices | — | Managed switch / flow export | Not measured. Host NIC series cover only the agent host's share. |
| **Wi-Fi PHY link rate / signal** | — | AP or controller provider | Unsupported. Never derived from anything else. |
| **Interface link speed** (negotiated, e.g. 1000 Mbit/s) | `interfaces.link_speed_mbps` (inventory attribute, not a series) | Agent | Implemented; labelled as link rate, not throughput. |
| **ISP advertised speed** | — | Operator knowledge | Not stored and never shown as a measurement. |
| **Tested internet capacity** | `speedtest.download_bps` (`observation_point = capacity_test`) | Opt-in capacity test on an agent | Implemented, off by default; download only. Upload capacity: unsupported. |
| **Gateway latency / loss / jitter** | `probe.latency_ms`, `probe.success`, `probe.jitter_ms` (label = `target:<id>`) | Agent probes | Implemented |
| **Host resources** | `host.cpu_pct`, `host.mem_pct` | Agent | Implemented (Linux) |

The Overview never substitutes host NIC numbers for WAN numbers. Without a gateway
source the WAN cards read "Unsupported" with setup guidance.

## Rate derivation

```
bits_per_second = 8 × max(0, counter_delta) / elapsed_seconds
```

`elapsed_seconds` comes from a monotonic clock (`time.monotonic()` on Linux, a
`Stopwatch` in the Windows agent), so wall-clock jumps cannot distort rates.
Implementation: `agents/linux/observenet_agent/rates.py`.

| Situation | Behaviour |
|---|---|
| First reading of a counter | No sample (nothing to subtract from) |
| Counter decreased, 64-bit counter | Treated as a reset (driver reload, interface re-created). No sample; re-baseline. |
| Counter decreased, 32-bit counter | Assumed to have wrapped once: `delta = cur + 2^32 − prev`. (The Linux agent reads 64-bit kernel counters; the 32-bit path exists for sources that expose 32-bit counters.) |
| `elapsed ≤ 0` | No sample |
| `elapsed > 3 × interval` (samples were missed) | No sample; re-baseline. An average over the gap is not a 1-second sample. |
| Interface disappeared | Tracker state is dropped so a re-created interface starts clean |

A missing sample is a gap in the chart (`avg: null`). It is never filled with zero or an
interpolation.

## Source: Linux agent

Code: `agents/linux/observenet_agent/`. Runs as an unprivileged process (container with
host networking, or a systemd service).

| Data | Method | Interval | Notes |
|---|---|---|---|
| NIC rx/tx bytes and packets | `psutil.net_io_counters(pernic=True)` (reads `/proc/net/dev`) | 1 s | Interfaces starting with `lo,veth,ifb,dummy` are skipped by default (`OBSERVENET_EXCLUDE_INTERFACES`). |
| CPU %, memory % | psutil | 1 s | Collector health context |
| Interfaces, addresses, MAC, link speed, MTU | psutil | 30 s | |
| Default gateway | `/proc/net/route` | 30 s | IPv4 default route with the lowest metric |
| Neighbour sightings | `/proc/net/arp` (IPv4 only) | 30 s | **Weak discovery signal.** Only devices this host recently exchanged packets with. Entries can outlive a departed device by minutes. Neighbours on virtual bridges (docker, veth, …) are ignored. |
| Listening TCP / bound UDP sockets | `/proc/net/{tcp,tcp6,udp,udp6}` | 300 s and on verify request | Process names only for sockets the agent's user may inspect. In a container with host networking but its own PID namespace, names are usually unavailable; the socket list is still complete for the host network namespace. |
| Docker published ports | Docker Engine API `GET /containers/json` through a read-only socket proxy | with posture | **Opt-in**, see below |
| Gateway / internal ICMP latency | Unprivileged datagram ICMP socket, else raw socket | per target (default 1 s) | Needs `net.ipv4.ping_group_range` to include the agent's group, or `CAP_NET_RAW`. Otherwise reported `unavailable` with the reason; an unavailable prober is not counted as packet loss. |
| External reachability | TCP handshake to an allowlisted port; DNS lookup via the system resolver | per target (default 30 s) | No application data is sent. |

Not collected: packet contents, flows, other hosts' traffic, IPv6 neighbours, Wi-Fi
signal, firewall rules.

## Source: Windows agent (optional)

Code: `agents/windows/observenet-agent.ps1`. **Written but never executed in the build
environment** (no Windows host was available). Treat it as untested until you run it.

Designed to report: NIC counters (`Get-NetAdapterStatistics`) as `net.rx_bps` /
`net.tx_bps`, firewall profile state (`Get-NetFirewallProfile`), SMBv1/SMBv2 server
setting (`Get-SmbServerConfiguration`, registry fallback), listening TCP/UDP endpoints,
heartbeat. No probes, no neighbour table, no admin requirement; sections that cannot be
read are sent as `unavailable` with the reason.

## Source: router client-list import

Code: `backend/observenet/providers/client_import.py`; UI: Devices → Import;
API: `POST /api/v1/devices/import`.

The vendor-neutral inventory path. Paste or upload the client/DHCP table from any
router's admin page as:

- CSV with a header row. Recognised columns (case-insensitive): IP — `ip`,
  `ip_address`, `address`, `ipv4`, `ip address`; MAC — `mac`, `mac_address`, `hwaddr`,
  `hardware_address`, `mac address`; name — `hostname`, `host`, `name`, `device`,
  `device_name`, `client`, `device name`.
- JSON: an array of objects with the same keys, or an object with a `clients`,
  `devices` or `leases` array.

Limits: 2000 rows; only private LAN addresses are accepted, other rows are reported as
row errors. Quality is `point_in_time`: presence is "as of import". It provides
hostnames and MACs for identity matching and nothing else — no counters, signal or
settings.

## Gateway providers: status

| Path | Status | Would provide | Would not provide |
|---|---|---|---|
| Client-list import | **Implemented** | Device inventory (point in time) | Anything live |
| SNMPv3 IF-MIB (read-only) | **Not implemented.** No SNMP code exists in this repository. | WAN interface octet counters (`ifHCInOctets`/`ifHCOutOctets`) → `wan.rx_bps`/`wan.tx_bps`; interface up/down | Per-device utilisation (generic interface counters cannot attribute traffic to clients), Wi-Fi signal, UPnP/port-forward settings |
| UniFi Network controller | Not implemented (listed as a planned adapter) | Inventory, Wi-Fi clients/signal, per-client traffic | — |
| OPNsense / pfSense | Not implemented (planned) | DHCP leases, ARP table, WAN counters, some read-only settings | — |
| NetFlow / IPFIX / sFlow | Not implemented (planned) | Flow records, per-device traffic | — |

Planned adapters are listed by `GET /api/v1/capabilities` with
`status: "not_implemented"` and the reason. An adapter will only be added against a
public, documented interface that can be tested; none was available during this build.

When SNMP is added, the intended design is to poll from the on-prem collector and keep
SNMPv3 credentials on that host only (never in the backend database).

## Probe policy

Probes run on the agent; targets are defined on the backend and validated twice
(ADR 0004).

Defaults seeded in real mode (`backend/observenet/api/app.py`):

| Target | Kind | Interval | Timeout |
|---|---|---|---|
| Default gateway (`auto` = the agent's default route) | `gateway_icmp` | 1 s | 900 ms |
| 1.1.1.1:443 | `tcp` | 30 s | 3 s |
| 9.9.9.9:443 | `tcp` | 30 s | 3 s |
| `example.com` | `dns` (system resolver) | 30 s | 3 s |

Rules enforced by `services/netscope.py` and again by the agent (`probes.py`):

- Loopback, link-local (including 169.254.169.254), multicast and unspecified addresses
  are never targets.
- `gateway`/`internal` targets must be literal private addresses inside an **approved
  CIDR** (Settings → Network scope). Approved CIDRs must lie in RFC 1918 space
  (10/8, 172.16/12, 192.168/16) or IPv6 unique-local space (fc00::/7), /16 or smaller
  (IPv6: /48 or smaller). CGNAT, documentation and other "non-global" ranges are
  refused.
- ICMP is limited to private addresses. External checks are TCP handshakes or DNS.
- TCP ports are limited to 53, 80, 443, 853.
- External targets may not be probed more often than every 10 s; at most 20 targets.
- All default targets can be disabled or replaced. The two public resolvers receive one
  TCP handshake every 30 s each and nothing else.

Derived statistics (`services/connectivity.py`):

- **Loss %** = unanswered probes / probes in the window. An empty window is `unknown`,
  not 0 %.
- **Jitter** = mean absolute difference between consecutive successful round-trip
  times; lost probes are skipped.
- Every figure is shown with its window and sample size.
- **Internet status** combines the external signals: `unreachable` requires at least two
  independent signals that each failed three times in a row. One failing endpoint is
  `degraded`, never an outage. A gateway that does not answer ICMP is reported as "not
  responding (may filter ICMP)".

## Opt-in: active discovery

Off by default (Settings → Collection). When enabled the agent sends ICMP echo requests
to each address of the configured ranges so the kernel neighbour cache fills in; results
surface through the normal neighbour collector.

- IPv4 only, at most 4 ranges, at most 1024 addresses each, each inside an approved
  CIDR.
- The agent additionally refuses any range that is not a subnet of a network the host
  is directly attached to.
- Rate limit ≤ 10 probes/s (default 2/s); interval 15 min – 24 h (default 60 min).
- Disable by turning the setting off; the agent picks it up within seconds.

## Opt-in: capacity test

Off by default. When enabled the agent downloads from an operator-chosen **HTTPS** URL
until `max_bytes` (default 25 MB) or 15 s, and reports
`speedtest.download_bps = 8 × bytes / elapsed`.

- Scheduled no more often than every 3 h (default 6 h); an on-demand request is honoured
  at most once per 10 minutes.
- Single stream to one server: a lower bound on capacity, labelled as such.
- Stored as its own series with `observation_point = capacity_test`; never mixed into
  utilisation charts.
- Upload capacity is not measured.

## Opt-in: Docker inventory

The web-facing containers never mount `/var/run/docker.sock`. Docker published ports are
collected only if you enable the `docker-inventory` Compose profile, which runs a
socket proxy exposing only the read-only container list endpoint on `127.0.0.1:2375`,
and set `OBSERVENET_DOCKER_API_URL=http://127.0.0.1:2375` for the collector. Without it
the `docker` posture section is `disabled` and rule `DOCKER-PORT-001` reports
`unsupported`.

Limitation: the proxy still reveals container names, images and port mappings to any
local process that can reach 127.0.0.1:2375 on the host.

## Device identity

Implemented in `backend/observenet/services/identity.py`.

| Evidence | Identity key | Confidence |
|---|---|---|
| Agent self-report | `agent_uid` | high |
| Globally administered MAC | `mac` | medium (high when self-reported by an agent) |
| Locally administered MAC (often randomized) | `mac`, flagged `mac_randomized` | low; never merged automatically |
| IP only | `ip_only`, scoped to the reporting source | low |

- Same MAC with a new IP (DHCP change): same asset, new address observation.
- Same IP with a different MAC: a different asset.
- The only automatic merge: an agent's self-reported physical NIC MAC equals an existing
  unmanaged sighting. It is recorded in `asset_merges` and can be undone.
- Manual merge/unmerge is available and audited.

### MAC vendor lookup

No vendor table is bundled. If you want vendor names, download the IEEE MA-L registry
CSV (`oui.csv`, columns `Assignment` and `Organization Name`) from the IEEE Registration
Authority, mount it into the API and worker containers and set
`OBSERVENET_OUI_FILE=/path/to/oui.csv`. Vendor is then derived from the first three
octets of globally administered MACs and stored with `vendor_method =
ieee-oui-registry`. Without the file, vendor is unknown.
