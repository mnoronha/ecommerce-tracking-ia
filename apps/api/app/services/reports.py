"""
Client reports — weekly (objective) and monthly (complete).

Two cadences:
  • Weekly  — Mondays. Slim digest: 7d revenue vs prev week, essential KPIs,
              month goal progress, open alerts + one AI action.
  • Monthly — 1st of each month, over the previous CLOSED month. Complete:
              total revenue + MoM, positive highlights, per-channel detail
              (Meta Ads / Google Ads with spend, revenue, ROAS, CPA), top
              products, retention, and a deep AI analysis.

Negativity gate (monthly only): if the month was clearly bad, the report is
NOT sent to the client. Instead it goes to the agency inbox
(AGENCY_NOTIFY_EMAIL) with a "held for review" banner so a human decides.
"""

import calendar
import logging
from datetime import datetime, date, timedelta, timezone
from typing import Optional

from ..config import settings
from ..database import get_supabase
from . import resend as email_service
from . import notify as _notify
from . import report_builder, report_renderer


def _html_to_pdf(html: str) -> Optional[bytes]:
    """Convert HTML string to PDF bytes using WeasyPrint. Returns None on failure."""
    try:
        from weasyprint import HTML as WP_HTML
        return WP_HTML(string=html).write_pdf()
    except Exception as exc:
        logger.warning("reports: PDF generation failed (WeasyPrint): %s", exc)
        return None

logger = logging.getLogger(__name__)

# ── Negativity gate thresholds (tweak here) ─────────────────────────────────────
MOM_DROP_HOLD_PCT   = 25.0   # MoM revenue drop ≥ this (%) → hold
ROAS_HOLD_FLOOR     = 1.0    # ROAS below this (with meaningful spend) → hold
MIN_SPEND_FOR_ROAS  = 100.0  # only judge ROAS when spend exceeds this (R$)

_MONTH_PT = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

# utm_source (lowercased) → ad_spend channel key
_CHANNEL_FROM_SOURCE = {
    "facebook": "meta_ads", "fb": "meta_ads", "instagram": "meta_ads",
    "ig": "meta_ads", "meta": "meta_ads", "facebook_ads": "meta_ads",
    "instagram_ads": "meta_ads", "meta_ads": "meta_ads",
    "google": "google_ads", "google_ads": "google_ads", "googleads": "google_ads",
    "adwords": "google_ads", "gads": "google_ads", "youtube": "google_ads",
    "tiktok": "tiktok_ads", "tiktok_ads": "tiktok_ads",
}

_CHANNEL_LABEL = {
    "meta_ads": "Meta Ads", "google_ads": "Google Ads", "tiktok_ads": "TikTok Ads",
}


# ── Formatting helpers ──────────────────────────────────────────────────────────

def fmt_brl(val: float) -> str:
    return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def delta_badge(pct: Optional[float]) -> str:
    if pct is None:
        return ""
    color  = "#16a34a" if pct >= 0 else "#dc2626"
    symbol = "▲" if pct >= 0 else "▼"
    return f'<span style="color:{color};font-size:12px">{symbol} {abs(pct):.1f}%</span>'


# ── Shared query helpers ────────────────────────────────────────────────────────

def _money(sb, client_id: str, start: str, end: str) -> dict:
    """Paid revenue/orders/AOV + daily series for a window [start, end)."""
    q = (
        sb.table("orders")
        .select("total_price, created_at")
        .eq("client_id", client_id)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", start)
        .lt("created_at", end)
        .execute()
    )
    rows = q.data or []
    revenue = sum(float(o["total_price"]) for o in rows)
    count   = len(rows)
    daily: dict[str, float] = {}
    for o in rows:
        d = o["created_at"][:10]
        daily[d] = daily.get(d, 0.0) + float(o["total_price"])
    return {
        "revenue": round(revenue, 2),
        "orders":  count,
        "aov":     round(revenue / count, 2) if count else 0.0,
        "daily":   sorted(daily.items()),
    }


def _spend_by_channel(sb, client_id: str, start_date: str, end_date: str) -> dict:
    q = (
        sb.table("ad_spend")
        .select("channel, spend, impressions, clicks, conversions")
        .eq("client_id", client_id)
        .gte("date", start_date)
        .lte("date", end_date)
        .execute()
    )
    out: dict[str, dict] = {}
    for r in (q.data or []):
        ch = r["channel"]
        agg = out.setdefault(ch, {"spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0})
        agg["spend"]       += float(r.get("spend") or 0)
        agg["impressions"] += int(r.get("impressions") or 0)
        agg["clicks"]      += int(r.get("clicks") or 0)
        agg["conversions"] += float(r.get("conversions") or 0)
    return out


def _revenue_by_channel(sb, client_id: str, start: str, end: str) -> dict:
    """Attribute paid revenue/orders to an ad channel via utm_source."""
    q = (
        sb.table("orders")
        .select("utm_source, total_price")
        .eq("client_id", client_id)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", start)
        .lt("created_at", end)
        .execute()
    )
    out: dict[str, dict] = {}
    for o in (q.data or []):
        ch = _CHANNEL_FROM_SOURCE.get((o.get("utm_source") or "").lower())
        if not ch:
            continue
        agg = out.setdefault(ch, {"revenue": 0.0, "orders": 0})
        agg["revenue"] += float(o.get("total_price") or 0)
        agg["orders"]  += 1
    return out


def _channel_detail(sb, client_id: str, start: str, end: str, start_date: str, end_date: str) -> list[dict]:
    """Merge spend + attributed revenue per ad channel → ROAS, CPA."""
    spend = _spend_by_channel(sb, client_id, start_date, end_date)
    rev   = _revenue_by_channel(sb, client_id, start, end)
    rows: list[dict] = []
    for ch in sorted(set(spend) | set(rev)):
        s = spend.get(ch, {})
        r = rev.get(ch, {})
        spd = round(float(s.get("spend") or 0), 2)
        rvn = round(float(r.get("revenue") or 0), 2)
        ords = int(r.get("orders") or 0)
        rows.append({
            "channel":     ch,
            "label":       _CHANNEL_LABEL.get(ch, ch),
            "spend":       spd,
            "revenue":     rvn,
            "orders":      ords,
            "roas":        round(rvn / spd, 2) if spd > 0 else None,
            "cpa":         round(spd / ords, 2) if ords > 0 else None,
            "impressions": int(s.get("impressions") or 0),
            "clicks":      int(s.get("clicks") or 0),
        })
    # Highest spend first
    rows.sort(key=lambda x: x["spend"], reverse=True)
    return rows


def _top_products(sb, client_id: str, start: str, end: str, limit: int = 8) -> list[dict]:
    q = (
        sb.table("orders")
        .select("order_items(name, quantity, line_total)")
        .eq("client_id", client_id)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", start)
        .lt("created_at", end)
        .execute()
    )
    prod: dict[str, dict] = {}
    for o in (q.data or []):
        for it in (o.get("order_items") or []):
            name = it.get("name") or "Produto sem nome"
            agg  = prod.setdefault(name, {"name": name, "qty": 0, "revenue": 0.0})
            agg["qty"]     += int(it.get("quantity") or 1)
            agg["revenue"] += float(it.get("line_total") or 0)
    rows = sorted(prod.values(), key=lambda p: p["revenue"], reverse=True)[:limit]
    for p in rows:
        p["revenue"] = round(p["revenue"], 2)
    return rows


def _new_vs_returning(sb, client_id: str, start: str, end: str) -> dict:
    q = (
        sb.table("orders")
        .select("is_first_purchase")
        .eq("client_id", client_id)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", start)
        .lt("created_at", end)
        .execute()
    )
    rows = q.data or []
    new = sum(1 for o in rows if o.get("is_first_purchase"))
    ret = sum(1 for o in rows if o.get("is_first_purchase") is False)
    total = new + ret
    return {
        "new":             new,
        "returning":       ret,
        "repeat_rate_pct": round(ret / total * 100, 1) if total else 0.0,
    }


def _goal_for_month(sb, client_id: str, month_start: date) -> tuple[float, Optional[float]]:
    """Returns (revenue_goal, roas_goal) for the given month."""
    revenue_goal: Optional[float] = None
    roas_goal:    Optional[float] = None
    g = (
        sb.table("goals")
        .select("revenue_goal, roas_goal")
        .eq("client_id", client_id)
        .eq("month", month_start.isoformat())
        .limit(1)
        .execute()
    )
    if g.data:
        revenue_goal = g.data[0].get("revenue_goal")
        roas_goal    = g.data[0].get("roas_goal")
    if revenue_goal is None or roas_goal is None:
        c = sb.table("clients").select("monthly_revenue_goal, target_roas").eq("id", client_id).limit(1).execute()
        if c.data:
            if revenue_goal is None:
                revenue_goal = c.data[0].get("monthly_revenue_goal")
            if roas_goal is None:
                roas_goal = c.data[0].get("target_roas")
    return float(revenue_goal or 0), roas_goal


def _open_alerts(sb, client_id: str) -> list[dict]:
    q = (
        sb.table("alerts")
        .select("severity, title")
        .eq("client_id", client_id)
        .is_("resolved_at", "null")
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    return q.data or []


def _visitors_conv(sb, client_id: str, start: str, orders: int) -> Optional[float]:
    v = (
        sb.table("visitors")
        .select("id", count="exact", head=True)
        .eq("client_id", client_id)
        .gte("first_seen_at", start)
        .execute()
    )
    vis = v.count or 0
    return round(orders / vis * 100, 2) if vis > 0 else None


def _latest_ai(sb, client_id: str, insight_type: str) -> Optional[dict]:
    try:
        q = (
            sb.table("ai_insights")
            .select("title, content, data")
            .eq("client_id", client_id)
            .eq("type", insight_type)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if q.data:
            return q.data[0]
    except Exception:
        pass
    return None


def _client_name(sb, client_id: str, pixel_id: str) -> str:
    try:
        row = sb.table("clients").select("name").eq("id", client_id).limit(1).execute()
        if row.data and row.data[0].get("name"):
            return row.data[0]["name"]
    except Exception:
        pass
    return pixel_id


def _client_logo(sb, client_id: str) -> Optional[str]:
    try:
        row = sb.table("clients").select("logo_url").eq("id", client_id).limit(1).execute()
        if row.data:
            return row.data[0].get("logo_url") or None
    except Exception:
        pass
    return None


def _header_html(client_name: str, subtitle: str,
                 client_logo: Optional[str] = None,
                 wide: bool = False) -> str:
    """
    Bloco de cabeçalho reutilizável para ambos os relatórios.
    Inclui logo do cliente (se houver) + logo da agência.
    """
    agency_logo = settings.AGENCY_LOGO_URL
    agency_name = settings.AGENCY_NAME or "Noroia"

    # Logo do cliente
    client_logo_html = ""
    if client_logo:
        client_logo_html = f'<img src="{client_logo}" alt="{client_name}" style="height:36px;max-width:160px;object-fit:contain;margin-bottom:8px;display:block">'

    # Logo da agência (canto superior direito)
    agency_logo_html = ""
    if agency_logo:
        agency_logo_html = f'<img src="{agency_logo}" alt="{agency_name}" style="height:22px;object-fit:contain;opacity:0.7">'
    else:
        agency_logo_html = f'<span style="color:#a5b4fc;font-size:11px;font-weight:600;letter-spacing:0.5px">{agency_name}</span>'

    max_w = "640px" if wide else "560px"
    return f"""
    <div style="background:#1e1b4b;border-radius:8px 8px 0 0;padding:20px 24px">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:4px">
        <div>
          {client_logo_html}
          <h1 style="margin:0;color:#fff;font-size:{'22px' if wide else '20px'};font-weight:700">{client_name}</h1>
          <p style="margin:4px 0 0;color:#8b9cf4;font-size:12px">{subtitle}</p>
        </div>
        <div style="text-align:right;padding-top:2px">
          {agency_logo_html}
        </div>
      </div>
    </div>"""


# ════════════════════════════════════════════════════════════════════════════════
# WEEKLY — objective digest
# ════════════════════════════════════════════════════════════════════════════════

def send_weekly_reports() -> None:
    """Scheduler entry point (Mondays). Slim weekly digest to every client."""
    try:
        clients = (
            get_supabase()
            .table("clients")
            .select("id, pixel_id, name, alert_email, alert_emails, whatsapp_group_jid, client_type, weekly_report_enabled")
            .eq("is_active", True)
            .eq("weekly_report_enabled", True)
            .execute()
        )
        for c in (clients.data or []):
            recipients = _notify._all_client_emails(c)
            if not recipients:
                continue
            if (c.get("client_type") or "ecommerce") == "leads":
                logger.info("weekly report skipped for %s — template de Leads ainda não implementado", c.get("pixel_id"))
                continue
            try:
                _send_weekly(c["id"], c["pixel_id"], c.get("name") or c["pixel_id"], recipients, c)
            except Exception as exc:
                logger.error("weekly report failed for %s: %s", c.get("pixel_id"), exc)
    except Exception as exc:
        logger.error("send_weekly_reports: %s", exc)


def _budget_for_month(sb, client_id: str, month_start: date,
                      revenue_goal: float = 0, roas_goal: Optional[float] = None) -> tuple[float, bool]:
    """Orçamento de mídia do mês. Usa a tabela `budgets` (soma dos canais) se
    houver; senão deriva um alvo IMPLÍCITO da meta de receita ÷ meta de ROAS
    (para bater R$X de receita a Yx de ROAS, investe-se ~X/Y). Retorna
    (valor, implícito)."""
    try:
        rows = (
            sb.table("budgets").select("amount")
            .eq("client_id", client_id).eq("month", month_start.isoformat())
            .execute()
        ).data or []
        explicit = sum(float(r.get("amount") or 0) for r in rows)
    except Exception:
        explicit = 0.0
    if explicit > 0:
        return round(explicit, 2), False
    if revenue_goal and roas_goal and float(roas_goal) > 0:
        return round(float(revenue_goal) / float(roas_goal), 2), True
    return 0.0, False


def _build_weekly(sb, client_id: str, now: datetime) -> dict:
    week_start      = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    prev_week_start = (now - timedelta(days=14)).replace(hour=0, minute=0, second=0, microsecond=0)
    month_start     = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    week = _money(sb, client_id, week_start.isoformat(), now.isoformat())
    prev = _money(sb, client_id, prev_week_start.isoformat(), week_start.isoformat())
    mtd  = _money(sb, client_id, month_start.isoformat(), now.isoformat())

    def _pct_delta(cur, prv):
        return round((cur - prv) / prv * 100, 1) if prv > 0 else None

    week_delta = _pct_delta(week["revenue"], prev["revenue"])

    # ── Investimento (semana vs anterior) ────────────────────────────────────
    spend_now  = _spend_by_channel(sb, client_id, week_start.date().isoformat(), now.date().isoformat())
    spend_prev = _spend_by_channel(sb, client_id, prev_week_start.date().isoformat(), week_start.date().isoformat())
    total_spend = round(sum(s["spend"] for s in spend_now.values()), 2)
    prev_spend  = round(sum(s["spend"] for s in spend_prev.values()), 2)
    spend_delta = _pct_delta(total_spend, prev_spend)

    # ── ROAS por canal: semana atual vs anterior ─────────────────────────────
    ch_now = _channel_detail(
        sb, client_id, week_start.isoformat(), now.isoformat(),
        week_start.date().isoformat(), now.date().isoformat())
    ch_prev = _channel_detail(
        sb, client_id, prev_week_start.isoformat(), week_start.isoformat(),
        prev_week_start.date().isoformat(), week_start.date().isoformat())
    prev_roas_by_ch = {c["channel"]: c["roas"] for c in ch_prev}
    for c in ch_now:
        c["roas_prev"] = prev_roas_by_ch.get(c["channel"])
    channels = [c for c in ch_now if c["spend"] > 0 or c["revenue"] > 0]

    # ROAS pago = receita atribuída aos canais ÷ investimento (comparável à meta
    # de ROAS). Evita o "ROAS blended" inflado por receita orgânica/direta.
    paid_rev    = round(sum(c["revenue"] for c in channels), 2)
    paid_orders = sum(c["orders"] for c in channels)
    roas = round(paid_rev / total_spend, 2) if total_spend > 0 else None
    cpa  = round(total_spend / paid_orders, 2) if (total_spend > 0 and paid_orders) else None

    # ── Metas (MTD) ──────────────────────────────────────────────────────────
    goal, roas_goal = _goal_for_month(sb, client_id, month_start.date())
    pct_of_goal = round(mtd["revenue"] / goal * 100, 1) if goal > 0 else None
    days_total  = calendar.monthrange(now.year, now.month)[1]
    on_pace_pct = round(now.day / days_total * 100, 1)
    # Projeção só a partir do dia 5 — extrapolar 2-3 dias é ruído.
    proj_revenue = round(mtd["revenue"] / now.day * days_total, 2) if now.day >= 5 else None

    # ── Ritmo da meta de investimento ────────────────────────────────────────
    mtd_spend = round(sum(
        s["spend"] for s in
        _spend_by_channel(sb, client_id, month_start.date().isoformat(), now.date().isoformat()).values()
    ), 2)
    budget, budget_implied = _budget_for_month(sb, client_id, month_start.date(), goal, roas_goal)
    pct_of_budget = round(mtd_spend / budget * 100, 1) if budget > 0 else None

    conv = _visitors_conv(sb, client_id, week_start.isoformat(), week["orders"])

    return {
        "week_start": week_start, "now": now,
        "week": week, "week_delta": week_delta,
        "spend": total_spend, "spend_delta": spend_delta,
        "roas": roas, "roas_goal": roas_goal, "cpa": cpa, "conv": conv,
        "channels": channels,
        "mtd": mtd, "goal": goal, "pct_of_goal": pct_of_goal,
        "on_pace_pct": on_pace_pct, "proj_revenue": proj_revenue,
        "mtd_spend": mtd_spend, "budget": budget, "budget_implied": budget_implied,
        "pct_of_budget": pct_of_budget,
        "top_products": _top_products(sb, client_id, week_start.isoformat(), now.isoformat(), limit=5),
        "nvr": _new_vs_returning(sb, client_id, week_start.isoformat(), now.isoformat()),
        "alerts": _open_alerts(sb, client_id),
        "ai": _latest_ai(sb, client_id, "weekly_report") or _latest_ai(sb, client_id, "recommendation"),
    }


def _send_weekly(client_id: str, pixel_id: str, client_name: str,
                 recipients: list[str] | str, client: Optional[dict] = None) -> None:
    sb        = get_supabase()
    now       = datetime.now(timezone.utc)
    m         = _build_weekly(sb, client_id, now)
    logo      = (client or {}).get("logo_url") or _client_logo(sb, client_id)
    html      = _render_weekly_html(pixel_id, client_name, m, client_logo=logo)
    subject   = f"📊 Semana {m['week_start'].strftime('%d/%m')}–{now.strftime('%d/%m')}: {fmt_brl(m['week']['revenue'])} · {client_name}"

    to_list = [recipients] if isinstance(recipients, str) else recipients
    for addr in to_list:
        email_service.send_email(to=addr, subject=subject, html_body=html)
    logger.info("weekly report sent to %s for %s", to_list, pixel_id)

    # WhatsApp group — resumo compacto
    if client:
        group_jid = (client.get("whatsapp_group_jid") or "").strip()
        if group_jid:
            from . import whatsapp as _wa
            wa_text = (
                f"📊 *Resumo Semanal — {client_name}*\n\n"
                f"Receita: *{fmt_brl(m['week']['revenue'])}*"
                + (f" {'+' if (m['week_delta'] or 0) >= 0 else ''}{m['week_delta']:.1f}% vs sem. anterior" if m['week_delta'] is not None else "")
                + f"\nPedidos: {m['week']['orders']} · Ticket médio: {fmt_brl(m['week']['aov'])}"
                + (f"\nROAS: {m['roas']:.2f}x" if m['roas'] else "")
                + (f"\nMeta do mês: {m['pct_of_goal']:.0f}%" if m['pct_of_goal'] is not None else "")
            )
            _wa.send_to_group(group_jid, wa_text)


def _render_weekly_html(pixel_id: str, client_name: str, m: dict,
                        client_logo: Optional[str] = None) -> str:
    now        = m["now"]
    week_range = f"{m['week_start'].strftime('%d/%m')} – {now.strftime('%d/%m')}"

    # ── Ritmo das metas (faturamento + investimento) ─────────────────────────
    def _pace_bar(title, value_html, pct, expected, target_html, good_high):
        if pct is None:
            return ""
        bar_w = min(int(pct), 100)
        if good_high:
            col    = "#16a34a" if pct >= expected * 0.9 else "#f59e0b"
            status = "no ritmo" if pct >= expected * 0.9 else "abaixo do ritmo"
        else:
            diff = pct - expected
            col, status = ("#16a34a", "no ritmo") if abs(diff) <= 12 else \
                          ("#f59e0b", "acima do ritmo") if diff > 12 else ("#3b82f6", "abaixo do ritmo")
        marker = min(int(expected), 100)
        return f"""
        <div style="margin-top:14px">
          <div style="display:flex;justify-content:space-between;align-items:baseline">
            <p style="margin:0;font-weight:600;color:#374151;font-size:13px">{title}</p>
            <span style="font-size:11px;font-weight:600;color:{col}">{status}</span>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:11px;color:#6b7280;margin:4px 0">
            <span>{value_html}</span><span>{target_html}</span>
          </div>
          <div style="position:relative;background:#e5e7eb;border-radius:4px;height:8px">
            <div style="background:{col};width:{bar_w}%;height:8px;border-radius:4px"></div>
            <div style="position:absolute;top:-2px;left:{marker}%;width:2px;height:12px;background:#9ca3af"></div>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:10px;color:#9ca3af;margin-top:3px">
            <span>{pct:.0f}% realizado</span><span>esperado p/ o dia {expected:.0f}%</span>
          </div>
        </div>"""

    rev_target = f"meta {fmt_brl(m['goal'])}" + (f" · projeção {fmt_brl(m['proj_revenue'])}" if m.get("proj_revenue") else "")
    goal_bar = _pace_bar("Meta de faturamento (mês)", f"realizado {fmt_brl(m['mtd']['revenue'])}",
                         m["pct_of_goal"], m["on_pace_pct"], rev_target, good_high=True)
    inv_target = (("orçamento " if not m["budget_implied"] else "alvo ") + fmt_brl(m["budget"])
                  + (" (meta÷ROAS)" if m["budget_implied"] else ""))
    invest_bar = _pace_bar("Meta de investimento (mês)", f"investido {fmt_brl(m['mtd_spend'])}",
                           m["pct_of_budget"], m["on_pace_pct"], inv_target, good_high=False)

    # ── ROAS por canal (semana atual vs anterior) ────────────────────────────
    channel_html = ""
    if m["channels"]:
        rows = ""
        for c in m["channels"]:
            roas_now = f'{c["roas"]:.2f}x' if c["roas"] is not None else "—"
            rp = c.get("roas_prev")
            if c["roas"] is not None and rp:
                d = (c["roas"] - rp) / rp * 100
                ar, dc = ("▲", "#16a34a") if d >= 0 else ("▼", "#ef4444")
                cmp_cell = f'<span style="color:{dc};font-weight:600">{ar} {abs(d):.0f}%</span> <span style="color:#9ca3af">(era {rp:.2f}x)</span>'
            elif rp:
                cmp_cell = f'<span style="color:#9ca3af">era {rp:.2f}x</span>'
            else:
                cmp_cell = '<span style="color:#9ca3af">—</span>'
            rows += f"""
            <tr style="border-bottom:1px solid #f3f4f6">
              <td style="padding:8px 0;font-size:12px;color:#374151;font-weight:600">{c['label']}</td>
              <td style="padding:8px 0;text-align:right;font-size:12px;color:#6b7280">{fmt_brl(c['spend'])}</td>
              <td style="padding:8px 0;text-align:right;font-size:12px;color:#111827">{fmt_brl(c['revenue'])}</td>
              <td style="padding:8px 0;text-align:right;font-size:12px;font-weight:600;color:#111827">{roas_now}</td>
              <td style="padding:8px 0;text-align:right;font-size:11px">{cmp_cell}</td>
            </tr>"""
        channel_html = f"""
        <div style="margin-top:20px">
          <p style="margin:0 0 8px;font-weight:600;color:#374151;font-size:13px">ROAS por canal · vs semana anterior</p>
          <table style="width:100%;border-collapse:collapse">
            <tr style="border-bottom:2px solid #e5e7eb">
              <td style="padding:6px 0;font-size:10px;text-transform:uppercase;color:#9ca3af;letter-spacing:0.4px">Canal</td>
              <td style="padding:6px 0;text-align:right;font-size:10px;text-transform:uppercase;color:#9ca3af">Invest.</td>
              <td style="padding:6px 0;text-align:right;font-size:10px;text-transform:uppercase;color:#9ca3af">Receita</td>
              <td style="padding:6px 0;text-align:right;font-size:10px;text-transform:uppercase;color:#9ca3af">ROAS</td>
              <td style="padding:6px 0;text-align:right;font-size:10px;text-transform:uppercase;color:#9ca3af">vs sem.</td>
            </tr>
            {rows}
          </table>
        </div>"""

    # ── Top produtos da semana ───────────────────────────────────────────────
    products_html = ""
    if m["top_products"]:
        items = "".join(
            f'<tr style="border-bottom:1px solid #f3f4f6">'
            f'<td style="padding:7px 0;font-size:12px;color:#374151">{i+1}. {p["name"][:48]}</td>'
            f'<td style="padding:7px 0;text-align:right;font-size:12px;color:#6b7280">{p["qty"]} un.</td>'
            f'<td style="padding:7px 0;text-align:right;font-size:12px;font-weight:600;color:#111827">{fmt_brl(p["revenue"])}</td></tr>'
            for i, p in enumerate(m["top_products"])
        )
        products_html = f"""
        <div style="margin-top:20px">
          <p style="margin:0 0 8px;font-weight:600;color:#374151;font-size:13px">Produtos mais vendidos na semana</p>
          <table style="width:100%;border-collapse:collapse">{items}</table>
        </div>"""

    # ── Novos vs recorrentes ─────────────────────────────────────────────────
    nvr = m["nvr"]
    nvr_html = ""
    if (nvr["new"] + nvr["returning"]) > 0:
        nvr_html = f"""
        <div style="margin-top:16px;display:flex;gap:10px">
          <div style="flex:1;background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:10px;text-align:center">
            <p style="margin:0;font-size:18px;font-weight:700;color:#111827">{nvr['new']}</p>
            <p style="margin:2px 0 0;font-size:11px;color:#6b7280">novos clientes</p>
          </div>
          <div style="flex:1;background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:10px;text-align:center">
            <p style="margin:0;font-size:18px;font-weight:700;color:#111827">{nvr['returning']}</p>
            <p style="margin:2px 0 0;font-size:11px;color:#6b7280">recorrentes ({nvr['repeat_rate_pct']:.0f}%)</p>
          </div>
        </div>"""

    # Essential KPIs
    roas_cell = "—"
    if m["roas"] is not None:
        tgt = float(m["roas_goal"] or 0)
        col = "#16a34a" if (tgt == 0 or m["roas"] >= tgt) else "#f59e0b"
        roas_cell = f'<span style="color:{col};font-weight:600">{m["roas"]:.2f}x</span>' + (
            f' <span style="color:#9ca3af;font-weight:400">/ meta {tgt:.1f}x</span>' if tgt else '')
    conv_cell = f'{m["conv"]:.2f}%' if m["conv"] is not None else "—"
    cpa_cell  = fmt_brl(m["cpa"]) if m["cpa"] is not None else "—"
    roas_big  = f'{m["roas"]:.2f}x' if m["roas"] is not None else "—"
    roas_col  = "#16a34a" if (m["roas"] is not None and (not m["roas_goal"] or m["roas"] >= float(m["roas_goal"]))) else "#f59e0b"

    # Open alerts (compact)
    alerts_html = ""
    if m["alerts"]:
        crit = sum(1 for a in m["alerts"] if a.get("severity") == "critical")
        items = "".join(
            f'<li style="margin:3px 0;color:#374151;font-size:12px">'
            f'<span style="color:{"#ef4444" if a.get("severity")=="critical" else "#f59e0b"}">●</span> {a["title"]}</li>'
            for a in m["alerts"][:3]
        )
        alerts_html = f"""
        <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:12px 16px;margin-top:16px">
          <p style="margin:0 0 6px;font-weight:600;color:#991b1b;font-size:13px">
            ⚠ {len(m['alerts'])} alerta{'s' if len(m['alerts'])>1 else ''} aberto{'s' if len(m['alerts'])>1 else ''}{f' ({crit} crítico{"s" if crit>1 else ""})' if crit else ''}
          </p>
          <ul style="margin:0;padding-left:16px">{items}</ul>
        </div>"""

    # One AI action
    ai_html = ""
    if m["ai"]:
        rec = (m["ai"].get("data") or {}).get("recommendation") or ""
        line = rec or (m["ai"].get("content") or "")[:240]
        if line:
            ai_html = f"""
        <div style="background:#f5f3ff;border:1px solid #ddd6fe;border-radius:6px;padding:14px 16px;margin-top:16px">
          <p style="margin:0 0 4px;font-weight:600;color:#5b21b6;font-size:12px">✦ Ação da semana</p>
          <p style="margin:0;color:#374151;font-size:13px;line-height:1.5">{line}</p>
        </div>"""

    dashboard_url = f"{settings.DASHBOARD_URL}/clients/{pixel_id}/dashboard"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f9fafb;margin:0;padding:0">
<div style="max-width:560px;margin:0 auto;padding:24px 16px">

  {_header_html(client_name, f"Resumo Semanal · {week_range}", client_logo=client_logo)}

  <div style="background:#fff;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;padding:24px">

    <!-- Hero: weekly revenue vs prev week -->
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:16px;text-align:center">
      <p style="margin:0 0 4px;color:#6b7280;font-size:11px;text-transform:uppercase;letter-spacing:0.5px">Receita na semana</p>
      <p style="margin:0;font-size:30px;font-weight:700;color:#111827">{fmt_brl(m['week']['revenue'])}</p>
      <p style="margin:6px 0 0;font-size:13px">vs. semana anterior {delta_badge(m['week_delta'])}</p>
    </div>

    <!-- Investimento + ROAS -->
    <div style="display:flex;gap:10px;margin-top:10px">
      <div style="flex:1;background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:12px;text-align:center">
        <p style="margin:0 0 2px;color:#6b7280;font-size:10px;text-transform:uppercase;letter-spacing:0.4px">Investido na semana</p>
        <p style="margin:0;font-size:18px;font-weight:700;color:#111827">{fmt_brl(m['spend'])}</p>
        <p style="margin:3px 0 0;font-size:11px">vs ant. {delta_badge(m['spend_delta'])}</p>
      </div>
      <div style="flex:1;background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:12px;text-align:center">
        <p style="margin:0 0 2px;color:#6b7280;font-size:10px;text-transform:uppercase;letter-spacing:0.4px">ROAS geral</p>
        <p style="margin:0;font-size:18px;font-weight:700;color:{roas_col}">{roas_big}</p>
        <p style="margin:3px 0 0;font-size:11px;color:#9ca3af">{('meta ' + format(float(m['roas_goal']), '.1f') + 'x') if m['roas_goal'] else 'receita ÷ invest.'}</p>
      </div>
    </div>

    <!-- Essential KPIs -->
    <table style="width:100%;border-collapse:collapse;margin-top:16px">
      <tr style="border-bottom:1px solid #f3f4f6">
        <td style="padding:9px 0;color:#6b7280;font-size:13px">Pedidos</td>
        <td style="padding:9px 0;text-align:right;font-size:13px;font-weight:600">{m['week']['orders']}</td>
      </tr>
      <tr style="border-bottom:1px solid #f3f4f6">
        <td style="padding:9px 0;color:#6b7280;font-size:13px">Ticket médio</td>
        <td style="padding:9px 0;text-align:right;font-size:13px">{fmt_brl(m['week']['aov'])}</td>
      </tr>
      <tr style="border-bottom:1px solid #f3f4f6">
        <td style="padding:9px 0;color:#6b7280;font-size:13px">CPA (custo por pedido)</td>
        <td style="padding:9px 0;text-align:right;font-size:13px">{cpa_cell}</td>
      </tr>
      <tr>
        <td style="padding:9px 0;color:#6b7280;font-size:13px">Taxa de conversão</td>
        <td style="padding:9px 0;text-align:right;font-size:13px">{conv_cell}</td>
      </tr>
    </table>

    {channel_html}

    <!-- Ritmo das metas -->
    <div style="margin-top:20px;background:#fafafa;border:1px solid #f0f0f0;border-radius:6px;padding:4px 16px 16px">
      {goal_bar}
      {invest_bar}
    </div>

    {products_html}
    {nvr_html}
    {alerts_html}
    {ai_html}

  </div>

  <p style="color:#9ca3af;font-size:11px;text-align:center;margin-top:16px">
    {settings.AGENCY_NAME or 'Ecommerce Tracking IA'} ·
    <a href="{dashboard_url}" style="color:#6366f1">Ver dashboard</a>
  </p>
</div>
</body></html>"""


# ════════════════════════════════════════════════════════════════════════════════
# MONTHLY — complete report + negativity gate
# ════════════════════════════════════════════════════════════════════════════════

def _prev_month_window(now: datetime) -> tuple[datetime, datetime, datetime]:
    """Returns (month_start, month_end, prev_month_start) for the last CLOSED month."""
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_end        = this_month_start                       # exclusive end of reported month
    month_start      = (this_month_start - timedelta(days=1)).replace(day=1)
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
    return month_start, month_end, prev_month_start


def _build_monthly(sb, client_id: str, now: datetime) -> dict:
    month_start, month_end, prev_month_start = _prev_month_window(now)
    s, e   = month_start.isoformat(), month_end.isoformat()
    ps, pe = prev_month_start.isoformat(), month_start.isoformat()

    cur  = _money(sb, client_id, s, e)
    prev = _money(sb, client_id, ps, pe)
    mom_delta = (
        round((cur["revenue"] - prev["revenue"]) / prev["revenue"] * 100, 1)
        if prev["revenue"] > 0 else None
    )

    goal, roas_goal = _goal_for_month(sb, client_id, month_start.date())
    pct_of_goal = round(cur["revenue"] / goal * 100, 1) if goal > 0 else None

    channels = _channel_detail(
        sb, client_id, s, e,
        month_start.date().isoformat(),
        (month_end - timedelta(days=1)).date().isoformat(),
    )
    total_spend = round(sum(c["spend"] for c in channels), 2)
    roas        = round(cur["revenue"] / total_spend, 2) if total_spend > 0 else None

    conv = _visitors_conv(sb, client_id, s, cur["orders"])

    m = {
        "month_label":  f"{_MONTH_PT[month_start.month]} {month_start.year}",
        "month_start":  month_start, "month_end": month_end,
        "revenue":      cur["revenue"], "orders": cur["orders"], "aov": cur["aov"],
        "daily":        cur["daily"],
        "prev_revenue": prev["revenue"], "mom_delta": mom_delta,
        "goal":         goal, "pct_of_goal": pct_of_goal,
        "roas_goal":    roas_goal, "roas": roas, "total_spend": total_spend,
        "conv":         conv,
        "channels":     channels,
        "top_products": _top_products(sb, client_id, s, e),
        "retention":    _new_vs_returning(sb, client_id, s, e),
        "alerts":       _open_alerts(sb, client_id),
        "ai":           _latest_ai(sb, client_id, "monthly_report"),
    }
    m["highlights"] = _positive_highlights(m)
    m["health"]     = _monthly_health(m)
    return m


def _positive_highlights(m: dict) -> list[str]:
    """Always surface what went WELL — even in a weak month."""
    out: list[str] = []
    if m["mom_delta"] is not None and m["mom_delta"] > 0:
        out.append(f"Receita cresceu {m['mom_delta']:.1f}% vs. o mês anterior.")
    if m["pct_of_goal"] is not None and m["pct_of_goal"] >= 100:
        out.append(f"Meta do mês batida — {m['pct_of_goal']:.0f}% do objetivo.")
    best = max((c for c in m["channels"] if c["roas"] is not None), key=lambda c: c["roas"], default=None)
    if best and best["roas"] >= 1:
        out.append(f"{best['label']} foi o canal mais eficiente: ROAS {best['roas']:.2f}x.")
    if m["top_products"]:
        tp = m["top_products"][0]
        out.append(f"Produto destaque: {tp['name']} ({fmt_brl(tp['revenue'])}).")
    if m["retention"]["returning"] > 0:
        out.append(f"{m['retention']['returning']} pedidos vieram de clientes recorrentes ({m['retention']['repeat_rate_pct']:.0f}% da base).")
    if not out:
        out.append(f"Faturamento de {fmt_brl(m['revenue'])} em {m['orders']} pedidos no mês.")
    return out[:4]


def _monthly_health(m: dict) -> dict:
    """Decide whether the month is too negative to auto-send to the client."""
    reasons: list[str] = []
    if m["revenue"] <= 0:
        reasons.append("Faturamento do mês foi zero.")
    if m["mom_delta"] is not None and m["mom_delta"] <= -MOM_DROP_HOLD_PCT:
        reasons.append(f"Receita caiu {abs(m['mom_delta']):.1f}% vs. o mês anterior.")
    if m["total_spend"] > MIN_SPEND_FOR_ROAS and (m["roas"] is None or m["roas"] < ROAS_HOLD_FLOOR):
        roas_str = f"{m['roas']:.2f}x" if m["roas"] is not None else "0"
        reasons.append(f"ROAS do mês ({roas_str}) abaixo de {ROAS_HOLD_FLOOR:.1f}x com {fmt_brl(m['total_spend'])} investidos.")
    return {"negative": bool(reasons), "reasons": reasons}


def send_monthly_reports() -> None:
    """Scheduler entry point (1st of month). Complete report over last closed month."""
    try:
        clients = (
            get_supabase()
            .table("clients")
            .select("id, pixel_id, name, alert_email, alert_emails, whatsapp_group_jid, logo_url, client_type, monthly_report_enabled")
            .eq("is_active", True)
            .eq("monthly_report_enabled", True)
            .execute()
        )
        for c in (clients.data or []):
            recipients = _notify._all_client_emails(c)
            if not recipients:
                continue
            if (c.get("client_type") or "ecommerce") == "leads":
                logger.info("monthly report skipped for %s — template de Leads ainda não implementado", c.get("pixel_id"))
                continue
            try:
                _ensure_monthly_ai(c["id"], c["pixel_id"])
                _send_monthly(c["id"], c["pixel_id"], c.get("name") or c["pixel_id"], recipients, client=c)
            except Exception as exc:
                logger.error("monthly report failed for %s: %s", c.get("pixel_id"), exc)
    except Exception as exc:
        logger.error("send_monthly_reports: %s", exc)


def _ensure_monthly_ai(client_id: str, pixel_id: str) -> None:
    """Generate the monthly AI insight if none exists in the last 24h."""
    try:
        from . import ai_analyst
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        recent = (
            get_supabase().table("ai_insights").select("id", count="exact", head=True)
            .eq("client_id", client_id).eq("type", "monthly_report")
            .gte("created_at", cutoff).execute()
        )
        if not (recent.count and recent.count > 0):
            ai_analyst.generate_monthly_insights(client_id)
    except Exception as exc:
        logger.warning("_ensure_monthly_ai failed for %s: %s", pixel_id, exc)


def _send_monthly(client_id: str, pixel_id: str, client_name: str,
                  recipients: list[str] | str, force: bool = False,
                  client: Optional[dict] = None) -> dict:
    """
    Build and send the monthly report. If the month is too negative and force is
    False, hold the client send and notify the agency instead.
    Returns {"sent_to", "held", "reasons"}.
    """
    sb  = get_supabase()
    now = datetime.now(timezone.utc)
    m   = _build_monthly(sb, client_id, now)
    to_list = [recipients] if isinstance(recipients, str) else recipients

    logo  = (client or {}).get("logo_url") or _client_logo(sb, client_id)

    held = m["health"]["negative"] and not force
    if held:
        agency = settings.AGENCY_NOTIFY_EMAIL
        if not agency:
            logger.warning("monthly report HELD for %s but AGENCY_NOTIFY_EMAIL unset — not sent", pixel_id)
            return {"sent_to": None, "held": True, "reasons": m["health"]["reasons"]}
        html    = _render_monthly_html(pixel_id, client_name, m,
                                       held_for=", ".join(to_list), client_logo=logo)
        subject = f"⚠️ [REVISAR] Relatório mensal retido — {client_name} · {m['month_label']}"
        email_service.send_email(to=agency, subject=subject, html_body=html)
        logger.info("monthly report HELD for %s → agency %s (%s)", pixel_id, agency, "; ".join(m["health"]["reasons"]))
        return {"sent_to": agency, "held": True, "reasons": m["health"]["reasons"]}

    # ── Build rich PDF via new template system ───────────────────────────
    pdf: Optional[bytes] = None
    try:
        now_dt  = datetime.now(timezone.utc)
        rb_year  = now_dt.year if now_dt.month > 1 else now_dt.year - 1
        rb_month = now_dt.month - 1 if now_dt.month > 1 else 12
        ctx = report_builder.build_monthly_context(
            client_id=client_id,
            client=client or {},
            year=rb_year,
            month=rb_month,
        )
        pdf = report_renderer.render_to_pdf(ctx)
        logger.info("monthly report: new template PDF generated (%d bytes)", len(pdf) if pdf else 0)
    except Exception as exc:
        logger.warning("monthly report: new template failed (%s) — falling back to legacy HTML", exc)

    # ── Fallback: legacy HTML template ───────────────────────────────────
    if not pdf:
        html = _render_monthly_html(pixel_id, client_name, m, client_logo=logo)
        pdf  = _html_to_pdf(html)

    subject = f"📈 Relatório mensal · {m['month_label']}: {fmt_brl(m['revenue'])} · {client_name}"
    body_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#f9fafb;padding:32px">
<div style="max-width:480px;margin:0 auto;background:#fff;border-radius:8px;border:1px solid #e5e7eb;padding:28px">
  <h2 style="margin:0 0 8px;color:#111827">📈 Relatório Mensal — {m['month_label']}</h2>
  <p style="margin:0 0 16px;color:#6b7280;font-size:14px">
    Olá! O relatório completo de <strong>{client_name}</strong> referente a <strong>{m['month_label']}</strong>
    está em anexo (PDF).
  </p>
  <p style="margin:0;color:#6b7280;font-size:13px">
    Faturamento do mês: <strong>{fmt_brl(m['revenue'])}</strong><br>
    Pedidos: <strong>{m['orders']}</strong> · Ticket médio: <strong>{fmt_brl(m['aov'])}</strong>
  </p>
</div>
</body></html>"""

    safe_name = client_name.lower().replace(" ", "-").replace("/", "-")[:30]
    filename  = f"relatorio-{m['month_label'].lower().replace(' ','').replace('/','_')}-{safe_name}.pdf"

    for addr in to_list:
        if pdf:
            email_service.send_email_with_attachment(
                to=addr, subject=subject, html_body=body_html,
                attachment_content=pdf, attachment_filename=filename,
            )
        else:
            html_fb = _render_monthly_html(pixel_id, client_name, m, client_logo=logo)
            email_service.send_email(to=addr, subject=subject, html_body=html_fb)

    logger.info("monthly report (%s) sent to %s for %s", "PDF" if pdf else "HTML-fallback", to_list, pixel_id)

    # WhatsApp group — resumo mensal compacto
    if client:
        group_jid = (client.get("whatsapp_group_jid") or "").strip()
        if group_jid:
            from . import whatsapp as _wa
            highlights = " · ".join(m.get("highlights", [])[:2])
            wa_text = (
                f"📈 *Relatório {m['month_label']} — {client_name}*\n\n"
                f"Faturamento: *{fmt_brl(m['revenue'])}*"
                + (f" {'+' if (m['mom_delta'] or 0) >= 0 else ''}{m['mom_delta']:.1f}% vs mês anterior" if m['mom_delta'] is not None else "")
                + f"\nPedidos: {m['orders']} · Ticket médio: {fmt_brl(m['aov'])}"
                + (f"\nROAS: {m['roas']:.2f}x" if m.get('roas') else "")
                + (f"\n\n{highlights}" if highlights else "")
                + "\n\nRelatório completo foi enviado por email."
            )
            _wa.send_to_group(group_jid, wa_text)

    return {"sent_to": to_list, "held": False, "reasons": []}


def _render_monthly_html(pixel_id: str, client_name: str, m: dict,
                         held_for: Optional[str] = None,
                         client_logo: Optional[str] = None) -> str:
    # Held banner (agency-only)
    held_banner = ""
    if held_for:
        reasons = "".join(f"<li style='margin:3px 0'>{r}</li>" for r in m["health"]["reasons"])
        held_banner = f"""
    <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:14px 16px;margin-bottom:18px">
      <p style="margin:0 0 6px;font-weight:700;color:#92400e;font-size:13px">⚠️ Relatório retido para revisão</p>
      <p style="margin:0 0 6px;color:#78350f;font-size:12px">
        Este mês foi sinalizado como negativo, então <strong>não foi enviado ao cliente</strong> ({held_for}).
        Revise e, se fizer sentido, dispare manualmente.
      </p>
      <ul style="margin:0;padding-left:18px;color:#78350f;font-size:12px">{reasons}</ul>
    </div>"""

    # Positive highlights (always)
    hl_items = "".join(
        f'<li style="margin:5px 0;color:#065f46;font-size:13px">✔ {h}</li>' for h in m["highlights"]
    )
    highlights = f"""
    <div style="background:#ecfdf5;border:1px solid #a7f3d0;border-radius:6px;padding:16px;margin-bottom:18px">
      <p style="margin:0 0 8px;font-weight:700;color:#065f46;font-size:13px">Destaques do mês</p>
      <ul style="margin:0;padding-left:18px;list-style:none">{hl_items}</ul>
    </div>"""

    # Goal bar
    goal_bar = ""
    if m["pct_of_goal"] is not None:
        bar_w   = min(int(m["pct_of_goal"]), 100)
        bar_col = "#16a34a" if m["pct_of_goal"] >= 100 else "#f59e0b"
        goal_bar = f"""
      <div style="margin-top:10px">
        <div style="display:flex;justify-content:space-between;font-size:11px;color:#6b7280;margin-bottom:4px">
          <span>{m['pct_of_goal']}% da meta ({fmt_brl(m['goal'])})</span>
        </div>
        <div style="background:#e5e7eb;border-radius:4px;height:8px">
          <div style="background:{bar_col};width:{bar_w}%;height:8px;border-radius:4px"></div>
        </div>
      </div>"""

    # Key metrics
    roas_row = ""
    if m["roas"] is not None:
        tgt = float(m["roas_goal"] or 0)
        col = "#16a34a" if (tgt == 0 or m["roas"] >= tgt) else "#f59e0b"
        tgt_s = f" / meta {tgt:.1f}x" if tgt > 0 else ""
        roas_row = f'<tr style="border-bottom:1px solid #f3f4f6"><td style="padding:9px 0;color:#6b7280;font-size:13px">ROAS</td><td style="padding:9px 0;text-align:right;font-size:13px;font-weight:600;color:{col}">{m["roas"]:.2f}x{tgt_s}</td></tr>'
    spend_row = ""
    if m["total_spend"] > 0:
        spend_row = f'<tr style="border-bottom:1px solid #f3f4f6"><td style="padding:9px 0;color:#6b7280;font-size:13px">Investimento total</td><td style="padding:9px 0;text-align:right;font-size:13px">{fmt_brl(m["total_spend"])}</td></tr>'
    conv_row = ""
    if m["conv"] is not None:
        conv_row = f'<tr style="border-bottom:1px solid #f3f4f6"><td style="padding:9px 0;color:#6b7280;font-size:13px">Taxa de conversão</td><td style="padding:9px 0;text-align:right;font-size:13px">{m["conv"]:.2f}%</td></tr>'

    # Per-channel detail (Meta / Google / TikTok)
    channel_html = ""
    if m["channels"]:
        crows = ""
        for c in m["channels"]:
            roas_s = f'{c["roas"]:.2f}x' if c["roas"] is not None else "—"
            roas_c = "#16a34a" if (c["roas"] is not None and c["roas"] >= 1) else "#dc2626" if c["roas"] is not None else "#9ca3af"
            cpa_s  = fmt_brl(c["cpa"]) if c["cpa"] is not None else "—"
            crows += f"""
        <tr style="border-bottom:1px solid #f3f4f6">
          <td style="padding:8px 0;font-size:12px;font-weight:600;color:#111827">{c['label']}</td>
          <td style="padding:8px 0;text-align:right;font-size:12px;color:#6b7280">{fmt_brl(c['spend'])}</td>
          <td style="padding:8px 0;text-align:right;font-size:12px">{fmt_brl(c['revenue'])}</td>
          <td style="padding:8px 0;text-align:right;font-size:12px;font-weight:600;color:{roas_c}">{roas_s}</td>
          <td style="padding:8px 0;text-align:right;font-size:12px;color:#6b7280">{cpa_s}</td>
        </tr>"""
        channel_html = f"""
    <div style="margin-top:22px">
      <p style="margin:0 0 8px;font-weight:600;color:#374151;font-size:14px">Desempenho por canal</p>
      <table style="width:100%;border-collapse:collapse">
        <tr style="border-bottom:2px solid #e5e7eb">
          <th style="padding:6px 0;text-align:left;font-size:11px;color:#9ca3af;font-weight:500">Canal</th>
          <th style="padding:6px 0;text-align:right;font-size:11px;color:#9ca3af;font-weight:500">Investimento</th>
          <th style="padding:6px 0;text-align:right;font-size:11px;color:#9ca3af;font-weight:500">Receita</th>
          <th style="padding:6px 0;text-align:right;font-size:11px;color:#9ca3af;font-weight:500">ROAS</th>
          <th style="padding:6px 0;text-align:right;font-size:11px;color:#9ca3af;font-weight:500">CPA</th>
        </tr>
        {crows}
      </table>
      <p style="margin:6px 0 0;font-size:10px;color:#9ca3af">Receita atribuída por origem (utm_source). ROAS = receita ÷ investimento.</p>
    </div>"""

    # Top products
    products_html = ""
    if m["top_products"]:
        prows = "".join(
            f'<tr style="border-bottom:1px solid #f3f4f6">'
            f'<td style="padding:7px 0;font-size:12px;color:#374151;max-width:300px">{p["name"]}</td>'
            f'<td style="padding:7px 0;text-align:right;font-size:12px;color:#6b7280">{p["qty"]}x</td>'
            f'<td style="padding:7px 0;text-align:right;font-size:12px;font-weight:600">{fmt_brl(p["revenue"])}</td>'
            f'</tr>'
            for p in m["top_products"]
        )
        products_html = f"""
    <div style="margin-top:22px">
      <p style="margin:0 0 8px;font-weight:600;color:#374151;font-size:14px">Produtos mais vendidos</p>
      <table style="width:100%;border-collapse:collapse">
        <tr style="border-bottom:2px solid #e5e7eb">
          <th style="padding:6px 0;text-align:left;font-size:11px;color:#9ca3af;font-weight:500">Produto</th>
          <th style="padding:6px 0;text-align:right;font-size:11px;color:#9ca3af;font-weight:500">Qtd</th>
          <th style="padding:6px 0;text-align:right;font-size:11px;color:#9ca3af;font-weight:500">Receita</th>
        </tr>
        {prows}
      </table>
    </div>"""

    # Retention
    ret = m["retention"]
    retention_html = f"""
    <div style="margin-top:22px">
      <p style="margin:0 0 8px;font-weight:600;color:#374151;font-size:14px">Base de clientes</p>
      <table style="width:100%;border-collapse:collapse">
        <tr style="border-bottom:1px solid #f3f4f6"><td style="padding:8px 0;color:#6b7280;font-size:13px">Pedidos de novos clientes</td><td style="padding:8px 0;text-align:right;font-size:13px;font-weight:600">{ret['new']}</td></tr>
        <tr style="border-bottom:1px solid #f3f4f6"><td style="padding:8px 0;color:#6b7280;font-size:13px">Pedidos de clientes recorrentes</td><td style="padding:8px 0;text-align:right;font-size:13px;font-weight:600">{ret['returning']}</td></tr>
        <tr><td style="padding:8px 0;color:#6b7280;font-size:13px">Taxa de recompra</td><td style="padding:8px 0;text-align:right;font-size:13px">{ret['repeat_rate_pct']:.1f}%</td></tr>
      </table>
    </div>"""

    # Deep AI analysis
    ai_html = ""
    if m["ai"] and m["ai"].get("content"):
        safe = m["ai"]["content"][:4000].replace("<", "&lt;").replace(">", "&gt;").replace("\n\n", "</p><p style='margin:10px 0 0'>").replace("\n", "<br>")
        ai_html = f"""
    <div style="background:#f5f3ff;border:1px solid #ddd6fe;border-radius:6px;padding:18px;margin-top:22px">
      <p style="margin:0 0 10px;font-weight:600;color:#5b21b6;font-size:14px">✦ Análise estratégica IA</p>
      <p style="margin:0;color:#374151;font-size:13px;line-height:1.6">{safe}</p>
    </div>"""

    mom_line = f"vs. {_MONTH_PT[m['month_start'].month-1] if m['month_start'].month>1 else 'Dezembro'}: {delta_badge(m['mom_delta'])}" if m["mom_delta"] is not None else ""
    dashboard_url = f"{settings.DASHBOARD_URL}/clients/{pixel_id}/dashboard"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f9fafb;margin:0;padding:0">
<div style="max-width:620px;margin:0 auto;padding:24px 16px">

  {_header_html(client_name, f"Relatório Mensal · {m['month_label']}", client_logo=client_logo, wide=True)}

  <div style="background:#fff;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;padding:24px">

    {held_banner}
    {highlights}

    <!-- Total revenue -->
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:18px">
      <p style="margin:0 0 4px;color:#6b7280;font-size:11px;text-transform:uppercase;letter-spacing:0.5px">Faturamento do mês</p>
      <p style="margin:0;font-size:32px;font-weight:700;color:#111827">{fmt_brl(m['revenue'])}</p>
      <p style="margin:6px 0 0;font-size:13px">{mom_line}</p>
      {goal_bar}
    </div>

    <!-- Key metrics -->
    <table style="width:100%;border-collapse:collapse;margin-top:18px">
      <tr style="border-bottom:1px solid #f3f4f6"><td style="padding:9px 0;color:#6b7280;font-size:13px">Pedidos</td><td style="padding:9px 0;text-align:right;font-size:13px;font-weight:600">{m['orders']}</td></tr>
      <tr style="border-bottom:1px solid #f3f4f6"><td style="padding:9px 0;color:#6b7280;font-size:13px">Ticket médio</td><td style="padding:9px 0;text-align:right;font-size:13px">{fmt_brl(m['aov'])}</td></tr>
      {conv_row}
      {spend_row}
      {roas_row}
    </table>

    {channel_html}
    {products_html}
    {retention_html}
    {ai_html}

  </div>

  <p style="color:#9ca3af;font-size:11px;text-align:center;margin-top:16px">
    {settings.AGENCY_NAME or 'Ecommerce Tracking IA'} ·
    <a href="{dashboard_url}" style="color:#6366f1">Ver dashboard</a>
  </p>
</div>
</body></html>"""


# ════════════════════════════════════════════════════════════════════════════════
# On-demand (UI button / API)
# ════════════════════════════════════════════════════════════════════════════════

def send_report_now(
    client_id: str,
    pixel_id: str,
    to_email: str,
    report_type: str = "weekly",
    generate_ai: bool = True,
    force: bool = False,
) -> dict:
    """
    Generate fresh Claude insights (if none recent) and send the requested
    report immediately. report_type ∈ {"weekly", "monthly"}.
    For monthly, force=True bypasses the negativity gate (agency override).
    """
    sb = get_supabase()
    insight_type = "monthly_report" if report_type == "monthly" else "weekly_report"

    if generate_ai:
        try:
            from . import ai_analyst
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
            recent = (
                sb.table("ai_insights").select("id", count="exact", head=True)
                .eq("client_id", client_id).eq("type", insight_type)
                .gte("created_at", cutoff).execute()
            )
            if not (recent.count and recent.count > 0):
                if report_type == "monthly":
                    ai_analyst.generate_monthly_insights(client_id)
                else:
                    ai_analyst.generate_insights(client_id)
        except Exception as exc:
            logger.warning("send_report_now: AI generation failed for %s: %s", pixel_id, exc)

    client_name = _client_name(sb, client_id, pixel_id)

    # Se foi passado email de override (UI / teste), usa ele.
    # Caso contrário usa toda a lista de destinatários do cliente.
    client_row  = sb.table("clients").select("alert_email, alert_emails, whatsapp_group_jid, logo_url").eq("id", client_id).limit(1).execute()
    client_data = (client_row.data or [{}])[0]
    recipients  = [to_email] if to_email else (_notify._all_client_emails(client_data) or [])

    if not recipients:
        raise ValueError(f"Nenhum email configurado para {pixel_id}")

    if report_type == "monthly":
        return _send_monthly(client_id, pixel_id, client_name, recipients, force=force, client=client_data)

    _send_weekly(client_id, pixel_id, client_name, recipients, client=client_data)
    return {"sent_to": recipients, "held": False, "reasons": []}
