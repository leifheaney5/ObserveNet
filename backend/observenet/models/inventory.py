"""Inventory: data sources, capabilities, assets and what was observed about them."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from observenet.models.base import Base

SOURCE_KINDS = ("linux_agent", "windows_agent", "router_snmp", "client_import", "demo")
CAPABILITY_STATUSES = ("supported", "unsupported", "unavailable", "disabled")
ASSET_TYPES = (
    "unknown", "server", "desktop", "laptop", "phone", "tablet", "iot", "printer",
    "router", "switch", "access_point", "tv", "console", "nas", "other",
)  # fmt: skip
CONFIDENCE = ("high", "medium", "low")


class DataSource(Base):
    __tablename__ = "data_sources"
    __table_args__ = (CheckConstraint(f"kind IN {SOURCE_KINDS}", name="kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(120))
    agent_uid: Mapped[str | None] = mapped_column(String(64), unique=True)
    platform: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[str | None] = mapped_column(String(32))
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    observation_point: Mapped[str] = mapped_column(String(48), default="host")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    expected_interval_s: Mapped[float] = mapped_column(Float, default=1.0)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    posture_requested_at: Mapped[datetime | None]

    capabilities: Mapped[list[Capability]] = relationship(
        back_populates="source", cascade="all, delete-orphan", lazy="selectin"
    )


class Capability(Base):
    """What a source can and cannot observe. Drives every 'Unsupported' label in the UI."""

    __tablename__ = "capabilities"
    __table_args__ = (
        UniqueConstraint("source_id", "name"),
        CheckConstraint(f"status IN {CAPABILITY_STATUSES}", name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    source: Mapped[DataSource] = relationship(back_populates="capabilities")


class CollectorHeartbeat(Base):
    __tablename__ = "collector_heartbeats"
    __table_args__ = (Index("ix_collector_heartbeats_source_ts", "source_id", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"))
    ts: Mapped[datetime]
    cpu_pct: Mapped[float | None] = mapped_column(Float)
    mem_pct: Mapped[float | None] = mapped_column(Float)
    uptime_s: Mapped[float | None] = mapped_column(Float)
    agent_version: Mapped[str | None] = mapped_column(String(32))
    errors: Mapped[dict[str, Any]] = mapped_column(default=dict)


class NetworkSegment(Base):
    __tablename__ = "network_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cidr: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str | None] = mapped_column(String(120))
    origin: Mapped[str] = mapped_column(String(16), default="observed")  # observed | configured
    first_seen: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(server_default=func.now())
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(f"asset_type IN {ASSET_TYPES}", name="asset_type"),
        CheckConstraint(f"confidence IN {CONFIDENCE}", name="confidence"),
        Index("ix_assets_last_seen_id", "last_seen", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    hostname: Mapped[str | None] = mapped_column(String(255))
    asset_type: Mapped[str] = mapped_column(String(24), default="unknown")
    group_name: Mapped[str | None] = mapped_column(String(80))
    owner: Mapped[str | None] = mapped_column(String(120))
    tags: Mapped[list[str]] = mapped_column(default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    vendor: Mapped[str | None] = mapped_column(String(160))
    vendor_method: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[str] = mapped_column(String(8), default="low")
    confidence_reason: Mapped[str | None] = mapped_column(Text)
    mac_randomized: Mapped[bool] = mapped_column(Boolean, default=False)
    managed: Mapped[bool] = mapped_column(Boolean, default=False)  # has a reporting agent
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[datetime | None]
    last_seen: Mapped[datetime | None]
    last_seen_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL", use_alter=True)
    )
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    identities: Mapped[list[AssetIdentity]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", lazy="noload"
    )


class AssetIdentity(Base):
    """A key that identifies an asset. (kind, value) is globally unique.

    kinds: agent_uid (strong), mac (strong unless locally administered), hostname (weak),
    ip_only (weak; scoped to the reporting source).
    """

    __tablename__ = "asset_identities"
    __table_args__ = (UniqueConstraint("kind", "value"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    value: Mapped[str] = mapped_column(String(255))
    confidence: Mapped[str] = mapped_column(String(8), default="low")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))
    original_asset_id: Mapped[int | None] = mapped_column(Integer)  # set while merged
    first_seen: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(server_default=func.now())

    asset: Mapped[Asset] = relationship(back_populates="identities")


class Interface(Base):
    __tablename__ = "interfaces"
    __table_args__ = (UniqueConstraint("asset_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(64))
    mac: Mapped[str | None] = mapped_column(String(17))
    is_up: Mapped[bool | None] = mapped_column(Boolean)
    link_speed_mbps: Mapped[int | None] = mapped_column(Integer)  # PHY/link rate, NOT throughput
    mtu: Mapped[int | None] = mapped_column(Integer)
    addresses: Mapped[list[Any]] = mapped_column(default=list)
    is_default_route: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(server_default=func.now())


class AddressObservation(Base):
    """An (ip, mac) sighting of an asset by one source. Weak evidence unless stated otherwise."""

    __tablename__ = "address_observations"
    __table_args__ = (
        UniqueConstraint("asset_id", "ip", "mac", "source_id", "kind", name="uq_address_observation"),
        Index("ix_address_observations_ip", "ip"),
        Index("ix_address_observations_last_seen", "last_seen"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"))
    segment_id: Mapped[int | None] = mapped_column(ForeignKey("network_segments.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(24))  # neighbor_cache | agent_self | client_import
    ip: Mapped[str] = mapped_column(String(45))
    mac: Mapped[str] = mapped_column(String(17), default="")
    hostname: Mapped[str | None] = mapped_column(String(255))
    original_asset_id: Mapped[int | None] = mapped_column(Integer)
    first_seen: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(server_default=func.now())


class ObservedService(Base):
    """A listening socket or published container port reported by an agent on the asset itself."""

    __tablename__ = "observed_services"
    __table_args__ = (
        UniqueConstraint("asset_id", "origin", "proto", "address", "port", name="uq_observed_service"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))
    origin: Mapped[str] = mapped_column(String(24), default="host_socket")  # host_socket | docker_published
    proto: Mapped[str] = mapped_column(String(8))
    address: Mapped[str] = mapped_column(String(64))
    port: Mapped[int] = mapped_column(Integer)
    process: Mapped[str | None] = mapped_column(String(120))
    container: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(server_default=func.now())


class AssetMerge(Base):
    __tablename__ = "asset_merges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    target_asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    merged_by: Mapped[int | None] = mapped_column(Integer)
    merged_at: Mapped[datetime] = mapped_column(server_default=func.now())
    undone_at: Mapped[datetime | None]
    undone_by: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
