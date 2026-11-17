"""Assessment + verification engine.

Find  -> ``assess`` runs every applicable rule over a posture snapshot and upserts findings.
Fix   -> manual; ObserveNet only shows instructions and never changes a host.
Verify-> the *same* rule is re-run on a snapshot collected strictly after the request.
         A finding becomes ``resolved`` only when fresh evidence shows the condition gone.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from observenet.core.time import utcnow
from observenet.models import (
    Assessment,
    Asset,
    Check,
    CheckResult,
    DataSource,
    Finding,
    FindingEvidence,
    PostureSnapshot,
    RemediationStep,
    VerificationDiff,
    VerificationRun,
)
from observenet.schemas.ingest import Posture
from observenet.security.rules import (
    ALL_META,
    ENGINE_VERSION,
    STALE_RULE,
    VERIFY_ALGORITHM_VERSION,
    RuleContext,
    RuleMeta,
    RuleOutcome,
    Violation,
    rules_for,
)
from observenet.services.events import add_event
from observenet.services.settings import PostureSettings

ACTIVE_STATUSES = ("open", "acknowledged", "snoozed")


def digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def diff_evidence(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Flat, deterministic field diff used for before/after verification records."""
    changes = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if b != a:
            changes.append({"field": key, "before": b, "after": a})
    return changes


async def sync_check_catalog(session: AsyncSession) -> None:
    for meta in ALL_META:
        stmt = pg_insert(Check).values(
            rule_id=meta.rule_id, version=meta.version, title=meta.title, description=meta.description,
            platform=meta.platform, section=meta.section, category=meta.category,
            default_severity=meta.default_severity, classification=meta.classification,
        )  # fmt: skip
        update_cols = {c: getattr(stmt.excluded, c) for c in (
            "version", "title", "description", "platform", "section", "category", "default_severity", "classification",
        )}  # fmt: skip
        await session.execute(stmt.on_conflict_do_update(index_elements=["rule_id"], set_=update_cols))


async def _latest_observed_evidence(session: AsyncSession, finding_id: int) -> FindingEvidence | None:
    return (
        await session.execute(
            select(FindingEvidence)
            .where(FindingEvidence.finding_id == finding_id, FindingEvidence.observed.is_(True))
            .order_by(FindingEvidence.collected_at.desc(), FindingEvidence.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _pending_runs(session: AsyncSession, finding_id: int) -> list[VerificationRun]:
    return list(
        (
            await session.execute(
                select(VerificationRun)
                .where(VerificationRun.finding_id == finding_id, VerificationRun.status == "pending")
                .order_by(VerificationRun.requested_at)
            )
        ).scalars()
    )


def _complete_run(
    session: AsyncSession,
    run: VerificationRun,
    *,
    outcome: str,
    reason: str,
    before: FindingEvidence | None,
    after: FindingEvidence | None,
    now: datetime,
) -> None:
    run.status = "completed"
    run.outcome = outcome
    run.outcome_reason = reason
    run.completed_at = now
    run.before_evidence_id = before.id if before else None
    run.after_evidence_id = after.id if after else None
    before_data = before.data if before else {}
    after_data = after.data if after else {}
    session.add(
        VerificationDiff(
            verification_run_id=run.id, summary=reason, before=before_data, after=after_data,
            changes=diff_evidence(before_data, after_data) if after else [],
        )  # fmt: skip
    )


def _new_evidence(
    finding: Finding, *, kind: str, observed: bool, snapshot: PostureSnapshot, assessment: Assessment,
    method: str, summary: str, data: dict[str, Any],
) -> FindingEvidence:  # fmt: skip
    return FindingEvidence(
        finding_id=finding.id, assessment_id=assessment.id, snapshot_id=snapshot.id, source_id=snapshot.source_id,
        kind=kind, observed=observed, collected_at=snapshot.collected_at,
        collection_method=method or "agent posture snapshot", summary=summary, data=data,
    )  # fmt: skip


async def _open_finding(
    session: AsyncSession, *, meta: RuleMeta, v: Violation, asset: Asset, source: DataSource,
    snapshot: PostureSnapshot, assessment: Assessment, method: str,
) -> Finding:  # fmt: skip
    finding = Finding(
        asset_id=asset.id, source_id=source.id, rule_id=meta.rule_id, rule_version=meta.version,
        subject=v.subject, category=meta.category, title=v.title, observation=v.observation,
        inference=v.inference, impact=v.impact, classification=meta.classification, severity=v.severity,
        severity_rationale=v.severity_rationale, confidence=v.confidence, status="open",
        first_observed_at=snapshot.collected_at, last_observed_at=snapshot.collected_at,
        status_changed_at=snapshot.collected_at, is_demo=source.is_demo,
    )  # fmt: skip
    session.add(finding)
    await session.flush()
    session.add(_new_evidence(finding, kind="initial", observed=True, snapshot=snapshot, assessment=assessment,
                              method=method, summary=v.observation, data=v.evidence))  # fmt: skip
    for pos, (instruction, command) in enumerate(v.remediation, start=1):
        session.add(RemediationStep(finding_id=finding.id, position=pos, instruction=instruction, command=command))
    add_event(session, "finding.opened", f"{v.title} on {asset.display_name or asset.hostname or f'device #{asset.id}'}",
              severity="warning", asset_id=asset.id, source_id=source.id, finding_id=finding.id,
              data={"rule_id": meta.rule_id, "severity": v.severity}, is_demo=source.is_demo)  # fmt: skip
    return finding


async def assess(
    session: AsyncSession,
    *,
    source: DataSource,
    asset: Asset,
    snapshot: PostureSnapshot,
    posture: Posture,
    settings: PostureSettings,
    trigger: str = "scheduled",
) -> Assessment:
    """Run all rules for the source's platform against one snapshot. Idempotent per snapshot."""
    now = utcnow()
    platform = source.platform or "linux"
    ctx = RuleContext(platform=platform, settings=settings)
    assessment = Assessment(
        asset_id=asset.id, source_id=source.id, snapshot_id=snapshot.id, trigger=trigger,
        engine_version=ENGINE_VERSION, started_at=now,
    )  # fmt: skip
    session.add(assessment)
    await session.flush()

    counts = {"pass": 0, "fail": 0, "unsupported": 0, "unknown": 0}
    opened: list[int] = []
    resolved: list[int] = []
    for rule in rules_for(platform):
        outcome = rule.evaluate(posture, ctx)
        counts[outcome.status] += 1
        session.add(
            CheckResult(
                assessment_id=assessment.id, rule_id=rule.meta.rule_id, rule_version=rule.meta.version,
                status=outcome.status, summary=outcome.summary, failing_subjects=len(outcome.violations),
            )  # fmt: skip
        )
        o, r = await _apply_outcome(session, rule.meta, outcome, asset=asset, source=source,
                                    snapshot=snapshot, assessment=assessment, now=now)  # fmt: skip
        opened += o
        resolved += r

    assessment.finished_at = utcnow()
    assessment.summary = {**counts, "opened_finding_ids": opened, "resolved_finding_ids": resolved}
    return assessment


async def _apply_outcome(
    session: AsyncSession, meta: RuleMeta, outcome: RuleOutcome, *, asset: Asset, source: DataSource,
    snapshot: PostureSnapshot, assessment: Assessment, now: datetime,
) -> tuple[list[int], list[int]]:  # fmt: skip
    opened: list[int] = []
    resolved: list[int] = []
    existing = {
        f.subject: f
        for f in (
            await session.execute(select(Finding).where(Finding.asset_id == asset.id, Finding.rule_id == meta.rule_id))
        ).scalars()
    }
    conclusive = outcome.status in ("pass", "fail")
    violating = {v.subject: v for v in outcome.violations}

    for subject, v in violating.items():
        finding = existing.get(subject)
        if finding is None:
            finding = await _open_finding(session, meta=meta, v=v, asset=asset, source=source, snapshot=snapshot,
                                          assessment=assessment, method=outcome.method)  # fmt: skip
            opened.append(finding.id)
            continue

        before = await _latest_observed_evidence(session, finding.id)
        pending = [r for r in await _pending_runs(session, finding.id) if snapshot.collected_at > r.requested_at]
        reopened = finding.status in ("resolved", "closed_unverified")
        changed = before is None or digest(before.data) != digest(v.evidence)
        evidence: FindingEvidence | None = None
        if pending or reopened or changed:
            evidence = _new_evidence(
                finding, kind="recheck" if pending else "changed", observed=True, snapshot=snapshot,
                assessment=assessment, method=outcome.method, summary=v.observation, data=v.evidence,
            )  # fmt: skip
            session.add(evidence)
            await session.flush()
        finding.last_observed_at = max(finding.last_observed_at, snapshot.collected_at)
        finding.observation, finding.title = v.observation, v.title
        finding.severity, finding.severity_rationale = v.severity, v.severity_rationale
        for run in pending:
            _complete_run(session, run, outcome="still_observed", before=before, after=evidence, now=now,
                          reason="The same check still observes the condition in evidence collected after the request.")  # fmt: skip
            finding.verification_status = "still_observed"
            add_event(session, "finding.verify_still_observed", f"Verification: still observed — {finding.title}",
                      severity="warning", asset_id=asset.id, finding_id=finding.id, is_demo=finding.is_demo)  # fmt: skip
        if reopened:
            finding.status = "open"
            finding.classification = meta.classification
            finding.resolved_at = None
            finding.status_changed_at = now
            finding.verification_status = "not_requested" if not pending else finding.verification_status
            opened.append(finding.id)
            add_event(session, "finding.reopened", f"Observed again: {finding.title}", severity="warning",
                      asset_id=asset.id, finding_id=finding.id, is_demo=finding.is_demo)  # fmt: skip

    for subject, finding in existing.items():
        if subject in violating or finding.status == "resolved":
            continue
        pending = [r for r in await _pending_runs(session, finding.id) if snapshot.collected_at > r.requested_at]
        if not conclusive:
            # The rule could not read its input: we cannot say anything about this finding.
            for run in pending:
                _complete_run(session, run, outcome="unknown", before=None, after=None, now=now,
                              reason=f"Fresh evidence arrived but the check was inconclusive: {outcome.summary}")  # fmt: skip
                finding.verification_status = "unknown"
            continue
        if snapshot.collected_at <= finding.last_observed_at:
            continue  # not newer than the evidence that showed the problem
        before = await _latest_observed_evidence(session, finding.id)
        after = _new_evidence(
            finding, kind="recheck", observed=False, snapshot=snapshot, assessment=assessment, method=outcome.method,
            summary=f"Condition no longer observed. {outcome.summary}", data=outcome.clean_evidence,
        )  # fmt: skip
        session.add(after)
        await session.flush()
        reason = "The same check no longer observes the condition in evidence collected after the finding was last seen."
        if not pending:
            run = VerificationRun(
                finding_id=finding.id, trigger="continuous", requested_at=snapshot.collected_at,
                deadline_at=snapshot.collected_at, algorithm_version=VERIFY_ALGORITHM_VERSION,
            )  # fmt: skip
            session.add(run)
            await session.flush()
            pending = [run]
        for run in pending:
            _complete_run(session, run, outcome="resolved", reason=reason, before=before, after=after, now=now)
        finding.status = "resolved"
        finding.classification = "VERIFIED_RESOLUTION"
        finding.verification_status = "resolved"
        finding.resolved_at = snapshot.collected_at
        finding.status_changed_at = now
        finding.snoozed_until = None
        resolved.append(finding.id)
        add_event(session, "finding.resolved", f"Verified resolved: {finding.title}", asset_id=asset.id,
                  finding_id=finding.id, is_demo=finding.is_demo)  # fmt: skip
    return opened, resolved


async def request_verification(
    session: AsyncSession, *, finding: Finding, user_id: int | None, timeout_s: int
) -> VerificationRun:
    """Ask the finding's source for fresh evidence. Completion happens in ``assess``."""
    now = utcnow()
    run = VerificationRun(
        finding_id=finding.id, trigger="manual", requested_by=user_id, requested_at=now,
        deadline_at=now + timedelta(seconds=timeout_s), algorithm_version=VERIFY_ALGORITHM_VERSION,
    )  # fmt: skip
    session.add(run)
    finding.verification_status = "pending"
    if finding.source_id is not None:
        source = await session.get(DataSource, finding.source_id)
        if source is not None:
            source.posture_requested_at = now
    await session.flush()
    add_event(session, "finding.verify_requested", f"Verification requested: {finding.title}",
              asset_id=finding.asset_id, finding_id=finding.id, is_demo=finding.is_demo)  # fmt: skip
    return run


async def expire_verification_runs(session: AsyncSession, now: datetime | None = None) -> int:
    """Pending runs past their deadline end as ``unknown`` — never as resolved."""
    now = now or utcnow()
    runs = list(
        (
            await session.execute(
                select(VerificationRun).where(VerificationRun.status == "pending", VerificationRun.deadline_at < now)
            )
        ).scalars()
    )
    for run in runs:
        _complete_run(session, run, outcome="unknown", before=None, after=None, now=now,
                      reason="No fresh evidence was received from the collector before the deadline, "
                             "so the result is unknown. The finding was left unchanged.")  # fmt: skip
        finding = await session.get(Finding, run.finding_id)
        if finding is not None and finding.verification_status == "pending":
            finding.verification_status = "unknown"
            add_event(session, "finding.verify_unknown", f"Verification inconclusive: {finding.title}",
                      asset_id=finding.asset_id, finding_id=finding.id, is_demo=finding.is_demo)  # fmt: skip
    return len(runs)


async def evaluate_agent_staleness(session: AsyncSession, *, disconnected_after_s: int, now: datetime | None = None) -> None:
    """OBS-STALE-001: an agent that used to report has gone silent (observability finding)."""
    now = now or utcnow()
    sources = (
        await session.execute(
            select(DataSource).where(
                DataSource.kind.in_(("linux_agent", "windows_agent", "demo")),
                DataSource.enabled.is_(True),
                DataSource.asset_id.is_not(None),
                DataSource.last_seen_at.is_not(None),
            )
        )
    ).scalars()
    for source in sources:
        assert source.last_seen_at is not None and source.asset_id is not None
        age = (now - source.last_seen_at).total_seconds()
        subject = f"source/{source.id}"
        finding = (
            await session.execute(
                select(Finding).where(
                    Finding.asset_id == source.asset_id, Finding.rule_id == STALE_RULE.rule_id, Finding.subject == subject
                )
            )
        ).scalar_one_or_none()
        silent = age > disconnected_after_s
        if silent and (finding is None or finding.status in ("resolved", "closed_unverified")):
            data = {"last_report_at": source.last_seen_at.isoformat(), "threshold_s": disconnected_after_s}
            observation = (
                f"No telemetry received from '{source.name}' since {source.last_seen_at.isoformat(timespec='seconds')} "
                f"(threshold {disconnected_after_s}s)."
            )
            if finding is None:
                finding = Finding(
                    asset_id=source.asset_id, source_id=source.id, rule_id=STALE_RULE.rule_id,
                    rule_version=STALE_RULE.version, subject=subject, category="observability",
                    title=f"Agent '{source.name}' stopped reporting", observation=observation,
                    inference="This says nothing about why: the host may be off, asleep, disconnected, or the agent stopped.",
                    impact="Metrics, device sightings and posture checks from this observation point are not being updated.",
                    classification=STALE_RULE.classification, severity="low",
                    severity_rationale="A monitoring gap, not a security weakness.", confidence="high",
                    first_observed_at=now, last_observed_at=now, is_demo=source.is_demo,
                )  # fmt: skip
                session.add(finding)
                await session.flush()
                session.add(RemediationStep(finding_id=finding.id, position=1, command="docker compose ps collector",
                                            instruction="Check that the host is on and the agent is running."))  # fmt: skip
                add_event(session, "finding.opened", finding.title, severity="warning", asset_id=finding.asset_id,
                          source_id=source.id, finding_id=finding.id, is_demo=source.is_demo)  # fmt: skip
            else:
                finding.status, finding.classification = "open", STALE_RULE.classification
                finding.observation, finding.last_observed_at = observation, now
                finding.resolved_at, finding.status_changed_at = None, now
                finding.verification_status = "not_requested"
            session.add(FindingEvidence(finding_id=finding.id, source_id=source.id, kind="initial", observed=True,
                                        collected_at=now, collection_method="backend heartbeat ledger",
                                        summary=observation, data=data))  # fmt: skip
        elif silent and finding is not None and finding.status in ACTIVE_STATUSES:
            finding.last_observed_at = now
        elif not silent and finding is not None and finding.status in ACTIVE_STATUSES:
            before = await _latest_observed_evidence(session, finding.id)
            after = FindingEvidence(
                finding_id=finding.id, source_id=source.id, kind="recheck", observed=False, collected_at=now,
                collection_method="backend heartbeat ledger",
                summary=f"Agent is reporting again (last report {source.last_seen_at.isoformat(timespec='seconds')}).",
                data={"last_report_at": source.last_seen_at.isoformat(), "threshold_s": disconnected_after_s},
            )  # fmt: skip
            session.add(after)
            await session.flush()
            run = VerificationRun(finding_id=finding.id, trigger="continuous", requested_at=now, deadline_at=now,
                                  algorithm_version=VERIFY_ALGORITHM_VERSION)  # fmt: skip
            session.add(run)
            await session.flush()
            _complete_run(session, run, outcome="resolved", before=before, after=after, now=now,
                          reason="Fresh telemetry was received from the agent.")  # fmt: skip
            finding.status, finding.classification = "resolved", "VERIFIED_RESOLUTION"
            finding.verification_status, finding.resolved_at, finding.status_changed_at = "resolved", now, now
            add_event(session, "finding.resolved", f"Verified resolved: {finding.title}", asset_id=finding.asset_id,
                      finding_id=finding.id, is_demo=finding.is_demo)  # fmt: skip
