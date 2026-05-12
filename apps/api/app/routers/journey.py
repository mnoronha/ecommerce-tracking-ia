"""
Customer journey analytics — campaign × product cross-tab.

Answers questions like:
  - Which campaigns drove sales of product X?
  - When customers come from "fb_blackfriday_2025", what do they actually buy?
  - Is my Google Ads campaign bringing high-margin or low-margin SKUs?

Two perspectives served from one endpoint:
  GET /journey/{pixel_id}/by-campaign  — group by campaign, list top products
  GET /journey/{pixel_id}/by-product   — group by product, list top campaigns
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..database import get_supabase
from ..services import meta_attribution_sync, meta_campaigns

logger = logging.getLogger(__name__)
router = APIRouter()


def _resolve(pixel_id: str) -> str:
    sb = get_supabase()
    r = (
        sb.table("clients")
        .select("id")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (r and r.data):
        raise HTTPException(status_code=404, detail="Client not found")
    return r.data[0]["id"]


def _platform_from_source(source: Optional[str]) -> str:
    """Coarse channel inference for grouping. Mirrors attribution_engine logic."""
    if not source:
        return "direto"
    s = source.lower()
    if any(x in s for x in ("facebook", "instagram", "meta", "fb")):
        return "meta"
    if "google" in s or "adwords" in s:
        return "google"
    if "tiktok" in s:
        return "tiktok"
    if "pinterest" in s:
        return "pinterest"
    if "email" in s or "klaviyo" in s or "newsletter" in s:
        return "email"
    if "organic" in s or "seo" in s:
        return "organic"
    return s


def _fetch_paid_orders_with_items(client_uuid: str, days: int) -> list[dict]:
    """Pull paid orders + their items in the window. Joined client-side because
    Supabase REST doesn't allow proper joins on aggregate. We fetch in two
    queries and stitch."""
    sb = get_supabase()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    orders = (
        sb.table("orders")
        .select("id, total_price, gross_profit, predicted_ltv, utm_source, utm_medium, utm_campaign, "
                "utm_content, platform_source, shipping_country, created_at")
        .eq("client_id", client_uuid)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", cutoff)
        .execute()
    ).data or []

    if not orders:
        return []

    order_ids = [o["id"] for o in orders]
    items: list[dict] = []
    # Supabase has a limit on .in_() — chunk if many orders
    for i in range(0, len(order_ids), 500):
        chunk = order_ids[i : i + 500]
        r = (
            sb.table("order_items")
            .select("order_id, sku, platform_product_id, name, quantity, line_total, cost_price_snapshot")
            .in_("order_id", chunk)
            .execute()
        )
        items.extend(r.data or [])

    items_by_order: dict[str, list[dict]] = {}
    for it in items:
        items_by_order.setdefault(it["order_id"], []).append(it)

    for o in orders:
        o["_items"] = items_by_order.get(o["id"], [])
    return orders


@router.get(
    "/journey/{pixel_id}/by-campaign",
    summary="Top campaigns and the products they drove",
    tags=["journey"],
)
async def by_campaign(pixel_id: str, days: int = 30, top_products: int = 5):
    """
    Returns one row per campaign with:
      - revenue, profit, orders, units
      - top N products (by revenue) inside that campaign
    Sorted by revenue desc.
    """
    client_uuid = _resolve(pixel_id)
    orders = _fetch_paid_orders_with_items(client_uuid, days)
    if not orders:
        return {"days": days, "campaigns": []}

    bucket: dict[str, dict] = {}
    for o in orders:
        # Campaign key: source|medium|campaign — keeps direct/email separate
        source   = o.get("utm_source")   or "direto"
        medium   = o.get("utm_medium")   or "—"
        campaign = o.get("utm_campaign") or "—"
        key = f"{source}|{medium}|{campaign}"

        if key not in bucket:
            bucket[key] = {
                "source":     source,
                "medium":     medium,
                "campaign":   campaign,
                "platform":   _platform_from_source(source),
                "orders":     0,
                "revenue":    0.0,
                "revenue_ltv": 0.0,  # sum of predicted_ltv — what Meta/Google should bid for
                "profit":     0.0,
                "units":      0,
                "_products":  {},  # platform_product_id → aggregates
            }
        b = bucket[key]
        b["orders"]  += 1
        total_price_val = float(o.get("total_price") or 0)
        b["revenue"] += total_price_val
        # predicted_ltv falls back to total_price when missing so the totals
        # stay comparable on historical orders predating the LTV column.
        b["revenue_ltv"] += float(o.get("predicted_ltv") or total_price_val)
        if o.get("gross_profit") is not None:
            b["profit"] += float(o["gross_profit"])

        for it in o.get("_items") or []:
            pid  = it.get("platform_product_id") or it.get("sku") or "unknown"
            qty  = int(it.get("quantity") or 1)
            line = float(it.get("line_total") or 0)
            cost = it.get("cost_price_snapshot")
            line_profit = (line - float(cost) * qty) if cost is not None else None

            p = b["_products"].setdefault(pid, {
                "product_id": pid,
                "name":       it.get("name") or "—",
                "sku":        it.get("sku"),
                "units":      0,
                "revenue":    0.0,
                "profit":     0.0,
            })
            p["units"]   += qty
            p["revenue"] += line
            if line_profit is not None:
                p["profit"] += line_profit
            b["units"]   += qty

    # Resolve Meta campaign IDs → names if any UTM looks numeric
    name_map: dict[str, str] = {}
    meta_ids = [
        b["campaign"] for b in bucket.values()
        if b["platform"] == "meta" and meta_campaigns.is_meta_id(b["campaign"])
    ]
    if meta_ids:
        name_map = meta_campaigns.get_name_map(client_uuid, meta_ids)

    # Finalize: sort products inside each campaign, slice top N, format
    campaigns = []
    for b in bucket.values():
        prods = sorted(b["_products"].values(), key=lambda p: p["revenue"], reverse=True)[:top_products]
        # Replace numeric Meta ID with human name when available
        display_name = name_map.get(b["campaign"], b["campaign"])
        revenue       = round(b["revenue"], 2)
        revenue_ltv   = round(b["revenue_ltv"], 2)
        # ltv_uplift_pct is how much bigger the LTV-projected revenue is vs
        # immediate revenue. A campaign at 50% uplift is bringing high-LTV
        # buyers; a campaign at 5% is bringing one-and-done buyers.
        ltv_uplift_pct = (
            round(((revenue_ltv - revenue) / revenue) * 100, 1)
            if revenue > 0 else None
        )
        campaigns.append({
            "source":            b["source"],
            "medium":             b["medium"],
            "campaign":           display_name,
            "campaign_id":        b["campaign"] if display_name != b["campaign"] else None,
            "platform":           b["platform"],
            "orders":             b["orders"],
            "revenue":            revenue,
            "revenue_ltv":        revenue_ltv,
            "ltv_uplift_pct":     ltv_uplift_pct,
            "profit":             round(b["profit"], 2) if b["profit"] else None,
            "units":              b["units"],
            "avg_ticket":         round(b["revenue"] / b["orders"], 2) if b["orders"] else 0,
            "top_products": [
                {
                    "product_id": p["product_id"],
                    "name":       p["name"],
                    "sku":        p["sku"],
                    "units":      p["units"],
                    "revenue":    round(p["revenue"], 2),
                    "profit":     round(p["profit"], 2) if p["profit"] else None,
                }
                for p in prods
            ],
        })
    campaigns.sort(key=lambda c: c["revenue"], reverse=True)
    return {"days": days, "campaigns": campaigns, "total_orders": sum(c["orders"] for c in campaigns)}


@router.get(
    "/journey/{pixel_id}/by-product",
    summary="Top products and the campaigns that drove their sales",
    tags=["journey"],
)
async def by_product(pixel_id: str, days: int = 30, top_campaigns: int = 5):
    """
    Returns one row per product with:
      - units, revenue, profit, distinct_orders
      - top N campaigns that drove that product (by revenue)
    Sorted by revenue desc.
    """
    client_uuid = _resolve(pixel_id)
    orders = _fetch_paid_orders_with_items(client_uuid, days)
    if not orders:
        return {"days": days, "products": []}

    bucket: dict[str, dict] = {}
    for o in orders:
        source   = o.get("utm_source")   or "direto"
        campaign = o.get("utm_campaign") or "—"
        platform = _platform_from_source(source)
        camp_key = f"{platform}|{source}|{campaign}"

        for it in o.get("_items") or []:
            pid  = it.get("platform_product_id") or it.get("sku") or "unknown"
            qty  = int(it.get("quantity") or 1)
            line = float(it.get("line_total") or 0)
            cost = it.get("cost_price_snapshot")
            line_profit = (line - float(cost) * qty) if cost is not None else None

            p = bucket.setdefault(pid, {
                "product_id":   pid,
                "name":         it.get("name") or "—",
                "sku":          it.get("sku"),
                "units":        0,
                "revenue":      0.0,
                "profit":       0.0,
                "orders":       set(),
                "_campaigns":   {},
            })
            p["units"]   += qty
            p["revenue"] += line
            if line_profit is not None:
                p["profit"] += line_profit
            p["orders"].add(o["id"])

            c = p["_campaigns"].setdefault(camp_key, {
                "platform": platform,
                "source":   source,
                "campaign": campaign,
                "units":    0,
                "revenue":  0.0,
                "orders":   set(),
            })
            c["units"]   += qty
            c["revenue"] += line
            c["orders"].add(o["id"])

    # Resolve Meta IDs at the campaign-level (collected across all products)
    all_meta_ids: set[str] = set()
    for p in bucket.values():
        for c in p["_campaigns"].values():
            if c["platform"] == "meta" and meta_campaigns.is_meta_id(c["campaign"]):
                all_meta_ids.add(c["campaign"])
    name_map = meta_campaigns.get_name_map(client_uuid, list(all_meta_ids)) if all_meta_ids else {}

    products = []
    for p in bucket.values():
        camps = sorted(p["_campaigns"].values(), key=lambda c: c["revenue"], reverse=True)[:top_campaigns]
        products.append({
            "product_id":  p["product_id"],
            "name":        p["name"],
            "sku":         p["sku"],
            "units":       p["units"],
            "revenue":     round(p["revenue"], 2),
            "profit":      round(p["profit"], 2) if p["profit"] else None,
            "orders":      len(p["orders"]),
            "top_campaigns": [
                {
                    "platform":    c["platform"],
                    "source":      c["source"],
                    "campaign":    name_map.get(c["campaign"], c["campaign"]),
                    "campaign_id": c["campaign"] if name_map.get(c["campaign"]) else None,
                    "units":       c["units"],
                    "revenue":     round(c["revenue"], 2),
                    "orders":      len(c["orders"]),
                }
                for c in camps
            ],
        })
    products.sort(key=lambda x: x["revenue"], reverse=True)
    return {"days": days, "products": products, "total_orders": len({o["id"] for o in orders})}


@router.get(
    "/journey/{pixel_id}/by-meta-attribution",
    summary="Campaigns ranked by Meta-reported purchases (server-side reconciliation)",
    tags=["journey"],
)
async def by_meta_attribution(pixel_id: str, days: int = 30):
    """
    Reads from `meta_ad_attributions` (synced daily). Aggregates by campaign
    so the UI can compare "Meta says X purchases / R$ Y" vs our server-side.
    """
    client_uuid = _resolve(pixel_id)
    sb          = get_supabase()
    cutoff      = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()

    rows = (
        sb.table("meta_ad_attributions")
        .select("campaign_id, campaign_name, ad_id, ad_name, "
                "spend, impressions, clicks, purchases, purchase_value")
        .eq("client_id", client_uuid)
        .gte("date", cutoff)
        .execute()
    ).data or []

    # Roll up to campaign level
    campaigns: dict[str, dict] = {}
    for r in rows:
        cid  = r.get("campaign_id") or "(sem id)"
        cname = r.get("campaign_name") or "(sem nome)"
        c = campaigns.setdefault(cid, {
            "campaign_id":     cid,
            "campaign_name":   cname,
            "spend":           0.0,
            "impressions":     0,
            "clicks":          0,
            "meta_purchases":  0,
            "meta_revenue":    0.0,
            "ads_count":       0,
        })
        c["spend"]          += float(r.get("spend") or 0)
        c["impressions"]    += int(r.get("impressions") or 0)
        c["clicks"]         += int(r.get("clicks") or 0)
        c["meta_purchases"] += int(r.get("purchases") or 0)
        c["meta_revenue"]   += float(r.get("purchase_value") or 0)
        c["ads_count"]      += 1

    # Cross with our server-side numbers from orders
    orders_cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    server_rows = (
        sb.table("orders")
        .select("utm_campaign, total_price")
        .eq("client_id", client_uuid)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", orders_cutoff)
        .execute()
    ).data or []

    # Build server-side map keyed by campaign_id (when utm is numeric)
    server_by_campaign: dict[str, dict] = {}
    for o in server_rows:
        camp = (o.get("utm_campaign") or "").strip()
        if not camp:
            continue
        # We only credit server-side when utm matches a Meta numeric ID
        if camp.isdigit():
            entry = server_by_campaign.setdefault(camp, {"orders": 0, "revenue": 0.0})
            entry["orders"]  += 1
            entry["revenue"] += float(o.get("total_price") or 0)

    out = []
    for c in campaigns.values():
        srv = server_by_campaign.get(c["campaign_id"], {"orders": 0, "revenue": 0.0})
        diff = (c["meta_purchases"] - srv["orders"]) if c["meta_purchases"] else 0
        out.append({
            **c,
            "spend":           round(c["spend"], 2),
            "meta_revenue":    round(c["meta_revenue"], 2),
            "meta_roas":       round(c["meta_revenue"] / c["spend"], 2) if c["spend"] > 0 else None,
            "meta_cpa":        round(c["spend"] / c["meta_purchases"], 2) if c["meta_purchases"] > 0 and c["spend"] > 0 else None,
            "server_orders":   srv["orders"],
            "server_revenue":  round(srv["revenue"], 2),
            "purchases_diff":  diff,
        })
    out.sort(key=lambda x: x["meta_revenue"] or 0, reverse=True)

    totals = {
        "spend":           round(sum(x["spend"] for x in out), 2),
        "meta_purchases":  sum(x["meta_purchases"] for x in out),
        "meta_revenue":    round(sum(x["meta_revenue"] for x in out), 2),
        "server_orders":   sum(x["server_orders"] for x in out),
        "server_revenue":  round(sum(x["server_revenue"] for x in out), 2),
    }
    totals["meta_roas"] = round(totals["meta_revenue"] / totals["spend"], 2) if totals["spend"] > 0 else None

    return {"days": days, "campaigns": out, "totals": totals}


@router.post(
    "/journey/{pixel_id}/sync-meta-attribution",
    summary="Force a sync of Meta-reported attribution (otherwise runs daily)",
    tags=["journey"],
)
async def force_sync_meta_attribution(pixel_id: str, days: int = 7):
    client_uuid = _resolve(pixel_id)
    sb = get_supabase()
    creds = (
        sb.table("clients")
        .select("meta_ad_account_id, meta_access_token")
        .eq("id", client_uuid)
        .limit(1)
        .execute()
    ).data or []
    if not creds:
        raise HTTPException(404, "Client not found")
    c = creds[0]
    if not (c.get("meta_ad_account_id") and c.get("meta_access_token")):
        raise HTTPException(400, "Client missing meta_ad_account_id or meta_access_token")
    return meta_attribution_sync.sync_for_client(
        client_uuid=client_uuid,
        account_id=c["meta_ad_account_id"],
        access_token=c["meta_access_token"],
        days=days,
    )


@router.post(
    "/journey/{pixel_id}/probable-match",
    summary="Run probabilistic match for orders without UTM (Phase 3)",
    tags=["journey"],
)
async def run_probable_match(pixel_id: str, days: int = 30):
    """
    Assigns probable_meta_campaign_* on orders that came from Meta but have
    no campaign UTM, based on which ad had the most clicks that day.
    Confidence reflects how dominant that ad's clicks were.
    """
    client_uuid = _resolve(pixel_id)
    return meta_attribution_sync.probabilistic_match(client_uuid, days=days)


@router.post(
    "/journey/{pixel_id}/resolve-meta-names",
    summary="Sync Meta Ads campaign id → name cache",
    tags=["journey"],
)
async def resolve_meta_names(pixel_id: str):
    """
    Pulls every campaign on the client's Meta ad account and caches name in
    `meta_campaign_names`. After this, journey reports show readable names
    instead of raw IDs like 120210118442. Safe to re-run.
    """
    client_uuid = _resolve(pixel_id)
    sb = get_supabase()
    creds = (
        sb.table("clients")
        .select("meta_ad_account_id, meta_access_token")
        .eq("id", client_uuid)
        .limit(1)
        .execute()
    ).data or []
    if not creds:
        raise HTTPException(404, "Client not found")
    c = creds[0]
    if not (c.get("meta_ad_account_id") and c.get("meta_access_token")):
        raise HTTPException(400, "Client missing meta_ad_account_id or meta_access_token")

    return meta_campaigns.sync_campaign_names(
        client_uuid=client_uuid,
        ad_account_id=c["meta_ad_account_id"],
        access_token=c["meta_access_token"],
    )
