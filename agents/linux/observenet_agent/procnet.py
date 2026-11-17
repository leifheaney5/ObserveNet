"""Pure parsers for /proc/net/* (testable with fixtures, no privileges required)."""

from __future__ import annotations

import ipaddress
import socket
import struct
from dataclasses import dataclass

TCP_LISTEN = "0A"
UDP_UNCONNECTED = "07"


@dataclass(frozen=True)
class ListeningSocket:
    proto: str
    address: str
    port: int
    process: str | None = None


@dataclass(frozen=True)
class Neighbor:
    ip: str
    mac: str | None
    interface: str
    state: str


def _hex_to_ipv4(hex_addr: str) -> str:
    return socket.inet_ntoa(struct.pack("<I", int(hex_addr, 16)))


def _hex_to_ipv6(hex_addr: str) -> str:
    # 4 little-endian 32-bit words
    raw = b"".join(struct.pack("<I", int(hex_addr[i : i + 8], 16)) for i in range(0, 32, 8))
    addr = ipaddress.IPv6Address(raw)
    if addr.ipv4_mapped is not None:
        return str(addr.ipv4_mapped)
    return str(addr)


def parse_proc_net_sockets(text: str, *, proto: str, ipv6: bool) -> list[ListeningSocket]:
    """Listening TCP sockets (state 0A) or unconnected bound UDP sockets (state 07)."""
    want = TCP_LISTEN if proto == "tcp" else UDP_UNCONNECTED
    out: list[ListeningSocket] = []
    for line in text.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 4 or cols[3] != want:
            continue
        try:
            addr_hex, port_hex = cols[1].rsplit(":", 1)
            port = int(port_hex, 16)
            address = _hex_to_ipv6(addr_hex) if ipv6 else _hex_to_ipv4(addr_hex)
        except (ValueError, struct.error):
            continue
        if port == 0:
            continue
        out.append(ListeningSocket(proto=proto, address=address, port=port))
    return out


def parse_proc_net_arp(text: str) -> list[Neighbor]:
    """IPv4 neighbour cache. Flag 0x2 = complete entry; incomplete entries carry no MAC."""
    out: list[Neighbor] = []
    for line in text.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 6:
            continue
        ip, _hw, flags, mac, _mask, dev = cols[:6]
        try:
            complete = bool(int(flags, 16) & 0x2)
        except ValueError:
            continue
        if not complete or mac == "00:00:00:00:00:00":
            continue
        out.append(Neighbor(ip=ip, mac=mac.lower(), interface=dev, state="reachable"))
    return out


def parse_default_gateway(text: str) -> tuple[str, str] | None:
    """(gateway_ip, interface) of the IPv4 default route with the lowest metric."""
    best: tuple[int, str, str] | None = None
    for line in text.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 8:
            continue
        iface, dest, gateway, flags, metric = cols[0], cols[1], cols[2], cols[3], cols[6]
        try:
            if dest != "00000000" or not int(flags, 16) & 0x2:  # RTF_GATEWAY
                continue
            entry = (int(metric), _hex_to_ipv4(gateway), iface)
        except (ValueError, struct.error):
            continue
        if best is None or entry[0] < best[0]:
            best = entry
    return (best[1], best[2]) if best else None
