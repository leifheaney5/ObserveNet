"""Find → (manual fix) → Verify. A finding is `resolved` only on fresh evidence from the same check."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from observenet.core.db import session_scope
from observenet.core.time import utcnow
from observenet.models import (
    Alert,
    Assessment,
    CheckResult,
    DataSource,
    Event,
    Finding,
    FindingEvidence,
    PostureSnapshot,
    RemediationStep,
    VerificationDiff,
    VerificationRun,
)
from observenet.models.security import CLASSIFICATIONS
from observenet.schemas.ingest import Posture
from observenet.security import engine
from observenet.security.rules import (
    ENGINE_VERSION,
    RULES,
    VERIFY_ALGORITHM_VERSION,
    DockerPublishedPortRule,
    RouterUpnpRule,
    RuleContext,
    Smb1Rule,
    WildcardListenerRule,
    WindowsFirewallRule,
)
from observenet.services.settings import PostureSettings, RuntimeSettings, save_runtime_settings
from tests import helpers as h

REDIS = ("tcp", "0.0.0.0", 6379)
WEB = ("tcp", "0.0.0.0", 8080)
CTX = RuleContext(platform="linux", settings=PostureSettings())


def parse(**sections: Any) -> Posture:
    return Posture.model_validate(h.posture(utcnow(), **sections))


async def findings() -> list[Finding]:
    async with session_scope() as session:
        return list((await session.execute(select(Finding).order_by(Finding.id))).scalars())


async def evidence(finding_id: int) -> list[FindingEvidence]:
    async with session_scope() as session:
        return list((await session.execute(select(FindingEvidence).where(FindingEvidence.finding_id == finding_id)
                                           .order_by(FindingEvidence.id))).scalars())  # fmt: skip


async def runs(finding_id: int) -> list[VerificationRun]:
    async with session_scope() as session:
        return list((await session.execute(select(VerificationRun).where(VerificationRun.finding_id == finding_id)
                                           .order_by(VerificationRun.id))).scalars())  # fmt: skip


async def request_verify(finding_id: int, timeout_s: int = 90) -> VerificationRun:
    async with session_scope() as session:
        finding = await session.get(Finding, finding_id)
        assert finding is not None
        return await engine.request_verification(session, finding=finding, user_id=None, timeout_s=timeout_s)


async def open_finding(
    sockets: tuple[tuple[str, str, int], ...] = (REDIS,), seconds_ago: float = 120
) -> tuple[int, Finding]:
    sid = await h.make_source()
    await h.process(sid, h.payload(posture=h.posture(h.ago(seconds_ago), listening=h.listening(*sockets))))
    return sid, (await findings())[0]


# ---------------------------------------------------------------- pure rules


def test_proven_exploitable_is_not_a_classification() -> None:
    assert "PROVEN_EXPLOITABLE" not in CLASSIFICATIONS
    assert set(CLASSIFICATIONS) == {"OBSERVED_CONFIGURATION", "POTENTIAL_EXPOSURE", "VERIFIED_RESOLUTION"}
    for rule in RULES:
        assert rule.meta.classification in ("OBSERVED_CONFIGURATION", "POTENTIAL_EXPOSURE")


def test_wildcard_listener_rule_separates_observation_from_inference() -> None:
    rule = WildcardListenerRule("LNX-LISTEN-001", "linux")
    out = rule.evaluate(parse(listening=h.listening(REDIS, ("tcp", "::", 6379), WEB, ("tcp", "127.0.0.1", 5432),
                                                    ("tcp", "192.168.1.5", 9000), ("tcp", "0.0.0.0", 22))), CTX)  # fmt: skip
    assert out.status == "fail"
    by_subject = {v.subject: v for v in out.violations}
    assert set(by_subject) == {
        "tcp/*:6379",
        "tcp/*:8080",
    }  # v4+v6 wildcard grouped; loopback, LAN-bound and allowlisted 22 ignored
    redis = by_subject["tcp/*:6379"]
    assert redis.severity == "medium" and "Redis" in redis.title and "Redis" in redis.severity_rationale
    assert by_subject["tcp/*:8080"].severity == "low"
    assert "0.0.0.0" in redis.observation and "::" in redis.observation
    assert (redis.inference or "").startswith("Inferred, not tested")
    assert "did not connect" in (redis.inference or "")
    assert redis.confidence == "high"
    assert len(redis.evidence["sockets"]) == 2 and redis.evidence["allowlisted"] is False
    assert redis.remediation and all(len(step) == 2 for step in redis.remediation)
    text_blob = " ".join(
        [redis.title, redis.observation, redis.inference or "", redis.impact, redis.severity_rationale]
    ).lower()
    assert "exploit" not in text_blob


def test_listener_allowlist_is_respected() -> None:
    rule = WildcardListenerRule("LNX-LISTEN-001", "linux")
    posture = parse(listening=h.listening(REDIS, WEB, ("udp", "0.0.0.0", 5353)))
    ctx = RuleContext(
        platform="linux", settings=PostureSettings(listener_allowlist=["tcp/6379", "tcp/8080", "udp/5353"])
    )
    out = rule.evaluate(posture, ctx)
    assert out.status == "pass" and out.violations == []
    assert out.clean_evidence == {"sockets": [], "listening_socket_count": 3}
    # the allowlist is per proto/port: tcp/5353 would not excuse udp/5353
    ctx2 = RuleContext(platform="linux", settings=PostureSettings(listener_allowlist=["tcp/5353"]))
    assert [
        v.subject
        for v in rule.evaluate(parse(listening=h.listening(("udp", "0.0.0.0", 5353))), ctx2).violations
    ] == ["udp/*:5353"]


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        (None, "unknown"),
        (h.listening(status="unavailable", reason="cannot read /proc/net/tcp"), "unknown"),
        (h.listening(status="unsupported", reason="n/a"), "unsupported"),
        (h.listening(status="disabled", reason="off"), "unsupported"),
    ],
)
def test_unreadable_section_is_never_a_pass(section: Any, expected: str) -> None:
    rule = WildcardListenerRule("LNX-LISTEN-001", "linux")
    posture = parse(listening=section) if section is not None else parse()
    out = rule.evaluate(posture, CTX)
    assert out.status == expected and out.violations == []


def test_windows_firewall_rule() -> None:
    def fw(**profiles: bool) -> Posture:
        return parse(firewall={"status": "ok", "method": "Get-NetFirewallProfile",
                               "profiles": [{"name": n, "enabled": e} for n, e in profiles.items()]})  # fmt: skip

    rule = WindowsFirewallRule()
    ctx = RuleContext(platform="windows", settings=PostureSettings())
    assert rule.evaluate(fw(Domain=True, Private=True, Public=True), ctx).status == "pass"
    out = rule.evaluate(fw(Domain=True, Private=False, Public=False), ctx)
    assert out.status == "fail"
    assert {v.subject: v.severity for v in out.violations} == {
        "profile/Private": "medium",
        "profile/Public": "high",
    }
    public = next(v for v in out.violations if v.subject == "profile/Public")
    assert public.inference is None  # a read setting is an observation, nothing inferred
    assert "Set-NetFirewallProfile -Profile Public -Enabled True" in (public.remediation[0][1] or "")
    assert rule.meta.classification == "OBSERVED_CONFIGURATION"
    assert rule.evaluate(parse(firewall={"status": "ok", "profiles": []}), ctx).status == "unknown"
    assert (
        rule.evaluate(parse(firewall={"status": "unavailable", "reason": "access denied"}), ctx).status
        == "unknown"
    )
    assert rule.evaluate(parse(), ctx).status == "unknown"


def test_smb1_rule() -> None:
    rule = Smb1Rule()
    ctx = RuleContext(platform="windows", settings=PostureSettings())

    def smb(v1: bool | None) -> Posture:
        return parse(
            smb={
                "status": "ok",
                "method": "Get-SmbServerConfiguration",
                "smb1_enabled": v1,
                "smb2_enabled": True,
            }
        )

    assert rule.evaluate(smb(False), ctx).status == "pass"
    assert rule.evaluate(smb(None), ctx).status == "unknown"  # could not tell ≠ disabled
    out = rule.evaluate(smb(True), ctx)
    assert out.status == "fail" and [v.subject for v in out.violations] == ["smb1-server"]
    assert out.violations[0].severity == "medium" and "did not test" in out.violations[0].severity_rationale
    assert out.clean_evidence == {"smb1_enabled": False, "smb2_enabled": True}


def test_docker_rule_requires_opt_in_and_flags_wildcard_publishes() -> None:
    rule = DockerPublishedPortRule()
    assert (
        rule.evaluate(parse(docker={"status": "disabled", "reason": "opt-in"}), CTX).status == "unsupported"
    )
    ports = [
        {
            "container": "db",
            "image": "postgres:16",
            "proto": "tcp",
            "host_address": "0.0.0.0",
            "host_port": 5432,
            "container_port": 5432,
        },
        {
            "container": "web",
            "image": "nginx",
            "proto": "tcp",
            "host_address": "127.0.0.1",
            "host_port": 8080,
            "container_port": 80,
        },
        {
            "container": "app",
            "image": "app",
            "proto": "tcp",
            "host_address": "::",
            "host_port": 9000,
            "container_port": 9000,
        },
    ]
    out = rule.evaluate(parse(docker={"status": "ok", "method": "docker api", "published_ports": ports}), CTX)
    assert out.status == "fail"
    assert {v.subject: v.severity for v in out.violations} == {"db:tcp/5432": "medium", "app:tcp/9000": "low"}
    allow = RuleContext(
        platform="linux", settings=PostureSettings(docker_port_allowlist=["tcp/5432", "tcp/9000"])
    )
    assert rule.evaluate(parse(docker={"status": "ok", "published_ports": ports}), allow).status == "pass"


def test_windows_and_linux_listener_allowlists_are_independent() -> None:
    posture = parse(listening=h.listening(("tcp", "0.0.0.0", 445), ("tcp", "0.0.0.0", 22)))
    settings = PostureSettings()
    linux = WildcardListenerRule("LNX-LISTEN-001", "linux").evaluate(
        posture, RuleContext(platform="linux", settings=settings)
    )
    windows = WildcardListenerRule("WIN-LISTEN-001", "windows").evaluate(
        posture, RuleContext(platform="windows", settings=settings)
    )
    assert [v.subject for v in linux.violations] == [
        "tcp/*:445"
    ]  # SMB on a Linux host is not a stock default
    assert [v.subject for v in windows.violations] == ["tcp/*:22"]  # ...and sshd on Windows is not either
    strict = PostureSettings(windows_listener_allowlist=[])
    all_flagged = WildcardListenerRule("WIN-LISTEN-001", "windows").evaluate(
        posture, RuleContext(platform="windows", settings=strict)
    )
    assert {v.subject for v in all_flagged.violations} == {"tcp/*:445", "tcp/*:22"}


def test_router_upnp_is_always_unsupported_never_a_pass() -> None:
    assert RouterUpnpRule().evaluate(parse(), CTX).status == "unsupported"


def test_diff_evidence_is_flat_and_deterministic() -> None:
    before = {"enabled": False, "profile": "Public", "same": 1}
    after = {"enabled": True, "same": 1, "extra": "x"}
    assert engine.diff_evidence(before, after) == [
        {"field": "enabled", "before": False, "after": True},
        {"field": "extra", "before": None, "after": "x"},
        {"field": "profile", "before": "Public", "after": None},
    ]
    assert engine.diff_evidence(before, before) == []


# ---------------------------------------------------------------- DB constraint


async def test_database_rejects_proven_exploitable_classification() -> None:
    _sid, finding = await open_finding()
    with pytest.raises(IntegrityError):
        async with session_scope() as session:
            await session.execute(
                text("UPDATE findings SET classification = 'PROVEN_EXPLOITABLE' WHERE id = :id"),
                {"id": finding.id},
            )


# ---------------------------------------------------------------- findings + evidence provenance


async def test_finding_carries_evidence_provenance() -> None:
    sid = await h.make_source()
    collected = h.ago(30)
    await h.process(sid, h.payload(posture=h.posture(collected, listening=h.listening(REDIS, ("tcp", "0.0.0.0", 22)),
                                                     docker={"status": "disabled", "reason": "opt-in"})))  # fmt: skip
    (finding,) = await findings()
    assert (finding.rule_id, finding.rule_version, finding.subject) == ("LNX-LISTEN-001", 1, "tcp/*:6379")
    assert finding.asset_id == await h.source_asset_id(sid) and finding.source_id == sid
    assert finding.classification == "POTENTIAL_EXPOSURE" and finding.status == "open"
    assert finding.verification_status == "not_requested"
    assert finding.severity == "medium" and finding.severity_rationale and finding.confidence == "high"
    assert (
        finding.first_observed_at == collected == finding.last_observed_at
    )  # collection time, not processing time
    assert finding.inference and finding.inference.startswith("Inferred, not tested")
    assert finding.impact

    (ev,) = await evidence(finding.id)
    async with session_scope() as session:
        snapshot = (await session.execute(select(PostureSnapshot))).scalar_one()
        assessment = (await session.execute(select(Assessment))).scalar_one()
        results = {r.rule_id: r for r in (await session.execute(select(CheckResult))).scalars()}
        steps = list(
            (await session.execute(select(RemediationStep).order_by(RemediationStep.position))).scalars()
        )
    assert (ev.kind, ev.observed) == ("initial", True)
    assert ev.source_id == sid and ev.snapshot_id == snapshot.id and ev.assessment_id == assessment.id
    assert ev.collected_at == collected
    assert ev.collection_method == "fixture socket table"
    assert ev.data["sockets"] == [{"proto": "tcp", "address": "0.0.0.0", "port": 6379, "process": None}]
    assert assessment.engine_version == ENGINE_VERSION and assessment.trigger == "scheduled"
    assert assessment.summary["fail"] == 1 and assessment.summary["unsupported"] == 1
    assert results["LNX-LISTEN-001"].status == "fail" and results["LNX-LISTEN-001"].failing_subjects == 1
    assert results["DOCKER-PORT-001"].status == "unsupported"  # recorded as not-checked, not as clean
    assert [s.position for s in steps] == [1, 2, 3, 4] and "ss -ltnup" in (steps[0].command or "")


async def test_opening_a_finding_raises_a_posture_alert_and_event(default_rules: None) -> None:
    _sid, finding = await open_finding()
    async with session_scope() as session:
        alert = (await session.execute(select(Alert))).scalar_one()
        kinds = [e.kind for e in (await session.execute(select(Event).order_by(Event.id))).scalars()]
    assert alert.kind == "posture_changed" and alert.finding_id == finding.id and alert.state == "firing"
    assert "not an exploitability claim" in alert.summary
    assert "finding.opened" in kinds and "alert.fired" in kinds


async def test_rules_only_run_for_the_source_platform() -> None:
    sid = await h.make_source(
        agent_uid="win-test-agent-0001", name="win-pc", platform="windows", kind="windows_agent"
    )
    body = h.payload(agent_uid="win-test-agent-0001", hostname="win-pc", platform="windows", posture=h.posture(
        h.ago(5),
        firewall={"status": "ok", "method": "Get-NetFirewallProfile", "profiles": [
            {"name": "Domain", "enabled": True}, {"name": "Private", "enabled": True}, {"name": "Public", "enabled": False}]},
        smb={"status": "ok", "method": "Get-SmbServerConfiguration", "smb1_enabled": True, "smb2_enabled": True},
        # 445/135 are on the default Windows listener allowlist (stock services); 3389 is not
        listening=h.listening(("tcp", "0.0.0.0", 445), ("tcp", "0.0.0.0", 135), ("tcp", "0.0.0.0", 3389)),
    ))  # fmt: skip
    await h.process(sid, body)
    got = {(f.rule_id, f.subject, f.severity, f.classification) for f in await findings()}
    assert got == {
        ("WIN-FW-001", "profile/Public", "high", "OBSERVED_CONFIGURATION"),
        ("WIN-SMB-001", "smb1-server", "medium", "OBSERVED_CONFIGURATION"),
        ("WIN-LISTEN-001", "tcp/*:3389", "medium", "POTENTIAL_EXPOSURE"),
    }
    async with session_scope() as session:
        rule_ids = set((await session.execute(select(CheckResult.rule_id))).scalars())
    assert rule_ids == {"WIN-FW-001", "WIN-SMB-001", "WIN-LISTEN-001"}  # no Linux/Docker/router rules


# ---------------------------------------------------------------- verification


async def test_manual_verify_resolves_only_on_evidence_collected_after_the_request(
    default_rules: None,
) -> None:
    sid, finding = await open_finding()
    run = await request_verify(finding.id)
    assert (
        run.status == "pending"
        and run.trigger == "manual"
        and run.algorithm_version == VERIFY_ALGORITHM_VERSION
    )
    async with session_scope() as session:
        source = await session.get(DataSource, sid)
        assert (
            source is not None and source.posture_requested_at == run.requested_at
        )  # agent is asked for fresh evidence
    assert (await findings())[0].verification_status == "pending"

    # A clean snapshot collected BEFORE the request (already in flight) must not decide anything.
    in_flight = h.posture(run.requested_at - timedelta(seconds=1), listening=h.listening())
    await h.process(sid, h.payload(posture=in_flight))
    (f,) = await findings()
    assert f.status == "open" and f.verification_status == "pending"
    assert (await runs(finding.id))[0].status == "pending"

    # ...neither does one stamped exactly at the request time.
    await h.process(sid, h.payload(posture=h.posture(run.requested_at, listening=h.listening())))
    (f,) = await findings()
    assert f.status == "open" and (await runs(finding.id))[0].status == "pending"

    # Fresh evidence collected after the request: now the same check may conclude.
    fresh = run.requested_at + timedelta(seconds=2)
    await h.process(sid, h.payload(posture=h.posture(fresh, listening=h.listening())))
    (f,) = await findings()
    assert f.status == "resolved" and f.classification == "VERIFIED_RESOLUTION"
    assert f.verification_status == "resolved" and f.resolved_at == fresh
    (done,) = await runs(finding.id)
    assert (done.status, done.outcome, done.trigger) == ("completed", "resolved", "manual")
    ev = await evidence(finding.id)
    before, after = ev[0], ev[-1]
    assert (after.kind, after.observed, after.collected_at) == ("recheck", False, fresh)
    assert (done.before_evidence_id, done.after_evidence_id) == (before.id, after.id)
    async with session_scope() as session:
        diff = (await session.execute(select(VerificationDiff))).scalar_one()
        source = await session.get(DataSource, sid)
        assert source is not None and source.posture_requested_at is None
        alert = (await session.execute(select(Alert))).scalar_one()
    assert diff.before["sockets"] and diff.after["sockets"] == []
    assert {c["field"] for c in diff.changes} >= {"sockets"}
    assert alert.state == "resolved" and alert.resolved_reason == "finding verified resolved"


async def test_manual_verify_still_observed_keeps_the_finding_open() -> None:
    sid, finding = await open_finding()
    run = await request_verify(finding.id)
    fresh = run.requested_at + timedelta(seconds=3)
    await h.process(sid, h.payload(posture=h.posture(fresh, listening=h.listening(REDIS))))
    (f,) = await findings()
    assert f.status == "open" and f.classification == "POTENTIAL_EXPOSURE"
    assert f.verification_status == "still_observed" and f.resolved_at is None and f.last_observed_at == fresh
    (done,) = await runs(finding.id)
    assert (done.status, done.outcome) == ("completed", "still_observed")
    ev = await evidence(finding.id)
    assert (ev[-1].kind, ev[-1].observed, ev[-1].collected_at) == ("recheck", True, fresh)
    assert done.after_evidence_id == ev[-1].id and done.before_evidence_id == ev[0].id


async def test_manual_verify_with_unreadable_section_is_unknown_and_changes_nothing() -> None:
    sid, finding = await open_finding()
    run = await request_verify(finding.id)
    unreadable = h.listening(status="unavailable", reason="permission denied reading /proc/net/tcp")
    await h.process(
        sid, h.payload(posture=h.posture(run.requested_at + timedelta(seconds=2), listening=unreadable))
    )
    (f,) = await findings()
    assert f.status == "open" and f.classification == "POTENTIAL_EXPOSURE" and f.resolved_at is None
    assert f.verification_status == "unknown"
    (done,) = await runs(finding.id)
    assert (done.status, done.outcome) == ("completed", "unknown")
    assert "inconclusive" in (done.outcome_reason or "") and done.after_evidence_id is None


async def test_unreadable_section_without_a_request_never_resolves() -> None:
    sid, finding = await open_finding()
    await h.process(
        sid, h.payload(posture=h.posture(h.ago(1), listening=h.listening(status="unavailable", reason="x")))
    )
    (f,) = await findings()
    assert f.status == "open" and await runs(finding.id) == []


async def test_verification_deadline_expiry_is_unknown_never_resolved() -> None:
    _sid, finding = await open_finding()
    run = await request_verify(finding.id, timeout_s=15)
    async with session_scope() as session:
        assert (
            await engine.expire_verification_runs(session, now=run.requested_at + timedelta(seconds=10)) == 0
        )
        assert await engine.expire_verification_runs(session, now=run.deadline_at + timedelta(seconds=1)) == 1
    (f,) = await findings()
    assert f.status == "open" and f.resolved_at is None and f.verification_status == "unknown"
    (done,) = await runs(finding.id)
    assert (done.status, done.outcome) == ("completed", "unknown")
    assert "No fresh evidence" in (done.outcome_reason or "")
    async with session_scope() as session:
        assert (
            await engine.expire_verification_runs(session, now=run.deadline_at + timedelta(hours=1)) == 0
        )  # idempotent


async def test_continuous_resolution_records_a_verification_run_and_diff() -> None:
    sid, finding = await open_finding(sockets=(REDIS, WEB))
    assert len(await findings()) == 2
    clean_at = h.ago(5)
    await h.process(
        sid, h.payload(posture=h.posture(clean_at, listening=h.listening(WEB)))
    )  # redis fixed, web still open
    by_subject = {f.subject: f for f in await findings()}
    fixed, still = by_subject["tcp/*:6379"], by_subject["tcp/*:8080"]
    assert (
        fixed.status == "resolved"
        and fixed.classification == "VERIFIED_RESOLUTION"
        and fixed.resolved_at == clean_at
    )
    assert still.status == "open" and still.last_observed_at == clean_at
    (run,) = await runs(fixed.id)
    assert (run.trigger, run.status, run.outcome) == ("continuous", "completed", "resolved")
    assert run.before_evidence_id is not None and run.after_evidence_id is not None
    async with session_scope() as session:
        diff = (
            await session.execute(
                select(VerificationDiff).where(VerificationDiff.verification_run_id == run.id)
            )
        ).scalar_one()
    assert diff.changes and diff.after["sockets"] == []
    assert await runs(still.id) == []


async def test_resolved_finding_reopens_when_observed_again() -> None:
    sid, finding = await open_finding()
    await h.process(sid, h.payload(posture=h.posture(h.ago(60), listening=h.listening())))
    assert (await findings())[0].status == "resolved"
    again = h.ago(5)
    await h.process(sid, h.payload(posture=h.posture(again, listening=h.listening(REDIS))))
    (f,) = await findings()  # same finding row, not a duplicate
    assert f.id == finding.id and f.status == "open" and f.classification == "POTENTIAL_EXPOSURE"
    assert f.resolved_at is None and f.verification_status == "not_requested" and f.last_observed_at == again
    ev = await evidence(f.id)
    assert [(e.kind, e.observed) for e in ev] == [("initial", True), ("recheck", False), ("changed", True)]
    async with session_scope() as session:
        kinds = [
            e.kind
            for e in (
                await session.execute(select(Event).where(Event.finding_id == f.id).order_by(Event.id))
            ).scalars()
        ]
    assert kinds == ["finding.opened", "finding.resolved", "finding.reopened"]


async def test_manual_close_is_closed_unverified_not_resolved(admin_client: httpx.AsyncClient) -> None:
    sid, finding = await open_finding()
    resp = await admin_client.post(
        f"/api/v1/findings/{finding.id}/status", json={"action": "close", "note": "I think I fixed it"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "closed_unverified" and body["resolved_at"] is None
    assert body["classification"] == "POTENTIAL_EXPOSURE" and body["verification_status"] == "not_requested"
    assert (await admin_client.get("/api/v1/findings", params={"status": "resolved"})).json() == []
    detail = (await admin_client.get(f"/api/v1/findings/{finding.id}")).json()
    assert "closed without verification" in detail["history"][0]["message"]
    assert "never proof of exploitability" in detail["disclaimer"]

    # still observed on the next changed snapshot -> it reopens by itself
    await h.process(
        sid, h.payload(posture=h.posture(h.ago(1), listening=h.listening(REDIS, ("tcp", "127.0.0.1", 9))))
    )
    assert (await findings())[0].status == "open"


async def test_closed_unverified_becomes_verified_resolved_once_fresh_evidence_is_clean(
    admin_client: httpx.AsyncClient,
) -> None:
    sid, finding = await open_finding()
    await admin_client.post(f"/api/v1/findings/{finding.id}/status", json={"action": "close"})
    await h.process(sid, h.payload(posture=h.posture(h.ago(1), listening=h.listening())))
    (f,) = await findings()
    assert f.status == "resolved" and f.classification == "VERIFIED_RESOLUTION"


async def test_finding_status_transitions(admin_client: httpx.AsyncClient) -> None:
    _sid, finding = await open_finding()
    url = f"/api/v1/findings/{finding.id}/status"
    assert (await admin_client.post(url, json={"action": "reopen"})).status_code == 409  # already open
    assert (await admin_client.post(url, json={"action": "snooze"})).status_code == 422  # needs 'until'
    past = (utcnow() - timedelta(hours=1)).isoformat()
    assert (await admin_client.post(url, json={"action": "snooze", "until": past})).status_code == 422
    until = (utcnow() + timedelta(days=1)).isoformat()
    snoozed = (await admin_client.post(url, json={"action": "snooze", "until": until})).json()
    assert snoozed["status"] == "snoozed" and snoozed["snoozed_until"] is not None
    acked = (await admin_client.post(url, json={"action": "acknowledge"})).json()
    assert acked["status"] == "acknowledged" and acked["snoozed_until"] is None
    assert (await admin_client.post(url, json={"action": "acknowledge"})).status_code == 409
    assert (
        await admin_client.post(url, json={"action": "resolve"})
    ).status_code == 422  # no API path to "resolved"
    assert (await admin_client.post(url, json={"action": "reopen"})).json()["status"] == "open"
    active = (await admin_client.get("/api/v1/findings")).json()
    assert [f["id"] for f in active] == [finding.id]
    history = (await admin_client.get(f"/api/v1/findings/{finding.id}")).json()["history"]
    assert [e["kind"] for e in history][:3] == ["finding.reopen", "finding.acknowledge", "finding.snooze"]


async def test_verified_resolved_finding_cannot_be_reopened_by_hand(admin_client: httpx.AsyncClient) -> None:
    sid, finding = await open_finding()
    await h.process(sid, h.payload(posture=h.posture(h.ago(1), listening=h.listening())))
    resp = await admin_client.post(f"/api/v1/findings/{finding.id}/status", json={"action": "reopen"})
    assert resp.status_code == 409
    assert (
        await admin_client.post(f"/api/v1/findings/{finding.id}/status", json={"action": "close"})
    ).status_code == 409


async def test_verify_endpoint(admin_client: httpx.AsyncClient, viewer_client: httpx.AsyncClient) -> None:
    sid, finding = await open_finding()
    assert (await viewer_client.post(f"/api/v1/findings/{finding.id}/verify")).status_code == 403
    assert (await admin_client.post("/api/v1/findings/999999/verify")).status_code == 404
    first = await admin_client.post(f"/api/v1/findings/{finding.id}/verify")
    assert (
        first.status_code == 202 and first.json()["status"] == "pending" and first.json()["outcome"] is None
    )
    second = await admin_client.post(f"/api/v1/findings/{finding.id}/verify")
    assert second.json()["id"] == first.json()["id"]  # no pile of duplicate requests
    assert len(await runs(finding.id)) == 1
    token, _ = await h.issue_token()
    async with session_scope() as session:  # bind the token to the source so /agent/config resolves it
        (await session.get(type(await h.get_token(1)), 1)).source_id = sid  # type: ignore[union-attr]
    cfg = (
        await admin_client.get("/api/v1/agent/config", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert cfg["posture_requested_at"] is not None  # the agent learns it must collect now

    requested = (await runs(finding.id))[0].requested_at
    await h.process(
        sid, h.payload(posture=h.posture(requested + timedelta(seconds=1), listening=h.listening()))
    )
    detail = (await admin_client.get(f"/api/v1/findings/{finding.id}")).json()
    assert (
        detail["finding"]["status"] == "resolved"
        and detail["finding"]["classification"] == "VERIFIED_RESOLUTION"
    )
    run = detail["verifications"][0]
    assert run["outcome"] == "resolved" and run["algorithm_version"] == VERIFY_ALGORITHM_VERSION
    assert run["diff"]["before"]["sockets"] and run["diff"]["after"]["sockets"] == []
    assert [e["kind"] for e in detail["evidence"]] == ["initial", "recheck"]
    assert detail["remediation"][0]["position"] == 1


async def test_listener_allowlist_setting_resolves_an_accepted_finding() -> None:
    sid, finding = await open_finding(sockets=(WEB,))
    async with session_scope() as session:
        await save_runtime_settings(
            session, RuntimeSettings(posture=PostureSettings(listener_allowlist=["tcp/22", "tcp/8080"]))
        )
    await h.process(
        sid, h.payload(posture=h.posture(h.ago(1), listening=h.listening(WEB, ("tcp", "127.0.0.1", 1))))
    )
    assert (await findings())[0].status == "resolved"


# ---------------------------------------------------------------- staleness (observability finding)


async def test_agent_staleness_finding_opens_and_resolves_on_fresh_telemetry() -> None:
    sid = await h.make_source()
    now = utcnow()
    await h.process(sid, h.payload(), received_at=now - timedelta(minutes=10))
    async with session_scope() as session:
        await engine.evaluate_agent_staleness(session, disconnected_after_s=90, now=now)
        await engine.evaluate_agent_staleness(
            session, disconnected_after_s=90, now=now + timedelta(seconds=10)
        )  # no duplicate
    (f,) = await findings()
    assert (f.rule_id, f.category, f.severity, f.status) == ("OBS-STALE-001", "observability", "low", "open")
    assert "says nothing about why" in (f.inference or "")
    assert len(await evidence(f.id)) == 1

    await h.process(sid, h.payload(), received_at=now + timedelta(seconds=20))
    async with session_scope() as session:
        await engine.evaluate_agent_staleness(
            session, disconnected_after_s=90, now=now + timedelta(seconds=25)
        )
    (f,) = await findings()
    assert f.status == "resolved" and f.classification == "VERIFIED_RESOLUTION"
    (run,) = await runs(f.id)
    assert (run.trigger, run.outcome) == ("continuous", "resolved")


async def test_checks_catalog_reports_unsupported_router_rule(admin_client: httpx.AsyncClient) -> None:
    await open_finding()
    catalog = {c["rule_id"]: c for c in (await admin_client.get("/api/v1/checks")).json()}
    assert catalog["RTR-UPNP-001"]["coverage"] == "unsupported" and catalog["RTR-UPNP-001"]["results"] == []
    assert catalog["LNX-LISTEN-001"]["coverage"] == "evaluated"
    assert catalog["LNX-LISTEN-001"]["results"][0]["status"] == "fail"
    assert catalog["WIN-FW-001"]["coverage"] == "not_evaluated"
    assert "OBS-STALE-001" in catalog
