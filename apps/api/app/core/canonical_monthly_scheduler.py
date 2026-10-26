"""
Canonical Monthly H8 Scheduler.

Replaces legacy ``monthly_review`` when CANONICAL_MONTHLY_REVIEW_ENABLED=true.
For every active client: read the most recent core_metric_snapshots row for the
last closed calendar month, build an H8 artifact, store it via canonical CAS,
and transition to READY_FOR_REVIEW.

No publish.  No delivery.  No email.  No media writes.  No pipeline calls.

Snapshot lookup rule (H8 - closed month):
    Query BOTH period_start = first-of-month AND period_end = last-of-month.
    This ensures a full-month snapshot, not a 7-day rolling window that
    happens to end on the 30th.  If no full-month snapshot exists (because
    materialize_closed_month has not run for this client), the artifact is
    PARTIAL with limitation "snapshot_not_found".

Period definition:
    period_start = first day of previous calendar month
    period_end   = last day of previous calendar month
    (Scheduler fires on 1st of month at 08:30 UTC, after daily pipeline.)

Failure isolation:
    One client's failure never aborts the loop.  Each client gets its own
    core_job_runs record (RUNNING -> SUCCEEDED | FAILED).

Idempotency:
    The CAS store uses UUID5(client_id, 'monthly', period_start, period_end)
    as report_id.  Re-running for the same month returns the existing record
    if the artifact hash matches; raises IMMUTABLE_VERSION_CONFLICT if the
    underlying snapshot changed (correct -- version 1 is locked once written).

Rollback:
    Set CANONICAL_MONTHLY_REVIEW_ENABLED=false and redeploy.
    main.py will register monthly_review again; this module is never called.
"""
from __future__ import annotations

import calendar
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Optional

from .canonical_reports import CoreStore, Flags, ReportError, Reports

logger = logging.getLogger(__name__)

_JOB_TYPE    = "canonical_monthly_review"
_ENGINE      = "H8"
_ACTOR       = "canonical_monthly_review"
_ERROR_TRUNC = 2000

_MONTH_PT = [
    "Janeiro", "Fevereiro", "Marco", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


# -- Helpers -------------------------------------------------------------------

def _engine_version() -> str:
    return sha256(Path(__file__).read_bytes()).hexdigest()


def _last_completed_month() -> tuple[date, date]:
    """Last closed calendar month from today's UTC date."""
    today        = date.today()
    first_today  = date(today.year, today.month, 1)
    last_of_prev = first_today - timedelta(days=1)
    first_of_prev = date(last_of_prev.year, last_of_prev.month, 1)
    return first_of_prev, last_of_prev


def _month_label(period_start: date) -> str:
    return f"{_MONTH_PT[period_start.month - 1]} {period_start.year}"


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
        logger.error("canonical_monthly: client list failed: %s", exc)
        return []


# -- Artifact builder ----------------------------------------------------------

_DEGRADED_STATES = frozenset({"ERROR", "ACCESS_MISSING", "PERMISSION_DENIED", "MISSING", "STALE"})


def _build_artifact(
    client_id: str,
    period_start: date,
    period_end: date,
    sb,
) -> dict:
    """
    Build an H8 canonical artifact from core_metric_snapshots.

    Queries with BOTH period_start and period_end to guarantee a full-month
    snapshot (not a 7-day window that happens to end on the 30th).
    Absence of data is represented explicitly -- never converted to zero.
    """
    try:
        snap_res = (
            sb.table("core_metric_snapshots")
            .select("metrics, health, id")
            .eq("client_id", client_id)
            .eq("period_start", period_start.isoformat())
            .eq("period_end", period_end.isoformat())
            .order("computed_at", desc=True)
            .limit(1)
            .execute()
        )
        snapshot = snap_res.data[0] if snap_res.data else None
    except Exception as exc:
        logger.warning(
            "canonical_monthly: snapshot load failed %s/%s: %s",
            client_id, period_end, exc,
        )
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

    # Facts: only metrics with a real value (null-absent, never zero-fill)
    facts = [
        {
            "metric_key":           k,
            "value":                v["value"],
            "value_status":         v.get("value_status", "UNKNOWN"),
            "certification_status": "PROVISIONAL",
            "unit":                 v.get("unit"),
            "domain":               v.get("domain"),
            "target":               v.get("target"),
            "target_ratio":         v.get("target_ratio"),
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

    label = _month_label(period_start)

    # Business truth: always a non-empty string
    rev  = metrics_dict.get("revenue_business", {})
    spnd = metrics_dict.get("total_spend", {})
    mer  = metrics_dict.get("mer", {})

    if rev.get("value") and spnd.get("value") and spnd["value"] > 0:
        bt = f"{client_id} {label}. Receita R${rev['value']:,.2f}"
        if mer.get("value"):
            bt += f" | MER {mer['value']:.1f}x"
            if mer.get("target"):
                bt += f" (meta {mer['target']}x)"
        bt += f" | Investimento R${spnd['value']:,.2f}. PROVISIONAL."
    elif facts:
        bt = f"{client_id} {label}. {len(facts)} metricas disponiveis. PROVISIONAL."
    elif snapshot:
        bt = f"{client_id}: snapshot encontrado, sem valores para {label}. PROVISIONAL."
    else:
        bt = (
            f"{client_id}: sem snapshot mensal completo para {label}. "
            "Execute materialize_closed_month para gerar. PROVISIONAL."
        )

    # Limitations
    limitations = [
        "CORE_SCHEDULER_DIRECT: artifact built from core_metric_snapshots"
    ]
    if not snapshot:
        limitations.append(
            "snapshot_not_found: materialize_closed_month must run before canonical monthly review"
        )
    for src, state in source_health.items():
        if state in _DEGRADED_STATES:
            limitations.append(f"{src}: {state}")

    # Hypotheses from real metric values
    hypotheses: list[dict] = []
    mer_v = metrics_dict.get("mer",          {}).get("value")
    mer_t = metrics_dict.get("mer",          {}).get("target")
    rm_v  = metrics_dict.get("roas_meta",    {}).get("value")
    rg_v  = metrics_dict.get("roas_google",  {}).get("value")
    cpa_m = metrics_dict.get("cpa_meta",     {}).get("value")
    cpa_g = metrics_dict.get("cpa_google",   {}).get("value")

    if mer_v and mer_t and mer_v > mer_t:
        hypotheses.append({
            "type":      "OBSERVATION",
            "statement": (
                f"MER {mer_v:.1f}x supera meta {mer_t}x em "
                f"{((mer_v / mer_t) - 1) * 100:.0f}%."
            ),
        })
    if rm_v and rg_v:
        hypotheses.append({
            "type":      "OBSERVATION",
            "statement": f"Meta ROAS {rm_v:.1f}x vs Google ROAS {rg_v:.1f}x.",
        })
    if cpa_m and cpa_g:
        hypotheses.append({
            "type":      "HYPOTHESIS",
            "statement": f"CPA Meta R${cpa_m:.2f} vs CPA Google R${cpa_g:.2f}.",
        })

    recommendations: list[dict] = []
    if mer_v and mer_t and mer_v > mer_t * 1.3:
        recommendations.append({
            "type":      "RECOMMENDATION",
            "statement": (
                f"MER {mer_v:.1f}x com {((mer_v / mer_t) - 1) * 100:.0f}% acima da meta -- "
                "avaliar escala controlada."
            ),
        })

    degraded       = any(v in _DEGRADED_STATES for v in source_health.values())
    content_status = "PARTIAL" if (not snapshot or degraded) else "READY_FOR_REVIEW"

    now_utc = datetime.now(timezone.utc).isoformat()
    ev      = _engine_version()

    content = {
        "client_id":                client_id,
        "analysis_period":          {"start": str(period_start), "end": str(period_end)},
        "schema_version":           "h8.1-core-scheduler",
        "canonical_business_model": "ecommerce",
        "review_model":             "ECOMMERCE_MONTHLY",
        "timezone":                 "America/Sao_Paulo",
        "origin":                   "CANONICAL_MONTHLY_SCHEDULER",
        "core_writes":              False,
        "platform_writes":          False,
        "facts":                    facts,
        "channels":                 channels,
        "business_truth":           bt,
        "observations":             source_state,
        "hypotheses":               hypotheses,
        "recommendations":          recommendations,
        "limitations":              limitations,
        "status":                   content_status,
    }

    cert = [
        {
            "window":               "analysis",
            "metric_key":           k,
            "certification_status": "PROVISIONAL",
            "value_status":         v.get("value_status", "UNKNOWN"),
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
        # No timestamp in evidence_refs -- hash stability across re-runs.
        "evidence_refs": [
            {
                "source":       "core_metric_snapshots",
                "client_id":    client_id,
                "period_start": str(period_start),
                "period_end":   str(period_end),
                "metric_count": len(metrics_dict),
                "snapshot_id":  snapshot_id,
            }
        ],
    }


# -- Job tracking --------------------------------------------------------------

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
        logger.warning("canonical_monthly: job insert failed %s: %s", client_id, exc)
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
        logger.warning("canonical_monthly: job finish failed %s: %s", job_id, exc)


# -- Public entrypoints --------------------------------------------------------

def run_canonical_monthly_for_client(
    client_id: str,
    period_start: date,
    period_end: date,
    sb=None,
    _store_override=None,
) -> dict:
    """
    Generate H8 canonical record for one client, transition to READY_FOR_REVIEW.
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
            "canonical_monthly: %s %s -> %s (report_id=%s)",
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
        logger.error("canonical_monthly: %s failed: %s", client_id, exc)
        return {"client_id": client_id, "status": "FAILED", "error": error}


def run_canonical_monthly_all_clients() -> None:
    """
    Scheduler entry point -- 1st of month 08:30 UTC.
    Loads active clients from DB, runs H8 generation for the last closed month.
    Per-client failure is isolated; does not abort the remaining clients.

    Prerequisite: materialize_closed_month must have run for each client
    before this fires (daily pipeline runs at 07:15 UTC; on the last day of
    the month it writes a 7-day snapshot ending on period_end -- NOT a
    full-month snapshot).  If no full-month snapshot exists, the artifact is
    marked PARTIAL and the record is stored for human review.
    """
    flags = Flags.environment()
    if not flags.CANONICAL_MONTHLY_REVIEW_ENABLED:
        logger.info("canonical_monthly: CANONICAL_MONTHLY_REVIEW_ENABLED=false -- skipping run")
        return

    period_start, period_end = _last_completed_month()
    sb      = _get_db()
    clients = _load_active_clients(sb)

    logger.info(
        "canonical_monthly: running for %d clients -- period %s to %s",
        len(clients), period_start, period_end,
    )

    results: list[dict] = []
    for c in clients:
        cid = c["client_id"]
        try:
            result = run_canonical_monthly_for_client(cid, period_start, period_end, sb=sb)
            results.append(result)
        except Exception as exc:
            logger.error("canonical_monthly: unexpected error for %s: %s", cid, exc)
            results.append({"client_id": cid, "status": "FAILED", "error": str(exc)[:200]})

    ok_count     = sum(1 for r in results if r.get("status") == "READY_FOR_REVIEW")
    failed_count = sum(1 for r in results if r.get("status") == "FAILED")
    logger.info(
        "canonical_monthly: done -- %d READY_FOR_REVIEW, %d FAILED, %d total",
        ok_count, failed_count, len(results),
    )
