"""
Core performance alert evaluator.

Deterministic rules reading only from core_metric_snapshots and ad_campaigns.
Never calls ad platform APIs. Never writes to external systems. Never fires on
STALE / MISSING / NO_DATA / ACCESS_MISSING metrics.

Implemented rules:
  SPEND_NO_CONVERSION        — spend >= threshold AND conversions == 0 (source READY)
  ROAS_BELOW_TARGET          — actual_roas < target * 0.80 (requires target_truth entry)
  CPA_ABOVE_TARGET           — actual_cpa > target * 1.30 (requires target_truth entry)
  CONVERSIONS_DROP           — conversions < median_prev * 0.60 (requires >= 3 baseline periods)
  REVENUE_DROP               — revenue < median_prev * 0.60 (ecommerce only; >= 3 baseline)
  SPEND_DROP                 — spend < median_prev * 0.60 (per platform; >= 3 baseline)
  CAMPAIGN_NOT_SPENDING      — platform total_spend == 0 for >= 2 consecutive days in ad_campaigns
  SOURCE_PERFORMANCE_ANOMALY — spend > 115% of median AND conversions < 50% of median

Not implemented (infrastructure gap — documented here to prevent silent omission):
  BUDGET_PACING_HIGH/LOW     — requires DailyBudget from ad platform API; not in Core collectors
  ACCOUNT_BALANCE_LOW/CRITICAL — Meta has balance field; Google has AccountBudget; but neither
                                  is fetched by the Core collectors today.
                                  Do NOT infer balance from budget or spend.
                                  Status: NOT_SUPPORTED until a dedicated balance collector exists.

Operational thresholds (_SPEND_NO_CONV_MIN_BRL, _DROP_THRESHOLD, etc.) are anomaly-detection
defaults, not client goals. They activate only when no target_truth entry overrides the rule.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ── Alert type catalogue ───────────────────────────────────────────────────────

PERF_ALERT_TYPES = frozenset({
    "SPEND_NO_CONVERSION",
    "ROAS_BELOW_TARGET",
    "CPA_ABOVE_TARGET",
    "CONVERSIONS_DROP",
    "REVENUE_DROP",
    "SPEND_DROP",
    "CAMPAIGN_NOT_SPENDING",
    "SOURCE_PERFORMANCE_ANOMALY",
    "MER_BELOW_TARGET",
})

_PERF_SEVERITY: dict[str, str] = {
    "SPEND_NO_CONVERSION":        "MEDIUM",
    "ROAS_BELOW_TARGET":          "HIGH",
    "CPA_ABOVE_TARGET":           "HIGH",
    "CONVERSIONS_DROP":           "HIGH",
    "REVENUE_DROP":               "HIGH",
    "SPEND_DROP":                 "MEDIUM",
    "CAMPAIGN_NOT_SPENDING":      "HIGH",
    "SOURCE_PERFORMANCE_ANOMALY": "HIGH",
    "MER_BELOW_TARGET":           "HIGH",
}


# ── Operational thresholds (anomaly-detection defaults, NOT client goals) ──────
# These constants are documented and will be made configurable per client in a
# future release.  They are intentionally conservative to avoid alert storms.

_SPEND_NO_CONV_MIN_BRL: float = 500.0  # minimum period spend before firing
_DROP_THRESHOLD:        float = 0.40   # >40% below median → drop alert
_ANOMALY_SPEND_HIGH:    float = 1.15   # spend >115% of median
_ANOMALY_CONV_LOW:      float = 0.50   # AND conversions <50% of median → anomaly
_ROAS_MISS_RATIO:       float = 0.80   # roas < 80% of target
_CPA_MISS_RATIO:        float = 1.30   # cpa > 130% of target
_MIN_BASELINE:          int   = 3      # min prior periods required for drop detection
_CAMPAIGN_ZERO_DAYS:    int   = 2      # consecutive zero-spend days before alert
_MAX_SNAPSHOT_HISTORY:  int   = 10     # max distinct period_ends to load
_CAMP_HISTORY_DAYS:     int   = 7      # campaign spend history window (days)
_CAMP_PAGE_SIZE:        int   = 500    # ad_campaigns pagination page size


# ── Business-model metric mapping ──────────────────────────────────────────────

_CONV_KEY: dict[str, dict[str, str]] = {
    "ecommerce":       {"meta": "meta_conversions",  "google": "google_purchase_conversions"},
    "lead_generation": {"meta": "meta_conversions",  "google": "google_conversions"},
    "auction":         {"meta": "meta_conversions",  "google": "google_conversions"},
}
_ROAS_KEY: dict[str, dict[str, str]] = {
    "ecommerce":       {"meta": "roas_meta",         "google": "google_roas_ecommerce"},
    "lead_generation": {"meta": "roas_meta",         "google": "roas_google"},
    "auction":         {"meta": "roas_meta",         "google": "roas_google"},
}
_CPA_KEY: dict[str, dict[str, str]] = {
    "ecommerce":       {"meta": "cpa_meta",          "google": "google_cpa_ecommerce"},
    "lead_generation": {"meta": "cpa_meta",          "google": "cpa_google"},
    "auction":         {"meta": "cpa_meta",          "google": "cpa_google"},
}
_SPEND_KEY: dict[str, str] = {
    "meta":   "meta_spend",
    "google": "google_spend",
}

_READY_STATUSES = frozenset({"OK", "PARTIAL"})


# ── Low-level helpers ──────────────────────────────────────────────────────────

def _safe_float(v: object) -> Optional[float]:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _metric_value(metrics: dict[str, dict], key: str) -> Optional[float]:
    """Return metric float value only when source quality is OK or PARTIAL."""
    m = metrics.get(key)
    if not m:
        return None
    if m.get("value_status") not in _READY_STATUSES:
        return None
    return _safe_float(m.get("value"))


def _upsert_perf_alert(
    sb,
    fingerprint: str,
    alert_type: str,
    client_uuid: str,
    title: str,
    message: str,
    evidence: dict,
    existing_open: dict[str, dict],
) -> None:
    if fingerprint in existing_open:
        try:
            sb.table("alerts").update({
                "occurrence_count": existing_open[fingerprint]["occurrence_count"] + 1,
                "data":             evidence,
            }).eq("id", existing_open[fingerprint]["id"]).execute()
        except Exception as exc:
            logger.warning("perf_alert: upsert update failed %s: %s", fingerprint, exc)
    else:
        try:
            sb.table("alerts").insert({
                "type":             alert_type,
                "client_id":        client_uuid,
                "title":            title,
                "message":          message,
                "severity":         _PERF_SEVERITY[alert_type],
                "status":           "OPEN",
                "fingerprint":      fingerprint,
                "data":             evidence,
                "occurrence_count": 1,
            }).execute()
        except Exception as exc:
            logger.warning("perf_alert: insert failed %s: %s", fingerprint, exc)


def _resolve_stale(
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
                logger.warning("perf_alert: resolve failed %s: %s", fp, exc)
    return resolved


# ── Snapshot loading ───────────────────────────────────────────────────────────

def _load_snapshot_history(sb, client_id: str) -> list[dict]:
    """
    Return up to _MAX_SNAPSHOT_HISTORY snapshots, one per distinct period_end,
    newest first.  Replays (multiple snapshots for same period_end) are
    deduplicated — only the latest computed_at per period is kept.
    core_metric_snapshots.client_id is TEXT (slug), not UUID.
    """
    try:
        res = (
            sb.table("core_metric_snapshots")
            .select("period_end, computed_at, metrics, health")
            .eq("client_id", client_id)
            .order("period_end",  desc=True)
            .order("computed_at", desc=True)
            .range(0, _MAX_SNAPSHOT_HISTORY * 5 - 1)  # fetch extra to absorb replays
            .execute()
        )
        rows = res.data or []
    except Exception as exc:
        logger.error("perf_alert: snapshot load failed %s: %s", client_id, exc)
        return []

    seen_periods: set[str] = set()
    deduped: list[dict] = []
    for row in rows:
        pe = str(row.get("period_end") or "")
        if pe in seen_periods:
            continue
        seen_periods.add(pe)
        raw_metrics = row.get("metrics") or []
        metrics_dict: dict[str, dict] = {
            m["metric_key"]: m
            for m in raw_metrics
            if m.get("metric_key")
        }
        deduped.append({
            "period_end": pe,
            "computed_at": row.get("computed_at"),
            "metrics": metrics_dict,
            "health":  row.get("health") or {},
        })
        if len(deduped) >= _MAX_SNAPSHOT_HISTORY:
            break

    return deduped


# ── Campaign spend loading (paginated) ────────────────────────────────────────

def _load_campaign_spend(sb, client_uuid: str) -> dict[tuple[str, str], float]:
    """
    Return {(platform, date_str): total_spend} for the last _CAMP_HISTORY_DAYS.
    Paginated via .range() to avoid PostgREST max_rows cap.
    """
    cutoff = (
        datetime.now(timezone.utc).date() - timedelta(days=_CAMP_HISTORY_DAYS)
    ).isoformat()
    rows: list[dict] = []
    offset = 0
    try:
        while True:
            page = (
                sb.table("ad_campaigns")
                .select("platform, date, spend")
                .eq("client_id", client_uuid)
                .is_("ad_id", "null")
                .gte("date", cutoff)
                .order("date", desc=True)
                .range(offset, offset + _CAMP_PAGE_SIZE - 1)
                .execute()
            )
            batch = page.data or []
            rows.extend(batch)
            if len(batch) < _CAMP_PAGE_SIZE:
                break
            offset += _CAMP_PAGE_SIZE
    except Exception as exc:
        logger.warning("perf_alert: ad_campaigns load failed uuid=%s: %s", client_uuid, exc)
        return {}

    result: dict[tuple[str, str], float] = defaultdict(float)
    for r in rows:
        result[(r["platform"], str(r["date"]))] += float(r.get("spend") or 0)
    return dict(result)


# ── Main evaluator ─────────────────────────────────────────────────────────────

def evaluate_performance_alerts(client_id: str) -> dict:
    """
    Evaluate all performance alerts for a client.
    Called after evaluate_operational_alerts() in core_scheduler.

    Returns a summary dict: {created, updated, resolved, skipped_reason?}.
    """
    from ..database import get_supabase

    sb  = get_supabase()
    now = datetime.now(timezone.utc)

    # ── 1. Load client config ─────────────────────────────────────────────────
    try:
        c_res = (
            sb.table("clients")
            .select("id, business_model")
            .eq("client_id", client_id)
            .limit(1)
            .execute()
        )
        c = c_res.data[0] if (c_res and c_res.data) else None
    except Exception as exc:
        logger.error("perf_alert: client load failed %s: %s", client_id, exc)
        return {"error": str(exc)}

    if not c:
        logger.warning("perf_alert: client not found %s", client_id)
        return {"error": "client_not_found"}

    client_uuid:    str = c["id"]
    business_model: str = (c.get("business_model") or "ecommerce").lower()

    # ── 2. Load target_truth ──────────────────────────────────────────────────
    try:
        tt_res = (
            sb.table("core_client_truth")
            .select("target_truth")
            .eq("client_id", client_id)
            .order("valid_from", desc=True)
            .limit(1)
            .execute()
        )
        raw_tt = (tt_res.data[0].get("target_truth") or {}) if (tt_res and tt_res.data) else {}
        target_truth: dict = raw_tt if isinstance(raw_tt, dict) else {}
    except Exception as exc:
        logger.warning("perf_alert: target_truth load failed %s: %s", client_id, exc)
        target_truth = {}

    # ── 3. Load snapshot history ──────────────────────────────────────────────
    history = _load_snapshot_history(sb, client_id)
    if not history:
        logger.info("perf_alert: no snapshots for %s — skipping", client_id)
        return {"skipped": "no_snapshots"}

    current     = history[0]
    prev        = history[1:]
    cur_metrics = current["metrics"]
    period_end  = current["period_end"]

    # ── 4. Load existing open performance alerts ──────────────────────────────
    try:
        open_res = (
            sb.table("alerts")
            .select("id, type, fingerprint, occurrence_count")
            .eq("client_id", client_uuid)
            .in_("type", list(PERF_ALERT_TYPES))
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
        logger.error("perf_alert: open alert load failed %s: %s", client_id, exc)
        existing_open = {}

    current_fingerprints: set[str] = set()

    def _fire(fp: str, alert_type: str, title: str, msg: str, evidence: dict) -> None:
        current_fingerprints.add(fp)
        _upsert_perf_alert(
            sb, fp, alert_type, client_uuid, title, msg, evidence, existing_open,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # Rule 1 — SPEND_NO_CONVERSION
    # Fires when channel spend >= threshold over the period AND conversions == 0.
    # Guard: conv metric must be present with OK/PARTIAL status (absent = unknown,
    # NOT zero — do not fire if the metric is STALE/MISSING/NO_DATA).
    # ═══════════════════════════════════════════════════════════════════════════
    conv_map = _CONV_KEY.get(business_model, _CONV_KEY["ecommerce"])
    for platform, spend_key in _SPEND_KEY.items():
        spend = _metric_value(cur_metrics, spend_key)
        if spend is None or spend < _SPEND_NO_CONV_MIN_BRL:
            continue
        conv_key = conv_map.get(platform)
        if not conv_key:
            continue
        convs = _metric_value(cur_metrics, conv_key)
        if convs is None:
            continue  # metric absent or STALE — cannot confirm zero; do not fire
        if convs == 0.0:
            fp = f"SPEND_NO_CONVERSION:{client_uuid}:{platform}"
            _fire(
                fp, "SPEND_NO_CONVERSION",
                f"Spend sem conversão — {platform} — {client_id}",
                (
                    f"{platform.capitalize()} acumulou R$ {spend:.2f} no período {period_end} "
                    f"com 0 conversões ({conv_key})."
                ),
                {
                    "platform": platform, "spend_metric": spend_key,
                    "conv_metric": conv_key, "spend": spend,
                    "conversions": 0, "period_end": period_end,
                    "threshold_brl": _SPEND_NO_CONV_MIN_BRL,
                },
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Rule 2 — ROAS_BELOW_TARGET
    # Only fires when target_truth contains a target for the specific roas metric.
    # If no target → NOT_APPLICABLE → no alert.
    # ═══════════════════════════════════════════════════════════════════════════
    roas_map = _ROAS_KEY.get(business_model, _ROAS_KEY["ecommerce"])
    for platform, roas_key in roas_map.items():
        tt_entry = target_truth.get(roas_key)
        if not isinstance(tt_entry, dict) or "target" not in tt_entry:
            continue  # NOT_APPLICABLE — no target defined
        target_roas = _safe_float(tt_entry["target"])
        if not target_roas or target_roas <= 0:
            continue
        actual_roas = _metric_value(cur_metrics, roas_key)
        if actual_roas is None:
            continue
        threshold = target_roas * _ROAS_MISS_RATIO
        if actual_roas < threshold:
            fp = f"ROAS_BELOW_TARGET:{client_uuid}:{roas_key}"
            _fire(
                fp, "ROAS_BELOW_TARGET",
                f"ROAS abaixo da meta — {platform} — {client_id}",
                (
                    f"{platform.capitalize()} ROAS {actual_roas:.2f}x abaixo de "
                    f"{_ROAS_MISS_RATIO*100:.0f}% da meta ({target_roas:.2f}x)."
                ),
                {
                    "platform": platform, "metric_key": roas_key,
                    "actual": actual_roas, "target": target_roas,
                    "threshold": round(threshold, 4), "period_end": period_end,
                },
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Rule 3 — CPA_ABOVE_TARGET
    # Only fires when target_truth contains a target for the specific cpa metric.
    # ═══════════════════════════════════════════════════════════════════════════
    cpa_map = _CPA_KEY.get(business_model, _CPA_KEY["ecommerce"])
    for platform, cpa_key in cpa_map.items():
        tt_entry = target_truth.get(cpa_key)
        if not isinstance(tt_entry, dict) or "target" not in tt_entry:
            continue  # NOT_APPLICABLE
        target_cpa = _safe_float(tt_entry["target"])
        if not target_cpa or target_cpa <= 0:
            continue
        actual_cpa = _metric_value(cur_metrics, cpa_key)
        if actual_cpa is None:
            continue
        threshold = target_cpa * _CPA_MISS_RATIO
        if actual_cpa > threshold:
            fp = f"CPA_ABOVE_TARGET:{client_uuid}:{cpa_key}"
            _fire(
                fp, "CPA_ABOVE_TARGET",
                f"CPA acima da meta — {platform} — {client_id}",
                (
                    f"{platform.capitalize()} CPA R$ {actual_cpa:.2f} acima de "
                    f"{_CPA_MISS_RATIO*100:.0f}% da meta (R$ {target_cpa:.2f})."
                ),
                {
                    "platform": platform, "metric_key": cpa_key,
                    "actual": actual_cpa, "target": target_cpa,
                    "threshold": round(threshold, 2), "period_end": period_end,
                },
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Rules 4–6: Drop detection (shared helper)
    # Requires >= _MIN_BASELINE prior periods with OK/PARTIAL data.
    # If fewer baseline periods are available → INSUFFICIENT_BASELINE → skip.
    # ═══════════════════════════════════════════════════════════════════════════
    def _drop_check(
        metric_key: str,
        alert_type: str,
        qualifier: str,
        label: str,
    ) -> None:
        current_val = _metric_value(cur_metrics, metric_key)
        if current_val is None:
            return

        baseline: list[float] = [
            v
            for snap in prev
            for v in [_metric_value(snap["metrics"], metric_key)]
            if v is not None
        ]
        if len(baseline) < _MIN_BASELINE:
            logger.debug(
                "perf_alert: %s INSUFFICIENT_BASELINE for %s/%s (%d periods)",
                client_id, alert_type, qualifier, len(baseline),
            )
            return

        med = statistics.median(baseline)
        if med <= 0:
            return

        drop_threshold = med * (1.0 - _DROP_THRESHOLD)
        if current_val < drop_threshold:
            pct = round((1.0 - current_val / med) * 100, 1)
            fp  = f"{alert_type}:{client_uuid}:{qualifier}"
            _fire(
                fp, alert_type,
                f"{label} — {client_id}",
                (
                    f"{metric_key} = {current_val:.2f} está {pct}% abaixo da "
                    f"mediana dos últimos {len(baseline)} períodos ({med:.2f})."
                ),
                {
                    "metric_key": metric_key, "current_value": current_val,
                    "baseline_median": round(med, 4),
                    "threshold": round(drop_threshold, 4),
                    "pct_drop": pct,
                    "baseline_periods": len(baseline),
                    "period_end": period_end,
                },
            )

    # Rule 4 — CONVERSIONS_DROP (per platform, business-model-aware)
    for platform, conv_key in _CONV_KEY.get(business_model, _CONV_KEY["ecommerce"]).items():
        _drop_check(conv_key, "CONVERSIONS_DROP", platform, f"Queda de conversões — {platform}")

    # Rule 5 — REVENUE_DROP (ecommerce only; business source)
    if business_model == "ecommerce":
        _drop_check(
            "revenue_business", "REVENUE_DROP", "business",
            "Queda de receita",
        )

    # Rule 6 — SPEND_DROP (per platform)
    for platform, spend_key in _SPEND_KEY.items():
        _drop_check(spend_key, "SPEND_DROP", platform, f"Queda de gasto — {platform}")

    # ═══════════════════════════════════════════════════════════════════════════
    # Rule 7 — CAMPAIGN_NOT_SPENDING
    # Reads ad_campaigns for the last _CAMP_HISTORY_DAYS.
    # Fires when a platform's total spend is 0 for >= _CAMPAIGN_ZERO_DAYS
    # consecutive days AND at least one older day in the window had spend > 0
    # (confirming the campaigns were previously active, not newly created).
    # ═══════════════════════════════════════════════════════════════════════════
    camp_spend = _load_campaign_spend(sb, client_uuid)
    if camp_spend:
        platforms_seen = {k[0] for k in camp_spend}
        for plat in platforms_seen:
            dates = sorted(
                {k[1] for k in camp_spend if k[0] == plat},
                reverse=True,
            )
            if len(dates) < _CAMPAIGN_ZERO_DAYS:
                continue

            recent_zero = all(
                camp_spend.get((plat, d), 0) == 0.0
                for d in dates[:_CAMPAIGN_ZERO_DAYS]
            )
            if not recent_zero:
                continue

            # Require at least one older date with spend > 0
            older_had_spend = any(
                camp_spend.get((plat, d), 0) > 0
                for d in dates[_CAMPAIGN_ZERO_DAYS:]
            )
            if not older_had_spend:
                continue  # all dates zero — likely new or never spent; skip

            zero_dates = dates[:_CAMPAIGN_ZERO_DAYS]
            last_active = next(
                (d for d in dates[_CAMPAIGN_ZERO_DAYS:]
                 if camp_spend.get((plat, d), 0) > 0),
                None,
            )
            fp = f"CAMPAIGN_NOT_SPENDING:{client_uuid}:{plat}"
            _fire(
                fp, "CAMPAIGN_NOT_SPENDING",
                f"Campanhas sem gasto — {plat} — {client_id}",
                (
                    f"Plataforma {plat} sem gasto por {len(zero_dates)} dias consecutivos "
                    f"({', '.join(zero_dates)}). Último gasto: {last_active or 'desconhecido'}."
                ),
                {
                    "platform": plat,
                    "zero_spend_dates": zero_dates,
                    "last_active_date": last_active,
                    "period_end": period_end,
                },
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Rule 8 — SOURCE_PERFORMANCE_ANOMALY
    # Efficiency collapse: spend elevated (>115% median) AND conversions
    # collapsed (<50% median) simultaneously.  Indicates budget running but
    # delivery / tracking broken.
    # ═══════════════════════════════════════════════════════════════════════════
    for platform in ("meta", "google"):
        spend_key = _SPEND_KEY[platform]
        conv_key  = _CONV_KEY.get(business_model, _CONV_KEY["ecommerce"]).get(platform)
        if not conv_key:
            continue

        cur_spend = _metric_value(cur_metrics, spend_key)
        cur_conv  = _metric_value(cur_metrics, conv_key)
        if cur_spend is None or cur_conv is None:
            continue

        prev_spends = [v for snap in prev for v in [_metric_value(snap["metrics"], spend_key)] if v is not None]
        prev_convs  = [v for snap in prev for v in [_metric_value(snap["metrics"],  conv_key)] if v is not None]

        if len(prev_spends) < _MIN_BASELINE or len(prev_convs) < _MIN_BASELINE:
            continue

        med_spend = statistics.median(prev_spends)
        med_conv  = statistics.median(prev_convs)
        if med_spend <= 0 or med_conv <= 0:
            continue

        spend_ratio = cur_spend / med_spend
        conv_ratio  = cur_conv  / med_conv

        if spend_ratio > _ANOMALY_SPEND_HIGH and conv_ratio < _ANOMALY_CONV_LOW:
            fp = f"SOURCE_PERFORMANCE_ANOMALY:{client_uuid}:{platform}"
            _fire(
                fp, "SOURCE_PERFORMANCE_ANOMALY",
                f"Anomalia de performance — {platform} — {client_id}",
                (
                    f"{platform.capitalize()} spend a {spend_ratio*100:.0f}% da mediana mas "
                    f"conversões apenas a {conv_ratio*100:.0f}% — colapso de eficiência."
                ),
                {
                    "platform": platform,
                    "spend_metric": spend_key, "conv_metric": conv_key,
                    "current_spend": cur_spend, "median_spend": round(med_spend, 2),
                    "current_conv":  cur_conv,  "median_conv":  round(med_conv, 4),
                    "spend_ratio":   round(spend_ratio, 3),
                    "conv_ratio":    round(conv_ratio, 3),
                    "period_end": period_end,
                },
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Rule 9 — MER_BELOW_TARGET
    # MER (Marketing Efficiency Ratio) = revenue_business / total_spend.
    # Fires when MER < target × 0.85.  Requires target_truth entry for "mer".
    # Period tag not required here (efficiency metric, point-in-time).
    # ═══════════════════════════════════════════════════════════════════════════
    _MER_MISS_RATIO: float = 0.85
    tt_mer = target_truth.get("mer")
    if isinstance(tt_mer, dict) and "target" in tt_mer:
        target_mer = _safe_float(tt_mer["target"])
        if target_mer and target_mer > 0:
            actual_mer = _metric_value(cur_metrics, "mer")
            if actual_mer is None:
                # Fallback: compute from revenue_business / total_spend
                rev   = _metric_value(cur_metrics, "revenue_business")
                spend = _metric_value(cur_metrics, "total_spend")
                if rev is not None and spend and spend > 0:
                    actual_mer = rev / spend
            if actual_mer is not None and actual_mer < target_mer * _MER_MISS_RATIO:
                fp = f"MER_BELOW_TARGET:{client_uuid}:{period_end}"
                _fire(
                    fp, "MER_BELOW_TARGET",
                    f"MER abaixo da meta — {client_id}",
                    (
                        f"MER {actual_mer:.2f}x abaixo de "
                        f"{_MER_MISS_RATIO*100:.0f}% da meta ({target_mer:.2f}x). "
                        f"Período: {period_end}."
                    ),
                    {
                        "target_mer":  target_mer,
                        "actual_mer":  round(actual_mer, 4),
                        "threshold":   round(target_mer * _MER_MISS_RATIO, 4),
                        "period_end":  period_end,
                    },
                )

    # ── Auto-resolve alerts whose condition no longer holds ───────────────────
    resolved = _resolve_stale(sb, existing_open, current_fingerprints, now)
    created  = sum(1 for fp in current_fingerprints if fp not in existing_open)
    updated  = sum(1 for fp in current_fingerprints if fp in existing_open)

    logger.info(
        "perf_alert: %s created=%d updated=%d resolved=%d",
        client_id, created, updated, resolved,
    )
    return {
        "created":              created,
        "updated":              updated,
        "resolved":             resolved,
        "current_fingerprints": list(current_fingerprints),
    }
