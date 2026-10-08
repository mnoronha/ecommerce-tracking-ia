"""
Telegram dispatcher for ACCOUNT_BALANCE_* alerts.

Dedup is PERSISTENT via core_notification_deliveries table — survives
restarts and redeploys. No in-process cache.

Rules:
  - Never sent → send.
  - Already sent (status='sent') → skip. Same fingerprint/severity never re-notifies.
  - Failed delivery (status='failed') → retry on next tick.
  - Severity escalation (LOW→CRITICAL) creates a NEW alert_id (new fingerprint)
    → no delivery record found → sends immediately. No extra logic needed.
  - Alert resolved → auto-resolved by alert_evaluator; no action here.
  - Does NOT alter Alert Engine or Balance classification.

On send:
  INSERT core_notification_deliveries (alert_id, channel='telegram', status='sent', ...)
  UPDATE alerts.sent_via to include 'telegram'

On failure:
  INSERT core_notification_deliveries (alert_id, channel='telegram', status='failed', error=...)
  Do NOT mark as sent — next tick will retry.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from ..database import get_supabase
from ..services import telegram

logger = logging.getLogger(__name__)

_BALANCE_TYPES = frozenset({
    "ACCOUNT_BALANCE_LOW",
    "ACCOUNT_BALANCE_CRITICAL",
    "ACCOUNT_BALANCE_EXHAUSTED",
})

_EMOJI = {
    "ACCOUNT_BALANCE_LOW":       "⚠️",
    "ACCOUNT_BALANCE_CRITICAL":  "🚨",
    "ACCOUNT_BALANCE_EXHAUSTED": "🔴",
}
_HEADING = {
    "ACCOUNT_BALANCE_LOW":       "Saldo baixo",
    "ACCOUNT_BALANCE_CRITICAL":  "Saldo crítico",
    "ACCOUNT_BALANCE_EXHAUSTED": "Saldo esgotado",
}
_PLATFORM_LABEL = {"google": "Google Ads", "meta": "Meta Ads"}


# ── Persistent dedup ───────────────────────────────────────────────────────────

def _has_successful_delivery(sb, alert_id: str, channel: str = "telegram") -> bool:
    """
    Returns True if a 'sent' delivery record already exists for this alert+channel.
    Restart-safe: reads from DB, not process memory.
    """
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
        logger.warning("balance_notifier: delivery lookup failed %s: %s", alert_id, exc)
        # On DB error: conservative — skip to avoid flood
        return True


def _record_delivery(
    sb,
    alert_id: str,
    channel: str,
    status: str,                    # 'sent' | 'failed'
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
            "balance_notifier: failed to record delivery %s/%s/%s: %s",
            alert_id, channel, status, exc,
        )


def _append_sent_via(sb, alert_id: str, channel: str) -> None:
    """Append channel to alerts.sent_via (idempotent read-modify-write)."""
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
        logger.warning("balance_notifier: sent_via update failed %s: %s", alert_id, exc)


# ── Message builder ────────────────────────────────────────────────────────────

def _build_message(alert: dict) -> str:
    data       = alert.get("data") or {}
    alert_type = alert.get("type", "")
    emoji      = _EMOJI.get(alert_type, "⚠️")
    heading    = _HEADING.get(alert_type, "Saldo")
    client_id  = str(data.get("client_id") or "—")
    platform   = _PLATFORM_LABEL.get(str(data.get("platform") or ""), str(data.get("platform") or ""))
    balance    = data.get("balance")
    currency   = str(data.get("currency") or "")
    avg_spend  = data.get("avg_daily_spend")
    days       = data.get("estimated_days_remaining")
    window     = data.get("spend_avg_window_days")

    def _brl(v: object) -> str:
        try:
            s = f"{float(v):,.2f}"
            # convert to pt-BR decimal notation
            return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")
        except (TypeError, ValueError):
            return "—"

    bal_str   = _brl(balance) if balance is not None else "—"
    avg_str   = f"{_brl(avg_spend)}/dia" if avg_spend is not None else "—"
    win_str   = f" ({window}d)" if window else ""
    days_str  = (
        f"{float(days):.1f}".replace(".", ",") + " dia(s)"
        if days is not None else "—"
    )

    return "\n".join([
        f"{emoji} <b>{heading} — {platform} — {client_id}</b>",
        "",
        f"Saldo: <b>{bal_str} {currency}</b>",
        f"Gasto médio: {avg_str}{win_str}",
        f"Autonomia: <b>{days_str}</b>",
        "",
        "Ação necessária: recarregar a conta.",
    ])


# ── Dispatcher ─────────────────────────────────────────────────────────────────

def dispatch_balance_notifications(client_id: str) -> dict:
    """
    Read OPEN ACCOUNT_BALANCE_* alerts for this client and dispatch
    Telegram messages using persistent delivery dedup.

    Called by balance_monitor after each snapshot + alert evaluation cycle.
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
        logger.error("balance_notifier: client lookup failed %s: %s", client_id, exc)
        return {"error": str(exc)}

    if not client_uuid:
        return {"error": f"client not found: {client_id}"}

    # Load OPEN balance alerts
    try:
        alert_res = (
            sb.table("alerts")
            .select("id, type, severity, title, message, data")
            .eq("client_id", client_uuid)
            .in_("type", list(_BALANCE_TYPES))
            .eq("status", "OPEN")
            .is_("resolved_at", "null")
            .execute()
        )
        alerts = alert_res.data or []
    except Exception as exc:
        logger.error("balance_notifier: alert load failed %s: %s", client_id, exc)
        return {"error": str(exc)}

    sent    = 0
    skipped = 0
    failed  = 0

    for alert in alerts:
        alert_id   = alert["id"]
        alert_type = alert.get("type", "")
        severity   = alert.get("severity", "")

        # Persistent dedup: skip if already successfully delivered
        if _has_successful_delivery(sb, alert_id, "telegram"):
            skipped += 1
            continue

        text = _build_message(alert)
        ok   = telegram.send_message(text)

        if ok:
            _record_delivery(
                sb, alert_id, "telegram", "sent",
                alert_type=alert_type, severity=severity,
            )
            _append_sent_via(sb, alert_id, "telegram")
            sent += 1
            logger.info("balance_notifier: sent telegram for %s/%s", client_id, alert_id)
        else:
            _record_delivery(
                sb, alert_id, "telegram", "failed",
                alert_type=alert_type, severity=severity,
                error="telegram.send_message returned False",
            )
            failed += 1
            logger.warning("balance_notifier: telegram delivery failed for %s/%s", client_id, alert_id)

    logger.info(
        "balance_notifier: %s — sent=%d skipped=%d failed=%d",
        client_id, sent, skipped, failed,
    )
    return {"client_id": client_id, "sent": sent, "skipped": skipped, "failed": failed}
