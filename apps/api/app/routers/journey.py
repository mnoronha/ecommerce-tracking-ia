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
        .select("id, total_price, gross_profit, utm_source, utm_medium, utm_campaign, "
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
                "profit":     0.0,
                "units":      0,
                "_products":  {},  # platform_product_id → aggregates
            }
        b = bucket[key]
        b["orders"]  += 1
        b["revenue"] += float(o.get("total_price") or 0)
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

    # Finalize: sort products inside each campaign, slice top N, format
    campaigns = []
    for b in bucket.values():
        prods = sorted(b["_products"].values(), key=lambda p: p["revenue"], reverse=True)[:top_products]
        campaigns.append({
            "source":      b["source"],
            "medium":      b["medium"],
            "campaign":    b["campaign"],
            "platform":    b["platform"],
            "orders":      b["orders"],
            "revenue":     round(b["revenue"], 2),
            "profit":      round(b["profit"], 2) if b["profit"] else None,
            "units":       b["units"],
            "avg_ticket":  round(b["revenue"] / b["orders"], 2) if b["orders"] else 0,
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
                    "platform": c["platform"],
                    "source":   c["source"],
                    "campaign": c["campaign"],
                    "units":    c["units"],
                    "revenue":  round(c["revenue"], 2),
                    "orders":   len(c["orders"]),
                }
                for c in camps
            ],
        })
    products.sort(key=lambda x: x["revenue"], reverse=True)
    return {"days": days, "products": products, "total_orders": len({o["id"] for o in orders})}
