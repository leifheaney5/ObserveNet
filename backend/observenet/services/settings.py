"""Runtime-tunable settings stored in PostgreSQL (one JSON document, validated by Pydantic)."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from observenet.models import Setting
from observenet.services.netscope import parse_approved_cidr, validate_discovery_cidr

KEY = "runtime"


class RetentionSettings(BaseModel):
    raw_hours: Annotated[int, Field(ge=1, le=24 * 14)] = 48
    rollup_1m_days: Annotated[int, Field(ge=1, le=730)] = 90
    rollup_1h_days: Annotated[int, Field(ge=7, le=3650)] = 365
    probe_results_hours: Annotated[int, Field(ge=1, le=24 * 30)] = 48
    events_days: Annotated[int, Field(ge=7, le=3650)] = 365
    posture_snapshots_days: Annotated[int, Field(ge=1, le=3650)] = 90


class CollectionSettings(BaseModel):
    sample_interval_s: Annotated[float, Field(ge=1, le=60)] = 1.0
    inventory_interval_s: Annotated[float, Field(ge=10, le=3600)] = 30.0
    posture_interval_s: Annotated[float, Field(ge=30, le=86400)] = 300.0


class FreshnessSettings(BaseModel):
    stale_after_s: Annotated[int, Field(ge=3, le=3600)] = 10
    disconnected_after_s: Annotated[int, Field(ge=10, le=86400)] = 90
    device_seen_recently_s: Annotated[int, Field(ge=30, le=86400 * 7)] = 600


class DiscoverySettings(BaseModel):
    """Opt-in low-rate ICMP sweep. Off by default; restricted to approved private CIDRs."""

    enabled: bool = False
    cidrs: list[str] = Field(default_factory=list, max_length=4)
    interval_minutes: Annotated[int, Field(ge=15, le=1440)] = 60
    max_probes_per_second: Annotated[float, Field(gt=0, le=10)] = 2.0


class SpeedtestSettings(BaseModel):
    """Opt-in capacity test: downloads a bounded payload from a user-chosen HTTPS URL."""

    enabled: bool = False
    download_url: str = ""
    max_bytes: Annotated[int, Field(ge=1_000_000, le=500_000_000)] = 25_000_000
    interval_hours: Annotated[float, Field(ge=3, le=168)] = 6.0
    requested_at: str | None = None  # ISO timestamp of an on-demand request

    @field_validator("download_url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        if v and not v.startswith("https://"):
            raise ValueError("speed test URL must be https://")
        return v


class PostureSettings(BaseModel):
    # "proto/port" entries that may listen on all interfaces without raising a finding.
    listener_allowlist: list[str] = Field(default_factory=lambda: ["tcp/22"], max_length=200)
    # Wildcard listeners every stock Windows installation has (RPC endpoint mapper, NetBIOS/SMB,
    # CDPSvc, WS-Discovery, Delivery Optimization, mDNS/LLMNR/SSDP, IPsec). SMB exposure is
    # judged by the firewall and SMBv1 checks instead. Untested against a real Windows host.
    windows_listener_allowlist: list[str] = Field(
        default_factory=lambda: [
            "tcp/135", "tcp/139", "tcp/445", "tcp/5040", "tcp/5357", "tcp/7680",
            "udp/137", "udp/138", "udp/500", "udp/1900", "udp/3702", "udp/4500", "udp/5050", "udp/5353", "udp/5355",
        ],
        max_length=200,
    )  # fmt: skip
    docker_port_allowlist: list[str] = Field(default_factory=list, max_length=200)
    verify_timeout_s: Annotated[int, Field(ge=15, le=900)] = 90


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    approved_cidrs: list[str] = Field(default_factory=list, max_length=16)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)
    collection: CollectionSettings = Field(default_factory=CollectionSettings)
    freshness: FreshnessSettings = Field(default_factory=FreshnessSettings)
    discovery: DiscoverySettings = Field(default_factory=DiscoverySettings)
    speedtest: SpeedtestSettings = Field(default_factory=SpeedtestSettings)
    posture: PostureSettings = Field(default_factory=PostureSettings)

    @field_validator("approved_cidrs")
    @classmethod
    def _private_only(cls, v: list[str]) -> list[str]:
        return [str(parse_approved_cidr(c)) for c in v]

    def validate_cross_fields(self) -> None:
        for cidr in self.discovery.cidrs:
            validate_discovery_cidr(cidr, self.approved_cidrs)
        if self.discovery.enabled and not self.discovery.cidrs:
            raise ValueError("discovery cannot be enabled without at least one approved sweep CIDR")
        if self.speedtest.enabled and not self.speedtest.download_url:
            raise ValueError("speed test cannot be enabled without a download URL")


async def get_runtime_settings(session: AsyncSession) -> RuntimeSettings:
    row = await session.get(Setting, KEY)
    return RuntimeSettings.model_validate(row.value) if row else RuntimeSettings()


async def save_runtime_settings(
    session: AsyncSession, settings: RuntimeSettings, user_id: int | None = None
) -> RuntimeSettings:
    settings.validate_cross_fields()
    row = await session.get(Setting, KEY)
    if row is None:
        row = Setting(key=KEY, value={})
        session.add(row)
    row.value = settings.model_dump(mode="json")
    row.updated_by = user_id
    await session.flush()
    return settings
