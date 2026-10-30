"""
Hermes Analysis Worker — CANONICAL REPORT ENGINE

Polls core_canonical_report_records for PENDING analysis-request contracts,
calls Anthropic API using the pre-built hermes_prompt, and applies the result
via Reports.apply_analysis().

Architecture: OPTION B (Hermes calls Core).
  - Core generates the report and writes the handoff contract, then stops.
  - This worker discovers pending contracts independently via scheduler.
  - Core does NOT import or call this module during report generation.

Flags:
  CANONICAL_HERMES_WORKER_ENABLED=true  — enables the scheduled job
  ANTHROPIC_API_KEY                     — required; falls back to SKIPPED if absent
  ANTHROPIC_MODEL                       — defaults to claude-sonnet-4-6

Scheduler: registered in main.py under id="canonical_hermes_worker".
Replay:    POST /agency/v1/canonical-reports/hermes-worker/run (agency_admin only).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

FLAG_HERMES_WORKER_ENABLED = "CANONICAL_HERMES_WORKER_ENABLED"
_DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS    = 8192
_MAX_PENDING   = 20


# ── Field normaliser ──────────────────────────────────────────────────────────

def _normalise_prompt_response(resp: dict) -> dict:
    """
    The hermes_prompt asks Claude for:
      google_analysis / meta_analysis (top-level)  →  channel_analysis.{google,meta}
      priorities_next_week                          →  priorities
      analysis_notes                                →  limitations

    This transform bridges the prompt output format to the HermesAnalysis model.
    """
    resp = dict(resp)

    # Nest platform analyses under channel_analysis
    ch: dict = resp.pop("channel_analysis", {})
    if "google_analysis" in resp:
        ch["google"] = resp.pop("google_analysis")
    if "meta_analysis" in resp:
        ch["meta"] = resp.pop("meta_analysis")
    if ch:
        resp["channel_analysis"] = ch

    # Rename priorities_next_week → priorities
    if "priorities_next_week" in resp and "priorities" not in resp:
        resp["priorities"] = resp.pop("priorities_next_week")

    # Rename analysis_notes → limitations
    if "analysis_notes" in resp and "limitations" not in resp:
        resp["limitations"] = resp.pop("analysis_notes")

    return resp


# ── Single-request processor ──────────────────────────────────────────────────

def _run_single(sb, doc: dict) -> dict:
    """Process one pending analysis-request. Returns a status dict."""
    from ..config import settings
    from .canonical_reports import CoreStore, Flags, Reports, ReportError

    analysis_request_id: str = doc.get("analysis_request_id", "")
    report_id:           str = doc.get("report_id", "")
    report_version:      int = doc.get("report_version") or 0
    hermes_prompt:       str = doc.get("hermes_prompt", "")
    client_id:           str = doc.get("client_id", "unknown")

    tag = f"hermes_worker[{analysis_request_id[:12]}]"

    if not all([analysis_request_id, report_id, report_version, hermes_prompt]):
        logger.warning("%s missing required fields — skipping", tag)
        return {"status": "SKIPPED", "reason": "missing_fields",
                "analysis_request_id": analysis_request_id}

    # Guard: check report still WAITING before calling Anthropic
    store = CoreStore(sb)
    flags = Flags(CANONICAL_WEEKLY_REVIEW_ENABLED=True,
                  CANONICAL_MONTHLY_REVIEW_ENABLED=True)
    rpts  = Reports(store, flags)
    try:
        base = rpts.get(report_id, report_version)
    except ReportError:
        logger.warning("%s report not found — skipping", tag)
        return {"status": "SKIPPED", "reason": "report_not_found",
                "analysis_request_id": analysis_request_id}

    base_status = base.get("content", {}).get("analysis_layer", {}).get("status")
    if base_status != "WAITING_FOR_HERMES_ANALYSIS":
        logger.info("%s already processed (status=%s)", tag, base_status)
        return {"status": "ALREADY_APPLIED", "analysis_request_id": analysis_request_id}

    # Anthropic key check
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        logger.info("%s ANTHROPIC_API_KEY absent — skipping", tag)
        return {"status": "SKIPPED", "reason": "no_anthropic_key",
                "analysis_request_id": analysis_request_id}

    model = settings.ANTHROPIC_MODEL or _DEFAULT_MODEL

    # ── Call Anthropic ────────────────────────────────────────────────────────
    try:
        import anthropic as _anthropic
        ac = _anthropic.Anthropic(api_key=api_key, max_retries=1)
        msg = ac.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": hermes_prompt}],
        )
        raw: str = msg.content[0].text.strip()
        # Strip markdown code block if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0].strip()
        logger.info("%s Anthropic OK — %d chars", tag, len(raw))
    except Exception as exc:
        logger.error("%s Anthropic error: %s", tag, exc)
        return {"status": "FAILED", "reason": f"anthropic_error:{str(exc)[:120]}",
                "analysis_request_id": analysis_request_id}

    # ── Parse JSON ────────────────────────────────────────────────────────────
    try:
        payload: dict = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("%s JSON parse error: %s\nraw_prefix: %s", tag, exc, raw[:200])
        return {"status": "FAILED", "reason": "json_parse_error",
                "analysis_request_id": analysis_request_id}

    # ── Normalise field names from prompt format → HermesAnalysis model ───────
    payload = _normalise_prompt_response(payload)
    payload["analysis_request_id"] = analysis_request_id
    payload["report_version"]      = report_version

    # ── Apply via Core Reports ────────────────────────────────────────────────
    try:
        at  = datetime.now(timezone.utc).isoformat()
        doc_out = rpts.apply_analysis(
            report_id, report_version, payload, analysis_request_id,
            actor="canonical_analysis_callback", at=at,
        )
        logger.info("%s APPLIED → version=%s status=%s",
                    tag, doc_out.get("version"), doc_out.get("status"))
        return {
            "status":               "APPLIED",
            "analysis_request_id":  analysis_request_id,
            "report_id":            report_id,
            "client_id":            client_id,
            "new_version":          doc_out.get("version"),
            "report_status":        doc_out.get("status"),
        }
    except Exception as exc:
        err = str(exc)
        if "BASE_VERSION_NOT_WAITING_FOR_ANALYSIS" in err:
            return {"status": "ALREADY_APPLIED", "analysis_request_id": analysis_request_id}
        logger.error("%s apply_analysis error: %s", tag, exc)
        return {"status": "FAILED", "reason": err[:120],
                "analysis_request_id": analysis_request_id}


# ── Main entry point ──────────────────────────────────────────────────────────

def run_hermes_worker_all(sb=None) -> dict:
    """
    Discover and process all pending Hermes analysis requests.
    Called by scheduler (canonical_hermes_worker job) and by the replay endpoint.
    """
    if os.getenv(FLAG_HERMES_WORKER_ENABLED, "false").strip().lower() != "true":
        return {"skipped": True, "reason": f"{FLAG_HERMES_WORKER_ENABLED}=false"}

    if sb is None:
        from ..database import get_supabase
        sb = get_supabase()

    try:
        rows = (
            sb.table("core_canonical_report_records")
            .select("entity_key,document")
            .like("entity_key", "analysis-request:%")
            .contains("document", {"status": "PENDING"})
            .order("entity_key", desc=False)
            .limit(_MAX_PENDING)
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("hermes_worker: failed to query pending: %s", exc)
        return {"error": str(exc)[:120]}

    results = []
    for row in rows:
        doc    = row.get("document") or {}
        result = _run_single(sb, doc)
        results.append(result)
        logger.info("hermes_worker: %s → %s",
                    doc.get("analysis_request_id", "?")[:12], result.get("status"))

    applied = sum(1 for r in results if r.get("status") == "APPLIED")
    already = sum(1 for r in results if r.get("status") == "ALREADY_APPLIED")
    skipped = sum(1 for r in results if r.get("status") == "SKIPPED")
    failed  = sum(1 for r in results if r.get("status") == "FAILED")

    return {
        "total":          len(results),
        "applied":        applied,
        "already_applied": already,
        "skipped":        skipped,
        "failed":         failed,
        "results":        results,
    }
