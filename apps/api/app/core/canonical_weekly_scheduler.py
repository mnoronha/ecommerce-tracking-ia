"""
Canonical Weekly H7 Scheduler.

Replaces legacy ``weekly_review`` when CANONICAL_WEEKLY_REVIEW_ENABLED=true.
For every active client: read the most recent core_metric_snapshots row for the
last closed Mon-Sun week, build an H7 artifact, store it via canonical CAS, and
transition to READY_FOR_REVIEW.

No publish.  No delivery.  No email.  No media writes.

Period rule:
    period_end   = last Sunday (UTC date)
    period_start = period_end - 6 days
    (Same definition as weekly_review.py; scheduler fires Mon 08:00 UTC.)

Failure isolation:
    One client's failure never aborts the loop.  Each client gets its own
    core_job_runs record (RUNNING → SUCCEEDED | FAILED).

Idempotency:
    The CAS store uses UUID5(client_id, 'weekly', period_start, period_end)
    as report_id.  Re-running the scheduler for the same week returns the
    existing record if the artifact hash matches; raises IMMUTABLE_VERSION_CONFLICT
    if the underlying snapshot changed (correct — version 1 is locked once written).

Rollback:
    Set CANONICAL_WEEKLY_REVIEW_ENABLED=false and redeploy.
    main.py will register weekly_review again; this module is never called.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Optional

from .canonical_reports import CoreStore, Flags, ReportError, Reports

logger = logging.getLogger(__name__)

_JOB_TYPE    = "canonical_weekly_review"
_ENGINE      = "H7"
# Automated scheduler transitions to READY_FOR_REVIEW; human approval is
# required for the APPROVED step.  The string must not appear in the
# HUMAN_ACTOR_REQUIRED blocked set ('hermes','hermes_service','platform_web',
# 'agency_admin','system','scheduler').
_ACTOR       = "canonical_weekly_review"
_ERROR_TRUNC = 2000


# ── Helpers ────────────────────────────────────────────────────────────────────

def _engine_version() -> str:
    """SHA-256 of this file — changes when scheduler logic changes."""
    return sha256(Path(__file__).read_bytes()).hexdigest()


def _last_completed_week() -> tuple[date, date]:
    """Most recently completed Mon-Sun week from today's UTC date."""
    today        = date.today()
    period_end   = today - timedelta(days=today.weekday() + 1)
    period_start = period_end - timedelta(days=6)
    return period_start, period_end


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
        logger.error("canonical_weekly: client list failed: %s", exc)
        return []


# ── Artifact builder ───────────────────────────────────────────────────────────

_DEGRADED_STATES = frozenset({"ERROR", "ACCESS_MISSING", "PERMISSION_DENIED", "MISSING", "STALE"})


def _build_artifact(client_id: str, period_start: date, period_end: date, sb) -> dict:
    """
    Build an H7 canonical artifact from core_metric_snapshots.
    Absence of data is represented explicitly — never converted to zero.
    """
    try:
        snap_res = (
            sb.table("core_metric_snapshots")
            .select("metrics, health, id")
            .eq("client_id", client_id)
            .eq("period_end", period_end.isoformat())
            .order("computed_at", desc=True)
            .limit(1)
            .execute()
        )
        snapshot = snap_res.data[0] if snap_res.data else None
    except Exception as exc:
        logger.warning("canonical_weekly: snapshot load failed %s/%s: %s", client_id, period_end, exc)
        snapshot = None

    metrics_raw   = (snapshot or {}).get("metrics") or []
    source_health = (snapshot or {}).get("health")  or {}
    snapshot_id   = str(snapshot["id"]) if snapshot else None

    if isinstance(metrics_raw, dict):
        metrics_raw = list(metrics_raw.values())
    metrics_dict = {
        m["metric_key"]: m
        for m in metrics_raw
        if isinstance(m, dict) and "metric_key" in m
    }

    # Facts: only metrics with a real value (null-absent never becomes 0)
    facts = [
        {
            "metric_key":           k,
            "value":                v["value"],
            "value_status":         v.get("value_status", "UNKNOWN"),
            "certification_status": "PROVISIONAL",
            "unit":                 v.get("unit"),
        }
        for k, v in metrics_dict.items()
        if v.get("value") is not None
    ]

    # Channels: per-channel spend + conversion + source health
    channels: dict = {}
    for ch_alias, ch_src in (("meta", "meta_ads"), ("google", "google_ads")):
        sm    = metrics_dict.get(f"{ch_alias}_spend", {})
        cm    = metrics_dict.get(f"{ch_alias}_conversions", {})
        state = source_health.get(ch_src, "NOT_FOUND")
        if sm.get("value") is not None or state not in ("NOT_FOUND", "NOT_CONTRACTED"):
            channels[ch_alias] = {
                "spend":        sm.get("value"),
                "conversions":  cm.get("value"),
                "source_state": state,
            }

    source_state = [
        {"source_system": k, "source_state": v}
        for k, v in source_health.items()
    ]

    # Business truth: always a non-empty string
    rev  = metrics_dict.get("revenue_business", {})
    spnd = metrics_dict.get("total_spend", {})
    if rev.get("value") and spnd.get("value") and spnd["value"] > 0:
        business_truth = (
            f"{client_id} {period_start} to {period_end}. "
            f"Revenue R${rev['value']:,.2f}, spend R${spnd['value']:,.2f}. PROVISIONAL."
        )
    elif facts:
        business_truth = f"{client_id} {period_start} to {period_end}. {len(facts)} metrics available."
    elif snapshot:
        business_truth = (
            f"{client_id}: snapshot found, no metric values for {period_start} to {period_end}."
        )
    else:
        business_truth = (
            f"{client_id}: no snapshot for {period_start} to {period_end}. "
            "Source may be STALE or NOT_CONTRACTED."
        )

    # Limitations: explicit, not swallowed
    limitations = [
        "CORE_SCHEDULER_DIRECT: artifact built from core_metric_snapshots"
    ]
    if not snapshot:
        limitations.append("snapshot_not_found: pipeline may not have run for this period")
    for src, state in source_health.items():
        if state in _DEGRADED_STATES:
            limitations.append(f"{src}: {state}")

    degraded       = any(v in _DEGRADED_STATES for v in source_health.values())
    content_status = "PARTIAL" if (not snapshot or degraded) else "READY_FOR_REVIEW"

    now_utc = datetime.now(timezone.utc).isoformat()
    ev      = _engine_version()

    content = {
        "client_id":                client_id,
        "analysis_period":          {"start": str(period_start), "end": str(period_end)},
        "schema_version":           "h7.1-core-scheduler",
        "canonical_business_model": "ecommerce",
        "review_model":             "ECOMMERCE",
        "timezone":                 "America/Sao_Paulo",
        "origin":                   "CANONICAL_WEEKLY_SCHEDULER",
        "core_writes":              False,
        "platform_writes":          False,
        "facts":                    facts,
        "channels":                 channels,
        "business_truth":           business_truth,
        "observations":             source_state,
        "hypotheses":               [],
        "recommendations":          [],
        "limitations":              limitations,
        "status":                   content_status,
    }

    cert = [
        {
            "window":                 "analysis",
            "metric_key":             k,
            "certification_status":   "PROVISIONAL",
            "value_status":           v.get("value_status", "UNKNOWN"),
        }
        for k, v in metrics_dict.items()
    ]

    return {
        "client_id":            client_id,
        "canonical_engine":     _ENGINE,
        "engine_version":       ev,
        "version":              1,
        "period_start":         str(period_start),
        "period_end":           str(period_end),
        "generated_at":         now_utc,
        "source_state":         source_state,
        "certification_status": cert,
        "review_status":        "READY" if facts else "PARTIAL",
        "content":              content,
        # Omit a generation-time timestamp from evidence_refs so the artifact
        # hash is stable across re-runs for the same snapshot (idempotency).
        "evidence_refs":        [
            {
                "source":        "core_metric_snapshots",
                "client_id":     client_id,
                "period_end":    str(period_end),
                "metric_count":  len(metrics_dict),
                "snapshot_id":   snapshot_id,
            }
        ],
    }


# ── Job tracking ───────────────────────────────────────────────────────────────

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
        logger.warning("canonical_weekly: job insert failed %s: %s", client_id, exc)
        return None


def _finish_job(sb, job_id: Optional[str], status: str, error: Optional[str] = None) -> None:
    if not job_id:
        return
    try:
        payload: dict = {
            "status":      status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        if error:
            payload["error"] = error[:_ERROR_TRUNC]
        sb.table("core_job_runs").update(payload).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("canonical_weekly: job finish failed %s: %s", job_id, exc)


# ── Public entrypoints ─────────────────────────────────────────────────────────

def run_canonical_weekly_for_client(
    client_id: str,
    period_start: date,
    period_end: date,
    sb=None,
    _store_override=None,   # injected in tests to avoid real DB for CAS
) -> dict:
    """
    Generate H7 canonical record for one client, transition to READY_FOR_REVIEW.
    Returns {"client_id", "status", "report_id"?, "error"?}.
    Idempotent: re-running for the same period returns the existing record.
    """
    if sb is None:
        sb = _get_db()

    store   = _store_override if _store_override is not None else CoreStore(sb)
    reports = Reports(store)

    job_id = _insert_running_job(sb, client_id, period_end)
    try:
        artifact = _build_artifact(client_id, period_start, period_end, sb)
        doc      = reports.generate(artifact)

        if doc["status"] == "DRAFT":
            at  = datetime.now(timezone.utc).isoformat()
            doc = reports.transition(
                doc["report_id"], 1, "READY_FOR_REVIEW",
                actor=_ACTOR, at=at, expected_hash=doc["artifact_hash"],
            )

        _finish_job(sb, job_id, "SUCCEEDED")
        logger.info(
            "canonical_weekly: %s %s → %s (report_id=%s)",
            client_id, period_end, doc["status"], doc["report_id"],
        )
        return {
            "client_id": client_id,
            "status":    doc["status"],
            "report_id": doc["report_id"],
        }

    except Exception as exc:
        error = str(exc)[:_ERROR_TRUNC]
        _finish_job(sb, job_id, "FAILED", error)
        logger.error("canonical_weekly: %s failed: %s", client_id, exc)
        return {"client_id": client_id, "status": "FAILED", "error": error}


def run_canonical_weekly_all_clients() -> None:
    """
    Scheduler entry point — Monday 08:00 UTC.
    Loads active clients from DB, runs H7 generation for the last closed week.
    Per-client failure is isolated; does not abort the remaining clients.
    """
    flags = Flags.environment()
    if not flags.CANONICAL_WEEKLY_REVIEW_ENABLED:
        logger.info("canonical_weekly: CANONICAL_WEEKLY_REVIEW_ENABLED=false — skipping run")
        return

    period_start, period_end = _last_completed_week()
    sb      = _get_db()
    clients = _load_active_clients(sb)

    logger.info(
        "canonical_weekly: running for %d clients — period %s to %s",
        len(clients), period_start, period_end,
    )

    results: list[dict] = []
    for c in clients:
        cid = c["client_id"]
        try:
            result = run_canonical_weekly_for_client(cid, period_start, period_end, sb=sb)
            results.append(result)
        except Exception as exc:
            logger.error("canonical_weekly: unexpected error for %s: %s", cid, exc)
            results.append({"client_id": cid, "status": "FAILED", "error": str(exc)[:200]})

    ok_count     = sum(1 for r in results if r.get("status") == "READY_FOR_REVIEW")
    failed_count = sum(1 for r in results if r.get("status") == "FAILED")
    logger.info(
        "canonical_weekly: done — %d READY_FOR_REVIEW, %d FAILED, %d total",
        ok_count, failed_count, len(results),
    )
