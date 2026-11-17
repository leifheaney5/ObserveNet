from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from observenet.models.base import Base

ALERT_KINDS = (
    "gateway_latency_high",
    "packet_loss_high",
    "collector_heartbeat_missing",
    "interface_bandwidth_high",
    "new_device",
    "stale_source",
    "posture_changed",
)
ALERT_STATES = ("firing", "acknowledged", "resolved")


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (CheckConstraint(f"kind IN {ALERT_KINDS}", name="kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True)
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    # threshold, clear_threshold (hysteresis), window_s, for_s (debounce), cooldown_s, ...
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # evaluator state per dedup key: pending_since, last_resolved_at
    state: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint(f"state IN {ALERT_STATES}", name="state"),
        # Deduplication: at most one non-resolved alert per dedup key.
        Index(
            "uq_alerts_active_dedup",
            "dedup_key",
            unique=True,
            postgresql_where=text("state <> 'resolved'"),
        ),
        Index("ix_alerts_started_at", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("alert_rules.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(40))
    dedup_key: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(16), default="firing")
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))
    finding_id: Mapped[int | None] = mapped_column(Integer)
    source_ts: Mapped[datetime | None]  # timestamp of the measurement that triggered it
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_evaluated_at: Mapped[datetime | None]
    resolved_at: Mapped[datetime | None]
    resolved_reason: Mapped[str | None] = mapped_column(String(120))
    acknowledged_at: Mapped[datetime | None]
    acknowledged_by: Mapped[int | None] = mapped_column(Integer)
    snoozed_until: Mapped[datetime | None]
    is_demo: Mapped[bool] = mapped_column(default=False)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(16))  # in_app | webhook
    event: Mapped[str] = mapped_column(String(16))  # fired | resolved
    status: Mapped[str] = mapped_column(String(16))  # delivered | failed | suppressed
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Event(Base):
    """Append-only timeline: device joined/left, alert/finding transitions, config changes."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_ts", "ts"), Index("ix_events_asset_ts", "asset_id", "ts"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(server_default=func.now())
    kind: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))
    alert_id: Mapped[int | None] = mapped_column(Integer)
    finding_id: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    is_demo: Mapped[bool] = mapped_column(default=False)


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]
    alert_kinds: Mapped[list[str]] = mapped_column(default=list)  # empty = all kinds
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
