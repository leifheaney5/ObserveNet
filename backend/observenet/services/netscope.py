"""Network-scope policy: which addresses ObserveNet is allowed to probe or sweep.

Probes are executed by collectors, so an attacker who could set arbitrary targets would
gain a port-scan/SSRF oracle inside the home network. Every target is therefore validated
here (and re-validated by the agent).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

DEFAULT_TCP_PORTS = frozenset({53, 80, 443, 853})
MAX_DISCOVERY_ADDRESSES = 1024
_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
# Carrier-grade NAT space is private-ish but not the user's own LAN.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class ScopeError(ValueError):
    """A target or CIDR violates the network-scope policy."""


@dataclass(frozen=True)
class ValidatedTarget:
    kind: str
    scope: str
    host: str
    port: int | None


def is_private_lan(addr: IPAddress) -> bool:
    """RFC 1918 / ULA unicast only: no loopback, link-local, CGNAT, multicast or reserved."""
    if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified or addr.is_reserved:
        return False
    if addr.version == 4 and addr in _CGNAT:
        return False
    return addr.is_private


def parse_approved_cidr(value: str) -> IPNetwork:
    """Validate a user-approved CIDR: must be a private LAN range, never public space."""
    try:
        net = ipaddress.ip_network(value.strip(), strict=True)
    except ValueError as exc:
        raise ScopeError(f"{value!r} is not a valid CIDR (host bits must be zero): {exc}") from exc
    if not is_private_lan(net.network_address) or not is_private_lan(net.broadcast_address):
        raise ScopeError(f"{net} is not private LAN address space; only your own private ranges may be approved")
    if net.version == 4 and net.prefixlen < 16:
        raise ScopeError(f"{net} is too broad; approve ranges of /16 or smaller")
    if net.version == 6 and net.prefixlen < 48:
        raise ScopeError(f"{net} is too broad; approve IPv6 ranges of /48 or smaller")
    return net


def validate_discovery_cidr(value: str, approved: list[str]) -> IPNetwork:
    """A sweep range must sit inside an approved CIDR and stay small."""
    net = parse_approved_cidr(value)
    if net.version != 4:
        raise ScopeError("active discovery supports IPv4 only")
    if net.num_addresses > MAX_DISCOVERY_ADDRESSES:
        raise ScopeError(f"{net} has {net.num_addresses} addresses; the limit is {MAX_DISCOVERY_ADDRESSES}")
    approved_nets = [parse_approved_cidr(c) for c in approved]
    if not any(net.version == a.version and net.subnet_of(a) for a in approved_nets):  # type: ignore[arg-type]
        raise ScopeError(f"{net} is not inside any approved CIDR")
    return net


def _in_approved(addr: IPAddress, approved: list[str]) -> bool:
    return any(addr in parse_approved_cidr(c) for c in approved)


def validate_probe_target(
    *,
    kind: str,
    scope: str,
    host: str,
    port: int | None,
    approved_cidrs: list[str],
    allowed_tcp_ports: frozenset[int] = DEFAULT_TCP_PORTS,
) -> ValidatedTarget:
    host = host.strip().lower()
    if kind not in {"gateway_icmp", "icmp", "tcp", "dns"}:
        raise ScopeError(f"unsupported probe kind {kind!r}")
    if scope not in {"gateway", "internal", "external"}:
        raise ScopeError(f"unsupported scope {scope!r}")

    if kind == "gateway_icmp":
        if scope != "gateway":
            raise ScopeError("gateway_icmp probes must use scope 'gateway'")
        if host == "auto":
            return ValidatedTarget(kind, scope, host, None)
    if kind == "tcp":
        if port is None or port not in allowed_tcp_ports:
            raise ScopeError(f"tcp probes are limited to ports {sorted(allowed_tcp_ports)}")
    elif kind == "dns":
        port = None
    else:
        port = None

    if kind == "dns":
        # host = name to resolve through the collector's system resolver.
        if not _HOSTNAME_RE.match(host):
            raise ScopeError("dns probes need a fully-qualified public hostname to resolve")
        if scope != "external":
            raise ScopeError("dns probes must use scope 'external'")
        return ValidatedTarget(kind, scope, host, None)

    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None

    if addr is None:
        if scope != "external":
            raise ScopeError("gateway/internal targets must be literal IP addresses inside an approved CIDR")
        if not _HOSTNAME_RE.match(host) or host.endswith((".local", ".internal", ".localhost", ".lan")):
            raise ScopeError(f"{host!r} is not an acceptable public hostname")
        return ValidatedTarget(kind, scope, host, port)

    if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        raise ScopeError(f"{addr} is loopback/link-local/multicast and can never be a probe target")
    if scope in {"gateway", "internal"}:
        if not is_private_lan(addr):
            raise ScopeError(f"{addr} is not a private LAN address")
        if not _in_approved(addr, approved_cidrs):
            raise ScopeError(f"{addr} is outside the approved network scope; add its CIDR in Settings first")
    else:
        if not addr.is_global:
            raise ScopeError(f"{addr} is not a public address; use scope 'internal' with an approved CIDR")
    return ValidatedTarget(kind, scope, str(addr), port)
