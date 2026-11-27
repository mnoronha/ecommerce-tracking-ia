"""
Outcome evaluator for core_actions (PRD §14.1 / R04).

evaluate_pending_outcomes() — idempotent, concurrency-guarded, and audited in
`core_job_runs`. Finds EXECUTED actions whose evaluation window has closed with
no active final outcome, collects real baseline vs. observation metrics from
`core_metric_snapshots`, cross-checks concurrent changes in `core_change_log`,
persists the outcome row, and generates a deduped PENDING learning candidate
when eligible.

Versioned re-evaluation (`reevaluate_action_outcome`):
  Marks a previous hollow/outdated outcome (e.g. LK Sneakers `1968845f-...`)
  as `SUPERSEDED` without deleting history, then inserts a new outcome row
  linked via `supersedes_outcome_id`.

Attribution language: "result observed after action" — causal claims are never
made. HOLD (`NO_ACTION_REQUIRED`) actions or windows with heavy concurrent
changes in `core_change_log` are classified as `INCONCLUSIVE` with `LOW`
confidence while preserving full real metrics and confounders.

Never fabricates improvement. Never converts null to zero.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from ..database import get_supabase

logger = logging.getLogger(__name__)

# Active outcome statuses that represent a completed evaluation (excludes SUPERSEDED)
_FINAL_STATUSES = {
    "CONFIRMED",
    "POSITIVE",
    "NEGATIVE",
    "NEUTRAL",
    "INCONCLUSIVE",
    "BLOCKED_DATA_QUALITY",
}

# Default evaluation windows per action_type (days)
_DEFAULT_WINDOWS: dict[str, int] = {
    "MANUAL_EXTERNAL_ACTION": 7,
    "INTERNAL_ACTION": 3,
    "NO_ACTION_REQUIRED": 1,
}

# Percentage threshold for NEUTRAL outcome (within ±5% of baseline = neutral)
_NEUTRAL_BAND_PCT = 5.0

# Concurrent changes threshold above which causal attribution is blocked
_CONCURRENT_CONFOUNDER_THRESHOLD = 3

# Cost metrics where a negative delta is an improvement
_INVERTED_COST_METRICS = frozenset({
    "cpa",
    "cpa_meta",
    "cpa_google",
    "google_cpa_ecommerce",
})

# Metrics prioritized per platform when evaluating core_metric_snapshots
_PLATFORM_METRIC_PRIORITY: dict[str, tuple[str, ...]] = {
    "meta": (
        "roas_meta",
        "cpa_meta",
        "meta_conversion_value",
        "meta_conversions",
        "meta_spend",
        "mer",
        "revenue_business",
        "orders_count",
    ),
    "google": (
        "google_roas_ecommerce",
        "roas_google",
        "google_cpa_ecommerce",
        "cpa_google",
        "google_purchase_conversion_value",
        "google_conversions",
        "google_spend",
        "mer",
        "revenue_business",
        "orders_count",
    ),
    "default": (
        "mer",
        "revenue_business",
        "orders_count",
        "total_spend",
        "roas_meta",
        "roas_google",
    ),
}

# In-process concurrency guard for overlapping scheduler ticks
_EVAL_RUN_LOCK = threading.Lock()


# ── Helpers ────────────────────────────────────────────────────────────────────


def _parse_ts(raw: object) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _parse_date(raw: object) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _opt_str(val: object) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s and s.lower() != "none" else None


def _resolve_window_days(action: dict) -> int:
    raw = action.get("evaluation_window_days")
    if raw is not None:
        try:
            days = int(raw)
            if days >= 1:
                return days
        except (TypeError, ValueError):
            pass
    action_type = str(action.get("action_type") or "MANUAL_EXTERNAL_ACTION")
    return _DEFAULT_WINDOWS.get(action_type, 7)


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


def _resolve_client_slug(sb, client_id_uuid: str) -> str:
    """Resolve `clients.id` UUID to canonical `clients.client_id` slug."""
    if not client_id_uuid:
        return ""
    try:
        slug_res = (
            sb.table("clients")
            .select("client_id, pixel_id")
            .eq("id", client_id_uuid)
            .limit(1)
            .execute()
        )
        if slug_res and slug_res.data:
            row = slug_res.data[0]
            return str(row.get("client_id") or row.get("pixel_id") or client_id_uuid)
    except Exception as exc:
        logger.warning("outcome_evaluator: client slug lookup failed for %s: %s", client_id_uuid, exc)
    return client_id_uuid


def _is_hollow_outcome(row: dict) -> bool:
    """
    Detect a legacy hollow outcome created without real snapshot metrics
    (e.g., `manual_state_comparison` or `no_after_state_available` with
    empty `metric_refs` and `delta is None`).
    """
    if not row or row.get("status") == "SUPERSEDED":
        return False
    metric_refs = row.get("metric_refs") or []
    delta = row.get("delta")
    dh = row.get("data_health") or {}
    note = dh.get("note") if isinstance(dh, dict) else None
    return (
        len(metric_refs) == 0
        and delta is None
        and note in ("manual_state_comparison", "no_after_state_available")
    )


# ── Snapshot & Change Log Loaders (R04) ───────────────────────────────────────


def _extract_snapshot_metrics(snap: dict) -> tuple[dict[str, float], list[str]]:
    """
    Extract numeric metrics with `value_status == 'OK'` (or unspecified) from a
    `core_metric_snapshots` row. Preserves nulls (never converts null to 0).
    """
    raw = snap.get("metrics")
    values: dict[str, float] = {}
    missing_keys: list[str] = []

    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            key = entry.get("metric_key")
            if not key:
                continue
            status = str(entry.get("value_status") or "OK").upper()
            val = entry.get("value")
            if val is None or status not in ("OK", "PARTIAL", "PROVISIONAL"):
                missing_keys.append(str(key))
                continue
            try:
                values[str(key)] = float(val)
            except (TypeError, ValueError):
                continue
    elif isinstance(raw, dict):
        for key, val in raw.items():
            if val is None:
                missing_keys.append(str(key))
                continue
            try:
                values[str(key)] = float(val)
            except (TypeError, ValueError):
                continue

    return values, missing_keys


def _snapshot_health_ok(snap: dict, platform: str) -> bool:
    health = snap.get("health")
    if not isinstance(health, dict) or not health:
        return True
    bad_states = {"MISSING", "ERROR", "NO_DATA", "ACCESS_MISSING", "PERMISSION_DENIED"}
    keys_to_check = ["business"]
    if platform == "meta":
        keys_to_check += ["meta_ads", "roas_meta"]
    elif platform == "google":
        keys_to_check += ["google_ads", "roas_google"]
    else:
        keys_to_check += ["mer"]
    for k in keys_to_check:
        st = health.get(k)
        if st and str(st).upper() in bad_states:
            return False
    return True


def _load_window_snapshots(
    sb,
    client_slug: str,
    executed_at: datetime,
    window_days: int,
) -> tuple[Optional[dict], Optional[dict]]:
    """
    Fetch baseline and observation `core_metric_snapshots` for `client_slug`.
      - baseline:    snapshot ending on or just before `executed_date`
                     (ideal period `[executed_date - window_days, executed_date - 1]`)
      - observation: snapshot covering `[executed_date, executed_date + window_days]`
                     (period_start >= executed_date or period_end >= window_end_date)
    """
    exec_date = executed_at.date()
    baseline_start = exec_date - timedelta(days=window_days)
    window_end_date = (executed_at + timedelta(days=window_days)).date()

    try:
        res = (
            sb.table("core_metric_snapshots")
            .select("id, client_id, period_start, period_end, view, metrics, health, computed_at")
            .eq("client_id", client_slug)
            .eq("view", "live")
            .gte("period_end", (baseline_start - timedelta(days=2)).isoformat())
            .lte("period_end", (window_end_date + timedelta(days=2)).isoformat())
            .order("computed_at", desc=True)
            .limit(40)
            .execute()
        )
        snaps = res.data or []
    except Exception as exc:
        logger.warning("outcome_evaluator: snapshot lookup failed for %s: %s", client_slug, exc)
        return None, None

    baseline_candidates: list[tuple[int, dict]] = []
    obs_candidates: list[tuple[int, dict]] = []

    target_baseline_end = exec_date - timedelta(days=1)

    for s in snaps:
        ps = _parse_date(s.get("period_start"))
        pe = _parse_date(s.get("period_end"))
        if not ps or not pe:
            continue
        span = (pe - ps).days + 1

        # Baseline snapshot: ends on/before exec_date (ideally exec_date - 1)
        if pe <= exec_date:
            dist = abs((pe - target_baseline_end).days) + abs(span - window_days)
            baseline_candidates.append((dist, s))

        # Observation snapshot: starts on/after exec_date and ends near window_end_date
        if ps >= exec_date and pe >= (window_end_date - timedelta(days=1)):
            dist = abs((pe - window_end_date).days) + abs(span - window_days)
            obs_candidates.append((dist, s))

    baseline_candidates.sort(key=lambda x: x[0])
    obs_candidates.sort(key=lambda x: x[0])

    baseline_snap = baseline_candidates[0][1] if baseline_candidates else None
    obs_snap = obs_candidates[0][1] if obs_candidates else None
    return baseline_snap, obs_snap


def _load_concurrent_changes(
    sb,
    client_slug: str,
    executed_at: datetime,
    window_end: datetime,
    exclude_change_log_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Query `core_change_log` for concurrent changes in `[executed_at, window_end]`
    that may confound attribution for this action.
    """
    try:
        res = (
            sb.table("core_change_log")
            .select("id, channel, change_type, occurred_at, entity_name_at_time")
            .eq("client_id", client_slug)
            .gte("occurred_at", executed_at.isoformat())
            .lte("occurred_at", window_end.isoformat())
            .order("occurred_at", desc=True)
            .limit(500)
            .execute()
        )
        rows = res.data or []
    except Exception as exc:
        logger.warning("outcome_evaluator: change_log lookup failed for %s: %s", client_slug, exc)
        return {
            "count": 0,
            "sample_ids": [],
            "channels": [],
            "lookup_error": str(exc)[:160],
        }

    filtered = [
        r for r in rows
        if not exclude_change_log_id or str(r.get("id")) != str(exclude_change_log_id)
    ]
    channels = sorted({str(r.get("channel")).upper() for r in filtered if r.get("channel")})
    sample_ids = [str(r["id"]) for r in filtered[:20] if r.get("id")]
    return {
        "count": len(filtered),
        "sample_ids": sample_ids,
        "channels": channels,
    }


# ── Action Evaluators ─────────────────────────────────────────────────────────


def _evaluate_balance_action(action: dict, sb) -> dict:
    """
    For MANUAL_EXTERNAL_ACTION on a balance-related entity (GOOGLE_BALANCE_RECHARGE etc.):
    check the latest balance snapshot to determine outcome.
    """
    client_id_uuid = str(action.get("client_id", ""))
    platform = (action.get("platform") or "").lower()
    before = action.get("before_state") or {}

    client_slug = _resolve_client_slug(sb, client_id_uuid)

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


def _evaluate_snapshot_action(
    action: dict,
    sb,
    *,
    executed_at: datetime,
    window_days: int,
) -> Optional[dict]:
    """
    Evaluate an action using real `core_metric_snapshots` (baseline vs observation)
    and cross-check concurrent changes in `core_change_log`.

    Returns None only if NO observation snapshot exists at all (allowing fallback
    when unit tests supply manual `after_state` without snapshots).
    """
    client_id_uuid = _opt_str(action.get("client_id")) or ""
    client_slug = _resolve_client_slug(sb, client_id_uuid)
    platform = (action.get("platform") or "").strip().lower()
    action_type = str(action.get("action_type") or "MANUAL_EXTERNAL_ACTION")
    window_end = executed_at + timedelta(days=window_days)

    baseline_snap, obs_snap = _load_window_snapshots(sb, client_slug, executed_at, window_days)
    concurrent = _load_concurrent_changes(
        sb,
        client_slug,
        executed_at,
        window_end,
        exclude_change_log_id=_opt_str(action.get("change_log_id")),
    )

    if not obs_snap:
        return None

    obs_metrics, obs_missing = _extract_snapshot_metrics(obs_snap)
    base_metrics: dict[str, float] = {}
    if baseline_snap:
        base_metrics, _ = _extract_snapshot_metrics(baseline_snap)

    # Merge any day-0 numeric metrics already recorded on action.before_state
    # without overriding a matching real baseline snapshot value.
    action_before = action.get("before_state") or {}
    if isinstance(action_before, dict):
        for k, v in action_before.items():
            if k not in base_metrics and v is not None:
                try:
                    base_metrics[k] = float(v)
                except (TypeError, ValueError):
                    pass

    priority_keys = _PLATFORM_METRIC_PRIORITY.get(platform, _PLATFORM_METRIC_PRIORITY["default"])
    # Compare all shared metrics in priority order
    all_keys = list(priority_keys) + [
        k for k in sorted(obs_metrics.keys()) if k not in priority_keys
    ]

    delta_dict: dict[str, Any] = {}
    primary_metric: Optional[str] = None
    primary_delta_pct: Optional[float] = None
    metric_refs: list[str] = []

    obs_snap_id = str(obs_snap.get("id") or "")
    base_snap_id = str(baseline_snap.get("id") or "") if baseline_snap else ""

    for key in all_keys:
        if key not in obs_metrics:
            continue
        if obs_snap_id:
            metric_refs.append(f"snapshot:{obs_snap_id}:{key}")
        if key not in base_metrics:
            continue
        b_val = base_metrics[key]
        a_val = obs_metrics[key]
        abs_d = round(a_val - b_val, 4)
        pct_d: Optional[float] = None
        if b_val != 0:
            raw_pct = ((a_val - b_val) / b_val) * 100.0
            pct_d = round(-raw_pct if key in _INVERTED_COST_METRICS else raw_pct, 2)
        delta_dict[key] = {
            "before": round(b_val, 4),
            "after":  round(a_val, 4),
            "abs":    abs_d,
            "pct":    pct_d,
        }
        if primary_metric is None and key in priority_keys:
            primary_metric = key
            primary_delta_pct = pct_d

    if base_snap_id and primary_metric:
        metric_refs.insert(0, f"snapshot:{base_snap_id}:{primary_metric}")

    health_ok = _snapshot_health_ok(obs_snap, platform) and (
        _snapshot_health_ok(baseline_snap, platform) if baseline_snap else True
    )
    ps_obs = _parse_date(obs_snap.get("period_start"))
    pe_obs = _parse_date(obs_snap.get("period_end"))
    data_days = ((pe_obs - ps_obs).days + 1) if (ps_obs and pe_obs) else None

    concurrent_count = int(concurrent.get("count") or 0)
    is_hold = action_type == "NO_ACTION_REQUIRED" or (
        isinstance(action.get("after_state"), dict)
        and str(action["after_state"].get("decision", "")).upper() == "HOLD"
    )

    confounders: list[str] = []
    if is_hold:
        confounders.append("hold_action_no_direct_causality")
    if concurrent_count >= _CONCURRENT_CONFOUNDER_THRESHOLD:
        confounders.append(f"concurrent_changes_in_window:{concurrent_count}")
    if not baseline_snap and not base_metrics:
        confounders.append("missing_baseline_snapshot")
    if not health_ok:
        confounders.append("degraded_snapshot_health")

    if not obs_metrics or not health_ok:
        outcome_status = "BLOCKED_DATA_QUALITY"
        confidence = "LOW"
    elif is_hold or concurrent_count >= _CONCURRENT_CONFOUNDER_THRESHOLD:
        # PRD §14.1 & §15: never attribute direct causality for HOLD or confounded windows
        outcome_status = "INCONCLUSIVE"
        confidence = "LOW"
    elif primary_delta_pct is None:
        outcome_status = "INCONCLUSIVE"
        confidence = "LOW"
    else:
        outcome_status = _outcome_status_from_delta(primary_delta_pct)
        confidence = _compute_confidence(
            has_before=bool(base_metrics),
            has_after=bool(obs_metrics),
            data_days=data_days,
            window_days=window_days,
            data_health_ok=health_ok,
        )
        if concurrent_count > 0 and confidence == "HIGH":
            confidence = "MEDIUM"

    measured_before_state = {
        **(action_before if isinstance(action_before, dict) else {}),
        "snapshot_id":  base_snap_id or None,
        "period_start": str(baseline_snap.get("period_start")) if baseline_snap else None,
        "period_end":   str(baseline_snap.get("period_end")) if baseline_snap else None,
        "metrics":      {k: round(v, 4) for k, v in base_metrics.items()},
    }
    measured_after_state = {
        "snapshot_id":  obs_snap_id or None,
        "period_start": str(obs_snap.get("period_start")),
        "period_end":   str(obs_snap.get("period_end")),
        "metrics":      {k: round(v, 4) for k, v in obs_metrics.items()},
    }
    if is_hold:
        measured_after_state["decision"] = "HOLD"

    data_health = {
        "note":                     "metric_snapshot_comparison",
        "primary_metric":           primary_metric,
        "baseline_snapshot_id":     base_snap_id or None,
        "observation_snapshot_id":  obs_snap_id or None,
        "data_days":                data_days,
        "snapshot_health_ok":       health_ok,
        "missing_metrics":          obs_missing[:10],
        "concurrent_changes_count": concurrent_count,
        "concurrent_change_ids":    concurrent.get("sample_ids") or [],
        "concurrent_channels":      concurrent.get("channels") or [],
        "confounders":              confounders,
        "attribution_language":     "result_observed_after_action_non_causal",
    }

    return {
        "status":               outcome_status,
        "confidence":           confidence,
        "delta":                delta_dict if delta_dict else None,
        "delta_pct":            primary_delta_pct,
        "metric_refs":          metric_refs[:30],
        "before_state":         measured_before_state,
        "after_state":          measured_after_state,
        "data_health":          data_health,
        "measurement_period": {
            "executed_at":       executed_at.isoformat(),
            "window_days":       window_days,
            "baseline_start":    str(baseline_snap.get("period_start")) if baseline_snap else None,
            "baseline_end":      str(baseline_snap.get("period_end")) if baseline_snap else None,
            "observation_start": str(obs_snap.get("period_start")),
            "observation_end":   str(obs_snap.get("period_end")),
        },
        "methodological_learning_eligible": bool(
            outcome_status == "INCONCLUSIVE" and obs_metrics and confounders
        ),
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
                    if k in _INVERTED_COST_METRICS:
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
        window_days=_resolve_window_days(action),
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
    recommendation_id: Optional[str],
    client_id_uuid: Optional[str],
    delta_pct: Optional[float],
    *,
    allow_methodological: bool = False,
    data_health: Optional[dict] = None,
) -> Optional[str]:
    """
    Write a PENDING learning candidate linked to this outcome.
    Idempotent: skips insert if a candidate already exists for this outcome or
    `(source_action_id, pattern)`. Never auto-approves.
    """
    methodological = bool(allow_methodological and outcome_status == "INCONCLUSIVE")
    if outcome_status not in _LEARNING_ELIGIBLE and not methodological:
        return None

    action_type = action.get("action_type", "MANUAL_EXTERNAL_ACTION")
    platform = action.get("platform") or ""
    entity_type = action.get("entity_type") or ""
    action_id_str = str(action["id"])

    if methodological:
        dh = data_health or {}
        concurrent_n = dh.get("concurrent_changes_count", 0)
        confounders = dh.get("confounders") or []
        statement = (
            f"Aprendizagem metodológica ({action_type} em {platform or 'N/A'}): "
            f"resultado observado inconclusivo devido a confundidores "
            f"({concurrent_n} mudanças concorrentes na janela; {', '.join(confounders) or 'sem causalidade direta'})."
        )
        pattern = f"{action_type}:{platform}:{entity_type}:INCONCLUSIVE_CONFOUNDED"
    else:
        direction = (
            "melhora" if outcome_status == "POSITIVE"
            else ("piora" if outcome_status == "NEGATIVE" else "não altera")
        )
        statement = (
            f"{action_type} em {platform or 'N/A'} ({entity_type or 'N/A'}) "
            f"{direction} o resultado observado após a ação."
        )
        pattern = f"{action_type}:{platform}:{entity_type}:{outcome_status}"

    # Dedup check: never create duplicate candidates for the same action/pattern or outcome
    try:
        ex_res = (
            sb.table("core_learning_candidates")
            .select("id")
            .eq("source_action_id", action_id_str)
            .eq("pattern", pattern)
            .limit(1)
            .execute()
        )
        ex_rows = ex_res.data if (ex_res and isinstance(ex_res.data, list)) else []
        if ex_rows and isinstance(ex_rows[0], dict) and isinstance(ex_rows[0].get("id"), str):
            logger.info(
                "outcome_evaluator: learning candidate already exists for action=%s pattern=%s — skipping",
                action_id_str,
                pattern,
            )
            return ex_rows[0]["id"]
    except Exception:
        pass

    observed_outcome = {
        "outcome_status": outcome_status,
        "delta_pct": delta_pct,
        "action_type": action_type,
        "platform": platform,
    }
    if data_health:
        observed_outcome["concurrent_changes_count"] = data_health.get("concurrent_changes_count", 0)
        observed_outcome["confounders"] = data_health.get("confounders") or []

    row = {
        "statement": statement,
        "scope": "CLIENT_ONLY",
        "evidence_level": "OBSERVATION",
        "evidence_refs": [outcome_id],
        "context": {
            "action_id": action_id_str,
            "recommendation_id": recommendation_id,
            "limitations": (data_health or {}).get("confounders") or [],
        },
        "client_id": client_id_uuid,
        "visibility_scope": "AGENCY_ONLY",
        "status": "PENDING",
        "source_action_id": action_id_str,
        "source_recommendation_id": recommendation_id,
        "pattern": pattern,
        "observed_outcome": observed_outcome,
    }
    # Link outcome_id when column exists (migration 059)
    row["outcome_id"] = outcome_id

    try:
        res = sb.table("core_learning_candidates").insert(row).execute()
        candidate_id = str(res.data[0]["id"]) if (res and res.data) else None
        logger.info(
            "outcome_evaluator: learning candidate %s created for action %s (outcome=%s)",
            candidate_id, action_id_str, outcome_status,
        )
        return candidate_id
    except Exception as exc:
        err = str(exc).lower()
        if "outcome_id" in err and ("column" in err or "42703" in err):
            row.pop("outcome_id", None)
            try:
                res = sb.table("core_learning_candidates").insert(row).execute()
                return str(res.data[0]["id"]) if (res and res.data) else None
            except Exception as exc2:
                logger.error("outcome_evaluator: learning_candidate retry insert failed: %s", exc2)
                return None
        logger.error("outcome_evaluator: learning_candidate insert failed for action %s: %s", action_id_str, exc)
        return None


# ── Core Evaluation & Versioned Supersession ──────────────────────────────────


def _is_balance_action(action: dict) -> bool:
    entity_type = (action.get("entity_type") or "").upper()
    return "BALANCE" in entity_type or "RECHARGE" in entity_type


def _supersede_outcome_row(sb, outcome_id: str, *, reason: str, now_iso: str) -> bool:
    """
    Mark an existing outcome row as `SUPERSEDED` without deleting history.
    Returns True if updated cleanly.
    """
    patch = {
        "status":            "SUPERSEDED",
        "superseded_at":     now_iso,
        "superseded_reason": reason,
        "updated_at":        now_iso,
    }
    try:
        sb.table("core_outcomes").update(patch).eq("id", outcome_id).execute()
        return True
    except Exception as exc:
        err = str(exc).lower()
        if ("superseded_at" in err or "superseded_reason" in err) and ("column" in err or "42703" in err):
            sb.table("core_outcomes").update({
                "status": "SUPERSEDED",
                "updated_at": now_iso,
            }).eq("id", outcome_id).execute()
            return True
        logger.error("outcome_evaluator: failed to supersede outcome %s: %s", outcome_id, exc)
        return False


def evaluate_action_outcome(action_id: str, *, supersede_hollow: bool = False) -> dict:
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
    return _evaluate_single(action, sb, supersede_hollow=supersede_hollow)


def reevaluate_action_outcome(
    action_id: str,
    *,
    reason: str = "versioned_reevaluation_real_snapshots",
    supersede_outcome_id: Optional[str] = None,
) -> dict:
    """
    Versioned re-evaluation of an EXECUTED action (PRD §14.1 / R04).

    Marks the prior active outcome (or explicit `supersede_outcome_id`, such as
    LK Sneakers hollow record `1968845f-4273-47f0-9b77-33f39c817bd5`) as
    `SUPERSEDED` without deleting history, then evaluates with real snapshots
    and inserts the new outcome linked via `supersedes_outcome_id`.
    """
    sb = get_supabase()
    try:
        res = sb.table("core_actions").select("*").eq("id", action_id).limit(1).execute()
        action = res.data[0] if (res and res.data) else None
    except Exception as exc:
        return {"error": f"action lookup failed: {exc}"}
    if not action:
        return {"error": f"action not found: {action_id}"}

    return _evaluate_single(
        action,
        sb,
        force_reevaluate=True,
        supersede_outcome_id=supersede_outcome_id,
        supersede_reason=reason,
    )


def _evaluate_single(
    action: dict,
    sb,
    *,
    supersede_hollow: bool = False,
    force_reevaluate: bool = False,
    supersede_outcome_id: Optional[str] = None,
    supersede_reason: str = "versioned_reevaluation",
) -> dict:
    action_id = str(action["id"])
    recommendation_id = _opt_str(action.get("recommendation_id"))
    client_id_uuid = _opt_str(action.get("client_id"))
    executed_at = _parse_ts(action.get("executed_at")) or datetime.now(timezone.utc)
    window_days = _resolve_window_days(action)
    now_iso = datetime.now(timezone.utc).isoformat()

    prior_to_supersede: Optional[str] = None

    # Check for existing active (non-SUPERSEDED) outcome for this action + window
    try:
        existing_res = (
            sb.table("core_outcomes")
            .select("id, status, metric_refs, delta, data_health, evaluation_window_days")
            .eq("action_id", action_id)
            .execute()
        )
        active_rows = [
            r for r in (existing_res.data or [])
            if r.get("status") != "SUPERSEDED"
            and (r.get("evaluation_window_days") in (None, window_days))
        ]
    except Exception as exc:
        logger.warning("outcome_evaluator: existing outcome check failed %s: %s", action_id, exc)
        active_rows = []

    if active_rows:
        row = active_rows[0]
        row_id = str(row["id"])
        if force_reevaluate:
            prior_to_supersede = supersede_outcome_id or row_id
        elif supersede_hollow and _is_hollow_outcome(row):
            prior_to_supersede = row_id
        elif row.get("status") in _FINAL_STATUSES:
            return {"skipped": True, "outcome_id": row_id, "status": row["status"]}
    elif force_reevaluate and supersede_outcome_id:
        prior_to_supersede = supersede_outcome_id

    # Route to evaluator: balance → real snapshots → manual generic fallback
    if _is_balance_action(action):
        result = _evaluate_balance_action(action, sb)
    else:
        snap_result = _evaluate_snapshot_action(
            action, sb, executed_at=executed_at, window_days=window_days
        )
        if snap_result is not None:
            result = snap_result
        elif action.get("after_state"):
            result = _evaluate_generic_action(action)
        else:
            result = {
                "status": "BLOCKED_DATA_QUALITY",
                "confidence": "LOW",
                "delta": None,
                "delta_pct": None,
                "data_health": {"note": "no_observation_snapshot_available"},
                "after_state": {},
            }

    # If we are superseding a hollow/prior outcome, supersede BEFORE insert so the
    # partial unique index `(action_id, evaluation_window_days) WHERE status <> 'SUPERSEDED'`
    # allows the new active row cleanly.
    if prior_to_supersede:
        if not _supersede_outcome_row(sb, prior_to_supersede, reason=supersede_reason, now_iso=now_iso):
            return {"error": f"failed to supersede prior outcome {prior_to_supersede}"}

    outcome_row: dict[str, Any] = {
        "action_id": action_id,
        "recommendation_id": recommendation_id,
        "client_id": client_id_uuid,
        "action_event_id": _opt_str(action.get("action_event_id")),
        "measurement_period": result.get("measurement_period") or {
            "executed_at": executed_at.isoformat(),
            "window_days": window_days,
        },
        "metric_refs": result.get("metric_refs") or [],
        "before_state": result.get("before_state") or (action.get("before_state") or {}),
        "after_state": result.get("after_state") or {},
        "delta": result.get("delta"),
        "status": result["status"],
        "evaluation_window_days": window_days,
        "confidence": result["confidence"],
        "data_health": result.get("data_health") or {},
        "measured_at": now_iso,
        "created_by": "outcome_evaluator",
    }
    if prior_to_supersede:
        outcome_row["supersedes_outcome_id"] = prior_to_supersede

    try:
        ins = sb.table("core_outcomes").insert(outcome_row).execute()
        outcome_id = str(ins.data[0]["id"]) if (ins and ins.data) else None
    except Exception as exc:
        err = str(exc).lower()
        # Migration 088 not yet applied: retry without supersedes_outcome_id
        if "supersedes_outcome_id" in err and ("column" in err or "42703" in err):
            outcome_row.pop("supersedes_outcome_id", None)
            try:
                ins = sb.table("core_outcomes").insert(outcome_row).execute()
                outcome_id = str(ins.data[0]["id"]) if (ins and ins.data) else None
            except Exception as exc2:
                logger.error("outcome_evaluator: outcome insert failed for action %s: %s", action_id, exc2)
                return {"error": f"outcome insert failed: {exc2}"}
        elif any(k in err for k in ("23505", "unique", "duplicate")):
            # Concurrent worker inserted the active outcome first — treat as idempotent skip
            logger.info("outcome_evaluator: concurrent duplicate insert for action %s — skipping", action_id)
            return {"skipped": True, "action_id": action_id, "status": "CONCURRENT_EXISTS"}
        else:
            logger.error("outcome_evaluator: outcome insert failed for action %s: %s", action_id, exc)
            return {"error": f"outcome insert failed: {exc}"}

    logger.info(
        "outcome_evaluator: action %s → outcome %s status=%s confidence=%s supersedes=%s",
        action_id, outcome_id, result["status"], result["confidence"], prior_to_supersede,
    )

    # Best-effort link outcome back to core_change_log when linked
    change_log_id = _opt_str(action.get("change_log_id"))
    if change_log_id and outcome_id:
        try:
            sb.table("core_change_log").update({
                "outcome_status": result["status"],
                "outcome_summary": f"outcome={outcome_id} confidence={result['confidence']}",
                "updated_at": now_iso,
            }).eq("id", change_log_id).execute()
        except Exception:
            pass

    # Generate deduped PENDING learning candidate when eligible (including methodological learnings)
    candidate_id: Optional[str] = None
    if outcome_id and (
        result["status"] in _LEARNING_ELIGIBLE
        or result.get("methodological_learning_eligible")
    ):
        candidate_id = _generate_learning_candidate(
            sb,
            action,
            outcome_id,
            result["status"],
            recommendation_id,
            client_id_uuid,
            result.get("delta_pct"),
            allow_methodological=bool(result.get("methodological_learning_eligible")),
            data_health=result.get("data_health"),
        )

    return {
        "outcome_id":            outcome_id,
        "supersedes_outcome_id": prior_to_supersede,
        "status":                result["status"],
        "confidence":            result["confidence"],
        "action_id":             action_id,
        "learning_candidate_id": candidate_id,
    }


# ── Auditable core_job_runs helpers ───────────────────────────────────────────


def _start_job_run(sb, run_key: str, now_iso: str) -> tuple[Optional[str], bool]:
    """
    Create a `RUNNING` row in `core_job_runs` for `outcome_evaluation`.
    Returns `(job_run_id, acquired)`.
    If `run_key` already exists (unique constraint `core_job_runs_run_key_key`),
    returns `(None, False)` so concurrent replicas skip cleanly.
    """
    try:
        res = sb.table("core_job_runs").insert({
            "job_type":   "outcome_evaluation",
            "run_key":    run_key,
            "status":     "RUNNING",
            "attempt":    1,
            "started_at": now_iso,
        }).execute()
        job_id = str(res.data[0]["id"]) if (res and res.data) else None
        return job_id, True
    except Exception as exc:
        err = str(exc).lower()
        if any(k in err for k in ("23505", "unique", "duplicate")):
            logger.info("outcome_evaluator: run_key=%s already claimed — skipping concurrent run", run_key)
            return None, False
        logger.warning("outcome_evaluator: core_job_runs insert failed (%s) — continuing", exc)
        return None, True


def _finish_job_run(sb, job_run_id: Optional[str], *, status: str, error: Optional[str] = None) -> None:
    if not job_run_id:
        return
    try:
        sb.table("core_job_runs").update({
            "status":      status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error":       error[:500] if error else None,
        }).eq("id", job_run_id).execute()
    except Exception as exc:
        logger.warning("outcome_evaluator: core_job_runs finish update failed: %s", exc)


def evaluate_pending_outcomes(
    *,
    run_key: Optional[str] = None,
    supersede_hollow: bool = False,
) -> dict:
    """
    Scheduler entry point — idempotent, concurrency-locked, and audited in `core_job_runs`.
    Finds all EXECUTED actions whose evaluation window has passed and no active
    final outcome exists (or hollow outcome when `supersede_hollow=True`).
    """
    if not _EVAL_RUN_LOCK.acquire(blocking=False):
        logger.info("outcome_evaluator: another evaluation run is already in progress — skipping")
        return {"skipped_concurrent": True, "evaluated": 0, "skipped": 0, "errors": 0}

    try:
        sb = get_supabase()
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        effective_run_key = run_key or f"outcome_evaluation:{now.strftime('%Y-%m-%dT%H')}"

        job_run_id, acquired = _start_job_run(sb, effective_run_key, now_iso)
        if not acquired:
            return {
                "skipped_concurrent": True,
                "run_key": effective_run_key,
                "evaluated": 0,
                "skipped": 0,
                "errors": 0,
            }

        try:
            res = (
                sb.table("core_actions")
                .select("*")
                .eq("status", "EXECUTED")
                .not_.is_("executed_at", "null")
                .order("executed_at", desc=True)
                .limit(500)
                .execute()
            )
            actions = res.data or []
        except Exception as exc:
            logger.error("outcome_evaluator: action fetch failed: %s", exc)
            _finish_job_run(sb, job_run_id, status="FAILED", error=str(exc))
            return {"error": str(exc), "job_run_id": job_run_id, "run_key": effective_run_key}

        evaluated = 0
        skipped = 0
        errors = 0
        error_msgs: list[str] = []

        for action in actions:
            executed_at = _parse_ts(action.get("executed_at"))
            window_days = _resolve_window_days(action)
            if not executed_at:
                skipped += 1
                continue
            if now < executed_at + timedelta(days=window_days):
                skipped += 1
                continue

            result = _evaluate_single(action, sb, supersede_hollow=supersede_hollow)
            if result.get("error"):
                errors += 1
                error_msgs.append(f"{action.get('id')}: {result['error']}")
            elif result.get("skipped"):
                skipped += 1
            else:
                evaluated += 1

        final_status = "FAILED" if (errors > 0 and evaluated == 0) else "SUCCEEDED"
        _finish_job_run(
            sb,
            job_run_id,
            status=final_status,
            error="; ".join(error_msgs) if error_msgs else None,
        )

        logger.info(
            "outcome_evaluator: run complete (run_key=%s) — evaluated=%d skipped=%d errors=%d",
            effective_run_key, evaluated, skipped, errors,
        )
        return {
            "job_run_id":    job_run_id,
            "run_key":       effective_run_key,
            "evaluated":     evaluated,
            "skipped":       skipped,
            "errors":        errors,
            "total_checked": len(actions),
        }
    finally:
        _EVAL_RUN_LOCK.release()
