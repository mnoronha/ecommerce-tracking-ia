"""
Scheduler entry point for automatic change history ingest.

Registered in main.py as a 15-minute interval job.
Pattern mirrors core_scheduler.run_core_pipeline_all_clients.

PLATFORM_WRITES = ZERO — this module only reads from Google Ads and Meta Ads.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..database import get_supabase

logger = logging.getLogger(__name__)

_JOB_ID = "change_history_ingest"


def run_change_history_all_clients() -> None:
    """
    Fetch and ingest change history for all active clients.
    One ingest run per (client, platform). Errors on one client do not abort others.
    """
    sb  = get_supabase()
    now = datetime.now(timezone.utc)
    run_key = f"{_JOB_ID}:{now.strftime('%Y%m%dT%H%M%S')}"

    # Log job start
    try:
        sb.table("core_job_runs").insert({
            "job_type":   _JOB_ID,
            "run_key":    run_key,
            "status":     "RUNNING",
            "started_at": now.isoformat(),
        }).execute()
    except Exception as exc:
        logger.warning("change_history_scheduler: job_runs insert failed: %s", exc)

    # Fetch active clients
    try:
        res = (
            sb.table("clients")
            .select("client_id")
            .eq("is_active", True)
            .execute()
        )
        clients = [r["client_id"] for r in (res.data or [])]
    except Exception as exc:
        logger.error("change_history_scheduler: client list failed: %s", exc)
        _finish_job(sb, run_id, "FAILED", str(exc))
        return

    logger.info("change_history_scheduler: starting — %d clients", len(clients))

    total_inserted = 0
    total_skipped  = 0
    errors: list[str] = []

    from .change_history_ingestor import ingest_for_client

    for client_id in clients:
        try:
            stats = ingest_for_client(client_id, sb)
            total_inserted += stats.get("total_inserted") or 0
            total_skipped  += stats.get("total_skipped")  or 0
            logger.debug(
                "change_history_scheduler: %s — inserted=%d skipped=%d",
                client_id, stats.get("total_inserted", 0), stats.get("total_skipped", 0),
            )
        except Exception as exc:
            msg = f"{client_id}: {exc}"
            errors.append(msg)
            logger.error("change_history_scheduler: error for %s: %s", client_id, exc)

    status  = "FAILED" if len(errors) == len(clients) and clients else "SUCCEEDED"
    summary = (
        f"inserted={total_inserted} skipped={total_skipped} clients={len(clients)}"
        + (f" errors={len(errors)}" if errors else "")
    )
    logger.info("change_history_scheduler: done — %s", summary)
    _finish_job(sb, run_key, status, "\n".join(errors) if errors else None)


def _finish_job(sb, run_key: str, status: str, error: str | None) -> None:
    try:
        sb.table("core_job_runs").update({
            "status":      status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error":       error[:500] if error else None,
        }).eq("run_key", run_key).execute()
    except Exception as exc:
        logger.warning("change_history_scheduler: job_runs update failed: %s", exc)
