"""
Outcome evaluator for core_actions.

evaluate_pending_outcomes() — idempotent; finds EXECUTED actions whose
evaluation window has passed with no CONFIRMED/POSITIVE/NEGATIVE/NEUTRAL outcome,
evaluates deterministically, and writes an outcome row.

Attribution language: "result observed after action" — causal claims are never
made. Confidence reflects data volume, window, and data health.

Never fabricates improvement. If evidence is insufficient → INCONCLUSIVE.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from ..database import get_supabase

logger = logging.getLogger(__name__)

# Outcome statuses that represent a completed evaluation
_FINAL_STATUSES = {"CONFIRMED", "POSITIVE", "NEGATIVE", "NEUTRAL", "INCONCLUSIVE", "BLOCKED_DATA_QUALITY"}

# Default evaluation windows per action_type (days)
_DEFAULT_WINDOWS: dict[str, int] = {
    "MANUAL_EXTERNAL_ACTION": 7,
    "INTERNAL_ACTION": 3,
    "NO_ACTION_REQUIRED": 1,
}

# Percentage threshold for NEUTRAL outcome (within ±5% of baseline = neutral)
_NEUTRAL_BAND_PCT = 5.0


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_ts(raw: object) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _compute_confidence(
    *,
    has_before: bool,
    has_after: bool,
    data_days: Optional[int],
    window_days: int,
    data_health_ok: bool,
) -> str:
    if not has_before or not has_after:
        return "LOW"
    if not data_health_ok:
        return "LOW"
    if data_days is None or data_days < 2:
        return "LOW"
    if data_days >= window_days * 0.7 and data_health_ok:
        return "HIGH"
    return "MEDIUM"


def _outcome_status_from_delta(delta_pct: Optional[float]) -> str:
    """
    Translate numeric delta into outcome status.
    Direction: positive delta = improvement (POSITIVE), negative = regression (NEGATIVE).
    """
    if delta_pct is None:
        return "INCONCLUSIVE"
    if abs(delta_pct) <= _NEUTRAL_BAND_PCT:
        return "NEUTRAL"
    return "POSITIVE" if delta_pct > 0 else "NEGATIVE"


def _evaluate_balance_action(action: dict, sb) -> dict:
    """
    For MANUAL_EXTERNAL_ACTION on a balance-related entity (GOOGLE_BALANCE_RECHARGE etc.):
    check the latest balance snapshot to determine outcome.
    """
    client_id_uuid = str(action.get("client_id", ""))
    platform = (action.get("platform") or "").lower()
    before = action.get("before_state") or {}

    # core_balance_snapshots.client_id is TEXT (slug), not UUID.
    # Resolve UUID → slug via the clients table before querying snapshots.
    client_slug = client_id_uuid  # safe fallback: UUID won't match any slug row
    try:
        slug_res = (
            sb.table("clients")
            .select("client_id")
            .eq("id", client_id_uuid)
            .limit(1)
            .execute()
        )
        if slug_res and slug_res.data:
            client_slug = slug_res.data[0]["client_id"]
        else:
            logger.warning(
                "outcome_evaluator: client UUID %s not found in clients table — "
                "balance snapshot lookup will return 0 rows",
                client_id_uuid,
            )
    except Exception as exc:
        logger.warning(
            "outcome_evaluator: client slug lookup failed for UUID %s: %s — "
            "falling back to UUID (snapshot query will return 0 rows)",
            client_id_uuid, exc,
        )

    data_health: dict = {"note": "balance_snapshot_lookup"}

    try:
        q = (
            sb.table("core_balance_snapshots")
            .select("balance, balance_status, estimated_days_remaining, snapshot_at, collection_status")
            .eq("client_id", client_slug)
            .order("snapshot_at", desc=True)
            .limit(1)
        )
        if platform in ("google", "meta"):
            q = q.eq("platform", platform)
        res = q.execute()
        snap = res.data[0] if (res and res.data) else None
    except Exception as exc:
        logger.warning("outcome_evaluator: balance_snapshot lookup failed for %s: %s", client_id_uuid, exc)
        snap = None

    if not snap:
        return {
            "status": "BLOCKED_DATA_QUALITY",
            "confidence": "LOW",
            "delta": None,
            "delta_pct": None,
            "data_health": {"note": "no_balance_snapshot"},
            "after_state": {},
        }

    # collector writes "PASS" for success; accept "OK" for forward-compatibility
    collection_ok = snap.get("collection_status") in ("OK", "PASS")
    days_remaining = snap.get("estimated_days_remaining")
    balance = snap.get("balance")
    balance_status = snap.get("balance_status", "MISSING")

    after_state = {
        "balance": balance,
        "balance_status": balance_status,
        "estimated_days_remaining": days_remaining,
        "platform": platform,
        "snapshot_at": snap.get("snapshot_at"),
    }
    data_health["collection_ok"] = collection_ok
    data_health["balance_status"] = balance_status

    before_balance = before.get("balance", 0) or 0
    before_balance_status = before.get("balance_status", "EXHAUSTED")

    if not collection_ok:
        return {
            "status": "BLOCKED_DATA_QUALITY",
            "confidence": "LOW",
            "delta": None,
            "delta_pct": None,
            "data_health": data_health,
            "after_state": after_state,
        }

    # Operational outcome: was balance restored?
    if balance and balance > 0 and balance_status not in ("EXHAUSTED", "MISSING"):
        if before_balance_status in ("EXHAUSTED", "MISSING", "CRITICAL"):
            outcome_status = "POSITIVE"
            confidence = "HIGH"
            delta = (balance or 0) - before_balance
            delta_pct = ((delta / max(before_balance, 1)) * 100) if before_balance else None
        elif (days_remaining or 0) > 3:
            outcome_status = "POSITIVE"
            confidence = "MEDIUM"
            delta = (balance or 0) - before_balance
            delta_pct = None
        else:
            outcome_status = "NEUTRAL"
            confidence = "MEDIUM"
            delta = None
            delta_pct = None
    else:
        # Balance still exhausted / not recovered
        outcome_status = "NEGATIVE"
        confidence = "HIGH"
        delta = None
        delta_pct = None

    return {
        "status": outcome_status,
        "confidence": confidence,
        "delta": {"balance_delta": delta},
        "delta_pct": delta_pct,
        "data_health": data_health,
        "after_state": after_state,
    }


def _evaluate_generic_action(action: dict) -> dict:
    """
    For actions with before_state and after_state already populated (manually set).
    Compares primary numeric metric if available.
    """
    before = action.get("before_state") or {}
    after = action.get("after_state") or {}

    if not before or not after:
        return {
            "status": "INCONCLUSIVE",
            "confidence": "LOW",
            "delta": None,
            "delta_pct": None,
            "data_health": {"note": "missing_before_or_after_state"},
            "after_state": after,
        }

    # Try to find a numeric primary metric to compare
    delta_dict: dict = {}
    delta_pct: Optional[float] = None
    primary_key: Optional[str] = None

    for k in ("balance", "roas", "cpa", "revenue", "conversions", "spend", "impressions"):
        if k in before and k in after:
            try:
                b_val = float(before[k])
                a_val = float(after[k])
                d = a_val - b_val
                delta_dict[k] = round(d, 4)
                if b_val != 0:
                    # Invert for cost metrics (lower CPA = better)
                    if k in ("cpa",):
                        delta_pct = round(-((d / b_val) * 100), 2)
                    else:
                        delta_pct = round((d / b_val) * 100, 2)
                primary_key = k
                break
            except (TypeError, ValueError):
                continue

    outcome_status = _outcome_status_from_delta(delta_pct)
    confidence = _compute_confidence(
        has_before=bool(before),
        has_after=bool(after),
        data_days=None,
        window_days=action.get("evaluation_window_days", 7),
        data_health_ok=True,
    )

    return {
        "status": outcome_status,
        "confidence": confidence,
        "delta": delta_dict if delta_dict else None,
        "delta_pct": delta_pct,
        "data_health": {"primary_metric": primary_key, "note": "manual_state_comparison"},
        "after_state": after,
    }


# ── Learning candidate generation ─────────────────────────────────────────────

_LEARNING_ELIGIBLE = {"POSITIVE", "NEGATIVE", "NEUTRAL"}


def _generate_learning_candidate(
    sb,
    action: dict,
    outcome_id: str,
    outcome_status: str,
    recommendation_id: str,
    client_id_uuid: str,
    delta_pct: Optional[float],
) -> Optional[str]:
    """
    Write a PENDING learning candidate linked to this outcome.
    Returns the learning_candidate_id or None on failure.
    Never auto-approves.
    """
    if outcome_status not in _LEARNING_ELIGIBLE:
        return None

    action_type = action.get("action_type", "MANUAL_EXTERNAL_ACTION")
    platform = action.get("platform") or ""
    entity_type = action.get("entity_type") or ""

    direction = "melhora" if outcome_status == "POSITIVE" else ("piora" if outcome_status == "NEGATIVE" else "não altera")
    statement = (
        f"{action_type} em {platform or 'N/A'} ({entity_type or 'N/A'}) "
        f"{direction} o resultado observado após a ação."
    )
    pattern = f"{action_type}:{platform}:{entity_type}:{outcome_status}"

    observed_outcome = {
        "outcome_status": outcome_status,
        "delta_pct": delta_pct,
        "action_type": action_type,
        "platform": platform,
    }

    scope = "CLIENT_ONLY"
    evidence_level = "OBSERVATION"

    row = {
        "statement": statement,
        "scope": scope,
        "evidence_level": evidence_level,
        "evidence_refs": [outcome_id],
        "context": {"action_id": str(action["id"]), "recommendation_id": recommendation_id},
        "client_id": client_id_uuid,
        "visibility_scope": "AGENCY_ONLY",
        "status": "PENDING",
        "source_action_id": str(action["id"]),
        "source_recommendation_id": recommendation_id,
        "pattern": pattern,
        "observed_outcome": observed_outcome,
    }

    try:
        res = sb.table("core_learning_candidates").insert(row).execute()
        candidate_id = res.data[0]["id"] if (res and res.data) else None
        logger.info(
            "outcome_evaluator: learning candidate %s created for action %s (outcome=%s)",
            candidate_id, action["id"], outcome_status,
        )
        return candidate_id
    except Exception as exc:
        logger.error("outcome_evaluator: learning_candidate insert failed for action %s: %s", action["id"], exc)
        return None


# ── Main evaluation function ───────────────────────────────────────────────────

def _is_balance_action(action: dict) -> bool:
    entity_type = (action.get("entity_type") or "").upper()
    return "BALANCE" in entity_type or "RECHARGE" in entity_type


def evaluate_action_outcome(action_id: str) -> dict:
    """
    On-demand evaluation for a single action.
    Returns summary dict with outcome_id, status, confidence.
    """
    sb = get_supabase()
    try:
        res = sb.table("core_actions").select("*").eq("id", action_id).limit(1).execute()
        action = res.data[0] if (res and res.data) else None
    except Exception as exc:
        return {"error": f"action lookup failed: {exc}"}
    if not action:
        return {"error": f"action not found: {action_id}"}
    return _evaluate_single(action, sb)


def _evaluate_single(action: dict, sb) -> dict:
    action_id = str(action["id"])
    recommendation_id = str(action.get("recommendation_id", ""))
    client_id_uuid = str(action.get("client_id", ""))

    # Check for existing final outcome
    try:
        existing = (
            sb.table("core_outcomes")
            .select("id, status")
            .eq("action_id", action_id)
            .limit(1)
            .execute()
        )
        if existing and existing.data:
            row = existing.data[0]
            if row["status"] in _FINAL_STATUSES:
                return {"skipped": True, "outcome_id": str(row["id"]), "status": row["status"]}
    except Exception as exc:
        logger.warning("outcome_evaluator: existing outcome check failed %s: %s", action_id, exc)

    # Route to correct evaluator
    if _is_balance_action(action):
        result = _evaluate_balance_action(action, sb)
    elif action.get("after_state"):
        result = _evaluate_generic_action(action)
    else:
        result = {
            "status": "INCONCLUSIVE",
            "confidence": "LOW",
            "delta": None,
            "delta_pct": None,
            "data_health": {"note": "no_after_state_available"},
            "after_state": {},
        }

    executed_at = _parse_ts(action.get("executed_at")) or datetime.now(timezone.utc)
    window_days = int(action.get("evaluation_window_days") or 7)

    outcome_row = {
        "action_id": action_id,
        "recommendation_id": recommendation_id or None,
        "client_id": client_id_uuid or None,
        "action_event_id": None,
        "measurement_period": {
            "executed_at": executed_at.isoformat(),
            "window_days": window_days,
        },
        "metric_refs": [],
        "before_state": action.get("before_state") or {},
        "after_state": result["after_state"],
        "delta": result["delta"],
        "status": result["status"],
        "evaluation_window_days": window_days,
        "confidence": result["confidence"],
        "data_health": result["data_health"],
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "created_by": "outcome_evaluator",
    }

    try:
        ins = sb.table("core_outcomes").insert(outcome_row).execute()
        outcome_id = str(ins.data[0]["id"]) if (ins and ins.data) else None
    except Exception as exc:
        logger.error("outcome_evaluator: outcome insert failed for action %s: %s", action_id, exc)
        return {"error": f"outcome insert failed: {exc}"}

    logger.info(
        "outcome_evaluator: action %s → outcome %s status=%s confidence=%s",
        action_id, outcome_id, result["status"], result["confidence"],
    )

    # Generate learning candidate for non-inconclusive outcomes
    if outcome_id and result["status"] in _LEARNING_ELIGIBLE:
        _generate_learning_candidate(
            sb, action, outcome_id, result["status"],
            recommendation_id, client_id_uuid, result.get("delta_pct"),
        )

    return {
        "outcome_id": outcome_id,
        "status": result["status"],
        "confidence": result["confidence"],
        "action_id": action_id,
    }


def evaluate_pending_outcomes() -> dict:
    """
    Scheduler entry point — idempotent.
    Finds all EXECUTED actions whose evaluation window has passed and no
    final outcome exists. Evaluates and persists each.
    """
    sb = get_supabase()
    now = datetime.now(timezone.utc)

    try:
        res = (
            sb.table("core_actions")
            .select("*")
            .eq("status", "EXECUTED")
            .not_.is_("executed_at", "null")
            .limit(100)
            .execute()
        )
        actions = res.data or []
    except Exception as exc:
        logger.error("outcome_evaluator: action fetch failed: %s", exc)
        return {"error": str(exc)}

    evaluated = 0
    skipped = 0
    errors = 0

    for action in actions:
        executed_at = _parse_ts(action.get("executed_at"))
        window_days = int(action.get("evaluation_window_days") or 7)
        if not executed_at:
            continue
        if now < executed_at + timedelta(days=window_days):
            skipped += 1
            continue

        result = _evaluate_single(action, sb)
        if result.get("error"):
            errors += 1
        elif result.get("skipped"):
            skipped += 1
        else:
            evaluated += 1

    logger.info(
        "outcome_evaluator: run complete — evaluated=%d skipped=%d errors=%d",
        evaluated, skipped, errors,
    )
    return {"evaluated": evaluated, "skipped": skipped, "errors": errors, "total_checked": len(actions)}
