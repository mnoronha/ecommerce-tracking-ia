"""
Agency API v1 router — stub implementation (Etapa 1).

Every endpoint returns a valid example response that matches its schema.
Real implementation replaces stubs incrementally from Etapa 4 onwards.

Auth: each route declares the allowed scopes via Depends(require_scopes(...)).
Idempotency-Key header is accepted on all write endpoints (ignored in stubs).

Route inventory (29 routes):
  GET  /clients
  GET  /clients/{client_id}/truth
  GET  /clients/{client_id}/health
  GET  /clients/{client_id}/pipeline-health
  GET  /clients/{client_id}/metrics
  GET  /clients/{client_id}/changes
  GET  /clients/{client_id}/recommendations
  GET  /clients/{client_id}/report-contracts
  GET  /clients/{client_id}/reports
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

from fastapi import APIRouter, Depends, Header, HTTPException, Query

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
    DiagnosisCreate,
    DiagnosisOut,
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
            .select("client_id, name, business_model, timezone, currency, country, is_active")
            .eq("client_id", client_id)
            .maybe_single()
            .execute()
        )
        return r.data
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


# ── GET /clients/{client_id}/health ──────────────────────────────────────────

@router.get(
    "/clients/{client_id}/health",
    summary="Data Health by domain with source_state",
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
            .select("source_system, source_state, last_error, last_validated_at, updated_at")
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
        checked_raw = s.get("last_validated_at") or s.get("updated_at")
        if isinstance(checked_raw, str):
            checked_at = datetime.fromisoformat(checked_raw.replace("Z", "+00:00"))
        else:
            checked_at = now

        entries.append(DataHealthEntry(
            domain=_source_to_domain(s["source_system"]),
            source_state=SourceState(s["source_state"]),
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
        sources = (
            _get_db().table("core_data_sources")
            .select("source_system, source_state, last_data_at, reconciliation_state")
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

    for s in (sources.data or []):
        domain = _source_to_domain(s["source_system"])
        last_collection[domain] = _parse_dt(s.get("last_data_at"))
        recon = s.get("reconciliation_state")
        if recon == "OK" and s["source_state"] == "READY":
            certification[domain] = CertificationStatus.PROVISIONAL.value
        else:
            certification[domain] = None

    last_snap_at: Optional[datetime] = None
    if snap.data:
        last_snap_at = _parse_dt(snap.data[0].get("computed_at"))

    return PipelineHealthOut(
        client_id=client_id,
        last_collection=last_collection,
        certification=certification,
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


# ── GET /clients/{client_id}/changes ─────────────────────────────────────────

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
    return [
        ChangeOut(
            id="chg_stub_01",
            client_id=client_id,
            occurred_at=_STUB_NOW,
            channel="google_ads",
            platform_account_id="123-456-7890",
            entity_type="campaign",
            campaign_id="20123456789",
            entity_name_at_time="PMax Geral",
            change_type=ChangeType.TARGET_ROAS,
            before=400,
            after=500,
            reason="Campanha gastando acima do ritmo",
            source=ChangeLogSource.HUMAN,
            reported_by="maicon",
            confidence=ChangeConfidence.CONFIRMED,
            match_status=MatchStatus.MATCHED,
            created_at=_STUB_NOW,
        )
    ]


# ── GET /clients/{client_id}/recommendations ─────────────────────────────────

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
    return [
        RecommendationOut(
            id="rec_stub_01",
            diagnosis_id="dia_stub_01",
            recommendation="Reduzir budget do ADV+ Geral em 15% e reavaliar em 72h",
            action_proposal=None,
            priority=Level.HIGH,
            confidence=Level.MEDIUM,
            risk=Level.LOW,
            reversible=True,
            expected_effect="Conter CPA enquanto criativos são renovados",
            review_window_days=3,
            requires_approval=True,
            visibility_scope=VisibilityScope.AGENCY_ONLY,
            status=RecommendationStatus.PENDING_REVIEW,
            created_at=_STUB_NOW,
        )
    ]


# ── GET /clients/{client_id}/report-contracts ─────────────────────────────────

@router.get(
    "/clients/{client_id}/report-contracts",
    summary="Core report contracts generated by the Deterministic Core",
    response_model=list[CoreReportContractOut],
)
async def get_report_contracts(
    client_id: str,
    report_type: Optional[str] = Query(None, pattern="^(WEEKLY|MONTHLY)$"),
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[CoreReportContractOut]:
    return [
        CoreReportContractOut(
            report_contract_id="rpc_stub_2026_w40_lk",
            client_id=client_id,
            report_type=ReportType.WEEKLY,
            period=_STUB_PERIOD,
            contract={"schema_version": "1.1", "stub": True},
            truth_versions=_STUB_VERSIONS,
            generated_at=_STUB_NOW,
        )
    ]


# ── GET /clients/{client_id}/reports ─────────────────────────────────────────

@router.get(
    "/clients/{client_id}/reports",
    summary="Report narratives; client_viewer sees only PUBLISHED",
    response_model=list[ReportNarrativeOut],
)
async def get_reports(
    client_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_ANY_AUTH)] = None,
) -> list[ReportNarrativeOut]:
    return [
        ReportNarrativeOut(
            id="nar_stub_01",
            report_contract_id="rpc_stub_2026_w40_lk",
            blocks=[
                NarrativeBlock(
                    section="resultado",
                    statement="Receita da semana: [stub]",
                    metric_refs={},
                )
            ],
            visibility_scope=VisibilityScope.AGENCY_ONLY,
            status=NarrativeStatus.DRAFT,
            created_at=_STUB_NOW,
        )
    ]


# ── GET /alerts ───────────────────────────────────────────────────────────────

@router.get(
    "/alerts",
    summary="Open (or filtered) alerts",
    response_model=list[AlertOut],
)
async def list_alerts(
    status: Optional[str] = Query(
        "open",
        description=(
            "Alert status filter. Case-insensitive. "
            "Accepted values: OPEN, open, RESOLVED, resolved."
        ),
    ),
    client_id: Optional[str] = Query(None),
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[AlertOut]:
    return [
        AlertOut(
            id="alt_stub_01",
            client_id=client_id or "lk-sneakers",
            rule_key="cpa_increase",
            rule_version="v1",
            metric_key="cpa_meta",
            entity="ADV+ Geral",
            observed_snapshot_refs=["snap_stub_124"],
            baseline_snapshot_refs=["snap_stub_098"],
            delta_pct=31.2,
            min_volume_met=True,
            severity=Level.HIGH,
            status=AlertStatus.OPEN,
            created_at=_STUB_NOW,
        )
    ]


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
    stub_alert = AlertOut(
        id=alert_id,
        client_id="lk-sneakers",
        rule_key="cpa_increase",
        rule_version="v1",
        metric_key="cpa_meta",
        entity="ADV+ Geral",
        observed_snapshot_refs=["snap_stub_124"],
        baseline_snapshot_refs=["snap_stub_098"],
        delta_pct=31.2,
        min_volume_met=True,
        severity=Level.HIGH,
        status=AlertStatus.OPEN,
        created_at=_STUB_NOW,
    )
    return AlertContext(
        alert=stub_alert,
        metrics=[
            MetricValue(metric_key="cpa_meta", value=63.0, unit="BRL",
                        value_status=ValueStatus.OK, certification_status=CertificationStatus.PROVISIONAL,
                        snapshot_ids=["snap_stub_124"]),
            MetricValue(metric_key="cpa_meta_baseline", value=48.0, unit="BRL",
                        value_status=ValueStatus.CERTIFIED, certification_status=CertificationStatus.CERTIFIED,
                        snapshot_ids=["snap_stub_098"]),
        ],
        changes=[
            ChangeOut(
                id="chg_stub_01",
                client_id="lk-sneakers",
                occurred_at=_STUB_NOW,
                channel="meta_ads",
                platform_account_id="act_stub",
                entity_type="ad",
                entity_name_at_time="Criativo Verão 01",
                change_type=ChangeType.CREATIVE_PAUSE,
                source=ChangeLogSource.AUTO_META,
                confidence=ChangeConfidence.CONFIRMED,
                match_status=MatchStatus.MATCHED,
                created_at=_STUB_NOW,
            )
        ],
        truth={"business_model": "ecommerce", "currency": "BRL"},
        health={
            "business":   SourceState.READY,
            "meta_ads":   SourceState.READY,
            "google_ads": SourceState.READY,
        },
    )


# ── GET /alert-rule-suggestions ───────────────────────────────────────────────

@router.get(
    "/alert-rule-suggestions",
    summary="Alert rule recalibration suggestions pending review",
    response_model=list[AlertRuleSuggestionOut],
)
async def list_alert_rule_suggestions(
    status: Optional[str] = Query("PENDING"),
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[AlertRuleSuggestionOut]:
    return [
        AlertRuleSuggestionOut(
            id="sug_stub_01",
            client_id="lk-sneakers",
            rule_key="cpa_increase",
            current_version="v1",
            proposed_params={"threshold_pct": 40, "min_conversions_baseline": 20},
            evidence=[{"alert_id": "alt_stub_01", "feedback": "NOISE"}],
            status=AlertRuleSuggestionStatus.PENDING,
            created_at=_STUB_NOW,
        )
    ]


# ── GET /system/health ────────────────────────────────────────────────────────

@router.get(
    "/system/health",
    summary="API, database, workers, queues and overdue jobs",
    response_model=SystemHealthOut,
)
async def system_health(
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> SystemHealthOut:
    return SystemHealthOut(
        database=ServiceStatus.UP,
        workers=[
            WorkerHealth(name="collect_worker", status=ServiceStatus.UP, last_heartbeat=_STUB_NOW),
            WorkerHealth(name="cert_worker",    status=ServiceStatus.UP, last_heartbeat=_STUB_NOW),
        ],
        dead_jobs=[],
        checked_at=_STUB_NOW,
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
    return [
        JobOut(
            id="job_stub_01",
            job_type="collect",
            run_key="collect:lk-sneakers:meta_ads:2026-10-03T12",
            status=JobStatus.SUCCEEDED,
            attempt=1,
            started_at=_STUB_NOW,
            finished_at=_STUB_NOW,
        )
    ]


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
    return JobOut(
        id=job_id,
        job_type="collect",
        run_key=f"collect:lk-sneakers:meta_ads:2026-10-03T12",
        status=JobStatus.SUCCEEDED,
        attempt=1,
        started_at=_STUB_NOW,
        finished_at=_STUB_NOW,
    )


# ── CCR-011: consultable intel chain ─────────────────────────────────────────
#
# Four additive GET routes that expose the FK chain:
#   alert → core_diagnoses.alert_id
#         → core_recommendations.diagnosis_id
#
# No relationship is inferred — FK chain only. Real DB reads in Etapa 4.

@router.get(
    "/alerts/{alert_id}/diagnoses",
    summary="Diagnoses linked to an alert (FK: core_diagnoses.alert_id)",
    response_model=list[DiagnosisOut],
)
async def get_alert_diagnoses(
    alert_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[DiagnosisOut]:
    return [
        DiagnosisOut(
            alert_id=alert_id,
            facts=[],
            related_changes=[],
            hypotheses=[],
            confidence=Level.MEDIUM,
            do_not_conclude=[],
            data_limitations=[],
            id="dia_stub_01",
            created_at=_STUB_NOW,
        )
    ]


@router.get(
    "/diagnoses/{diagnosis_id}",
    summary="Single diagnosis by ID",
    response_model=DiagnosisOut,
)
async def get_diagnosis(
    diagnosis_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> DiagnosisOut:
    return DiagnosisOut(
        alert_id="alt_stub_01",
        facts=[],
        related_changes=[],
        hypotheses=[],
        confidence=Level.MEDIUM,
        do_not_conclude=[],
        data_limitations=[],
        id=diagnosis_id,
        created_at=_STUB_NOW,
    )


@router.get(
    "/diagnoses/{diagnosis_id}/recommendations",
    summary="Recommendations linked to a diagnosis (FK: core_recommendations.diagnosis_id)",
    response_model=list[RecommendationOut],
)
async def get_diagnosis_recommendations(
    diagnosis_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> list[RecommendationOut]:
    return [
        RecommendationOut(
            diagnosis_id=diagnosis_id,
            recommendation="[stub] Reduzir budget do ADV+ Geral em 15%",
            priority=Level.HIGH,
            confidence=Level.MEDIUM,
            risk=Level.LOW,
            reversible=True,
            id="rec_stub_01",
            created_at=_STUB_NOW,
        )
    ]


@router.get(
    "/recommendations/{recommendation_id}",
    summary="Single recommendation by ID",
    response_model=RecommendationOut,
)
async def get_recommendation(
    recommendation_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_READ_ANY)] = None,
) -> RecommendationOut:
    return RecommendationOut(
        diagnosis_id="dia_stub_01",
        recommendation="[stub] Reduzir budget do ADV+ Geral em 15%",
        priority=Level.HIGH,
        confidence=Level.MEDIUM,
        risk=Level.LOW,
        reversible=True,
        id=recommendation_id,
        created_at=_STUB_NOW,
    )


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
    _auth: Annotated[AuthContext, Depends(require_scopes("agency_admin", "platform_web"))] = None,
) -> JobOut:
    return JobOut(
        id=f"job_replay_{job_id}",
        job_type="collect",
        run_key=f"collect:lk-sneakers:meta_ads:replay",
        status=JobStatus.QUEUED,
        attempt=0,
        replay_of=job_id,
    )
