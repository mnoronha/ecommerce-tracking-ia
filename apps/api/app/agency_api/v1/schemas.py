"""
Agency API v1.1 — Pydantic contracts.

All objects that cross the Agency API boundary carry schema_version = "1.1".
Enums are imported from .enums — they are the single source of truth.

Naming conventions:
  *Create  — request body for POST endpoints (Hermes writes)
  *Out     — response body for GET / POST returns

Sections (in order):
  1. Common / shared types
  2. Clients and Truth
  3. Data health and pipeline health
  4. Metrics (metric contract)
  5. Core report contracts
  6. Change log
  7. Alerts
  8. Diagnoses (intel)
  9. Recommendations (intel)
  10. Action events
  11. Alert feedback
  12. Report narratives (intel)
  13. Alert rule suggestions
  14. Learning candidates (intel)
  15. Jobs
  16. System health
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    ActionEventType,
    AlertRuleSuggestionStatus,
    AlertStatus,
    BusinessModel,
    CertificationStatus,
    ChangeConfidence,
    ChangeLogSource,
    ChangeType,
    DiagnosisStatus,
    EvidenceLevel,
    HypothesisType,
    JobStatus,
    LearningCandidateStatus,
    LearningScope,
    Level,
    MatchStatus,
    MetricDomain,
    NarrativeStatus,
    OutcomeStatus,
    RecommendationStatus,
    ReportType,
    ServiceStatus,
    SourceState,
    TargetStatus,
    ValueStatus,
    VisibilityScope,
)

SCHEMA_VERSION: Literal["1.1"] = "1.1"


# ── 0. Base ───────────────────────────────────────────────────────────────────

class _Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


# ── 1. Common / shared types ──────────────────────────────────────────────────

class Period(_Base):
    start: date
    end: date


class MetricRef(_Base):
    """
    Points to one or more snapshots (for diagnoses/recommendations) or
    to a contract path (for narratives).

    At least one of snapshot_ids or contract_path must be provided.
    """
    snapshot_ids: list[str] = Field(default_factory=list)
    baseline_snapshot_ids: list[str] = Field(default_factory=list)
    contract_path: Optional[str] = None
    # Rendering hint: "delta_pct" | "currency" | "pct" | "number" | "x_ratio"
    presentation: str


class TextWithRefs(_Base):
    """A human-readable statement that may contain {{mN}} placeholders."""
    statement: str
    metric_refs: dict[str, MetricRef] = Field(default_factory=dict)


class TruthVersions(_Base):
    client: int
    target: int
    conversion_map: int


# ── 2. Clients and Truth ──────────────────────────────────────────────────────

class ClientOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    client_id: str
    name: str
    business_model: BusinessModel
    timezone: str
    currency: str
    country: Optional[str] = None
    status: Literal["active", "inactive", "onboarding"] = "active"


class TruthOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    client_id: str
    client_version: int
    client_truth: dict[str, Any]
    target_version: int
    target_truth: dict[str, Any]
    valid_from: datetime


# ── 3. Data health and pipeline health ───────────────────────────────────────

class DataHealthEntry(_Base):
    domain: str  # "business"|"google_ads"|"meta_ads"|"ga4"
    source_key: Optional[str] = None
    source_system: Optional[str] = None
    semantic_domain: Optional[str] = None
    source_state: SourceState
    # Derived statuses (deterministic — no arbitrary thresholds beyond DQG defaults)
    collection_status: str = "UNKNOWN"      # OK | NO_DATA | STALE | ERROR | NOT_APPLICABLE
    freshness_status: str = "UNKNOWN"       # FRESH | STALE | UNKNOWN
    reconciliation_status: str = "UNKNOWN"  # OK | DEGRADED | UNKNOWN
    last_attempt_at: Optional[datetime] = None
    last_data_at: Optional[datetime] = None
    last_validated_at: Optional[datetime] = None
    last_reconciled_at: Optional[datetime] = None
    reconciliation_state: Optional[str] = None
    reason: Optional[str] = None  # last_error, kept for backward compat
    checked_at: datetime


class DataHealthOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    client_id: str
    health: list[DataHealthEntry]
    checked_at: datetime


class DataSourceDetail(_Base):
    """Full detail for one row of core_data_sources (P1 — Wave 1)."""
    source_key: str
    source_system: str
    semantic_domain: Optional[str] = None
    source_state: SourceState
    last_attempt_at: Optional[datetime] = None
    last_data_at: Optional[datetime] = None
    last_validated_at: Optional[datetime] = None
    last_reconciled_at: Optional[datetime] = None
    reconciliation_state: Optional[str] = None
    last_error: Optional[str] = None
    # delta/tolerance are computed transiently during reconciliation and are NOT
    # stored per-source in core_data_sources — intentionally absent here.


class PipelineHealthOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    client_id: str
    last_collection: dict[str, Optional[datetime]]
    certification: dict[str, Optional[str]]
    sources: list[DataSourceDetail] = Field(default_factory=list)  # P1
    alert_evaluation_at: Optional[datetime] = None
    notion_sync_at: Optional[datetime] = None
    last_report_at: Optional[datetime] = None
    checked_at: datetime


# ── 4. Metrics (metric contract) ──────────────────────────────────────────────

class MetricValue(_Base):
    metric_key: str
    value: Optional[float] = None
    unit: Optional[str] = None
    currency: Optional[str] = None
    value_status: ValueStatus
    reason_code: Optional[str] = None
    certification_status: Optional[CertificationStatus] = None
    snapshot_ids: list[str] = Field(default_factory=list)
    target: Optional[float] = None
    target_ratio: Optional[float] = None  # P2: value/target, deterministic, no policy threshold
    target_status: Optional[TargetStatus] = None
    # CCR-010: semantic domain. Filled by the Core only; Hermes never assigns this.
    # None = domain not yet classified by Core. Source systems are NOT MetricDomain.
    domain: Optional[MetricDomain] = None


class MetricContract(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    client_id: str
    business_model: BusinessModel
    timezone: str
    currency: str
    period: Period
    view: Literal["live", "certified"]
    truth_versions: TruthVersions
    metrics: list[MetricValue]
    health: dict[str, SourceState]


# ── 5. Core report contracts ──────────────────────────────────────────────────

class CoreReportContractOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    report_contract_id: str
    client_id: str
    report_type: ReportType
    period: Period
    contract: dict[str, Any]
    truth_versions: TruthVersions
    period_closed_at: Optional[datetime] = None
    certification_coverage: Optional[float] = None
    generated_at: datetime
    supersedes_id: Optional[str] = None
    # CCR-013: explicit provenance to distinguish real contracts from stubs/absence
    provenance_status: Literal["REAL", "STUB", "MISSING"] = "REAL"


# ── 6. Change log ─────────────────────────────────────────────────────────────

class ChangeCreate(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    client_id: str
    occurred_at: datetime
    channel: str
    platform_account_id: str
    entity_type: str
    campaign_id: Optional[str] = None
    adset_or_adgroup_id: Optional[str] = None
    ad_id: Optional[str] = None
    entity_name_at_time: str
    change_type: ChangeType
    before: Optional[Any] = None
    after: Optional[Any] = None
    reason: Optional[str] = None
    source: ChangeLogSource
    reported_by: Optional[str] = None
    confidence: ChangeConfidence = ChangeConfidence.CONFIRMED
    linked_action_id: Optional[str] = None


class ChangeOut(ChangeCreate):
    id: str
    external_change_id: Optional[str] = None
    match_status: Optional[MatchStatus] = None
    matched_change_id: Optional[str] = None
    created_at: datetime


# ── 7. Alerts ─────────────────────────────────────────────────────────────────

class AlertOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    id: str
    client_id: Optional[str] = None       # slug — None for system-wide alerts
    alert_type: str                         # JOB_FAILED | JOB_STUCK | SOURCE_STALE | SOURCE_ERROR | RECONCILIATION_FAILED | PIPELINE_NOT_RUN
    severity: str                           # HIGH | MEDIUM | LOW
    status: AlertStatus = AlertStatus.OPEN
    title: str
    message: str
    dedup_key: str                          # fingerprint used for upsert dedup
    source_key: Optional[str] = None
    job_id: Optional[str] = None
    evidence: dict = Field(default_factory=dict)
    occurrence_count: int = 1
    detected_at: datetime                   # when first seen (created_at from DB)
    resolved_at: Optional[datetime] = None


class AlertContext(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    alert: AlertOut
    metrics: list[MetricValue] = Field(default_factory=list)
    recent_jobs: list[dict] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)


# ── 8. Diagnoses (intel) ──────────────────────────────────────────────────────

class RootCauseHypothesis(_Base):
    """One hypothesis in a Hermes diagnosis. type distinguishes fact from inference."""
    type: HypothesisType
    description: str
    evidence_refs: list[str] = Field(default_factory=list)


class EvidenceRefs(_Base):
    """Structured references to Core canonical context — no raw payload copies."""
    alert_id: Optional[str] = None
    source_key: Optional[str] = None
    job_id: Optional[str] = None
    metric_refs: list[str] = Field(default_factory=list)
    snapshot_refs: list[str] = Field(default_factory=list)
    period: Optional[str] = None
    source_state: Optional[str] = None
    value_status: Optional[str] = None
    certification_status: Optional[str] = None


class DiagnosisCreate(_Base):
    """Request body for POST /alerts/{alert_id}/diagnoses (Hermes write)."""
    summary: str = Field(min_length=1)
    root_cause_hypotheses: list[RootCauseHypothesis] = Field(default_factory=list)
    evidence: EvidenceRefs = Field(default_factory=EvidenceRefs)
    confidence: Optional[Level] = None
    limitations: Optional[str] = None
    status: DiagnosisStatus = DiagnosisStatus.DRAFT


class DiagnosisOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    id: str
    alert_id: str
    client_id: Optional[str] = None
    status: DiagnosisStatus
    summary: str
    root_cause_hypotheses: list[dict] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    confidence: Optional[str] = None
    limitations: Optional[str] = None
    fingerprint: Optional[str] = None
    created_at: datetime
    created_by: str = "hermes"


# ── 9. Recommendations (intel) ────────────────────────────────────────────────

class RecommendationCreate(_Base):
    """Request body for POST /diagnoses/{diagnosis_id}/recommendations (Hermes write)."""
    title: str = Field(min_length=1)
    action: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    priority: Level = Level.MEDIUM
    risk: Optional[str] = None
    expected_impact: Optional[str] = None
    requires_human_approval: bool = True


class RecommendationOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    id: str
    diagnosis_id: str
    alert_id: str
    client_id: Optional[str] = None
    title: str
    action: str
    rationale: str
    priority: str
    risk: Optional[str] = None
    expected_impact: Optional[str] = None
    requires_human_approval: bool
    status: str = "PROPOSED"
    fingerprint: Optional[str] = None
    created_at: datetime
    created_by: str = "hermes"


# ── 9b. Recommendation decision (human-only) ─────────────────────────────────

class RecommendationDecision(_Base):
    """
    Request body for POST /recommendations/{id}/decision.

    Allowed scopes: agency_admin, platform_web.
    hermes_service is EXPLICITLY excluded — Hermes cannot approve autonomously.

    decision semantics:
      ACCEPTED → recommendation.status = APPROVED (human approval for possible future execution)
      REJECTED → recommendation.status = REJECTED
      DEFERRED → recommendation.status = DEFERRED (revisit later; does NOT authorise any external change)

    actor MUST be a stable human identifier (user slug or UUID), never a service name.
    reason is optional but strongly recommended for REJECTED and DEFERRED.
    """
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    decision: Literal["ACCEPTED", "REJECTED", "DEFERRED"]
    actor: str = Field(
        description=(
            "Stable identifier of the human making this decision "
            "(e.g. a user slug or UUID — not a service identity). "
            "Passing a service name here is an audit contract violation."
        )
    )
    reason: Optional[str] = None


# ── 10. Action events ─────────────────────────────────────────────────────────

class ActionEventCreate(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    recommendation_id: str
    event_type: ActionEventType
    actor: str
    occurred_at: Optional[datetime] = None
    note: Optional[str] = None
    change_id: Optional[str] = None


class ActionEventOut(ActionEventCreate):
    id: str
    created_at: datetime


# ── 11. Alert feedback ────────────────────────────────────────────────────────

class AlertFeedbackCreate(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    alert_id: str
    feedback: Literal["USEFUL", "NOISE"]
    actor: str


# ── 12. Report narratives (intel) ─────────────────────────────────────────────

class NarrativeBlock(_Base):
    section: str
    statement: str
    metric_refs: dict[str, MetricRef] = Field(default_factory=dict)


class ReportNarrativeCreate(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    report_contract_id: str
    blocks: list[NarrativeBlock]
    visibility_scope: VisibilityScope = VisibilityScope.AGENCY_ONLY


class ReportNarrativeOut(ReportNarrativeCreate):
    id: str
    status: NarrativeStatus = NarrativeStatus.DRAFT
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    created_at: datetime
    # Contract context — populated when available from the join
    client_id: Optional[str] = None
    report_type: Optional[ReportType] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    truth_versions: Optional[TruthVersions] = None


class NarrativeTransition(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    target_status: NarrativeStatus
    actor: str
    note: Optional[str] = None


# ── 13. Alert rule suggestions ────────────────────────────────────────────────

class AlertRuleSuggestionOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    id: str
    client_id: str
    rule_key: str
    current_version: str
    proposed_params: dict[str, Any]
    evidence: list[dict[str, Any]]
    status: AlertRuleSuggestionStatus
    created_at: datetime


class AlertRuleSuggestionDecision(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    decision: Literal["APPROVED", "REJECTED"]
    actor: str = Field(
        description=(
            "Stable identifier of the human who authorised this decision "
            "(e.g. a user slug or UUID — not a service identity). "
            "When transmitted by `hermes_service`, this MUST be the human actor_id, "
            "never the service name. "
            "Enforcement of non-service identity is a Core invariant (Etapa 4). "
            "A future field `approval_event_id` will reference the canonical "
            "Telegram approval event once that audit chain is available."
        )
    )
    note: Optional[str] = None


# ── 14. Learning candidates (intel) ──────────────────────────────────────────

class LearningCandidateCreate(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    statement: str
    scope: LearningScope
    evidence_level: EvidenceLevel
    evidence_refs: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    client_id: Optional[str] = None
    visibility_scope: VisibilityScope = VisibilityScope.AGENCY_ONLY


class LearningCandidateOut(LearningCandidateCreate):
    id: str
    status: LearningCandidateStatus = LearningCandidateStatus.PENDING
    created_at: datetime


# ── 15. Jobs ──────────────────────────────────────────────────────────────────

class JobOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    id: str
    job_type: str
    run_key: str
    status: JobStatus
    attempt: int = 0
    next_retry_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    replay_of: Optional[str] = None


# ── 16. System health ─────────────────────────────────────────────────────────

class WorkerHealth(_Base):
    name: str
    status: ServiceStatus
    last_heartbeat: Optional[datetime] = None
    note: Optional[str] = None


class SchedulerJobStatus(_Base):
    """One APScheduler job as observed at health-check time."""
    job_id: str
    trigger: str
    next_run_time: Optional[datetime] = None
    last_run_status: Optional[str] = None  # "ok" | "error" from APScheduler event
    last_run_at: Optional[str] = None      # ISO string from _JOB_RUNS


class SystemHealthOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    overall_status: ServiceStatus = ServiceStatus.UP
    api: Literal["UP"] = "UP"
    database: ServiceStatus
    scheduler: ServiceStatus = ServiceStatus.UP
    scheduled_jobs: list[SchedulerJobStatus] = Field(default_factory=list)
    workers: list[WorkerHealth]           # kept for backward compat; empty in APScheduler model
    running_jobs: int = 0
    queued_jobs: int = 0
    failed_jobs_last_24h: int = 0
    # Stuck thresholds (documented here, not hidden):
    #   RUNNING > 120 min = STUCK; QUEUED > 240 min = QUEUED_STALE
    stuck_jobs: list[JobOut] = Field(default_factory=list)
    dead_jobs: list[JobOut]               # kept for backward compat; same as stuck_jobs
    last_successful_pipeline_run: Optional[datetime] = None
    last_pipeline_error: Optional[str] = None
    checked_at: datetime


# ── 17. Meta Ads Diagnostic Insights (READ-ONLY) ─────────────────────────────
# Sourced from meta_ad_attributions (daily sync, no live API call at read time).
# Credential path: Core only — token never loaded in this contract.

class MetaInsightRow(_Base):
    """One aggregated row from meta_ad_attributions."""
    period: str                          # YYYY-MM-DD (daily) | YYYY-MM (monthly)
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    adset_id: Optional[str] = None
    adset_name: Optional[str] = None
    ad_id: Optional[str] = None
    ad_name: Optional[str] = None
    spend: float
    impressions: int
    clicks: int
    conversions: Optional[float] = None       # None when no purchase events recorded
    conversion_value: Optional[float] = None  # None when no purchase value recorded


class MetaInsightsOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    client_id: str
    account_id: str         # act_{id} — no token, no secret
    date_from: str
    date_to: str
    aggregation: str        # daily | monthly
    level: str              # account | campaign | adset | ad
    rows: list[MetaInsightRow]
    row_count: int          # total aggregated rows available (may exceed len(rows) when truncated)
    data_source: str = "meta_ad_attributions"
    note: Optional[str] = None
    request_id: str


# ── 17. Entity performance — campaign rows (P3 — Wave 1) ─────────────────────

class CampaignPerformanceRow(_Base):
    platform: str
    campaign_id: str
    campaign_name: Optional[str] = None
    date: date
    spend: Optional[float] = None
    impressions: Optional[int] = None
    clicks: Optional[int] = None
    conversions: Optional[int] = None
    revenue: Optional[float] = None
    roas: Optional[float] = None
    cpa: Optional[float] = None
    ctr: Optional[float] = None
    cpc: Optional[float] = None
    cpm: Optional[float] = None


class EntityPerformanceOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    client_id: str
    level: Literal["campaign"] = "campaign"
    period: Period
    platform: Optional[str] = None
    total_rows: int
    page: int
    page_size: int
    rows: list[CampaignPerformanceRow]


# ── 18. Outcomes ─────────────────────────────────────────────────────────────

class OutcomeCreate(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    action_event_id: str
    measurement_period: dict[str, Any] = Field(default_factory=dict)
    metric_refs: list[str] = Field(default_factory=list)
    before_state: Optional[dict[str, Any]] = None
    after_state: Optional[dict[str, Any]] = None
    delta: Optional[dict[str, Any]] = None
    status: OutcomeStatus = OutcomeStatus.PENDING
    measured_at: Optional[datetime] = None


class OutcomeOut(OutcomeCreate):
    id: str
    recommendation_id: Optional[str] = None
    client_id: Optional[str] = None
    created_at: datetime
    created_by: str = "human"


# ── 19. Error responses ───────────────────────────────────────────────────────

class ErrorOut(_Base):
    """Standard error envelope returned on 401, 403, 409, 429 and 503."""
    error: str = Field(description="Machine-readable error code")
    detail: Optional[str] = Field(default=None, description="Human-readable explanation")
    request_id: Optional[str] = Field(default=None, description="Echoed from X-Request-ID header")
