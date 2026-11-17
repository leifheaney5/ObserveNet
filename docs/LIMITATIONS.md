# Limitations

Blunt and in priority order. "Not implemented" means there is no code; "untested" means
code exists but was never run where it matters.

## P0 — things that change what the dashboard can tell you

1. **No WAN throughput in real mode.** `wan.rx_bps` / `wan.tx_bps` need counters from
   the gateway. No gateway provider exists, so the WAN cards read *Unsupported*. Host
   NIC throughput is a different metric and is never substituted.
2. **No per-device traffic in real mode.** Attributing bandwidth to devices needs
   per-client accounting or flow export from the router/switch/AP. Not implemented. The
   "top devices" view exists only for sources that provide `device.*` series, which
   today is the demo generator.
3. **Device inventory is partial.** It comes from the IPv4 neighbour cache of hosts
   running an agent (devices they recently exchanged packets with), the agents
   themselves, and optional client-list imports. Devices that never talk to an agent
   host are invisible. IPv6-only neighbours are not read. Neighbour entries linger for
   minutes after a device leaves, so "last seen" is approximate.
4. **No router adapter of any kind.** No SNMP (v3 or otherwise), no UniFi, no
   OPNsense/pfSense, no flow collector. Only the provider interface, a null provider and
   the client-list import exist. Consequently Wi-Fi clients/signal, DHCP leases as a
   live source, and UPnP/port-forward checks (`RTR-UPNP-001`) are *Unsupported*.
5. **The public hostname is not deployed.** `https://observenet.leafslab.com` is a
   documented procedure (`DOMAIN_AND_AUTH.md`), not a running endpoint. No DNS, tunnel
   or Access policy was created or verified.
6. **Not run on `linux-leif`.** Everything was built and exercised in a build sandbox,
   not on the target host or network.

## P1 — untested or thin areas

7. **Windows agent is untested.** `agents/windows/observenet-agent.ps1` was never
   executed or parsed by PowerShell (no Windows, no interpreter in the build). Only a
   hand-written sample payload was validated against the backend schema. It also needs
   an HTTPS backend URL, so a LAN PC cannot use it until the hostname (or another TLS
   endpoint) exists.
8. **Windows listener rule needs tuning.** `WIN-LISTEN-001` flags wildcard listeners
   that are not on the Windows allowlist. The default allowlist covers the usual
   built-in Windows services (RPC endpoint mapper, SMB/NetBIOS, mDNS/LLMNR/SSDP,
   WS-Discovery, Delivery Optimization) but was chosen without a real Windows host to
   validate it; expect extra low-severity findings for vendor software and dynamic RPC
   ports until you adjust it in Settings → Security posture.
9. **Process names are often missing** for listening sockets: the collector runs
   unprivileged (and, in the container, in its own PID namespace). Ports and bind
   addresses are complete; owners are not.
10. **Posture checks are few and shallow.** Linux: wildcard listeners and (opt-in)
    Docker published ports. Windows: firewall profiles, SMBv1, wildcard listeners.
    Nothing about patches, accounts, firewall rules, TLS, or services' actual
    configuration. Specific-address binds (e.g. a service bound to the LAN IP) are not
    flagged.
11. **Findings describe configuration, not reachability.** ObserveNet never connects to
    a flagged service, so it cannot say whether a firewall already blocks it. A host
    that lies about itself (compromised agent) is believed.
12. **Neo4j projection has no automated test.** The adapter
    (`services/graph_sync.py`, ADR 0006) was verified by hand against a real Neo4j
    container during the build, but CI has no Neo4j service, so regressions would not be
    caught. It projects only the small observed topology; it adds little until a gateway
    provider supplies richer relationships.
12a. **The real-mode "fixed → Resolved" transition was not exercised live.** On real
    data the build ran Verify against an existing finding and got *Still observed* from
    fresh evidence (about 5 s after the request). The *Resolved* path is covered by the
    backend tests and by the end-to-end test in demo mode, which uses the same engine;
    changing a real host's configuration to watch it flip was not done in the build
    sandbox.
13. **systemd unit untested.** `agents/linux/observenet-agent.service` was written for
    the native install but never run (no systemd in the build environment).
14. **CI workflow never ran on GitHub.** `.github/workflows/ci.yml` is unverified
    there.
15. **Alembic downgrades are unexercised.** Roll back with a database restore.
16. **Capacity test is download-only and single-stream** to one operator-chosen URL; it
    is a lower bound, not an ISP speed measurement. Upload is not measured.
17. **Gateway ICMP depends on host configuration.** Without
    `net.ipv4.ping_group_range` (or `CAP_NET_RAW` made effective) the gateway latency
    probe is `unavailable`. A gateway that filters or rate-limits ICMP looks lossy.

## P2 — architectural limits

18. **Single node, no high availability.** One host, one PostgreSQL, one Redis. If
    `linux-leif` is down, nothing is monitored and nothing alerts.
19. **PostgreSQL as the time-series store.** No compression; rollups keep
    avg/min/max/sum/count only (no percentiles beyond raw retention); sized for a
    handful of agents at 1 Hz. Not load-tested.
20. **Queue durability.** Queued batches live in Redis. If Redis loses its data before
    the worker drains it, those batches are gone unless still in an agent's 300-batch
    buffer (~5 minutes).
21. **Notifications are in-app only**, plus an optional signed webhook. No email, push
    or chat integration. Maintenance windows suppress new alerts; they do not queue
    them.
22. **Authentication is local accounts only.** No OIDC/SSO, no MFA, no account lockout
    beyond per-IP rate limiting. Sessions are not bound to IP/device. The design relies
    on the edge access policy for stronger identity.
23. **Two roles only** (admin, viewer). No per-device or per-site permissions.
24. **No encryption at rest** beyond what the host's disk provides. Backups are plain
    `pg_dump` files containing inventory and password hashes.
25. **Vendor names need an operator-supplied IEEE OUI file.** Nothing is bundled; by
    default vendor is unknown.
26. **Identity reconciliation is heuristic.** MAC randomization creates extra
    low-confidence entries that must be merged by hand; the one automatic merge
    (agent NIC MAC = existing sighting) can be wrong on unusual setups (it is logged and
    undoable).
27. **Insights are simple comparisons** (day-over-day means, a co-occurrence count).
    No anomaly detection, no seasonality, and correlation is explicitly not causation.
28. **CSV export hardening is minimal.** Cells that start with `=`, `+`, `-`, `@`, tab
    or CR are prefixed with `'` so spreadsheets do not evaluate them; nothing else is
    sanitised.
29. **Outage detection needs at least two external signals** that each fail three times
    in a row; short outages (under ~90 s with default 30 s checks) are not recorded as
    outages.
30. **Demo mode makes unsupported capabilities look supported** (WAN, per-device). The
    banner and `synthetic` flags are the only safeguards against misreading a demo
    screenshot.

## Deliberately out of scope

Exploitation, attack paths, scanning of other devices, packet capture, remote control
of devices or the router, multi-site/multi-tenant operation.

## Next steps, in order

1. Run on `linux-leif`; fix whatever the real network exposes.
2. Gateway source: SNMPv3 IF-MIB WAN counters polled from the collector, tested against
   a simulator and the actual router.
3. Run the Windows agent on a real PC; fix and add a default Windows allowlist.
4. Deploy tunnel + Access; run the verification checklist; record the results.
5. Per-device traffic through whichever router integration the actual hardware
   supports.
6. Restore drill, load test, and CI on GitHub.
