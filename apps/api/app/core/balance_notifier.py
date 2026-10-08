"""
Telegram dispatcher for ACCOUNT_BALANCE_* alerts.

Called by balance_monitor after each snapshot + alert evaluation cycle.

Dedup: module-level in-process cache keyed by alert_id → last_notified_at.
  - Cooldown: 4h for LOW, 2h for CRITICAL/EXHAUSTED.
  - In-process only — persists between hourly ticks, resets on deploy.
    A single resend after a Railway restart is acceptable for an ops channel.
  - Severity increase (LOW→CRITICAL) creates a new alert with a new fingerprint,
    which is never in cache → sends immediately. No special severity logic needed.

Delivery: updates alerts.sent_via to include "telegram" on first successful send.
Does NOT invent balance values — reads them from the alert's data jsonb.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..database import get_supabase
from ..services import telegram

logger = logging.getLogger(__name__)

_BALANCE_TYPES = frozenset({
    "ACCOUNT_BALANCE_LOW",
    "ACCOUNT_BALANCE_CRITICAL",
    "ACCOUNT_BALANCE_EXHAUSTED",
})

_COOLDOWN: dict[str, timedelta] = {
    "ACCOUNT_BALANCE_LOW":       timedelta(hours=4),
    "ACCOUNT_BALANCE_CRITICAL":  timedelta(hours=2),
    "ACCOUNT_BALANCE_EXHAUSTED": timedelta(hours=2),
}
_DEFAULT_COOLDOWN = timedelta(hours=4)

_EMOJI = {
    "ACCOUNT_BALANCE_LOW":       "⚠️",
    "ACCOUNT_BALANCE_CRITICAL":  "🚨",
    "ACCOUNT_BALANCE_EXHAUSTED": "🔴",
}
_TITLE = {
    "ACCOUNT_BALANCE_LOW":       "Saldo baixo",
    "ACCOUNT_BALANCE_CRITICAL":  "Saldo crítico",
    "ACCOUNT_BALANCE_EXHAUSTED": "Saldo esgotado",
}
_PLATFORM_LABEL = {"google": "Google Ads", "meta": "Meta Ads"}

# In-process dedup: alert_id → last_notified_at (UTC)
_sent_cache: dict[str, datetime] = {}


def _should_notify(alert_id: str, alert_type: str) -> bool:
    last = _sent_cache.get(alert_id)
    if last is None:
        return True
    cooldown = _COOLDOWN.get(alert_type, _DEFAULT_COOLDOWN)
    return datetime.now(timezone.utc) - last >= cooldown


def _build_message(alert: dict) -> str:
    data       = alert.get("data") or {}
    alert_type = alert.get("type", "")
    emoji      = _EMOJI.get(alert_type, "⚠️")
    heading    = _TITLE.get(alert_type, "Saldo")
    client_id  = str(data.get("client_id") or "—")
    platform   = _PLATFORM_LABEL.get(str(data.get("platform") or ""), str(data.get("platform") or ""))
    balance    = data.get("balance")
    currency   = str(data.get("currency") or "")
    avg_spend  = data.get("avg_daily_spend")
    days       = data.get("estimated_days_remaining")
    window     = data.get("spend_avg_window_days")

    def _fmt_brl(v: object) -> str:
        try:
            return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (TypeError, ValueError):
            return "—"

    bal_str  = _fmt_brl(balance) if balance is not None else "—"
    avg_str  = f"{_fmt_brl(avg_spend)}/dia" if avg_spend is not None else "—"
    days_str = (
        f"{float(days):.1f} dia(s)".replace(".", ",")
        if days is not None else "—"
    )
    window_str = f" ({window}d)" if window else ""

    lines = [
        f"{emoji} <b>{heading} — {platform} — {client_id}</b>",
        "",
        f"Saldo: <b>{bal_str} {currency}</b>",
        f"Gasto médio: {avg_str}{window_str}",
        f"Autonomia: <b>{days_str}</b>",
        "",
        "Ação necessária: recarregar a conta.",
    ]
    return "\n".join(lines)


def dispatch_balance_notifications(client_id: str) -> dict:
    """
    Read OPEN ACCOUNT_BALANCE_* alerts for this client and dispatch
    Telegram messages, respecting the in-process cooldown dedup cache.
    """
    sb  = get_supabase()
    now = datetime.now(timezone.utc)

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
            .select("id, type, title, message, data, severity, sent_via")
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

    for alert in alerts:
        alert_id   = alert["id"]
        alert_type = alert.get("type", "")

        if not _should_notify(alert_id, alert_type):
            skipped += 1
            continue

        text = _build_message(alert)
        ok   = telegram.send_message(text)

        if ok:
            _sent_cache[alert_id] = now
            sent += 1
            # Append "telegram" to sent_via (idempotent)
            current_via = list(alert.get("sent_via") or [])
            if "telegram" not in current_via:
                current_via.append("telegram")
                try:
                    sb.table("alerts").update({"sent_via": current_via}).eq("id", alert_id).execute()
                except Exception as exc:
                    logger.warning("balance_notifier: sent_via update failed %s: %s", alert_id, exc)
        else:
            logger.warning("balance_notifier: telegram send failed for alert %s", alert_id)

    logger.info("balance_notifier: %s — sent=%d skipped=%d", client_id, sent, skipped)
    return {"client_id": client_id, "sent": sent, "skipped": skipped}
