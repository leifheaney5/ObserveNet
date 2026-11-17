"""Collector -> backend wire contract (schema_version 1).

Every posture/inventory section carries an explicit ``status`` so the backend can
distinguish "checked and clean" from "could not check" and never guess.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from observenet.core.time import ensure_utc

SectionStatus = Literal["ok", "unavailable", "unsupported", "disabled"]
Str64 = Annotated[str, Field(max_length=64)]
Str255 = Annotated[str, Field(max_length=255)]

METRIC_UNITS: dict[str, str] = {
    "net.rx_bps": "bit/s",
    "net.tx_bps": "bit/s",
    "net.rx_pps": "packet/s",
    "net.tx_pps": "packet/s",
    "wan.rx_bps": "bit/s",
    "wan.tx_bps": "bit/s",
    "device.rx_bps": "bit/s",
    "device.tx_bps": "bit/s",
    "host.cpu_pct": "percent",
    "host.mem_pct": "percent",
    "probe.latency_ms": "ms",
    "probe.success": "bool",
    "probe.jitter_ms": "ms",
    "speedtest.download_bps": "bit/s",
    "speedtest.upload_bps": "bit/s",
}
METRIC_OBSERVATION_POINT: dict[str, str] = {
    "net.": "host_nic",
    "wan.": "gateway_wan",
    "device.": "gateway_per_device",
    "host.": "host",
    "probe.": "active_probe",
    "speedtest.": "capacity_test",
}


def observation_point_for(metric: str) -> str:
    for prefix, point in METRIC_OBSERVATION_POINT.items():
        if metric.startswith(prefix):
            return point
    raise ValueError(f"unknown metric {metric!r}")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Timestamped(_Model):
    @field_validator("ts", "collected_at", "sent_at", check_fields=False)
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)


class AgentInfo(_Model):
    agent_uid: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]
    hostname: Str255
    platform: Literal["linux", "windows", "router", "demo"]
    # DEMO_MODE only: which platform's rules/labels a synthetic agent stands in for.
    emulates: Literal["linux", "windows", "router"] | None = None
    agent_version: Annotated[str, Field(max_length=32)]


class Sample(_Timestamped):
    metric: Str64
    label: Annotated[str, Field(max_length=120)] = ""
    ts: datetime
    value: Annotated[float, Field(allow_inf_nan=False)]
    unit: Annotated[str, Field(max_length=16)]
    interval_s: Annotated[float, Field(gt=0, le=86400)] = 1.0
    quality: Literal["measured", "derived", "synthetic"] = "measured"

    @field_validator("metric")
    @classmethod
    def _known_metric(cls, v: str) -> str:
        if v not in METRIC_UNITS:
            raise ValueError(f"unknown metric {v!r}")
        return v


class ProbeResultIn(_Timestamped):
    target_id: int
    ts: datetime
    success: bool
    latency_ms: Annotated[float, Field(ge=0, le=600_000)] | None = None
    jitter_ms: Annotated[float, Field(ge=0, le=600_000)] | None = None
    method: Annotated[str, Field(max_length=24)]
    resolved_host: Str64 | None = None
    error: Annotated[str, Field(max_length=300)] | None = None


class CapabilityIn(_Model):
    name: Str64
    status: Literal["supported", "unsupported", "unavailable", "disabled"]
    detail: Annotated[str, Field(max_length=500)] | None = None


class Heartbeat(_Timestamped):
    ts: datetime
    cpu_pct: Annotated[float, Field(ge=0, le=100)] | None = None
    mem_pct: Annotated[float, Field(ge=0, le=100)] | None = None
    uptime_s: Annotated[float, Field(ge=0)] | None = None
    errors: dict[Str64, Annotated[str, Field(max_length=500)]] = Field(default_factory=dict, max_length=32)
    capabilities: list[CapabilityIn] = Field(default_factory=list, max_length=64)


class InterfaceAddress(_Model):
    family: Literal["ipv4", "ipv6"]
    address: Str64
    prefix_len: Annotated[int, Field(ge=0, le=128)] | None = None


class InterfaceIn(_Model):
    name: Str64
    mac: Annotated[str, Field(max_length=17)] | None = None
    is_up: bool | None = None
    link_speed_mbps: Annotated[int, Field(ge=0, le=1_000_000)] | None = None
    mtu: Annotated[int, Field(ge=0, le=65536)] | None = None
    addresses: list[InterfaceAddress] = Field(default_factory=list, max_length=64)


class NeighborIn(_Model):
    ip: Str64
    mac: Annotated[str, Field(max_length=17)] | None = None
    interface: Str64 | None = None
    state: Annotated[str, Field(max_length=24)] = "reachable"


class DefaultGateway(_Model):
    ip: Str64
    interface: Str64


class Inventory(_Timestamped):
    collected_at: datetime
    interfaces: list[InterfaceIn] = Field(default_factory=list, max_length=256)
    default_gateway: DefaultGateway | None = None
    neighbors_status: SectionStatus = "unavailable"
    neighbors: list[NeighborIn] = Field(default_factory=list, max_length=4096)


class ListeningSocket(_Model):
    proto: Literal["tcp", "udp"]
    address: Str64
    port: Annotated[int, Field(ge=1, le=65535)]
    process: Annotated[str, Field(max_length=120)] | None = None


class ListeningSection(_Model):
    status: SectionStatus
    reason: Annotated[str, Field(max_length=300)] | None = None
    method: Annotated[str, Field(max_length=120)] = ""
    items: list[ListeningSocket] = Field(default_factory=list, max_length=4096)


class FirewallProfile(_Model):
    name: Literal["Domain", "Private", "Public"]
    enabled: bool


class FirewallSection(_Model):
    status: SectionStatus
    reason: Annotated[str, Field(max_length=300)] | None = None
    method: Annotated[str, Field(max_length=120)] = ""
    profiles: list[FirewallProfile] = Field(default_factory=list, max_length=3)


class SmbSection(_Model):
    status: SectionStatus
    reason: Annotated[str, Field(max_length=300)] | None = None
    method: Annotated[str, Field(max_length=120)] = ""
    smb1_enabled: bool | None = None
    smb2_enabled: bool | None = None


class DockerPort(_Model):
    container: Annotated[str, Field(max_length=120)]
    image: Annotated[str, Field(max_length=200)] | None = None
    proto: Literal["tcp", "udp"]
    host_address: Str64
    host_port: Annotated[int, Field(ge=1, le=65535)]
    container_port: Annotated[int, Field(ge=1, le=65535)]


class DockerSection(_Model):
    status: SectionStatus
    reason: Annotated[str, Field(max_length=300)] | None = None
    method: Annotated[str, Field(max_length=120)] = ""
    published_ports: list[DockerPort] = Field(default_factory=list, max_length=2048)


class Posture(_Timestamped):
    collected_at: datetime
    listening: ListeningSection | None = None
    firewall: FirewallSection | None = None
    smb: SmbSection | None = None
    docker: DockerSection | None = None


class IngestPayload(_Timestamped):
    schema_version: Literal[1]
    agent: AgentInfo
    batch_id: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    sent_at: datetime
    samples: list[Sample] = Field(default_factory=list, max_length=5000)
    probe_results: list[ProbeResultIn] = Field(default_factory=list, max_length=2000)
    heartbeat: Heartbeat | None = None
    inventory: Inventory | None = None
    posture: Posture | None = None


class IngestAccepted(BaseModel):
    status: Literal["accepted"] = "accepted"
    batch_id: str
    samples: int
    config_version: str


class AgentProbeConfig(BaseModel):
    target_id: int
    kind: str
    host: str
    port: int | None
    interval_s: float
    timeout_ms: int
    params: dict[str, str | int | float | bool] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    """Server -> agent configuration. The agent re-validates everything locally."""

    config_version: str
    sample_interval_s: float
    inventory_interval_s: float
    posture_interval_s: float
    posture_requested_at: datetime | None
    probes: list[AgentProbeConfig]
    discovery: dict[str, object]
    speedtest: dict[str, object]
