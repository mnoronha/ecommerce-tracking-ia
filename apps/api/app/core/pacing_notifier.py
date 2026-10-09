"""
Telegram dispatcher for budget pacing and target pacing alerts.

Uses the same persistent delivery dedup framework as balance_notifier.py:
  - Never sent → send.
  - Already sent (status='sent') → skip (restart-safe).
  - Failed delivery (status='failed') → retry on next tick.
  - Alert resolved → auto-resolved by evaluator; no action here.

Alert types handled:
  BUDGET_PACING_LOW / BUDGET_PACING_HIGH
  PROJECTED_OVERSPEND / PROJECTED_UNDERSPEND
  REVENUE_TARGET_AT_RISK / CONVERSIONS_TARGET_AT_RISK / LEADS_TARGET_AT_RISK
  MER_BELOW_TARGET
  ROAS_BELOW_TARGET / CPA_ABOVE_TARGET (performance alerts, piggybacked here)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from ..database import get_supabase
from ..services import telegram

logger = logging.getLogger(__name__)

_PACING_TELEGRAM_TYPES = frozenset({
    "BUDGET_PACING_LOW",
    "BUDGET_PACING_HIGH",
    "PROJECTED_OVERSPEND",
    "PROJECTED_UNDERSPEND",
    "REVENUE_TARGET_AT_RISK",
    "CONVERSIONS_TARGET_AT_RISK",
    "LEADS_TARGET_AT_RISK",
    "MER_BELOW_TARGET",
    "ROAS_BELOW_TARGET",
    "CPA_ABOVE_TARGET",
})

_EMOJI = {
    "BUDGET_PACING_LOW":          "📉",
    "BUDGET_PACING_HIGH":         "📈",
    "PROJECTED_OVERSPEND":        "🔴",
    "PROJECTED_UNDERSPEND":       "⚠️",
    "REVENUE_TARGET_AT_RISK":     "🚨",
    "CONVERSIONS_TARGET_AT_RISK": "🚨",
    "LEADS_TARGET_AT_RISK":       "🚨",
    "MER_BELOW_TARGET":           "📊",
    "ROAS_BELOW_TARGET":          "📊",
    "CPA_ABOVE_TARGET":           "📊",
}


# ── Persistent dedup (identical to balance_notifier.py) ───────────────────────

def _has_successful_delivery(sb, alert_id: str, channel: str = "telegram") -> bool:
    try:
        res = (
            sb.table("core_notification_deliveries")
            .select("id")
            .eq("alert_id", alert_id)
            .eq("channel", channel)
            .eq("status", "sent")
            .limit(1)
            .execute()
        )
        return bool(res.data)
    except Exception as exc:
        logger.warning("pacing_notifier: delivery lookup failed %s: %s", alert_id, exc)
        return True  # conservative — skip to avoid flood on DB error


def _record_delivery(
    sb,
    alert_id: str,
    channel: str,
    status: str,
    alert_type: str,
    severity: str,
    provider_response: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    try:
        sb.table("core_notification_deliveries").insert({
            "alert_id":           alert_id,
            "channel":            channel,
            "status":             status,
            "alert_type_at_send": alert_type,
            "severity_at_send":   severity,
            "provider_response":  provider_response,
            "error":              error,
        }).execute()
    except Exception as exc:
        logger.error(
            "pacing_notifier: failed to record delivery %s/%s/%s: %s",
            alert_id, channel, status, exc,
        )


def _append_sent_via(sb, alert_id: str, channel: str) -> None:
    try:
        row = (
            sb.table("alerts")
            .select("sent_via")
            .eq("id", alert_id)
            .limit(1)
            .execute()
        ).data
        current_via = list((row[0].get("sent_via") or []) if row else [])
        if channel not in current_via:
            current_via.append(channel)
            sb.table("alerts").update({"sent_via": current_via}).eq("id", alert_id).execute()
    except Exception as exc:
        logger.warning("pacing_notifier: sent_via update failed %s: %s", alert_id, exc)


# ── Message builder ────────────────────────────────────────────────────────────

def _build_message(alert: dict) -> str:
    data       = alert.get("data") or {}
    alert_type = alert.get("type", "")
    emoji      = _EMOJI.get(alert_type, "⚠️")
    client_id  = str(data.get("client_id") or "—")
    platform   = str(data.get("platform") or "")
    plat_label = f" — {platform.capitalize()}" if platform else ""
    period     = str(data.get("period_label") or "")
    period_lbl = f" ({period})" if period else ""

    # Use alert title/message stored in DB (already human-readable)
    title   = str(alert.get("title") or alert_type)
    message = str(alert.get("message") or "")

    lines = [
        f"{emoji} <b>{title}{period_lbl}</b>",
        "",
        message,
    ]

    # Append key numeric context from evidence when available
    pacing_ratio = data.get("pacing_ratio")
    if pacing_ratio is not None:
        lines.append(f"Pacing: <b>{pacing_ratio*100:.1f}%</b> do esperado")

    attainment = data.get("attainment_pct")
    if attainment is not None:
        lines.append(f"Atingimento MTD: <b>{attainment*100:.1f}%</b>")

    actual_mer = data.get("actual_mer")
    if actual_mer is not None:
        target_mer = data.get("target_mer")
        if target_mer:
            lines.append(f"MER: <b>{actual_mer:.2f}x</b> vs meta <b>{target_mer:.2f}x</b>")

    return "\n".join(lines)


# ── Dispatcher ─────────────────────────────────────────────────────────────────

def dispatch_pacing_notifications(client_id: str) -> dict:
    """
    Read OPEN pacing/target alerts for this client and dispatch
    Telegram messages using persistent delivery dedup.

    Called by core_scheduler after budget + target pacing evaluation.
    """
    sb = get_supabase()

    # Resolve client UUID
    try:
        res = (
            sb.table("clients")
            .select("id")
            .eq("client_id", client_id)
            .limit(1)
            .execute()
        )
        client_uuid: Optional[str] = res.data[0]["id"] if (res and res.data) else None
    except Exception as exc:
        logger.error("pacing_notifier: client lookup failed %s: %s", client_id, exc)
        return {"error": str(exc)}

    if not client_uuid:
        return {"error": f"client not found: {client_id}"}

    # Load OPEN pacing alerts
    try:
        alert_res = (
            sb.table("alerts")
            .select("id, type, severity, title, message, data")
            .eq("client_id", client_uuid)
            .in_("type", list(_PACING_TELEGRAM_TYPES))
            .eq("status", "OPEN")
            .is_("resolved_at", "null")
            .execute()
        )
        alerts = alert_res.data or []
    except Exception as exc:
        logger.error("pacing_notifier: alert load failed %s: %s", client_id, exc)
        return {"error": str(exc)}

    sent    = 0
    skipped = 0
    failed  = 0

    for alert in alerts:
        alert_id   = alert["id"]
        alert_type = alert.get("type", "")
        severity   = alert.get("severity", "")

        if _has_successful_delivery(sb, alert_id, "telegram"):
            skipped += 1
            continue

        text   = _build_message(alert)
        ok, tg = telegram.send_message(text)

        if ok:
            _record_delivery(
                sb, alert_id, "telegram", "sent",
                alert_type=alert_type, severity=severity,
                provider_response=tg,
            )
            _append_sent_via(sb, alert_id, "telegram")
            sent += 1
            logger.info(
                "pacing_notifier: sent telegram for %s/%s message_id=%s",
                client_id, alert_id, tg.get("message_id"),
            )
        else:
            _record_delivery(
                sb, alert_id, "telegram", "failed",
                alert_type=alert_type, severity=severity,
                provider_response=tg,
                error=tg.get("reason", "unknown"),
            )
            failed += 1
            logger.warning(
                "pacing_notifier: telegram delivery failed %s/%s reason=%s",
                client_id, alert_id, tg.get("reason"),
            )

    logger.info(
        "pacing_notifier: %s — sent=%d skipped=%d failed=%d",
        client_id, sent, skipped, failed,
    )
    return {"client_id": client_id, "sent": sent, "skipped": skipped, "failed": failed}
