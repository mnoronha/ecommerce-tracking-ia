"""
Conversion rate alert system.

Runs on a schedule (every 6 hours). For each active client with a
slack_webhook_url, compares last-24h conversion rate vs. 7-day baseline.
Sends a Slack alert when the drop exceeds DROP_THRESHOLD (30%).

To avoid spam, each client is rate-limited to one alert per COOLDOWN_HOURS.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from ..database import get_supabase
from . import email as email_service

logger = logging.getLogger(__name__)

DROP_THRESHOLD  = 0.30   # 30% drop triggers alert
COOLDOWN_HOURS  = 24     # minimum hours between repeated alerts per client

# In-memory cooldown: client_id → last alert datetime (resets on restart)
_last_alerted: dict[str, datetime] = {}


# ── Core check ────────────────────────────────────────────────────────────────

def run_conversion_check() -> None:
    """Entry point called by the scheduler. Checks all active clients."""
    try:
        clients = (
            get_supabase()
            .table("clients")
            .select("id, pixel_id, slack_webhook_url")
            .eq("is_active", True)
            .execute()
        )
        if not (clients and clients.data):
            return
        for c in clients.data:
            if c.get("slack_webhook_url"):
                _check_client(c["id"], c["pixel_id"], c["slack_webhook_url"])
    except Exception as exc:
        logger.error("run_conversion_check: %s", exc)


def _check_client(client_id: str, pixel_id: str, webhook_url: str) -> None:
    now = datetime.now(timezone.utc)
    t24h = now - timedelta(hours=24)
    t8d  = now - timedelta(days=8)

    sb = get_supabase()
    try:
        # ── Last 24 hours ─────────────────────────────────────────────────────
        v24 = (sb.table("visitors").select("id", count="exact", head=True)
               .eq("client_id", client_id).gte("last_seen_at", t24h.isoformat()).execute())
        o24 = (sb.table("orders").select("id", count="exact", head=True)
               .eq("client_id", client_id).gte("created_at", t24h.isoformat()).execute())

        visitors_24h = v24.count or 0
        orders_24h   = o24.count or 0

        # ── Days 2–8 baseline (7-day average) ─────────────────────────────────
        v7 = (sb.table("visitors").select("id", count="exact", head=True)
              .eq("client_id", client_id)
              .gte("last_seen_at", t8d.isoformat())
              .lt("last_seen_at", t24h.isoformat()).execute())
        o7 = (sb.table("orders").select("id", count="exact", head=True)
              .eq("client_id", client_id)
              .gte("created_at", t8d.isoformat())
              .lt("created_at", t24h.isoformat()).execute())

        visitors_7d = v7.count or 0
        orders_7d   = o7.count or 0
    except Exception as exc:
        logger.warning("_check_client(%s): query failed — %s", pixel_id, exc)
        return

    if visitors_24h < 10 or visitors_7d < 10:
        return  # not enough traffic for a meaningful comparison

    conv_24h = orders_24h / visitors_24h
    conv_7d  = (orders_7d / 7) / (visitors_7d / 7)  # daily average

    if conv_7d == 0:
        return

    drop = (conv_7d - conv_24h) / conv_7d
    logger.debug(
        "alert check %s — conv_24h=%.2f%% conv_7d=%.2f%% drop=%.1f%%",
        pixel_id, conv_24h * 100, conv_7d * 100, drop * 100,
    )

    if drop >= DROP_THRESHOLD:
        # Check cooldown
        last = _last_alerted.get(client_id)
        if last and (now - last).total_seconds() < COOLDOWN_HOURS * 3600:
            logger.debug("alert skipped (cooldown) for %s", pixel_id)
            return

        _send_slack_alert(
            webhook_url=webhook_url,
            pixel_id=pixel_id,
            conv_24h=conv_24h,
            conv_7d=conv_7d,
            drop=drop,
            orders_24h=orders_24h,
            visitors_24h=visitors_24h,
        )
        _last_alerted[client_id] = now


# ── Slack notification ────────────────────────────────────────────────────────

def _send_slack_alert(
    webhook_url: str,
    pixel_id: str,
    conv_24h: float,
    conv_7d: float,
    drop: float,
    orders_24h: int,
    visitors_24h: int,
) -> None:
    message = {
        "text": f":warning: *Queda de conversão detectada — {pixel_id}*",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":warning: *Queda de conversão — `{pixel_id}`*\n"
                        f"Taxa hoje: *{conv_24h:.2%}* "
                        f"vs. média 7d: *{conv_7d:.2%}* "
                        f"(queda de *{drop:.1%}*)\n"
                        f"Pedidos nas últimas 24h: *{orders_24h}* | "
                        f"Visitantes: *{visitors_24h}*"
                    ),
                },
            }
        ],
    }
    try:
        resp = httpx.post(webhook_url, json=message, timeout=10)
        resp.raise_for_status()
        logger.info("slack alert sent for %s (drop=%.1f%%)", pixel_id, drop * 100)
    except Exception as exc:
        logger.warning("_send_slack_alert(%s): %s", pixel_id, exc)


# ── Weekly report ─────────────────────────────────────────────────────────────

def send_weekly_reports() -> None:
    """Entry point called by the scheduler (Mondays 8h). Sends weekly report to all clients."""
    try:
        clients = (
            get_supabase()
            .table("clients")
            .select("id, pixel_id, notification_email")
            .eq("is_active", True)
            .not_.is_("notification_email", "null")
            .execute()
        )
        if not (clients and clients.data):
            return
        for c in clients.data:
            if c.get("notification_email"):
                _send_client_weekly_report(c["id"], c["pixel_id"], c["notification_email"])
    except Exception as exc:
        logger.error("send_weekly_reports: %s", exc)


def _send_client_weekly_report(client_id: str, pixel_id: str, to_email: str) -> None:
    """Fetch the latest weekly_report insight and send it by email."""
    try:
        result = (
            get_supabase()
            .table("ai_insights")
            .select("title, content, created_at")
            .eq("client_id", client_id)
            .eq("type", "weekly_report")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not (result and result.data):
            logger.info("weekly report: no insight found for %s — skipping email", pixel_id)
            return
        insight = result.data[0]
    except Exception as exc:
        logger.warning("_send_client_weekly_report(%s): query failed — %s", pixel_id, exc)
        return

    subject = f"Relatório semanal — {pixel_id}"
    html_body = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px">
      <h2 style="color:#1a1a2e">{insight['title']}</h2>
      <div style="background:#f8f9fa;border-left:4px solid #4361ee;padding:16px;border-radius:4px">
        <pre style="white-space:pre-wrap;font-family:inherit;margin:0">{insight['content']}</pre>
      </div>
      <p style="color:#888;font-size:12px;margin-top:24px">
        Gerado em {insight['created_at'][:10]} · Ecommerce Tracking IA · {pixel_id}
      </p>
    </body></html>
    """
    email_service.send_email(to=to_email, subject=subject, html_body=html_body)


# ── Manual trigger (for testing) ─────────────────────────────────────────────

def send_test_alert(pixel_id: str, webhook_url: str) -> bool:
    """Send a test alert to confirm the Slack webhook is working."""
    try:
        resp = httpx.post(
            webhook_url,
            json={"text": f":white_check_mark: Alerta de teste — `{pixel_id}` conectado com sucesso!"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("send_test_alert: %s", exc)
        return False
