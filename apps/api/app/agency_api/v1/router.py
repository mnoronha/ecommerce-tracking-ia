"""
Agency API v1 router — stub implementation (Etapa 1).

Every endpoint returns a valid example response that matches its schema.
Real implementation replaces stubs incrementally from Etapa 4 onwards.

Auth: each route declares the allowed scopes via Depends(require_scopes(...)).
Idempotency-Key header is accepted on all write endpoints (ignored in stubs).

Route inventory (46 routes):
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
  GET  /alerts/{alert_id}/diagnoses
  GET  /diagnoses/{diagnosis_id}
  GET  /diagnoses/{diagnosis_id}/recommendations
  GET  /recommendations/{recommendation_id}
  GET  /alert-rule-suggestions
  GET  /system/health
  GET  /jobs
  GET  /jobs/{job_id}
  POST /alerts/{alert_id}/diagnoses        [Hermes write]
  POST /diagnoses/{diagnosis_id}/recommendations  [Hermes write]
  POST /changes
  POST /action-events
  POST /alert-feedback
  POST /report-narratives
  POST /report-narratives/{narrative_id}/transitions
  POST /clients/{client_id}/monthly-review/replay
  POST /alert-rule-suggestions/{suggestion_id}/decision
  POST /recommendations/{recommendation_id}/decision  [human-only, PLATFORM_WRITE]
  POST /learning-candidates
  POST /jobs/{job_id}/replay
  GET  /clients/{client_id}/entity-performance
  GET  /clients/{client_id}/data-source/{source_key}
  GET  /clients/{client_id}/meta/insights         [READ-ONLY diagnostic, SCOPE_READ_ANY]
  GET  /dashboard
  GET  /clients/{client_id}/action-events
  POST /clients/{client_id}/pipeline/trigger
  POST /clients/{client_id}/weekly-review/replay
  GET  /clients/{client_id}/truth/targets
  PUT  /clients/{client_id}/truth/targets  [human-only, PLATFORM_WRITE]
  POST /clients/{client_id}/monthly-review/replay
  GET  /clients/{client_id}/balance           [prepaid ad account balance]
  POST /clients/{client_id}/balance/trigger   [on-demand check, PLATFORM_WRITE]
"""

from __future__ import annotations

import logging
import uuid as _uuid_mod
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
    SCOPE_WRITE_ANY,
    AuthContext,
    require_scopes,
)
from .schemas import (
    ActionEventCreate,
    ActionEventOut,
    ActionEventType,
    BalanceSnapshotOut,
    BalanceTriggerOut,
    ClientBalanceOut,
    EnvVarStatus,
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
    DiagnosisStatus,
    EntityPerformanceOut,
    EvidenceLevel,
    EvidenceRefs,
    HypothesisType,
    JobOut,
    JobStatus,
    LearningCandidateCreate,
    LearningCandidateOut,
    LearningCandidateStatus,
    LearningScope,
    Level,
    MatchStatus,
    MetaInsightRow,
    MetaInsightsOut,
    MetricContract,
    MetricRef,
    MetricValue,
    NarrativeBlock,
    NarrativeStatus,
    NarrativeTransition,
    OutcomeCreate,
    OutcomeOut,
    Period,
    PipelineHealthOut,
    RecommendationCreate,
    RecommendationDecision,
    RecommendationOut,
    RecommendationStatus,
    ReportNarrativeCreate,
    ReportNarrativeOut,
    ReportType,
    RootCauseHypothesis,
    SchedulerJobStatus,
    ServiceStatus,
    SourceState,
    SystemHealthOut,
    NotificationsConfigOut,
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

def _change_row_to_out(row: dict) -> ChangeOut:
    try:
        change_type = ChangeType(row.get("change_type", "OTHER"))
    except ValueError:
        change_type = ChangeType.OTHER
    try:
        source = ChangeLogSource(row.get("source", "HUMAN"))
    except ValueError:
        source = ChangeLogSource.HUMAN
    try:
        confidence = ChangeConfidence(row.get("confidence", "CONFIRMED"))
    except ValueError:
        confidence = ChangeConfidence.CONFIRMED
    match_status = None
    if row.get("match_status"):
        try:
            match_status = MatchStatus(row["match_status"])
        except ValueError:
            pass
    return ChangeOut(
        client_id=str(row.get("client_id", "")),
        occurred_at=_parse_ts_utc(row.get("occurred_at")) or datetime.now(timezone.utc),
        channel=row.get("channel", ""),
        platform_account_id=row.get("platform_account_id", ""),
        entity_type=row.get("entity_type", ""),
        entity_name_at_time=row.get("entity_name_at_time", ""),
        change_type=change_type,
        before=row.get("before_state"),
        after=row.get("after_state"),
        reason=row.get("reason"),
        source=source,
        reported_by=row.get("reported_by"),
        confidence=confidence,
        linked_action_id=str(row["linked_action_id"]) if row.get("linked_action_id") else None,
        campaign_id=row.get("campaign_id"),
        adset_or_adgroup_id=row.get("adset_or_adgroup_id"),
        ad_id=row.get("ad_id"),
        id=str(row["id"]),
        external_change_id=row.get("external_change_id"),
        match_status=match_status,
        matched_change_id=str(row["matched_change_id"]) if row.get("matched_change_id") else None,
        created_at=_parse_ts_utc(row.get("created_at")) or datetime.now(timezone.utc),
    )


@router.get(
    "/clients/{client_id}/changes",
    summary="Change log for a client",
    response_model=list[ChangeOut],
)
async def get_changes(
    client_id: str,
    since: Optional[str] = Query(None, description="ISO-8601 datetime filter (occurred_at >=)"),
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[ChangeOut]:
    db = _get_db()
    try:
        q = (
            db.table("core_change_log")
            .select("*")
            .eq("client_id", client_id)
            .order("occurred_at", desc=True)
            .limit(100)
        )
        if since:
            q = q.gte("occurred_at", since)
        res = q.execute()
    except Exception as exc:
        logger.error("get_changes: DB error for %s: %s", client_id, exc)
        raise HTTPException(503, "database error")
    return [_change_row_to_out(r) for r in (res.data or [])]


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

_VALID_TRANSITIONS: dict[NarrativeStatus, set[NarrativeStatus]] = {
    NarrativeStatus.DRAFT:            {NarrativeStatus.READY_FOR_REVIEW},
    NarrativeStatus.READY_FOR_REVIEW: {NarrativeStatus.APPROVED, NarrativeStatus.DRAFT},
    NarrativeStatus.APPROVED:         {NarrativeStatus.PUBLISHED, NarrativeStatus.DRAFT},
    NarrativeStatus.PUBLISHED:        set(),
    NarrativeStatus.SUPERSEDED:       set(),
}


def _narrative_row_to_out(
    row: dict,
    contract: dict | None = None,
    truth_versions: "TruthVersions | None" = None,
) -> ReportNarrativeOut:
    raw_status = row.get("status", "DRAFT")
    try:
        status = NarrativeStatus(raw_status)
    except ValueError:
        status = NarrativeStatus.DRAFT

    period_start = None
    period_end = None
    report_type = None
    if contract:
        try:
            period_start = date.fromisoformat(str(contract["period_start"])) if contract.get("period_start") else None
            period_end   = date.fromisoformat(str(contract["period_end"])) if contract.get("period_end") else None
        except (ValueError, KeyError):
            pass
        try:
            report_type = ReportType((contract.get("report_type") or "weekly").upper())
        except ValueError:
            pass

    return ReportNarrativeOut(
        id=str(row["id"]),
        report_contract_id=str(row["report_contract_id"]),
        blocks=row.get("blocks") or [],
        visibility_scope=row.get("visibility_scope", "AGENCY_ONLY"),
        status=status,
        approved_by=row.get("approved_by"),
        approved_at=_parse_ts_utc(row.get("approved_at")),
        published_at=_parse_ts_utc(row.get("published_at")),
        created_at=_parse_ts_utc(row.get("created_at")) or datetime.now(timezone.utc),
        client_id=contract.get("client_slug") if contract else None,
        report_type=report_type,
        period_start=period_start,
        period_end=period_end,
        truth_versions=truth_versions,
    )


@router.get(
    "/clients/{client_id}/reports",
    summary="Report narratives; client_viewer sees only PUBLISHED",
    response_model=list[ReportNarrativeOut],
)
async def get_reports(
    client_id: str,
    status: Optional[str] = Query(None, description="Filter by lifecycle status"),
    report_type: Optional[str] = Query(None, pattern="^(WEEKLY|MONTHLY)$"),
    _auth: Annotated[AuthContext, Depends(SCOPE_ANY_AUTH)] = None,
) -> list[ReportNarrativeOut]:
    db = _get_db()
    try:
        # Step 1: contracts for this client
        cq = (
            db.table("agency_report_contracts")
            .select("id, client_slug, report_type, period_start, period_end")
            .eq("client_slug", client_id)
        )
        if report_type:
            cq = cq.eq("report_type", report_type.lower())
        contracts_res = cq.execute()
    except Exception as exc:
        logger.error("get_reports: contracts DB error for %s: %s", client_id, exc)
        raise HTTPException(503, "database error")

    if not contracts_res.data:
        return []

    contract_ids = [str(r["id"]) for r in contracts_res.data]
    contract_meta = {str(r["id"]): r for r in contracts_res.data}

    try:
        nq = (
            db.table("core_report_narratives")
            .select("*")
            .in_("report_contract_id", contract_ids)
            .order("created_at", desc=True)
        )
        # client_viewer sees only PUBLISHED; agency_admin sees all or filtered
        if _auth and _auth.scope == "client_viewer":
            nq = nq.eq("status", "PUBLISHED")
        elif status:
            nq = nq.eq("status", status.upper())
        narratives_res = nq.execute()
    except Exception as exc:
        logger.error("get_reports: narratives DB error for %s: %s", client_id, exc)
        raise HTTPException(503, "database error")

    truth_versions = _load_truth_versions(client_id)
    return [
        _narrative_row_to_out(r, contract_meta.get(str(r["report_contract_id"])), truth_versions)
        for r in (narratives_res.data or [])
    ]


@router.get(
    "/report-narratives/{narrative_id}",
    summary="Single report narrative by ID",
    response_model=ReportNarrativeOut,
)
async def get_report_narrative(
    narrative_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_ANY_AUTH)] = None,
) -> ReportNarrativeOut:
    if not _is_valid_uuid(narrative_id):
        raise HTTPException(404, f"narrative not found: {narrative_id!r}")
    db = _get_db()
    try:
        res = (
            db.table("core_report_narratives")
            .select("*")
            .eq("id", narrative_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("get_report_narrative: DB error %s: %s", narrative_id, exc)
        raise HTTPException(503, "database error")
    row = res.data[0] if (res and res.data) else None
    if not row:
        raise HTTPException(404, f"narrative not found: {narrative_id!r}")

    if _auth and _auth.scope == "client_viewer" and row.get("status") != "PUBLISHED":
        raise HTTPException(404, "narrative not found")

    # Fetch contract context
    contract: dict | None = None
    try:
        cr = (
            db.table("agency_report_contracts")
            .select("id, client_slug, report_type, period_start, period_end")
            .eq("id", str(row["report_contract_id"]))
            .limit(1)
            .execute()
        )
        contract = cr.data[0] if (cr and cr.data) else None
    except Exception:
        pass

    truth_versions = _load_truth_versions(contract["client_slug"]) if contract else None
    return _narrative_row_to_out(row, contract, truth_versions)


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


def _diagnosis_row_to_out(row: dict) -> DiagnosisOut:
    """Convert a core_diagnoses row to DiagnosisOut."""
    return DiagnosisOut(
        id=str(row["id"]),
        alert_id=str(row["alert_id"]),
        client_id=str(row["client_id"]) if row.get("client_id") else None,
        status=row.get("status", "DRAFT"),
        summary=row.get("summary", ""),
        root_cause_hypotheses=row.get("root_cause_hypotheses") or [],
        evidence=row.get("evidence") or {},
        confidence=row.get("confidence"),
        limitations=row.get("limitations"),
        fingerprint=row.get("fingerprint"),
        created_at=row["created_at"],
        created_by=row.get("created_by", "hermes"),
    )


def _recommendation_row_to_out(row: dict) -> RecommendationOut:
    """Convert a core_recommendations row to RecommendationOut."""
    return RecommendationOut(
        id=str(row["id"]),
        diagnosis_id=str(row["diagnosis_id"]),
        alert_id=str(row["alert_id"]),
        client_id=str(row["client_id"]) if row.get("client_id") else None,
        title=row.get("title", ""),
        action=row.get("action", ""),
        rationale=row.get("rationale", ""),
        priority=row.get("priority", "MEDIUM"),
        risk=row.get("risk"),
        expected_impact=row.get("expected_impact"),
        requires_human_approval=row.get("requires_human_approval", True),
        status=row.get("status", "PROPOSED"),
        fingerprint=row.get("fingerprint"),
        created_at=row["created_at"],
        created_by=row.get("created_by", "hermes"),
    )


def _is_valid_uuid(value: str) -> bool:
    try:
        _uuid_mod.UUID(value)
        return True
    except ValueError:
        return False


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


# ── GET /dashboard ───────────────────────────────────────────────────────────

@router.get(
    "/dashboard",
    summary="Agency dashboard — per-client aggregates via get_agency_dashboard RPC",
    response_model=list[dict],
)
async def get_agency_dashboard_summary(
    agency_id: str = Query(..., description="Agency UUID"),
    days: int = Query(30, ge=1, le=90),
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[dict]:
    try:
        res = _get_db().rpc("get_agency_dashboard", {
            "p_agency_id": agency_id,
            "p_days": days,
        }).execute()
        return res.data or []
    except Exception as exc:
        logger.error("get_agency_dashboard_summary: RPC error: %s", exc)
        raise HTTPException(503, "database unavailable")


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


# ── GET /system/notifications-config ─────────────────────────────────────────

@router.get(
    "/system/notifications-config",
    summary="Telegram env var presence/length — never exposes secret values",
    response_model=NotificationsConfigOut,
)
async def system_notifications_config(
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> NotificationsConfigOut:
    import os
    from ...config import settings

    bot_token = settings.TELEGRAM_BOT_TOKEN or ""
    chat_id   = settings.TELEGRAM_CHAT_ID or ""

    return NotificationsConfigOut(
        railway_service=os.environ.get("RAILWAY_SERVICE_NAME") or os.environ.get("RAILWAY_SERVICE_ID"),
        telegram_bot_token=EnvVarStatus(present=bool(bot_token), length=len(bot_token)),
        telegram_chat_id=EnvVarStatus(present=bool(chat_id), length=len(chat_id)),
        checked_at=datetime.now(timezone.utc),
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


# ── Intel chain: GET endpoints (real DB reads) ────────────────────────────────

@router.get(
    "/alerts/{alert_id}/diagnoses",
    summary="Diagnoses linked to an alert (FK: core_diagnoses.alert_id)",
    response_model=list[DiagnosisOut],
)
async def get_alert_diagnoses(
    alert_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[DiagnosisOut]:
    if not _is_valid_uuid(alert_id):
        return []
    db = _get_db()
    try:
        res = (
            db.table("core_diagnoses")
            .select("*")
            .eq("alert_id", alert_id)
            .order("created_at", desc=True)
            .execute()
        )
        return [_diagnosis_row_to_out(r) for r in (res.data or [])]
    except Exception as exc:
        logger.error("get_alert_diagnoses: DB error %s: %s", alert_id, exc)
        raise HTTPException(503, "database error")


@router.get(
    "/diagnoses/{diagnosis_id}",
    summary="Single diagnosis by ID",
    response_model=DiagnosisOut,
)
async def get_diagnosis(
    diagnosis_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> DiagnosisOut:
    if not _is_valid_uuid(diagnosis_id):
        raise HTTPException(404, f"diagnosis not found: {diagnosis_id!r}")
    db = _get_db()
    try:
        res = (
            db.table("core_diagnoses")
            .select("*")
            .eq("id", diagnosis_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("get_diagnosis: DB error %s: %s", diagnosis_id, exc)
        raise HTTPException(503, "database error")
    row = res.data[0] if (res and res.data) else None
    if not row:
        raise HTTPException(404, f"diagnosis not found: {diagnosis_id!r}")
    return _diagnosis_row_to_out(row)


@router.get(
    "/diagnoses/{diagnosis_id}/recommendations",
    summary="Recommendations linked to a diagnosis (FK: core_recommendations.diagnosis_id)",
    response_model=list[RecommendationOut],
)
async def get_diagnosis_recommendations(
    diagnosis_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[RecommendationOut]:
    if not _is_valid_uuid(diagnosis_id):
        return []
    db = _get_db()
    try:
        res = (
            db.table("core_recommendations")
            .select("*")
            .eq("diagnosis_id", diagnosis_id)
            .order("created_at", desc=True)
            .execute()
        )
        return [_recommendation_row_to_out(r) for r in (res.data or [])]
    except Exception as exc:
        logger.error("get_diagnosis_recommendations: DB error %s: %s", diagnosis_id, exc)
        raise HTTPException(503, "database error")


@router.get(
    "/recommendations/{recommendation_id}",
    summary="Single recommendation by ID",
    response_model=RecommendationOut,
)
async def get_recommendation(
    recommendation_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> RecommendationOut:
    if not _is_valid_uuid(recommendation_id):
        raise HTTPException(404, f"recommendation not found: {recommendation_id!r}")
    db = _get_db()
    try:
        res = (
            db.table("core_recommendations")
            .select("*")
            .eq("id", recommendation_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("get_recommendation: DB error %s: %s", recommendation_id, exc)
        raise HTTPException(503, "database error")
    row = res.data[0] if (res and res.data) else None
    if not row:
        raise HTTPException(404, f"recommendation not found: {recommendation_id!r}")
    return _recommendation_row_to_out(row)


# ── POST /recommendations/{recommendation_id}/decision ───────────────────────
#
# Human Decision Contract — Core invariants:
#   1. Scope SCOPE_PLATFORM_WRITE: agency_admin + platform_web only.
#      hermes_service is DENIED at the auth layer — Hermes cannot approve autonomously.
#   2. Decision is always human: `actor` in body MUST be a human identifier.
#   3. DEFERRED ≠ ACCEPTED: DEFERRED does not authorise any external write or Change.
#   4. Idempotency via Idempotency-Key header: fingerprint stored in decision_fingerprint.
#   5. Audit trail: every decision writes a core_action_events row with actor + timestamp.
#   6. Terminal states (APPROVED, REJECTED, EXECUTED) block further decisions (422).

_VALID_DECISION_SOURCES: frozenset[str] = frozenset({"PROPOSED", "DEFERRED"})

_DECISION_TO_STATUS: dict[str, str] = {
    "ACCEPTED": "APPROVED",
    "REJECTED": "REJECTED",
    "DEFERRED": "DEFERRED",
}

_DECISION_TO_EVENT: dict[str, str] = {
    "ACCEPTED": "APPROVED",
    "REJECTED": "REJECTED",
    "DEFERRED": "DEFERRED",
}


@router.post(
    "/recommendations/{recommendation_id}/decision",
    summary="Record a human decision on a recommendation (ACCEPTED / REJECTED / DEFERRED)",
    response_model=RecommendationOut,
)
async def decide_recommendation(
    recommendation_id: str,
    body: RecommendationDecision,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    _auth: Annotated[AuthContext, Depends(SCOPE_PLATFORM_WRITE)] = None,
) -> RecommendationOut:
    if not _is_valid_uuid(recommendation_id):
        raise HTTPException(404, f"recommendation not found: {recommendation_id!r}")
    db = _get_db()

    # Load current recommendation
    try:
        res = (
            db.table("core_recommendations")
            .select("*")
            .eq("id", recommendation_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("decide_recommendation: fetch failed %s: %s", recommendation_id, exc)
        raise HTTPException(503, "database error")
    row = res.data[0] if (res and res.data) else None
    if not row:
        raise HTTPException(404, f"recommendation not found: {recommendation_id!r}")

    # Idempotency: if same Idempotency-Key already stored, return current row unchanged
    fingerprint: Optional[str] = None
    if idempotency_key:
        fingerprint = f"dec:{recommendation_id}:{idempotency_key}"
        if row.get("decision_fingerprint") == fingerprint:
            return _recommendation_row_to_out(row)

    # State machine: only PROPOSED and DEFERRED accept a new decision
    current_status = row.get("status", "PROPOSED")
    if current_status not in _VALID_DECISION_SOURCES:
        raise HTTPException(
            422,
            f"recommendation {recommendation_id!r} is in terminal state {current_status!r}; "
            "decisions can only be made on PROPOSED or DEFERRED recommendations",
        )

    decision   = body.decision
    new_status = _DECISION_TO_STATUS[decision]
    event_type = _DECISION_TO_EVENT[decision]
    now_utc    = datetime.now(timezone.utc)

    # Persist status change (+ idempotency fingerprint if key supplied)
    update_payload: dict = {"status": new_status, "updated_at": now_utc.isoformat()}
    if fingerprint:
        update_payload["decision_fingerprint"] = fingerprint
    try:
        upd = (
            db.table("core_recommendations")
            .update(update_payload)
            .eq("id", recommendation_id)
            .execute()
        )
    except Exception as exc:
        logger.error("decide_recommendation: update failed %s: %s", recommendation_id, exc)
        raise HTTPException(503, "database error")
    updated_row = (upd.data[0] if (upd and upd.data) else None) or {**row, **update_payload}

    # Audit trail: insert action event (non-fatal on failure — rec already updated)
    try:
        db.table("core_action_events").insert({
            "recommendation_id": recommendation_id,
            "event_type":        event_type,
            "actor":             body.actor,
            "occurred_at":       now_utc.isoformat(),
            "note":              body.reason,
        }).execute()
    except Exception as exc:
        logger.warning(
            "decide_recommendation: audit event insert failed (status already updated) %s: %s",
            recommendation_id, exc,
        )

    logger.info(
        "decide_recommendation: %s %s→%s actor=%s scope=%s",
        recommendation_id, current_status, new_status, body.actor,
        getattr(_auth, "scope", "unknown") if _auth else "unknown",
    )
    return _recommendation_row_to_out(updated_row)


# ── Intel chain: POST endpoints (Hermes write) ────────────────────────────────

@router.post(
    "/alerts/{alert_id}/diagnoses",
    summary="Create a structured diagnosis for an alert (Hermes write)",
    response_model=DiagnosisOut,
    status_code=201,
)
async def create_diagnosis_for_alert(
    alert_id: str,
    body: DiagnosisCreate,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    _auth: Annotated[AuthContext, Depends(SCOPE_HERMES_WRITE)] = None,
) -> DiagnosisOut:
    db = _get_db()

    # Verify alert exists; read client_id for propagation
    try:
        alert_res = (
            db.table("alerts")
            .select("id, client_id")
            .eq("id", alert_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("create_diagnosis: alert lookup failed %s: %s", alert_id, exc)
        raise HTTPException(503, "database error")

    alert_row = alert_res.data[0] if (alert_res and alert_res.data) else None
    if not alert_row:
        raise HTTPException(404, f"alert not found: {alert_id!r}")

    client_uuid = alert_row.get("client_id")

    # Idempotency: if key provided, check for existing diagnosis with same fingerprint
    fingerprint: Optional[str] = None
    if idempotency_key:
        fingerprint = f"diag:{alert_id}:{idempotency_key}"
        try:
            dup = (
                db.table("core_diagnoses")
                .select("*")
                .eq("fingerprint", fingerprint)
                .limit(1)
                .execute()
            )
            if dup and dup.data:
                return _diagnosis_row_to_out(dup.data[0])
        except Exception:
            pass  # dedup lookup failed — proceed with insert

    created_by = getattr(_auth, "scope", "hermes") if _auth else "hermes"
    try:
        ins = db.table("core_diagnoses").insert({
            "alert_id":              alert_id,
            "client_id":             client_uuid,
            "status":                body.status if isinstance(body.status, str) else body.status.value,
            "summary":               body.summary,
            "root_cause_hypotheses": [h.model_dump(mode="json") for h in body.root_cause_hypotheses],
            "evidence":              body.evidence.model_dump(mode="json"),
            "confidence":            body.confidence if isinstance(body.confidence, str) or body.confidence is None else body.confidence.value,
            "limitations":           body.limitations,
            "fingerprint":           fingerprint,
            "created_by":            created_by,
        }).execute()
    except Exception as exc:
        logger.error("create_diagnosis: insert failed %s: %s", alert_id, exc)
        raise HTTPException(503, "database error")

    row = ins.data[0] if (ins and ins.data) else None
    if not row:
        raise HTTPException(503, "insert returned no data")
    return _diagnosis_row_to_out(row)


@router.post(
    "/diagnoses/{diagnosis_id}/recommendations",
    summary="Create a recommendation for a diagnosis (Hermes write)",
    response_model=RecommendationOut,
    status_code=201,
)
async def create_recommendation_for_diagnosis(
    diagnosis_id: str,
    body: RecommendationCreate,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    _auth: Annotated[AuthContext, Depends(SCOPE_HERMES_WRITE)] = None,
) -> RecommendationOut:
    db = _get_db()

    # Verify diagnosis exists; inherit alert_id + client_id
    try:
        diag_res = (
            db.table("core_diagnoses")
            .select("id, alert_id, client_id")
            .eq("id", diagnosis_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("create_recommendation: diag lookup failed %s: %s", diagnosis_id, exc)
        raise HTTPException(503, "database error")

    diag_row = diag_res.data[0] if (diag_res and diag_res.data) else None
    if not diag_row:
        raise HTTPException(404, f"diagnosis not found: {diagnosis_id!r}")

    alert_id   = str(diag_row["alert_id"])
    client_uuid = diag_row.get("client_id")

    # Idempotency
    fingerprint: Optional[str] = None
    if idempotency_key:
        fingerprint = f"rec:{diagnosis_id}:{idempotency_key}"
        try:
            dup = (
                db.table("core_recommendations")
                .select("*")
                .eq("fingerprint", fingerprint)
                .limit(1)
                .execute()
            )
            if dup and dup.data:
                return _recommendation_row_to_out(dup.data[0])
        except Exception:
            pass

    created_by = getattr(_auth, "scope", "hermes") if _auth else "hermes"
    priority_val = body.priority if isinstance(body.priority, str) else body.priority.value
    try:
        ins = db.table("core_recommendations").insert({
            "diagnosis_id":            diagnosis_id,
            "alert_id":                alert_id,
            "client_id":               client_uuid,
            "title":                   body.title,
            "action":                  body.action,
            "rationale":               body.rationale,
            "priority":                priority_val,
            "risk":                    body.risk,
            "expected_impact":         body.expected_impact,
            "requires_human_approval": body.requires_human_approval,
            "status":                  "PROPOSED",
            "fingerprint":             fingerprint,
            "created_by":              created_by,
        }).execute()
    except Exception as exc:
        logger.error("create_recommendation: insert failed %s: %s", diagnosis_id, exc)
        raise HTTPException(503, "database error")

    row = ins.data[0] if (ins and ins.data) else None
    if not row:
        raise HTTPException(503, "insert returned no data")
    return _recommendation_row_to_out(row)


# ── POST /changes ─────────────────────────────────────────────────────────────

@router.post(
    "/changes",
    summary="Record a HUMAN change log entry",
    response_model=ChangeOut,
    status_code=201,
)
async def create_change(
    body: ChangeCreate,
    idempotency_key: Optional[str] = Header(default=None),
    _auth: Annotated[AuthContext, Depends(SCOPE_HERMES_WRITE)] = None,
) -> ChangeOut:
    db = _get_db()
    try:
        ins = db.table("core_change_log").insert({
            "client_id":           body.client_id,
            "occurred_at":         body.occurred_at.isoformat(),
            "channel":             body.channel,
            "platform_account_id": body.platform_account_id,
            "entity_type":         body.entity_type,
            "entity_name_at_time": body.entity_name_at_time,
            "change_type":         body.change_type if isinstance(body.change_type, str) else body.change_type.value,
            "confidence":          body.confidence if isinstance(body.confidence, str) else body.confidence.value,
            "source":              body.source if isinstance(body.source, str) else body.source.value,
            "campaign_id":         body.campaign_id,
            "adset_or_adgroup_id": body.adset_or_adgroup_id,
            "ad_id":               body.ad_id,
            "before_state":        body.before,
            "after_state":         body.after,
            "reason":              body.reason,
            "reported_by":         body.reported_by,
            "linked_action_id":    body.linked_action_id,
        }).execute()
    except Exception as exc:
        logger.error("create_change: insert failed: %s", exc)
        raise HTTPException(503, "database error")
    row = ins.data[0] if (ins and ins.data) else None
    if not row:
        raise HTTPException(503, "insert returned no data")
    return _change_row_to_out(row)


# ── GET /recommendations/{recommendation_id}/action-events ───────────────────

@router.get(
    "/recommendations/{recommendation_id}/action-events",
    summary="Action events for a recommendation",
    response_model=list[ActionEventOut],
)
async def get_recommendation_action_events(
    recommendation_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[ActionEventOut]:
    if not _is_valid_uuid(recommendation_id):
        return []
    db = _get_db()
    try:
        res = (
            db.table("core_action_events")
            .select("*")
            .eq("recommendation_id", recommendation_id)
            .order("occurred_at", desc=True)
            .execute()
        )
    except Exception as exc:
        logger.error("get_recommendation_action_events: DB error %s: %s", recommendation_id, exc)
        raise HTTPException(503, "database error")
    return [_action_event_row_to_out(r) for r in (res.data or [])]


# ── GET /clients/{client_id}/action-events ────────────────────────────────────

@router.get(
    "/clients/{client_id}/action-events",
    summary="Flat action events list for a client (2-step: recommendations → events)",
    response_model=list[ActionEventOut],
)
async def get_client_action_events(
    client_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[ActionEventOut]:
    client = _load_client_meta(client_id)
    if not client:
        raise HTTPException(404, f"client not found: {client_id!r}")

    db = _get_db()
    try:
        recs_res = (
            db.table("core_recommendations")
            .select("id")
            .eq("client_id", client["id"])
            .execute()
        )
    except Exception as exc:
        logger.error("get_client_action_events: recs query failed for %s: %s", client_id, exc)
        raise HTTPException(503, "database error")

    rec_ids = [str(r["id"]) for r in (recs_res.data or [])]
    if not rec_ids:
        return []

    try:
        events_res = (
            db.table("core_action_events")
            .select("*")
            .in_("recommendation_id", rec_ids)
            .order("occurred_at", desc=True)
            .limit(100)
            .execute()
        )
    except Exception as exc:
        logger.error("get_client_action_events: events query failed for %s: %s", client_id, exc)
        raise HTTPException(503, "database error")

    return [_action_event_row_to_out(r) for r in (events_res.data or [])]


# ── POST /action-events ───────────────────────────────────────────────────────

def _action_event_row_to_out(row: dict) -> ActionEventOut:
    return ActionEventOut(
        recommendation_id=str(row["recommendation_id"]),
        event_type=row.get("event_type", "APPROVED"),
        actor=row.get("actor", ""),
        occurred_at=_parse_ts_utc(row.get("occurred_at")),
        note=row.get("note"),
        change_id=str(row["change_id"]) if row.get("change_id") else None,
        id=str(row["id"]),
        created_at=_parse_ts_utc(row.get("created_at")) or datetime.now(timezone.utc),
    )


@router.post(
    "/action-events",
    summary="Record a decision event (approved, ignored, executed, etc.)",
    response_model=ActionEventOut,
    status_code=201,
)
async def create_action_event(
    body: ActionEventCreate,
    idempotency_key: Optional[str] = Header(default=None),
    _auth: Annotated[AuthContext, Depends(SCOPE_WRITE_ANY)] = None,
) -> ActionEventOut:
    db = _get_db()
    occurred = (body.occurred_at or datetime.now(timezone.utc)).isoformat()
    try:
        ins = db.table("core_action_events").insert({
            "recommendation_id": body.recommendation_id,
            "event_type":        body.event_type if isinstance(body.event_type, str) else body.event_type.value,
            "actor":             body.actor,
            "occurred_at":       occurred,
            "note":              body.note,
            "change_id":         body.change_id,
        }).execute()
    except Exception as exc:
        logger.error("create_action_event: insert failed: %s", exc)
        raise HTTPException(503, "database error")
    row = ins.data[0] if (ins and ins.data) else None
    if not row:
        raise HTTPException(503, "insert returned no data")
    return _action_event_row_to_out(row)


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
    db = _get_db()
    # Verify the contract exists
    if not _is_valid_uuid(body.report_contract_id):
        raise HTTPException(422, f"invalid report_contract_id: {body.report_contract_id!r}")
    try:
        cr = (
            db.table("agency_report_contracts")
            .select("id, client_slug, report_type, period_start, period_end")
            .eq("id", body.report_contract_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("create_report_narrative: contract lookup failed: %s", exc)
        raise HTTPException(503, "database error")
    contract = cr.data[0] if (cr and cr.data) else None
    if not contract:
        raise HTTPException(404, f"report_contract not found: {body.report_contract_id!r}")

    try:
        ins = db.table("core_report_narratives").insert({
            "report_contract_id": body.report_contract_id,
            "blocks":             [b.model_dump(mode="json") for b in body.blocks],
            "visibility_scope":   body.visibility_scope if isinstance(body.visibility_scope, str) else body.visibility_scope.value,
            "status":             "DRAFT",
        }).execute()
    except Exception as exc:
        logger.error("create_report_narrative: insert failed: %s", exc)
        raise HTTPException(503, "database error")
    row = ins.data[0] if (ins and ins.data) else None
    if not row:
        raise HTTPException(503, "insert returned no data")
    truth_versions = _load_truth_versions(contract.get("client_slug", ""))
    return _narrative_row_to_out(row, contract, truth_versions)


# ── POST /report-narratives/{narrative_id}/transitions ───────────────────────


def _trigger_report_delivery(narrative_id: str, contract_id: str, approved_by: str) -> None:
    """Background delivery task fired when a narrative transitions to PUBLISHED."""
    try:
        from ...core.report_publisher import publish_narrative_report
        publish_narrative_report(narrative_id, contract_id, approved_by)
    except Exception as exc:
        logger.error(
            "_trigger_report_delivery: failed narrative=%s contract=%s: %s",
            narrative_id, contract_id, exc,
        )


@router.post(
    "/report-narratives/{narrative_id}/transitions",
    summary="Transition narrative lifecycle: DRAFT→READY_FOR_REVIEW→APPROVED→PUBLISHED",
    response_model=ReportNarrativeOut,
)
async def transition_narrative(
    narrative_id: str,
    body: NarrativeTransition,
    background_tasks: BackgroundTasks,
    idempotency_key: Optional[str] = Header(default=None, alias="idempotency-key"),
    _auth: Annotated[AuthContext, Depends(SCOPE_WRITE_ANY)] = None,
) -> ReportNarrativeOut:
    if not _is_valid_uuid(narrative_id):
        raise HTTPException(404, f"narrative not found: {narrative_id!r}")
    db = _get_db()

    # Fetch current narrative
    try:
        res = (
            db.table("core_report_narratives")
            .select("*")
            .eq("id", narrative_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("transition_narrative: fetch failed %s: %s", narrative_id, exc)
        raise HTTPException(503, "database error")
    row = res.data[0] if (res and res.data) else None
    if not row:
        raise HTTPException(404, f"narrative not found: {narrative_id!r}")

    try:
        current = NarrativeStatus(row.get("status", "DRAFT"))
    except ValueError:
        current = NarrativeStatus.DRAFT
    target = body.target_status

    # target may be a plain str (use_enum_values=True on _Base) or NarrativeStatus
    target_str = str(target)
    try:
        target_enum = NarrativeStatus(target_str)
    except ValueError:
        raise HTTPException(422, f"unknown target_status: {target_str!r}")

    allowed = _VALID_TRANSITIONS.get(current, set())
    if target_enum not in allowed:
        raise HTTPException(
            422,
            f"invalid transition {current.value!r} → {target_str!r}; "
            f"allowed from {current.value!r}: {[s.value for s in allowed] or 'none'}"
        )

    # Governance: hermes_service may flag READY_FOR_REVIEW but cannot approve or publish.
    # Approval/publication require a human-bearing scope (agency_admin or platform_web).
    _caller_scope = getattr(_auth, "scope", "") if _auth else ""
    if target_enum in (NarrativeStatus.APPROVED, NarrativeStatus.PUBLISHED):
        if _caller_scope == "hermes_service":
            raise HTTPException(
                403,
                f"hermes_service cannot perform {target_str!r} transition; "
                "approval and publication require agency_admin or platform_web"
            )

    now_utc = datetime.now(timezone.utc)
    updates: dict = {"status": target_str}
    if target_enum == NarrativeStatus.APPROVED:
        updates["approved_by"] = body.actor
        updates["approved_at"] = now_utc.isoformat()
    elif target_enum == NarrativeStatus.PUBLISHED:
        updates["published_at"] = now_utc.isoformat()

    try:
        upd = (
            db.table("core_report_narratives")
            .update(updates)
            .eq("id", narrative_id)
            .execute()
        )
    except Exception as exc:
        logger.error("transition_narrative: update failed %s: %s", narrative_id, exc)
        raise HTTPException(503, "database error")

    updated_row = (upd.data[0] if (upd and upd.data) else None) or {**row, **updates}

    # On PUBLISHED: supersede previous PUBLISHED narrative + fire delivery
    if target_enum == NarrativeStatus.PUBLISHED:
        try:
            db.table("core_report_narratives").update({"status": "SUPERSEDED"}).eq(
                "report_contract_id", str(row["report_contract_id"])
            ).eq("status", "PUBLISHED").neq("id", narrative_id).execute()
        except Exception as exc:
            logger.warning("transition_narrative: supersede failed: %s", exc)
        background_tasks.add_task(
            _trigger_report_delivery,
            narrative_id=narrative_id,
            contract_id=str(row["report_contract_id"]),
            approved_by=body.actor,
        )

    contract: dict | None = None
    try:
        cr = (
            db.table("agency_report_contracts")
            .select("id, client_slug, report_type, period_start, period_end")
            .eq("id", str(row["report_contract_id"]))
            .limit(1)
            .execute()
        )
        contract = cr.data[0] if (cr and cr.data) else None
    except Exception:
        pass

    truth_versions = _load_truth_versions(contract["client_slug"]) if contract else None
    return _narrative_row_to_out(updated_row, contract, truth_versions)


# ── GET /action-events/{action_event_id}/outcomes ────────────────────────────

@router.get(
    "/action-events/{action_event_id}/outcomes",
    summary="Outcomes measured for an action event",
    response_model=list[OutcomeOut],
)
async def get_action_event_outcomes(
    action_event_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[OutcomeOut]:
    if not _is_valid_uuid(action_event_id):
        return []
    db = _get_db()
    try:
        res = (
            db.table("core_outcomes")
            .select("*")
            .eq("action_event_id", action_event_id)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:
        logger.error("get_action_event_outcomes: DB error %s: %s", action_event_id, exc)
        raise HTTPException(503, "database error")
    return [_outcome_row_to_out(r) for r in (res.data or [])]


# ── POST /outcomes ────────────────────────────────────────────────────────────

def _outcome_row_to_out(row: dict) -> OutcomeOut:
    from .enums import OutcomeStatus as _OutcomeStatus
    try:
        status = _OutcomeStatus(row.get("status", "PENDING"))
    except ValueError:
        status = _OutcomeStatus.PENDING
    return OutcomeOut(
        action_event_id=str(row["action_event_id"]),
        measurement_period=row.get("measurement_period") or {},
        metric_refs=row.get("metric_refs") or [],
        before_state=row.get("before_state"),
        after_state=row.get("after_state"),
        delta=row.get("delta"),
        status=status,
        measured_at=_parse_ts_utc(row.get("measured_at")),
        id=str(row["id"]),
        recommendation_id=str(row["recommendation_id"]) if row.get("recommendation_id") else None,
        client_id=str(row["client_id"]) if row.get("client_id") else None,
        created_at=_parse_ts_utc(row.get("created_at")) or datetime.now(timezone.utc),
        created_by=row.get("created_by", "human"),
    )


@router.post(
    "/outcomes",
    summary="Record a measured outcome for an action event",
    response_model=OutcomeOut,
    status_code=201,
)
async def create_outcome(
    body: OutcomeCreate,
    _auth: Annotated[AuthContext, Depends(SCOPE_PLATFORM_WRITE)] = None,
) -> OutcomeOut:
    db = _get_db()
    try:
        ins = db.table("core_outcomes").insert({
            "action_event_id":    body.action_event_id,
            "measurement_period": body.measurement_period,
            "metric_refs":        body.metric_refs,
            "before_state":       body.before_state,
            "after_state":        body.after_state,
            "delta":              body.delta,
            "status":             body.status if isinstance(body.status, str) else body.status.value,
            "measured_at":        body.measured_at.isoformat() if body.measured_at else None,
        }).execute()
    except Exception as exc:
        logger.error("create_outcome: insert failed: %s", exc)
        raise HTTPException(503, "database error")
    row = ins.data[0] if (ins and ins.data) else None
    if not row:
        raise HTTPException(503, "insert returned no data")
    return _outcome_row_to_out(row)


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
    _auth: Annotated[AuthContext, Depends(SCOPE_WRITE_ANY)] = None,
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

    # Parse run_key: core_pipeline:{client_id}:{period_end}[:{any suffix}]
    # Suffix present on replay jobs (e.g. :replay:{id[:8]}) — ignored for parsing.
    run_key = original.get("run_key", "")
    parts = run_key.split(":")
    if len(parts) < 3 or parts[0] != "core_pipeline":
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


# ── POST /clients/{client_id}/pipeline/trigger ────────────────────────────────
#
# First-run trigger — creates a new QUEUED job without requiring an existing job.
# Used for multi-client onboarding before the daily scheduler has ever run.
# period_end default: today - 1 (e.g. 2026-10-05 when today=2026-10-06).
# period_start: period_end - 6 days (e.g. 2026-09-29→2026-10-05 for default).
# For LK-equivalent window pass period_end=2026-10-04 → 2026-09-28→2026-10-04.

@router.post(
    "/clients/{client_id}/pipeline/trigger",
    summary="Trigger a first (or fresh) Core pipeline run for a client",
    response_model=JobOut,
)
async def trigger_pipeline(
    client_id: str,
    background_tasks: BackgroundTasks,
    period_end_str: Optional[str] = Query(
        None,
        alias="period_end",
        description="ISO date for period_end (default: yesterday). period_start = period_end - 6d.",
    ),
    _auth: Annotated[AuthContext, Depends(require_scopes("agency_admin", "platform_web"))] = None,
) -> JobOut:
    from datetime import date as _date
    from ...core.core_scheduler import run_core_pipeline_for_client

    # Resolve period
    if period_end_str:
        try:
            period_end = _date.fromisoformat(period_end_str)
        except ValueError:
            raise HTTPException(422, detail=f"invalid period_end: {period_end_str!r}")
    else:
        period_end = _date.today() - timedelta(days=1)
    period_start = period_end - timedelta(days=6)

    # Verify client exists
    try:
        res = _get_db().table("clients").select("client_id").eq("client_id", client_id).limit(1).execute()
    except Exception as exc:
        logger.error("trigger_pipeline: DB error for %s: %s", client_id, exc)
        raise HTTPException(503, detail="database unavailable")
    if not (res and res.data):
        raise HTTPException(404, detail=f"client not found: {client_id!r}")

    # Insert QUEUED job (no replay_of — first run)
    run_key = f"core_pipeline:{client_id}:{period_end.isoformat()}"
    job_id  = str(_uuid_mod.uuid4())
    try:
        ins = _get_db().table("core_job_runs").insert({
            "id":         job_id,
            "job_type":   "core_pipeline",
            "run_key":    run_key,
            "status":     "QUEUED",
            "attempt":    1,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        job_id = ins.data[0]["id"] if ins.data else job_id
    except Exception as exc:
        # Unique constraint = same period already queued/ran; return existing
        logger.warning("trigger_pipeline: job insert failed for %s (%s): %s", client_id, run_key, exc)
        try:
            ex = _get_db().table("core_job_runs").select("*").eq("run_key", run_key).limit(1).execute()
            if ex.data:
                row = ex.data[0]
                return JobOut(
                    id=row["id"], job_type=row["job_type"], run_key=row["run_key"],
                    status=JobStatus(row["status"]), attempt=row["attempt"],
                )
        except Exception:
            pass
        raise HTTPException(409, detail=f"job already exists for period {period_end.isoformat()}")

    background_tasks.add_task(run_core_pipeline_for_client, job_id, client_id, period_start, period_end)
    logger.info("trigger_pipeline: queued %s for %s (%s→%s)", job_id, client_id, period_start, period_end)

    return JobOut(
        id=job_id,
        job_type="core_pipeline",
        run_key=run_key,
        status=JobStatus.QUEUED,
        attempt=1,
    )


# ── GET /clients/{client_id}/meta/insights ────────────────────────────────────
#
# READ-ONLY diagnostic — reads from meta_ad_attributions (pre-aggregated by the
# legacy collector).  Never loads Meta access tokens.  Never writes.
# Hermes cannot reach this endpoint (SCOPE_READ_ANY excludes hermes_service on
# any path that would expose client secrets, but here there are no secrets anyway
# — keeping SCOPE_READ_ANY consistent with other diagnostic routes).

_META_VALID_AGGREGATIONS = frozenset({"daily", "monthly"})
_META_VALID_LEVELS       = frozenset({"account", "campaign", "adset", "ad"})
_META_DB_FETCH_LIMIT     = 50_000   # hard safety cap on total raw rows fetched
_META_PAGE_SIZE          = 1_000    # PostgREST max_rows is typically 1000


@router.get(
    "/clients/{client_id}/meta/insights",
    summary="Meta Ads diagnostic — READ-ONLY insights from meta_ad_attributions",
    response_model=MetaInsightsOut,
)
async def get_meta_insights(
    client_id: str,
    date_from: str = Query(..., description="YYYY-MM-DD inclusive start"),
    date_to:   str = Query(..., description="YYYY-MM-DD inclusive end"),
    aggregation: str = Query("daily",    pattern="^(daily|monthly)$"),
    level:       str = Query("campaign", pattern="^(account|campaign|adset|ad)$"),
    limit: int = Query(500, ge=1, le=2000, description="Max rows in response"),
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> MetaInsightsOut:
    from collections import defaultdict

    # ── 1. Validate date params ───────────────────────────────────────────────
    try:
        d_from = date.fromisoformat(date_from)
        d_to   = date.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(422, "date_from and date_to must be YYYY-MM-DD")
    if d_from > d_to:
        raise HTTPException(422, "date_from must be <= date_to")

    # ── 2. Resolve client UUID (no token) ─────────────────────────────────────
    client_meta = _load_client_meta(client_id)
    if not client_meta:
        raise HTTPException(404, f"client not found: {client_id!r}")
    client_uuid = client_meta["id"]

    # ── 3. Load account_id (no token) ────────────────────────────────────────
    account_id = "unknown"
    try:
        acc_res = (
            _get_db().table("clients")
            .select("meta_ad_account_id")
            .eq("id", client_uuid)
            .limit(1)
            .execute()
        )
        if acc_res.data and acc_res.data[0].get("meta_ad_account_id"):
            raw_acc = acc_res.data[0]["meta_ad_account_id"]
            account_id = raw_acc if str(raw_acc).startswith("act_") else f"act_{raw_acc}"
    except Exception as exc:
        logger.warning("get_meta_insights: could not load account_id for %s: %s", client_id, exc)

    # ── 4. Fetch raw rows from meta_ad_attributions (paginated) ──────────────
    # PostgREST silently caps .limit() at its server-side max_rows (typically
    # 1000).  Use .range() in a loop so every page is an explicit window that
    # PostgREST must honour, ensuring no rows are silently dropped.
    raw_rows: list[dict] = []
    try:
        db = _get_db()
        offset = 0
        while len(raw_rows) < _META_DB_FETCH_LIMIT:
            page_res = (
                db.table("meta_ad_attributions")
                .select(
                    "date, campaign_id, campaign_name, "
                    "adset_id, adset_name, ad_id, ad_name, "
                    "spend, impressions, clicks, purchases, purchase_value"
                )
                .eq("client_id", client_uuid)
                .gte("date", d_from.isoformat())
                .lte("date", d_to.isoformat())
                .order("date", desc=False)
                .range(offset, offset + _META_PAGE_SIZE - 1)
                .execute()
            )
            batch = page_res.data or []
            raw_rows.extend(batch)
            if len(batch) < _META_PAGE_SIZE:
                break   # last page — no more rows
            offset += _META_PAGE_SIZE
    except Exception as exc:
        logger.error("get_meta_insights: DB error for %s: %s", client_id, exc)
        raise HTTPException(503, "database unavailable")

    # ── 5. NO_DATA short-circuit ──────────────────────────────────────────────
    request_id = str(_uuid_mod.uuid4())
    if not raw_rows:
        return MetaInsightsOut(
            client_id=client_id,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            aggregation=aggregation,
            level=level,
            rows=[],
            row_count=0,
            note=f"NO_DATA: no rows in meta_ad_attributions for {client_id!r} between {date_from} and {date_to}",
            request_id=request_id,
        )

    # ── 6. Aggregate in Python ───────────────────────────────────────────────
    # key: (period_str, campaign_id|None, campaign_name|None,
    #        adset_id|None, adset_name|None, ad_id|None, ad_name|None)
    # The level controls which entity fields are kept (others set to None).

    def _period_key(row_date: str) -> str:
        if aggregation == "monthly":
            return row_date[:7]   # YYYY-MM
        return row_date           # YYYY-MM-DD

    def _entity_key(r: dict) -> tuple:
        if level == "account":
            return (None, None, None, None, None, None)
        if level == "campaign":
            return (r.get("campaign_id"), r.get("campaign_name"), None, None, None, None)
        if level == "adset":
            return (
                r.get("campaign_id"), r.get("campaign_name"),
                r.get("adset_id"), r.get("adset_name"),
                None, None,
            )
        # ad
        return (
            r.get("campaign_id"), r.get("campaign_name"),
            r.get("adset_id"), r.get("adset_name"),
            r.get("ad_id"), r.get("ad_name"),
        )

    Bucket = dict  # typed alias for clarity
    buckets: dict[tuple, Bucket] = defaultdict(lambda: {
        "spend": 0.0, "impressions": 0, "clicks": 0,
        "purchases": 0.0, "purchase_value": 0.0,
        "has_any": False,
    })

    for r in raw_rows:
        pk  = _period_key(str(r["date"]))
        ek  = _entity_key(r)
        key = (pk, *ek)
        b   = buckets[key]
        b["spend"]          += float(r.get("spend") or 0)
        b["impressions"]    += int(r.get("impressions") or 0)
        b["clicks"]         += int(r.get("clicks") or 0)
        b["purchases"]      += float(r.get("purchases") or 0)
        b["purchase_value"] += float(r.get("purchase_value") or 0)
        b["has_any"] = True

    # Sort keys for deterministic output
    sorted_keys = sorted(buckets.keys())
    row_count   = len(sorted_keys)
    truncated   = row_count > limit
    output_keys = sorted_keys[:limit]

    out_rows: list[MetaInsightRow] = []
    for key in output_keys:
        pk, cid, cname, sid, sname, aid, aname = key
        b = buckets[key]
        purchases      = round(b["purchases"], 2)
        purchase_value = round(b["purchase_value"], 2)
        out_rows.append(MetaInsightRow(
            period=pk,
            campaign_id=cid,
            campaign_name=cname,
            adset_id=sid,
            adset_name=sname,
            ad_id=aid,
            ad_name=aname,
            spend=round(b["spend"], 2),
            impressions=b["impressions"],
            clicks=b["clicks"],
            conversions=purchases if purchases > 0 else None,
            conversion_value=purchase_value if purchase_value > 0 else None,
        ))

    note: Optional[str] = None
    if truncated:
        note = f"TRUNCATED: response limited to {limit} rows; {row_count} total available"
    if len(raw_rows) >= _META_DB_FETCH_LIMIT:
        fetch_note = f"DB_FETCH_LIMIT_HIT: only {_META_DB_FETCH_LIMIT} raw rows were loaded; widen date range with care"
        note = f"{note}; {fetch_note}" if note else fetch_note

    return MetaInsightsOut(
        client_id=client_id,
        account_id=account_id,
        date_from=date_from,
        date_to=date_to,
        aggregation=aggregation,
        level=level,
        rows=out_rows,
        row_count=row_count,
        note=note,
        request_id=request_id,
    )


# ── POST /clients/{client_id}/weekly-review/replay ────────────────────────────
#
# Trigger a fresh Weekly Account Review contract for a client.
# Defaults to the most recently completed Mon-Sun week (auto-detected from UTC date).
# Optional period_end param forces a specific week (useful for backfill / testing).
#
# Scope: agency_admin or platform_web (same as pipeline replay).
# Returns: QUEUED job record; execution runs in BackgroundTasks.
# Idempotency: contract write is delete+insert on (client_slug, report_type, period_end).

@router.post(
    "/clients/{client_id}/weekly-review/replay",
    summary="Trigger (or replay) a Weekly Account Review for a client",
    response_model=JobOut,
    status_code=202,
)
async def replay_weekly_review(
    client_id: str,
    background_tasks: BackgroundTasks,
    period_end_str: Optional[str] = Query(
        None,
        description="ISO date for period_end (Sunday). Defaults to last completed Sunday.",
    ),
    _auth: Annotated[AuthContext, Depends(require_scopes("agency_admin", "platform_web"))] = None,
) -> JobOut:
    from ...core.weekly_review import (
        execute_weekly_review_replay,
        queue_weekly_review_replay,
        _last_completed_week,
    )

    # Determine period
    if period_end_str:
        try:
            period_end = date.fromisoformat(period_end_str)
        except ValueError:
            raise HTTPException(422, detail=f"invalid period_end: {period_end_str!r}")
    else:
        _, period_end = _last_completed_week()

    period_start = period_end - timedelta(days=6)

    # Validate client exists
    try:
        res = _get_db().table("clients").select("client_id").eq("client_id", client_id).limit(1).execute()
    except Exception as exc:
        logger.error("replay_weekly_review: DB error for %s: %s", client_id, exc)
        raise HTTPException(503, detail="database unavailable")
    if not (res and res.data):
        raise HTTPException(404, detail=f"client not found: {client_id!r}")

    # Find most recent weekly_review job for this period to use as replay_of
    replay_of = str(_uuid_mod.uuid4())  # sentinel when no prior job exists
    try:
        prior = (
            _get_db().table("core_job_runs")
            .select("id")
            .eq("job_type", "weekly_review")
            .like("run_key", f"weekly_review:{client_id}:{period_end.isoformat()}%")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if prior and prior.data:
            replay_of = prior.data[0]["id"]
    except Exception:
        pass

    # Queue and execute in background
    db = _get_db()
    new_job_id = queue_weekly_review_replay(db, client_id, period_end, replay_of)
    background_tasks.add_task(execute_weekly_review_replay, new_job_id, client_id, period_end)

    run_key = f"weekly_review:{client_id}:{period_end.isoformat()}"
    logger.info(
        "replay_weekly_review: queued %s for %s (%s→%s)",
        new_job_id, client_id, period_start, period_end,
    )
    return JobOut(
        id=new_job_id,
        job_type="weekly_review",
        run_key=run_key,
        status=JobStatus.QUEUED,
        attempt=1,
        replay_of=replay_of,
    )


# ── POST /clients/{client_id}/monthly-review/replay ───────────────────────────
#
# Trigger a fresh Monthly Account Review contract for a client.
# Defaults to the most recently completed calendar month (auto-detected from UTC date).
# Optional year/month params force a specific month (useful for backfill / testing).
#
# Scope: agency_admin or platform_web.
# Returns: QUEUED job record; execution runs in BackgroundTasks.
# Idempotency: contract write is delete+insert on (client_slug, report_type='monthly', period_end).

@router.post(
    "/clients/{client_id}/monthly-review/replay",
    summary="Trigger (or replay) a Monthly Account Review for a client",
    response_model=JobOut,
    status_code=202,
)
async def replay_monthly_review(
    client_id: str,
    background_tasks: BackgroundTasks,
    year: Optional[int] = Query(None, description="Year of the closed month. Defaults to last completed month."),
    month: Optional[int] = Query(None, description="Month (1-12) of the closed month. Defaults to last completed month."),
    _auth: Annotated[AuthContext, Depends(require_scopes("agency_admin", "platform_web"))] = None,
) -> JobOut:
    from ...core.monthly_review import (
        execute_monthly_review_replay,
        queue_monthly_review_replay,
        _last_completed_month,
        _month_bounds,
    )

    if (year is None) != (month is None):
        raise HTTPException(422, detail="year and month must both be provided or both omitted")

    if year is not None and month is not None:
        if not (1 <= month <= 12):
            raise HTTPException(422, detail=f"invalid month: {month!r}")
        if year < 2020 or year > 2100:
            raise HTTPException(422, detail=f"invalid year: {year!r}")
    else:
        year, month = _last_completed_month()

    # Validate client exists
    try:
        res = _get_db().table("clients").select("client_id").eq("client_id", client_id).limit(1).execute()
    except Exception as exc:
        logger.error("replay_monthly_review: DB error for %s: %s", client_id, exc)
        raise HTTPException(503, detail="database unavailable")
    if not (res and res.data):
        raise HTTPException(404, detail=f"client not found: {client_id!r}")

    _, period_end = _month_bounds(year, month)

    replay_of = str(_uuid_mod.uuid4())
    try:
        prior = (
            _get_db().table("core_job_runs")
            .select("id")
            .eq("job_type", "monthly_review")
            .like("run_key", f"monthly_review:{client_id}:{period_end.isoformat()}%")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if prior and prior.data:
            replay_of = prior.data[0]["id"]
    except Exception:
        pass

    db = _get_db()
    new_job_id = queue_monthly_review_replay(db, client_id, year, month, replay_of)
    background_tasks.add_task(execute_monthly_review_replay, new_job_id, client_id, year, month)

    run_key = f"monthly_review:{client_id}:{period_end.isoformat()}"
    logger.info(
        "replay_monthly_review: queued %s for %s (%d-%02d)",
        new_job_id, client_id, year, month,
    )
    return JobOut(
        id=new_job_id,
        job_type="monthly_review",
        run_key=run_key,
        status=JobStatus.QUEUED,
        attempt=1,
        replay_of=replay_of,
    )


# ── Balance monitoring ─────────────────────────────────────────────────────────

@router.get(
    "/clients/{client_id}/balance",
    response_model=ClientBalanceOut,
    summary="Latest balance snapshots for a client's prepaid ad accounts",
)
async def get_client_balance(
    client_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> ClientBalanceOut:
    db = _get_db()

    try:
        client_res = (
            db.table("clients")
            .select("client_id, google_prepaid, meta_prepaid")
            .eq("client_id", client_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("get_client_balance: DB error for %s: %s", client_id, exc)
        raise HTTPException(503, detail="database unavailable")

    if not (client_res and client_res.data):
        raise HTTPException(404, detail=f"client not found: {client_id!r}")

    client_row  = client_res.data[0]
    any_prepaid = bool(client_row.get("google_prepaid") or client_row.get("meta_prepaid"))

    try:
        snaps_res = (
            db.table("core_balance_snapshots")
            .select(
                "platform, billing_model, collection_status, balance, balance_status, "
                "threshold_low, threshold_critical, currency, snapshot_at, error, "
                "avg_daily_spend, spend_avg_window_days, spend_data_days, estimated_days_remaining"
            )
            .eq("client_id", client_id)
            .order("snapshot_at", desc=True)
            .limit(20)
            .execute()
        )
        rows = snaps_res.data or []
    except Exception as exc:
        logger.error("get_client_balance: snapshot load failed %s: %s", client_id, exc)
        raise HTTPException(503, detail="database unavailable")

    seen: set[str] = set()
    snapshots: list[BalanceSnapshotOut] = []
    for row in rows:
        p = row.get("platform", "")
        if p in seen:
            continue
        seen.add(p)
        snapshots.append(BalanceSnapshotOut(
            platform=p,
            billing_model=row.get("billing_model"),
            collection_status=row.get("collection_status"),
            balance_available=row.get("balance"),
            balance_status=row.get("balance_status", "NO_DATA"),
            threshold_low=row.get("threshold_low"),
            threshold_critical=row.get("threshold_critical"),
            currency=row.get("currency"),
            spend_daily_reference=row.get("avg_daily_spend"),
            spend_avg_window_days=row.get("spend_avg_window_days"),
            spend_data_days=row.get("spend_data_days"),
            estimated_days_remaining=row.get("estimated_days_remaining"),
            source_state=row.get("collection_status"),
            collected_at=row.get("snapshot_at"),
            error=row.get("error"),
        ))

    return ClientBalanceOut(
        client_id=client_id,
        snapshots=snapshots,
        any_prepaid=any_prepaid,
        monitoring_enabled=any_prepaid,
    )


@router.post(
    "/clients/{client_id}/balance/trigger",
    response_model=BalanceTriggerOut,
    status_code=200,
    summary="Run an on-demand balance check for a client (synchronous)",
)
async def trigger_client_balance(
    client_id: str,
    platform: Optional[str] = Query(None, description="google | meta — omit to check all prepaid"),
    _auth: Annotated[AuthContext, Depends(require_scopes("agency_admin", "platform_web"))] = None,
) -> BalanceTriggerOut:
    if platform and platform not in ("google", "meta"):
        raise HTTPException(422, detail=f"platform must be 'google' or 'meta', got {platform!r}")

    from ...core.balance_monitor import trigger_balance_check

    result = trigger_balance_check(client_id, platform=platform)
    if "error" in result and not result.get("platforms"):
        if "not found" in str(result.get("error", "")):
            raise HTTPException(404, detail=result["error"])
        raise HTTPException(503, detail=result["error"])

    return BalanceTriggerOut(
        client_id=client_id,
        triggered_at=datetime.now(timezone.utc),
        platforms=result.get("platforms", {}),
    )
