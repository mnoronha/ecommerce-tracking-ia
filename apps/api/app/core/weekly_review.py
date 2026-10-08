"""
Weekly Account Review orchestrator.

Assembles the Hermes handoff contract for each client's completed weekly period.
Does NOT generate narrative, call Hermes, or invent analysis.

Flow per client:
  1. Load current-week snapshot (latest computed_at for period_end)
  2. Load prior-week snapshot (period_end - 7 days)
  3. Load active alerts (operational + performance)
  4. Build contract JSONB: metrics, WoW, data quality, alerts, target_truth
  5. Write to agency_report_contracts (delete+insert for idempotency)
  6. Write core_job_runs record RUNNING → SUCCEEDED | FAILED

Period definition (Mon-Sun, America/Sao_Paulo):
  period_end   = last Sunday (UTC date; scheduler fires at 08:00 UTC Mon = 05:00 BRT)
  period_start = period_end - 6 days
  comparison   = prior week (period_start/end - 7 days)

review_status:
  BLOCKED          — no snapshot for this period (pipeline didn't run or too early)
  PARTIAL          — snapshot found but ≥1 critical source (meta/google/business) degraded
  READY_FOR_REVIEW — snapshot found, all contracted ad sources OK or PARTIAL

Scheduler: Monday 08:00 UTC via main.py APScheduler.
Replay:    POST /clients/{client_id}/weekly-review/replay via Agency API router.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_JOB_TYPE   = "weekly_review"
_SCHEMA_VER = "core-weekly-review-v1"
_ERROR_TRUNC = 2000

# Sources whose degradation downgrades the review status to PARTIAL.
# ga4 degradation is informational only (NOT_CONTRACTED is normal for many clients).
_CRITICAL_SOURCES = frozenset({"meta_ads", "google_ads", "business"})
_DEGRADED_STATES  = frozenset({"ERROR", "ACCESS_MISSING", "PERMISSION_DENIED", "MISSING", "STALE"})


# ── Period helpers ─────────────────────────────────────────────────────────────

def _last_completed_week() -> tuple[date, date]:
    """
    Most recently completed Mon-Sun week, computed from UTC date.
    today.weekday(): Mon=0 … Sun=6 → days since last Sunday = weekday + 1.
    """
    today = date.today()
    period_end   = today - timedelta(days=today.weekday() + 1)
    period_start = period_end - timedelta(days=6)
    return period_start, period_end


def _iso_week_label(d: date) -> str:
    yr, wk, _ = d.isocalendar()
    return f"Semana {wk}/{yr}"


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
        logger.error("weekly_review: client list failed: %s", exc)
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
        logger.warning("weekly_review: client load failed %s: %s", client_id, exc)
        return None


def _load_snapshot(sb, client_id: str, period_end: date) -> Optional[dict]:
    """Latest snapshot for this client/period_end (may be multiple runs; take newest)."""
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
        logger.warning("weekly_review: snapshot load failed %s/%s: %s", client_id, period_end, exc)
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
    """Active (non-resolved) alerts for this client by UUID."""
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
        logger.warning("weekly_review: alerts load failed %s: %s", client_uuid, exc)
        return []


# ── Contract helpers ───────────────────────────────────────────────────────────

def _metrics_to_dict(metrics_jsonb: Any) -> dict[str, dict]:
    if not isinstance(metrics_jsonb, list):
        return {}
    return {m["metric_key"]: m for m in metrics_jsonb if isinstance(m, dict) and "metric_key" in m}


def _compute_wow(current: dict[str, dict], prior: dict[str, dict]) -> dict[str, dict]:
    """
    WoW % change for metrics present in both snapshots with OK or PARTIAL status.
    change_pct = (current - prior) / abs(prior) * 100, rounded to 1 decimal.
    None when prior is zero.
    """
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
        return ["snapshot_not_found: pipeline may not have run for this period"]
    lims: list[str] = []
    if not prior_found:
        lims.append("prior_snapshot_not_found: WoW comparison unavailable")
    for src, state in health.items():
        if state in _DEGRADED_STATES:
            lims.append(f"{src}: {state}")
    return lims


# ── Main contract builder ──────────────────────────────────────────────────────

def build_weekly_review_contract(
    client_id: str,
    period_start: date,
    period_end: date,
) -> dict:
    """
    Assemble the full Hermes handoff contract.
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

    prior_start = period_start - timedelta(days=7)
    prior_end   = period_end   - timedelta(days=7)

    snapshot       = _load_snapshot(sb, client_id, period_end)
    prior_snapshot = _load_snapshot(sb, client_id, prior_end)
    target_truth   = _load_target_truth(sb, client_id)
    active_alerts  = _load_active_alerts(sb, client_uuid)

    snapshot_found = snapshot is not None
    prior_found    = prior_snapshot is not None

    health: dict          = (snapshot or {}).get("health") or {}
    current_metrics: dict = _metrics_to_dict((snapshot or {}).get("metrics"))
    prior_metrics: dict   = _metrics_to_dict((prior_snapshot or {}).get("metrics"))
    wow_changes           = _compute_wow(current_metrics, prior_metrics) if (snapshot_found and prior_found) else {}

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

    # Target truth: expose only {metric_key: {target, unit}} — no internal config details
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
            "label": _iso_week_label(period_end),
        },
        "comparison_period": {
            "start": prior_start.isoformat(),
            "end":   prior_end.isoformat(),
            "label": _iso_week_label(prior_end),
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
        "wow_changes":     wow_changes,
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
        logger.warning("weekly_review: job insert failed %s: %s", client_id, exc)
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
        logger.warning("weekly_review: replay insert failed %s: %s", client_id, exc)
        return job_id


def _finish_job(sb, job_id: str, status: str, error: Optional[str] = None) -> None:
    try:
        payload: dict = {"status": status, "finished_at": datetime.now(timezone.utc).isoformat()}
        if error:
            payload["error"] = error[:_ERROR_TRUNC]
        sb.table("core_job_runs").update(payload).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("weekly_review: job finish failed %s: %s", job_id, exc)


def _write_contract(
    sb,
    client_id: str,
    period_start: date,
    period_end: date,
    contract: dict,
    source_run_id: Optional[str] = None,
) -> None:
    """Delete+insert for idempotency on (client_slug='weekly', period_end)."""
    prior_start    = period_start - timedelta(days=7)
    prior_end      = period_end   - timedelta(days=7)
    business_model = contract.get("client", {}).get("business_model", "ecommerce")
    now            = datetime.now(timezone.utc).isoformat()

    try:
        sb.table("agency_report_contracts").delete() \
            .eq("client_slug", client_id) \
            .eq("report_type", "weekly") \
            .eq("period_end",  period_end.isoformat()) \
            .execute()
    except Exception as exc:
        logger.warning("weekly_review: contract delete failed %s: %s", client_id, exc)

    sb.table("agency_report_contracts").insert({
        "client_slug":             client_id,
        "report_type":             "weekly",
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


# ── Public entrypoints ─────────────────────────────────────────────────────────

def run_weekly_review_for_client(
    client_id: str,
    period_start: date,
    period_end: date,
    job_id: Optional[str] = None,
) -> None:
    """
    Build and persist the weekly review contract for one client.
    job_id: if provided, transitions an existing QUEUED record (replay path).
             if None, creates a new RUNNING record.
    """
    sb = _get_db()

    if job_id:
        try:
            sb.table("core_job_runs").update({"status": "RUNNING"}).eq("id", job_id).execute()
        except Exception as exc:
            logger.warning("weekly_review: replay → RUNNING failed %s: %s", job_id, exc)
    else:
        job_id = _insert_running_job(sb, client_id, period_end)

    run_id = f"{_JOB_TYPE}:{client_id}:{period_end.isoformat()}"
    try:
        contract = build_weekly_review_contract(client_id, period_start, period_end)
        _write_contract(sb, client_id, period_start, period_end, contract, source_run_id=run_id)
        status = "SUCCEEDED"
        error  = None
        logger.info(
            "weekly_review: %s %s → %s (review_status=%s)",
            client_id, period_end, status, contract.get("review_status"),
        )
    except Exception as exc:
        status = "FAILED"
        error  = str(exc)[:_ERROR_TRUNC]
        logger.error("weekly_review: %s failed: %s", client_id, exc)

    if job_id:
        _finish_job(sb, job_id, status, error)


def run_weekly_review_all_clients() -> None:
    """
    Scheduler entry point.
    Determines the most recently completed Mon-Sun week and runs for all active clients.
    """
    period_start, period_end = _last_completed_week()
    sb = _get_db()
    clients = _load_active_clients(sb)
    logger.info(
        "weekly_review: running for %d clients — period %s→%s",
        len(clients), period_start, period_end,
    )
    for c in clients:
        cid = c["client_id"]
        try:
            run_weekly_review_for_client(cid, period_start, period_end)
        except Exception as exc:
            logger.error("weekly_review: unexpected error for %s: %s", cid, exc)


def queue_weekly_review_replay(
    sb,
    client_id: str,
    period_end: date,
    replay_of: str,
) -> str:
    """Queue a QUEUED replay job. Returns new job_id."""
    return _insert_queued_replay(sb, client_id, period_end, replay_of)


def execute_weekly_review_replay(
    job_id: str,
    client_id: str,
    period_end: date,
) -> None:
    """Background task body for replay — transitions QUEUED → RUNNING → SUCCEEDED|FAILED."""
    period_start = period_end - timedelta(days=6)
    run_weekly_review_for_client(client_id, period_start, period_end, job_id=job_id)
