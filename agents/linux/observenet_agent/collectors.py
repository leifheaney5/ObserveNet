"""Read-only host collectors. Each returns data plus an explicit status; none needs root."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import psutil

from observenet_agent.procnet import (
    ListeningSocket,
    parse_default_gateway,
    parse_proc_net_arp,
    parse_proc_net_sockets,
)

PROC = Path("/proc")


@dataclass
class Section:
    status: str  # ok | unavailable | unsupported | disabled
    reason: str | None = None
    method: str = ""
    items: list[Any] = field(default_factory=list)


def nic_counters() -> dict[str, tuple[int, int, int, int]]:
    """iface -> (rx_bytes, tx_bytes, rx_packets, tx_packets), cumulative since boot."""
    return {
        name: (c.bytes_recv, c.bytes_sent, c.packets_recv, c.packets_sent)
        for name, c in psutil.net_io_counters(pernic=True).items()
    }


def interfaces() -> list[dict[str, Any]]:
    stats = psutil.net_if_stats()
    out = []
    for name, addrs in psutil.net_if_addrs().items():
        mac = None
        addresses = []
        for a in addrs:
            if a.family == psutil.AF_LINK:
                mac = a.address.lower() if a.address and a.address != "00:00:00:00:00:00" else None
            elif a.family in (socket.AF_INET, socket.AF_INET6):
                address = a.address.split("%", 1)[0]
                prefix = None
                if a.netmask:
                    try:
                        if a.family == socket.AF_INET:
                            prefix = sum(bin(int(o)).count("1") for o in a.netmask.split("."))
                        else:
                            prefix = sum(bin(int(h, 16)).count("1") for h in a.netmask.split(":") if h)
                    except ValueError:
                        prefix = None
                addresses.append({"family": "ipv4" if a.family == socket.AF_INET else "ipv6", "address": address,
                                  "prefix_len": prefix})  # fmt: skip
        st = stats.get(name)
        out.append({"name": name, "mac": mac, "is_up": st.isup if st else None,
                    "link_speed_mbps": (st.speed or None) if st else None, "mtu": st.mtu if st else None,
                    "addresses": addresses})  # fmt: skip
    return out


def default_gateway() -> tuple[str, str] | None:
    try:
        return parse_default_gateway((PROC / "net/route").read_text())
    except OSError:
        return None


def neighbors() -> Section:
    """IPv4 neighbour cache: only devices this host recently exchanged packets with.
    A weak discovery signal — not a complete inventory."""
    try:
        text = (PROC / "net/arp").read_text()
    except OSError as exc:
        return Section("unavailable", f"cannot read /proc/net/arp: {exc.strerror}")
    return Section("ok", method="/proc/net/arp (IPv4 neighbour cache)", items=parse_proc_net_arp(text))


def _process_names() -> dict[tuple[str, int], str]:
    """(proto, port) -> process name, for sockets whose owner this user may inspect."""
    names: dict[tuple[str, int], str] = {}
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError, OSError):
        return names
    for c in conns:
        if not c.laddr or c.pid is None:
            continue
        proto = "tcp" if c.type == socket.SOCK_STREAM else "udp"
        if proto == "tcp" and c.status != psutil.CONN_LISTEN:
            continue
        try:
            names[(proto, c.laddr.port)] = psutil.Process(c.pid).name()[:120]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return names


def listening_sockets() -> Section:
    """Listening TCP / bound UDP sockets from the kernel socket table of this network namespace."""
    found: list[ListeningSocket] = []
    read_any = False
    for fname, proto, v6 in (
        ("tcp", "tcp", False),
        ("tcp6", "tcp", True),
        ("udp", "udp", False),
        ("udp6", "udp", True),
    ):
        try:
            text = (PROC / "net" / fname).read_text()
        except OSError:
            continue
        read_any = True
        found += parse_proc_net_sockets(text, proto=proto, ipv6=v6)
    if not read_any:
        return Section("unavailable", "cannot read /proc/net/{tcp,udp}")
    names = _process_names()
    unique: dict[tuple[str, str, int], ListeningSocket] = {}
    for s in found:
        unique[(s.proto, s.address, s.port)] = ListeningSocket(
            s.proto, s.address, s.port, names.get((s.proto, s.port))
        )
    named = sum(1 for s in unique.values() if s.process)
    return Section(
        "ok",
        method=f"/proc/net/{{tcp,tcp6,udp,udp6}}; process names resolved for {named}/{len(unique)} sockets "
        "(needs same-user or root visibility)",
        items=sorted(unique.values(), key=lambda s: (s.proto, s.port, s.address)),
    )


async def docker_published_ports(api_url: str | None) -> Section:
    """Opt-in. Talks to a *read-only, containers-only* Docker API proxy — never the raw socket.

    Leave OBSERVENET_DOCKER_API_URL unset to keep this disabled.
    """
    if not api_url:
        return Section(
            "disabled",
            "Docker inventory is opt-in (set OBSERVENET_DOCKER_API_URL to a read-only socket proxy)",
        )
    try:
        async with httpx.AsyncClient(base_url=api_url, timeout=5) as client:
            resp = await client.get("/containers/json")
            resp.raise_for_status()
            containers = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return Section("unavailable", f"Docker API proxy not reachable: {type(exc).__name__}")
    items = []
    for c in containers:
        name = (c.get("Names") or ["?"])[0].lstrip("/")
        for p in c.get("Ports") or []:
            if "PublicPort" not in p:
                continue
            items.append({"container": name[:120], "image": (c.get("Image") or "")[:200] or None,
                          "proto": p.get("Type", "tcp"), "host_address": p.get("IP") or "0.0.0.0",
                          "host_port": p["PublicPort"], "container_port": p["PrivatePort"]})  # fmt: skip
    return Section(
        "ok", method="Docker Engine API GET /containers/json via read-only socket proxy", items=items
    )
