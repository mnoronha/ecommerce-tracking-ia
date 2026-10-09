"""
Target pacing evaluator.

Reads target_truth (core_client_truth) and MTD actuals (ad_spend + orders) to
evaluate monthly volume targets and efficiency targets.

Volume metrics (linear pacing — requires period='monthly' in target_truth entry):
  REVENUE_BELOW_PACE          — revenue_business MTD pacing < 0.85
  REVENUE_TARGET_AT_RISK      — projected EOM revenue < target × 0.80
  CONVERSIONS_BELOW_PACE      — google/meta conversions MTD pacing < 0.85
  CONVERSIONS_TARGET_AT_RISK  — projected EOM conversions < target × 0.80
  LEADS_BELOW_PACE            — leads MTD pacing < 0.85 (lead_gen / auction)
  LEADS_TARGET_AT_RISK        — projected EOM leads < target × 0.80

Efficiency metrics (point-in-time, no linear pacing):
  MER_BELOW_TARGET            — MER MTD < target × 0.85

Quality guards:
  - Target absent → NOT_APPLICABLE (never invented)
  - Volume target without period='monthly' → skip
  - Actual = None (source stale/missing) → skip
  - Actual = 0 with reliable source → alert
  - Never substitute business revenue with media-attributed revenue
  - Days elapsed < 5 for volume pacing (insufficient sample)
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Alert catalogue ────────────────────────────────────────────────────────────

TARGET_ALERT_TYPES = frozenset({
    "REVENUE_BELOW_PACE",
    "REVENUE_TARGET_AT_RISK",
    "CONVERSIONS_BELOW_PACE",
    "CONVERSIONS_TARGET_AT_RISK",
    "LEADS_BELOW_PACE",
    "LEADS_TARGET_AT_RISK",
    "MER_BELOW_TARGET",
})

_TARGET_SEVERITY: dict[str, str] = {
    "REVENUE_BELOW_PACE":         "MEDIUM",
    "REVENUE_TARGET_AT_RISK":     "HIGH",
    "CONVERSIONS_BELOW_PACE":     "MEDIUM",
    "CONVERSIONS_TARGET_AT_RISK": "HIGH",
    "LEADS_BELOW_PACE":           "MEDIUM",
    "LEADS_TARGET_AT_RISK":       "HIGH",
    "MER_BELOW_TARGET":           "HIGH",
}

# Thresholds
_VOL_PACE_WARN:   float = 0.85   # attainment < 0.85 → BELOW_PACE
_VOL_AT_RISK:     float = 0.80   # projected < target × 0.80 → AT_RISK
_MER_MISS_RATIO:  float = 0.85   # mer < target × 0.85 → MER_BELOW_TARGET
_MIN_DAYS_VOL:    int   = 5      # minimum days elapsed for volume pacing

# Channel mapping for conversions
_AD_SPEND_CHANNEL = {"google": "google_ads", "meta": "meta_ads"}

# Leads metric key per business model
_LEADS_KEY: dict[str, str] = {
    "lead_generation": "google_conversions",
    "auction":         "google_conversions",
    "ecommerce":       "meta_conversions",   # fallback; typically not used
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _safe_float(v: object) -> Optional[float]:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _upsert_alert(
    sb,
    fingerprint: str,
    alert_type: str,
    client_uuid: str,
    title: str,
    message: str,
    evidence: dict,
    existing_open: dict[str, dict],
) -> None:
    sev = _TARGET_SEVERITY.get(alert_type, "MEDIUM")
    if fingerprint in existing_open:
        try:
            sb.table("alerts").update({
                "occurrence_count": existing_open[fingerprint]["occurrence_count"] + 1,
                "data":             evidence,
            }).eq("id", existing_open[fingerprint]["id"]).execute()
        except Exception as exc:
            logger.warning("target_pacing: upsert update failed %s: %s", fingerprint, exc)
    else:
        try:
            sb.table("alerts").insert({
                "type":             alert_type,
                "client_id":        client_uuid,
                "title":            title,
                "message":          message,
                "severity":         sev,
                "status":           "OPEN",
                "fingerprint":      fingerprint,
                "data":             evidence,
                "occurrence_count": 1,
            }).execute()
        except Exception as exc:
            logger.warning("target_pacing: insert failed %s: %s", fingerprint, exc)


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
                logger.warning("target_pacing: resolve failed %s: %s", fp, exc)
    return resolved


# ── Main evaluator ─────────────────────────────────────────────────────────────

def evaluate_target_pacing(client_id: str) -> dict:
    """
    Evaluate target pacing alerts for a client.
    Called after evaluate_budget_pacing() in core_scheduler.

    Returns summary dict: {created, updated, resolved, skipped_reason?}.
    """
    from ..database import get_supabase

    sb  = get_supabase()
    now = datetime.now(timezone.utc)
    today = now.date()

    # Period info
    year, month        = today.year, today.month
    days_in_month      = calendar.monthrange(year, month)[1]
    days_elapsed       = today.day - 1   # completed days
    period_label       = f"{year:04d}-{month:02d}"
    first_day_of_month = today.replace(day=1)
    elapsed_pct        = days_elapsed / days_in_month if days_in_month > 0 else 0.0

    # ── 1. Resolve client UUID + business_model ───────────────────────────────
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
        logger.error("target_pacing: client load failed %s: %s", client_id, exc)
        return {"error": str(exc)}

    if not c:
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
        logger.warning("target_pacing: target_truth load failed %s: %s", client_id, exc)
        target_truth = {}

    if not target_truth:
        logger.info("target_pacing: %s — no target_truth, skipping", client_id)
        return {"skipped": "no_target_truth"}

    # ── 3. Load MTD spend + conversions from ad_spend ─────────────────────────
    try:
        spend_res = (
            sb.table("ad_spend")
            .select("channel, spend, conversions, conversion_value")
            .eq("client_id", client_uuid)
            .gte("date", first_day_of_month.isoformat())
            .lt("date", today.isoformat())
            .execute()
        )
        spend_rows: list[dict] = spend_res.data or []
    except Exception as exc:
        logger.error("target_pacing: ad_spend load failed %s: %s", client_id, exc)
        spend_rows = []

    # Aggregate per channel
    mtd_spend:      dict[str, float] = {}
    mtd_conv:       dict[str, float] = {}
    mtd_conv_value: dict[str, float] = {}
    for row in spend_rows:
        ch = str(row.get("channel") or "")
        mtd_spend[ch]      = mtd_spend.get(ch, 0.0)      + float(row.get("spend")            or 0)
        mtd_conv[ch]       = mtd_conv.get(ch, 0.0)       + float(row.get("conversions")       or 0)
        mtd_conv_value[ch] = mtd_conv_value.get(ch, 0.0) + float(row.get("conversion_value")  or 0)

    mtd_total_spend = sum(mtd_spend.values())

    # ── 4. Load MTD business revenue from orders ──────────────────────────────
    mtd_revenue: Optional[float] = None
    try:
        orders_res = (
            sb.table("orders")
            .select("total_price")
            .eq("client_id", client_uuid)
            .in_("financial_status", ["paid", "partially_refunded"])
            .gte("created_at", first_day_of_month.isoformat())
            .lt("created_at", today.isoformat())
            .execute()
        )
        orders = orders_res.data or []
        if orders:
            mtd_revenue = sum(float(o.get("total_price") or 0) for o in orders)
    except Exception as exc:
        logger.warning("target_pacing: orders load failed %s: %s", client_id, exc)

    # ── 5. Load open target alerts ────────────────────────────────────────────
    try:
        open_res = (
            sb.table("alerts")
            .select("id, type, fingerprint, occurrence_count")
            .eq("client_id", client_uuid)
            .in_("type", list(TARGET_ALERT_TYPES))
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
        logger.error("target_pacing: open alert load failed %s: %s", client_id, exc)
        existing_open = {}

    current_fingerprints: set[str] = set()

    def _fire(fp: str, atype: str, title: str, msg: str, ev: dict) -> None:
        current_fingerprints.add(fp)
        _upsert_alert(sb, fp, atype, client_uuid, title, msg, ev, existing_open)

    def _volume_check(
        target_key: str,
        actual_mtd: Optional[float],
        source_label: str,
        alert_pace: str,
        alert_risk: str,
        fp_qualifier: str,
        unit: str = "",
    ) -> None:
        """Shared logic for volume target pacing."""
        if days_elapsed < _MIN_DAYS_VOL:
            return

        tt_entry = target_truth.get(target_key)
        if not isinstance(tt_entry, dict) or "target" not in tt_entry:
            return  # NOT_APPLICABLE

        # Require explicit period='monthly'
        if (tt_entry.get("period") or "").lower() != "monthly":
            logger.debug(
                "target_pacing: %s/%s — period not 'monthly' (%s), skipping",
                client_id, target_key, tt_entry.get("period"),
            )
            return

        target_val = _safe_float(tt_entry["target"])
        if not target_val or target_val <= 0:
            return

        if actual_mtd is None:
            return  # source unavailable — absence ≠ zero

        expected    = target_val * elapsed_pct
        attainment  = actual_mtd / expected if expected > 0 else None
        projected   = (actual_mtd / days_elapsed * days_in_month) if days_elapsed > 0 else None

        ev_base = {
            "client_id":       client_id,
            "target_key":      target_key,
            "period_label":    period_label,
            "days_elapsed":    days_elapsed,
            "days_in_month":   days_in_month,
            "elapsed_pct":     round(elapsed_pct, 4),
            "target":          target_val,
            "actual_mtd":      round(actual_mtd, 2),
            "expected_to_date": round(expected, 2) if expected else None,
            "attainment_pct":  round(attainment, 4) if attainment is not None else None,
            "projected_eom":   round(projected, 2) if projected is not None else None,
            "unit":            unit,
        }

        if attainment is not None and attainment < _VOL_PACE_WARN:
            _fire(
                f"{alert_pace}:{client_uuid}:{fp_qualifier}:{period_label}",
                alert_pace,
                f"{source_label} abaixo do ritmo — {client_id}",
                (
                    f"{source_label} acumulou {actual_mtd:,.2f}{unit} "
                    f"({attainment*100:.1f}% do esperado {expected:,.2f}{unit}) "
                    f"em {days_elapsed}/{days_in_month} dias de {period_label}."
                ),
                ev_base,
            )

        if projected is not None and projected < target_val * _VOL_AT_RISK:
            _fire(
                f"{alert_risk}:{client_uuid}:{fp_qualifier}:{period_label}",
                alert_risk,
                f"Meta de {source_label} em risco — {client_id}",
                (
                    f"Projeção de fim de mês para {source_label}: "
                    f"{projected:,.2f}{unit} vs meta {target_val:,.2f}{unit} "
                    f"({projected/target_val*100:.1f}%)."
                ),
                {**ev_base, "projection_pct": round(projected / target_val, 4)},
            )

    # ═════════════════════════════════════════════════════════════════════════
    # Rule A — REVENUE_BELOW_PACE / REVENUE_TARGET_AT_RISK
    # Uses business revenue from orders — never media-attributed revenue.
    # ═════════════════════════════════════════════════════════════════════════
    _volume_check(
        "revenue_business",
        mtd_revenue,
        "Receita",
        "REVENUE_BELOW_PACE",
        "REVENUE_TARGET_AT_RISK",
        fp_qualifier="revenue",
        unit=" BRL",
    )

    # ═════════════════════════════════════════════════════════════════════════
    # Rule B — CONVERSIONS_BELOW_PACE / CONVERSIONS_TARGET_AT_RISK (per platform)
    # ═════════════════════════════════════════════════════════════════════════
    for platform, channel in _AD_SPEND_CHANNEL.items():
        conv_key = f"{platform}_conversions"
        actual_conv: Optional[float] = (
            mtd_conv.get(channel) if spend_rows else None
        )
        _volume_check(
            conv_key,
            actual_conv,
            f"Conversões {platform.capitalize()}",
            "CONVERSIONS_BELOW_PACE",
            "CONVERSIONS_TARGET_AT_RISK",
            fp_qualifier=platform,
        )

    # ═════════════════════════════════════════════════════════════════════════
    # Rule C — LEADS_BELOW_PACE / LEADS_TARGET_AT_RISK
    # Only for lead_generation and auction business models.
    # ═════════════════════════════════════════════════════════════════════════
    if business_model in ("lead_generation", "auction"):
        leads_channel = _AD_SPEND_CHANNEL.get(
            "google" if business_model == "lead_generation" else "google", "google_ads"
        )
        actual_leads: Optional[float] = (
            mtd_conv.get(leads_channel) if spend_rows else None
        )
        _volume_check(
            "leads",
            actual_leads,
            "Leads",
            "LEADS_BELOW_PACE",
            "LEADS_TARGET_AT_RISK",
            fp_qualifier="leads",
        )

    # ═════════════════════════════════════════════════════════════════════════
    # Rule D — MER_BELOW_TARGET (efficiency — point-in-time, no linear pacing)
    # MER = total_revenue / total_spend (MTD calculation here, not snapshot).
    # ═════════════════════════════════════════════════════════════════════════
    tt_mer = target_truth.get("mer")
    if isinstance(tt_mer, dict) and "target" in tt_mer:
        target_mer = _safe_float(tt_mer["target"])
        if target_mer and target_mer > 0 and mtd_revenue is not None and mtd_total_spend > 0:
            actual_mer = mtd_revenue / mtd_total_spend
            if actual_mer < target_mer * _MER_MISS_RATIO:
                fp = f"MER_BELOW_TARGET:{client_uuid}:{period_label}"
                _fire(
                    fp, "MER_BELOW_TARGET",
                    f"MER abaixo da meta — {client_id}",
                    (
                        f"MER MTD = {actual_mer:.2f}x abaixo de "
                        f"{_MER_MISS_RATIO*100:.0f}% da meta ({target_mer:.2f}x). "
                        f"Receita: {mtd_revenue:,.2f} / Gasto: {mtd_total_spend:,.2f}."
                    ),
                    {
                        "client_id":       client_id,
                        "period_label":    period_label,
                        "target_mer":      target_mer,
                        "actual_mer":      round(actual_mer, 4),
                        "threshold":       round(target_mer * _MER_MISS_RATIO, 4),
                        "mtd_revenue":     round(mtd_revenue, 2),
                        "mtd_total_spend": round(mtd_total_spend, 2),
                    },
                )

    # ── Auto-resolve alerts whose condition cleared ───────────────────────────
    resolved = _resolve_stale(sb, existing_open, current_fingerprints, now)
    created  = sum(1 for fp in current_fingerprints if fp not in existing_open)
    updated  = sum(1 for fp in current_fingerprints if fp in existing_open)

    logger.info(
        "target_pacing: %s created=%d updated=%d resolved=%d",
        client_id, created, updated, resolved,
    )
    return {
        "created":  created,
        "updated":  updated,
        "resolved": resolved,
    }
