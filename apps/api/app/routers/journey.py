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
from pydantic import BaseModel, Field

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


def _fetch_paid_orders_with_items(
    client_uuid: str,
    days: int,
    start: Optional[str] = None,
    end:   Optional[str] = None,
) -> list[dict]:
    """Pull paid orders + their items in the window. Joined client-side because
    Supabase REST doesn't allow proper joins on aggregate. We fetch in two
    queries and stitch."""
    sb = get_supabase()
    if start and end:
        p_start = start + "T00:00:00+00:00"
        p_end   = end   + "T23:59:59+00:00"
    else:
        p_start = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        p_end   = None

    q = (
        sb.table("orders")
        .select("id, total_price, gross_profit, predicted_ltv, utm_source, utm_medium, utm_campaign, "
                "utm_content, platform_source, shipping_country, created_at")
        .eq("client_id", client_uuid)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", p_start)
    )
    if p_end:
        q = q.lte("created_at", p_end)
    orders = q.execute().data or []

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
async def by_campaign(
    pixel_id: str,
    days: int = 30,
    top_products: int = 5,
    start: Optional[str] = None,
    end:   Optional[str] = None,
):
    """
    Returns one row per campaign with:
      - revenue, profit, orders, units
      - top N products (by revenue) inside that campaign
    Sorted by revenue desc. start/end (ISO date YYYY-MM-DD) override days.
    """
    client_uuid = _resolve(pixel_id)
    orders = _fetch_paid_orders_with_items(client_uuid, days, start, end)
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

    # Resolve Meta campaign IDs → names.
    # Handles pure numeric IDs ("120210118442") and embedded IDs
    # ("meta paid|120210118442") where the UTM template mixed a label with the id.
    name_map: dict[str, str] = {}
    raw_to_id: dict[str, str] = {}  # original campaign value → numeric id to look up
    for b in bucket.values():
        if b["platform"] == "meta":
            numeric = meta_campaigns.extract_meta_id(b["campaign"])
            if numeric:
                raw_to_id[b["campaign"]] = numeric
    if raw_to_id:
        id_to_name = meta_campaigns.get_name_map(client_uuid, list(set(raw_to_id.values())))
        for raw_val, numeric in raw_to_id.items():
            if numeric in id_to_name:
                name_map[raw_val] = id_to_name[numeric]

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
async def by_product(
    pixel_id: str,
    days: int = 30,
    top_campaigns: int = 5,
    start: Optional[str] = None,
    end:   Optional[str] = None,
):
    """
    Returns one row per product with:
      - units, revenue, profit, distinct_orders
      - top N campaigns that drove that product (by revenue)
    Sorted by revenue desc. start/end (ISO date YYYY-MM-DD) override days.
    """
    client_uuid = _resolve(pixel_id)
    orders = _fetch_paid_orders_with_items(client_uuid, days, start, end)
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

    # Resolve Meta IDs at the campaign-level (handles embedded IDs too)
    raw_to_id: dict[str, str] = {}
    for p in bucket.values():
        for c in p["_campaigns"].values():
            if c["platform"] == "meta":
                numeric = meta_campaigns.extract_meta_id(c["campaign"])
                if numeric:
                    raw_to_id[c["campaign"]] = numeric
    name_map: dict[str, str] = {}
    if raw_to_id:
        id_to_name = meta_campaigns.get_name_map(client_uuid, list(set(raw_to_id.values())))
        for raw_val, numeric in raw_to_id.items():
            if numeric in id_to_name:
                name_map[raw_val] = id_to_name[numeric]

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
async def by_meta_attribution(
    pixel_id: str,
    days: int = 30,
    start: Optional[str] = None,
    end:   Optional[str] = None,
):
    """
    Reads from `meta_ad_attributions` (synced daily). Aggregates by campaign
    so the UI can compare "Meta says X purchases / R$ Y" vs our server-side.
    start/end (ISO date YYYY-MM-DD) override days.
    """
    client_uuid = _resolve(pixel_id)
    sb          = get_supabase()
    if start and end:
        p_start = start
        p_end   = end
    else:
        p_start = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        p_end   = None

    q = (
        sb.table("meta_ad_attributions")
        .select("campaign_id, campaign_name, ad_id, ad_name, "
                "spend, impressions, clicks, purchases, purchase_value")
        .eq("client_id", client_uuid)
        .gte("date", p_start)
    )
    if p_end:
        q = q.lte("date", p_end)
    rows = q.execute().data or []

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
    o_start = (start + "T00:00:00+00:00") if start else (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    o_end   = (end   + "T23:59:59+00:00") if end   else None
    sq = (
        sb.table("orders")
        .select("utm_campaign, total_price")
        .eq("client_id", client_uuid)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", o_start)
    )
    if o_end:
        sq = sq.lte("created_at", o_end)
    server_rows = sq.execute().data or []

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


# ── Post-purchase attribution survey ─────────────────────────────────────────
#
# Captures the buyer's own answer to "how did you hear about us?" right after
# checkout. Crossed with our UTM/click-id data, this is the only way to recover
# attribution for the 90% of orders that arrive without UTMs (dark social,
# influencers, word-of-mouth).

_VALID_SURVEY_SOURCES = {
    "meta", "instagram", "facebook",
    "google", "youtube", "tiktok",
    "organic_search", "referral_friend", "influencer",
    "email", "podcast", "event_offline", "other",
}


class SurveyResponseBody(BaseModel):
    source_declared: str = Field(..., description="One of the canonical buckets")
    free_text:       Optional[str] = None
    order_id:        Optional[str] = None  # Shopify platform_order_id
    visitor_cookie_id: Optional[str] = None
    page_url:        Optional[str]  = None


@router.post(
    "/journey/{pixel_id}/survey-response",
    summary="Submit a post-purchase 'how did you hear about us?' answer",
    tags=["journey"],
)
async def submit_survey_response(pixel_id: str, body: SurveyResponseBody):
    if body.source_declared not in _VALID_SURVEY_SOURCES:
        raise HTTPException(400, f"invalid source_declared (must be one of {sorted(_VALID_SURVEY_SOURCES)})")

    client_uuid = _resolve(pixel_id)
    sb = get_supabase()

    # Resolve platform_order_id → orders.id when possible (the pixel only knows
    # the Shopify order number at thank-you time).
    order_uuid: Optional[str] = None
    visitor_uuid: Optional[str] = None
    if body.order_id:
        try:
            o = (
                sb.table("orders")
                .select("id, visitor_id")
                .eq("client_id", client_uuid)
                .eq("platform_order_id", str(body.order_id))
                .limit(1)
                .execute()
            )
            if o and o.data:
                order_uuid   = o.data[0]["id"]
                visitor_uuid = o.data[0].get("visitor_id")
        except Exception as exc:
            logger.debug("survey order lookup failed: %s", exc)

    if not visitor_uuid and body.visitor_cookie_id:
        try:
            v = (
                sb.table("visitors")
                .select("id")
                .eq("client_id", client_uuid)
                .eq("visitor_id", body.visitor_cookie_id)
                .limit(1)
                .execute()
            )
            if v and v.data:
                visitor_uuid = v.data[0]["id"]
        except Exception as exc:
            logger.debug("survey visitor lookup failed: %s", exc)

    row = {
        "client_id":         client_uuid,
        "order_id":          order_uuid,
        "visitor_id":        visitor_uuid,
        "visitor_cookie_id": body.visitor_cookie_id,
        "source_declared":   body.source_declared,
        "free_text":         (body.free_text or "").strip()[:500] or None,
        "page_url":          body.page_url,
    }
    try:
        result = sb.table("post_purchase_surveys").insert(row).execute()
        return {"status": "ok", "id": (result.data or [{}])[0].get("id")}
    except Exception as exc:
        logger.warning("survey insert failed: %s", exc)
        raise HTTPException(500, "failed to persist survey response")


@router.get(
    "/journey/{pixel_id}/by-declared-source",
    summary="Aggregate orders by buyer-declared source, with UTM cross-check",
    tags=["journey"],
)
async def by_declared_source(
    pixel_id: str,
    days: int = 30,
    start: Optional[str] = None,
    end:   Optional[str] = None,
):
    """
    For each declared source bucket, returns:
      - declared_orders / declared_revenue (orders that reported this source)
      - utm_match_orders / utm_match_revenue (declared AND have a matching utm_source)
      - utm_miss_orders / utm_miss_revenue (declared but no matching utm)

    A high utm_miss share is the value the survey unlocks — those are sales
    we could not have attributed any other way. start/end override days.
    """
    client_uuid = _resolve(pixel_id)
    if start and end:
        p_start = start + "T00:00:00+00:00"
        p_end   = end   + "T23:59:59+00:00"
    else:
        p_start = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        p_end   = None
    sb = get_supabase()

    sq = (
        sb.table("post_purchase_surveys")
        .select("source_declared, order_id, created_at")
        .eq("client_id", client_uuid)
        .gte("created_at", p_start)
    )
    if p_end:
        sq = sq.lte("created_at", p_end)
    surveys = sq.execute().data or []
    if not surveys:
        return {"days": days, "by_source": [], "total_responses": 0}

    order_ids = list({s["order_id"] for s in surveys if s.get("order_id")})
    orders_by_id: dict[str, dict] = {}
    if order_ids:
        for i in range(0, len(order_ids), 500):
            chunk = order_ids[i:i + 500]
            r = (
                sb.table("orders")
                .select("id, total_price, utm_source, utm_medium")
                .in_("id", chunk)
                .execute()
            ).data or []
            for o in r:
                orders_by_id[o["id"]] = o

    bucket: dict[str, dict] = {}
    for s in surveys:
        src = s["source_declared"]
        b = bucket.setdefault(src, {
            "source_declared":    src,
            "responses":          0,
            "declared_orders":    0,
            "declared_revenue":   0.0,
            "utm_match_orders":   0,
            "utm_match_revenue":  0.0,
            "utm_miss_orders":    0,
            "utm_miss_revenue":   0.0,
        })
        b["responses"] += 1
        order = orders_by_id.get(s.get("order_id") or "")
        if not order:
            continue
        rev = float(order.get("total_price") or 0)
        b["declared_orders"]  += 1
        b["declared_revenue"] += rev
        utm_platform = _platform_from_source(order.get("utm_source"))
        # Bucket-vs-utm match logic: declared 'meta'/'instagram'/'facebook' match
        # utm_source like fb/instagram; 'google'/'youtube' match google; etc.
        match_table = {
            "meta": "meta", "instagram": "meta", "facebook": "meta",
            "google": "google", "youtube": "google",
            "tiktok": "tiktok",
            "organic_search": "organic",
            "email": "email",
        }
        expected = match_table.get(src)
        if expected and utm_platform == expected:
            b["utm_match_orders"]  += 1
            b["utm_match_revenue"] += rev
        else:
            b["utm_miss_orders"]   += 1
            b["utm_miss_revenue"]  += rev

    rows = []
    for b in bucket.values():
        b["declared_revenue"]  = round(b["declared_revenue"], 2)
        b["utm_match_revenue"] = round(b["utm_match_revenue"], 2)
        b["utm_miss_revenue"]  = round(b["utm_miss_revenue"], 2)
        rows.append(b)
    rows.sort(key=lambda r: r["declared_revenue"], reverse=True)

    return {"days": days, "by_source": rows, "total_responses": len(surveys)}
