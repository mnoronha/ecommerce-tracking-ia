"""
Core pipeline scheduler.

Provides:
  run_core_pipeline_all_clients() — wired into APScheduler in main.py, daily at 07:15 UTC.
  run_core_pipeline_for_client() — used by replay endpoint (runs in a BackgroundTask).

Each run writes a record to core_job_runs (RUNNING → SUCCEEDED | FAILED).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_JOB_TYPE = "core_pipeline"
_ERROR_TRUNCATE = 2000  # guard against huge stack traces in the DB column


# ── Private helpers ───────────────────────────────────────────────────────────

def _insert_running_job(sb, client_id: str, period_end: date) -> Optional[str]:
    run_key = f"{_JOB_TYPE}:{client_id}:{period_end.isoformat()}"
    job_id = str(uuid.uuid4())
    try:
        res = sb.table("core_job_runs").insert({
            "id": job_id,
            "job_type": _JOB_TYPE,
            "run_key": run_key,
            "status": "RUNNING",
            "attempt": 1,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        return res.data[0]["id"] if res.data else job_id
    except Exception as exc:
        logger.warning("core_scheduler: job insert failed for %s: %s", client_id, exc)
        return None


def _insert_queued_replay(sb, client_id: str, period_end: date, replay_of: str) -> str:
    # Suffix with replay_of[:8] so the run_key is unique even when replaying a SUCCEEDED job.
    # The router parser uses only parts[0..2]; the suffix is informational provenance only.
    run_key = f"{_JOB_TYPE}:{client_id}:{period_end.isoformat()}:replay:{replay_of[:8]}"
    job_id = str(uuid.uuid4())
    try:
        res = sb.table("core_job_runs").insert({
            "id": job_id,
            "job_type": _JOB_TYPE,
            "run_key": run_key,
            "status": "QUEUED",
            "attempt": 1,
            "replay_of": replay_of,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        return res.data[0]["id"] if res.data else job_id
    except Exception as exc:
        logger.warning("core_scheduler: replay insert failed for %s: %s", client_id, exc)
        return job_id


def _finish_job(sb, job_id: str, status: str, error: Optional[str] = None) -> None:
    try:
        payload: dict = {
            "status": status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        if error:
            payload["error"] = error[:_ERROR_TRUNCATE]
        sb.table("core_job_runs").update(payload).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("core_scheduler: job finish update failed %s: %s", job_id, exc)


def _run_one(client_id: str, period_start: date, period_end: date) -> tuple[str, Optional[str]]:
    """Run pipeline for one client. Returns (status, error)."""
    from ..core.pipeline import run_pipeline  # local import avoids circular at module load

    snapshot_id = None
    try:
        result = run_pipeline(client_id, period_start, period_end)
        snapshot_id = result.snapshot_id
        if result.error:
            return "FAILED", result.error
        return "SUCCEEDED", None
    except Exception as exc:
        logger.error("core_scheduler: pipeline raised for %s: %s", client_id, exc)
        return "FAILED", str(exc)
    finally:
        if snapshot_id:
            logger.info("core_scheduler: %s snapshot=%s", client_id, snapshot_id)


# ── Public entrypoints ────────────────────────────────────────────────────────

def run_core_pipeline_all_clients() -> None:
    """Daily Core pipeline run for all active clients (called by APScheduler)."""
    from ..database import get_supabase

    today = date.today()
    period_end = today - timedelta(days=1)
    period_start = period_end - timedelta(days=6)

    sb = get_supabase()
    try:
        res = sb.table("clients").select("client_id").eq("is_active", True).execute()
        client_ids = [c["client_id"] for c in (res.data or [])]
    except Exception as exc:
        logger.error("core_scheduler: client list failed: %s", exc)
        return

    logger.info(
        "core_scheduler: running for %d clients (%s → %s)",
        len(client_ids), period_start, period_end,
    )

    for client_id in client_ids:
        job_id = _insert_running_job(sb, client_id, period_end)
        status, error = _run_one(client_id, period_start, period_end)
        if job_id:
            _finish_job(sb, job_id, status, error)
        logger.info("core_scheduler: %s → %s", client_id, status)
        try:
            from ..core.alert_evaluator import evaluate_operational_alerts
            evaluate_operational_alerts(client_id)
        except Exception as exc:
            logger.warning("core_scheduler: alert eval failed for %s: %s", client_id, exc)
        try:
            from ..core.performance_alert_evaluator import evaluate_performance_alerts
            evaluate_performance_alerts(client_id)
        except Exception as exc:
            logger.warning("core_scheduler: perf alert eval failed for %s: %s", client_id, exc)


def run_core_pipeline_for_client(
    job_id: str,
    client_id: str,
    period_start: date,
    period_end: date,
) -> None:
    """
    Background execution for a replay job.
    Updates job record from QUEUED → SUCCEEDED | FAILED.
    Called via FastAPI BackgroundTasks.
    """
    from ..database import get_supabase

    sb = get_supabase()
    try:
        sb.table("core_job_runs").update({
            "status": "RUNNING",
        }).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("core_scheduler: replay status→RUNNING failed %s: %s", job_id, exc)

    status, error = _run_one(client_id, period_start, period_end)
    _finish_job(sb, job_id, status, error)
    logger.info("core_scheduler: replay %s → %s", job_id, status)
    try:
        from ..core.alert_evaluator import evaluate_operational_alerts
        evaluate_operational_alerts(client_id)
    except Exception as exc:
        logger.warning("core_scheduler: alert eval failed after replay %s: %s", job_id, exc)
    try:
        from ..core.performance_alert_evaluator import evaluate_performance_alerts
        evaluate_performance_alerts(client_id)
    except Exception as exc:
        logger.warning("core_scheduler: perf alert eval failed after replay %s: %s", job_id, exc)
