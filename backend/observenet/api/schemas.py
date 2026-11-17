"""REST response/request models (source of truth for the generated TypeScript types)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from observenet.services.views import Kpi, NetCapability


class Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Meta(BaseModel):
    name: str = "ObserveNet"
    version: str
    demo_mode: bool
    setup_required: bool
    setup_hint: str | None = None


class LoginRequest(BaseModel):
    username: Annotated[str, Field(min_length=1, max_length=64)]
    password: Annotated[str, Field(min_length=1, max_length=256)]


class UserOut(Orm):
    id: int
    username: str
    role: str
    disabled: bool
    created_at: datetime
    last_login_at: datetime | None


class MeOut(BaseModel):
    user: UserOut
    csrf_token: str
    demo_mode: bool


class UserCreate(BaseModel):
    username: Annotated[str, Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")]
    password: Annotated[str, Field(min_length=12, max_length=256)]
    role: Literal["admin", "viewer"] = "viewer"


class UserPatch(BaseModel):
    role: Literal["admin", "viewer"] | None = None
    disabled: bool | None = None
    password: Annotated[str, Field(min_length=12, max_length=256)] | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: Annotated[str, Field(min_length=12, max_length=256)]


class CapabilityOut(Orm):
    name: str
    status: str
    detail: str | None
    updated_at: datetime


class SourceOut(BaseModel):
    id: int
    kind: str
    name: str
    platform: str | None
    version: str | None
    asset_id: int | None
    observation_point: str
    enabled: bool
    is_demo: bool
    created_at: datetime
    last_seen_at: datetime | None
    last_error: str | None
    freshness: str
    age_s: float | None
    capabilities: list[CapabilityOut]


class SourcePatch(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    enabled: bool | None = None


class SourceTestOut(BaseModel):
    source_id: int
    ok: bool
    freshness: str
    checks: list[dict[str, Any]]
    note: str


class TokenOut(Orm):
    id: int
    name: str
    token_prefix: str
    scopes: list[str]
    source_id: int | None
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    is_bootstrap: bool


class TokenCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    ttl_days: Annotated[int, Field(ge=1, le=365)] | None = None


class TokenCreated(BaseModel):
    token: str  # shown exactly once
    info: TokenOut


class SeriesOut(Orm):
    id: int
    source_id: int
    asset_id: int | None
    metric: str
    label: str
    unit: str
    observation_point: str
    quality: str
    interval_s: float
    coverage: str | None
    first_ts: datetime | None
    last_ts: datetime | None


class CurrentValue(BaseModel):
    key: str
    source_id: int
    metric: str
    label: str
    value: float
    unit: str
    quality: str
    ts: datetime
    age_s: float
    freshness: str


class HistoryPoint(BaseModel):
    t: datetime
    avg: float | None
    min: float | None
    max: float | None
    n: int


class HistorySeries(BaseModel):
    series: SeriesOut
    points: list[HistoryPoint]


class HistoryOut(BaseModel):
    metric: str
    start: datetime
    end: datetime
    bucket_s: int
    resolution: Literal["raw", "1m", "1h"]
    series: list[HistorySeries]
    note: str | None = None


class Presence(BaseModel):
    state: str
    label: str
    basis: str


class DeviceOut(BaseModel):
    id: int
    name: str
    display_name: str | None
    hostname: str | None
    asset_type: str
    group_name: str | None
    owner: str | None
    tags: list[str]
    notes: str | None
    favorite: bool
    vendor: str | None
    vendor_method: str | None
    confidence: str
    confidence_reason: str | None
    mac_randomized: bool
    managed: bool
    is_demo: bool
    first_seen: datetime | None
    last_seen: datetime | None
    last_seen_source: str | None
    presence: Presence
    primary_ip: str | None
    primary_mac: str | None
    open_findings: int
    merged_into_id: int | None


class DevicePage(BaseModel):
    items: list[DeviceOut]
    total: int
    limit: int
    offset: int
    coverage_note: str


class DevicePatch(BaseModel):
    display_name: Annotated[str, Field(max_length=120)] | None = None
    asset_type: str | None = None
    group_name: Annotated[str, Field(max_length=80)] | None = None
    owner: Annotated[str, Field(max_length=120)] | None = None
    tags: Annotated[list[Annotated[str, Field(min_length=1, max_length=40)]], Field(max_length=20)] | None = None
    notes: Annotated[str, Field(max_length=4000)] | None = None
    favorite: bool | None = None


class MergeRequest(BaseModel):
    into_id: int
    reason: Annotated[str, Field(max_length=500)] | None = None


class DeviceDetail(BaseModel):
    device: DeviceOut
    identities: list[dict[str, Any]]
    addresses: list[dict[str, Any]]
    interfaces: list[dict[str, Any]]
    services: list[dict[str, Any]]
    series: list[SeriesOut]
    findings: list[dict[str, Any]]
    alerts: list[dict[str, Any]]
    events: list[dict[str, Any]]
    merges: list[dict[str, Any]]


class ImportRequest(BaseModel):
    content: Annotated[str, Field(min_length=1, max_length=500_000)]


class FindingOut(BaseModel):
    id: int
    asset_id: int
    asset_name: str
    source_id: int | None
    rule_id: str
    rule_version: int
    subject: str
    category: str
    title: str
    observation: str
    inference: str | None
    impact: str
    classification: str
    severity: str
    severity_rationale: str
    confidence: str
    status: str
    verification_status: str
    first_observed_at: datetime
    last_observed_at: datetime
    resolved_at: datetime | None
    snoozed_until: datetime | None
    is_demo: bool


class EvidenceOut(Orm):
    id: int
    kind: str
    observed: bool
    collected_at: datetime
    collection_method: str
    summary: str
    data: dict[str, Any]
    source_id: int | None
    assessment_id: int | None


class RemediationOut(Orm):
    position: int
    instruction: str
    command: str | None


class VerificationOut(BaseModel):
    id: int
    trigger: str
    status: str
    outcome: str | None
    outcome_reason: str | None
    algorithm_version: str
    requested_at: datetime
    deadline_at: datetime
    completed_at: datetime | None
    before_evidence_id: int | None
    after_evidence_id: int | None
    diff: dict[str, Any] | None


class FindingDetail(BaseModel):
    finding: FindingOut
    evidence: list[EvidenceOut]
    remediation: list[RemediationOut]
    verifications: list[VerificationOut]
    history: list[dict[str, Any]]
    disclaimer: str


class FindingAction(BaseModel):
    action: Literal["acknowledge", "snooze", "close", "reopen"]
    until: datetime | None = None
    note: Annotated[str, Field(max_length=1000)] | None = None


class AlertOut(Orm):
    id: int
    kind: str
    state: str
    severity: str
    title: str
    summary: str
    details: dict[str, Any]
    asset_id: int | None
    source_id: int | None
    finding_id: int | None
    source_ts: datetime | None
    started_at: datetime
    resolved_at: datetime | None
    resolved_reason: str | None
    acknowledged_at: datetime | None
    snoozed_until: datetime | None
    is_demo: bool


class AlertRuleOut(Orm):
    id: int
    kind: str
    name: str
    description: str | None
    enabled: bool
    severity: str
    params: dict[str, Any]


class AlertRulePatch(BaseModel):
    enabled: bool | None = None
    severity: Literal["info", "warning", "critical"] | None = None
    params: dict[str, Annotated[float, Field(ge=0, le=1e12)]] | None = None


class SnoozeRequest(BaseModel):
    until: datetime


class MaintenanceIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    starts_at: datetime
    ends_at: datetime
    alert_kinds: list[str] = []


class MaintenanceOut(Orm):
    id: int
    name: str
    starts_at: datetime
    ends_at: datetime
    alert_kinds: list[str]


class EventOut(Orm):
    id: int
    ts: datetime
    kind: str
    severity: str
    message: str
    asset_id: int | None
    source_id: int | None
    alert_id: int | None
    finding_id: int | None
    data: dict[str, Any]


class ProbeTargetIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    kind: Literal["gateway_icmp", "icmp", "tcp", "dns"]
    scope: Literal["gateway", "internal", "external"]
    host: Annotated[str, Field(min_length=1, max_length=255)]
    port: Annotated[int, Field(ge=1, le=65535)] | None = None
    interval_s: Annotated[float, Field(ge=1, le=3600)] = 30
    timeout_ms: Annotated[int, Field(ge=100, le=10000)] = 1000
    enabled: bool = True


class ProbeTargetOut(Orm):
    id: int
    name: str
    kind: str
    scope: str
    host: str
    port: int | None
    interval_s: float
    timeout_ms: int
    enabled: bool
    is_demo: bool


class TargetSummary(BaseModel):
    target: ProbeTargetOut
    window_s: int
    sample_size: int
    loss_pct: float | None
    latency_avg_ms: float | None
    latency_min_ms: float | None
    latency_max_ms: float | None
    latency_p95_ms: float | None
    jitter_ms: float | None
    last_ts: datetime | None
    last_method: str | None
    last_error: str | None
    resolved_host: str | None
    state: str
    note: str | None


class ConnectivityOut(BaseModel):
    generated_at: datetime
    internet: dict[str, Any]
    targets: list[TargetSummary]
    outages: list[dict[str, Any]]
    wifi: dict[str, str]


class OverviewOut(BaseModel):
    generated_at: datetime
    demo_mode: bool
    kpis: list[Kpi]
    primary: dict[str, Any]
    internet: dict[str, Any]
    active_alerts: list[dict[str, Any]]
    recent_device_events: list[dict[str, Any]]
    per_device_traffic: dict[str, Any]
    posture: dict[str, Any]


class CapabilitiesOut(BaseModel):
    generated_at: datetime
    demo_mode: bool
    network: list[NetCapability]
    router_provider: dict[str, Any]


class StatusOut(BaseModel):
    version: str
    demo_mode: bool
    components: list[dict[str, Any]]
    queues: dict[str, int | None]
    ws_clients: int


class AuditOut(Orm):
    id: int
    ts: datetime
    actor_label: str
    action: str
    target_type: str | None
    target_id: str | None
    ip: str | None
    details: dict[str, Any]
