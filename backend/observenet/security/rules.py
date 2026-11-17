"""Deterministic, read-only posture rules.

A rule is a pure function of a posture snapshot (data the asset's own agent reported).
Nothing here sends a packet, opens a connection or attempts to exploit anything, so no
rule may ever claim exploitability. Each violation separates the *observation* (fact)
from the *inference* (labelled as such).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from observenet.schemas.ingest import ListeningSocket, Posture
from observenet.services.settings import PostureSettings

ENGINE_VERSION = "1.0.0"
VERIFY_ALGORITHM_VERSION = "recheck-same-rule/1"

WILDCARDS = {"0.0.0.0", "::", "*", "[::]"}  # noqa: S104 - addresses we look for, not bind to
# Ports whose services commonly ship without authentication or hold sensitive data.
SENSITIVE_PORTS: dict[int, str] = {
    23: "Telnet", 2375: "Docker API (unauthenticated)", 3306: "MySQL/MariaDB", 5432: "PostgreSQL",
    6379: "Redis", 9200: "Elasticsearch", 11211: "memcached", 27017: "MongoDB", 5900: "VNC",
    3389: "Remote Desktop", 445: "SMB", 139: "NetBIOS", 2049: "NFS", 111: "rpcbind",
}  # fmt: skip


@dataclass(frozen=True)
class RuleMeta:
    rule_id: str
    version: int
    title: str
    description: str
    platform: str  # linux | windows | router
    section: str
    category: str  # security | observability
    default_severity: str
    classification: str


@dataclass
class Violation:
    subject: str
    title: str
    observation: str
    inference: str | None
    impact: str
    severity: str
    severity_rationale: str
    confidence: str
    evidence: dict[str, Any]
    remediation: list[tuple[str, str | None]] = field(default_factory=list)


@dataclass
class RuleOutcome:
    status: str  # pass | fail | unsupported | unknown
    summary: str
    method: str = ""
    violations: list[Violation] = field(default_factory=list)
    clean_evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleContext:
    platform: str
    settings: PostureSettings


class Rule(Protocol):
    meta: RuleMeta

    def evaluate(self, posture: Posture, ctx: RuleContext) -> RuleOutcome: ...


def _section_unreadable(section: Any, name: str) -> RuleOutcome | None:
    if section is None:
        return RuleOutcome("unknown", f"The collector did not report the '{name}' section.")
    if section.status == "ok":
        return None
    reason = section.reason or "no reason given"
    if section.status in ("unsupported", "disabled"):
        return RuleOutcome("unsupported", f"'{name}' collection is {section.status} on this collector: {reason}")
    return RuleOutcome("unknown", f"'{name}' could not be read: {reason}")


def _group_wildcard(items: list[ListeningSocket]) -> dict[tuple[str, int], list[ListeningSocket]]:
    grouped: dict[tuple[str, int], list[ListeningSocket]] = {}
    for sock in items:
        if sock.address in WILDCARDS:
            grouped.setdefault((sock.proto, sock.port), []).append(sock)
    return grouped


def _sock_dict(s: ListeningSocket) -> dict[str, Any]:
    return {"proto": s.proto, "address": s.address, "port": s.port, "process": s.process}


class WildcardListenerRule:
    """A socket bound to all interfaces that is not on the operator's allowlist."""

    def __init__(self, rule_id: str, platform: str) -> None:
        self.meta = RuleMeta(
            rule_id=rule_id,
            version=1,
            title="Service listening on all network interfaces",
            description=(
                "Lists sockets bound to a wildcard address (0.0.0.0 / ::) that are not on the listener "
                "allowlist. Read from the host's own socket table; reachability is not tested."
            ),
            platform=platform,
            section="listening",
            category="security",
            default_severity="low",
            classification="POTENTIAL_EXPOSURE",
        )
        self._platform = platform

    def evaluate(self, posture: Posture, ctx: RuleContext) -> RuleOutcome:
        unreadable = _section_unreadable(posture.listening, "listening sockets")
        if unreadable:
            return unreadable
        assert posture.listening is not None
        allow = set(ctx.settings.listener_allowlist)
        violations: list[Violation] = []
        grouped = _group_wildcard(posture.listening.items)
        for (proto, port), socks in sorted(grouped.items()):
            if f"{proto}/{port}" in allow:
                continue
            service = SENSITIVE_PORTS.get(port)
            process = next((s.process for s in socks if s.process), None)
            who = f" by process '{process}'" if process else " (process name not visible to the collector)"
            addresses = ", ".join(sorted({s.address for s in socks}))
            severity = "medium" if service else "low"
            rationale = (
                f"Port {port} is commonly used by {service}, which often holds sensitive data or ships "
                "without authentication; a wildcard bind makes it reachable on every attached network "
                "unless a firewall intervenes."
                if service
                else "A wildcard bind is common and often intended; rated low because only the bind "
                "address was observed, not what the service is or whether a firewall filters it."
            )
            if self._platform == "linux":
                fix = [
                    ("Identify the owning process on the host.", f"sudo ss -ltnup 'sport = :{port}'"),
                    ("If it should only be used locally, bind it to 127.0.0.1 (or a specific LAN address) "
                     "in the service's configuration and restart the service.", None),
                    ("If it is not needed, stop and disable the service.", "sudo systemctl disable --now <service>"),
                    (f"If the wildcard bind is intended, add '{proto}/{port}' to the listener allowlist in "
                     "Settings → Security posture.", None),
                ]  # fmt: skip
            else:
                fix = [
                    ("Identify the owning process (elevated PowerShell).",
                     f"Get-NetTCPConnection -LocalPort {port} -State Listen | Select OwningProcess; Get-Process -Id <pid>"),
                    ("Disable the feature or service if it is not needed, or restrict it with a Windows "
                     "Firewall rule scoped to the networks that need it.", None),
                    (f"If the wildcard bind is intended, add '{proto}/{port}' to the listener allowlist in "
                     "Settings → Security posture.", None),
                ]  # fmt: skip
            violations.append(
                Violation(
                    subject=f"{proto}/*:{port}",
                    title=f"{proto.upper()} port {port} is listening on all interfaces"
                    + (f" ({service})" if service else ""),
                    observation=f"Socket table shows {proto.upper()} port {port} bound to {addresses}{who}.",
                    inference=(
                        "Inferred, not tested: the service may be reachable from other devices on every "
                        "network this host is attached to. ObserveNet did not connect to it and does not "
                        "know whether a host or network firewall filters it."
                    ),
                    impact=(
                        "Any service reachable from the LAN widens what a compromised or untrusted device "
                        "on the same network could talk to."
                    ),
                    severity=severity,
                    severity_rationale=rationale,
                    confidence="high",
                    evidence={"sockets": [_sock_dict(s) for s in socks], "allowlisted": False},
                    remediation=fix,
                )
            )
        total = len(posture.listening.items)
        method = posture.listening.method
        if violations:
            return RuleOutcome(
                "fail", f"{len(violations)} wildcard listener(s) not on the allowlist (of {total} sockets).",
                method, violations, {"sockets": [], "listening_socket_count": total},
            )  # fmt: skip
        return RuleOutcome(
            "pass", f"No unexpected wildcard listeners among {total} sockets.", method,
            clean_evidence={"sockets": [], "listening_socket_count": total},
        )  # fmt: skip


class WindowsFirewallRule:
    meta = RuleMeta(
        rule_id="WIN-FW-001",
        version=1,
        title="Windows Firewall profile disabled",
        description="Reads the enabled state of the Domain, Private and Public firewall profiles.",
        platform="windows",
        section="firewall",
        category="security",
        default_severity="medium",
        classification="OBSERVED_CONFIGURATION",
    )

    def evaluate(self, posture: Posture, ctx: RuleContext) -> RuleOutcome:
        unreadable = _section_unreadable(posture.firewall, "Windows Firewall")
        if unreadable:
            return unreadable
        assert posture.firewall is not None
        profiles = {p.name: p.enabled for p in posture.firewall.profiles}
        if not profiles:
            return RuleOutcome("unknown", "The collector reported no firewall profiles.")
        violations = []
        for name, enabled in sorted(profiles.items()):
            if enabled:
                continue
            severity = "high" if name == "Public" else "medium"
            violations.append(
                Violation(
                    subject=f"profile/{name}",
                    title=f"Windows Firewall is disabled for the {name} profile",
                    observation=f"Get-NetFirewallProfile reported Enabled=False for the {name} profile.",
                    inference=None,
                    impact=(
                        "With the profile disabled, Windows does not filter inbound connections on networks "
                        f"classified as {name}; every listening service is reachable from those networks."
                    ),
                    severity=severity,
                    severity_rationale=(
                        "The Public profile applies to untrusted networks (hotels, cafés), so disabling it "
                        "removes filtering exactly where it matters most."
                        if name == "Public"
                        else f"The {name} profile applies to networks you marked as trusted; filtering is "
                        "still a meaningful layer there."
                    ),
                    confidence="high",
                    evidence={"profile": name, "enabled": False, "all_profiles": profiles},
                    remediation=[
                        ("Open an elevated PowerShell window on the Windows PC and re-enable the profile.",
                         f"Set-NetFirewallProfile -Profile {name} -Enabled True"),
                        ("Confirm the state.", "Get-NetFirewallProfile | Select Name, Enabled"),
                        ("Return here and choose Verify fix so ObserveNet re-reads the setting.", None),
                    ],  # fmt: skip
                )
            )
        method = posture.firewall.method
        if violations:
            return RuleOutcome("fail", f"{len(violations)} firewall profile(s) disabled.", method, violations,
                               {"enabled": True, "all_profiles": profiles})  # fmt: skip
        return RuleOutcome("pass", "All reported firewall profiles are enabled.", method,
                           clean_evidence={"enabled": True, "all_profiles": profiles})  # fmt: skip


class Smb1Rule:
    meta = RuleMeta(
        rule_id="WIN-SMB-001",
        version=1,
        title="SMBv1 server protocol enabled",
        description="Reads whether the legacy SMBv1 server protocol is enabled.",
        platform="windows",
        section="smb",
        category="security",
        default_severity="medium",
        classification="OBSERVED_CONFIGURATION",
    )

    def evaluate(self, posture: Posture, ctx: RuleContext) -> RuleOutcome:
        unreadable = _section_unreadable(posture.smb, "SMB configuration")
        if unreadable:
            return unreadable
        assert posture.smb is not None
        method = posture.smb.method
        if posture.smb.smb1_enabled is None:
            return RuleOutcome("unknown", "The collector could not determine the SMBv1 server setting.", method)
        state = {"smb1_enabled": posture.smb.smb1_enabled, "smb2_enabled": posture.smb.smb2_enabled}
        if not posture.smb.smb1_enabled:
            return RuleOutcome("pass", "SMBv1 server protocol is disabled.", method, clean_evidence=state)
        return RuleOutcome(
            "fail", "SMBv1 server protocol is enabled.", method,
            [
                Violation(
                    subject="smb1-server",
                    title="SMBv1 server protocol is enabled",
                    observation="The SMB server configuration reports EnableSMB1Protocol=True.",
                    inference=None,
                    impact=(
                        "SMBv1 is a deprecated protocol without modern integrity protections. Microsoft "
                        "recommends disabling it; only very old devices still require it."
                    ),
                    severity="medium",
                    severity_rationale=(
                        "A deprecated protocol is enabled. Rated medium (not high) because ObserveNet only "
                        "read the setting and did not test whether the SMB port is reachable or vulnerable."
                    ),
                    confidence="high",
                    evidence=state,
                    remediation=[
                        ("In an elevated PowerShell window, disable the SMBv1 server protocol.",
                         "Set-SmbServerConfiguration -EnableSMB1Protocol $false -Force"),
                        ("Confirm the state.", "Get-SmbServerConfiguration | Select EnableSMB1Protocol"),
                        ("Return here and choose Verify fix.", None),
                    ],  # fmt: skip
                )
            ],
            {"smb1_enabled": False, "smb2_enabled": posture.smb.smb2_enabled},
        )  # fmt: skip


class DockerPublishedPortRule:
    meta = RuleMeta(
        rule_id="DOCKER-PORT-001",
        version=1,
        title="Container port published on all interfaces",
        description=(
            "Lists container ports published to 0.0.0.0/:: that are not on the Docker port allowlist. "
            "Requires the opt-in, read-only Docker inventory collector."
        ),
        platform="linux",
        section="docker",
        category="security",
        default_severity="low",
        classification="POTENTIAL_EXPOSURE",
    )

    def evaluate(self, posture: Posture, ctx: RuleContext) -> RuleOutcome:
        unreadable = _section_unreadable(posture.docker, "Docker published ports")
        if unreadable:
            return unreadable
        assert posture.docker is not None
        allow = set(ctx.settings.docker_port_allowlist)
        violations = []
        for p in sorted(posture.docker.published_ports, key=lambda x: (x.host_port, x.proto, x.container)):
            if p.host_address not in WILDCARDS or f"{p.proto}/{p.host_port}" in allow:
                continue
            service = SENSITIVE_PORTS.get(p.host_port) or SENSITIVE_PORTS.get(p.container_port)
            violations.append(
                Violation(
                    subject=f"{p.container}:{p.proto}/{p.host_port}",
                    title=f"Container '{p.container}' publishes {p.proto.upper()} port {p.host_port} on all interfaces",
                    observation=(
                        f"Docker reports container '{p.container}' publishing {p.host_address}:{p.host_port} "
                        f"-> {p.container_port}/{p.proto}."
                    ),
                    inference=(
                        "Inferred, not tested: Docker-published ports usually bypass host firewall rules "
                        "(ufw/firewalld), so the port may be reachable from the LAN. ObserveNet did not connect to it."
                    ),
                    impact="A container service may be reachable by every device on the networks this host is attached to.",
                    severity="medium" if service else "low",
                    severity_rationale=(
                        f"The port is commonly used by {service}." if service
                        else "Publishing on all interfaces is Docker's default and frequently intended."
                    ),
                    confidence="high",
                    evidence={"container": p.container, "image": p.image, "proto": p.proto,
                              "host_address": p.host_address, "host_port": p.host_port,
                              "container_port": p.container_port},  # fmt: skip
                    remediation=[
                        ("If the service is only for this host or a reverse proxy, publish it on loopback: "
                         f"change the mapping to 127.0.0.1:{p.host_port}:{p.container_port} in the compose "
                         "file or `docker run -p` and recreate the container.", None),
                        (f"If it is intended, add '{p.proto}/{p.host_port}' to the Docker port allowlist in Settings.", None),
                    ],  # fmt: skip
                )
            )
        total = len(posture.docker.published_ports)
        method = posture.docker.method
        if violations:
            return RuleOutcome("fail", f"{len(violations)} published port(s) on all interfaces (of {total}).",
                               method, violations, {"published": False, "published_port_count": total})  # fmt: skip
        return RuleOutcome("pass", f"No unexpected wildcard-published ports among {total}.", method,
                           clean_evidence={"published": False, "published_port_count": total})  # fmt: skip


class RouterUpnpRule:
    """Catalog entry only: no provider can read router UPnP/port-forward state yet."""

    meta = RuleMeta(
        rule_id="RTR-UPNP-001",
        version=1,
        title="Router UPnP / port-forward exposure",
        description=(
            "Would list UPnP state and port forwards from a read-only router provider. No supported "
            "provider reports these settings yet, so this check is always Unsupported."
        ),
        platform="router",
        section="router",
        category="security",
        default_severity="medium",
        classification="OBSERVED_CONFIGURATION",
    )

    def evaluate(self, posture: Posture, ctx: RuleContext) -> RuleOutcome:
        return RuleOutcome("unsupported", "No read-only router provider reports UPnP or port-forward settings.")


STALE_RULE = RuleMeta(
    rule_id="OBS-STALE-001",
    version=1,
    title="Monitoring agent stopped reporting",
    description="An agent that previously reported has been silent longer than the disconnect threshold.",
    platform="any",
    section="heartbeat",
    category="observability",
    default_severity="low",
    classification="OBSERVED_CONFIGURATION",
)

RULES: list[Rule] = [
    WildcardListenerRule("LNX-LISTEN-001", "linux"),
    WildcardListenerRule("WIN-LISTEN-001", "windows"),
    WindowsFirewallRule(),
    Smb1Rule(),
    DockerPublishedPortRule(),
    RouterUpnpRule(),
]
ALL_META: list[RuleMeta] = [r.meta for r in RULES] + [STALE_RULE]


def rules_for(platform: str) -> list[Rule]:
    return [r for r in RULES if r.meta.platform == platform]
