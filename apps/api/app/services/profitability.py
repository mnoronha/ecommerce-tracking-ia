"""
Profitability — COGS lookup + gross-profit calculation.

DTC ecommerce lives or dies by margin, not revenue. ROAS 3x at 25% margin
is actually a 0.75x ROAS-of-margin: real money lost. This service:

  1. Resolves cost_price per line-item using product_costs (lookup by SKU or
     platform_product_id; falls back to 0 when nothing's registered).
  2. Computes gross_profit = sum(line_total) - sum(cogs).
  3. Persists order_items + denormalized columns on orders for fast queries.

Called from writer.write_order after the order row is upserted.
"""

import logging
from typing import Optional

from ..database import get_supabase
from ..models.events import NormalizedEvent

logger = logging.getLogger(__name__)


def _lookup_cost(
    client_uuid: str,
    sku: Optional[str],
    platform_product_id: Optional[str],
    cost_cache: dict,
) -> Optional[float]:
    """
    Look up cost for a single line item. Cache hit by (sku, pid) key to avoid
    re-querying for the same product across multiple line items in one order.
    """
    cache_key = (sku, platform_product_id)
    if cache_key in cost_cache:
        return cost_cache[cache_key]

    sb = get_supabase()
    cost: Optional[float] = None

    # Try SKU first (more specific — same product can have variants)
    if sku:
        try:
            r = (
                sb.table("product_costs")
                .select("cost_price")
                .eq("client_id", client_uuid)
                .eq("sku", sku)
                .limit(1)
                .execute()
            )
            if r and r.data:
                cost = float(r.data[0]["cost_price"])
        except Exception as exc:
            logger.debug("cost lookup by SKU failed: %s", exc)

    # Fallback to platform_product_id
    if cost is None and platform_product_id:
        try:
            r = (
                sb.table("product_costs")
                .select("cost_price")
                .eq("client_id", client_uuid)
                .eq("platform_product_id", platform_product_id)
                .limit(1)
                .execute()
            )
            if r and r.data:
                cost = float(r.data[0]["cost_price"])
        except Exception as exc:
            logger.debug("cost lookup by product_id failed: %s", exc)

    cost_cache[cache_key] = cost
    return cost


def persist_items_and_margin(
    client_uuid: str,
    order_uuid: str,
    event: NormalizedEvent,
) -> Optional[dict]:
    """
    Persist order_items + computed margin on the orders row.

    Returns {gross_profit, cogs_total, margin_pct, items_with_cost, items_total}
    for caller logging, or None on failure.
    """
    order = event.order
    if not order or not order.items:
        return None

    sb = get_supabase()
    cost_cache: dict = {}
    items_payload: list[dict] = []
    cogs_total = 0.0
    revenue_from_items = 0.0
    items_with_cost = 0

    for item in order.items:
        unit_price = float(item.price or 0)
        qty        = int(item.quantity or 1)
        line_total = float(item.total or unit_price * qty)
        revenue_from_items += line_total

        cost_unit = _lookup_cost(
            client_uuid,
            sku=item.sku,
            platform_product_id=item.product_id,
            cost_cache=cost_cache,
        )
        if cost_unit is not None:
            cogs_total += cost_unit * qty
            items_with_cost += 1

        items_payload.append({
            "order_id":             order_uuid,
            "client_id":            client_uuid,
            "platform_product_id":  item.product_id,
            "sku":                  item.sku,
            "name":                 item.name,
            "quantity":             qty,
            "unit_price":           unit_price,
            "line_total":           line_total,
            "cost_price_snapshot":  cost_unit,
        })

    # Insert items (best-effort — never raise to caller)
    try:
        if items_payload:
            sb.table("order_items").upsert(
                items_payload,
                on_conflict="order_id,sku" if all(p.get("sku") for p in items_payload) else None,
            ).execute()
    except Exception as exc:
        logger.warning("order_items insert failed: %s", exc)

    # Update orders row with margin columns. Skip if no costs at all to avoid
    # falsely showing 100% margin when COGS aren't loaded yet.
    if items_with_cost == 0:
        return {
            "gross_profit":   None,
            "cogs_total":     None,
            "margin_pct":     None,
            "items_with_cost": 0,
            "items_total":    len(items_payload),
        }

    # Use full order total (incl. shipping/tax) as revenue base for margin calc
    revenue = float(order.total or revenue_from_items)
    gross_profit = round(revenue - cogs_total, 2)
    margin_pct = round((gross_profit / revenue) * 100, 2) if revenue > 0 else None

    try:
        sb.table("orders").update({
            "gross_profit": gross_profit,
            "cogs_total":   round(cogs_total, 2),
            "margin_pct":   margin_pct,
        }).eq("id", order_uuid).execute()
    except Exception as exc:
        logger.warning("order margin update failed: %s", exc)

    return {
        "gross_profit":    gross_profit,
        "cogs_total":      round(cogs_total, 2),
        "margin_pct":      margin_pct,
        "items_with_cost": items_with_cost,
        "items_total":     len(items_payload),
    }


def recompute_all_orders(client_uuid: str, days: int = 365) -> dict:
    """
    Recompute margin for all orders in the window. Useful when the merchant
    just imported their COGS for the first time, or revised them.

    Returns counts of updated/skipped orders.
    """
    from datetime import datetime, timedelta, timezone

    sb     = get_supabase()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    items = (
        sb.table("order_items")
        .select("order_id, sku, platform_product_id, quantity, line_total, unit_price")
        .eq("client_id", client_uuid)
        .gte("created_at", cutoff)
        .execute()
    ).data or []

    if not items:
        return {"updated": 0, "skipped": 0, "missing_cost": 0}

    # Build cost cache once for the whole batch
    cost_cache: dict = {}
    by_order: dict[str, list[dict]] = {}
    for it in items:
        by_order.setdefault(it["order_id"], []).append(it)

    updated = skipped = missing_cost_orders = 0

    for order_id, lines in by_order.items():
        cogs_total = 0.0
        revenue    = 0.0
        items_with_cost = 0

        for line in lines:
            qty   = int(line.get("quantity") or 1)
            total = float(line.get("line_total") or 0)
            revenue += total
            cost_unit = _lookup_cost(
                client_uuid,
                sku=line.get("sku"),
                platform_product_id=line.get("platform_product_id"),
                cost_cache=cost_cache,
            )
            if cost_unit is not None:
                cogs_total += cost_unit * qty
                items_with_cost += 1

        if items_with_cost == 0:
            missing_cost_orders += 1
            continue

        gross_profit = round(revenue - cogs_total, 2)
        margin_pct   = round((gross_profit / revenue) * 100, 2) if revenue > 0 else None

        try:
            sb.table("orders").update({
                "gross_profit": gross_profit,
                "cogs_total":   round(cogs_total, 2),
                "margin_pct":   margin_pct,
            }).eq("id", order_id).execute()
            updated += 1
        except Exception as exc:
            logger.warning("recompute order %s failed: %s", order_id, exc)
            skipped += 1

    return {"updated": updated, "skipped": skipped, "missing_cost": missing_cost_orders}
