"""Read-only posture assessment: snapshots -> check results -> findings -> verification."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
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

# PROVEN_EXPLOITABLE is deliberately absent: ObserveNet never exploits anything.
CLASSIFICATIONS = ("OBSERVED_CONFIGURATION", "POTENTIAL_EXPOSURE", "VERIFIED_RESOLUTION")
FINDING_STATUSES = ("open", "acknowledged", "snoozed", "resolved", "closed_unverified")
VERIFY_OUTCOMES = ("resolved", "still_observed", "unknown")
CHECK_STATUSES = ("pass", "fail", "unsupported", "unknown")
SEVERITIES = ("info", "low", "medium", "high")


class PostureSnapshot(Base):
    __tablename__ = "posture_snapshots"
    __table_args__ = (Index("ix_posture_snapshots_source_collected", "source_id", "collected_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    collected_at: Mapped[datetime]
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Identical consecutive snapshots are not stored again; this records the latest re-confirmation.
    last_confirmed_at: Mapped[datetime | None]
    digest: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Check(Base):
    """Catalog of deterministic rules, synced from code at startup."""

    __tablename__ = "checks"

    rule_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(16))
    section: Mapped[str] = mapped_column(String(32))  # posture section this rule needs
    category: Mapped[str] = mapped_column(String(16), default="security")  # security | observability
    default_severity: Mapped[str] = mapped_column(String(8))
    classification: Mapped[str] = mapped_column(String(32))


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (Index("ix_assessments_asset_started", "asset_id", "started_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"))
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("posture_snapshots.id", ondelete="CASCADE"))
    trigger: Mapped[str] = mapped_column(String(16))  # scheduled | verify
    engine_version: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)

    results: Mapped[list[CheckResult]] = relationship(cascade="all, delete-orphan", lazy="noload")


class CheckResult(Base):
    __tablename__ = "check_results"
    __table_args__ = (CheckConstraint(f"status IN {CHECK_STATUSES}", name="status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[str] = mapped_column(String(32))
    rule_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text)
    failing_subjects: Mapped[int] = mapped_column(Integer, default=0)


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("asset_id", "rule_id", "subject", name="uq_finding_fingerprint"),
        CheckConstraint(f"status IN {FINDING_STATUSES}", name="status"),
        CheckConstraint(f"classification IN {CLASSIFICATIONS}", name="classification"),
        CheckConstraint(f"severity IN {SEVERITIES}", name="severity"),
        Index("ix_findings_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))
    rule_id: Mapped[str] = mapped_column(String(32))
    rule_version: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(200))  # e.g. "tcp/0.0.0.0:8080"
    category: Mapped[str] = mapped_column(String(16), default="security")
    title: Mapped[str] = mapped_column(String(240))
    observation: Mapped[str] = mapped_column(Text)  # what was actually observed (fact)
    inference: Mapped[str | None] = mapped_column(Text)  # what is inferred, labelled as such
    impact: Mapped[str] = mapped_column(Text)
    classification: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))
    severity_rationale: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24), default="open")
    verification_status: Mapped[str] = mapped_column(String(24), default="not_requested")
    first_observed_at: Mapped[datetime]
    last_observed_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None]
    snoozed_until: Mapped[datetime | None]
    status_changed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    is_demo: Mapped[bool] = mapped_column(default=False)

    evidence: Mapped[list[FindingEvidence]] = relationship(
        cascade="all, delete-orphan", lazy="noload", order_by="FindingEvidence.collected_at"
    )
    remediation_steps: Mapped[list[RemediationStep]] = relationship(
        cascade="all, delete-orphan", lazy="noload", order_by="RemediationStep.position"
    )


class FindingEvidence(Base):
    __tablename__ = "finding_evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    assessment_id: Mapped[int | None] = mapped_column(ForeignKey("assessments.id", ondelete="SET NULL"))
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("posture_snapshots.id", ondelete="SET NULL"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16))  # initial | changed | recheck
    observed: Mapped[bool]  # was the condition observed in this snapshot?
    collected_at: Mapped[datetime]
    collection_method: Mapped[str] = mapped_column(String(120))
    summary: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)


class RemediationStep(Base):
    __tablename__ = "remediation_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    instruction: Mapped[str] = mapped_column(Text)
    command: Mapped[str | None] = mapped_column(Text)  # shown to the user; never executed by ObserveNet


class VerificationRun(Base):
    __tablename__ = "verification_runs"
    __table_args__ = (
        CheckConstraint("status IN ('pending','completed')", name="status"),
        Index("ix_verification_runs_finding", "finding_id", "requested_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"))
    trigger: Mapped[str] = mapped_column(String(16))  # manual | continuous
    requested_by: Mapped[int | None] = mapped_column(Integer)
    requested_at: Mapped[datetime]
    deadline_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(16), default="pending")
    outcome: Mapped[str | None] = mapped_column(String(16))  # resolved | still_observed | unknown
    outcome_reason: Mapped[str | None] = mapped_column(Text)
    algorithm_version: Mapped[str] = mapped_column(String(32))
    before_evidence_id: Mapped[int | None] = mapped_column(BigInteger)
    after_evidence_id: Mapped[int | None] = mapped_column(BigInteger)
    completed_at: Mapped[datetime | None]

    diff: Mapped[VerificationDiff | None] = relationship(
        cascade="all, delete-orphan", lazy="noload", uselist=False
    )


class VerificationDiff(Base):
    __tablename__ = "verification_diffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    verification_run_id: Mapped[int] = mapped_column(
        ForeignKey("verification_runs.id", ondelete="CASCADE"), unique=True
    )
    summary: Mapped[str] = mapped_column(Text)
    before: Mapped[dict[str, Any]] = mapped_column(default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(default=dict)
    changes: Mapped[list[Any]] = mapped_column(default=list)
