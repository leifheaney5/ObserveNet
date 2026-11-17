"""Narrow, low-rate health probes. Each returns a result; none raises.

Supported: ICMP echo to the default gateway / an approved private address, TCP connect to
a small port allowlist, and DNS resolution through the system resolver. Targets come from
the backend (validated there against the approved network scope) and are validated again
here before a single packet is sent.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import struct
import time
from dataclasses import dataclass

ALLOWED_TCP_PORTS = frozenset({53, 80, 443, 853})
MIN_INTERVAL = {"gateway": 1.0, "internal": 1.0, "external": 10.0}
ICMP_ECHO_REQUEST, ICMP_ECHO_REPLY = 8, 0


class TargetRejected(ValueError):
    pass


@dataclass(frozen=True)
class ProbeOutcome:
    success: bool
    latency_ms: float | None
    method: str
    resolved_host: str | None = None
    error: str | None = None


def validate_target(
    kind: str, host: str, port: int | None, interval_s: float, *, default_gateway: str | None
) -> tuple[str, str]:
    """Agent-side safety net. Returns (resolved_host, scope) or raises TargetRejected."""
    if kind == "gateway_icmp":
        if host == "auto":
            if not default_gateway:
                raise TargetRejected("no default gateway detected")
            host = default_gateway
        scope = "gateway"
    elif kind == "dns":
        if interval_s < MIN_INTERVAL["external"]:
            raise TargetRejected("external checks may not run more often than every 10 s")
        return host, "external"
    else:
        scope = ""
    if kind == "tcp" and port not in ALLOWED_TCP_PORTS:
        raise TargetRejected(f"tcp port {port} is not in the allowlist {sorted(ALLOWED_TCP_PORTS)}")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        if kind != "tcp":
            raise TargetRejected("ICMP targets must be literal IP addresses") from None
        if interval_s < MIN_INTERVAL["external"]:
            raise TargetRejected("external checks may not run more often than every 10 s") from None
        return host, "external"
    if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        raise TargetRejected(f"{addr} is loopback/link-local/multicast")
    if kind in ("gateway_icmp", "icmp"):
        if not addr.is_private:
            raise TargetRejected("ICMP probes are limited to private addresses on your own network")
        scope = scope or "internal"
    else:
        scope = "internal" if addr.is_private else "external"
    if interval_s < MIN_INTERVAL[scope]:
        raise TargetRejected(
            f"{scope} targets may not be probed more often than every {MIN_INTERVAL[scope]:g} s"
        )
    return str(addr), scope


def icmp_checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def build_echo_request(ident: int, seq: int, payload: bytes) -> bytes:
    header = struct.pack("!BBHHH", ICMP_ECHO_REQUEST, 0, 0, ident, seq)
    checksum = icmp_checksum(header + payload)
    return struct.pack("!BBHHH", ICMP_ECHO_REQUEST, 0, checksum, ident, seq) + payload


def parse_echo_reply(packet: bytes, *, raw: bool) -> tuple[int, int, bytes] | None:
    """(ident, seq, payload) of an echo reply, or None. Raw sockets include the IP header."""
    if raw:
        if len(packet) < 20:
            return None
        packet = packet[(packet[0] & 0x0F) * 4 :]
    if len(packet) < 8:
        return None
    kind, code, _csum, ident, seq = struct.unpack("!BBHHH", packet[:8])
    if kind != ICMP_ECHO_REPLY or code != 0:
        return None
    return ident, seq, packet[8:]


class IcmpProber:
    """Unprivileged datagram ICMP first (needs net.ipv4.ping_group_range), raw socket second
    (needs CAP_NET_RAW). If neither is permitted the probe reports 'unavailable' — the
    agent does not shell out and does not ask for root."""

    def __init__(self) -> None:
        self.mode: str | None = None
        self.unavailable_reason: str | None = None
        self._seq = 0
        self._ident = os.getpid() & 0xFFFF

    def _open(self) -> socket.socket | None:
        if self.unavailable_reason:
            return None
        attempts = [("icmp_dgram", socket.SOCK_DGRAM), ("icmp_raw", socket.SOCK_RAW)]
        if self.mode:
            attempts = [a for a in attempts if a[0] == self.mode]
        errors = []
        for mode, sock_type in attempts:
            try:
                sock = socket.socket(socket.AF_INET, sock_type, socket.IPPROTO_ICMP)
            except (PermissionError, OSError) as exc:
                errors.append(f"{mode}: {exc.strerror or exc}")
                continue
            self.mode = mode
            return sock
        self.unavailable_reason = (
            "ICMP sockets not permitted (" + "; ".join(errors) + "). Allow unprivileged ping via "
            "net.ipv4.ping_group_range or grant CAP_NET_RAW to the collector."
        )
        return None

    def ping_blocking(self, host: str, timeout_s: float) -> ProbeOutcome:
        sock = self._open()
        if sock is None:
            return ProbeOutcome(False, None, "unavailable", host, self.unavailable_reason)
        self._seq = (self._seq + 1) & 0xFFFF
        seq = self._seq
        payload = struct.pack("!d", time.time()) + os.urandom(8)
        try:
            sock.settimeout(timeout_s)
            start = time.perf_counter()
            sock.sendto(build_echo_request(self._ident, seq, payload), (host, 0))
            deadline = start + timeout_s
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return ProbeOutcome(False, None, self.mode or "icmp", host, "timeout")
                sock.settimeout(remaining)
                packet, addr = sock.recvfrom(1500)
                elapsed = (time.perf_counter() - start) * 1000
                reply = parse_echo_reply(packet, raw=self.mode == "icmp_raw")
                # Datagram sockets rewrite the identifier, so match on sequence + payload.
                if reply and addr[0] == host and reply[1] == seq and reply[2] == payload:
                    return ProbeOutcome(True, round(elapsed, 3), self.mode or "icmp", host)
        except TimeoutError:
            return ProbeOutcome(False, None, self.mode or "icmp", host, "timeout")
        except OSError as exc:
            return ProbeOutcome(False, None, self.mode or "icmp", host, exc.strerror or str(exc))
        finally:
            sock.close()

    async def ping(self, host: str, timeout_s: float) -> ProbeOutcome:
        return await asyncio.to_thread(self.ping_blocking, host, timeout_s)


async def tcp_probe(host: str, port: int, timeout_s: float) -> ProbeOutcome:
    """Time to complete a TCP handshake. Sends no application data."""
    start = time.perf_counter()
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout_s)
    except TimeoutError:
        return ProbeOutcome(False, None, "tcp_connect", None, "timeout")
    except OSError as exc:
        return ProbeOutcome(False, None, "tcp_connect", None, exc.strerror or type(exc).__name__)
    elapsed = (time.perf_counter() - start) * 1000
    peer = writer.get_extra_info("peername")
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=1)
    except (TimeoutError, OSError):
        pass
    return ProbeOutcome(True, round(elapsed, 3), "tcp_connect", peer[0] if peer else None)


async def dns_probe(name: str, timeout_s: float) -> ProbeOutcome:
    """Resolve ``name`` through the system resolver and time it."""
    loop = asyncio.get_running_loop()
    start = time.perf_counter()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(name, None, type=socket.SOCK_STREAM), timeout=timeout_s
        )
    except TimeoutError:
        return ProbeOutcome(False, None, "system_resolver", None, "timeout")
    except OSError as exc:
        return ProbeOutcome(False, None, "system_resolver", None, exc.strerror or type(exc).__name__)
    elapsed = (time.perf_counter() - start) * 1000
    return ProbeOutcome(True, round(elapsed, 3), "system_resolver", str(infos[0][4][0]) if infos else None)
