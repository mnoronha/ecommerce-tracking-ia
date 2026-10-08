"""
Monthly Account Review orchestrator.

Assembles the Hermes handoff contract for each client's completed calendar month.
Does NOT generate narrative, call Hermes, or invent analysis.

Flow per client:
  1. Load month's final snapshot (period_end = last day of month, from daily pipeline)
  2. Load prior month's final snapshot (for MoM comparison)
  3. Load active alerts
  4. Build contract JSONB: metrics, MoM, data quality, alerts, target_truth
  5. Write to agency_report_contracts (delete+insert for idempotency)
  6. Create core_report_narratives DRAFT via report_draft_builder
  7. Write core_job_runs record RUNNING → SUCCEEDED | FAILED

Period definition:
  Scheduler fires on 1st of month at 08:30 UTC (after daily core pipeline 07:15).
  period_end   = last day of previous month (daily pipeline wrote this snapshot on 1st)
  period_start = first day of previous month
  comparison   = the month before that (MoM)

Scheduler: 1st of month 08:30 UTC via main.py APScheduler.
Replay:    POST /clients/{client_id}/monthly-review/replay via Agency API router.
"""
from __future__ import annotations

import calendar
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_JOB_TYPE    = "monthly_review"
_SCHEMA_VER  = "core-monthly-review-v1"
_ERROR_TRUNC = 2000

_CRITICAL_SOURCES = frozenset({"meta_ads", "google_ads", "business"})
_DEGRADED_STATES  = frozenset({"ERROR", "ACCESS_MISSING", "PERMISSION_DENIED", "MISSING", "STALE"})

_MONTH_PT = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


# ── Period helpers ─────────────────────────────────────────────────────────────

def _last_completed_month() -> tuple[int, int]:
    """
    Returns (year, month) for the most recently completed calendar month.
    Scheduler fires on 1st → previous month just closed.
    """
    today = date.today()
    last_of_prev = date(today.year, today.month, 1) - timedelta(days=1)
    return last_of_prev.year, last_of_prev.month


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    first = date(year, month, 1)
    last  = date(year, month, calendar.monthrange(year, month)[1])
    return first, last


def _prior_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _month_label(year: int, month: int) -> str:
    return f"{_MONTH_PT[month - 1]} {year}"


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _get_db():
    from ..database import get_supabase
    return get_supabase()


def _load_active_clients(sb) -> list[dict]:
    try:
        res = (
            sb.table("clients")
            .select("id, client_id, name, business_model")
            .eq("is_active", True)
            .execute()
        )
        return [c for c in (res.data or []) if c.get("client_id")]
    except Exception as exc:
        logger.error("monthly_review: client list failed: %s", exc)
        return []


def _load_client(sb, client_id: str) -> Optional[dict]:
    try:
        res = (
            sb.table("clients")
            .select("id, client_id, name, business_model")
            .eq("client_id", client_id)
            .single()
            .execute()
        )
        return res.data
    except Exception as exc:
        logger.warning("monthly_review: client load failed %s: %s", client_id, exc)
        return None


def _load_snapshot(sb, client_id: str, period_end: date) -> Optional[dict]:
    try:
        res = (
            sb.table("core_metric_snapshots")
            .select("id, metrics, health, computed_at, period_start, period_end")
            .eq("client_id", client_id)
            .eq("period_end", period_end.isoformat())
            .order("computed_at", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as exc:
        logger.warning("monthly_review: snapshot load failed %s/%s: %s", client_id, period_end, exc)
        return None


def _load_target_truth(sb, client_id: str) -> dict:
    try:
        res = (
            sb.table("core_client_truth")
            .select("target_truth")
            .eq("client_id", client_id)
            .order("valid_from", desc=True)
            .limit(1)
            .execute()
        )
        if res.data:
            tt = res.data[0].get("target_truth") or {}
            return tt if isinstance(tt, dict) else {}
    except Exception:
        pass
    return {}


def _load_active_alerts(sb, client_uuid: str) -> list[dict]:
    try:
        res = (
            sb.table("alerts")
            .select("id, type, title, message, severity, fingerprint, occurrence_count, created_at")
            .eq("client_id", client_uuid)
            .eq("is_resolved", False)
            .order("created_at", desc=False)
            .execute()
        )
        return [
            {
                "id":               str(r["id"]),
                "type":             r.get("type"),
                "severity":         r.get("severity"),
                "title":            r.get("title"),
                "message":          r.get("message"),
                "fingerprint":      r.get("fingerprint"),
                "occurrence_count": r.get("occurrence_count", 1),
                "triggered_at":     r.get("created_at"),
            }
            for r in (res.data or [])
        ]
    except Exception as exc:
        logger.warning("monthly_review: alerts load failed %s: %s", client_uuid, exc)
        return []


# ── Contract helpers ───────────────────────────────────────────────────────────

def _metrics_to_dict(metrics_jsonb: Any) -> dict[str, dict]:
    if not isinstance(metrics_jsonb, list):
        return {}
    return {m["metric_key"]: m for m in metrics_jsonb if isinstance(m, dict) and "metric_key" in m}


def _compute_mom(current: dict[str, dict], prior: dict[str, dict]) -> dict[str, dict]:
    """MoM % change for metrics present in both snapshots with OK or PARTIAL status."""
    _USABLE = frozenset({"OK", "PARTIAL"})
    changes: dict[str, dict] = {}
    for key, cur_m in current.items():
        if cur_m.get("value_status") not in _USABLE:
            continue
        cur_val = cur_m.get("value")
        if cur_val is None:
            continue
        prior_m = prior.get(key)
        if not prior_m or prior_m.get("value_status") not in _USABLE:
            continue
        prior_val = prior_m.get("value")
        if prior_val is None:
            continue
        change_pct = (
            None if prior_val == 0
            else round((cur_val - prior_val) / abs(prior_val) * 100, 1)
        )
        changes[key] = {
            "current":    cur_val,
            "prior":      prior_val,
            "change_pct": change_pct,
        }
    return changes


def _overall_quality(health: dict, snapshot_found: bool) -> str:
    if not snapshot_found:
        return "BLOCKED"
    degraded = [s for s in _CRITICAL_SOURCES if health.get(s) in _DEGRADED_STATES]
    return "PARTIAL" if degraded else "COMPLETE"


def _build_limitations(health: dict, snapshot_found: bool, prior_found: bool) -> list[str]:
    if not snapshot_found:
        return ["snapshot_not_found: daily pipeline may not have run for period_end"]
    lims: list[str] = []
    if not prior_found:
        lims.append("prior_snapshot_not_found: MoM comparison unavailable")
    for src, state in health.items():
        if state in _DEGRADED_STATES:
            lims.append(f"{src}: {state}")
    return lims


# ── Main contract builder ──────────────────────────────────────────────────────

def build_monthly_review_contract(
    client_id: str,
    year: int,
    month: int,
) -> dict:
    """
    Assemble the full Hermes handoff contract for a completed calendar month.
    Pure data — no narrative generated, no Hermes call made.
    """
    sb = _get_db()

    client = _load_client(sb, client_id)
    if not client:
        return {
            "schema_version": _SCHEMA_VER,
            "review_status":  "BLOCKED",
            "error":          f"client not found: {client_id}",
        }

    client_uuid    = client["id"]
    business_model = client.get("business_model") or "ecommerce"

    period_start, period_end = _month_bounds(year, month)
    prior_year, prior_month  = _prior_month(year, month)
    prior_start, prior_end   = _month_bounds(prior_year, prior_month)

    snapshot       = _load_snapshot(sb, client_id, period_end)
    prior_snapshot = _load_snapshot(sb, client_id, prior_end)
    target_truth   = _load_target_truth(sb, client_id)
    active_alerts  = _load_active_alerts(sb, client_uuid)

    snapshot_found = snapshot is not None
    prior_found    = prior_snapshot is not None

    health: dict          = (snapshot or {}).get("health") or {}
    current_metrics: dict = _metrics_to_dict((snapshot or {}).get("metrics"))
    prior_metrics: dict   = _metrics_to_dict((prior_snapshot or {}).get("metrics"))
    mom_changes = _compute_mom(current_metrics, prior_metrics) if (snapshot_found and prior_found) else {}

    overall     = _overall_quality(health, snapshot_found)
    limitations = _build_limitations(health, snapshot_found, prior_found)

    def _slim_current(m: dict) -> dict:
        return {
            "value":        m.get("value"),
            "value_status": m.get("value_status"),
            "unit":         m.get("unit"),
            "target":       m.get("target"),
            "target_ratio": m.get("target_ratio"),
        }

    def _slim_prior(m: dict) -> dict:
        return {"value": m.get("value"), "value_status": m.get("value_status")}

    tt_slim = {
        k: {"target": v.get("target"), "unit": v.get("unit")}
        for k, v in target_truth.items()
        if isinstance(v, dict) and "target" in v
    }

    return {
        "schema_version": _SCHEMA_VER,
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "review_status":  overall,
        "client": {
            "client_id":      client_id,
            "name":           client.get("name"),
            "business_model": business_model,
        },
        "period": {
            "start": period_start.isoformat(),
            "end":   period_end.isoformat(),
            "label": _month_label(year, month),
            "year":  year,
            "month": month,
        },
        "comparison_period": {
            "start": prior_start.isoformat(),
            "end":   prior_end.isoformat(),
            "label": _month_label(prior_year, prior_month),
        },
        "data_quality": {
            "sources":              health,
            "snapshot_found":       snapshot_found,
            "prior_snapshot_found": prior_found,
            "limitations":          limitations,
            "overall":              overall,
        },
        "metrics_current": {k: _slim_current(v) for k, v in current_metrics.items()},
        "metrics_prior":   {k: _slim_prior(v)   for k, v in prior_metrics.items()},
        "mom_changes":     mom_changes,
        "target_truth":    tt_slim,
        "active_alerts":   active_alerts,
        "source_snapshot_id":       str(snapshot["id"])       if snapshot       else None,
        "source_prior_snapshot_id": str(prior_snapshot["id"]) if prior_snapshot else None,
    }


# ── Job tracking helpers ───────────────────────────────────────────────────────

def _insert_running_job(sb, client_id: str, period_end: date) -> Optional[str]:
    run_key = f"{_JOB_TYPE}:{client_id}:{period_end.isoformat()}"
    job_id  = str(uuid.uuid4())
    try:
        res = sb.table("core_job_runs").insert({
            "id":         job_id,
            "job_type":   _JOB_TYPE,
            "run_key":    run_key,
            "status":     "RUNNING",
            "attempt":    1,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        return res.data[0]["id"] if res.data else job_id
    except Exception as exc:
        logger.warning("monthly_review: job insert failed %s: %s", client_id, exc)
        return None


def _insert_queued_replay(sb, client_id: str, period_end: date, replay_of: str) -> str:
    run_key = f"{_JOB_TYPE}:{client_id}:{period_end.isoformat()}:replay:{replay_of[:8]}"
    job_id  = str(uuid.uuid4())
    try:
        res = sb.table("core_job_runs").insert({
            "id":         job_id,
            "job_type":   _JOB_TYPE,
            "run_key":    run_key,
            "status":     "QUEUED",
            "attempt":    1,
            "replay_of":  replay_of,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        return res.data[0]["id"] if res.data else job_id
    except Exception as exc:
        logger.warning("monthly_review: replay insert failed %s: %s", client_id, exc)
        return job_id


def _finish_job(sb, job_id: str, status: str, error: Optional[str] = None) -> None:
    try:
        payload: dict = {"status": status, "finished_at": datetime.now(timezone.utc).isoformat()}
        if error:
            payload["error"] = error[:_ERROR_TRUNC]
        sb.table("core_job_runs").update(payload).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("monthly_review: job finish failed %s: %s", job_id, exc)


def _write_contract(
    sb,
    client_id: str,
    year: int,
    month: int,
    contract: dict,
    source_run_id: Optional[str] = None,
) -> Optional[str]:
    """Delete+insert for idempotency on (client_slug, report_type='monthly', period_end)."""
    period_start, period_end   = _month_bounds(year, month)
    py, pm                     = _prior_month(year, month)
    prior_start, prior_end     = _month_bounds(py, pm)
    business_model = contract.get("client", {}).get("business_model", "ecommerce")
    now = datetime.now(timezone.utc).isoformat()

    try:
        sb.table("agency_report_contracts").delete() \
            .eq("client_slug", client_id) \
            .eq("report_type", "monthly") \
            .eq("period_end",  period_end.isoformat()) \
            .execute()
    except Exception as exc:
        logger.warning("monthly_review: contract delete failed %s: %s", client_id, exc)

    try:
        res = sb.table("agency_report_contracts").insert({
            "client_slug":             client_id,
            "report_type":             "monthly",
            "business_model":          business_model,
            "period_start":            period_start.isoformat(),
            "period_end":              period_end.isoformat(),
            "comparison_period_start": prior_start.isoformat(),
            "comparison_period_end":   prior_end.isoformat(),
            "schema_version":          _SCHEMA_VER,
            "source_run_id":           source_run_id,
            "generated_at":            now,
            "contract":                contract,
            "created_at":              now,
            "updated_at":              now,
        }).execute()
        row = res.data[0] if res.data else None
        return str(row["id"]) if row else None
    except Exception as exc:
        logger.error("monthly_review: contract insert failed %s: %s", client_id, exc)
        return None


# ── Public entrypoints ─────────────────────────────────────────────────────────

def run_monthly_review_for_client(
    client_id: str,
    year: int,
    month: int,
    job_id: Optional[str] = None,
) -> None:
    """
    Build and persist the monthly review contract for one client.
    job_id: if provided, transitions an existing QUEUED record (replay path).
    """
    _, period_end = _month_bounds(year, month)
    sb = _get_db()

    if job_id:
        try:
            sb.table("core_job_runs").update({"status": "RUNNING"}).eq("id", job_id).execute()
        except Exception as exc:
            logger.warning("monthly_review: replay → RUNNING failed %s: %s", job_id, exc)
    else:
        job_id = _insert_running_job(sb, client_id, period_end)

    run_id = f"{_JOB_TYPE}:{client_id}:{period_end.isoformat()}"
    try:
        contract    = build_monthly_review_contract(client_id, year, month)
        contract_id = _write_contract(sb, client_id, year, month, contract, source_run_id=run_id)
        if contract_id:
            from .report_draft_builder import create_narrative_draft
            create_narrative_draft(sb, contract_id, "monthly", contract.get("review_status", "BLOCKED"))
        status = "SUCCEEDED"
        error  = None
        logger.info(
            "monthly_review: %s %d-%02d → %s (review_status=%s)",
            client_id, year, month, status, contract.get("review_status"),
        )
    except Exception as exc:
        status = "FAILED"
        error  = str(exc)[:_ERROR_TRUNC]
        logger.error("monthly_review: %s failed: %s", client_id, exc)

    if job_id:
        _finish_job(sb, job_id, status, error)


def run_monthly_review_all_clients() -> None:
    """
    Scheduler entry point.
    Fires on 1st of month after the daily pipeline closes the previous month's snapshot.
    """
    year, month = _last_completed_month()
    sb = _get_db()
    clients = _load_active_clients(sb)
    logger.info(
        "monthly_review: running for %d clients — %s",
        len(clients), _month_label(year, month),
    )
    for c in clients:
        cid = c["client_id"]
        try:
            run_monthly_review_for_client(cid, year, month)
        except Exception as exc:
            logger.error("monthly_review: unexpected error for %s: %s", cid, exc)


def queue_monthly_review_replay(
    sb,
    client_id: str,
    year: int,
    month: int,
    replay_of: str,
) -> str:
    _, period_end = _month_bounds(year, month)
    return _insert_queued_replay(sb, client_id, period_end, replay_of)


def execute_monthly_review_replay(
    job_id: str,
    client_id: str,
    year: int,
    month: int,
) -> None:
    run_monthly_review_for_client(client_id, year, month, job_id=job_id)
