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
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Optional

from .canonical_reports import CoreStore, Flags, ReportError, Reports
from .analysis_layer import (
    build_monthly_analysis_layer,
    build_hermes_handoff_from_artifact,
    check_quality_gate,
    compute_quality_scores,
    detect_anomalies,
    build_wow,
    FLAG_CANONICAL_ANALYSIS,
)

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


def _fetch_prev_month_snapshot(sb, client_id: str, period_start: date) -> Optional[dict]:
    """Fetch the previous month's full snapshot for MoM comparison."""
    prev_end   = period_start - timedelta(days=1)
    prev_start = date(prev_end.year, prev_end.month, 1)
    try:
        res = (
            sb.table("core_metric_snapshots")
            .select("metrics, health")
            .eq("client_id", client_id)
            .eq("period_start", prev_start.isoformat())
            .eq("period_end", prev_end.isoformat())
            .order("computed_at", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as exc:
        logger.warning("canonical_monthly: prev snapshot failed %s/%s: %s", client_id, prev_end, exc)
        return None


def _load_context(sb, client_id: str, period_start: date, period_end: date) -> dict:
    """Load operational context: targets, alerts, executed actions, manual changes."""
    ctx: dict = {}

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
            ctx["targets"] = {
                k: {"target": v.get("target"), "unit": v.get("unit")}
                for k, v in (tt if isinstance(tt, dict) else {}).items()
                if isinstance(v, dict) and "target" in v
            }
    except Exception as exc:
        logger.debug("canonical_monthly: target_truth failed %s: %s", client_id, exc)

    client_uuid: Optional[str] = None
    try:
        res = (
            sb.table("clients")
            .select("id")
            .eq("client_id", client_id)
            .limit(1)
            .execute()
        )
        if res.data:
            client_uuid = str(res.data[0]["id"])
    except Exception as exc:
        logger.debug("canonical_monthly: client UUID lookup failed %s: %s", client_id, exc)

    if client_uuid:
        try:
            res = (
                sb.table("alerts")
                .select("severity, title, message")
                .eq("client_id", client_uuid)
                .eq("is_resolved", False)
                .order("created_at", desc=False)
                .limit(10)
                .execute()
            )
            ctx["active_alerts"] = [
                {"severity": r.get("severity"), "title": r.get("title"), "message": r.get("message")}
                for r in (res.data or [])
            ]
        except Exception as exc:
            logger.debug("canonical_monthly: alerts failed %s: %s", client_id, exc)

        try:
            from .actions_loader import load_executed_actions, load_open_recommendations, load_manual_changes
            ctx["executed_actions"]     = load_executed_actions(sb, client_uuid, period_start, period_end)
            ctx["open_recommendations"] = load_open_recommendations(sb, client_uuid)
            ctx["manual_changes"]       = load_manual_changes(sb, client_id, period_start, period_end)
        except Exception as exc:
            logger.debug("canonical_monthly: actions/changes failed %s: %s", client_id, exc)

    return ctx


# -- Artifact builder ----------------------------------------------------------

_DEGRADED_STATES = frozenset({"ERROR", "ACCESS_MISSING", "PERMISSION_DENIED", "MISSING", "STALE"})


def _build_change_summary(sb, client_id: str, period_start: date, period_end: date) -> dict:
    """
    Build per-platform change_summary from Change Digest for the monthly period.

    Context assembly priority:
        Business Truth > metrics > Change Digest > raw change_history > Outcomes > Learnings

    Monthly: includes only relevant changes (not hundreds of raw events).
    Never raises — any failure returns a limitation entry.
    Saves digest to core_change_digests for hash stability on re-runs.
    """
    from .change_digest import build_digest, save_digest, get_digest

    result: dict = {}
    for platform in ("meta", "google"):
        try:
            stored = get_digest(sb, client_id, platform, str(period_start), str(period_end))
            if stored is None:
                digest = build_digest(sb, client_id, platform, str(period_start), str(period_end))
                save_digest(sb, digest)
                stored = digest
            summary = stored.get("summary") or {}
            result[platform] = {
                "main_changes":         summary.get("main_changes", []),
                "budget_changes":       summary.get("budget_changes", []),
                "campaign_activations": summary.get("campaign_activations", []),
                "campaign_pauses":      summary.get("campaign_pauses", []),
                "unknown_reason_count": summary.get("unknown_reason_count", 0),
                "total_changes_in_log": stored.get("total_raw_count", 0),
                "relevant_changes":     stored.get("relevant_count", 0),
                "limitations":          stored.get("limitations", []),
                # digest_generated_at excluded: timestamp makes artifact hash non-deterministic
            }
        except Exception as exc:
            result[platform] = {
                "error":            f"DIGEST_UNAVAILABLE: {str(exc)[:120]}",
                "limitations":      [f"{platform}: change digest indisponível — usar core_change_log diretamente"],
                "main_changes":     [],
                "budget_changes":   [],
                "unknown_reason_count": 0,
            }
    return result


def _build_artifact(
    client_id: str,
    period_start: date,
    period_end: date,
    sb,
    version: int = 1,
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

    # Change Digest — built before limitations so failures surface there
    change_summary = _build_change_summary(sb, client_id, period_start, period_end)

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
    for platform, cs in change_summary.items():
        if cs.get("error"):
            limitations.append(f"change_digest_{platform}: {cs['error']}")

    # Rule-based observations (always computed, supplement to analysis layer)
    obs_hypotheses: list[dict] = []
    mer_v = metrics_dict.get("mer",          {}).get("value")
    mer_t = metrics_dict.get("mer",          {}).get("target")
    rm_v  = metrics_dict.get("roas_meta",    {}).get("value")
    rg_v  = metrics_dict.get("roas_google",  {}).get("value")
    cpa_m = metrics_dict.get("cpa_meta",     {}).get("value")
    cpa_g = metrics_dict.get("cpa_google",   {}).get("value")

    if mer_v and mer_t and mer_v > mer_t:
        obs_hypotheses.append({
            "type":      "OBSERVATION",
            "statement": (
                f"MER {mer_v:.1f}x supera meta {mer_t}x em "
                f"{((mer_v / mer_t) - 1) * 100:.0f}%."
            ),
        })
    if rm_v and rg_v:
        obs_hypotheses.append({
            "type":      "OBSERVATION",
            "statement": f"Meta ROAS {rm_v:.1f}x vs Google ROAS {rg_v:.1f}x.",
        })
    if cpa_m and cpa_g:
        obs_hypotheses.append({
            "type":      "HYPOTHESIS",
            "statement": f"CPA Meta R${cpa_m:.2f} vs CPA Google R${cpa_g:.2f}.",
        })

    degraded       = any(v in _DEGRADED_STATES for v in source_health.values())
    content_status = "PARTIAL" if (not snapshot or degraded) else "READY_FOR_REVIEW"

    now_utc = datetime.now(timezone.utc).isoformat()
    ev      = _engine_version()

    # Hermes monthly brief — top 5 changes from the month, no technical payload
    hermes_brief_items: list[str] = []
    for platform, cs in change_summary.items():
        for item in (cs.get("main_changes") or [])[:3]:
            hermes_brief_items.append(f"[{platform.upper()}] {item}")
    hermes_brief = {
        "title": "PRINCIPAIS ALTERACOES DO MES",
        "items": hermes_brief_items[:5],
        "note":  "Baseado no Change Digest — apenas mudancas de impacto high/medium",
    }

    # Context manifest
    context = _load_context(sb, client_id, period_start, period_end)
    context_manifest = {
        "targets_loaded":             bool(context.get("targets")),
        "alerts_count":               len(context.get("active_alerts") or []),
        "executed_actions_count":     len(context.get("executed_actions") or []),
        "open_recommendations_count": len(context.get("open_recommendations") or []),
        "manual_changes_count":       len(context.get("manual_changes") or []),
    }

    # MoM comparison (prev month snapshot)
    prev_month_snapshot = _fetch_prev_month_snapshot(sb, client_id, period_start)

    # Analysis layer (Claude) — same engine as H7, monthly period_type
    analysis_layer = build_monthly_analysis_layer(
        client_id           = client_id,
        period_start        = str(period_start),
        period_end          = str(period_end),
        metrics_dict        = metrics_dict,
        channels            = channels,
        business_truth      = bt,
        change_summary      = change_summary,
        limitations         = limitations,
        prev_month_snapshot = prev_month_snapshot,
        context             = context,
    )
    if analysis_layer.get("status") not in ("ANALYSIS_COMPLETE", "ANALYSIS_DISABLED"):
        limitations.append(f"analysis_layer: {analysis_layer.get('reason', 'UNAVAILABLE')}")

    # Promoted recommendations: analysis layer first, rule-based as fallback
    analysis_recs: list[dict] = []
    if analysis_layer.get("status") == "ANALYSIS_COMPLETE":
        analysis_recs = analysis_layer.get("recommendations") or []

    content = {
        "client_id":                client_id,
        "analysis_period":          {"start": str(period_start), "end": str(period_end)},
        "schema_version":           "h8.3-core-scheduler",
        "canonical_business_model": "ecommerce",
        "review_model":             "ECOMMERCE_MONTHLY",
        "timezone":                 "America/Sao_Paulo",
        "origin":                   "CANONICAL_MONTHLY_SCHEDULER",
        "core_writes":              False,
        "platform_writes":          False,
        "facts":                    facts,
        "channels":                 channels,
        "business_truth":           bt,
        "change_summary":           change_summary,
        "hermes_monthly_brief":     hermes_brief,
        "analysis_layer":           analysis_layer,
        "context_manifest":         context_manifest,
        "observations":             source_state,
        "hypotheses":               obs_hypotheses,
        "recommendations":          analysis_recs or [],
        "limitations":              limitations,
        "status":                   content_status,
    }

    quality_scores = compute_quality_scores(content)

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
        "version":              version,
        "period_start":         str(period_start),
        "period_end":           str(period_end),
        "generated_at":         now_utc,
        "source_state":         source_state,
        "certification_status": cert,
        "review_status":        "READY" if facts else "PARTIAL",
        "quality_scores":       quality_scores,
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
    version: int = 1,
) -> dict:
    """
    Generate H8 canonical record for one client, transition to READY_FOR_REVIEW.
    Returns {"client_id", "status", "report_id"?, "error"?, "violations"?}.
    Idempotent: re-running for the same period+version returns the existing record.
    Quality gate mirrors H7: keeps DRAFT if analysis incomplete.
    """
    if sb is None:
        sb = _get_db()

    analysis_enabled = os.getenv(FLAG_CANONICAL_ANALYSIS, "true").strip().lower() == "true"
    store   = _store_override if _store_override is not None else CoreStore(sb)
    reports = Reports(store)

    job_id = _insert_running_job(sb, client_id, period_end)
    try:
        artifact = _build_artifact(client_id, period_start, period_end, sb, version=version)

        try:
            doc = reports.generate(artifact)
        except ReportError as exc:
            if "IMMUTABLE_VERSION_CONFLICT" in str(exc) and analysis_enabled:
                import json as _json, uuid as _uuid
                rid = str(_uuid.uuid5(
                    _uuid.NAMESPACE_URL,
                    _json.dumps([client_id, "monthly", str(period_start), str(period_end)]),
                ))
                existing = store.get(f"report:{rid}:v{version}")
                if existing:
                    doc_ex = existing["document"]
                    _finish_job(sb, job_id, "SUCCEEDED")
                    return {
                        "client_id": client_id,
                        "status":    "IMMUTABLE_VERSION_CONFLICT_SKIPPED",
                        "report_id": doc_ex.get("report_id"),
                    }
            raise

        if doc["status"] == "DRAFT":
            analysis_status = doc.get("content", {}).get("analysis_layer", {}).get("status")

            # Async Hermes handoff: write contract and return, do not transition yet.
            if analysis_status == "WAITING_FOR_HERMES_ANALYSIS":
                analysis_request_id = str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"analysis-request:{doc['report_id']}:v{version}",
                ))
                handoff = build_hermes_handoff_from_artifact(
                    analysis_request_id=analysis_request_id,
                    report_id=doc["report_id"],
                    report_version=version,
                    artifact=doc,
                    period_type="monthly",
                )
                reports.write_analysis_request(analysis_request_id, handoff)
                _finish_job(sb, job_id, "SUCCEEDED")
                logger.info(
                    "canonical_monthly: %s v%d -> WAITING_FOR_HERMES_ANALYSIS (request=%s)",
                    client_id, version, analysis_request_id,
                )
                return {
                    "client_id":           client_id,
                    "status":              "WAITING_FOR_HERMES_ANALYSIS",
                    "report_id":           doc["report_id"],
                    "analysis_request_id": analysis_request_id,
                }

            # Quality gate: only transition when analysis is complete.
            gate_passes, violations = check_quality_gate(
                doc.get("content", {}), analysis_enabled=analysis_enabled
            )
            if not gate_passes:
                _finish_job(sb, job_id, "SUCCEEDED")
                logger.warning(
                    "canonical_monthly: %s v%d quality gate FAIL — left as DRAFT: %s",
                    client_id, version, violations,
                )
                return {
                    "client_id":  client_id,
                    "status":     "DRAFT_QUALITY_GATE_FAILED",
                    "report_id":  doc["report_id"],
                    "violations": violations,
                }

            at  = datetime.now(timezone.utc).isoformat()
            doc = reports.transition(
                doc["report_id"], version, "READY_FOR_REVIEW",
                actor=_ACTOR, at=at, expected_hash=doc["artifact_hash"],
            )

        _finish_job(sb, job_id, "SUCCEEDED")
        logger.info(
            "canonical_monthly: %s %s v%d -> %s (report_id=%s)",
            client_id, period_end, version, doc["status"], doc["report_id"],
        )
        return {
            "client_id": client_id,
            "status":    doc["status"],
            "report_id": doc["report_id"],
        }

    except Exception as exc:
        error = str(exc)[:_ERROR_TRUNC]
        _finish_job(sb, job_id, "FAILED", error)
        logger.error("canonical_monthly: %s v%d failed: %s", client_id, version, exc)
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
