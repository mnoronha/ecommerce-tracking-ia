"""
Core operational alert evaluator.

Detects failures from core_data_sources and core_job_runs and upserts
records into the existing `alerts` table using fingerprint-based dedup.

Rules:
  SOURCE_STALE          — source_state == 'STALE'
  SOURCE_ERROR          — source_state in ERROR family
  RECONCILIATION_FAILED — reconciliation_state not OK/null
  JOB_FAILED            — FAILED jobs in last 24h for client
  JOB_STUCK             — RUNNING > 120 min or QUEUED > 240 min
  PIPELINE_NOT_RUN      — no SUCCEEDED core_pipeline job in last 36h (system-wide)

evaluate_operational_alerts(client_id) — called after each pipeline run.
evaluate_system_alerts()               — called periodically (every 30 min).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

CORE_ALERT_TYPES = frozenset({
    "JOB_FAILED",
    "JOB_STUCK",
    "SOURCE_STALE",
    "SOURCE_ERROR",
    "RECONCILIATION_FAILED",
    "PIPELINE_NOT_RUN",
})

_CLIENT_ALERT_TYPES = CORE_ALERT_TYPES - {"PIPELINE_NOT_RUN"}

_SEVERITY: dict[str, str] = {
    "JOB_FAILED":            "HIGH",
    "JOB_STUCK":             "HIGH",
    "SOURCE_ERROR":          "HIGH",
    "RECONCILIATION_FAILED": "HIGH",
    "SOURCE_STALE":          "MEDIUM",
    "PIPELINE_NOT_RUN":      "HIGH",
}

_ERROR_SOURCE_STATES = frozenset({"ERROR", "ACCESS_MISSING", "PERMISSION_DENIED", "MISSING"})

_STUCK_RUNNING_MIN  = 120   # RUNNING > 2h = stuck
_STUCK_QUEUED_MIN   = 240   # QUEUED > 4h = stuck
_PIPELINE_MAX_HOURS = 36    # no SUCCEEDED run in 36h = PIPELINE_NOT_RUN


# ── Helpers ───────────────────────────────────────────────────────────────────

def _upsert_alert(
    sb,
    fingerprint: str,
    alert_type: str,
    client_uuid: Optional[str],
    title: str,
    message: str,
    evidence: dict,
    existing_open: dict[str, dict],
) -> None:
    if fingerprint in existing_open:
        try:
            sb.table("alerts").update({
                "occurrence_count": existing_open[fingerprint]["occurrence_count"] + 1,
                "data": evidence,
            }).eq("id", existing_open[fingerprint]["id"]).execute()
        except Exception as exc:
            logger.warning("alert_evaluator: upsert update failed %s: %s", fingerprint, exc)
    else:
        try:
            sb.table("alerts").insert({
                "type":             alert_type,
                "client_id":        client_uuid,
                "title":            title,
                "message":          message,
                "severity":         _SEVERITY[alert_type],
                "status":           "OPEN",
                "fingerprint":      fingerprint,
                "data":             evidence,
                "occurrence_count": 1,
            }).execute()
        except Exception as exc:
            logger.warning("alert_evaluator: insert failed %s: %s", fingerprint, exc)


def _resolve_stale_open(
    sb,
    existing_open: dict[str, dict],
    current_fingerprints: set[str],
    now: datetime,
) -> int:
    resolved = 0
    for fp, row in list(existing_open.items()):
        if fp not in current_fingerprints:
            try:
                sb.table("alerts").update({
                    "status":      "RESOLVED",
                    "resolved_at": now.isoformat(),
                }).eq("id", row["id"]).execute()
                resolved += 1
            except Exception as exc:
                logger.warning("alert_evaluator: resolve failed %s: %s", fp, exc)
    return resolved


def _parse_dt(v: object) -> Optional[datetime]:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ── Per-client evaluation ─────────────────────────────────────────────────────

def evaluate_operational_alerts(client_id: str) -> dict:
    """
    Evaluate all client-scoped operational alerts.
    Called after each pipeline run in core_scheduler.
    """
    from ..database import get_supabase

    sb  = get_supabase()
    now = datetime.now(timezone.utc)
    today_iso = now.date().isoformat()

    # Resolve client UUID
    try:
        res = (
            sb.table("clients")
            .select("id")
            .eq("client_id", client_id)
            .limit(1)
            .execute()
        )
        client_uuid: Optional[str] = res.data[0]["id"] if (res and res.data) else None
    except Exception as exc:
        logger.error("alert_evaluator: client lookup failed %s: %s", client_id, exc)
        return {"error": str(exc)}

    if not client_uuid:
        logger.warning("alert_evaluator: client %s not found", client_id)
        return {"error": "client not found"}

    # Load all existing OPEN Core alerts for this client
    try:
        open_res = (
            sb.table("alerts")
            .select("id, type, fingerprint, occurrence_count")
            .eq("client_id", client_uuid)
            .in_("type", list(_CLIENT_ALERT_TYPES))
            .eq("status", "OPEN")
            .is_("resolved_at", "null")
            .execute()
        )
        existing_open: dict[str, dict] = {
            r["fingerprint"]: r
            for r in (open_res.data or [])
            if r.get("fingerprint")
        }
    except Exception as exc:
        logger.error("alert_evaluator: open alert load failed %s: %s", client_id, exc)
        existing_open = {}

    current_fingerprints: set[str] = set()

    # ── SOURCE_STALE / SOURCE_ERROR / RECONCILIATION_FAILED ──────────────────

    try:
        sources = (
            sb.table("core_data_sources")
            .select("source_key, source_system, source_state, reconciliation_state, last_data_at, last_error")
            .eq("client_id", client_id)   # core_data_sources.client_id is TEXT (slug)
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("alert_evaluator: sources load failed %s: %s", client_id, exc)
        sources = []

    for s in sources:
        sk    = s.get("source_key") or s.get("source_system", "unknown")
        state = s.get("source_state", "")
        recon = s.get("reconciliation_state")

        if state == "STALE":
            fp = f"SOURCE_STALE:{client_uuid}:{sk}"
            current_fingerprints.add(fp)
            _upsert_alert(
                sb, fp, "SOURCE_STALE", client_uuid,
                title=f"Source stale: {sk}",
                message=f"Source {sk!r} has stale data (last seen: {s.get('last_data_at', 'never')}).",
                evidence={"source_key": sk, "source_state": state, "last_data_at": s.get("last_data_at")},
                existing_open=existing_open,
            )

        if state in _ERROR_SOURCE_STATES:
            fp = f"SOURCE_ERROR:{client_uuid}:{sk}"
            current_fingerprints.add(fp)
            _upsert_alert(
                sb, fp, "SOURCE_ERROR", client_uuid,
                title=f"Source error: {sk}",
                message=f"Source {sk!r} is in error state: {state}. Error: {s.get('last_error') or 'unknown'}.",
                evidence={"source_key": sk, "source_state": state, "last_error": s.get("last_error")},
                existing_open=existing_open,
            )

        if recon and recon != "OK":
            fp = f"RECONCILIATION_FAILED:{client_uuid}:{sk}"
            current_fingerprints.add(fp)
            _upsert_alert(
                sb, fp, "RECONCILIATION_FAILED", client_uuid,
                title=f"Reconciliation failed: {sk}",
                message=f"Source {sk!r} reconciliation state: {recon}.",
                evidence={"source_key": sk, "reconciliation_state": recon},
                existing_open=existing_open,
            )

    # ── JOB_FAILED ───────────────────────────────────────────────────────────

    cutoff_24h = (now - timedelta(hours=24)).isoformat()
    try:
        failed_jobs = (
            sb.table("core_job_runs")
            .select("id, run_key, error, started_at")
            .eq("status", "FAILED")
            .like("run_key", f"core_pipeline:{client_id}:%")
            .gte("started_at", cutoff_24h)
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("alert_evaluator: failed jobs query failed %s: %s", client_id, exc)
        failed_jobs = []

    if failed_jobs:
        fp = f"JOB_FAILED:{client_id}:{today_iso}"
        current_fingerprints.add(fp)
        _upsert_alert(
            sb, fp, "JOB_FAILED", client_uuid,
            title=f"Pipeline failed: {client_id}",
            message=(
                f"{len(failed_jobs)} pipeline job(s) failed in the last 24h. "
                f"Latest error: {failed_jobs[0].get('error') or 'unknown'}."
            ),
            evidence={
                "client_id":    client_id,
                "failed_count": len(failed_jobs),
                "latest_error": failed_jobs[0].get("error"),
                "date":         today_iso,
            },
            existing_open=existing_open,
        )

    # ── JOB_STUCK ────────────────────────────────────────────────────────────

    try:
        active_jobs = (
            sb.table("core_job_runs")
            .select("id, status, started_at, run_key")
            .in_("status", ["RUNNING", "QUEUED"])
            .like("run_key", f"core_pipeline:{client_id}:%")
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("alert_evaluator: active jobs query failed %s: %s", client_id, exc)
        active_jobs = []

    for job in active_jobs:
        started = _parse_dt(job.get("started_at"))
        if not started:
            continue
        age_min   = (now - started).total_seconds() / 60
        threshold = _STUCK_RUNNING_MIN if job["status"] == "RUNNING" else _STUCK_QUEUED_MIN
        if age_min > threshold:
            fp = f"JOB_STUCK:{job['id']}"
            current_fingerprints.add(fp)
            _upsert_alert(
                sb, fp, "JOB_STUCK", client_uuid,
                title=f"Job stuck: {job['id'][:8]}",
                message=(
                    f"Job {job['id'][:8]} has been {job['status']} for "
                    f"{int(age_min)} min (threshold: {threshold} min)."
                ),
                evidence={
                    "job_id":      job["id"],
                    "status":      job["status"],
                    "started_at":  job.get("started_at"),
                    "age_minutes": round(age_min, 1),
                },
                existing_open=existing_open,
            )

    # ── Auto-resolve OPEN alerts whose condition no longer holds ─────────────

    resolved = _resolve_stale_open(sb, existing_open, current_fingerprints, now)
    created  = sum(1 for fp in current_fingerprints if fp not in existing_open)
    updated  = sum(1 for fp in current_fingerprints if fp in existing_open)

    logger.info(
        "alert_evaluator: %s created=%d updated=%d resolved=%d",
        client_id, created, updated, resolved,
    )
    return {"created": created, "updated": updated, "resolved": resolved}


# ── System-wide evaluation ────────────────────────────────────────────────────

def evaluate_system_alerts() -> dict:
    """
    Evaluate system-wide operational alerts (no client scope).
    Called every 30 min via APScheduler.
    """
    from ..database import get_supabase

    sb  = get_supabase()
    now = datetime.now(timezone.utc)
    cutoff_36h = (now - timedelta(hours=_PIPELINE_MAX_HOURS)).isoformat()
    fp_not_run = "PIPELINE_NOT_RUN:system"

    try:
        ok_res = (
            sb.table("core_job_runs")
            .select("id, finished_at")
            .eq("job_type", "core_pipeline")
            .eq("status", "SUCCEEDED")
            .gte("finished_at", cutoff_36h)
            .limit(1)
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("alert_evaluator: system check failed: %s", exc)
        return {"error": str(exc)}

    try:
        existing = (
            sb.table("alerts")
            .select("id, occurrence_count")
            .eq("fingerprint", fp_not_run)
            .eq("status", "OPEN")
            .is_("resolved_at", "null")
            .limit(1)
            .execute()
        )
        existing_alert = existing.data[0] if (existing and existing.data) else None
    except Exception as exc:
        logger.error("alert_evaluator: PIPELINE_NOT_RUN lookup failed: %s", exc)
        existing_alert = None

    if not ok_res:
        if existing_alert:
            try:
                sb.table("alerts").update({
                    "occurrence_count": existing_alert["occurrence_count"] + 1,
                    "data": {"last_checked_at": now.isoformat(), "max_hours": _PIPELINE_MAX_HOURS},
                }).eq("id", existing_alert["id"]).execute()
            except Exception as exc:
                logger.warning("alert_evaluator: PIPELINE_NOT_RUN update failed: %s", exc)
        else:
            try:
                sb.table("alerts").insert({
                    "type":             "PIPELINE_NOT_RUN",
                    "client_id":        None,
                    "title":            "Core pipeline has not run",
                    "message":          f"No successful core pipeline run in the last {_PIPELINE_MAX_HOURS}h.",
                    "severity":         "HIGH",
                    "status":           "OPEN",
                    "fingerprint":      fp_not_run,
                    "data":             {"last_checked_at": now.isoformat(), "max_hours": _PIPELINE_MAX_HOURS},
                    "occurrence_count": 1,
                }).execute()
            except Exception as exc:
                logger.warning("alert_evaluator: PIPELINE_NOT_RUN insert failed: %s", exc)
        return {"pipeline_not_run": True}
    else:
        if existing_alert:
            try:
                sb.table("alerts").update({
                    "status":      "RESOLVED",
                    "resolved_at": now.isoformat(),
                }).eq("id", existing_alert["id"]).execute()
            except Exception as exc:
                logger.warning("alert_evaluator: PIPELINE_NOT_RUN resolve failed: %s", exc)
            return {"pipeline_not_run": False, "resolved": 1}
        return {"pipeline_not_run": False}
