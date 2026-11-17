# ObserveNet product specification

**ObserveNet — Know Your Network.** A self-hosted dashboard for a home network: what is
on it, how it is performing, and whether the hosts you manage are configured the way you
intend. Security posture is one part of the product, not the whole product.

ObserveNet is an independent project. Its Find → Fix → Verify emphasis is loosely
inspired by ideas popularised by Horizon3.ai; it is not affiliated with Horizon3.ai, does
not replicate NodeZero, and never claims to prove exploitability.

## Principles

1. **No fake observability.** In real mode a value that cannot be measured is shown as
   `Unknown`, `Unavailable` or `Unsupported` with the reason. Synthetic data exists only
   in `DEMO_MODE` and is labelled everywhere.
2. **Every number has provenance.** Value, unit, timestamp, source and observation point.
3. **Read-only.** ObserveNet observes. It does not scan aggressively, exploit, change
   router/firewall settings, or run commands on hosts.
4. **Evidence before conclusions.** A finding states what was observed, what is merely
   inferred, and how it was collected. "Resolved" requires fresh evidence.

## Navigation and pages

| Page | Shows | Backend |
|---|---|---|
| **Overview** | KPI cards (known devices, WAN down/up, host NIC rx/tx, gateway latency, packet loss, open findings), live utilisation and latency chart, per-device traffic card (top talkers *or* the "requires gateway integration" explanation), recent device events, service health, active alerts, posture summary. Period selector 1m / 15m / 1h / 24h / 7d. | `GET /overview`, `GET /metrics/history`, `WS /stream`, `GET /status` |
| **Devices** | Inventory table: name, type, IP, MAC, vendor (if known), presence, confidence, last seen + source, open findings. Search and filters. Detail view: identities, address history, interfaces, observed services, series, findings, alerts, events, merge history. Edit alias/type/group/tags/notes/owner/favorite. Manual merge and unmerge. Import router client list. Export CSV/JSON. | `GET/PATCH /devices`, `/devices/{id}/merge`, `/devices/import`, `/export/devices`, GraphQL `devices`/`device` |
| **Traffic** | Host NIC throughput per interface, WAN throughput when a gateway source exists, long-range history from rollups, comparison periods, top devices when supported. Capacity-test results are shown separately from utilisation. | `GET /metrics/series`, `/metrics/history`, `/traffic/top-devices` |
| **Connectivity** | Per-target latency, loss, jitter with window and sample size; multi-signal internet status; outage list; Wi-Fi signal marked Unsupported. | `GET /connectivity/summary`, `/metrics/history` |
| **Security** | Findings list with severity, classification and verification status; finding detail with evidence timeline, remediation steps, verify button, before/after diff; rule catalog including Unsupported/Not evaluated rules; assessment history. | `GET /findings`, `/findings/{id}`, `POST /findings/{id}/verify`, `/findings/{id}/status`, `/checks`, `/assessments` |
| **Alerts** | Active and resolved alerts, acknowledge / snooze / close, rule thresholds, maintenance windows, event timeline. | `GET /alerts`, `/alert-rules`, `/maintenance-windows`, `/events` |
| **Insights** | Plain comparisons (today vs. yesterday latency and traffic, busiest hour, co-occurrence of high traffic and elevated latency, new/quiet devices, service changes), each with its method and sample sizes; a list of insights that could not be computed and why. | `GET /insights` |
| **Settings** | Data sources and capability matrix, agent tokens, probe targets, collection intervals, retention, approved network scope, discovery and capacity test opt-ins, posture allowlists, users, audit log, domain/deployment facts. | `GET/PUT /settings`, `/sources`, `/tokens`, `/probe-targets`, `/users`, `/audit`, `/capabilities`, `/settings/deployment` |

## Header

Logo, live indicator (Live / Stale / Disconnected / Demo), last refresh time, period
selector, settings link, account menu (role, change password, sign out).

## States every view must handle

| State | Meaning | Presentation |
|---|---|---|
| Loading | Request in flight | Skeletons, not spinners over stale numbers |
| Empty (real mode, new install) | Nothing measured yet | Explanation of what will populate the view and how (start an agent, import a client list) |
| Live | Samples within the freshness window (default 10 s) | Normal |
| Stale | Last sample older than the stale threshold | Value kept, visibly marked with its age |
| Disconnected | Last sample older than the disconnect threshold (default 90 s) or WebSocket down | Value removed, "source not reporting" + last timestamp; stream reconnects with backoff |
| Unsupported | No source can provide this measurement | Card with the reason and setup guidance; never a zero or an estimate |
| Unavailable | A source should provide it but currently cannot (permissions, error) | Reason from the collector |
| Permission | Viewer role on an admin action | Control disabled with explanation; API returns 403 |
| Demo | `OBSERVENET_DEMO_MODE=true` | Persistent banner on every page; synthetic sources flagged |

## Presence vocabulary

ObserveNet does not label unmanaged devices "online/offline". Presence is
source-dependent:

- **Agent reporting / Agent delayed / Agent silent** — hosts running an agent.
- **Seen recently** — passive sighting within the window (default 10 min).
- **Not seen recently** — no passive sighting; explicitly *not* proof of being offline.
- **Unknown** — never observed by a live source (e.g. only imported).

## Finding vocabulary

- Classification: `OBSERVED_CONFIGURATION`, `POTENTIAL_EXPOSURE`, `VERIFIED_RESOLUTION`.
  There is no `PROVEN_EXPLOITABLE`.
- Status: `open`, `acknowledged`, `snoozed`, `resolved` (engine only, after a recheck),
  `closed_unverified` (operator closed it without a recheck; shown as such).
- Verification: `not_requested`, `pending`, `resolved`, `still_observed`, `unknown`.

## Roles

- **admin** — everything.
- **viewer** — read-only; all mutations return 403.

## Non-goals

- Exploitation, attack-path inference, credential testing, intrusive or internet-wide
  scanning, packet capture, traffic interception.
- Remote control: reboots, router or firewall changes, blocking devices, killing
  services. Not implemented and not planned for the default product; any future version
  would be a separate, approval-gated feature.
- Claiming a complete device inventory or per-device bandwidth without an authoritative
  source.
- Multi-tenant or multi-site operation, high availability.
- An opaque "risk score".
