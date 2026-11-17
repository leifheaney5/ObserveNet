# ADR 0004 — Probes run on the agent; targets are validated on the server and again on the agent

Status: accepted

## Context

Gateway latency must be measured from inside the LAN at 1 Hz. The backend containers sit
on a Docker bridge behind NAT; a 1-second loop does not fit a task queue; and the agent
is already a long-running process at the right vantage point.

But "the backend tells an agent what to connect to" is an SSRF / port-scan primitive if
left open.

## Decision

- Probe targets are rows in `probe_targets`, managed by admins. The agent fetches them
  with `GET /api/v1/agent/config` (every 5 s) and runs one loop per target.
- **Server-side validation** (`services/netscope.py`) on create/update:
  - never loopback, link-local, multicast or unspecified;
  - `gateway`/`internal` scope: literal private address inside an operator-approved
    CIDR (RFC 1918 or IPv6 unique-local space only, ≤ /16);
  - `external` scope: public address or public FQDN (no `.local`, `.lan`, `.internal`);
  - kinds limited to ICMP echo (private only), TCP handshake on ports 53/80/443/853,
    DNS resolution of an FQDN;
  - minimum intervals (1 s internal, 10 s external), at most 20 targets.
- **Agent-side validation** (`observenet_agent/probes.py`) repeats the address, port and
  interval rules before any packet is sent, and reports a refused target as an error
  instead of probing it.
- Probes return only success, latency, method and resolved address. No response content
  is collected.
- The agent executes no commands and accepts no code or scripts from the backend.

## Consequences

- Latency is measured where it matters; the backend needs no LAN reachability.
- Results describe the path from one agent host; other vantage points may differ (this
  is stated in each series' coverage text).
- Within the policy an admin can still learn whether approved-range hosts answer ping or
  accept TCP on four ports. Accepted; the approved-CIDR list is itself admin-controlled
  and audited.
- Two copies of the policy must be kept in sync (server and agent).
