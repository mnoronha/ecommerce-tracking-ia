"""
Agency API v1 — Admin diagnostic endpoints (Etapa 4 Bloco 2).

These endpoints are TEMPORARY scaffolding for production validation.
They require agency_admin scope and NEVER return credential values.

Routes:
  POST /agency/v1/admin/pipeline-run     — trigger pipeline + return full audit
  GET  /agency/v1/admin/pipeline-audit   — read last snapshot audit without re-running
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .auth import SCOPE_ADMIN_ONLY, AuthContext

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/agency/v1/admin", tags=["agency-v1-admin"])


# ── Request / Response models ─────────────────────────────────────────────────

class PipelineRunRequest(BaseModel):
    client_id: str
    period_start: str   # YYYY-MM-DD
    period_end:   str   # YYYY-MM-DD
    view: str = "live"


class DQCheckOut(BaseModel):
    name:   str
    passed: bool
    detail: Optional[str] = None


class SourceAudit(BaseModel):
    source_system:       str
    source_key:          str
    source_state:        str
    value_status:        str
    reconciliation:      Optional[dict[str, Any]] = None
    dqg_checks:          list[DQCheckOut]
    collected_aggregates: dict[str, Any]


class MetricOut(BaseModel):
    metric_key:           str
    value:                Optional[float]
    unit:                 Optional[str]
    currency:             Optional[str]
    value_status:         str
    certification_status: Optional[str]
    domain:               Optional[str]


class SnapshotAudit(BaseModel):
    snapshot_id:          str
    schema_version:       str = "1.1"   # not stored as DB column; returned as constant
    period_start:         str
    period_end:           str
    view:                 str
    computed_at:          str
    client_truth_version: int
    target_truth_version: int
    metric_count:         int
    metric_keys:          list[str]
    certification_status: Optional[str]
    total_snapshots_for_period: int = 0


class DataSourceAudit(BaseModel):
    source_key:           str
    source_system:        str
    source_state:         str
    last_attempt_at:      Optional[str]
    last_data_at:         Optional[str]
    last_validated_at:    Optional[str]
    last_reconciled_at:   Optional[str]
    reconciliation_state: Optional[str]
    last_error:           Optional[str]


_PIPELINE_TAG = "bloco2-fix5"  # bump on each deploy to verify Railway is running new code


class PipelineRunResponse(BaseModel):
    run_id:               str
    client_id:            str
    period_start:         str
    period_end:           str
    view:                 str
    success:              bool
    snapshot_id:          Optional[str]
    error:                Optional[str]
    sources:              list[SourceAudit]
    metrics:              list[MetricOut]
    health:               dict[str, str]
    snapshot_audit:       Optional[SnapshotAudit] = None
    snapshot_audit_error: Optional[str] = None   # diagnostic: why snapshot_audit is null
    data_sources:         list[DataSourceAudit] = []
    ran_at:               str = ""
    pipeline_tag:         str = _PIPELINE_TAG    # non-sensitive deploy marker


# ── POST /agency/v1/admin/pipeline-run ────────────────────────────────────────

@admin_router.post(
    "/pipeline-run",
    summary="[Admin] Trigger Core pipeline and return full audit (no secrets)",
    response_model=PipelineRunResponse,
)
def trigger_pipeline_run(
    body: PipelineRunRequest,
    _auth: Annotated[AuthContext, Depends(SCOPE_ADMIN_ONLY)],
) -> PipelineRunResponse:
    try:
        p_start = date.fromisoformat(body.period_start)
        p_end   = date.fromisoformat(body.period_end)
    except ValueError as exc:
        raise HTTPException(400, f"invalid date: {exc}")

    from ...core.pipeline import run_pipeline

    result = run_pipeline(body.client_id, p_start, p_end, view=body.view)

    sources_out: list[SourceAudit] = []
    for sr in result.sources:
        sources_out.append(SourceAudit(
            source_system=sr.source_system,
            source_key=sr.source_key,
            source_state=sr.dqg.source_state,
            value_status=sr.dqg.value_status,
            reconciliation=sr.reconciliation,
            dqg_checks=[
                DQCheckOut(name=c.name, passed=c.passed, detail=c.detail)
                for c in sr.dqg.checks
            ],
            collected_aggregates=sr.collection.aggregates if sr.collection else {},
        ))

    metrics_out: list[MetricOut] = []
    for m in result.metrics:
        metrics_out.append(MetricOut(
            metric_key=m.get("metric_key", ""),
            value=m.get("value"),
            unit=m.get("unit"),
            currency=m.get("currency"),
            value_status=m.get("value_status", "UNKNOWN"),
            certification_status=m.get("certification_status"),
            domain=m.get("domain"),
        ))

    # Read full core_data_sources state from DB
    data_sources_out: list[DataSourceAudit] = []
    snap_audit: Optional[SnapshotAudit] = None
    snap_audit_error: Optional[str] = None
    try:
        from ...database import get_supabase
        db = get_supabase()

        ds_rows = (
            db.table("core_data_sources")
            .select(
                "source_key, source_system, source_state, "
                "last_attempt_at, last_data_at, last_validated_at, "
                "last_reconciled_at, reconciliation_state, last_error"
            )
            .eq("client_id", body.client_id)
            .execute()
        )
        for row in (ds_rows.data or []):
            data_sources_out.append(DataSourceAudit(
                source_key=row.get("source_key", ""),
                source_system=row.get("source_system", ""),
                source_state=row.get("source_state", ""),
                last_attempt_at=row.get("last_attempt_at"),
                last_data_at=row.get("last_data_at"),
                last_validated_at=row.get("last_validated_at"),
                last_reconciled_at=row.get("last_reconciled_at"),
                reconciliation_state=row.get("reconciliation_state"),
                last_error=row.get("last_error"),
            ))

        if result.snapshot_id:
            # schema_version is NOT a column in core_metric_snapshots — omit from select.
            # It is returned as the hardcoded constant "1.1" in SnapshotAudit.
            snap_rows = (
                db.table("core_metric_snapshots")
                .select(
                    "id, period_start, period_end, view, "
                    "computed_at, client_truth_version, target_truth_version, metrics"
                )
                .eq("id", result.snapshot_id)
                .limit(1)
                .execute()
            )
            if snap_rows.data:
                s = snap_rows.data[0]
                raw_metrics: list[dict] = s.get("metrics") or []
                metric_keys = [m.get("metric_key", "") for m in raw_metrics]
                cert_statuses = [
                    m.get("certification_status") for m in raw_metrics
                    if m.get("certification_status")
                ]
                # Count ALL snapshots for this client+period for append-only audit
                count_r = (
                    db.table("core_metric_snapshots")
                    .select("id", count="exact")
                    .eq("client_id", body.client_id)
                    .eq("period_start", str(s.get("period_start", "")))
                    .eq("period_end",   str(s.get("period_end", "")))
                    .execute()
                )
                total_snaps = count_r.count if count_r.count is not None else len(count_r.data or [])
                snap_audit = SnapshotAudit(
                    snapshot_id=s["id"],
                    period_start=str(s.get("period_start", "")),
                    period_end=str(s.get("period_end", "")),
                    view=s.get("view", ""),
                    computed_at=str(s.get("computed_at", "")),
                    client_truth_version=s.get("client_truth_version", 0),
                    target_truth_version=s.get("target_truth_version", 0),
                    metric_count=len(raw_metrics),
                    metric_keys=metric_keys,
                    certification_status=cert_statuses[0] if cert_statuses else None,
                    total_snapshots_for_period=total_snaps,
                )
            else:
                snap_audit_error = f"snapshot {result.snapshot_id} not found in DB"
                logger.warning("pipeline-run: %s", snap_audit_error)
    except Exception as exc:
        snap_audit_error = f"audit DB read failed: {exc}"
        logger.error("pipeline-run audit DB read failed: %s", exc)

    return PipelineRunResponse(
        run_id=result.run_id,
        client_id=result.client_id,
        period_start=str(result.period_start),
        period_end=str(result.period_end),
        view=body.view,
        success=result.success,
        snapshot_id=result.snapshot_id,
        error=result.error,
        sources=sources_out,
        metrics=metrics_out,
        health=result.health,
        snapshot_audit=snap_audit,
        snapshot_audit_error=snap_audit_error,
        data_sources=data_sources_out,
        ran_at=datetime.now(timezone.utc).isoformat(),
    )


# ── GET /agency/v1/admin/pipeline-audit ───────────────────────────────────────

@admin_router.get(
    "/pipeline-audit",
    summary="[Admin] Read latest snapshot + data_sources state without re-running",
    response_model=dict,
)
def pipeline_audit(
    client_id: str = Query(...),
    period_start: Optional[str] = Query(None),
    period_end: Optional[str] = Query(None),
    _auth: Annotated[AuthContext, Depends(SCOPE_ADMIN_ONLY)] = None,
) -> dict:
    from ...database import get_supabase
    db = get_supabase()

    ds_rows = (
        db.table("core_data_sources")
        .select(
            "source_key, source_system, source_state, "
            "last_attempt_at, last_data_at, last_validated_at, "
            "last_reconciled_at, reconciliation_state, last_error, updated_at"
        )
        .eq("client_id", client_id)
        .execute()
    )

    snap_q = (
        db.table("core_metric_snapshots")
        .select("id, period_start, period_end, view, computed_at, metrics, health, client_truth_version")
        .eq("client_id", client_id)
        .order("computed_at", desc=True)
        .limit(5)
    )
    if period_start:
        snap_q = snap_q.eq("period_start", period_start)
    if period_end:
        snap_q = snap_q.eq("period_end", period_end)
    snap_rows = snap_q.execute()

    snaps_out = []
    for s in (snap_rows.data or []):
        raw_m: list[dict] = s.get("metrics") or []
        snaps_out.append({
            "id":            s["id"],
            "schema_version": "1.1",   # not a DB column; constant
            "period":         f"{s.get('period_start')} -> {s.get('period_end')}",
            "view":           s.get("view"),
            "computed_at":    str(s.get("computed_at")),
            "metric_count":   len(raw_m),
            "metric_keys":    [m.get("metric_key") for m in raw_m],
            "health":         s.get("health"),
            "metrics_summary": [
                {
                    "metric_key":   m.get("metric_key"),
                    "value":        m.get("value"),
                    "unit":         m.get("unit"),
                    "value_status": m.get("value_status"),
                    "cert":         m.get("certification_status"),
                }
                for m in raw_m
            ],
        })

    return {
        "client_id":   client_id,
        "data_sources": ds_rows.data or [],
        "snapshots":    snaps_out,
        "queried_at":   datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Notion task sync — admin endpoints (Phase NOTION REAL)
#
# All endpoints require agency_admin scope.
# NOTION_API_KEY is NEVER returned in responses.
# ══════════════════════════════════════════════════════════════════════════════

class NotionSyncTaskOut(BaseModel):
    task_id:          str
    status:           str          # synced | error | CONFIG_REQUIRED
    external_task_id: Optional[str] = None
    operation:        Optional[str] = None  # create | update
    error:            Optional[str] = None
    missing:          Optional[list[str]] = None
    ran_at:           str


class NotionSyncPendingOut(BaseModel):
    status:  str
    total:   int = 0
    synced:  int = 0
    errors:  int = 0
    skipped: int = 0
    missing: Optional[list[str]] = None
    ran_at:  str


class NotionSyncStatusOut(BaseModel):
    total_tasks:      int
    synced:           int
    pending:          int
    error_count:      int
    none_count:       int
    config_ok:        bool
    missing_config:   list[str]
    queried_at:       str


@admin_router.post(
    "/notion/sync/task/{task_id}",
    response_model=NotionSyncTaskOut,
    summary="[Admin] Manually push one Core task to Notion",
)
def admin_notion_sync_task(
    task_id: str,
    _auth: Annotated[AuthContext, Depends(SCOPE_ADMIN_ONLY)],
) -> NotionSyncTaskOut:
    from ...core.notion_adapter import NotionTaskAdapter
    from ...database import get_supabase

    adapter = NotionTaskAdapter(supabase=get_supabase())
    result  = adapter.sync_task_to_notion(task_id)
    return NotionSyncTaskOut(
        task_id          = task_id,
        status           = result.get("status", "error"),
        external_task_id = result.get("external_task_id"),
        operation        = result.get("operation"),
        error            = result.get("error"),
        missing          = result.get("missing"),
        ran_at           = datetime.now(timezone.utc).isoformat(),
    )


@admin_router.post(
    "/notion/sync/pending",
    response_model=NotionSyncPendingOut,
    summary="[Admin] Sync all pending Core tasks to Notion (batch, max 50)",
)
def admin_notion_sync_pending(
    _auth: Annotated[AuthContext, Depends(SCOPE_ADMIN_ONLY)],
    limit: int = Query(default=50, ge=1, le=200),
) -> NotionSyncPendingOut:
    from ...core.notion_adapter import NotionTaskAdapter
    from ...database import get_supabase

    adapter = NotionTaskAdapter(supabase=get_supabase())
    result  = adapter.sync_pending_tasks_to_notion(limit=limit)
    return NotionSyncPendingOut(
        status  = result.get("status", "error"),
        total   = result.get("total", 0),
        synced  = result.get("synced", 0),
        errors  = result.get("errors", 0),
        skipped = result.get("skipped", 0),
        missing = result.get("missing"),
        ran_at  = datetime.now(timezone.utc).isoformat(),
    )


@admin_router.get(
    "/notion/sync/status",
    response_model=NotionSyncStatusOut,
    summary="[Admin] Notion sync health overview — counts by sync_state",
)
def admin_notion_sync_status(
    _auth: Annotated[AuthContext, Depends(SCOPE_ADMIN_ONLY)],
) -> NotionSyncStatusOut:
    from ...database import get_supabase
    from ...config import settings as _settings

    db = get_supabase()
    try:
        res = db.table("core_tasks").select("sync_state").execute()
        rows = res.data or []
    except Exception as exc:
        raise HTTPException(503, detail=f"db error: {exc}")

    counts: dict[str, int] = {}
    for r in rows:
        st = r.get("sync_state", "none")
        counts[st] = counts.get(st, 0) + 1

    missing: list[str] = []
    if not _settings.NOTION_API_KEY:
        missing.append("NOTION_API_KEY")
    if not _settings.NOTION_TASKS_DATABASE_ID:
        missing.append("NOTION_TASKS_DATABASE_ID")

    return NotionSyncStatusOut(
        total_tasks    = len(rows),
        synced         = counts.get("synced", 0),
        pending        = counts.get("pending", 0),
        error_count    = counts.get("error", 0),
        none_count     = counts.get("none", 0),
        config_ok      = len(missing) == 0,
        missing_config = missing,
        queried_at     = datetime.now(timezone.utc).isoformat(),
    )
