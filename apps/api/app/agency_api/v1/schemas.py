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
    EvidenceLevel,
    JobStatus,
    LearningCandidateStatus,
    LearningScope,
    Level,
    MatchStatus,
    MetricDomain,
    NarrativeStatus,
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
    domain: str  # "business"|"google_ads"|"meta_ads"|"ga4"|"conversion_map"|"notion_truth"
    source_state: SourceState
    reason: Optional[str] = None
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
    client_id: str
    rule_key: str
    rule_version: str
    metric_key: str
    entity: Optional[str] = None
    observed_snapshot_refs: list[str] = Field(default_factory=list)
    baseline_snapshot_refs: list[str] = Field(default_factory=list)
    delta_pct: Optional[float] = None
    min_volume_met: bool = True
    severity: Level
    status: AlertStatus = AlertStatus.OPEN
    created_at: datetime
    resolved_at: Optional[datetime] = None


class AlertContext(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    alert: AlertOut
    metrics: list[MetricValue]
    changes: list[ChangeOut]
    truth: dict[str, Any]
    health: dict[str, SourceState]


# ── 8. Diagnoses (intel) ──────────────────────────────────────────────────────

class Hypothesis(_Base):
    statement: str
    supporting_refs: list[str] = Field(default_factory=list)
    how_to_verify: Optional[str] = None


class DiagnosisCreate(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    alert_id: str
    facts: list[TextWithRefs]
    localization: Optional[str] = None
    related_changes: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    confidence: Level
    do_not_conclude: list[str] = Field(default_factory=list)
    data_limitations: list[str] = Field(default_factory=list)
    visibility_scope: VisibilityScope = VisibilityScope.AGENCY_ONLY


class DiagnosisOut(DiagnosisCreate):
    id: str
    created_at: datetime


# ── 9. Recommendations (intel) ────────────────────────────────────────────────

class ActionProposal(_Base):
    platform: str
    platform_account_id: str
    entity_type: str
    entity_id: str
    entity_name_at_time: str
    change_type: ChangeType
    before: Optional[Any] = None
    after: Optional[Any] = None
    unit: Optional[str] = None


class RecommendationCreate(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    diagnosis_id: str
    recommendation: str
    action_proposal: Optional[ActionProposal] = None
    priority: Level
    confidence: Level
    risk: Level
    reversible: bool
    expected_effect: Optional[str] = None
    review_window_days: int = 3
    requires_approval: bool = True
    visibility_scope: VisibilityScope = VisibilityScope.AGENCY_ONLY


class RecommendationOut(RecommendationCreate):
    id: str
    status: RecommendationStatus = RecommendationStatus.PENDING_REVIEW
    created_at: datetime


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


class SystemHealthOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    api: Literal["UP"] = "UP"
    database: ServiceStatus
    workers: list[WorkerHealth]
    dead_jobs: list[JobOut]
    checked_at: datetime


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


# ── 18. Error responses ───────────────────────────────────────────────────────

class ErrorOut(_Base):
    """Standard error envelope returned on 401, 403, 409, 429 and 503."""
    error: str = Field(description="Machine-readable error code")
    detail: Optional[str] = Field(default=None, description="Human-readable explanation")
    request_id: Optional[str] = Field(default=None, description="Echoed from X-Request-ID header")
