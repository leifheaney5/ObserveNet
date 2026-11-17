from __future__ import annotations

import struct

import pytest

from observenet_agent import probes
from observenet_agent.probes import (
    IcmpProber,
    TargetRejected,
    build_echo_request,
    icmp_checksum,
    parse_echo_reply,
    validate_target,
)


def test_checksum_matches_rfc1071_reference() -> None:
    # Worked example from RFC 1071 §3.
    data = bytes.fromhex("0001f203f4f5f6f7")
    assert icmp_checksum(data) == 0x220D


def test_checksum_odd_length_is_padded() -> None:
    assert icmp_checksum(b"\x01") == icmp_checksum(b"\x01\x00")


def test_echo_request_is_type8_and_checksums_to_zero() -> None:
    pkt = build_echo_request(0x1234, 7, b"payload!")
    kind, code, _csum, ident, seq = struct.unpack("!BBHHH", pkt[:8])
    assert (kind, code, ident, seq) == (8, 0, 0x1234, 7)
    assert pkt[8:] == b"payload!"
    assert icmp_checksum(pkt) == 0  # a valid packet sums to zero including its checksum


def _reply(ident: int, seq: int, payload: bytes, kind: int = 0) -> bytes:
    return struct.pack("!BBHHH", kind, 0, 0, ident, seq) + payload


def test_parse_echo_reply_dgram() -> None:
    assert parse_echo_reply(_reply(9, 3, b"abc"), raw=False) == (9, 3, b"abc")


def test_parse_echo_reply_raw_strips_ip_header() -> None:
    ip_header = bytes([0x45]) + bytes(19)  # IHL=5 → 20 bytes
    assert parse_echo_reply(ip_header + _reply(9, 3, b"abc"), raw=True) == (9, 3, b"abc")
    ip_header_opts = bytes([0x46]) + bytes(23)  # IHL=6 → 24 bytes
    assert parse_echo_reply(ip_header_opts + _reply(1, 2, b"x"), raw=True) == (1, 2, b"x")


def test_parse_echo_reply_rejects_non_replies_and_short_packets() -> None:
    assert parse_echo_reply(_reply(9, 3, b"abc", kind=8), raw=False) is None  # our own request echoed back
    assert parse_echo_reply(_reply(9, 3, b"abc", kind=3), raw=False) is None  # destination unreachable
    assert parse_echo_reply(b"\x00\x00", raw=False) is None
    assert parse_echo_reply(b"\x45" + bytes(5), raw=True) is None


GW = "192.168.1.1"


def test_gateway_auto_resolves_to_detected_default_gateway() -> None:
    assert validate_target("gateway_icmp", "auto", None, 1.0, default_gateway=GW) == (GW, "gateway")


def test_gateway_auto_without_gateway_is_rejected() -> None:
    with pytest.raises(TargetRejected, match="no default gateway"):
        validate_target("gateway_icmp", "auto", None, 1.0, default_gateway=None)


@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "224.0.0.1", "0.0.0.0", "::1"])
def test_loopback_link_local_multicast_rejected(host: str) -> None:
    for kind, port in (("icmp", None), ("tcp", 443)):
        with pytest.raises(TargetRejected):
            validate_target(kind, host, port, 30.0, default_gateway=GW)


@pytest.mark.parametrize("kind", ["icmp", "gateway_icmp"])
def test_public_icmp_rejected(kind: str) -> None:
    with pytest.raises(TargetRejected, match="private"):
        validate_target(kind, "8.8.8.8", None, 30.0, default_gateway=GW)


def test_icmp_hostname_rejected() -> None:
    with pytest.raises(TargetRejected, match="literal IP"):
        validate_target("icmp", "router.example.com", None, 30.0, default_gateway=GW)


@pytest.mark.parametrize("port", [22, 8080, 3389, None])
def test_tcp_port_allowlist(port: int | None) -> None:
    with pytest.raises(TargetRejected, match="allowlist"):
        validate_target("tcp", "1.1.1.1", port, 30.0, default_gateway=GW)


def test_tcp_allowed_ports_and_scope_classification() -> None:
    assert validate_target("tcp", "1.1.1.1", 443, 30.0, default_gateway=GW) == ("1.1.1.1", "external")
    assert validate_target("tcp", "192.168.1.10", 80, 1.0, default_gateway=GW) == ("192.168.1.10", "internal")
    assert validate_target("tcp", "one.one.one.one", 853, 10.0, default_gateway=GW) == (
        "one.one.one.one",
        "external",
    )


@pytest.mark.parametrize(
    ("kind", "host", "port"),
    [("tcp", "1.1.1.1", 443), ("tcp", "example.com", 443), ("dns", "example.com", None)],
)
def test_external_interval_floor_is_ten_seconds(kind: str, host: str, port: int | None) -> None:
    with pytest.raises(TargetRejected, match="10 s"):
        validate_target(kind, host, port, 5.0, default_gateway=GW)
    validate_target(kind, host, port, 10.0, default_gateway=GW)


def test_gateway_interval_floor_is_one_second() -> None:
    with pytest.raises(TargetRejected):
        validate_target("gateway_icmp", "auto", None, 0.2, default_gateway=GW)


def test_icmp_prober_reports_unavailable_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*_a: object, **_k: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(probes.socket, "socket", deny)
    prober = IcmpProber()
    outcome = prober.ping_blocking("192.168.1.1", 0.1)
    assert outcome.success is False
    assert outcome.method == "unavailable"
    assert outcome.latency_ms is None
    assert "CAP_NET_RAW" in (outcome.error or "")
    # stays unavailable without retrying socket creation every second
    assert prober.ping_blocking("192.168.1.1", 0.1).method == "unavailable"


async def test_tcp_probe_refused_connection_is_a_failure_not_an_exception() -> None:
    import socket

    with socket.socket() as s:  # grab a free loopback port, then close it so nothing listens
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    outcome = await probes.tcp_probe("127.0.0.1", port, 1.0)
    assert outcome.success is False and outcome.latency_ms is None and outcome.method == "tcp_connect"


async def test_tcp_probe_measures_handshake_to_local_listener() -> None:
    import asyncio

    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        outcome = await probes.tcp_probe("127.0.0.1", port, 1.0)
    finally:
        server.close()
        await server.wait_closed()
    assert outcome.success is True
    assert outcome.latency_ms is not None and outcome.latency_ms >= 0
    assert outcome.resolved_host == "127.0.0.1"
