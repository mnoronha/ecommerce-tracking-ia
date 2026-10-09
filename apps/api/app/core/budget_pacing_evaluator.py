"""
Budget pacing evaluator.

Reads core_budget_config (monthly budget per client/platform) and ad_spend
(daily spend per platform) to calculate MTD pacing and fire alerts.

Rules:
  BUDGET_PACING_LOW    — pacing_ratio < 0.90 (underspending vs expected)
  BUDGET_PACING_HIGH   — pacing_ratio > 1.10 (overspending vs expected)
  PROJECTED_UNDERSPEND — projected EOM spend < budget × 0.80
  PROJECTED_OVERSPEND  — projected EOM spend > budget × 1.20

Quality guards:
  - No budget config → skip silently (NOT_APPLICABLE)
  - days_elapsed < 3  → early month, insufficient sample
  - spend_mtd == 0    → no spend data yet
  - expected_spend == 0 → skip (first day of month)
  - monitoring_enabled = false → skip

Never invents a budget. Absence of config ≠ zero budget.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Alert type catalogue ───────────────────────────────────────────────────────

BUDGET_ALERT_TYPES = frozenset({
    "BUDGET_PACING_LOW",
    "BUDGET_PACING_HIGH",
    "PROJECTED_UNDERSPEND",
    "PROJECTED_OVERSPEND",
})

_AD_SPEND_CHANNEL = {
    "google": "google_ads",
    "meta":   "meta_ads",
}

# Pacing thresholds
_PACE_LOW_WARN:     float = 0.90   # pacing_ratio below → BUDGET_PACING_LOW WARNING
_PACE_LOW_CRIT:     float = 0.80   # pacing_ratio below → BUDGET_PACING_LOW HIGH
_PACE_HIGH_WARN:    float = 1.10   # pacing_ratio above → BUDGET_PACING_HIGH WARNING
_PACE_HIGH_CRIT:    float = 1.20   # pacing_ratio above → BUDGET_PACING_HIGH HIGH
_PROJ_UNDER_RATIO:  float = 0.80   # projected EOM < budget × 0.80 → PROJECTED_UNDERSPEND
_PROJ_OVER_RATIO:   float = 1.20   # projected EOM > budget × 1.20 → PROJECTED_OVERSPEND
_MIN_DAYS_ELAPSED:  int   = 3      # guard: skip if fewer days elapsed this month


# ── Helpers ────────────────────────────────────────────────────────────────────

def _upsert_alert(
    sb,
    fingerprint: str,
    alert_type: str,
    client_uuid: str,
    title: str,
    message: str,
    evidence: dict,
    severity: str,
    existing_open: dict[str, dict],
) -> None:
    if fingerprint in existing_open:
        try:
            sb.table("alerts").update({
                "occurrence_count": existing_open[fingerprint]["occurrence_count"] + 1,
                "severity":         severity,
                "data":             evidence,
            }).eq("id", existing_open[fingerprint]["id"]).execute()
        except Exception as exc:
            logger.warning("budget_pacing: upsert update failed %s: %s", fingerprint, exc)
    else:
        try:
            sb.table("alerts").insert({
                "type":             alert_type,
                "client_id":        client_uuid,
                "title":            title,
                "message":          message,
                "severity":         severity,
                "status":           "OPEN",
                "fingerprint":      fingerprint,
                "data":             evidence,
                "occurrence_count": 1,
            }).execute()
        except Exception as exc:
            logger.warning("budget_pacing: insert failed %s: %s", fingerprint, exc)


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
                logger.warning("budget_pacing: resolve failed %s: %s", fp, exc)
    return resolved


# ── Main evaluator ─────────────────────────────────────────────────────────────

def evaluate_budget_pacing(client_id: str) -> dict:
    """
    Evaluate budget pacing alerts for a client.
    Called after evaluate_performance_alerts() in core_scheduler.

    Returns summary dict: {created, updated, resolved, platforms_evaluated}.
    """
    from ..database import get_supabase

    sb  = get_supabase()
    now = datetime.now(timezone.utc)
    today = now.date()

    # Period info
    year, month       = today.year, today.month
    days_in_month     = calendar.monthrange(year, month)[1]
    days_elapsed      = today.day - 1   # completed days (exclude today)
    period_label      = f"{year:04d}-{month:02d}"
    first_day_of_month = today.replace(day=1)

    # Guard: early month
    if days_elapsed < _MIN_DAYS_ELAPSED:
        logger.info("budget_pacing: %s — early month (%d days), skipping", client_id, days_elapsed)
        return {"skipped": "early_month", "days_elapsed": days_elapsed}

    elapsed_pct = days_elapsed / days_in_month

    # ── 1. Resolve client UUID ────────────────────────────────────────────────
    try:
        c_res = (
            sb.table("clients")
            .select("id")
            .eq("client_id", client_id)
            .limit(1)
            .execute()
        )
        c = c_res.data[0] if (c_res and c_res.data) else None
    except Exception as exc:
        logger.error("budget_pacing: client load failed %s: %s", client_id, exc)
        return {"error": str(exc)}

    if not c:
        logger.warning("budget_pacing: client not found %s", client_id)
        return {"error": "client_not_found"}

    client_uuid: str = c["id"]

    # ── 2. Load budget configs for this client/period ─────────────────────────
    try:
        cfg_res = (
            sb.table("core_budget_config")
            .select("platform, monthly_budget, currency, monitoring_enabled")
            .eq("client_id", client_id)
            .eq("period_label", period_label)
            .eq("monitoring_enabled", True)
            .execute()
        )
        configs: list[dict] = cfg_res.data or []
    except Exception as exc:
        logger.error("budget_pacing: config load failed %s: %s", client_id, exc)
        return {"error": str(exc)}

    if not configs:
        logger.info("budget_pacing: %s — no budget config for %s, skipping", client_id, period_label)
        return {"skipped": "no_budget_config", "period_label": period_label}

    # ── 3. Load MTD spend per platform from ad_spend ──────────────────────────
    try:
        spend_res = (
            sb.table("ad_spend")
            .select("channel, spend")
            .eq("client_id", client_uuid)
            .gte("date", first_day_of_month.isoformat())
            .lt("date", today.isoformat())
            .execute()
        )
        spend_rows: list[dict] = spend_res.data or []
    except Exception as exc:
        logger.error("budget_pacing: ad_spend load failed %s: %s", client_id, exc)
        spend_rows = []

    # Aggregate spend per channel
    spend_by_channel: dict[str, float] = {}
    for row in spend_rows:
        ch = str(row.get("channel") or "")
        spend_by_channel[ch] = spend_by_channel.get(ch, 0.0) + float(row.get("spend") or 0)

    # ── 4. Load existing open budget alerts ───────────────────────────────────
    try:
        open_res = (
            sb.table("alerts")
            .select("id, type, fingerprint, occurrence_count")
            .eq("client_id", client_uuid)
            .in_("type", list(BUDGET_ALERT_TYPES))
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
        logger.error("budget_pacing: open alert load failed %s: %s", client_id, exc)
        existing_open = {}

    current_fingerprints: set[str] = set()

    def _fire(fp: str, atype: str, title: str, msg: str, sev: str, ev: dict) -> None:
        current_fingerprints.add(fp)
        _upsert_alert(sb, fp, atype, client_uuid, title, msg, ev, sev, existing_open)

    # ── 5. Evaluate per platform ──────────────────────────────────────────────
    platforms_evaluated: list[str] = []

    for cfg in configs:
        platform       = cfg["platform"]
        monthly_budget = float(cfg["monthly_budget"])
        currency       = str(cfg.get("currency") or "BRL")
        channel_key    = _AD_SPEND_CHANNEL.get(platform, f"{platform}_ads")
        spend_mtd      = spend_by_channel.get(channel_key, 0.0)

        platforms_evaluated.append(platform)

        # Guard: no spend data
        if spend_mtd == 0.0 and not spend_rows:
            logger.info(
                "budget_pacing: %s/%s — no spend data in ad_spend, skipping",
                client_id, platform,
            )
            continue

        expected_spend  = monthly_budget * elapsed_pct
        remaining       = monthly_budget - spend_mtd
        projected_eom   = (spend_mtd / days_elapsed * days_in_month) if days_elapsed > 0 else None

        # Guard: expected == 0 (budget is zero)
        if monthly_budget == 0:
            continue

        pacing_ratio: Optional[float] = (
            spend_mtd / expected_spend if expected_spend > 0 else None
        )

        base_evidence = {
            "client_id":               client_id,
            "platform":                platform,
            "period_label":            period_label,
            "days_elapsed":            days_elapsed,
            "days_in_month":           days_in_month,
            "elapsed_pct":             round(elapsed_pct, 4),
            "monthly_budget":          monthly_budget,
            "spend_mtd":               round(spend_mtd, 2),
            "expected_spend_to_date":  round(expected_spend, 2) if expected_spend else None,
            "pacing_ratio":            round(pacing_ratio, 4) if pacing_ratio is not None else None,
            "remaining_budget":        round(remaining, 2),
            "projected_eom_spend":     round(projected_eom, 2) if projected_eom is not None else None,
            "currency":                currency,
        }

        # ── BUDGET_PACING_LOW / BUDGET_PACING_HIGH ────────────────────────────
        if pacing_ratio is not None:
            fp_low  = f"BUDGET_PACING_LOW:{client_uuid}:{platform}:{period_label}"
            fp_high = f"BUDGET_PACING_HIGH:{client_uuid}:{platform}:{period_label}"

            if pacing_ratio < _PACE_LOW_WARN:
                sev = "HIGH" if pacing_ratio < _PACE_LOW_CRIT else "MEDIUM"
                _fire(
                    fp_low, "BUDGET_PACING_LOW",
                    f"Orçamento sub-utilizado — {platform} — {client_id}",
                    (
                        f"{platform.capitalize()} gastou {spend_mtd:,.2f} {currency} "
                        f"({pacing_ratio*100:.1f}% do esperado {expected_spend:,.2f} {currency}) "
                        f"em {days_elapsed} de {days_in_month} dias de {period_label}."
                    ),
                    sev, base_evidence,
                )
            elif pacing_ratio > _PACE_HIGH_WARN:
                sev = "HIGH" if pacing_ratio > _PACE_HIGH_CRIT else "MEDIUM"
                _fire(
                    fp_high, "BUDGET_PACING_HIGH",
                    f"Orçamento acelerado — {platform} — {client_id}",
                    (
                        f"{platform.capitalize()} gastou {spend_mtd:,.2f} {currency} "
                        f"({pacing_ratio*100:.1f}% do esperado {expected_spend:,.2f} {currency}) "
                        f"em {days_elapsed} de {days_in_month} dias de {period_label}."
                    ),
                    sev, base_evidence,
                )

        # ── PROJECTED_UNDERSPEND / PROJECTED_OVERSPEND ────────────────────────
        if projected_eom is not None:
            fp_under = f"PROJECTED_UNDERSPEND:{client_uuid}:{platform}:{period_label}"
            fp_over  = f"PROJECTED_OVERSPEND:{client_uuid}:{platform}:{period_label}"

            if projected_eom < monthly_budget * _PROJ_UNDER_RATIO:
                _fire(
                    fp_under, "PROJECTED_UNDERSPEND",
                    f"Projeção de subutilização — {platform} — {client_id}",
                    (
                        f"{platform.capitalize()} projetado encerrar o mês com "
                        f"{projected_eom:,.2f} {currency} "
                        f"({projected_eom/monthly_budget*100:.1f}% do orçamento "
                        f"{monthly_budget:,.2f} {currency})."
                    ),
                    "MEDIUM", {**base_evidence, "projection_pct": round(projected_eom / monthly_budget, 4)},
                )
            elif projected_eom > monthly_budget * _PROJ_OVER_RATIO:
                _fire(
                    fp_over, "PROJECTED_OVERSPEND",
                    f"Projeção de estouro — {platform} — {client_id}",
                    (
                        f"{platform.capitalize()} projetado encerrar o mês com "
                        f"{projected_eom:,.2f} {currency} "
                        f"({projected_eom/monthly_budget*100:.1f}% do orçamento "
                        f"{monthly_budget:,.2f} {currency})."
                    ),
                    "MEDIUM", {**base_evidence, "projection_pct": round(projected_eom / monthly_budget, 4)},
                )

    # ── 6. Auto-resolve alerts whose condition cleared ────────────────────────
    resolved = _resolve_stale(sb, existing_open, current_fingerprints, now)
    created  = sum(1 for fp in current_fingerprints if fp not in existing_open)
    updated  = sum(1 for fp in current_fingerprints if fp in existing_open)

    logger.info(
        "budget_pacing: %s created=%d updated=%d resolved=%d platforms=%s",
        client_id, created, updated, resolved, platforms_evaluated,
    )
    return {
        "created":             created,
        "updated":             updated,
        "resolved":            resolved,
        "platforms_evaluated": platforms_evaluated,
    }
