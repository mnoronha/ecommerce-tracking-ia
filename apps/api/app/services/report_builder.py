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
    novos = recorrentes = 0
    rev_novos = rev_rec = 0.0
    for o in orders:
        v = float(o.get("total_price") or 0)
        if o.get("is_first_purchase") is True:
            novos += 1; rev_novos += v
        elif o.get("is_first_purchase") is False:
            recorrentes += 1; rev_rec += v
    total = novos + recorrentes
    if total == 0:
        total = len(orders)
        novos = total
        rev_novos = sum(float(o.get("total_price") or 0) for o in orders)

    rep_rate = round(recorrentes / total * 100, 1) if total > 0 else 0
    nov_pct  = round(novos       / total * 100, 1) if total > 0 else 100
    rev_total = rev_novos + rev_rec
    rev_rec_pct = round(rev_rec / rev_total * 100, 1) if rev_total > 0 else 0
    ticket_novo = round(rev_novos / novos, 2) if novos > 0 else 0
    ticket_rec  = round(rev_rec / recorrentes, 2) if recorrentes > 0 else 0
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
        # Receita por tipo de cliente
        "rev_novos":         round(rev_novos, 2),
        "rev_rec":           round(rev_rec, 2),
        "rev_novos_fmt":     _brl(rev_novos),
        "rev_rec_fmt":       _brl(rev_rec),
        "rev_rec_pct":       rev_rec_pct,
        "rev_rec_pct_fmt":   _pct(rev_rec_pct),
        "ticket_novo_fmt":   _brl(ticket_novo),
        "ticket_rec_fmt":    _brl(ticket_rec),
    }


# ── Funil de conversão (RPC server-side) ──────────────────────────────────────

def _fetch_funnel(sb, client_id: str, start: str, end: str, orders_count: int) -> list[dict]:
    """Funil do mês via RPC funnel_counts (agrega tracking_events server-side).
    Etapa final (Compras) vem de orders — o pixel não emite purchase (vem do CAPI)."""
    counts: dict[str, int] = {}
    try:
        res = sb.rpc("funnel_counts", {"p_client": client_id, "p_start": start, "p_end": end}).execute()
        for r in (res.data or []):
            counts[r["event_type"]] = int(r["n"])
    except Exception as exc:
        logger.warning("_fetch_funnel rpc failed for %s: %s", client_id, exc)
        return []

    stages = [
        ("Visitas",                 counts.get("pageview", 0)),
        ("Visualizaram produto",    counts.get("view_product", 0)),
        ("Adicionaram ao carrinho", counts.get("add_to_cart", 0)),
        ("Iniciaram checkout",      counts.get("begin_checkout", 0)),
        ("Compraram",               orders_count),
    ]
    top = stages[0][1] or max((n for _, n in stages), default=0) or 1
    out: list[dict] = []
    prev_n: Optional[int] = None
    for label, n in stages:
        conv = round(n / prev_n * 100, 1) if (prev_n and prev_n > 0) else None
        out.append({
            "label":    label,
            "n":        n,
            "n_fmt":    _num(n),
            "bar_pct":  round(n / top * 100, 1) if top else 0,
            "conv_fmt": _pct(conv) if conv is not None else None,
        })
        prev_n = n
    return out


# ── Eficiência de mídia: MER, CAC, LTV, LTV:CAC ───────────────────────────────

def _build_efficiency(cur: dict, retention: dict, ltv_stats: dict) -> dict:
    rev    = cur["revenue"]
    spend  = cur["spend"]
    novos  = retention.get("novos") or 0
    mer    = round(rev / spend, 2) if spend > 0 else None
    cac    = round(spend / novos, 2) if (spend > 0 and novos > 0) else None

    # LTV estimado: ticket médio × multiplicador de recompra (média ponderada
    # por amostra das estatísticas de LTV por canal, 180d).
    ltv = ltv_cac = None
    if ltv_stats:
        tot_n = sum((s.get("sample_size") or 0) for s in ltv_stats.values())
        if tot_n > 0:
            blended = sum(
                (1.0 + (s.get("repeat_rate") or 0) * (s.get("avg_repeat_orders") or 0)) * (s.get("sample_size") or 0)
                for s in ltv_stats.values()
            ) / tot_n
            if cur["aov"]:
                ltv = round(cur["aov"] * blended, 2)
                if cac and cac > 0:
                    ltv_cac = round(ltv / cac, 1)
    return {
        "mer":         mer,
        "mer_fmt":     _roas(mer),
        "cac":         cac,
        "cac_fmt":     _brl(cac),
        "ltv":         ltv,
        "ltv_fmt":     _brl(ltv) if ltv else None,
        "ltv_cac":     ltv_cac,
        "ltv_cac_fmt": (f"{ltv_cac:.1f}:1".replace(".", ",") if ltv_cac else None),
        "ltv_cac_ok":  bool(ltv_cac and ltv_cac >= 3),
    }


# ── Scorecard de saúde + Metas vs Realizado ───────────────────────────────────

def _build_scorecard(cur: dict, mom_delta: Optional[float],
                     goal_rev: float, goal_roas: float) -> tuple[dict, list[dict]]:
    rev   = cur["revenue"]
    roas  = cur["roas"]
    spend = cur["spend"]

    metas: list[dict] = []

    def _meta(label, realizado_fmt, meta_fmt, pct, lower_better=False):
        if pct is None:
            return
        if lower_better:
            st = "good" if pct <= 100 else ("warn" if pct <= 115 else "bad")
        else:
            st = "good" if pct >= 100 else ("warn" if pct >= 85 else "bad")
        metas.append({
            "label": label, "realizado": realizado_fmt, "meta": meta_fmt,
            "pct": pct, "pct_fmt": _pct(pct), "bar_pct": min(round(pct), 100),
            "status": st,
        })

    pct_rev = round(rev / goal_rev * 100, 1) if goal_rev > 0 else None
    _meta("Faturamento", _brl(rev), _brl(goal_rev), pct_rev)

    pct_roas = round(roas / goal_roas * 100, 1) if (goal_roas > 0 and roas) else None
    _meta("ROAS (MER)", _roas(roas), _roas(goal_roas), pct_roas)

    if goal_rev > 0 and goal_roas > 0:
        budget = goal_rev / goal_roas
        pct_spend = round(spend / budget * 100, 1) if budget > 0 else None
        _meta("Investimento", _brl(spend), _brl(budget), pct_spend, lower_better=True)

    # Veredito do mês
    bad = warn = 0
    if rev <= 0:
        bad += 2
    if mom_delta is not None and mom_delta <= -20:
        bad += 1
    elif mom_delta is not None and mom_delta < 0:
        warn += 1
    for m in metas:
        if m["status"] == "bad":
            bad += 1
        elif m["status"] == "warn":
            warn += 1

    if bad >= 2:
        status, emoji, label = "vermelho", "🔴", "Mês de atenção"
    elif bad == 1 or warn >= 2:
        status, emoji, label = "amarelo", "🟡", "Mês dentro do esperado, com ressalvas"
    else:
        status, emoji, label = "verde", "🟢", "Mês saudável"

    parts: list[str] = [f"Faturamento de {_brl(rev)}"]
    if mom_delta is not None:
        parts.append(f"{'+' if mom_delta >= 0 else ''}{mom_delta:.0f}% vs. mês anterior")
    if pct_rev is not None:
        parts.append(f"{_pct(pct_rev)} da meta")
    if roas:
        parts.append(f"ROAS {_roas(roas)}")
    verdict = " · ".join(parts) + "."

    return {"status": status, "emoji": emoji, "label": label, "verdict": verdict}, metas


# ── Atribuição multi-toque: comparação de modelos ─────────────────────────────

_ATTR_PLATFORM_LABELS = {
    "meta": "Meta Ads", "facebook": "Meta Ads", "instagram": "Meta Ads",
    "google": "Google Ads", "google_ads": "Google Ads",
    "tiktok": "TikTok Ads", "direct": "Direto", "organic": "Orgânico",
    "email": "E-mail", "other": "Outros",
    "pos": "PDV / Loja física", "offline": "PDV / Loja física",
}

def _fetch_attribution_models(client_id: str, start_date: str, end_date: str) -> dict:
    """Compara receita por canal sob 3 modelos (último clique / linear / time decay).
    Onde os modelos divergem, há canais que assistem a venda sem levar o último clique.
    Quando os 3 modelos coincidem (realidade de toque único), a seção é omitida —
    rows=[] — para não exibir 3 colunas idênticas sem valor analítico."""
    from . import attribution_engine
    models = ["last_click", "linear", "time_decay"]
    per_model: dict[str, dict] = {}
    coverage: Optional[float] = None
    for mdl in models:
        try:
            s = attribution_engine.get_summary(client_id, model=mdl, start=start_date, end=end_date)
        except Exception as exc:
            logger.warning("_fetch_attribution_models(%s) failed: %s", mdl, exc)
            s = {}
        per_model[mdl] = {p.get("platform"): float(p.get("revenue") or 0) for p in (s.get("by_platform") or [])}
        if coverage is None:
            coverage = s.get("multitouch_pct")

    plats = {p for d in per_model.values() for p in d if p}
    if not plats:
        return {"rows": [], "multitouch_pct": coverage, "multitouch_pct_fmt": None, "converged": False}

    lc = per_model.get("last_click", {})
    ln = per_model.get("linear", {})
    td = per_model.get("time_decay", {})
    # Divergência real entre modelos? (tolerância de R$1 p/ ruído de arredondamento)
    diverged = any(
        abs(lc.get(p, 0) - ln.get(p, 0)) > 1.0 or abs(lc.get(p, 0) - td.get(p, 0)) > 1.0
        for p in plats
    )
    if not diverged:
        return {"rows": [], "multitouch_pct": coverage,
                "multitouch_pct_fmt": _pct(coverage) if coverage is not None else None,
                "converged": True}

    rows = []
    for p in sorted(plats, key=lambda x: lc.get(x, 0), reverse=True):
        rows.append({
            "canal":          _ATTR_PLATFORM_LABELS.get((p or "").lower(), (p or "—").title()),
            "last_click_fmt": _brl(lc.get(p, 0)),
            "linear_fmt":     _brl(ln.get(p, 0)),
            "time_decay_fmt": _brl(td.get(p, 0)),
        })
    return {
        "rows": rows,
        "multitouch_pct": coverage,
        "multitouch_pct_fmt": _pct(coverage) if coverage is not None else None,
        "converged": False,
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


_SOURCE_TO_CHANNEL = {
    "facebook": "meta_ads", "fb": "meta_ads", "instagram": "meta_ads",
    "ig": "meta_ads", "meta": "meta_ads",
    "google": "google_ads", "google_ads": "google_ads",
    "tiktok": "tiktok_ads", "tiktok_ads": "tiktok_ads",
}

_CHANNEL_INFO = {
    "meta_ads":   {"nome": "Meta Ads (Facebook + Instagram)", "tipo": "meta",   "icone": "📘"},
    "google_ads": {"nome": "Google Ads (Search + Shopping + PMax)", "tipo": "google", "icone": "🔴"},
    "tiktok_ads": {"nome": "TikTok Ads", "tipo": "tiktok", "icone": "🎵"},
}


def _rev_by_channel(rev_by_source: dict) -> dict[str, dict]:
    rev: dict[str, dict] = {}
    for src, data in rev_by_source.items():
        ch = _SOURCE_TO_CHANNEL.get(src.lower(), "other")
        if ch == "other":
            continue
        agg = rev.setdefault(ch, {"revenue": 0.0, "orders": 0})
        agg["revenue"] += data["revenue"]
        agg["orders"]  += data["orders"]
    return rev


def _build_channel_rows(spend: dict, rev_by_source: dict,
                        spend_prev: Optional[dict] = None,
                        rev_by_source_prev: Optional[dict] = None) -> list[dict]:
    """Per-channel cards with metrics and deltas vs. the previous month."""
    rev      = _rev_by_channel(rev_by_source)
    rev_prev = _rev_by_channel(rev_by_source_prev or {})
    spend_prev = spend_prev or {}

    def _metric(label, valor, atual, anterior, lower_better=False):
        d, cls = _delta(atual, anterior, lower_better=lower_better)
        return {"label": label, "valor": valor, "delta": d, "classe": cls}

    rows = []
    for ch, s in sorted(spend.items(), key=lambda x: -x[1]["spend"]):
        info = _CHANNEL_INFO.get(ch, {"nome": ch, "tipo": "other", "icone": "📡"})
        r    = rev.get(ch, {"revenue": 0.0, "orders": 0})
        spd  = s["spend"]
        roas = round(r["revenue"] / spd, 2) if spd > 0 else None
        cpa  = round(spd / r["orders"], 2) if r["orders"] > 0 else None
        ctr  = round(s["clicks"] / s["impressions"] * 100, 2) if s["impressions"] > 0 else 0
        cpm  = round(s["spend"] / s["impressions"] * 1000, 2) if s["impressions"] > 0 else 0

        # Previous month for deltas
        sp   = spend_prev.get(ch, {})
        rp   = rev_prev.get(ch, {"revenue": 0.0, "orders": 0})
        spd_p  = float(sp.get("spend") or 0)
        roas_p = round(rp["revenue"] / spd_p, 2) if spd_p > 0 else None
        cpa_p  = round(spd_p / rp["orders"], 2) if rp["orders"] > 0 else None
        ctr_p  = round(int(sp.get("clicks") or 0) / int(sp.get("impressions") or 0) * 100, 2) if sp.get("impressions") else None

        metricas = [
            _metric("Investimento",     _brl(spd),          spd,           spd_p,         lower_better=False),
            _metric("Receita Atribuída", _brl(r["revenue"]), r["revenue"],  rp["revenue"], lower_better=False),
            _metric("ROAS",             _roas(roas),        roas,          roas_p,        lower_better=False),
            _metric("Pedidos",          _num(r["orders"]),  r["orders"],   rp["orders"],  lower_better=False),
            _metric("CPA",              _brl(cpa),          cpa,           cpa_p,         lower_better=True),
            _metric("CTR",              _pct(ctr),          ctr,           ctr_p,         lower_better=False),
        ]

        roas_pct_meta = None  # caller may set ROAS-vs-goal bar later
        rows.append({
            **info,
            "investimento":     spd,
            "investimento_fmt": _brl(spd),
            "receita":          r["revenue"],
            "receita_fmt":      _brl(r["revenue"]),
            "pedidos":          r["orders"],
            "roas":             roas,
            "roas_fmt":         _roas(roas),
            "destaque":         f"ROAS {_roas(roas)}" if roas else _brl(spd),
            "cpa":              cpa,
            "cpa_fmt":          _brl(cpa),
            "cpm_fmt":          _brl(cpm),
            "metricas":         metricas,
        })
    return rows


# ── Top campaigns per channel (Meta + Google, live) ───────────────────────────

def _build_campaigns(client: dict, start_date: str, end_date: str,
                     prev_month_label: str = "") -> tuple[list[dict], list[dict]]:
    """
    Fetch top campaigns from Meta + Google APIs and normalise to the template's
    campaign-row shape. Returns (flat_list, grouped_by_channel). Best-effort:
    any channel that fails is simply omitted — the report never breaks.

    Revenue/results shown are platform-reported (Meta purchases & value; Google
    conversions & conversions_value), which differ from our server-side
    attribution — the template footnotes this.
    """
    flat: list[dict] = []
    grouped: list[dict] = []

    def _status(raw: str) -> tuple[str, str]:
        s = (raw or "").upper()
        if s in ("ACTIVE", "ENABLED"):
            return "ativo", "Ativo"
        if s in ("PAUSED",):
            return "pausado", "Pausado"
        return "pausado", (raw or "—").title()

    # ── Meta ──
    meta_rows: list[dict] = []
    if client.get("meta_ad_account_id") and client.get("meta_access_token"):
        try:
            from . import meta_ads
            for c in meta_ads.fetch_campaign_insights(
                account_id = client["meta_ad_account_id"],
                access_token = client["meta_access_token"],
                since = start_date, until = end_date,
            ):
                spend = float(c.get("spend") or 0)
                if spend < 1:
                    continue
                purchases = int(c.get("meta_purchases") or 0)
                revenue   = float(c.get("meta_revenue") or 0)
                roas      = round(revenue / spend, 2) if spend > 0 else None
                cpa       = c.get("meta_cpa") or (round(spend / purchases, 2) if purchases else None)
                st, st_lbl = _status(c.get("status") or c.get("effective_status") or "")
                meta_rows.append({
                    "canal": "meta", "canal_label": "Meta Ads", "icone": "📘",
                    "nome": (c.get("campaign_name") or "—")[:48],
                    "investimento": spend, "investimento_fmt": _brl(spend),
                    "resultado_fmt": _brl(revenue),
                    "indice_fmt": _roas(roas),
                    "qtd_fmt": _num(purchases),
                    "custo_fmt": _brl(cpa) if cpa else "—",
                    "ctr_fmt": _pct(c.get("ctr")) if c.get("ctr") else "—",
                    "status": st, "status_label": st_lbl,
                })
        except Exception as exc:
            logger.warning("_build_campaigns Meta failed: %s", exc)
    if meta_rows:
        meta_rows.sort(key=lambda x: x["investimento"], reverse=True)
        meta_rows = meta_rows[:6]
        grouped.append({"canal": "meta", "canal_label": "Meta Ads", "icone": "📘", "campanhas": meta_rows})
        flat.extend(meta_rows)

    # ── Google ──
    google_rows: list[dict] = []
    if client.get("google_ads_customer_id") and client.get("google_ads_refresh_token"):
        try:
            from . import google_ads
            for c in google_ads.fetch_campaign_insights(
                customer_id = client["google_ads_customer_id"],
                refresh_token = client["google_ads_refresh_token"],
                start_date = start_date, end_date = end_date,
                manager_id = client.get("google_ads_login_customer_id"),
            ):
                spend = float(c.get("spend") or 0)
                if spend < 1:
                    continue
                conv  = float(c.get("conversions") or 0)
                value = float(c.get("conversions_value") or 0)
                st, st_lbl = _status(c.get("status") or "")
                google_rows.append({
                    "canal": "google", "canal_label": "Google Ads", "icone": "🔴",
                    "nome": (c.get("campaign_name") or "—")[:48],
                    "investimento": spend, "investimento_fmt": _brl(spend),
                    "resultado_fmt": _brl(value),
                    "indice_fmt": _roas(c.get("roas")),
                    "qtd_fmt": _num(int(round(conv))),
                    "custo_fmt": _brl(c.get("cpa")) if c.get("cpa") else "—",
                    "ctr_fmt": _pct(c.get("ctr")) if c.get("ctr") else "—",
                    "status": st, "status_label": st_lbl,
                })
        except Exception as exc:
            logger.warning("_build_campaigns Google failed: %s", exc)
    if google_rows:
        google_rows.sort(key=lambda x: x["investimento"], reverse=True)
        google_rows = google_rows[:6]
        grouped.append({"canal": "google", "canal_label": "Google Ads", "icone": "🔴", "campanhas": google_rows})
        flat.extend(google_rows)

    flat.sort(key=lambda x: x["investimento"], reverse=True)
    return flat, grouped


# ── AI analysis + plan ────────────────────────────────────────────────────────

def _build_ai_analysis(client_id, store_name, cur, prev, mom_delta, goal_rev, goal_roas,
                       channels, campanhas, funil, retention, top_products,
                       eficiencia, mes_label) -> dict:
    """Assemble compact REAL metrics and ask the AI for the strategic analysis.
    Returns {} on any failure — the report stays valuable with deterministic
    fallbacks downstream."""
    try:
        from . import ai_analyst
    except Exception:
        return {}

    report_metrics = {
        "mes": mes_label,
        "faturamento": cur["revenue"], "pedidos": cur["orders"], "ticket_medio": cur["aov"],
        "investimento_total": cur["spend"], "roas": cur["roas"], "cpa": cur["cpa"],
        "mom_delta_pct": mom_delta,
        "faturamento_mes_anterior": prev["revenue"],
        "meta_faturamento": goal_rev or None, "meta_roas": goal_roas or None,
        "canais": [
            {"nome": c.get("nome"), "investimento": c.get("investimento"),
             "receita_atribuida": c.get("receita"), "roas": c.get("roas"),
             "cpa": c.get("cpa"), "pedidos": c.get("pedidos")}
            for c in channels
        ],
        "top_campanhas": [
            {"canal": c.get("canal_label"), "nome": c.get("nome"),
             "investimento": c.get("investimento_fmt"), "resultado": c.get("resultado_fmt"),
             "roas": c.get("indice_fmt"), "cpa": c.get("custo_fmt")}
            for c in (campanhas or [])[:10]
        ],
        "funil": funil,
        "retencao": {
            "novos": retention.get("novos") if isinstance(retention, dict) else None,
            "recorrentes": retention.get("recorrentes") if isinstance(retention, dict) else None,
            "taxa_recompra": retention.get("rep_rate_fmt") if isinstance(retention, dict) else None,
        },
        "eficiencia": {
            "mer": eficiencia.get("mer_fmt") if isinstance(eficiencia, dict) else None,
            "cac": eficiencia.get("cac_fmt") if isinstance(eficiencia, dict) else None,
            "ltv_cac": eficiencia.get("ltv_cac_fmt") if isinstance(eficiencia, dict) else None,
        },
        "top_produtos": [{"nome": p.get("name"), "receita": p.get("revenue_fmt")}
                         for p in (top_products or [])[:5]],
    }
    try:
        return ai_analyst.generate_monthly_analysis(client_id, report_metrics, store_name=store_name) or {}
    except Exception as exc:
        logger.warning("_build_ai_analysis failed: %s", exc)
        return {}


def _build_destaques(ai: dict, cur, prev, top_products, prev_month) -> list[dict]:
    """AI destaques first; deterministic fallback so the section is never empty."""
    out: list[dict] = []
    for item in (ai.get("destaques") or []):
        tipo = item.get("tipo", "positivo")
        tipo = "atencao" if tipo not in ("positivo", "atencao") else tipo
        out.append({"tipo": tipo, "titulo": item.get("titulo", ""), "texto": item.get("texto", "")})
    if out:
        return out[:5]
    # Fallback from data
    if cur["roas"] and (prev["roas"] or 0) > 0:
        d, cls = _delta(cur["roas"], prev["roas"])
        out.append({"tipo": "positivo" if cls == "good" else "atencao",
                    "titulo": "Desempenho do ROAS",
                    "texto": f"ROAS de {_roas(cur['roas'])} no mês ({d} vs {_MESES_PT[prev_month]})."})
    if top_products:
        tp = top_products[0]
        out.append({"tipo": "positivo", "titulo": "Produto destaque",
                    "texto": f"{tp['name']} liderou as vendas com {tp['revenue_fmt']} em receita."})
    return out


def _build_plano(ai: dict, next_month_label, cur, goal_rev, goal_roas,
                 channels, top_products, retention) -> tuple[Optional[dict], str, str]:
    """Next-month plan from the AI, with a deterministic fallback built from data."""
    ai_plano = ai.get("plano") or {}

    def _fmt_roas_plain(v):
        try: return f"{float(v):.1f}".replace(".", ",")
        except (TypeError, ValueError): return "—"

    meta_fat   = ai_plano.get("meta_faturamento") or goal_rev or (round(cur["revenue"] * 1.1, 2) if cur["revenue"] else 0)
    meta_roas  = ai_plano.get("meta_roas") or goal_roas or cur["roas"] or 0
    meta_cpa   = ai_plano.get("meta_cpa") or cur["cpa"] or 0
    budget     = ai_plano.get("budget_total")
    if not budget:
        budget = round(float(meta_fat) / float(meta_roas), 2) if (meta_fat and meta_roas) else cur["spend"]

    acoes = list(ai_plano.get("acoes") or [])
    if not acoes:
        best = max((c for c in channels if c.get("roas")), key=lambda c: c["roas"], default=None)
        if best:
            acoes.append(f"Escalar {best['nome']} — canal mais eficiente (ROAS {_roas(best['roas'])}).")
        if top_products:
            acoes.append(f"Manter estoque e mídia no produto destaque: {top_products[0]['name']}.")
        if isinstance(retention, dict) and (retention.get("recorrentes") or 0) > 0:
            acoes.append("Ativar campanha de recompra/CRM para a base de clientes recorrentes.")
        acoes.append("Revisar criativos de menor CTR e renovar os de pior desempenho.")

    plano = {
        "mes": next_month_label,
        "meta_roas": _fmt_roas_plain(meta_roas),
        "meta_cpa":  _brl(meta_cpa) if meta_cpa else "—",
        "acoes": acoes[:5],
    }
    return plano, _brl(meta_fat), _brl(budget)


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

    # Load the FULL client row (with decrypted secrets + ad-account ids). The
    # caller often passes a thin dict (only logo/emails), so we self-load to be
    # able to hit Meta/Google APIs for creatives & campaigns. Caller fields win.
    from . import crypto
    try:
        _full = (sb.table("clients").select("*").eq("id", client_id).limit(1).execute().data or [{}])[0]
        _full = crypto.decrypt_client_secrets(_full) or {}
    except Exception as exc:
        logger.warning("build_monthly_context: full client load failed: %s", exc)
        _full = {}
    # _full wins: it carries the DECRYPTED secrets + ad-account ids. The caller's
    # dict is a subset of the same row and may hold still-encrypted tokens, which
    # must NOT override the decrypted ones (that silently broke Meta/Google fetch).
    client = crypto.decrypt_client_secrets({**(client or {}), **_full})

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
    def _safe_fetch_orders(*a, **kw):
        try:
            return _fetch_orders(*a, **kw)
        except Exception as exc:
            logger.warning("build_monthly_context: _fetch_orders failed: %s", exc)
            return []

    orders_cur  = _safe_fetch_orders(sb, client_id, start, end)
    orders_prev = _safe_fetch_orders(sb, client_id, prev_s, prev_e)
    orders_yoy  = _safe_fetch_orders(sb, client_id, yoy_s, yoy_e)
    logger.info("build_monthly_context: orders cur=%d prev=%d yoy=%d", len(orders_cur), len(orders_prev), len(orders_yoy))

    def agg_orders(rows):
        rev = sum(float(o.get("total_price") or 0) for o in rows)
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
    def _safe_fetch_spend(*a, **kw):
        try:
            return _fetch_spend(*a, **kw)
        except Exception as exc:
            logger.warning("build_monthly_context: _fetch_spend failed: %s", exc)
            return {}

    spend_cur  = _safe_fetch_spend(sb, client_id, start_date, end_date_iso)
    spend_prev = _safe_fetch_spend(sb, client_id, prev_sd, prev_ed)
    spend_yoy  = _safe_fetch_spend(sb, client_id, yoy_sd, yoy_ed)
    logger.info("build_monthly_context: spend channels cur=%s", list(spend_cur.keys()))

    for agg, sp in [(cur, spend_cur), (prev, spend_prev), (yoy, spend_yoy)]:
        agg["spend"] = round(sum(s["spend"] for s in sp.values()), 2)
        agg["roas"]  = round(agg["revenue"] / agg["spend"], 2) if agg["spend"] > 0 else None
        agg["cpa"]   = round(agg["spend"] / agg["orders"], 2) if agg["orders"] > 0 else None

    # Revenue by utm_source for channel attribution (current + previous month)
    def _rev_by_source(rows: list[dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for o in rows:
            src = (o.get("utm_source") or "").lower()
            agg2 = out.setdefault(src, {"revenue": 0.0, "orders": 0})
            agg2["revenue"] += float(o.get("total_price") or 0)
            agg2["orders"]  += 1
        return out

    rev_by_source      = _rev_by_source(orders_cur)
    rev_by_source_prev = _rev_by_source(orders_prev)

    # Attribution funnel
    attribution = _fetch_attribution(orders_cur)

    # Daily revenue
    try:
        daily_revenue = _fetch_daily_revenue(sb, client_id, start, end)
    except Exception as exc:
        logger.warning("build_monthly_context: _fetch_daily_revenue failed: %s", exc)
        daily_revenue = []

    # Retention
    try:
        retention = _fetch_retention(orders_cur)
    except Exception as exc:
        logger.warning("build_monthly_context: _fetch_retention failed: %s", exc)
        retention = {"novos": 0, "recorrentes": 0, "total": 0, "rep_rate": 0, "rep_rate_fmt": "0,0%",
                     "novos_pct": 100, "novos_pct_fmt": "100%", "novos_fmt": "0", "recorrentes_fmt": "0",
                     "total_fmt": "0", "bar_novos": 100, "bar_rec": 0,
                     "rev_novos": 0.0, "rev_rec": 0.0, "rev_novos_fmt": "—", "rev_rec_fmt": "—",
                     "rev_rec_pct": 0, "rev_rec_pct_fmt": "0%", "ticket_novo_fmt": "—", "ticket_rec_fmt": "—"}
    logger.info("build_monthly_context: retention novos=%s recorrentes=%s", retention.get("novos"), retention.get("recorrentes"))

    # Top products
    try:
        top_products = _fetch_top_products(sb, client_id, start, end)
    except Exception as exc:
        logger.warning("build_monthly_context: _fetch_top_products failed: %s", exc)
        top_products = []
    logger.info("build_monthly_context: top_products count=%d", len(top_products))

    # Channels (with deltas vs previous month)
    try:
        channels = _build_channel_rows(spend_cur, rev_by_source, spend_prev, rev_by_source_prev)
    except Exception as exc:
        logger.warning("build_monthly_context: _build_channel_rows failed: %s", exc)
        channels = []
    logger.info("build_monthly_context: channels=%d", len(channels))

    # Top campaigns per channel (Meta + Google, live from APIs)
    try:
        campanhas, campanhas_por_canal = _build_campaigns(
            client, start_date, end_date_iso, prev_month_label=_MESES_PT[prev_month],
        )
    except Exception as exc:
        logger.warning("build_monthly_context: _build_campaigns failed: %s", exc)
        campanhas, campanhas_por_canal = [], []
    logger.info("build_monthly_context: campaigns flat=%d grouped=%d", len(campanhas), len(campanhas_por_canal))

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

    # Agency config — bundle now lives inside the API package (ships in the
    # Docker build context); repo-root path kept as a legacy fallback.
    _here = Path(__file__).resolve()
    agency_cfg_path = next(
        (p for p in (
            _here.parent.parent.parent / "relatorios-agencia" / "config" / "agencia.json",
            _here.parent.parent.parent.parent.parent / "relatorios-agencia" / "config" / "agencia.json",
        ) if p.exists()),
        _here.parent.parent.parent / "relatorios-agencia" / "config" / "agencia.json",
    )
    agencia = {"nome": settings.AGENCY_NAME or "Noroia", "logo_texto": settings.AGENCY_NAME or "NOROIA",
                "site": settings.AGENCY_WEBSITE or "noroia.com", "cor_primaria": "#6c47ff", "cor_secundaria": "#a855f7"}
    try:
        import json
        if agency_cfg_path.exists():
            agencia = json.loads(agency_cfg_path.read_text())
    except Exception:
        pass

    # Client goal + LTV stats
    goal_row = sb.table("clients").select("monthly_revenue_goal, target_roas, ltv_stats").eq("id", client_id).limit(1).execute()
    _goal0    = (goal_row.data or [{}])[0]
    goal_rev  = float(_goal0.get("monthly_revenue_goal") or 0)
    goal_roas = float(_goal0.get("target_roas") or 0)
    goal_pct  = round(cur["revenue"] / goal_rev * 100, 1) if goal_rev > 0 else None
    ltv_stats = _goal0.get("ltv_stats") or {}

    # Funil de conversão (RPC server-side)
    funil = _fetch_funnel(sb, client_id, start, end, cur["orders"])
    logger.info("build_monthly_context: funil stages=%d", len(funil))

    # Eficiência de mídia (MER, CAC, LTV, LTV:CAC)
    try:
        eficiencia = _build_efficiency(cur, retention, ltv_stats)
    except Exception as exc:
        logger.warning("build_monthly_context: _build_efficiency failed: %s", exc)
        eficiencia = {}

    # Scorecard de saúde + Metas vs Realizado
    mom_delta_val = (
        round((cur["revenue"] - prev["revenue"]) / prev["revenue"] * 100, 1)
        if prev["revenue"] > 0 else None
    )
    try:
        scorecard, metas = _build_scorecard(cur, mom_delta_val, goal_rev, goal_roas)
    except Exception as exc:
        logger.warning("build_monthly_context: _build_scorecard failed: %s", exc)
        scorecard, metas = {"status": "verde", "emoji": "📊", "label": "Resultado do Mês", "verdict": ""}, []

    # Atribuição multi-toque (comparação de modelos)
    atribuicao_modelos = _fetch_attribution_models(client_id, start_date, end_date_iso)
    logger.info("build_monthly_context: atribuicao_modelos rows=%d", len(atribuicao_modelos.get("rows") or []))

    # ── AI strategic analysis (real numbers) + next-month plan ────────────────
    next_month       = month + 1 if month < 12 else 1
    next_month_label = _MESES_PT[next_month]
    logger.info("build_monthly_context: calling _build_ai_analysis")
    ai = _build_ai_analysis(
        client_id, client.get("name") or client.get("pixel_id") or "",
        cur, prev, mom_delta_val, goal_rev, goal_roas, channels, campanhas,
        funil, retention, top_products, eficiencia, mes_label,
    )
    resumo_exec   = (ai.get("resumo_executivo") or "")[:800]
    analise_canais = ai.get("analise_canais") or ""
    logger.info("build_monthly_context: ai done, has_resumo=%s", bool(resumo_exec))

    # Destaques: prefer AI, fall back to data-derived
    try:
        destaques = _build_destaques(ai, cur, prev, top_products, prev_month)
    except Exception as exc:
        logger.warning("build_monthly_context: _build_destaques failed: %s", exc)
        destaques = []

    # Plano do próximo mês: AI plan, with deterministic fallback
    try:
        plano, plano_meta_fat_fmt, plano_budget_fmt = _build_plano(
            ai, next_month_label, cur, goal_rev, goal_roas, channels, top_products, retention,
        )
    except Exception as exc:
        logger.warning("build_monthly_context: _build_plano failed: %s", exc)
        plano, plano_meta_fat_fmt, plano_budget_fmt = None, "—", "—"
    logger.info("build_monthly_context: all data ready — building return dict")

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

        # Scorecard de saúde + Metas vs Realizado
        "scorecard": scorecard,
        "metas":     metas,

        # Eficiência de mídia (MER, CAC, LTV, LTV:CAC)
        "eficiencia": eficiencia,

        # Funil de conversão
        "funil": funil,

        # Channels
        "canais": channels,

        # Attribution
        "atribuicao": attribution,
        "atribuicao_total_fmt": _brl(cur["revenue"]),
        "atribuicao_modelos": atribuicao_modelos,

        # Daily revenue (bar chart)
        "receita_diaria": daily_revenue,
        "receita_max":    max((d["receita"] for d in daily_revenue), default=0),

        # Meta creatives
        "criativos_meta":      meta_creatives,
        "tem_criativos_meta":  bool(meta_creatives),

        # Campaigns (top per channel, live from Meta + Google)
        "campanhas": campanhas,
        "campanhas_por_canal": campanhas_por_canal,
        "tem_campanhas": bool(campanhas),

        # Top products
        "top_produtos": top_products,
        "tem_produtos": bool(top_products),

        # Retention
        "retencao": retention,

        # Highlights / Alerts
        "destaques": destaques,
        "atencoes":  atencoes,
        "tem_atencoes": bool(atencoes),

        # AI channel analysis
        "analise_canais": analise_canais,

        # Next month plan (from AI, with deterministic fallback)
        "plano": plano,
        "plano_meta_faturamento_fmt": plano_meta_fat_fmt,
        "plano_budget_fmt": plano_budget_fmt,
    }
