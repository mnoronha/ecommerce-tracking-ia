"""
Monthly report data builder — fetches all data from DB + Meta API and
assembles the full context dict for the relatorios-agencia Handlebars templates.

Produces a context compatible with relatorios-agencia/templates/mensal.html.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone, date as date_type
from pathlib import Path
from typing import Optional

import httpx

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)

_GRAPH   = "https://graph.facebook.com/v19.0"
_MESES_PT = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
              "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]

# ── Formatters (mirrored in JS; used to pre-format values for template) ───────

def _brl(v) -> str:
    if v is None: return "—"
    try:
        n = float(v)
        s = f"{abs(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {s}" if n >= 0 else f"-R$ {s}"
    except (TypeError, ValueError):
        return "—"

def _num(v) -> str:
    if v is None: return "—"
    try: return f"{int(v):,}".replace(",", ".")
    except (TypeError, ValueError): return "—"

def _pct(v, decimals=1) -> str:
    if v is None: return "—"
    try: return f"{float(v):.{decimals}f}%".replace(".", ",")
    except (TypeError, ValueError): return "—"

def _roas(v) -> str:
    if v is None: return "—"
    try: return f"{float(v):.1f}x".replace(".", ",")
    except (TypeError, ValueError): return "—"

def _delta(atual, anterior, lower_better=False) -> tuple[str, str]:
    """Returns (formatted_delta, css_class)."""
    if atual is None or anterior is None or anterior == 0:
        return "→ s/ base", "flat"
    p = (float(atual) - float(anterior)) / abs(float(anterior)) * 100
    if abs(p) < 0.5:
        return "→ estável", "flat"
    seta = "▲" if p > 0 else "▼"
    sign = "+" if p > 0 else ""
    label = f"{seta} {sign}{p:.0f}%"
    subiu = p > 0
    good  = (not subiu) if lower_better else subiu
    return label, "good" if good else "bad"


# ── DB queries ────────────────────────────────────────────────────────────────

def _window(year: int, month: int):
    """Returns (start_iso, end_iso) for the given month in UTC."""
    s = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
    if month == 12:
        e = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        e = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return s.isoformat(), e.isoformat()


def _fetch_orders(sb, client_id: str, start: str, end: str) -> list[dict]:
    r = (
        sb.table("orders")
        .select("total_price, financial_status, is_first_purchase, utm_source, utm_medium, created_at, platform_order_number")
        .eq("client_id", client_id)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", start)
        .lt("created_at", end)
        .not_.is_("platform_order_number", "null")
        .neq("platform_order_number", "")
        .execute()
    )
    return r.data or []


def _fetch_spend(sb, client_id: str, start_date: str, end_date: str) -> dict:
    """Returns {channel: {spend, impressions, clicks, conversions}}."""
    r = (
        sb.table("ad_spend")
        .select("channel, spend, impressions, clicks, conversions")
        .eq("client_id", client_id)
        .gte("date", start_date)
        .lte("date", end_date)
        .execute()
    )
    out: dict = {}
    for row in (r.data or []):
        ch = row["channel"]
        agg = out.setdefault(ch, {"spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0})
        agg["spend"]       += float(row.get("spend") or 0)
        agg["impressions"] += int(row.get("impressions") or 0)
        agg["clicks"]      += int(row.get("clicks") or 0)
        agg["conversions"] += float(row.get("conversions") or 0)
    return out


def _fetch_top_products(sb, client_id: str, start: str, end: str, limit=10) -> list[dict]:
    r = (
        sb.table("orders")
        .select("order_items(name, quantity, line_total)")
        .eq("client_id", client_id)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", start)
        .lt("created_at", end)
        .execute()
    )
    prod: dict = {}
    for o in (r.data or []):
        for it in (o.get("order_items") or []):
            # Strip size suffix for grouping (e.g. "Nike Air Jordan ... - 44" → group by base name)
            raw_name = (it.get("name") or "Produto sem nome").strip()
            # Use raw name for now; grouping by base could be done via regex
            agg = prod.setdefault(raw_name, {"name": raw_name, "qty": 0, "revenue": 0.0})
            agg["qty"]     += int(it.get("quantity") or 1)
            agg["revenue"] += float(it.get("line_total") or 0)
    rows = sorted(prod.values(), key=lambda p: p["revenue"], reverse=True)[:limit]
    for i, p in enumerate(rows, 1):
        p["rank"]        = i
        p["revenue_fmt"] = _brl(p["revenue"])
        p["qty_fmt"]     = _num(p["qty"])
    return rows


def _fetch_daily_revenue(sb, client_id: str, start: str, end: str) -> list[dict]:
    r = (
        sb.table("orders")
        .select("total_price, created_at")
        .eq("client_id", client_id)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", start)
        .lt("created_at", end)
        .execute()
    )
    daily: dict[str, float] = {}
    for o in (r.data or []):
        # Convert UTC to BRT (UTC-3) for date bucketing
        dt  = datetime.fromisoformat(o["created_at"].replace("Z", "+00:00"))
        brt = dt - timedelta(hours=3)
        d   = brt.date().isoformat()
        daily[d] = daily.get(d, 0.0) + float(o["total_price"])

    if not daily:
        return []

    max_val = max(daily.values()) or 1
    rows = []
    for d, v in sorted(daily.items()):
        day_label = datetime.fromisoformat(d).strftime("%d/%m")
        rows.append({
            "dia":        day_label,
            "receita":    round(v, 2),
            "receita_fmt": _brl(v),
            "bar_pct":    round(v / max_val * 100, 1),
        })
    return rows


def _fetch_attribution(orders: list[dict]) -> list[dict]:
    """Group orders by utm_source into attribution buckets."""
    _META_SRC  = {"facebook", "fb", "instagram", "ig", "meta", "facebook_ads", "instagram_ads", "meta_ads"}
    _GOOGLE_SRC = {"google", "google_ads", "googleads", "adwords", "gads", "youtube"}
    _TIKTOK_SRC = {"tiktok", "tiktok_ads"}

    buckets: dict[str, dict] = {}

    def bucket(src: Optional[str], medium: Optional[str]) -> str:
        s = (src or "").lower()
        m = (medium or "").lower()
        if s in {"pos", "in_store", "offline"} or m in {"pos", "in_store"}:
            return "Loja Física"
        if s in _META_SRC:   return "Meta Ads"
        if s in _GOOGLE_SRC:
            if m in {"organic", ""}:
                return "Google Orgânico"
            return "Google Ads"
        if s in _TIKTOK_SRC: return "TikTok Ads"
        if s in {"direct", ""} or m in {"direct", "none", ""}:
            return "Direto"
        if s in {"klaviyo", "email", "newsletter"}: return "Email"
        if m == "organic" or s in {"organic", "seo"}: return "Orgânico"
        return "Outros"

    for o in orders:
        b = bucket(o.get("utm_source"), o.get("utm_medium"))
        agg = buckets.setdefault(b, {"canal": b, "pedidos": 0, "receita": 0.0})
        agg["pedidos"] += 1
        agg["receita"] += float(o.get("total_price") or 0)

    total_receita = sum(b["receita"] for b in buckets.values()) or 1
    total_pedidos = sum(b["pedidos"] for b in buckets.values()) or 1

    rows = sorted(buckets.values(), key=lambda x: x["receita"], reverse=True)
    for r in rows:
        r["receita_fmt"]  = _brl(r["receita"])
        r["pct_receita"]  = round(r["receita"] / total_receita * 100, 1)
        r["pct_pedidos"]  = round(r["pedidos"] / total_pedidos * 100, 1)
        r["pct_fmt"]      = _pct(r["pct_receita"])
        r["bar_pct"]      = r["pct_receita"]

    return rows


def _fetch_retention(orders: list[dict]) -> dict:
    novos      = sum(1 for o in orders if o.get("is_first_purchase") is True)
    recorrentes = sum(1 for o in orders if o.get("is_first_purchase") is False)
    total      = novos + recorrentes
    if total == 0:
        total = len(orders)
        novos = total

    rep_rate = round(recorrentes / total * 100, 1) if total > 0 else 0
    nov_pct  = round(novos       / total * 100, 1) if total > 0 else 100
    return {
        "novos":           novos,
        "recorrentes":     recorrentes,
        "total":           total,
        "rep_rate":        rep_rate,
        "rep_rate_fmt":    _pct(rep_rate),
        "novos_pct":       nov_pct,
        "novos_pct_fmt":   _pct(nov_pct),
        "novos_fmt":       _num(novos),
        "recorrentes_fmt": _num(recorrentes),
        "total_fmt":       _num(total),
        "bar_novos":       nov_pct,
        "bar_rec":         rep_rate,
    }


# ── Meta creative performance ─────────────────────────────────────────────────

def _fetch_meta_creatives(
    ad_account_id: str,
    access_token: str,
    start_date: str,
    end_date: str,
    client_id: str,
    limit: int = 6,
) -> list[dict]:
    """
    Fetch ad-level performance for the period from Meta Ads API.
    Returns top `limit` ads by spend with creative metadata.
    """
    clean = ad_account_id.removeprefix("act_")
    try:
        resp = httpx.get(
            f"{_GRAPH}/act_{clean}/ads",
            params={
                "fields": (
                    "id,name,effective_status,"
                    "insights.time_range({'since':'" + start_date + "','until':'" + end_date + "'})"
                    "{spend,impressions,clicks,actions,ctr,cpp}"
                ),
                "limit": 100,
                "access_token": access_token,
            },
            timeout=30.0,
        )
        if resp.status_code != 200:
            logger.warning("meta_creatives HTTP %s: %s", resp.status_code, resp.text[:200])
            return []
        ads = resp.json().get("data") or []
    except Exception as exc:
        logger.warning("meta_creatives fetch failed: %s", exc)
        return []

    # Fetch creative thumbnails from DB
    try:
        sb = get_supabase()
        creatives_r = (
            sb.table("ad_creatives")
            .select("ad_id, thumbnail_url, image_url, body, headline, call_to_action")
            .eq("client_id", client_id)
            .execute()
        )
        thumb_map: dict[str, dict] = {
            c["ad_id"]: c for c in (creatives_r.data or [])
        }
    except Exception:
        thumb_map = {}

    rows: list[dict] = []
    for ad in ads:
        insights_data = (ad.get("insights") or {}).get("data") or []
        if not insights_data:
            continue
        ins = insights_data[0]

        spend = float(ins.get("spend") or 0)
        if spend < 1:
            continue

        impressions = int(ins.get("impressions") or 0)
        clicks      = int(ins.get("clicks") or 0)
        ctr         = float(ins.get("ctr") or 0)

        # Count purchases from actions
        purchases = 0.0
        for action in (ins.get("actions") or []):
            if action.get("action_type") in ("purchase", "omni_purchase",
                                              "offsite_conversion.fb_pixel_purchase"):
                purchases += float(action.get("value") or 0)

        roas_val = None  # No revenue value in ad insights; show purchases instead

        creative = thumb_map.get(ad["id"], {})
        thumb    = creative.get("thumbnail_url") or creative.get("image_url") or ""
        headline = creative.get("headline") or creative.get("body") or ad.get("name") or "—"
        headline = headline[:60]

        cpa = round(spend / purchases, 2) if purchases > 0 else None

        rows.append({
            "ad_id":          ad["id"],
            "nome":           (ad.get("name") or "—")[:55],
            "headline":       headline,
            "status":         ad.get("effective_status", ""),
            "thumbnail":      thumb,
            "spend":          spend,
            "spend_fmt":      _brl(spend),
            "impressions":    impressions,
            "impressions_fmt": _num(impressions),
            "clicks":         clicks,
            "clicks_fmt":     _num(clicks),
            "ctr":            round(ctr, 2),
            "ctr_fmt":        _pct(ctr),
            "purchases":      purchases,
            "purchases_fmt":  _num(int(purchases)),
            "cpa":            cpa,
            "cpa_fmt":        _brl(cpa) if cpa else "—",
            "has_thumb":      bool(thumb),
        })

    rows.sort(key=lambda x: x["spend"], reverse=True)
    return rows[:limit]


# ── AI insights ───────────────────────────────────────────────────────────────

def _fetch_ai_insights(sb, client_id: str, insight_type: str) -> Optional[dict]:
    try:
        r = (
            sb.table("ai_insights")
            .select("title, content, data")
            .eq("client_id", client_id)
            .eq("type", insight_type)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return r.data[0] if r.data else None
    except Exception:
        return None


def _fetch_open_alerts(sb, client_id: str, limit=5) -> list[dict]:
    try:
        r = (
            sb.table("alerts")
            .select("severity, title, message")
            .eq("client_id", client_id)
            .is_("resolved_at", "null")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return r.data or []
    except Exception:
        return []


# ── KPI builders ──────────────────────────────────────────────────────────────

def _build_kpis(cur: dict, prev: dict, yoy: dict) -> list[dict]:
    def mk(cor, label, val_cur, val_prev, val_yoy, fmt_fn, lower=False):
        mom, mom_cls = _delta(val_cur, val_prev, lower)
        yoy_d, yoy_cls = _delta(val_cur, val_yoy, lower)
        return {
            "cor": cor,
            "label": label,
            "valor": fmt_fn(val_cur),
            "mom": mom,
            "momClass": mom_cls,
            "yoy": yoy_d,
            "yoyClass": yoy_cls,
        }

    return [
        mk("var(--c-faturamento)", "Faturamento",  cur["revenue"],  prev["revenue"],  yoy["revenue"],  _brl),
        mk("var(--c-roas)",        "ROAS Geral",   cur["roas"],     prev["roas"],     yoy["roas"],     _roas),
        mk("var(--c-pedidos)",     "Pedidos",       cur["orders"],   prev["orders"],   yoy["orders"],   _num),
        mk("var(--c-cpa)",         "CPA",           cur["cpa"],      prev["cpa"],      yoy["cpa"],      _brl, True),
        mk("var(--c-ctr)",         "Investimento",  cur["spend"],    prev["spend"],    yoy["spend"],    _brl),
        {"cor": "var(--c-conv)", "label": "Ticket Médio", "valor": _brl(cur["aov"]),
         "mom": "", "momClass": "flat", "yoy": "", "yoyClass": "flat"},
    ]


def _build_yoy(cur: dict, yoy: dict) -> list[dict]:
    def row(label, c, y, fmt, lower=False):
        d, cls = _delta(c, y, lower)
        return {"label": label, "atual": fmt(c), "ant": fmt(y), "delta": d, "classe": cls}
    return [
        row("Faturamento", cur["revenue"], yoy["revenue"], _brl),
        row("ROAS",        cur["roas"],    yoy["roas"],    _roas),
        row("Pedidos",     cur["orders"],  yoy["orders"],  _num),
        row("CPA",         cur["cpa"],     yoy["cpa"],     _brl, True),
    ]


def _build_channel_rows(spend: dict, rev_by_source: dict) -> list[dict]:
    _META_KEYS   = {"meta_ads"}
    _GOOGLE_KEYS = {"google_ads"}
    _TIKTOK_KEYS = {"tiktok_ads"}

    _SOURCE_TO_CHANNEL = {
        "facebook": "meta_ads", "fb": "meta_ads", "instagram": "meta_ads",
        "ig": "meta_ads", "meta": "meta_ads",
        "google": "google_ads", "google_ads": "google_ads",
        "tiktok": "tiktok_ads", "tiktok_ads": "tiktok_ads",
    }

    # Aggregate revenue by channel
    rev: dict[str, dict] = {}
    for src, data in rev_by_source.items():
        ch = _SOURCE_TO_CHANNEL.get(src.lower(), "other")
        if ch == "other":
            continue
        agg = rev.setdefault(ch, {"revenue": 0.0, "orders": 0})
        agg["revenue"] += data["revenue"]
        agg["orders"]  += data["orders"]

    _CHANNEL_INFO = {
        "meta_ads":   {"nome": "Meta Ads (Facebook + Instagram)", "tipo": "meta",   "icone": "📘"},
        "google_ads": {"nome": "Google Ads (Search + Shopping + PMax)", "tipo": "google", "icone": "🔴"},
        "tiktok_ads": {"nome": "TikTok Ads", "tipo": "tiktok", "icone": "🎵"},
    }

    rows = []
    for ch, s in sorted(spend.items(), key=lambda x: -x[1]["spend"]):
        info = _CHANNEL_INFO.get(ch, {"nome": ch, "tipo": "other", "icone": "📡"})
        r    = rev.get(ch, {"revenue": 0.0, "orders": 0})
        spd  = s["spend"]
        roas = round(r["revenue"] / spd, 2) if spd > 0 else None
        cpa  = round(spd / r["orders"], 2) if r["orders"] > 0 else None
        ctr  = round(s["clicks"] / s["impressions"] * 100, 2) if s["impressions"] > 0 else 0

        metricas = [
            {"label": "Receita Atribuída", "valor": _brl(r["revenue"]), "delta": "", "classe": "flat"},
            {"label": "Pedidos",           "valor": _num(r["orders"]),   "delta": "", "classe": "flat"},
            {"label": "CPM",               "valor": _brl(round(s["spend"] / s["impressions"] * 1000, 2) if s["impressions"] > 0 else 0), "delta": "", "classe": "flat"},
            {"label": "CTR",               "valor": _pct(ctr),           "delta": "", "classe": "flat"},
            {"label": "Cliques",           "valor": _num(s["clicks"]),   "delta": "", "classe": "flat"},
            {"label": "Impressões",        "valor": _num(s["impressions"]), "delta": "", "classe": "flat"},
        ]

        rows.append({
            **info,
            "investimento":     spd,
            "investimento_fmt": _brl(spd),
            "receita":          r["revenue"],
            "pedidos":          r["orders"],
            "roas":             roas,
            "destaque":         f"ROAS {_roas(roas)}" if roas else _brl(spd),
            "cpa":              cpa,
            "cpa_fmt":          _brl(cpa),
            "metricas":         metricas,
        })
    return rows


# ── Main builder ──────────────────────────────────────────────────────────────

def build_monthly_context(
    client_id: str,
    client: dict,
    year: int,
    month: int,
) -> dict:
    """
    Fetches all data and returns the full template context dict.
    client: dict with DB client fields.
    """
    sb = get_supabase()

    # Date windows
    start, end   = _window(year, month)
    start_date   = f"{year}-{month:02d}-01"
    end_date_iso = f"{year}-{month:02d}-{(datetime(year, month + 1 if month < 12 else 1, 1) - timedelta(days=1)).day:02d}" if month < 12 else f"{year}-12-31"

    prev_month  = month - 1 if month > 1 else 12
    prev_year   = year if month > 1 else year - 1
    prev_s, prev_e = _window(prev_year, prev_month)
    prev_sd     = f"{prev_year}-{prev_month:02d}-01"
    prev_ed     = f"{prev_year}-{prev_month:02d}-{(datetime(prev_year, prev_month + 1 if prev_month < 12 else 1, 1) - timedelta(days=1)).day:02d}" if prev_month < 12 else f"{prev_year}-12-31"

    yoy_year    = year - 1
    yoy_s, yoy_e = _window(yoy_year, month)
    yoy_sd      = f"{yoy_year}-{month:02d}-01"
    yoy_ed      = f"{yoy_year}-{month:02d}-{(datetime(yoy_year, month + 1 if month < 12 else 1, 1) - timedelta(days=1)).day:02d}" if month < 12 else f"{yoy_year}-12-31"

    # Orders
    orders_cur  = _fetch_orders(sb, client_id, start, end)
    orders_prev = _fetch_orders(sb, client_id, prev_s, prev_e)
    orders_yoy  = _fetch_orders(sb, client_id, yoy_s, yoy_e)

    def agg_orders(rows):
        rev = sum(float(o["total_price"]) for o in rows)
        cnt = len(rows)
        return {
            "revenue": round(rev, 2),
            "orders":  cnt,
            "aov":     round(rev / cnt, 2) if cnt else 0,
            "spend":   0.0, "roas": None, "cpa": None,
        }

    cur  = agg_orders(orders_cur)
    prev = agg_orders(orders_prev)
    yoy  = agg_orders(orders_yoy)

    # Spend
    spend_cur  = _fetch_spend(sb, client_id, start_date, end_date_iso)
    spend_prev = _fetch_spend(sb, client_id, prev_sd, prev_ed)
    spend_yoy  = _fetch_spend(sb, client_id, yoy_sd, yoy_ed)

    for agg, sp in [(cur, spend_cur), (prev, spend_prev), (yoy, spend_yoy)]:
        agg["spend"] = round(sum(s["spend"] for s in sp.values()), 2)
        agg["roas"]  = round(agg["revenue"] / agg["spend"], 2) if agg["spend"] > 0 else None
        agg["cpa"]   = round(agg["spend"] / agg["orders"], 2) if agg["orders"] > 0 else None

    # Revenue by utm_source for channel attribution
    rev_by_source: dict[str, dict] = {}
    for o in orders_cur:
        src = (o.get("utm_source") or "").lower()
        agg2 = rev_by_source.setdefault(src, {"revenue": 0.0, "orders": 0})
        agg2["revenue"] += float(o.get("total_price") or 0)
        agg2["orders"]  += 1

    # Attribution funnel
    attribution = _fetch_attribution(orders_cur)

    # Daily revenue
    daily_revenue = _fetch_daily_revenue(sb, client_id, start, end)

    # Retention
    retention = _fetch_retention(orders_cur)

    # Top products
    top_products = _fetch_top_products(sb, client_id, start, end)

    # Channels
    channels = _build_channel_rows(spend_cur, rev_by_source)

    # Meta creatives performance
    meta_creatives: list[dict] = []
    if client.get("meta_ad_account_id") and client.get("meta_access_token"):
        meta_creatives = _fetch_meta_creatives(
            ad_account_id = client["meta_ad_account_id"],
            access_token  = client["meta_access_token"],
            start_date    = start_date,
            end_date      = end_date_iso,
            client_id     = client_id,
        )

    # AI insights
    ai_monthly  = _fetch_ai_insights(sb, client_id, "monthly_report")
    resumo_exec = ""
    if ai_monthly:
        data = ai_monthly.get("data") or {}
        resumo_exec = data.get("resumo_executivo") or data.get("summary") or ai_monthly.get("content") or ""
        resumo_exec = resumo_exec[:800]

    # Destaques from AI
    destaques: list[dict] = []
    if ai_monthly:
        data = ai_monthly.get("data") or {}
        for item in (data.get("destaques") or []):
            destaques.append({
                "tipo":   item.get("tipo", "positivo"),
                "titulo": item.get("titulo", ""),
                "texto":  item.get("texto", ""),
            })

    if not destaques:
        # Build from data
        if cur["roas"] and (prev["roas"] or 0) > 0:
            d, cls = _delta(cur["roas"], prev["roas"])
            destaques.append({
                "tipo": "positivo" if cls == "good" else "negativo",
                "titulo": "Desempenho do ROAS",
                "texto": f"ROAS de {_roas(cur['roas'])} no mês ({d} vs {_MESES_PT[prev_month]}).",
            })
        if top_products:
            tp = top_products[0]
            destaques.append({
                "tipo": "positivo",
                "titulo": "Produto Destaque",
                "texto": f"{tp['name']} liderou as vendas com {tp['revenue_fmt']} em receita.",
            })

    # Atenções from open alerts
    open_alerts = _fetch_open_alerts(sb, client_id)
    atencoes = [
        {
            "icone": "⚠️" if a.get("severity") == "critical" else "📊",
            "titulo": a.get("title", ""),
            "descricao": a.get("message", ""),
            "prioridade": "alta" if a.get("severity") == "critical" else "media",
        }
        for a in open_alerts
    ]

    # Mes label
    mes_label = f"{_MESES_PT[month]}/{year}"
    mes_ant_label = f"{_MESES_PT[prev_month]}/{prev_year}"
    ano_ant_label = f"{_MESES_PT[month]}/{yoy_year}"

    # Agency config
    agency_cfg_path = Path(__file__).parent.parent.parent.parent.parent / "relatorios-agencia" / "config" / "agencia.json"
    agencia = {"nome": settings.AGENCY_NAME or "Noroia", "logo_texto": settings.AGENCY_NAME or "NOROIA",
                "site": settings.AGENCY_WEBSITE or "noroia.com", "cor_primaria": "#6c47ff", "cor_secundaria": "#a855f7"}
    try:
        import json
        if agency_cfg_path.exists():
            agencia = json.loads(agency_cfg_path.read_text())
    except Exception:
        pass

    # Client goal
    goal_row = sb.table("clients").select("monthly_revenue_goal, target_roas").eq("id", client_id).limit(1).execute()
    goal_rev  = float((goal_row.data or [{}])[0].get("monthly_revenue_goal") or 0)
    goal_roas = float((goal_row.data or [{}])[0].get("target_roas") or 0)
    goal_pct  = round(cur["revenue"] / goal_rev * 100, 1) if goal_rev > 0 else None

    return {
        # Identity
        "agencia": agencia,
        "cliente": {
            "id":   client.get("pixel_id") or client_id,
            "nome": client.get("name") or client.get("pixel_id") or "",
            "tipo": "ecommerce",
            "logo": client.get("logo_url") or "",
        },
        "ecommerce":    True,
        "leads":        False,
        "perfil_label": "E-commerce",
        "data_geracao": datetime.now(timezone.utc).strftime("%d/%m/%Y"),

        # Period
        "mes":              _MESES_PT[month],
        "ano":              year,
        "mes_label":        mes_label,
        "mes_anterior_label": mes_ant_label,
        "ano_anterior_label": ano_ant_label,

        # AI text
        "resumo_executivo": resumo_exec or f"Relatório de {mes_label} para {client.get('name', '')}.",

        # KPIs
        "kpis": _build_kpis(cur, prev, yoy),
        "yoy":  _build_yoy(cur, yoy),

        # Summary numbers (for held banner etc.)
        "revenue_fmt":      _brl(cur["revenue"]),
        "orders_fmt":       _num(cur["orders"]),
        "aov_fmt":          _brl(cur["aov"]),
        "roas_fmt":         _roas(cur["roas"]),
        "spend_fmt":        _brl(cur["spend"]),
        "cpa_fmt":          _brl(cur["cpa"]),
        "goal_pct":         goal_pct,
        "goal_pct_fmt":     _pct(goal_pct) if goal_pct else None,

        # Channels
        "canais": channels,

        # Attribution
        "atribuicao": attribution,
        "atribuicao_total_fmt": _brl(cur["revenue"]),

        # Daily revenue (bar chart)
        "receita_diaria": daily_revenue,
        "receita_max":    max((d["receita"] for d in daily_revenue), default=0),

        # Meta creatives
        "criativos_meta":      meta_creatives,
        "tem_criativos_meta":  bool(meta_creatives),

        # Campaigns (placeholder — populated from AI or left empty)
        "campanhas": [],

        # Top products
        "top_produtos": top_products,
        "tem_produtos": bool(top_products),

        # Retention
        "retencao": retention,

        # Highlights / Alerts
        "destaques": destaques,
        "atencoes":  atencoes,
        "tem_atencoes": bool(atencoes),

        # Next month plan (from AI if available)
        "plano": None,
        "plano_meta_faturamento_fmt": "",
        "plano_budget_fmt": "",
    }
