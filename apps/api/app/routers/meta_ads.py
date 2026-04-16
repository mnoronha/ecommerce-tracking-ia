"""
Meta Ads ROAS endpoint.

Combina gasto de campanhas (Meta Ads API) com receita dos pedidos (banco local)
para calcular ROAS, CPA e outras métricas por campanha.

GET /meta-ads/{pixel_id}/roas?days=30
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException

from ..database import get_supabase
from ..services import meta_ads as meta_ads_svc

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/meta-ads/{pixel_id}/roas",
    summary="ROAS por campanha (Meta Ads spend + pedidos)",
    tags=["meta_ads"],
)
async def get_roas(pixel_id: str, days: int = 30):
    """
    Retorna por campanha:
      - spend  (Meta Ads API)
      - revenue, orders  (pedidos com utm_campaign)
      - roas = revenue / spend
      - cpa   = spend / orders
      - impressions, clicks (Meta Ads API)

    Se meta_ad_account_id não estiver configurado no cliente, retorna os dados
    de receita apenas (sem gasto/ROAS), sinalizando has_ads_credentials=false.
    """
    sb = get_supabase()

    # ── Buscar credenciais do cliente ─────────────────────────────────────────
    creds_result = (
        sb.table("clients")
        .select("id, meta_pixel_id, meta_access_token, meta_ad_account_id, monthly_ad_spend, monthly_revenue, monthly_roas")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (creds_result and creds_result.data):
        raise HTTPException(status_code=404, detail="Client not found or inactive")

    c            = creds_result.data[0]
    client_uuid  = c["id"]
    has_ads_creds = bool(c.get("meta_ad_account_id") and c.get("meta_access_token"))

    # ── Pedidos no período agrupados por utm_campaign ─────────────────────────
    start_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
    orders_result = (
        sb.table("orders")
        .select("utm_campaign, utm_source, utm_medium, total_price, financial_status")
        .eq("client_id", client_uuid)
        .gte("created_at", start_date)
        .execute()
    )
    orders = orders_result.data or []

    campaign_map: dict[str, dict] = {}
    for o in orders:
        key = o.get("utm_campaign") or "(sem campanha)"
        if key not in campaign_map:
            campaign_map[key] = {
                "revenue": 0.0,
                "orders":  0,
                "utm_source": o.get("utm_source"),
                "utm_medium": o.get("utm_medium"),
            }
        campaign_map[key]["revenue"] += float(o.get("total_price") or 0)
        campaign_map[key]["orders"]  += 1

    # ── Buscar gasto das campanhas no Meta Ads API ────────────────────────────
    ads_rows: list[dict] = []
    if has_ads_creds:
        ads_rows = meta_ads_svc.fetch_campaign_insights(
            account_id=c["meta_ad_account_id"],
            access_token=c["meta_access_token"],
            days=days,
        )

    # Mapa por nome de campanha (lowercase para matching tolerante)
    spend_map: dict[str, dict] = {r["campaign_name"].lower(): r for r in ads_rows}

    # ── Merge ─────────────────────────────────────────────────────────────────
    all_names = set(campaign_map.keys()) | {r["campaign_name"] for r in ads_rows}
    rows = []
    for name in all_names:
        rev_data = campaign_map.get(name, {"revenue": 0.0, "orders": 0})
        ads      = spend_map.get(name.lower(), {})
        spend    = ads.get("spend", 0.0)
        revenue  = rev_data["revenue"]
        n_orders = rev_data["orders"]

        rows.append({
            "campaign_name": name,
            "utm_source":    rev_data.get("utm_source") or ads.get("utm_source"),
            "spend":         round(spend, 2),
            "revenue":       round(revenue, 2),
            "orders":        n_orders,
            "roas":          round(revenue / spend, 2)        if spend > 0    else None,
            "cpa":           round(spend / n_orders, 2)       if n_orders > 0 and spend > 0 else None,
            "impressions":   ads.get("impressions", 0),
            "clicks":        ads.get("clicks", 0),
            "ctr":           round(ads["clicks"] / ads["impressions"] * 100, 2)
                             if ads.get("impressions", 0) > 0 else None,
            "cpm":           ads.get("cpm"),
        })

    rows.sort(key=lambda r: r["revenue"], reverse=True)

    total_spend   = round(sum(r["spend"] for r in rows), 2)
    total_revenue = round(sum(r["revenue"] for r in rows), 2)
    total_orders  = sum(r["orders"] for r in rows)

    return {
        "has_ads_credentials": has_ads_creds,
        "days":     days,
        "campaigns": rows,
        "totals": {
            "spend":      total_spend,
            "revenue":    total_revenue,
            "orders":     total_orders,
            "roas":       round(total_revenue / total_spend, 2) if total_spend > 0 else None,
            "total_cpa":  round(total_spend / total_orders, 2)  if total_orders > 0 and total_spend > 0 else None,
        },
    }
