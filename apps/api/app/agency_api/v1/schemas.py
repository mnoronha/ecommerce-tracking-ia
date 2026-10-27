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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    ActionEventType,
    ActionStatus,
    ActionType,
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
    MeetingProcessingStatus,
    MeetingProvider,
    MetricDomain,
    NarrativeStatus,
    NotionSyncState,
    ObsidianStatus,
    OutcomeStatus,
    PromotionStatus,
    RecommendationStatus,
    ReportType,
    ServiceStatus,
    SourceState,
    TargetStatus,
    TaskPriority,
    TaskSourceType,
    TaskStatus,
    TranscriptStatus,
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


# ── 2b. Target Truth write contract ──────────────────────────────────────────


class TargetEntry(_Base):
    """
    One metric target. Backward-compatible with legacy {target, currency} shape.
    period=None means UNKNOWN (pre-existing rows written before the write API existed).
    """
    target: float
    unit: Optional[str] = None
    currency: Optional[str] = None
    period: Optional[str] = None   # "daily|weekly|monthly|quarterly|annual|none" | None=UNKNOWN
    channel: Optional[str] = None  # "google|meta|tiktok|pinterest|all" | None=not specified


class TargetTruthPatch(_Base):
    """
    Request body for PUT /clients/{client_id}/truth/targets.

    Merge semantics:
    - 'targets': keys to SET/ADD. Only the listed keys are changed; all others preserved.
    - 'remove_keys': explicit list of keys to DELETE. Absence from 'targets' NEVER deletes.
    - Keys in 'remove_keys' take precedence over 'targets' for the same key.
    - 'actor' MUST be a human identifier (slug or UUID), never a service name.
    """
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    targets: dict[str, TargetEntry] = Field(default_factory=dict)
    remove_keys: list[str] = Field(default_factory=list)
    actor: str = Field(description="Human identifier — not a service name")
    reason: Optional[str] = None
    provenance: str = Field(
        default="manual_agency",
        description="Origin of this write: manual_agency | notion_import | pipeline | etc.",
    )


class TargetTruthWriteOut(_Base):
    """Response for a successful Target Truth version write."""
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    client_id: str
    target_version: int
    target_truth: dict[str, Any]
    previous_version: int
    keys_set: list[str]
    keys_removed: list[str]
    actor: str
    reason: Optional[str] = None
    provenance: str
    valid_from: datetime
    idempotent_replay: bool = False


class TargetTruthReadOut(_Base):
    """Response for GET /clients/{client_id}/truth/targets — enriched with period audit."""
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    client_id: str
    target_version: int
    target_truth: dict[str, Any]
    period_audit: dict[str, Optional[str]]  # key → period value or None (UNKNOWN)
    valid_from: datetime
    write_meta: Optional[dict[str, Any]] = None


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
    # v2 — canonical media change contract
    entity_id: Optional[str] = None
    description: Optional[str] = None
    requested_by: Optional[str] = None
    approved_by: Optional[str] = None
    executed_by: Optional[str] = None
    recommendation_id: Optional[str] = None
    task_id: Optional[str] = None
    report_id: Optional[str] = None
    detected_at: Optional[datetime] = None
    expected_impact: Optional[Any] = None
    risk_level: Optional[Level] = None
    outcome_status: Optional[OutcomeStatus] = None
    outcome_summary: Optional[str] = None


class ChangeOut(ChangeCreate):
    id: str
    external_change_id: Optional[str] = None
    match_status: Optional[MatchStatus] = None
    matched_change_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


# ── 7. Alerts ─────────────────────────────────────────────────────────────────

class AlertOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    id: str
    client_id: Optional[str] = None       # slug — None for system-wide alerts
    alert_type: str
    severity: str                           # HIGH | MEDIUM | LOW
    status: AlertStatus = AlertStatus.OPEN
    platform: Optional[str] = None         # google | meta | None for pipeline/source alerts
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


# ── 9c. Recommendation approve (H10 bridge: hermes_service → Core Task) ──────

class RecommendationApproval(_Base):
    """
    Request body for POST /recommendations/{id}/approve.

    Allowed scopes: agency_admin, hermes_service  (SCOPE_RECOMMENDATION_APPROVE).
    platform_web is excluded — human approvals use /recommendations/{id}/decision.

    This is the ONLY path for hermes_service to trigger task creation.
    Generic POST /tasks remains blocked to hermes_service.
    """
    actor: str = Field(
        description=(
            "Stable identifier of who/what is approving "
            "(human slug/UUID for human-in-loop; service identity for automated H10)."
        )
    )
    reason: Optional[str] = None


class RecommendationApprovalOut(_Base):
    """Response for POST /recommendations/{id}/approve."""
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    recommendation_id: str
    task_id: str
    client_id: str
    actor: str
    approved_at: datetime
    created: bool  # True = new task; False = idempotent replay of existing task


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


# ── 10b. Actions (M20) ───────────────────────────────────────────────────────

class ActionCreate(_Base):
    """Request body for POST /recommendations/{id}/actions."""
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    action_type: ActionType
    platform: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    evaluation_window_days: int = Field(default=7, ge=1, le=90)
    before_state: dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    requires_external_write: bool = False
    action_event_id: Optional[str] = None  # approving decision event ("decision_id")
    task_id: Optional[str] = None          # task that triggered this action


class ActionOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    id: str
    client_id: str
    recommendation_id: str
    diagnosis_id: Optional[str] = None
    alert_id: Optional[str] = None
    action_type: str
    platform: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    status: str = "PLANNED"
    executed_by: Optional[str] = None
    executed_at: Optional[datetime] = None
    evaluation_window_days: int = 7
    before_state: dict[str, Any] = Field(default_factory=dict)
    after_state: dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    requires_external_write: bool = False
    change_log_id: Optional[str] = None
    action_event_id: Optional[str] = None
    task_id: Optional[str] = None
    created_at: datetime


class ActionUpdate(_Base):
    """Request body for PUT /actions/{id} — status transition."""
    status: ActionStatus
    executed_by: Optional[str] = None
    after_state: dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = None


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
    # Actions from the contract JSONB — empty when contract not yet built
    executed_actions: list[dict] = Field(default_factory=list)
    open_recommendations: list[dict] = Field(default_factory=list)


class NarrativeTransition(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    target_status: NarrativeStatus
    actor: str
    note: Optional[str] = None
    # Optional internal-only delivery override: if set, report is sent only to this
    # address instead of the full client list. Does not modify stored client recipients.
    recipient_override: Optional[str] = None


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
    source_action_id: Optional[str] = None          # idempotency key (preferred)
    source_recommendation_id: Optional[str] = None  # idempotency key (fallback)
    outcome_id: Optional[str] = None                # outcome that motivated this candidate


class LearningCandidateOut(LearningCandidateCreate):
    id: str
    status: LearningCandidateStatus = LearningCandidateStatus.PENDING
    pattern: Optional[str] = None
    observed_outcome: dict[str, Any] = Field(default_factory=dict)
    reused: bool = False
    created_at: datetime


class LearningCandidateDecision(_Base):
    """Human approval / rejection of a learning candidate."""
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    decision: Literal["APPROVED", "REJECTED", "SUPERSEDED"]
    actor: str
    note: Optional[str] = None


# ── 14b. Knowledge Promotion (M21) ────────────────────────────────────────────

class KnowledgePromotionCreate(_Base):
    """Request body for POST /learning-candidates/{id}/promote."""
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    reason: Optional[str] = None
    force_new_version: bool = False


class KnowledgePromotionOut(_Base):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    id: str                                         # promotion_id
    learning_candidate_id: str
    knowledge_id: Optional[str] = None
    actor: str
    reason: Optional[str] = None
    status: PromotionStatus
    destination: str = "agency_knowledge"
    document_path: Optional[str] = None
    document_version: int = 1
    obsidian_status: ObsidianStatus = ObsidianStatus.PENDING
    obsidian_url: Optional[str] = None
    existing_knowledge_id: Optional[str] = None
    error: Optional[str] = None
    promoted_at: Optional[datetime] = None
    created_at: datetime


class AgencyKnowledgeOut(_Base):
    """A single promoted knowledge document, readable by Hermes."""
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    id: str
    learning_candidate_id: Optional[str] = None
    title: str
    statement: str
    pattern: Optional[str] = None
    context: dict[str, Any] = Field(default_factory=dict)
    observed_outcome: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_level: str = "OBSERVATION"
    confidence: Optional[str] = None
    scope: str
    client_id: Optional[str] = None
    business_model: Optional[str] = None
    platform: Optional[str] = None
    when_to_apply: Optional[str] = None
    when_not_to_apply: Optional[str] = None
    limitations: Optional[str] = None
    source_alert_id: Optional[str] = None
    source_diagnosis_id: Optional[str] = None
    source_recommendation_id: Optional[str] = None
    source_action_id: Optional[str] = None
    source_outcome_id: Optional[str] = None
    approved_by: str
    approved_at: str
    promoted_by: str
    document_path: Optional[str] = None
    document_version: int = 1
    obsidian_status: str = "PENDING"
    obsidian_url: Optional[str] = None
    promoted_at: datetime
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


class EnvVarStatus(_Base):
    present: bool
    length: int


class NotificationsConfigOut(_Base):
    """Runtime presence of notification env vars — never exposes values."""
    railway_service: Optional[str] = None
    telegram_bot_token: EnvVarStatus
    telegram_chat_id: EnvVarStatus
    env_names_match_code: Literal["PASS"] = "PASS"
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
    action_event_id: Optional[str] = None
    evaluation_window_days: Optional[int] = None  # idempotency key together with action_event_id
    measurement_period: dict[str, Any] = Field(default_factory=dict)
    metric_refs: list[str] = Field(default_factory=list)
    before_state: Optional[dict[str, Any]] = None
    after_state: Optional[dict[str, Any]] = None
    delta: Optional[dict[str, Any]] = None
    status: OutcomeStatus = OutcomeStatus.PENDING
    measured_at: Optional[datetime] = None


class OutcomeOut(OutcomeCreate):
    id: str
    action_id: Optional[str] = None
    recommendation_id: Optional[str] = None
    client_id: Optional[str] = None
    confidence: Optional[str] = None
    data_health: dict[str, Any] = Field(default_factory=dict)
    reused: bool = False
    created_at: datetime
    created_by: str = "human"


# ── 19. Error responses ───────────────────────────────────────────────────────

class ErrorOut(_Base):
    """Standard error envelope returned on 401, 403, 409, 429 and 503."""
    error: str = Field(description="Machine-readable error code")
    detail: Optional[str] = Field(default=None, description="Human-readable explanation")
    request_id: Optional[str] = Field(default=None, description="Echoed from X-Request-ID header")


# ── 20. Balance monitoring ────────────────────────────────────────────────────

class BalanceSnapshotOut(_Base):
    """Latest balance snapshot for a single platform."""
    platform: str
    billing_model: Optional[str] = None        # "prepaid" | "postpaid" | None
    collection_status: Optional[str] = None    # "PASS" | "BLOCKED" | "PERMISSION_DENIED" | "NOT_SUPPORTED"
    balance_available: Optional[float] = None  # actual balance in major currency units
    balance_status: str                        # OK | LOW | CRITICAL | EXHAUSTED | NO_DATA | MISSING | …
    currency: Optional[str] = None
    spend_daily_reference: Optional[float] = None  # avg daily spend used for estimation
    spend_avg_window_days: Optional[int] = None    # window used (3 or 7 days)
    spend_data_days: Optional[int] = None          # actual days with spend > 0 in window
    estimated_days_remaining: Optional[float] = None
    threshold_low: Optional[float] = None
    threshold_critical: Optional[float] = None
    # ── Metas & Orçamento contract ─────────────────────────────────────────────
    days_threshold_low: int = 3         # days remaining ≤ this → LOW alert
    days_threshold_critical: int = 1    # days remaining ≤ this → CRITICAL alert
    source_state: Optional[str] = None  # alias for collection_status (Goals & Budget tab)
    # ──────────────────────────────────────────────────────────────────────────
    collected_at: Optional[datetime] = None
    error: Optional[str] = None


class ClientBalanceOut(_Base):
    """Balance summary for a client across all prepaid platforms."""
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    client_id: str
    snapshots: list[BalanceSnapshotOut]
    any_prepaid: bool
    monitoring_enabled: bool = False  # alias for any_prepaid (Goals & Budget tab)


class BalanceTriggerOut(_Base):
    """Result of a manual balance check trigger."""
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    client_id: str
    triggered_at: datetime
    platforms: dict[str, Any]  # per-platform snapshot results


# ── 21. Budget config ─────────────────────────────────────────────────────────

class BudgetConfigIn(_Base):
    """Request body for PUT /clients/{client_id}/budget/config."""
    platform: Literal["google", "meta"]
    period_label: str              # 'YYYY-MM'
    monthly_budget: float
    currency: str = "BRL"
    monitoring_enabled: bool = True
    actor: str = Field(description="Human identifier — not a service name")
    reason: Optional[str] = None
    provenance: str = Field(
        default="manual_agency",
        description="manual_agency | import | pipeline",
    )


class BudgetConfigOut(_Base):
    """Single budget config row response."""
    id: str
    client_id: str
    platform: str
    period_label: str
    monthly_budget: float
    currency: str
    monitoring_enabled: bool
    write_meta: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime


class BudgetConfigListOut(_Base):
    """List of budget configs for a client."""
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    client_id: str
    configs: list[BudgetConfigOut]


# ── 22. Pacing contract ───────────────────────────────────────────────────────

class PlatformBudgetPacing(_Base):
    """Budget pacing state for one platform in the current month."""
    platform: str
    monthly_budget: Optional[float] = None
    currency: str = "BRL"
    spend_mtd: float
    expected_spend_to_date: Optional[float] = None
    pacing_ratio: Optional[float] = None
    remaining_budget: Optional[float] = None
    projected_month_end_spend: Optional[float] = None
    budget_status: Literal["ON_PACE", "UNDER_PACE", "OVER_PACE", "UNKNOWN"] = "UNKNOWN"


class TargetPacingEntry(_Base):
    """Pacing state for one target metric in the current month."""
    metric_key: str
    target: float
    unit: Optional[str] = None
    actual: Optional[float] = None
    expected_target_to_date: Optional[float] = None   # None for efficiency metrics
    attainment_pct: Optional[float] = None
    projected_target_value: Optional[float] = None    # None for efficiency metrics
    target_status: Literal["ON_PACE", "BELOW_PACE", "AT_RISK", "EXCEEDED", "UNKNOWN"] = "UNKNOWN"


class PlatformBalanceSummary(_Base):
    """Compact balance view for the Metas & Orçamento tab."""
    platform: str
    balance: Optional[float] = None
    balance_status: str
    estimated_days_remaining: Optional[float] = None
    currency: Optional[str] = None


class PacingContract(_Base):
    """Full pacing contract for GET /clients/{client_id}/pacing."""
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    client_id: str
    period_label: str
    computed_at: datetime
    budget: list[PlatformBudgetPacing]
    targets: list[TargetPacingEntry]
    balance: list[PlatformBalanceSummary]


# ── 17. Tasks (Phase 2 + Phase 3 Notion sync prep) ────────────────────────────

class TaskCreate(_Base):
    title:            str
    description:      Optional[str]    = None
    priority:         TaskPriority     = TaskPriority.MEDIUM
    owner:            Optional[str]    = None
    due_date:         Optional[date]   = None
    source_type:      Optional[TaskSourceType] = None
    source_id:        Optional[str]    = None
    visibility_scope: VisibilityScope  = VisibilityScope.AGENCY_ONLY


class TaskUpdate(_Base):
    title:            Optional[str]          = None
    description:      Optional[str]          = None
    status:           Optional[TaskStatus]   = None
    priority:         Optional[TaskPriority] = None
    owner:            Optional[str]          = None
    due_date:         Optional[date]         = None
    visibility_scope: Optional[VisibilityScope] = None
    # Notion sync fields (Phase 3 — set by sync adapter, not by humans)
    external_task_id: Optional[str]          = None
    external_system:  Optional[str]          = None
    sync_state:       Optional[NotionSyncState] = None
    sync_error:       Optional[str]          = None


class TaskOut(_Base):
    schema_version:   Literal["1.1"] = SCHEMA_VERSION
    id:               str
    client_id:        str
    title:            str
    description:      Optional[str]       = None
    status:           TaskStatus
    priority:         TaskPriority
    owner:            Optional[str]       = None
    due_date:         Optional[date]      = None
    source_type:      Optional[str]       = None
    source_id:        Optional[str]       = None
    visibility_scope: VisibilityScope
    # Notion sync
    external_task_id: Optional[str]       = None
    external_system:  Optional[str]       = None
    sync_state:       NotionSyncState     = NotionSyncState.NONE
    last_synced_at:   Optional[datetime]  = None
    sync_error:       Optional[str]       = None
    # Timestamps
    created_at:       datetime
    updated_at:       datetime
    completed_at:     Optional[datetime]  = None


# ── 18. Meetings (Phase 5) ────────────────────────────────────────────────────

class MeetingParticipant(_Base):
    name:  Optional[str] = None
    email: Optional[str] = None
    role:  Optional[str] = None   # 'host' | 'attendee' | 'notetaker'


class MeetingCreate(_Base):
    title:          str
    provider:       MeetingProvider = MeetingProvider.MANUAL
    external_id:    Optional[str]   = None
    started_at:     Optional[datetime] = None
    ended_at:       Optional[datetime] = None
    participants:   list[MeetingParticipant] = Field(default_factory=list)
    recording_url:  Optional[str]   = None


class TranscriptIngestCreate(_Base):
    """Raw transcript ingest. Processor output created separately via /meetings/{id}/process."""
    raw_text:  str
    language:  str = "pt"


class MeetingDecision(_Base):
    decision:   str
    rationale:  Optional[str] = None
    owner:      Optional[str] = None


class MeetingCommitment(_Base):
    who:       Optional[str] = None
    what:      str
    due_date:  Optional[date] = None


class MeetingFollowUp(_Base):
    action:    str
    owner:     Optional[str] = None
    due_date:  Optional[date] = None


class MeetingProcessCreate(_Base):
    """Human or AI-produced structured summary. Processor is caller's identity."""
    summary:         Optional[str]                    = None
    decisions:       list[MeetingDecision]            = Field(default_factory=list)
    open_questions:  list[str]                        = Field(default_factory=list)
    commitments:     list[MeetingCommitment]          = Field(default_factory=list)
    follow_ups:      list[MeetingFollowUp]            = Field(default_factory=list)
    create_tasks:    bool                             = False  # if True: auto-create Tasks from commitments
    processor:       str                              = "manual"


class MeetingSummaryOut(_Base):
    id:                      str
    meeting_id:              str
    summary:                 Optional[str]   = None
    decisions:               list[dict]      = Field(default_factory=list)
    open_questions:          list[str]       = Field(default_factory=list)
    commitments:             list[dict]      = Field(default_factory=list)
    follow_ups:              list[dict]      = Field(default_factory=list)
    tasks_created:           list[dict]      = Field(default_factory=list)
    processor:               str
    processor_manual_status: str             = "AVAILABLE"
    processor_claude_status: str             = "UNKNOWN"
    replayed:                bool            = False
    created_at:              datetime


class MeetingOut(_Base):
    schema_version:     Literal["1.1"] = SCHEMA_VERSION
    id:                 str
    client_id:          str
    provider:           MeetingProvider
    external_id:        Optional[str]            = None
    title:              str
    started_at:         Optional[datetime]        = None
    ended_at:           Optional[datetime]        = None
    participants:       list[MeetingParticipant]  = Field(default_factory=list)
    recording_url:      Optional[str]             = None
    transcript_status:  TranscriptStatus
    processing_status:  MeetingProcessingStatus
    summary:            Optional[MeetingSummaryOut] = None
    created_at:         datetime
    updated_at:         datetime


# ── 17. Tracking Ingestion (Phase 7) ─────────────────────────────────────────

_VALID_EVENT_NAMES = frozenset(
    {"page_view", "view_item", "add_to_cart", "begin_checkout", "purchase"}
)


class TrackingEventIn(_Base):
    """Single browser/server tracking event sent to the ingestion endpoint."""

    event_id:           str
    event_name:         str
    occurred_at:        datetime
    session_id:         Optional[str]   = None
    visitor_cookie_id:  Optional[str]   = None
    url:                Optional[str]   = None
    referrer:           Optional[str]   = None
    utm_source:         Optional[str]   = None
    utm_medium:         Optional[str]   = None
    utm_campaign:       Optional[str]   = None
    utm_content:        Optional[str]   = None
    utm_term:           Optional[str]   = None
    fbp:                Optional[str]   = None
    fbc:                Optional[str]   = None
    fbclid:             Optional[str]   = None
    gclid:              Optional[str]   = None
    gbraid:             Optional[str]   = None
    wbraid:             Optional[str]   = None
    ga_client_id:       Optional[str]   = None
    ga_session_id:      Optional[str]   = None   # v4.3.1+: GA4 session from _ga_<MEASUREMENT_ID>
    order_id:           Optional[str]   = None
    value:              Optional[float] = None
    currency:           Optional[str]   = None
    product_id:         Optional[str]   = None
    variant_id:         Optional[str]   = None   # v4.3+
    sku:                Optional[str]   = None   # v4.3+
    product_name:       Optional[str]   = None
    product_price:      Optional[float] = None
    product_quantity:   Optional[int]   = None
    product_category:   Optional[str]   = None
    properties:         Optional[dict]  = None

    @field_validator("event_id")
    @classmethod
    def event_id_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("event_id must be a non-empty string")
        return v

    @field_validator("event_name")
    @classmethod
    def event_name_valid(cls, v: str) -> str:
        if v not in _VALID_EVENT_NAMES:
            raise ValueError(
                f"unknown event_name '{v}'. Valid: {sorted(_VALID_EVENT_NAMES)}"
            )
        return v


class TrackingEventBatchIn(_Base):
    """Batch wrapper. Use `{\"events\": [...]}` for single or multiple events."""

    events: list[TrackingEventIn]

    @field_validator("events")
    @classmethod
    def max_batch_size(cls, v: list) -> list:
        if len(v) > 50:
            raise ValueError("Batch limit is 50 events per request")
        if len(v) == 0:
            raise ValueError("events list must not be empty")
        return v


class TrackingEventResult(_Base):
    event_id:   str
    status:     Literal["accepted", "duplicate", "rejected"]
    reason:     Optional[str] = None


class TrackingIngestOut(_Base):
    events_received:        int
    events_inserted:        int
    events_deduplicated:    int
    events_rejected:        int
    sessions_created:       int
    sessions_reused:        int
    identities_created:     int
    identities_linked:      int
    results:                list[TrackingEventResult]
