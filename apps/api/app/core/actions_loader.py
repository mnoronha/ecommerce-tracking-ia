"""
Actions Loader — shared utility for report builders.

Queries core_actions (status=EXECUTED, executed_at in period) and
core_recommendations (status PROPOSED/APPROVED/PENDING_REVIEW).

Security invariants:
- Never alters action status, data, before/after, outcome, actor, or source recommendation.
- Returns read-only snapshots; callers must not mutate returned dicts before storage.
- PROPOSED recommendations are never returned by load_executed_actions.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_OUTCOME_STATUS_MAP = {
    "CONFIRMED":            ("Impacto confirmado", "ok"),
    "PENDING":              ("Avaliando impacto",  "pending"),
    "BLOCKED_DATA_QUALITY": ("Dados insuficientes", "blocked"),
    "NO_IMPACT":            ("Sem impacto observado", "blocked"),
}


def format_executed_actions(actions: list[dict]) -> list[dict]:
    """Convert raw core_actions rows into template-ready dicts for Handlebars/React."""
    result = []
    for a in actions:
        executed_at = a.get("executed_at")
        try:
            from datetime import datetime as _dt
            data_label = _dt.fromisoformat(str(executed_at).replace("Z", "+00:00")).strftime("%d/%m/%Y") if executed_at else ""
        except Exception:
            data_label = str(executed_at or "")

        outcome_raw = a.get("outcome") or {}
        outcome_out: Any = None
        if outcome_raw:
            status = (outcome_raw.get("status") or "PENDING").upper()
            status_label, status_css = _OUTCOME_STATUS_MAP.get(status, (status.replace("_", " ").title(), "pending"))
            delta = outcome_raw.get("delta") or {}
            delta_label = ""
            if isinstance(delta, dict):
                for k, v in delta.items():
                    try:
                        num = float(v)
                        sign = "+" if num >= 0 else ""
                        delta_label = f"{sign}{round(num, 1):g}% {k}"
                    except Exception:
                        delta_label = f"{k}: {v}"
                    break
            outcome_out = {
                "status":       status,
                "status_label": status_label,
                "status_css":   status_css,
                "delta_label":  delta_label or None,
                "confidence":   (outcome_raw.get("confidence") or "").upper() or None,
            }

        result.append({
            "id":          a.get("id"),
            "data":        data_label,
            "platform":    (a.get("platform") or "").upper() or None,
            "action_type": (a.get("action_type") or "").replace("_", " ").title() or None,
            "description": a.get("description") or "",
            "executed_by": a.get("executed_by"),
            "outcome":     outcome_out,
        })
    return result


def format_open_recommendations(recs: list[dict]) -> list[dict]:
    """Convert raw core_recommendations rows into template-ready dicts."""
    result = []
    for i, r in enumerate(recs, 1):
        prio = (r.get("priority") or "").upper()
        priority_label = {"HIGH": "!", "MEDIUM": "→", "LOW": "→"}.get(prio, str(i))
        result.append({
            "id":              r.get("id"),
            "priority_label":  priority_label,
            "title":           r.get("title") or r.get("action") or "",
            "rationale":       r.get("rationale"),
            "expected_impact": r.get("expected_impact"),
        })
    return result


def load_executed_actions(
    sb,
    client_uuid: str,
    period_start: date,
    period_end: date,
) -> list[dict]:
    """
    Load actions with status=EXECUTED and executed_at within [period_start, period_end].
    Enriches with recommendation title/expected_impact and linked outcome when available.
    """
    start_iso = datetime(
        period_start.year, period_start.month, period_start.day,
        0, 0, 0, tzinfo=timezone.utc,
    ).isoformat()
    end_iso = datetime(
        period_end.year, period_end.month, period_end.day,
        23, 59, 59, tzinfo=timezone.utc,
    ).isoformat()

    try:
        res = (
            sb.table("core_actions")
            .select(
                "id, action_type, platform, entity_type, entity_id, "
                "status, executed_by, executed_at, reason, "
                "before_state, after_state, evaluation_window_days, "
                "recommendation_id, diagnosis_id, alert_id"
            )
            .eq("client_id", client_uuid)
            .eq("status", "EXECUTED")
            .gte("executed_at", start_iso)
            .lte("executed_at", end_iso)
            .order("executed_at")
            .execute()
        )
        actions = res.data or []
    except Exception as exc:
        logger.warning(
            "actions_loader: load_executed_actions failed client=%s: %s", client_uuid, exc
        )
        return []

    if not actions:
        return []

    # Enrich with recommendation title/expected_impact
    rec_ids = list({str(a["recommendation_id"]) for a in actions if a.get("recommendation_id")})
    rec_map: dict[str, dict] = {}
    if rec_ids:
        try:
            rr = (
                sb.table("core_recommendations")
                .select("id, title, expected_impact, action")
                .in_("id", rec_ids)
                .execute()
            )
            for r in (rr.data or []):
                rec_map[str(r["id"])] = r
        except Exception as exc:
            logger.warning("actions_loader: recommendation enrich failed: %s", exc)

    # Load outcomes linked to these actions
    action_ids = [str(a["id"]) for a in actions]
    outcome_map: dict[str, dict] = {}
    try:
        ores = (
            sb.table("core_outcomes")
            .select(
                "action_id, delta, status, confidence, "
                "measurement_period, before_state, after_state"
            )
            .in_("action_id", action_ids)
            .execute()
        )
        for o in (ores.data or []):
            aid = str(o["action_id"])
            if aid not in outcome_map:
                outcome_map[aid] = o
    except Exception as exc:
        logger.warning("actions_loader: outcome load failed: %s", exc)

    result: list[dict] = []
    for a in actions:
        rec = rec_map.get(str(a.get("recommendation_id") or "")) or {}
        outcome = outcome_map.get(str(a["id"]))

        entry: dict[str, Any] = {
            "id":                str(a["id"]),
            "executed_at":       a.get("executed_at"),
            "platform":          a.get("platform"),
            "action_type":       a.get("action_type"),
            "entity_type":       a.get("entity_type"),
            "entity_id":         a.get("entity_id"),
            "description":       a.get("reason") or rec.get("title") or rec.get("action") or "",
            "executed_by":       a.get("executed_by"),
            "before_state":      a.get("before_state"),
            "after_state":       a.get("after_state"),
            "recommendation_id": str(a["recommendation_id"]) if a.get("recommendation_id") else None,
            "diagnosis_id":      str(a["diagnosis_id"]) if a.get("diagnosis_id") else None,
            "alert_id":          a.get("alert_id"),
            "expected_impact":   rec.get("expected_impact"),
            "outcome": {
                "status":             outcome.get("status"),
                "confidence":         outcome.get("confidence"),
                "delta":              outcome.get("delta"),
                "before_state":       outcome.get("before_state"),
                "after_state":        outcome.get("after_state"),
                "measurement_period": outcome.get("measurement_period"),
            } if outcome else None,
        }
        result.append(entry)

    logger.info(
        "actions_loader: %d executed actions for client=%s period=%s→%s",
        len(result), client_uuid, period_start.isoformat(), period_end.isoformat(),
    )
    return result


def load_open_recommendations(sb, client_uuid: str) -> list[dict]:
    """
    Load open recommendations (PROPOSED, APPROVED, PENDING_REVIEW).
    These represent upcoming/proposed work — never mark as executed.
    """
    try:
        res = (
            sb.table("core_recommendations")
            .select(
                "id, title, action, rationale, priority, "
                "expected_impact, risk, status, created_at"
            )
            .eq("client_id", client_uuid)
            .in_("status", ["PROPOSED", "APPROVED", "PENDING_REVIEW"])
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        rows = res.data or []
    except Exception as exc:
        logger.warning(
            "actions_loader: load_open_recommendations failed client=%s: %s", client_uuid, exc
        )
        return []

    result = [
        {
            "id":              str(r["id"]),
            "title":           r.get("title"),
            "action":          r.get("action"),
            "rationale":       r.get("rationale"),
            "priority":        r.get("priority"),
            "expected_impact": r.get("expected_impact"),
            "risk":            r.get("risk"),
            "status":          r.get("status"),
            "created_at":      r.get("created_at"),
        }
        for r in rows
    ]
    logger.info(
        "actions_loader: %d open recommendations for client=%s",
        len(result), client_uuid,
    )
    return result
