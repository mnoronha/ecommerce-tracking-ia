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
from . import smtp as email_service

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
            .select("id, pixel_id, name, alert_email")
            .eq("is_active", True)
            .not_.is_("alert_email", "null")
            .execute()
        )
        if not (clients and clients.data):
            return
        for c in clients.data:
            if c.get("alert_email"):
                _send_client_weekly_report(c["id"], c["pixel_id"], c.get("name") or c["pixel_id"], c["alert_email"])
    except Exception as exc:
        logger.error("send_weekly_reports: %s", exc)


def _send_client_weekly_report(client_id: str, pixel_id: str, client_name: str, to_email: str) -> None:
    """
    Build and send a rich HTML weekly report with real data:
    - Revenue MTD vs goal
    - Orders, AOV, conversion rate
    - ROAS (if spend data available)
    - Open alerts summary
    - 7-day revenue sparkline table
    """
    from datetime import date

    sb  = get_supabase()
    now = datetime.now(timezone.utc)

    # ── Date windows ──────────────────────────────────────────────────────────
    month_start  = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_start   = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    prev_week_start = (now - timedelta(days=14)).replace(hour=0, minute=0, second=0, microsecond=0)

    # ── MTD orders ────────────────────────────────────────────────────────────
    mtd_q = (
        sb.table("orders")
        .select("total_price, created_at")
        .eq("client_id", client_id)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", month_start.isoformat())
        .execute()
    )
    mtd_orders  = mtd_q.data or []
    mtd_revenue = sum(float(o["total_price"]) for o in mtd_orders)
    mtd_count   = len(mtd_orders)
    mtd_aov     = round(mtd_revenue / mtd_count, 2) if mtd_count > 0 else 0

    # ── Last 7d vs prev 7d ────────────────────────────────────────────────────
    week_q = (
        sb.table("orders")
        .select("total_price, created_at")
        .eq("client_id", client_id)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", week_start.isoformat())
        .execute()
    )
    prev_week_q = (
        sb.table("orders")
        .select("total_price")
        .eq("client_id", client_id)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", prev_week_start.isoformat())
        .lt("created_at", week_start.isoformat())
        .execute()
    )
    week_revenue      = sum(float(o["total_price"]) for o in (week_q.data or []))
    prev_week_revenue = sum(float(o["total_price"]) for o in (prev_week_q.data or []))
    week_count        = len(week_q.data or [])
    week_delta_pct    = round((week_revenue - prev_week_revenue) / prev_week_revenue * 100, 1) if prev_week_revenue > 0 else None

    # ── Daily revenue last 7 days ─────────────────────────────────────────────
    daily: dict[str, float] = {}
    for o in (week_q.data or []):
        d = o["created_at"][:10]
        daily[d] = daily.get(d, 0.0) + float(o["total_price"])
    daily_rows = sorted(daily.items())  # [(date_str, revenue), ...]

    # ── Goal ──────────────────────────────────────────────────────────────────
    month_key = month_start.date().isoformat()
    goal_q = (
        sb.table("goals")
        .select("revenue_goal, roas_goal")
        .eq("client_id", client_id)
        .eq("month", month_key)
        .limit(1)
        .execute()
    )
    revenue_goal = None
    roas_goal    = None
    if goal_q.data:
        revenue_goal = goal_q.data[0].get("revenue_goal")
        roas_goal    = goal_q.data[0].get("roas_goal")
    if revenue_goal is None:
        client_row = sb.table("clients").select("monthly_revenue_goal, target_roas").eq("id", client_id).limit(1).execute()
        if client_row.data:
            revenue_goal = client_row.data[0].get("monthly_revenue_goal")
            roas_goal    = roas_goal or client_row.data[0].get("target_roas")

    goal         = float(revenue_goal or 0)
    pct_of_goal  = round(mtd_revenue / goal * 100, 1) if goal > 0 else None
    days_done    = now.day
    days_total   = 31  # close enough for email; exact calc is in pacing.py
    import calendar
    days_total   = calendar.monthrange(now.year, now.month)[1]
    on_pace_pct  = round(days_done / days_total * 100, 1)

    # ── Spend MTD (from ad_spend table) ──────────────────────────────────────
    spend_q = (
        sb.table("ad_spend")
        .select("channel, spend")
        .eq("client_id", client_id)
        .gte("date", month_start.date().isoformat())
        .execute()
    )
    spend_by_channel: dict[str, float] = {}
    for row in (spend_q.data or []):
        ch = row["channel"]
        spend_by_channel[ch] = spend_by_channel.get(ch, 0.0) + float(row["spend"])
    total_spend = sum(spend_by_channel.values())
    roas_mtd    = round(mtd_revenue / total_spend, 2) if total_spend > 0 else None

    # ── Visitors & conversion rate ────────────────────────────────────────────
    vis_q = (
        sb.table("visitors")
        .select("id", count="exact", head=True)
        .eq("client_id", client_id)
        .gte("first_seen_at", week_start.isoformat())
        .execute()
    )
    vis_7d    = vis_q.count or 0
    conv_rate = round(week_count / vis_7d * 100, 2) if vis_7d > 0 else None

    # ── Open alerts ───────────────────────────────────────────────────────────
    alerts_q = (
        sb.table("alerts")
        .select("severity, title")
        .eq("client_id", client_id)
        .is_("resolved_at", "null")
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    open_alerts   = alerts_q.data or []
    alert_critical = [a for a in open_alerts if a.get("severity") == "critical"]
    alert_warning  = [a for a in open_alerts if a.get("severity") == "warning"]

    # ── Build HTML ────────────────────────────────────────────────────────────
    def fmt_brl(val: float) -> str:
        return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def delta_badge(pct: Optional[float]) -> str:
        if pct is None:
            return ""
        color  = "#16a34a" if pct >= 0 else "#dc2626"
        symbol = "▲" if pct >= 0 else "▼"
        return f'<span style="color:{color};font-size:12px">{symbol} {abs(pct):.1f}%</span>'

    goal_bar = ""
    if pct_of_goal is not None:
        bar_w   = min(int(pct_of_goal), 100)
        bar_col = "#16a34a" if pct_of_goal >= on_pace_pct * 0.9 else "#f59e0b"
        goal_bar = f"""
        <div style="margin:8px 0">
          <div style="display:flex;justify-content:space-between;font-size:11px;color:#6b7280;margin-bottom:4px">
            <span>{pct_of_goal}% da meta</span><span>esperado: {on_pace_pct}%</span>
          </div>
          <div style="background:#e5e7eb;border-radius:4px;height:8px">
            <div style="background:{bar_col};width:{bar_w}%;height:8px;border-radius:4px"></div>
          </div>
        </div>"""

    daily_table_rows = "".join(
        f'<tr><td style="padding:4px 8px;color:#6b7280;font-size:12px">{d}</td>'
        f'<td style="padding:4px 8px;text-align:right;font-size:12px">{fmt_brl(v)}</td></tr>'
        for d, v in daily_rows[-7:]
    )

    alert_html = ""
    if open_alerts:
        items = ""
        for a in open_alerts[:5]:
            col = "#ef4444" if a.get("severity") == "critical" else "#f59e0b"
            items += f'<li style="margin:4px 0;color:#374151;font-size:13px"><span style="color:{col}">●</span> {a["title"]}</li>'
        alert_html = f"""
        <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:12px 16px;margin:16px 0">
          <p style="margin:0 0 8px;font-weight:600;color:#991b1b;font-size:13px">
            ⚠ {len(open_alerts)} alerta{'s' if len(open_alerts)>1 else ''} aberto{'s' if len(open_alerts)>1 else ''}
            ({len(alert_critical)} crítico{'s' if len(alert_critical)>1 else ''})
          </p>
          <ul style="margin:0;padding-left:16px">{items}</ul>
        </div>"""

    roas_row = ""
    if roas_mtd is not None:
        roas_target = float(roas_goal or 0)
        roas_col    = "#16a34a" if (roas_target == 0 or roas_mtd >= roas_target) else "#f59e0b"
        target_str  = f" / meta {roas_target:.1f}x" if roas_target > 0 else ""
        roas_row = f"""
        <tr>
          <td style="padding:10px 0;color:#6b7280;font-size:13px">ROAS MTD</td>
          <td style="padding:10px 0;text-align:right;font-size:13px;font-weight:600;color:{roas_col}">{roas_mtd:.2f}x{target_str}</td>
        </tr>"""

    spend_row = ""
    if total_spend > 0:
        spend_row = f"""
        <tr>
          <td style="padding:10px 0;color:#6b7280;font-size:13px">Investimento MTD</td>
          <td style="padding:10px 0;text-align:right;font-size:13px">{fmt_brl(total_spend)}</td>
        </tr>"""

    week_date_range = f"{week_start.strftime('%d/%m')} – {now.strftime('%d/%m')}"

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f9fafb;margin:0;padding:0">
<div style="max-width:600px;margin:0 auto;padding:24px 16px">

  <!-- Header -->
  <div style="background:#1e1b4b;border-radius:8px 8px 0 0;padding:20px 24px">
    <p style="margin:0;color:#a5b4fc;font-size:11px;letter-spacing:1px;text-transform:uppercase">Relatório Semanal</p>
    <h1 style="margin:4px 0 0;color:#fff;font-size:20px">{client_name}</h1>
    <p style="margin:4px 0 0;color:#8b9cf4;font-size:12px">{week_date_range} · gerado em {now.strftime('%d/%m/%Y %H:%M')} UTC</p>
  </div>

  <!-- Body -->
  <div style="background:#fff;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;padding:24px">

    {alert_html}

    <!-- MTD Revenue -->
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:16px;margin-bottom:16px">
      <p style="margin:0 0 4px;color:#6b7280;font-size:11px;text-transform:uppercase;letter-spacing:0.5px">Receita no mês</p>
      <p style="margin:0;font-size:28px;font-weight:700;color:#111827">{fmt_brl(mtd_revenue)}</p>
      {goal_bar}
    </div>

    <!-- Key metrics table -->
    <table style="width:100%;border-collapse:collapse;margin-bottom:16px">
      <tr style="border-bottom:1px solid #f3f4f6">
        <td style="padding:10px 0;color:#6b7280;font-size:13px">Pedidos MTD</td>
        <td style="padding:10px 0;text-align:right;font-size:13px;font-weight:600">{mtd_count}</td>
      </tr>
      <tr style="border-bottom:1px solid #f3f4f6">
        <td style="padding:10px 0;color:#6b7280;font-size:13px">Ticket médio MTD</td>
        <td style="padding:10px 0;text-align:right;font-size:13px">{fmt_brl(mtd_aov)}</td>
      </tr>
      <tr style="border-bottom:1px solid #f3f4f6">
        <td style="padding:10px 0;color:#6b7280;font-size:13px">Receita 7d</td>
        <td style="padding:10px 0;text-align:right;font-size:13px">{fmt_brl(week_revenue)} {delta_badge(week_delta_pct)}</td>
      </tr>
      {"" if conv_rate is None else f'<tr style="border-bottom:1px solid #f3f4f6"><td style="padding:10px 0;color:#6b7280;font-size:13px">Taxa de conversão 7d</td><td style="padding:10px 0;text-align:right;font-size:13px">{conv_rate:.2f}%</td></tr>'}
      {spend_row}
      {roas_row}
    </table>

    <!-- Daily trend -->
    {"" if not daily_table_rows else f'''
    <p style="margin:16px 0 8px;font-weight:600;color:#374151;font-size:13px">Receita diária — últimos 7 dias</p>
    <table style="width:100%;border-collapse:collapse">
      {daily_table_rows}
    </table>'''}

  </div>

  <p style="color:#9ca3af;font-size:11px;text-align:center;margin-top:16px">
    Ecommerce Tracking IA · <a href="https://app.noroia.com/clients/{pixel_id}/dashboard" style="color:#6366f1">Ver dashboard</a>
  </p>
</div>
</body></html>"""

    subject = f"📊 Semana {week_start.strftime('%d/%m')}–{now.strftime('%d/%m')}: {fmt_brl(week_revenue)} · {client_name}"
    email_service.send_email(to=to_email, subject=subject, html_body=html_body)
    logger.info("weekly report sent to %s for %s", to_email, pixel_id)


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
