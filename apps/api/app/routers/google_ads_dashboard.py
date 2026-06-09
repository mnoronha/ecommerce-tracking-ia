"""
Google Ads Dashboard endpoint.

GET /google-ads/{pixel_id}/overview?days=30

Usa dados server-side (pedidos + match_type do Google) — sem chamada
live à Google Ads API. Foca em:
  - Receita atribuída a tráfego Google (utm_source=google/cpc)
  - Cobertura de conversão: gclid vs enhanced vs sem match
  - Breakdown por campanha UTM
  - Série diária
  - Funil de eventos
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()

_GOOGLE_SOURCES = {"google", "cpc", "paid_search", "ppc", "adwords"}
_GOOGLE_MEDIUMS = {"cpc", "paid_search", "ppc", "google"}


def _is_google(source: Optional[str], medium: Optional[str]) -> bool:
    s = (source or "").lower()
    m = (medium or "").lower()
    return "google" in s or s in _GOOGLE_SOURCES or m in _GOOGLE_MEDIUMS


def _delta(curr: float | None, prev: float | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return round((curr - prev) / prev * 100, 1)


@router.get(
    "/google-ads/{pixel_id}/overview",
    summary="Dashboard Google Ads — dados server-side (pedidos + match_type)",
    tags=["google_ads"],
)
async def google_overview(
    pixel_id: str,
    days: int = 30,
    start: str | None = None,
    end:   str | None = None,
):
    """
    Retorna:
    - KPIs: compras, receita, cobertura gclid/enhanced, CPA estimado
    - % Δ vs período anterior
    - Série diária
    - Breakdown por campanha UTM
    - Match type (gclid / gbraid / enhanced_only)
    - Funil de eventos
    - Produtos por campanha (via order_items)
    """
    sb = get_supabase()

    creds = (
        sb.table("clients")
        .select("id, google_ads_customer_id, google_ads_conversion_action_id, "
                "google_ads_refresh_token, google_ads_login_customer_id")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (creds and creds.data):
        raise HTTPException(status_code=404, detail="Client not found or inactive")
    from ..services import crypto
    c         = crypto.decrypt_client_secrets(creds.data[0])
    client_id = c["id"]
    has_creds = bool(c.get("google_ads_customer_id"))

    if start and end:
        d_start = datetime.fromisoformat(start).date()
        d_end   = datetime.fromisoformat(end).date()
        days    = max(1, (d_end - d_start).days + 1)
    else:
        today   = datetime.now(timezone.utc).date()
        d_end   = today
        d_start = today - timedelta(days=days - 1)
    d_prev_end   = d_start - timedelta(days=1)
    d_prev_start = d_prev_end - timedelta(days=days - 1)

    def _fetch_orders(d_from, d_to):
        return (
            sb.table("orders")
            .select(
                "id, total_price, created_at, utm_source, utm_medium, utm_campaign, "
                "google_sent, google_match_type, google_last_error, platform_source"
            )
            .eq("client_id", client_id)
            .eq("financial_status", "paid")
            .gt("total_price", 0)
            .gte("created_at", f"{d_from}T00:00:00+00:00")
            .lte("created_at", f"{d_to}T23:59:59+00:00")
            .limit(5000)
            .execute()
        ).data or []

    curr_orders = _fetch_orders(d_start, d_end)
    prev_orders = _fetch_orders(d_prev_start, d_prev_end)

    # ── Ad spend (Google) — tabela ad_spend (sync diário, account-level) ─────
    def _fetch_spend(d_from, d_to):
        return (
            sb.table("ad_spend")
            .select("date, spend, impressions, clicks")
            .eq("client_id", client_id)
            .eq("channel", "google_ads")
            .gte("date", str(d_from))
            .lte("date", str(d_to))
            .execute()
        ).data or []

    curr_spend_rows = _fetch_spend(d_start, d_end)
    prev_spend_rows = _fetch_spend(d_prev_start, d_prev_end)
    curr_spend  = round(sum(float(r.get("spend") or 0)     for r in curr_spend_rows), 2)
    prev_spend  = round(sum(float(r.get("spend") or 0)     for r in prev_spend_rows), 2)
    curr_impr   = sum(int(r.get("impressions") or 0)       for r in curr_spend_rows)
    curr_clicks = sum(int(r.get("clicks") or 0)            for r in curr_spend_rows)
    has_spend   = len(curr_spend_rows) > 0
    spend_by_day: dict = {}
    for r in curr_spend_rows:
        spend_by_day[str(r["date"])[:10]] = float(r.get("spend") or 0)

    # Partition: Google-attributed vs all conversions sent to Google
    def _partition(orders):
        google_attr = [o for o in orders if _is_google(o.get("utm_source"), o.get("utm_medium"))]
        all_sent    = [o for o in orders if o.get("google_sent")]
        return google_attr, all_sent

    curr_google, curr_sent = _partition(curr_orders)
    prev_google, prev_sent = _partition(prev_orders)

    def _agg(orders):
        revenue = sum(float(o.get("total_price") or 0) for o in orders)
        n       = len(orders)
        return {"orders": n, "revenue": round(revenue, 2)}

    def _match_breakdown(orders):
        sent = [o for o in orders if o.get("google_sent")]
        return {
            "gclid":          sum(1 for o in sent if o.get("google_match_type") == "gclid"),
            "gbraid":         sum(1 for o in sent if o.get("google_match_type") == "gbraid"),
            "enhanced_only":  sum(1 for o in sent if o.get("google_match_type") == "enhanced_only"),
            "not_sent":       sum(1 for o in orders if not o.get("google_sent")
                                  and o.get("platform_source") != "pos"),
            "total_sent":     len(sent),
        }

    curr_agg   = _agg(curr_google)
    prev_agg   = _agg(prev_google)
    curr_match = _match_breakdown(curr_orders)
    prev_match = _match_breakdown(prev_orders)

    sent_coverage_curr = (
        round(curr_match["total_sent"] / len(curr_orders) * 100, 1)
        if curr_orders else None
    )
    gclid_pct_curr = (
        round(curr_match["gclid"] / curr_match["total_sent"] * 100, 1)
        if curr_match["total_sent"] > 0 else None
    )

    # ── Totals with deltas ─────────────────────────────────────────────────
    prev_roas = round(prev_agg["revenue"] / prev_spend, 2) if prev_spend > 0 else None
    totals = {
        **curr_agg,
        "spend":            curr_spend,
        "has_spend":        has_spend,
        "impressions":      curr_impr,
        "clicks":           curr_clicks,
        "roas":             round(curr_agg["revenue"] / curr_spend, 2) if curr_spend > 0 else None,
        "total_sent":       curr_match["total_sent"],
        "sent_coverage_pct": sent_coverage_curr,
        "gclid_pct":        gclid_pct_curr,
        "gclid":            curr_match["gclid"],
        "gbraid":           curr_match["gbraid"],
        "enhanced_only":    curr_match["enhanced_only"],
        "not_sent":         curr_match["not_sent"],
        # CPA real = investimento ÷ pedidos atribuídos ao Google (null se sem spend)
        "cpa": round(curr_spend / curr_agg["orders"], 2) if (has_spend and curr_agg["orders"] > 0) else None,
        "avg_ticket": round(curr_agg["revenue"] / curr_agg["orders"], 2) if curr_agg["orders"] > 0 else None,
    }
    prev_totals = {
        **prev_agg,
        "spend":  prev_spend,
        "roas":   prev_roas,
        "gclid":  prev_match["gclid"],
        "total_sent": prev_match["total_sent"],
    }
    deltas = {
        "orders":  _delta(curr_agg["orders"],  prev_agg["orders"]),
        "revenue": _delta(curr_agg["revenue"], prev_agg["revenue"]),
        "spend":   _delta(curr_spend, prev_spend),
        "roas":    _delta(totals["roas"], prev_roas),
        "gclid":   _delta(curr_match["gclid"], prev_match["gclid"]),
        "total_sent": _delta(curr_match["total_sent"], prev_match["total_sent"]),
    }

    # ── Daily time series ──────────────────────────────────────────────────
    daily_map: dict = {}
    for o in curr_orders:
        if not _is_google(o.get("utm_source"), o.get("utm_medium")):
            continue
        d = o["created_at"][:10]
        if d not in daily_map:
            daily_map[d] = {"date": d, "orders": 0, "revenue": 0.0, "gclid": 0, "enhanced": 0}
        daily_map[d]["orders"]   += 1
        daily_map[d]["revenue"]  += float(o.get("total_price") or 0)
        mt = o.get("google_match_type") or ""
        if mt == "gclid":    daily_map[d]["gclid"]    += 1
        elif "enhanced" in mt: daily_map[d]["enhanced"] += 1

    daily = []
    for i in range(days):
        d = str(d_start + timedelta(days=i))
        row = daily_map.get(d, {"date": d, "orders": 0, "revenue": 0.0, "gclid": 0, "enhanced": 0})
        row["revenue"] = round(row["revenue"], 2)
        row["spend"]   = round(spend_by_day.get(d, 0.0), 2)
        roas_d         = row["revenue"] / row["spend"] if row["spend"] > 0 else None
        row["roas"]    = round(roas_d, 2) if roas_d is not None else None
        daily.append(row)

    # ── Campaign breakdown ─────────────────────────────────────────────────
    camp_map: dict = {}
    prev_camp: dict = {}
    for o in prev_google:
        cam = o.get("utm_campaign") or "(sem campanha)"
        e   = prev_camp.setdefault(cam, {"orders": 0, "revenue": 0.0})
        e["orders"]  += 1
        e["revenue"] += float(o.get("total_price") or 0)

    for o in curr_google:
        cam = o.get("utm_campaign") or "(sem campanha)"
        e   = camp_map.setdefault(cam, {
            "campaign": cam,
            "orders": 0, "revenue": 0.0,
            "gclid": 0, "enhanced": 0,
        })
        e["orders"]  += 1
        e["revenue"] += float(o.get("total_price") or 0)
        mt = o.get("google_match_type") or ""
        if mt == "gclid":    e["gclid"]    += 1
        elif "enhanced" in mt: e["enhanced"] += 1

    # Fetch order_items for product breakdown
    curr_google_ids = [o["id"] for o in curr_google]
    items_by_order: dict = {}
    if curr_google_ids:
        for i in range(0, len(curr_google_ids), 500):
            chunk = curr_google_ids[i:i+500]
            rows = (
                sb.table("order_items")
                .select("order_id, name, sku, quantity, line_total")
                .in_("order_id", chunk)
                .execute()
            ).data or []
            for it in rows:
                items_by_order.setdefault(it["order_id"], []).append(it)

    prod_by_camp: dict = {}
    for o in curr_google:
        cam   = o.get("utm_campaign") or "(sem campanha)"
        items = items_by_order.get(o["id"], [])
        prod_map = prod_by_camp.setdefault(cam, {})
        for it in items:
            name = it.get("name") or "—"
            p    = prod_map.setdefault(name, {"name": name, "sku": it.get("sku"), "units": 0, "revenue": 0.0})
            p["units"]   += int(it.get("quantity") or 1)
            p["revenue"] += float(it.get("line_total") or 0)

    campaigns_out = []
    for cam, e in camp_map.items():
        prev_c  = prev_camp.get(cam, {"orders": 0, "revenue": 0.0})
        rev     = round(e["revenue"], 2)
        prods   = sorted(prod_by_camp.get(cam, {}).values(), key=lambda p: p["revenue"], reverse=True)[:5]
        campaigns_out.append({
            "campaign":      cam,
            "orders":        e["orders"],
            "revenue":       rev,
            "gclid":         e["gclid"],
            "enhanced":      e["enhanced"],
            "cpa":           round(rev / e["orders"], 2) if e["orders"] > 0 else None,
            "revenue_delta": _delta(e["revenue"], prev_c["revenue"]),
            "top_products":  [{"name": p["name"], "sku": p["sku"], "units": p["units"], "revenue": round(p["revenue"], 2)} for p in prods],
        })
    campaigns_out.sort(key=lambda x: x["revenue"], reverse=True)

    # ── Funil (eventos do site) ──────────────────────────────────────────────
    # O exact count em tracking_events (centenas de milhares de linhas) estoura
    # o statement_timeout do PostgREST em janelas longas — o que derrubava o
    # endpoint inteiro com 500 ao trocar de período. Só calculamos em janelas
    # curtas e degradamos com segurança: os KPIs/investimento/campanhas sempre
    # carregam, independente do tamanho do período.
    FUNNEL_MAX_DAYS = 7
    funnel: dict = {"pageview": None, "add_to_cart": None, "begin_checkout": None,
                    "purchases": curr_agg["orders"]}
    funnel_available = False
    if days <= FUNNEL_MAX_DAYS:
        funnel_start = f"{d_start}T00:00:00+00:00"
        funnel_end   = f"{d_end}T23:59:59+00:00"
        try:
            for et in ("pageview", "add_to_cart", "begin_checkout"):
                funnel[et] = (
                    sb.table("tracking_events")
                    .select("id", count="exact", head=True)
                    .eq("client_id", client_id)
                    .eq("event_type", et)
                    .gte("created_at", funnel_start)
                    .lte("created_at", funnel_end)
                    .execute()
                ).count or 0
            funnel_available = True
        except Exception as exc:
            logger.warning("google_overview: funil indisponível (%s, %sd): %s", pixel_id, days, exc)

    # ── Campanhas reais do Google Ads (live via API, todas as campanhas) ──────
    # Métricas por campanha + grupos de anúncios (hierarquia 2 níveis).
    # Server-side (pedidos/receita/produtos) mesclado por nome de campanha.
    # Best-effort: [] se faltar credencial ou a API falhar (não derruba o resto).
    platform_campaigns: list[dict] = []
    if c.get("google_ads_customer_id") and c.get("google_ads_refresh_token"):
        try:
            from ..services import google_ads

            # Grupos de anúncios indexados por campaign_id (string)
            adgroups_by_camp: dict = {}
            try:
                for ag in google_ads.fetch_adgroup_insights(
                    customer_id   = c["google_ads_customer_id"],
                    refresh_token = c["google_ads_refresh_token"],
                    start_date    = str(d_start),
                    end_date      = str(d_end),
                    manager_id    = c.get("google_ads_login_customer_id"),
                ):
                    cid = ag.pop("campaign_id", "")
                    adgroups_by_camp.setdefault(cid, []).append(ag)
            except Exception as exc:
                logger.warning("google_overview: ad groups (%s): %s", pixel_id, exc)

            # Lookup server-side por nome de campanha (lowercase) para mesclar
            server_by_name: dict = {
                e["campaign"].lower(): {
                    "orders":       e["orders"],
                    "revenue":      e["revenue"],
                    "gclid":        e["gclid"],
                    "enhanced":     e["enhanced"],
                    "top_products": e["top_products"],
                }
                for e in campaigns_out
            }

            for camp in google_ads.fetch_campaign_insights(
                customer_id   = c["google_ads_customer_id"],
                refresh_token = c["google_ads_refresh_token"],
                start_date    = str(d_start),
                end_date      = str(d_end),
                manager_id    = c.get("google_ads_login_customer_id"),
                limit         = 100,
            ):
                spend    = float(camp.get("spend") or 0)
                clicks   = int(camp.get("clicks") or 0)
                camp_id  = str(camp.get("campaign_id") or "")
                cam_name = camp.get("campaign_name") or "—"
                svr      = server_by_name.get(cam_name.lower(), {})
                platform_campaigns.append({
                    "campaign_id":       camp.get("campaign_id"),
                    "campaign_name":     cam_name,
                    "status":            camp.get("status") or "",
                    "spend":             spend,
                    "impressions":       int(camp.get("impressions") or 0),
                    "clicks":            clicks,
                    "ctr":               camp.get("ctr"),
                    "cpc":               round(spend / clicks, 2) if clicks else None,
                    "conversions":       camp.get("conversions"),
                    "conversions_value": camp.get("conversions_value"),
                    "roas":              camp.get("roas"),
                    "cpa":               camp.get("cpa"),
                    "ad_groups":         adgroups_by_camp.get(camp_id, []),
                    "server_orders":     svr.get("orders", 0),
                    "server_revenue":    svr.get("revenue", 0.0),
                    "server_gclid":      svr.get("gclid", 0),
                    "server_enhanced":   svr.get("enhanced", 0),
                    "top_products":      svr.get("top_products", []),
                })
        except Exception as exc:
            logger.warning("google_overview: platform campaigns indisponíveis (%s): %s", pixel_id, exc)

    return {
        "days":        days,
        "start":       str(d_start),
        "end":         str(d_end),
        "prev_start":  str(d_prev_start),
        "prev_end":    str(d_prev_end),
        "has_creds":   has_creds,
        "customer_id": c.get("google_ads_customer_id"),
        "totals":      totals,
        "prev_totals": prev_totals,
        "deltas":      deltas,
        "campaigns":   campaigns_out,
        "platform_campaigns": platform_campaigns,
        "daily":       daily,
        "funnel":      funnel,
        "funnel_available": funnel_available,
    }


# ── GA4 Reporting ─────────────────────────────────────────────────────────────

@router.get("/ga4/{pixel_id}/report", summary="GA4 Data API — sessões e conversões")
async def ga4_report(
    pixel_id: str,
    start: Optional[str] = None,
    end:   Optional[str] = None,
    days:  int = 30,
):
    """
    Lê métricas do GA4 via Data API (analytics.readonly scope).
    Retorna sessões, usuários, conversões e receita por canal + série diária.
    Requer ga4_reporting_enabled=true e ga4_property_id configurados.
    """
    from datetime import date as date_type
    sb = get_supabase()

    row = (
        sb.table("clients")
        .select("id, ga4_property_id, ga4_reporting_enabled, google_ads_refresh_token")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (row and row.data):
        raise HTTPException(status_code=404, detail="Client not found")

    from ..services import crypto, ga4_reporting
    c = crypto.decrypt_client_secrets(row.data[0])

    if not c.get("ga4_reporting_enabled"):
        raise HTTPException(status_code=403, detail="GA4 reporting not enabled for this client")
    if not c.get("ga4_property_id"):
        raise HTTPException(status_code=400, detail="ga4_property_id not configured")
    if not c.get("google_ads_refresh_token"):
        raise HTTPException(status_code=400, detail="Google OAuth not connected")

    try:
        if start and end:
            start_dt = date_type.fromisoformat(start)
            end_dt   = date_type.fromisoformat(end)
        else:
            from datetime import datetime, timezone
            today    = datetime.now(timezone.utc).date()
            end_dt   = today - timedelta(days=1)
            start_dt = end_dt - timedelta(days=days - 1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format — use YYYY-MM-DD")

    result = ga4_reporting.fetch_overview(
        property_id=c["ga4_property_id"],
        refresh_token=c["google_ads_refresh_token"],
        start_date=start_dt,
        end_date=end_dt,
    )

    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])

    return result
