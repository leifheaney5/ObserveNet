"""Time-series storage.

``metric_series`` carries provenance once per series (source, unit, observation point,
quality, sample interval, coverage); ``metric_samples`` is a narrow, range-partitioned
(series_id, ts, value) fact table. Rollups are idempotent upserts keyed by (series, bucket).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from observenet.models.base import Base

OBSERVATION_POINTS = (
    "host_nic",  # counters of one host's own interface
    "gateway_wan",  # router WAN interface counters
    "gateway_per_device",  # router/flow per-client accounting
    "host",  # host-local resource (cpu/mem)
    "active_probe",  # latency/reachability probe result
    "capacity_test",  # opt-in speed test (NOT utilization)
)
QUALITIES = ("measured", "derived", "synthetic")


class MetricSeries(Base):
    __tablename__ = "metric_series"
    __table_args__ = (
        UniqueConstraint("source_id", "metric", "label"),
        CheckConstraint(f"observation_point IN {OBSERVATION_POINTS}", name="observation_point"),
        CheckConstraint(f"quality IN {QUALITIES}", name="quality"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"), index=True)
    interface_id: Mapped[int | None] = mapped_column(ForeignKey("interfaces.id", ondelete="SET NULL"))
    probe_target_id: Mapped[int | None] = mapped_column(Integer)
    metric: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(120), default="")  # interface name, "target:<id>", ...
    unit: Mapped[str] = mapped_column(String(16))
    observation_point: Mapped[str] = mapped_column(String(32))
    quality: Mapped[str] = mapped_column(String(16), default="measured")
    interval_s: Mapped[float] = mapped_column(Float, default=1.0)
    coverage: Mapped[str | None] = mapped_column(Text)
    first_ts: Mapped[datetime | None]
    last_ts: Mapped[datetime | None]


class MetricSample(Base):
    """Raw samples (default 1 s). Declaratively range-partitioned by day on ``ts``."""

    __tablename__ = "metric_samples"
    __table_args__ = (
        PrimaryKeyConstraint("series_id", "ts"),
        {"postgresql_partition_by": "RANGE (ts)"},
    )

    series_id: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime]
    value: Mapped[float] = mapped_column(Float)


class _RollupMixin:
    series_id: Mapped[int] = mapped_column(Integer)
    bucket: Mapped[datetime]
    avg: Mapped[float] = mapped_column(Float)
    min: Mapped[float] = mapped_column(Float)
    max: Mapped[float] = mapped_column(Float)
    sum: Mapped[float] = mapped_column(Float)
    count: Mapped[int] = mapped_column(Integer)


class MetricRollup1m(_RollupMixin, Base):
    __tablename__ = "metric_rollups_1m"
    __table_args__ = (PrimaryKeyConstraint("series_id", "bucket"),)


class MetricRollup1h(_RollupMixin, Base):
    __tablename__ = "metric_rollups_1h"
    __table_args__ = (PrimaryKeyConstraint("series_id", "bucket"),)


class IngestBatch(Base):
    """Idempotency ledger: a (source, batch_id) is applied at most once."""

    __tablename__ = "ingest_batches"
    __table_args__ = (PrimaryKeyConstraint("source_id", "batch_id"),)

    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"))
    batch_id: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(index=True)
    processed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    sample_count: Mapped[int] = mapped_column(Integer, default=0)


class JobState(Base):
    """Watermarks for idempotent background jobs (rollups, retention)."""

    __tablename__ = "job_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class ProbeTarget(Base):
    __tablename__ = "probe_targets"
    __table_args__ = (
        CheckConstraint("kind IN ('gateway_icmp','icmp','tcp','dns')", name="kind"),
        CheckConstraint("scope IN ('gateway','internal','external')", name="scope"),
        CheckConstraint("interval_s >= 1", name="interval_min"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(16))
    host: Mapped[str] = mapped_column(String(255))  # "auto" = the agent's default gateway
    port: Mapped[int | None] = mapped_column(Integer)
    interval_s: Mapped[float] = mapped_column(Float, default=30.0)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=1000)
    enabled: Mapped[bool] = mapped_column(default=True)
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    is_demo: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ProbeResult(Base):
    __tablename__ = "probe_results"
    __table_args__ = (UniqueConstraint("target_id", "source_id", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("probe_targets.id", ondelete="CASCADE"))
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(index=True)
    success: Mapped[bool]
    latency_ms: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(24))
    resolved_host: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)


class Outage(Base):
    """An interval where *multiple independent* external signals failed."""

    __tablename__ = "outages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(24), default="internet")
    started_at: Mapped[datetime] = mapped_column(index=True)
    ended_at: Mapped[datetime | None]
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
