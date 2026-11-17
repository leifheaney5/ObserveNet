"""Network-scope policy: what may be probed or swept (SSRF / scan-oracle avoidance)."""

from __future__ import annotations

import pytest

from observenet.services.netscope import (
    ScopeError,
    is_private_lan,
    parse_approved_cidr,
    validate_discovery_cidr,
    validate_probe_target,
)
from observenet.services.settings import DiscoverySettings, RuntimeSettings, SpeedtestSettings

APPROVED = ["192.168.1.0/24"]


def check(
    kind: str, scope: str, host: str, port: int | None = None, approved: list[str] | None = None
) -> object:
    return validate_probe_target(
        kind=kind, scope=scope, host=host, port=port, approved_cidrs=approved or APPROVED
    )


@pytest.mark.parametrize("cidr", ["192.168.1.0/24", "10.20.0.0/16", "172.16.5.0/24", "fd12:3456:789a::/48"])
def test_private_cidrs_accepted(cidr: str) -> None:
    assert str(parse_approved_cidr(cidr)) == cidr


@pytest.mark.parametrize(
    "cidr",
    ["8.8.8.0/24", "1.1.1.1/32", "0.0.0.0/0", "100.64.0.0/16", "169.254.0.0/16", "127.0.0.0/8", "224.0.0.0/24",
     "2001:db8::/48", "203.0.113.0/24"],
)  # fmt: skip
def test_public_and_special_cidrs_rejected(cidr: str) -> None:
    with pytest.raises(ScopeError):
        parse_approved_cidr(cidr)


@pytest.mark.parametrize("cidr", ["10.0.0.0/8", "192.168.0.0/15", "fd00::/8"])
def test_too_broad_cidrs_rejected(cidr: str) -> None:
    with pytest.raises(ScopeError, match="too broad|not private"):
        parse_approved_cidr(cidr)


@pytest.mark.parametrize("cidr", ["192.168.1.5/24", "not-a-cidr", "192.168.1.0/33", ""])
def test_malformed_cidrs_rejected(cidr: str) -> None:
    with pytest.raises(ScopeError):
        parse_approved_cidr(cidr)


def test_is_private_lan_excludes_cgnat_loopback_linklocal() -> None:
    import ipaddress

    assert is_private_lan(ipaddress.ip_address("192.168.1.10"))
    for bad in (
        "100.64.1.1",
        "127.0.0.1",
        "169.254.169.254",
        "8.8.8.8",
        "224.0.0.1",
        "0.0.0.0",
        "::1",
        "fe80::1",
    ):
        assert not is_private_lan(ipaddress.ip_address(bad)), bad


def test_discovery_cidr_must_be_inside_approved_scope_and_small() -> None:
    assert str(validate_discovery_cidr("192.168.1.0/25", APPROVED)) == "192.168.1.0/25"
    with pytest.raises(ScopeError, match="not inside any approved"):
        validate_discovery_cidr("192.168.2.0/24", APPROVED)
    with pytest.raises(ScopeError, match="limit is 1024"):
        validate_discovery_cidr("10.0.0.0/16", ["10.0.0.0/16"])
    with pytest.raises(ScopeError):
        validate_discovery_cidr("8.8.8.0/24", APPROVED)
    with pytest.raises(ScopeError, match="IPv4 only"):
        validate_discovery_cidr("fd12:3456:789a::/64", ["fd12:3456:789a::/48"])
    with pytest.raises(ScopeError):
        validate_discovery_cidr("192.168.1.0/24", [])  # nothing approved => nothing may be swept


def test_gateway_auto_is_allowed_without_any_approved_cidr() -> None:
    t = validate_probe_target(kind="gateway_icmp", scope="gateway", host="auto", port=None, approved_cidrs=[])
    assert t.host == "auto" and t.port is None


@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "::1", "fe80::1", "224.0.0.251", "0.0.0.0"])
@pytest.mark.parametrize(
    ("kind", "scope", "port"), [("tcp", "external", 443), ("tcp", "internal", 80), ("icmp", "internal", None)]
)
def test_loopback_metadata_linklocal_multicast_never_allowed(
    host: str, kind: str, scope: str, port: int | None
) -> None:
    with pytest.raises(ScopeError):
        check(kind, scope, host, port)


def test_internal_target_must_be_inside_an_approved_cidr() -> None:
    assert check("icmp", "internal", "192.168.1.20").host == "192.168.1.20"  # type: ignore[attr-defined]
    with pytest.raises(ScopeError, match="outside the approved network scope"):
        check("icmp", "internal", "192.168.2.20")
    with pytest.raises(ScopeError, match="outside the approved"):
        check("tcp", "internal", "10.0.0.5", 443)
    with pytest.raises(ScopeError, match="not a private LAN"):
        check("icmp", "internal", "8.8.8.8")
    with pytest.raises(ScopeError, match="not a private LAN"):
        check("gateway_icmp", "gateway", "100.64.0.1")


def test_external_scope_refuses_private_addresses() -> None:
    with pytest.raises(ScopeError, match="not a public address"):
        check("tcp", "external", "192.168.1.1", 443)
    with pytest.raises(ScopeError, match="not a public address"):
        check("tcp", "external", "10.0.0.1", 443)


@pytest.mark.parametrize("port", [22, 23, 3389, 8080, 6379, None])
def test_tcp_port_allowlist(port: int | None) -> None:
    with pytest.raises(ScopeError, match="limited to ports"):
        check("tcp", "external", "1.1.1.1", port)


@pytest.mark.parametrize("port", [53, 80, 443, 853])
def test_tcp_allowed_ports(port: int) -> None:
    assert check("tcp", "external", "1.1.1.1", port).port == port  # type: ignore[attr-defined]


def test_hostnames_only_for_external_scope() -> None:
    assert check("tcp", "external", "Example.COM", 443).host == "example.com"  # type: ignore[attr-defined]
    with pytest.raises(ScopeError, match="literal IP"):
        check("tcp", "internal", "nas.example.com", 443)
    with pytest.raises(ScopeError, match="literal IP"):
        check("gateway_icmp", "gateway", "router.example.com")


@pytest.mark.parametrize(
    "host",
    [
        "printer.local",
        "db.internal",
        "foo.localhost",
        "nas.lan",
        "localhost",
        "intranet",
        "a b.com",
        "http://x.com",
    ],
)
def test_internal_looking_or_malformed_hostnames_refused(host: str) -> None:
    with pytest.raises(ScopeError):
        check("tcp", "external", host, 443)


def test_dns_probe_rules() -> None:
    t = check("dns", "external", "example.com", 53)
    assert t.port is None  # type: ignore[attr-defined]
    with pytest.raises(ScopeError):
        check("dns", "internal", "example.com")
    with pytest.raises(ScopeError):
        check("dns", "external", "192.168.1.1")
    with pytest.raises(ScopeError):
        check("dns", "external", "printer")


def test_unknown_kind_and_scope_and_mismatch() -> None:
    with pytest.raises(ScopeError):
        check("http", "external", "example.com", 443)
    with pytest.raises(ScopeError):
        check("tcp", "everywhere", "1.1.1.1", 443)
    with pytest.raises(ScopeError, match="scope 'gateway'"):
        check("gateway_icmp", "external", "auto")


def test_runtime_settings_reject_public_scope_and_unsafe_combinations() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RuntimeSettings(approved_cidrs=["8.8.8.0/24"])
    enabled_without_cidr = RuntimeSettings(approved_cidrs=APPROVED, discovery=DiscoverySettings(enabled=True))
    with pytest.raises(ValueError, match="without at least one"):
        enabled_without_cidr.validate_cross_fields()
    outside = RuntimeSettings(
        approved_cidrs=APPROVED, discovery=DiscoverySettings(enabled=True, cidrs=["10.0.0.0/24"])
    )
    with pytest.raises(ScopeError):
        outside.validate_cross_fields()
    with pytest.raises(ValueError, match="without a download URL"):
        RuntimeSettings(speedtest=SpeedtestSettings(enabled=True)).validate_cross_fields()
    with pytest.raises(ValidationError):
        SpeedtestSettings(download_url="http://plain.example.com/file")
    with pytest.raises(ValidationError):
        SpeedtestSettings(interval_hours=1)  # never more often than every 3 h
    RuntimeSettings(
        approved_cidrs=APPROVED, discovery=DiscoverySettings(enabled=True, cidrs=["192.168.1.0/26"])
    ).validate_cross_fields()


def test_defaults_are_safe() -> None:
    s = RuntimeSettings()
    assert s.discovery.enabled is False and s.speedtest.enabled is False and s.approved_cidrs == []
    assert (s.retention.raw_hours, s.retention.rollup_1m_days, s.retention.rollup_1h_days) == (48, 90, 365)
