"""
Agency API v1 router — stub implementation (Etapa 1).

Every endpoint returns a valid example response that matches its schema.
Real implementation replaces stubs incrementally from Etapa 4 onwards.

Auth: each route declares the allowed scopes via Depends(require_scopes(...)).
Idempotency-Key header is accepted on all write endpoints (ignored in stubs).

Route inventory (30 routes):
  GET  /clients
  GET  /clients/{client_id}/truth
  GET  /clients/{client_id}/health
  GET  /clients/{client_id}/pipeline-health
  GET  /clients/{client_id}/metrics
  GET  /clients/{client_id}/changes
  GET  /clients/{client_id}/recommendations
  GET  /clients/{client_id}/report-contracts
  GET  /clients/{client_id}/reports
  GET  /clients/{client_id}/alerts
  GET  /alerts
  GET  /alerts/{alert_id}/context
  GET  /alerts/{alert_id}/diagnoses        [CCR-011]
  GET  /diagnoses/{diagnosis_id}           [CCR-011]
  GET  /diagnoses/{diagnosis_id}/recommendations  [CCR-011]
  GET  /recommendations/{recommendation_id}       [CCR-011]
  GET  /alert-rule-suggestions
  GET  /system/health
  GET  /jobs
  GET  /jobs/{job_id}
  POST /diagnoses
  POST /recommendations
  POST /changes
  POST /action-events
  POST /alert-feedback
  POST /report-narratives
  POST /report-narratives/{narrative_id}/transitions
  POST /alert-rule-suggestions/{suggestion_id}/decision
  POST /learning-candidates
  POST /jobs/{job_id}/replay
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query

logger = logging.getLogger(__name__)

from .auth import (
    SCOPE_ADMIN_ONLY,
    SCOPE_ANY_AUTH,
    SCOPE_CLIENT_VIEWER,
    SCOPE_HERMES_WRITE,
    SCOPE_PLATFORM_WRITE,
    SCOPE_READ_ANY,
    AuthContext,
    require_scopes,
)
from .schemas import (
    ActionEventCreate,
    ActionEventOut,
    ErrorOut,
    AlertContext,
    AlertFeedbackCreate,
    AlertOut,
    AlertRuleSuggestionDecision,
    AlertRuleSuggestionOut,
    AlertRuleSuggestionStatus,
    AlertStatus,
    BusinessModel,
    CampaignPerformanceRow,
    CertificationStatus,
    ChangeCreate,
    ChangeConfidence,
    ChangeLogSource,
    ChangeOut,
    ChangeType,
    ClientOut,
    CoreReportContractOut,
    DataHealthEntry,
    DataHealthOut,
    DataSourceDetail,
    DiagnosisCreate,
    DiagnosisOut,
    EntityPerformanceOut,
    EvidenceLevel,
    Hypothesis,
    JobOut,
    JobStatus,
    LearningCandidateCreate,
    LearningCandidateOut,
    LearningCandidateStatus,
    LearningScope,
    Level,
    MatchStatus,
    MetricContract,
    MetricRef,
    MetricValue,
    NarrativeBlock,
    NarrativeStatus,
    NarrativeTransition,
    Period,
    PipelineHealthOut,
    RecommendationCreate,
    RecommendationOut,
    RecommendationStatus,
    ReportNarrativeCreate,
    ReportNarrativeOut,
    ReportType,
    SchedulerJobStatus,
    ServiceStatus,
    SourceState,
    SystemHealthOut,
    TargetStatus,
    TextWithRefs,
    TruthOut,
    TruthVersions,
    ValueStatus,
    VisibilityScope,
    WorkerHealth,
)

router = APIRouter(prefix="/agency/v1", tags=["agency-v1"])

_STUB_NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
_STUB_VERSIONS = TruthVersions(client=1, target=1, conversion_map=1)
_STUB_PERIOD = Period(start=date(2026, 9, 26), end=date(2026, 10, 2))


# ── DB helpers (real reads — Etapa 4) ─────────────────────────────────────────

def _get_db():
    from ...database import get_supabase
    return get_supabase()


def _parse_period(period_param: Optional[str]) -> tuple[date, date]:
    """
    Parse YYYY-MM-DD:YYYY-MM-DD. Defaults to yesterday-6d → yesterday.
    """
    if period_param:
        try:
            parts = period_param.split(":")
            if len(parts) == 2:
                return date.fromisoformat(parts[0]), date.fromisoformat(parts[1])
        except ValueError:
            pass
    today     = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    return yesterday - timedelta(days=6), yesterday


def _source_to_domain(source_system: str) -> str:
    return {
        "shopify":    "business",
        "meta_ads":   "meta_ads",
        "google_ads": "google_ads",
        "ga4":        "ga4",
    }.get(source_system, source_system)


def _load_client_meta(client_id: str) -> Optional[dict]:
    try:
        r = (
            _get_db().table("clients")
            .select("id, client_id, name, business_model, timezone, currency, country, is_active")
            .eq("client_id", client_id)
            .limit(1)
            .execute()
        )
        return r.data[0] if (r and r.data) else None
    except Exception as exc:
        logger.warning("agency_api: client load failed %s: %s", client_id, exc)
        return None


# ── GET /clients ──────────────────────────────────────────────────────────────

@router.get(
    "/clients",
    summary="List active clients with business_model, timezone and currency",
    response_model=list[ClientOut],
)
async def list_clients(
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)],
) -> list[ClientOut]:
    try:
        rows = (
            _get_db().table("clients")
            .select("client_id, name, business_model, timezone, currency, country, is_active")
            .not_.is_("client_id", "null")
            .eq("is_active", True)
            .execute()
        )
        return [
            ClientOut(
                client_id=r["client_id"],
                name=r.get("name") or r["client_id"],
                business_model=BusinessModel(r["business_model"]) if r.get("business_model") else BusinessModel.ECOMMERCE,
                timezone=r.get("timezone") or "America/Sao_Paulo",
                currency=r.get("currency") or "BRL",
                country=r.get("country"),
                status="active" if r.get("is_active") else "inactive",
            )
            for r in (rows.data or [])
        ]
    except Exception as exc:
        logger.error("list_clients: DB error: %s", exc)
        raise HTTPException(503, "database unavailable")


# ── GET /clients/{client_id}/truth ────────────────────────────────────────────

@router.get(
    "/clients/{client_id}/truth",
    summary="Client Truth and Target Truth — current versions",
    response_model=TruthOut,
)
async def get_client_truth(
    client_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)],
) -> TruthOut:
    try:
        row = (
            _get_db().table("core_client_truth")
            .select("client_id, client_version, client_truth, target_version, target_truth, valid_from")
            .eq("client_id", client_id)
            .order("valid_from", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("get_client_truth: DB error for %s: %s", client_id, exc)
        raise HTTPException(503, "database unavailable")

    if not row.data:
        raise HTTPException(404, f"truth not found for client {client_id!r}")

    r = row.data[0]
    vf = r.get("valid_from")
    if isinstance(vf, str):
        vf = datetime.fromisoformat(vf.replace("Z", "+00:00"))

    return TruthOut(
        client_id=r["client_id"],
        client_version=r["client_version"],
        client_truth=r["client_truth"],
        target_version=r["target_version"],
        target_truth=r["target_truth"],
        valid_from=vf,
    )


# ── Data Health helpers ───────────────────────────────────────────────────────

# Freshness thresholds per source (reused from DQG defaults)
_FRESHNESS_HOURS_HEALTH: dict[str, int] = {
    "shopify":    24,
    "meta_ads":   48,
    "google_ads": 48,
    "ga4":        72,
}

# RUNNING jobs stuck after this many minutes
_STUCK_RUNNING_MINUTES = 120
# QUEUED jobs stuck after this many minutes
_STUCK_QUEUED_MINUTES = 240


def _collection_status(source_state: str) -> str:
    if source_state in ("READY", "PARTIAL"):
        return "OK"
    if source_state == "NO_DATA":
        return "NO_DATA"
    if source_state == "STALE":
        return "STALE"
    if source_state in ("NOT_CONTRACTED", "NOT_APPLICABLE"):
        return "NOT_APPLICABLE"
    return "ERROR"


def _freshness_status(source_system: str, last_data_at_str: Optional[str], now: datetime) -> str:
    if not last_data_at_str:
        return "UNKNOWN"
    threshold_h = _FRESHNESS_HOURS_HEALTH.get(source_system, 48)
    try:
        dt = datetime.fromisoformat(last_data_at_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_h = (now - dt).total_seconds() / 3600
        return "FRESH" if age_h <= threshold_h else "STALE"
    except ValueError:
        return "UNKNOWN"


def _reconciliation_status(reconciliation_state: Optional[str]) -> str:
    if reconciliation_state is None:
        return "UNKNOWN"
    return "OK" if reconciliation_state == "OK" else "DEGRADED"


def _parse_dt_safe(v: Optional[str]) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


# ── GET /clients/{client_id}/health ──────────────────────────────────────────

@router.get(
    "/clients/{client_id}/health",
    summary="Data Health by domain with source_state and derived statuses",
    response_model=DataHealthOut,
)
async def get_client_health(
    client_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)],
) -> DataHealthOut:
    now = datetime.now(timezone.utc)
    try:
        sources = (
            _get_db().table("core_data_sources")
            .select(
                "source_key, source_system, semantic_domain, source_state, "
                "last_attempt_at, last_data_at, last_validated_at, "
                "last_reconciled_at, reconciliation_state, last_error"
            )
            .eq("client_id", client_id)
            .execute()
        )
    except Exception as exc:
        logger.error("get_client_health: DB error for %s: %s", client_id, exc)
        raise HTTPException(503, "database unavailable")

    if not sources.data:
        raise HTTPException(404, f"no sources found for client {client_id!r}")

    entries: list[DataHealthEntry] = []
    for s in sources.data:
        sys = s.get("source_system") or ""
        state_str = s.get("source_state") or "ERROR"
        recon_state = s.get("reconciliation_state")
        checked_at = _parse_dt_safe(s.get("last_validated_at")) or now

        entries.append(DataHealthEntry(
            domain=_source_to_domain(sys),
            source_key=s.get("source_key"),
            source_system=sys,
            semantic_domain=s.get("semantic_domain"),
            source_state=SourceState(state_str),
            collection_status=_collection_status(state_str),
            freshness_status=_freshness_status(sys, s.get("last_data_at"), now),
            reconciliation_status=_reconciliation_status(recon_state),
            last_attempt_at=_parse_dt_safe(s.get("last_attempt_at")),
            last_data_at=_parse_dt_safe(s.get("last_data_at")),
            last_validated_at=_parse_dt_safe(s.get("last_validated_at")),
            last_reconciled_at=_parse_dt_safe(s.get("last_reconciled_at")),
            reconciliation_state=recon_state,
            reason=s.get("last_error"),
            checked_at=checked_at,
        ))

    return DataHealthOut(client_id=client_id, health=entries, checked_at=now)


# ── GET /clients/{client_id}/pipeline-health ─────────────────────────────────

@router.get(
    "/clients/{client_id}/pipeline-health",
    summary="Last collection, certification, alert evaluation, notion sync and report",
    response_model=PipelineHealthOut,
)
async def get_pipeline_health(
    client_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)],
) -> PipelineHealthOut:
    now = datetime.now(timezone.utc)
    try:
        sources_res = (
            _get_db().table("core_data_sources")
            .select(
                "source_key, source_system, semantic_domain, source_state, "
                "last_attempt_at, last_data_at, last_validated_at, "
                "last_reconciled_at, reconciliation_state, last_error"
            )
            .eq("client_id", client_id)
            .execute()
        )
        snap = (
            _get_db().table("core_metric_snapshots")
            .select("computed_at, view")
            .eq("client_id", client_id)
            .order("computed_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("get_pipeline_health: DB error for %s: %s", client_id, exc)
        raise HTTPException(503, "database unavailable")

    def _parse_dt(v: Optional[str]) -> Optional[datetime]:
        if not v:
            return None
        return datetime.fromisoformat(v.replace("Z", "+00:00"))

    last_collection: dict[str, Optional[datetime]] = {}
    certification:   dict[str, Optional[str]]      = {}
    source_details:  list[DataSourceDetail]        = []

    for s in (sources_res.data or []):
        domain = _source_to_domain(s["source_system"])
        last_collection[domain] = _parse_dt(s.get("last_data_at"))
        recon = s.get("reconciliation_state")
        if recon == "OK" and s["source_state"] == "READY":
            certification[domain] = CertificationStatus.PROVISIONAL.value
        else:
            certification[domain] = None

        source_details.append(DataSourceDetail(
            source_key=s.get("source_key") or s["source_system"],
            source_system=s["source_system"],
            semantic_domain=s.get("semantic_domain"),
            source_state=SourceState(s["source_state"]),
            last_attempt_at=_parse_dt(s.get("last_attempt_at")),
            last_data_at=_parse_dt(s.get("last_data_at")),
            last_validated_at=_parse_dt(s.get("last_validated_at")),
            last_reconciled_at=_parse_dt(s.get("last_reconciled_at")),
            reconciliation_state=s.get("reconciliation_state"),
            last_error=s.get("last_error"),
        ))

    last_snap_at: Optional[datetime] = None
    if snap.data:
        last_snap_at = _parse_dt(snap.data[0].get("computed_at"))

    return PipelineHealthOut(
        client_id=client_id,
        last_collection=last_collection,
        certification=certification,
        sources=source_details,
        last_report_at=last_snap_at,
        checked_at=now,
    )


# ── GET /clients/{client_id}/metrics ─────────────────────────────────────────

@router.get(
    "/clients/{client_id}/metrics",
    summary="Metric contract — live or certified view",
    response_model=MetricContract,
)
async def get_metrics(
    client_id: str,
    period: Optional[str] = Query(None, description="YYYY-MM-DD:YYYY-MM-DD"),
    grain: Optional[str] = Query("day"),
    view: Optional[str] = Query("live", pattern="^(live|certified)$"),
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> MetricContract:
    view_str = view or "live"
    period_start, period_end = _parse_period(period)

    # Load client metadata
    client_meta = _load_client_meta(client_id)
    if not client_meta:
        raise HTTPException(404, f"client not found: {client_id!r}")

    bm_str  = client_meta.get("business_model") or "ecommerce"
    tz_str  = client_meta.get("timezone") or "America/Sao_Paulo"
    cur_str = client_meta.get("currency") or "BRL"

    try:
        snap = (
            _get_db().table("core_metric_snapshots")
            .select("id, metrics, health, client_truth_version, target_truth_version, conversion_map_version, computed_at, view")
            .eq("client_id", client_id)
            .eq("period_start", period_start.isoformat())
            .eq("period_end", period_end.isoformat())
            .eq("view", view_str)
            .order("computed_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("get_metrics: DB error for %s: %s", client_id, exc)
        raise HTTPException(503, "database unavailable")

    if not snap.data:
        # No snapshot yet — return empty contract, all NO_DATA
        return MetricContract(
            client_id=client_id,
            business_model=BusinessModel(bm_str),
            timezone=tz_str,
            currency=cur_str,
            period=Period(start=period_start, end=period_end),
            view=view_str,  # type: ignore[arg-type]
            truth_versions=TruthVersions(client=1, target=1, conversion_map=1),
            metrics=[],
            health={},
        )

    row = snap.data[0]
    raw_metrics: list[dict] = row.get("metrics") or []
    raw_health:  dict       = row.get("health")  or {}

    metrics = [
        MetricValue(
            metric_key=m["metric_key"],
            value=m.get("value"),
            unit=m.get("unit"),
            currency=m.get("currency"),
            value_status=ValueStatus(m.get("value_status", "UNKNOWN")),
            reason_code=m.get("reason_code"),
            certification_status=CertificationStatus(m["certification_status"]) if m.get("certification_status") else None,
            snapshot_ids=m.get("snapshot_ids", []),
            target=m.get("target"),
            target_status=TargetStatus(m["target_status"]) if m.get("target_status") else None,
            domain=m.get("domain"),
        )
        for m in raw_metrics
    ]

    health_out = {k: SourceState(v) for k, v in raw_health.items() if v}

    return MetricContract(
        client_id=client_id,
        business_model=BusinessModel(bm_str),
        timezone=tz_str,
        currency=cur_str,
        period=Period(start=period_start, end=period_end),
        view=view_str,  # type: ignore[arg-type]
        truth_versions=TruthVersions(
            client=row.get("client_truth_version", 1),
            target=row.get("target_truth_version", 1),
            conversion_map=row.get("conversion_map_version", 1),
        ),
        metrics=metrics,
        health=health_out,
    )


# ── GET /clients/{client_id}/entity-performance ──────────────────────────────

@router.get(
    "/clients/{client_id}/entity-performance",
    summary="Campaign-level performance rows (P3 — Wave 1)",
    response_model=EntityPerformanceOut,
)
async def get_entity_performance(
    client_id: str,
    period_start: str = Query(..., description="YYYY-MM-DD"),
    period_end: str   = Query(..., description="YYYY-MM-DD"),
    platform: Optional[str] = Query(None, description="meta | google"),
    level: str = Query("campaign", pattern="^campaign$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> EntityPerformanceOut:
    try:
        ps = date.fromisoformat(period_start)
        pe = date.fromisoformat(period_end)
    except ValueError:
        raise HTTPException(422, "period_start and period_end must be YYYY-MM-DD")

    try:
        client_meta = _load_client_meta(client_id)
        if not client_meta:
            raise HTTPException(404, f"client not found: {client_id!r}")

        client_uuid = client_meta.get("id") or client_id

        count_q = (
            _get_db().table("ad_campaigns")
            .select("id", count="exact")
            .eq("client_id", client_uuid)
            .gte("date", ps.isoformat())
            .lte("date", pe.isoformat())
            .is_("ad_id", "null")
        )
        if platform:
            count_q = count_q.eq("platform", platform.lower())
        count_res = count_q.execute()
        total = count_res.count or 0

        offset = (page - 1) * page_size
        rows_q = (
            _get_db().table("ad_campaigns")
            .select(
                "platform, campaign_id, campaign_name, date, "
                "spend, impressions, clicks, conversions, revenue, "
                "roas, cpa, ctr, cpc, cpm"
            )
            .eq("client_id", client_uuid)
            .gte("date", ps.isoformat())
            .lte("date", pe.isoformat())
            .is_("ad_id", "null")
            .order("spend", desc=True)
            .range(offset, offset + page_size - 1)
        )
        if platform:
            rows_q = rows_q.eq("platform", platform.lower())
        rows_res = rows_q.execute()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_entity_performance: DB error for %s: %s", client_id, exc)
        raise HTTPException(503, "database unavailable")

    perf_rows = [
        CampaignPerformanceRow(
            platform=r["platform"],
            campaign_id=r["campaign_id"],
            campaign_name=r.get("campaign_name"),
            date=date.fromisoformat(str(r["date"])),
            spend=float(r["spend"]) if r.get("spend") is not None else None,
            impressions=r.get("impressions"),
            clicks=r.get("clicks"),
            conversions=r.get("conversions"),
            revenue=float(r["revenue"]) if r.get("revenue") is not None else None,
            roas=float(r["roas"]) if r.get("roas") is not None else None,
            cpa=float(r["cpa"]) if r.get("cpa") is not None else None,
            ctr=float(r["ctr"]) if r.get("ctr") is not None else None,
            cpc=float(r["cpc"]) if r.get("cpc") is not None else None,
            cpm=float(r["cpm"]) if r.get("cpm") is not None else None,
        )
        for r in (rows_res.data or [])
    ]

    return EntityPerformanceOut(
        client_id=client_id,
        level="campaign",
        period=Period(start=ps, end=pe),
        platform=platform.lower() if platform else None,
        total_rows=total,
        page=page,
        page_size=page_size,
        rows=perf_rows,
    )


# ── GET /clients/{client_id}/changes ─────────────────────────────────────────
# CCR-014: core_change_log has 0 rows → honest empty list.

@router.get(
    "/clients/{client_id}/changes",
    summary="Change log for a client",
    response_model=list[ChangeOut],
)
async def get_changes(
    client_id: str,
    since: Optional[str] = Query(None, description="ISO-8601 datetime"),
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[ChangeOut]:
    # core_change_log is empty — return honest absence rather than fabricated stub
    return []


# ── GET /clients/{client_id}/recommendations ─────────────────────────────────
# CCR-014: core_recommendations has 0 rows → honest empty list.

@router.get(
    "/clients/{client_id}/recommendations",
    summary="Recommendations and their current status",
    response_model=list[RecommendationOut],
)
async def get_recommendations(
    client_id: str,
    status: Optional[str] = Query(None),
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[RecommendationOut]:
    return []


# ── GET /clients/{client_id}/report-contracts ─────────────────────────────────
# CCR-013: compatibility adapter — reads from legacy agency_report_contracts.
# Invariants:
#   - provenance_status="REAL" only when backed by a row in agency_report_contracts
#   - empty list = honest absence (no fabrication)
#   - report_type DB (lowercase) ↔ enum (UPPERCASE) mapping here
#   - schema_version "1.1" = Agency API envelope; legacy contract schema_version
#     preserved inside contract["legacy_schema_version"]
#   - Hermes must not access the DB directly; this endpoint is the only boundary

def _parse_ts_utc(s: object) -> Optional[datetime]:
    """Parse Supabase-returned timestamptz string to UTC-aware datetime."""
    if not s:
        return None
    try:
        normalized = str(s).replace(" ", "T")
        if normalized.endswith("+00"):
            normalized += ":00"
        if "+" not in normalized and not normalized.endswith("Z"):
            normalized += "+00:00"
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _load_truth_versions(client_id: str) -> TruthVersions:
    """Best-effort: fetch latest truth versions from core_client_truth."""
    try:
        row = (
            _get_db()
            .table("core_client_truth")
            .select("client_version, target_version, conversion_map_version")
            .eq("client_id", client_id)
            .order("valid_from", desc=True)
            .limit(1)
            .execute()
        )
        if row.data:
            d = row.data[0]
            return TruthVersions(
                client=int(d.get("client_version") or 1),
                target=int(d.get("target_version") or 1),
                conversion_map=int(d.get("conversion_map_version") or 1),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("CCR-013: truth versions unavailable for %s: %s", client_id, exc)
    return _STUB_VERSIONS


@router.get(
    "/clients/{client_id}/report-contracts",
    summary="Report contracts from legacy agency_report_contracts (CCR-013 compatibility adapter)",
    response_model=list[CoreReportContractOut],
)
async def get_report_contracts(
    client_id: str,
    report_type: Optional[str] = Query(None, pattern="^(WEEKLY|MONTHLY)$"),
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[CoreReportContractOut]:
    try:
        q = (
            _get_db()
            .table("agency_report_contracts")
            .select(
                "id, client_slug, report_type, "
                "period_start, period_end, "
                "comparison_period_start, comparison_period_end, "
                "schema_version, source_run_id, generated_at, contract"
            )
            .eq("client_slug", client_id)
            .order("period_end", desc=True)
            .limit(20)
        )
        if report_type:
            # DB stores lowercase; API query param is UPPERCASE
            q = q.eq("report_type", report_type.lower())
        rows = q.execute()
    except Exception as exc:
        logger.error("CCR-013 get_report_contracts: DB error for %s: %s", client_id, exc)
        raise HTTPException(503, "database unavailable")

    if not rows.data:
        return []  # honest absence — no fabrication, no stub

    truth_versions = _load_truth_versions(client_id)
    today = datetime.now(timezone.utc).date()
    results: list[CoreReportContractOut] = []

    for row in rows.data:
        # Map report_type: DB lowercase → enum UPPERCASE
        try:
            rt = ReportType((row.get("report_type") or "weekly").upper())
        except ValueError:
            rt = ReportType.WEEKLY

        p_start = date.fromisoformat(str(row["period_start"]))
        p_end   = date.fromisoformat(str(row["period_end"]))
        gen_at  = _parse_ts_utc(row.get("generated_at")) or _STUB_NOW

        # period_closed_at: set when the period has ended
        closed_at: Optional[datetime] = gen_at if p_end < today else None

        # Contract body: pass legacy JSONB through; preserve schema lineage
        contract_body: dict = dict(row.get("contract") or {})
        contract_body["legacy_schema_version"] = row.get("schema_version", "")
        if row.get("comparison_period_start"):
            contract_body["comparison_period"] = {
                "start": str(row["comparison_period_start"]),
                "end":   str(row.get("comparison_period_end") or ""),
            }

        results.append(
            CoreReportContractOut(
                report_contract_id=str(row["id"]),
                client_id=client_id,
                report_type=rt,
                period=Period(start=p_start, end=p_end),
                contract=contract_body,
                truth_versions=truth_versions,
                period_closed_at=closed_at,
                certification_coverage=None,
                generated_at=gen_at,
                supersedes_id=None,
                provenance_status="REAL",
            )
        )

    return results


# ── GET /clients/{client_id}/reports ─────────────────────────────────────────

# CCR-014: core_report_narratives has 0 rows → honest empty list.
@router.get(
    "/clients/{client_id}/reports",
    summary="Report narratives; client_viewer sees only PUBLISHED",
    response_model=list[ReportNarrativeOut],
)
async def get_reports(
    client_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_ANY_AUTH)] = None,
) -> list[ReportNarrativeOut]:
    return []


# ── Alert helpers ─────────────────────────────────────────────────────────────

from ...core.alert_evaluator import CORE_ALERT_TYPES as _CORE_ALERT_TYPES  # noqa: E402


def _alert_row_to_out(row: dict, slug_map: dict[str, str] | None = None) -> AlertOut:
    """Convert an `alerts` table row to AlertOut."""
    data = row.get("data") or {}
    client_uuid = row.get("client_id")
    client_slug: Optional[str] = None
    if slug_map and client_uuid:
        client_slug = slug_map.get(str(client_uuid))
    elif client_uuid:
        client_slug = str(client_uuid)

    raw_status = row.get("status", "OPEN").upper()
    try:
        status = AlertStatus(raw_status)
    except ValueError:
        status = AlertStatus.OPEN

    resolved_raw = row.get("resolved_at")
    resolved_at: Optional[datetime] = None
    if resolved_raw:
        try:
            resolved_at = datetime.fromisoformat(str(resolved_raw).replace("Z", "+00:00"))
        except ValueError:
            pass

    created_raw = row.get("created_at")
    if created_raw:
        try:
            detected_at = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
        except ValueError:
            detected_at = datetime.now(timezone.utc)
    else:
        detected_at = datetime.now(timezone.utc)

    return AlertOut(
        id=str(row.get("id", "")),
        client_id=client_slug,
        alert_type=row.get("type", "UNKNOWN"),
        severity=row.get("severity", "HIGH"),
        status=status,
        title=row.get("title", ""),
        message=row.get("message", ""),
        dedup_key=row.get("fingerprint", ""),
        source_key=data.get("source_key"),
        job_id=data.get("job_id"),
        evidence=data,
        occurrence_count=row.get("occurrence_count", 1) or 1,
        detected_at=detected_at,
        resolved_at=resolved_at,
    )


def _fetch_slug_map(rows: list[dict]) -> dict[str, str]:
    """Batch-resolve client UUIDs → slug strings."""
    uuids = {str(r["client_id"]) for r in rows if r.get("client_id")}
    if not uuids:
        return {}
    try:
        res = (
            _get_db().table("clients")
            .select("id, client_id")
            .in_("id", list(uuids))
            .execute()
        )
        return {str(r["id"]): r["client_id"] for r in (res.data or [])}
    except Exception:
        return {}


# ── GET /clients/{client_id}/alerts ───────────────────────────────────────────

@router.get(
    "/clients/{client_id}/alerts",
    summary="Core operational alerts for a specific client",
    response_model=list[AlertOut],
)
async def get_client_alerts(
    client_id: str,
    status: Optional[str] = Query("open", description="OPEN | ACKNOWLEDGED | RESOLVED"),
    limit: int = Query(50, ge=1, le=200),
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[AlertOut]:
    client = _load_client_meta(client_id)
    if not client:
        raise HTTPException(404, f"client not found: {client_id!r}")

    status_filter = (status or "OPEN").upper()
    db = _get_db()
    try:
        q = (
            db.table("alerts")
            .select("*")
            .eq("client_id", client["id"])
            .in_("type", list(_CORE_ALERT_TYPES))
            .order("created_at", desc=True)
            .limit(limit)
        )
        if status_filter in ("OPEN", "ACKNOWLEDGED", "RESOLVED"):
            q = q.eq("status", status_filter)
        rows = q.execute().data or []
    except Exception as exc:
        logger.error("agency_api: client alerts query failed %s: %s", client_id, exc)
        raise HTTPException(503, "database error")

    slug_map = {str(client["id"]): client_id}
    return [_alert_row_to_out(r, slug_map) for r in rows]


# ── GET /alerts ───────────────────────────────────────────────────────────────

@router.get(
    "/alerts",
    summary="Core operational alerts — all clients or filtered",
    response_model=list[AlertOut],
)
async def list_alerts(
    status: Optional[str] = Query(
        "open",
        description="Alert status filter: OPEN | ACKNOWLEDGED | RESOLVED",
    ),
    client_id: Optional[str] = Query(None, description="Filter by client slug"),
    limit: int = Query(50, ge=1, le=200),
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[AlertOut]:
    db = _get_db()
    status_filter = (status or "OPEN").upper()

    try:
        q = (
            db.table("alerts")
            .select("*")
            .in_("type", list(_CORE_ALERT_TYPES))
            .order("created_at", desc=True)
            .limit(limit)
        )
        if status_filter in ("OPEN", "ACKNOWLEDGED", "RESOLVED"):
            q = q.eq("status", status_filter)

        if client_id:
            meta = _load_client_meta(client_id)
            if meta:
                q = q.eq("client_id", meta["id"])
            else:
                return []

        rows = q.execute().data or []
    except Exception as exc:
        logger.error("agency_api: list_alerts failed: %s", exc)
        raise HTTPException(503, "database error")

    slug_map = _fetch_slug_map(rows)
    return [_alert_row_to_out(r, slug_map) for r in rows]


# ── GET /alerts/{alert_id}/context ───────────────────────────────────────────

@router.get(
    "/alerts/{alert_id}/context",
    summary="Full context package for a single alert (for Hermes diagnosis)",
    response_model=AlertContext,
)
async def get_alert_context(
    alert_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> AlertContext:
    db = _get_db()
    try:
        res = (
            db.table("alerts")
            .select("*")
            .eq("id", alert_id)
            .in_("type", list(_CORE_ALERT_TYPES))
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("agency_api: alert context query failed %s: %s", alert_id, exc)
        raise HTTPException(503, "database error")

    row = res.data[0] if (res and res.data) else None
    if not row:
        raise HTTPException(404, f"alert not found: {alert_id!r}")

    # resolve slug
    client_uuid = row.get("client_id")
    slug_map: dict[str, str] = {}
    if client_uuid:
        try:
            cr = db.table("clients").select("id, client_id").eq("id", client_uuid).limit(1).execute()
            if cr and cr.data:
                slug_map[str(cr.data[0]["id"])] = cr.data[0]["client_id"]
        except Exception:
            pass

    alert_out = _alert_row_to_out(row, slug_map)
    data = row.get("data") or {}

    recent_jobs: list[dict] = []
    if data.get("job_id"):
        try:
            jr = (
                db.table("core_job_runs")
                .select("*")
                .eq("id", data["job_id"])
                .limit(1)
                .execute()
            )
            if jr and jr.data:
                recent_jobs = [_job_row_to_out(jr.data[0]).model_dump(mode="json")]
        except Exception:
            pass
    elif client_uuid and slug_map.get(str(client_uuid)):
        client_slug = slug_map[str(client_uuid)]
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
            jr = (
                db.table("core_job_runs")
                .select("*")
                .like("run_key", f"core_pipeline:{client_slug}:%")
                .gte("started_at", cutoff)
                .order("started_at", desc=True)
                .limit(3)
                .execute()
            )
            recent_jobs = [_job_row_to_out(r).model_dump(mode="json") for r in (jr.data or [])]
        except Exception:
            pass

    return AlertContext(
        alert=alert_out,
        metrics=[],
        recent_jobs=recent_jobs,
        evidence=data,
    )


# ── GET /alert-rule-suggestions ───────────────────────────────────────────────

# CCR-014: no alert rule suggestions yet → honest empty list.
@router.get(
    "/alert-rule-suggestions",
    summary="Alert rule recalibration suggestions pending review",
    response_model=list[AlertRuleSuggestionOut],
)
async def list_alert_rule_suggestions(
    status: Optional[str] = Query("PENDING"),
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[AlertRuleSuggestionOut]:
    return []


# ── GET /system/health ────────────────────────────────────────────────────────

@router.get(
    "/system/health",
    summary="API, database, scheduler, queues and stuck jobs",
    response_model=SystemHealthOut,
)
async def system_health(
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> SystemHealthOut:
    from ...core.scheduler_registry import get_job_runs, get_scheduler

    now = datetime.now(timezone.utc)
    db = _get_db()

    # ── 1. DB connectivity ────────────────────────────────────────────────────
    db_status = ServiceStatus.UP
    try:
        db.table("core_job_runs").select("id").limit(1).execute()
    except Exception as exc:
        logger.error("system_health: DB probe failed: %s", exc)
        db_status = ServiceStatus.DOWN

    # ── 2. Scheduler status + scheduled jobs ─────────────────────────────────
    sch = get_scheduler()
    job_runs_cache = get_job_runs()
    scheduler_status = ServiceStatus.UP if (sch and sch.running) else ServiceStatus.DOWN

    scheduled_jobs: list[SchedulerJobStatus] = []
    if sch:
        for j in sch.get_jobs():
            cache = job_runs_cache.get(j.id, {})
            scheduled_jobs.append(SchedulerJobStatus(
                job_id=j.id,
                trigger=str(j.trigger),
                next_run_time=j.next_run_time,
                last_run_status=cache.get("last_status"),
                last_run_at=cache.get("last_run"),
            ))

    # ── 3. Job metrics from core_job_runs ─────────────────────────────────────
    running_jobs = 0
    queued_jobs = 0
    failed_jobs_last_24h = 0
    stuck_jobs: list[JobOut] = []
    last_successful_pipeline_run: Optional[datetime] = None
    last_pipeline_error: Optional[str] = None

    if db_status == ServiceStatus.UP:
        try:
            # RUNNING and QUEUED (for stuck detection — no time filter; these should be rare)
            active_res = (
                db.table("core_job_runs")
                .select("*")
                .in_("status", ["RUNNING", "QUEUED"])
                .order("started_at", desc=True)
                .execute()
            )
            for r in (active_res.data or []):
                st = r.get("status", "")
                started_at_str = r.get("started_at")

                if st == "RUNNING":
                    running_jobs += 1
                    threshold_min = _STUCK_RUNNING_MINUTES
                elif st == "QUEUED":
                    queued_jobs += 1
                    threshold_min = _STUCK_QUEUED_MINUTES
                else:
                    continue

                if started_at_str:
                    started = _parse_dt_safe(str(started_at_str))
                    if started:
                        if started.tzinfo is None:
                            started = started.replace(tzinfo=timezone.utc)
                        age_min = (now - started).total_seconds() / 60
                        if age_min > threshold_min:
                            stuck_jobs.append(_job_row_to_out(r))

            # FAILED in last 24h
            cutoff_24h = (now - timedelta(hours=24)).isoformat()
            failed_res = (
                db.table("core_job_runs")
                .select("id")
                .eq("status", "FAILED")
                .gte("started_at", cutoff_24h)
                .execute()
            )
            failed_jobs_last_24h = len(failed_res.data or [])

            # Last successful core_pipeline run
            ok_res = (
                db.table("core_job_runs")
                .select("finished_at")
                .eq("job_type", "core_pipeline")
                .eq("status", "SUCCEEDED")
                .order("finished_at", desc=True)
                .limit(1)
                .execute()
            )
            if ok_res.data:
                last_successful_pipeline_run = _parse_dt_safe(ok_res.data[0].get("finished_at"))

            # Last core_pipeline error
            err_res = (
                db.table("core_job_runs")
                .select("error")
                .eq("job_type", "core_pipeline")
                .eq("status", "FAILED")
                .order("finished_at", desc=True)
                .limit(1)
                .execute()
            )
            if err_res.data:
                last_pipeline_error = err_res.data[0].get("error")

        except Exception as exc:
            logger.error("system_health: job metrics query failed: %s", exc)

    # ── 4. Overall status ─────────────────────────────────────────────────────
    if db_status == ServiceStatus.DOWN or scheduler_status == ServiceStatus.DOWN:
        overall_status = ServiceStatus.DOWN
    elif stuck_jobs or failed_jobs_last_24h > 0:
        overall_status = ServiceStatus.DEGRADED
    else:
        overall_status = ServiceStatus.UP

    return SystemHealthOut(
        overall_status=overall_status,
        api="UP",
        database=db_status,
        scheduler=scheduler_status,
        scheduled_jobs=scheduled_jobs,
        workers=[],  # APScheduler is the worker; no separate worker processes
        running_jobs=running_jobs,
        queued_jobs=queued_jobs,
        failed_jobs_last_24h=failed_jobs_last_24h,
        stuck_jobs=stuck_jobs,
        dead_jobs=stuck_jobs,  # backward compat alias
        last_successful_pipeline_run=last_successful_pipeline_run,
        last_pipeline_error=last_pipeline_error,
        checked_at=now,
    )


# ── Jobs helpers ──────────────────────────────────────────────────────────────

def _job_row_to_out(row: dict) -> JobOut:
    def _dt(v) -> Optional[datetime]:
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        try:
            return datetime.fromisoformat(str(v))
        except ValueError:
            return None

    return JobOut(
        id=str(row["id"]),
        job_type=row.get("job_type") or "core_pipeline",
        run_key=row.get("run_key") or "",
        status=JobStatus(row.get("status", "RUNNING")),
        attempt=row.get("attempt") or 1,
        next_retry_at=_dt(row.get("next_retry_at")),
        started_at=_dt(row.get("started_at")),
        finished_at=_dt(row.get("finished_at")),
        error=row.get("error"),
        replay_of=str(row["replay_of"]) if row.get("replay_of") else None,
    )


# ── GET /jobs ─────────────────────────────────────────────────────────────────

@router.get(
    "/jobs",
    summary="List recent job executions",
    response_model=list[JobOut],
)
async def list_jobs(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _auth: Annotated[AuthContext, Depends(SCOPE_ADMIN_ONLY)] = None,
) -> list[JobOut]:
    try:
        q = _get_db().table("core_job_runs").select("*").order("started_at", desc=True).limit(limit)
        if status:
            q = q.eq("status", status.upper())
        rows = q.execute().data or []
    except Exception as exc:
        logger.error("list_jobs: DB error: %s", exc)
        raise HTTPException(503, detail="database unavailable")
    return [_job_row_to_out(r) for r in rows]


# ── GET /jobs/{job_id} ────────────────────────────────────────────────────────

@router.get(
    "/jobs/{job_id}",
    summary="Single job execution detail",
    response_model=JobOut,
)
async def get_job(
    job_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_ADMIN_ONLY)] = None,
) -> JobOut:
    try:
        res = _get_db().table("core_job_runs").select("*").eq("id", job_id).limit(1).execute()
        row = res.data[0] if (res and res.data) else None
    except Exception as exc:
        logger.error("get_job: DB error: %s", exc)
        raise HTTPException(503, detail="database unavailable")
    if not row:
        raise HTTPException(404, detail=f"job not found: {job_id!r}")
    return _job_row_to_out(row)


# ── CCR-011: consultable intel chain ─────────────────────────────────────────
#
# Four additive GET routes that expose the FK chain:
#   alert → core_diagnoses.alert_id
#         → core_recommendations.diagnosis_id
#
# No relationship is inferred — FK chain only. Real DB reads in Etapa 4.

# CCR-014: core_diagnoses has 0 rows → honest empty list.
@router.get(
    "/alerts/{alert_id}/diagnoses",
    summary="Diagnoses linked to an alert (FK: core_diagnoses.alert_id)",
    response_model=list[DiagnosisOut],
)
async def get_alert_diagnoses(
    alert_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[DiagnosisOut]:
    return []


# CCR-014: core_diagnoses has 0 rows → 404 for any diagnosis_id.
@router.get(
    "/diagnoses/{diagnosis_id}",
    summary="Single diagnosis by ID",
    response_model=DiagnosisOut,
)
async def get_diagnosis(
    diagnosis_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> DiagnosisOut:
    raise HTTPException(404, f"diagnosis not found: {diagnosis_id!r}")


# CCR-014: core_recommendations has 0 rows → honest empty list.
@router.get(
    "/diagnoses/{diagnosis_id}/recommendations",
    summary="Recommendations linked to a diagnosis (FK: core_recommendations.diagnosis_id)",
    response_model=list[RecommendationOut],
)
async def get_diagnosis_recommendations(
    diagnosis_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[RecommendationOut]:
    return []


# CCR-014: core_recommendations has 0 rows → 404 for any recommendation_id.
@router.get(
    "/recommendations/{recommendation_id}",
    summary="Single recommendation by ID",
    response_model=RecommendationOut,
)
async def get_recommendation(
    recommendation_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> RecommendationOut:
    raise HTTPException(404, f"recommendation not found: {recommendation_id!r}")


# ── POST /diagnoses ───────────────────────────────────────────────────────────

@router.post(
    "/diagnoses",
    summary="Create a structured diagnosis linked to an alert (Hermes)",
    response_model=DiagnosisOut,
    status_code=201,
)
async def create_diagnosis(
    body: DiagnosisCreate,
    idempotency_key: Optional[str] = Header(default=None),
    _auth: Annotated[AuthContext, Depends(SCOPE_HERMES_WRITE)] = None,
) -> DiagnosisOut:
    return DiagnosisOut(**body.model_dump(), id="dia_stub_01", created_at=_STUB_NOW)


# ── POST /recommendations ─────────────────────────────────────────────────────

@router.post(
    "/recommendations",
    summary="Create a recommendation linked to a diagnosis (Hermes)",
    response_model=RecommendationOut,
    status_code=201,
)
async def create_recommendation(
    body: RecommendationCreate,
    idempotency_key: Optional[str] = Header(default=None),
    _auth: Annotated[AuthContext, Depends(SCOPE_HERMES_WRITE)] = None,
) -> RecommendationOut:
    return RecommendationOut(**body.model_dump(), id="rec_stub_01", created_at=_STUB_NOW)


# ── POST /changes ─────────────────────────────────────────────────────────────

@router.post(
    "/changes",
    summary="Record a HUMAN change log entry (Hermes via Telegram)",
    response_model=ChangeOut,
    status_code=201,
)
async def create_change(
    body: ChangeCreate,
    idempotency_key: Optional[str] = Header(default=None),
    _auth: Annotated[AuthContext, Depends(SCOPE_HERMES_WRITE)] = None,
) -> ChangeOut:
    return ChangeOut(**body.model_dump(), id="chg_stub_new", created_at=_STUB_NOW)


# ── POST /action-events ───────────────────────────────────────────────────────

@router.post(
    "/action-events",
    summary="Record a decision event (approved, ignored, executed, etc.)",
    response_model=ActionEventOut,
    status_code=201,
)
async def create_action_event(
    body: ActionEventCreate,
    idempotency_key: Optional[str] = Header(default=None),
    _auth: Annotated[AuthContext, Depends(require_scopes("agency_admin", "hermes_service", "platform_web"))] = None,
) -> ActionEventOut:
    return ActionEventOut(**body.model_dump(), id="evt_stub_01", created_at=_STUB_NOW)


# ── POST /alert-feedback ──────────────────────────────────────────────────────

@router.post(
    "/alert-feedback",
    summary="Mark an alert as useful or noise (Hermes)",
    status_code=204,
)
async def create_alert_feedback(
    body: AlertFeedbackCreate,
    idempotency_key: Optional[str] = Header(default=None),
    _auth: Annotated[AuthContext, Depends(SCOPE_HERMES_WRITE)] = None,
) -> None:
    return None


# ── POST /report-narratives ───────────────────────────────────────────────────

@router.post(
    "/report-narratives",
    summary="Create a narrative (DRAFT) referencing a report contract (Hermes)",
    response_model=ReportNarrativeOut,
    status_code=201,
)
async def create_report_narrative(
    body: ReportNarrativeCreate,
    idempotency_key: Optional[str] = Header(default=None),
    _auth: Annotated[AuthContext, Depends(SCOPE_HERMES_WRITE)] = None,
) -> ReportNarrativeOut:
    return ReportNarrativeOut(**body.model_dump(), id="nar_stub_new", created_at=_STUB_NOW)


# ── POST /report-narratives/{narrative_id}/transitions ───────────────────────

@router.post(
    "/report-narratives/{narrative_id}/transitions",
    summary="Transition narrative status (READY_FOR_REVIEW → APPROVED → PUBLISHED)",
    response_model=ReportNarrativeOut,
)
async def transition_narrative(
    narrative_id: str,
    body: NarrativeTransition,
    idempotency_key: Optional[str] = Header(default=None, alias="idempotency-key"),
    _auth: Annotated[AuthContext, Depends(require_scopes("agency_admin", "hermes_service", "platform_web"))] = None,
) -> ReportNarrativeOut:
    return ReportNarrativeOut(
        id=narrative_id,
        report_contract_id="rpc_stub_01",
        blocks=[],
        visibility_scope=VisibilityScope.AGENCY_ONLY,
        status=body.target_status,
        created_at=_STUB_NOW,
    )


# ── POST /alert-rule-suggestions/{suggestion_id}/decision ────────────────────
#
# CCR-001: hermes_service ADDED — acts as transport for a human decision made in
#   Telegram. The `actor` field in the body MUST contain the human actor_id, never
#   the service identity. Enforcement of this invariant is a Core concern (Etapa 4).
#   audit_log must record both: auth_scope=hermes_service AND actor=<human_id>.
#
# Governance: Hermes CANNOT decide autonomously. If it did, the audit trail would
#   expose it (actor would be a service name). No breaking schema change now;
#   `approval_event_id` field planned for v1.2 to reference the canonical audit event.

@router.post(
    "/alert-rule-suggestions/{suggestion_id}/decision",
    summary="Approve or reject a rule recalibration suggestion",
    response_model=AlertRuleSuggestionOut,
)
async def decide_alert_rule_suggestion(
    suggestion_id: str,
    body: AlertRuleSuggestionDecision,
    idempotency_key: Optional[str] = Header(default=None, alias="idempotency-key"),
    _auth: Annotated[AuthContext, Depends(require_scopes("agency_admin", "hermes_service", "platform_web"))] = None,
) -> AlertRuleSuggestionOut:
    new_status = (
        AlertRuleSuggestionStatus.APPROVED
        if body.decision == "APPROVED"
        else AlertRuleSuggestionStatus.REJECTED
    )
    return AlertRuleSuggestionOut(
        id=suggestion_id,
        client_id="lk-sneakers",
        rule_key="cpa_increase",
        current_version="v1",
        proposed_params={},
        evidence=[],
        status=new_status,
        created_at=_STUB_NOW,
    )


# ── POST /learning-candidates ─────────────────────────────────────────────────

@router.post(
    "/learning-candidates",
    summary="Propose a learning candidate (Hermes)",
    response_model=LearningCandidateOut,
    status_code=201,
)
async def create_learning_candidate(
    body: LearningCandidateCreate,
    idempotency_key: Optional[str] = Header(default=None),
    _auth: Annotated[AuthContext, Depends(SCOPE_HERMES_WRITE)] = None,
) -> LearningCandidateOut:
    return LearningCandidateOut(**body.model_dump(), id="lrn_stub_01", created_at=_STUB_NOW)


# ── POST /jobs/{job_id}/replay ────────────────────────────────────────────────
#
# CCR-001: platform_web ADDED — platform operators may trigger replay from the
#   dashboard. hermes_service remains DENIED: Hermes alerts and recommends but does
#   not execute operational retries directly.
#
# audit_log gap (Etapa 4): when called via platform_web, the auth_scope is
#   "platform_web" but the individual human user is not yet available in this
#   endpoint (no session/actor parameter). The implementation MUST record:
#     auth_scope = platform_web | agency_admin
#     actor_id   = dashboard user (available once session auth is wired, Etapa 4)
#     job_replay_of = job_id
#   Until then, actor_id is the scope string — document the gap, do not invent identity.

@router.post(
    "/jobs/{job_id}/replay",
    summary="Re-execute a failed or DEAD job",
    response_model=JobOut,
)
async def replay_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    _auth: Annotated[AuthContext, Depends(require_scopes("agency_admin", "platform_web"))] = None,
) -> JobOut:
    # Fetch original job
    try:
        res = _get_db().table("core_job_runs").select("*").eq("id", job_id).limit(1).execute()
        original = res.data[0] if (res and res.data) else None
    except Exception as exc:
        logger.error("replay_job: DB error looking up %s: %s", job_id, exc)
        raise HTTPException(503, detail="database unavailable")
    if not original:
        raise HTTPException(404, detail=f"job not found: {job_id!r}")

    # Parse run_key: core_pipeline:{client_id}:{period_end}
    run_key = original.get("run_key", "")
    parts = run_key.split(":")
    if len(parts) != 3 or parts[0] != "core_pipeline":
        raise HTTPException(422, detail=f"run_key format not replayable: {run_key!r}")
    client_id = parts[1]
    try:
        from datetime import date as _date
        period_end = _date.fromisoformat(parts[2])
    except ValueError:
        raise HTTPException(422, detail=f"run_key has invalid period_end: {parts[2]!r}")
    period_start = period_end - timedelta(days=6)

    # Insert QUEUED replay record
    from ...core.core_scheduler import _insert_queued_replay, run_core_pipeline_for_client
    new_job_id = _insert_queued_replay(_get_db(), client_id, period_end, replay_of=job_id)

    # Schedule background execution
    background_tasks.add_task(run_core_pipeline_for_client, new_job_id, client_id, period_start, period_end)

    logger.info("replay_job: queued replay %s → new job %s for %s", job_id, new_job_id, client_id)
    return JobOut(
        id=new_job_id,
        job_type="core_pipeline",
        run_key=f"core_pipeline:{client_id}:{period_end.isoformat()}",
        status=JobStatus.QUEUED,
        attempt=1,
        replay_of=job_id,
    )
