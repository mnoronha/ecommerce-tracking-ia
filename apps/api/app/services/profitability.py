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


def recompute_after_refund(order_uuid: str) -> Optional[dict]:
    """
    Recompute gross_profit and margin_pct on an order after a refund lands.

    Net revenue = total_price − Σ refund.amount. We don't refund the COGS on a
    return (no automatic restock signal yet), so gross_profit shrinks by the
    refunded amount.
    """
    if not order_uuid:
        return None
    sb = get_supabase()
    try:
        ord_row = (
            sb.table("orders")
            .select("total_price, cogs_total")
            .eq("id", order_uuid)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.warning("recompute_after_refund: order lookup failed for %s: %s", order_uuid, exc)
        return None
    if not (ord_row and ord_row.data):
        return None
    order = ord_row.data[0]

    try:
        refs = (
            sb.table("refunds").select("amount").eq("order_id", order_uuid).execute()
        ).data or []
    except Exception as exc:
        logger.warning("recompute_after_refund: refunds lookup failed for %s: %s", order_uuid, exc)
        return None

    refund_total = sum(float(r.get("amount") or 0) for r in refs)
    total_price  = float(order.get("total_price") or 0)
    cogs         = order.get("cogs_total")
    if cogs is None:
        # Without COGS we can't compute meaningful margin — record refund total only.
        return {"refund_total": refund_total, "gross_profit": None, "margin_pct": None}

    net_revenue  = max(0.0, total_price - refund_total)
    gross_profit = round(net_revenue - float(cogs), 2)
    margin_pct   = round((gross_profit / net_revenue) * 100, 2) if net_revenue > 0 else None

    try:
        sb.table("orders").update({
            "gross_profit": gross_profit,
            "margin_pct":   margin_pct,
        }).eq("id", order_uuid).execute()
    except Exception as exc:
        logger.warning("recompute_after_refund: order update failed for %s: %s", order_uuid, exc)
    return {
        "refund_total": round(refund_total, 2),
        "net_revenue":  round(net_revenue, 2),
        "gross_profit": gross_profit,
        "margin_pct":   margin_pct,
    }


def backfill_items_from_events(client_uuid: str, pixel_id: str, days: int = 365) -> dict:
    """
    Reconstruct order_items for historical orders that were imported before
    the items pipeline existed. Reads raw_payload (or order_data) from the
    `events` table to get the line items that the Shopify webhook delivered.

    Idempotent — orders that already have items are skipped. Margin is
    recomputed afterwards using whatever product_costs are loaded.
    """
    from datetime import datetime, timedelta, timezone

    sb     = get_supabase()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Orders in scope. Pull a moderately wide window — caller controls `days`.
    orders = (
        sb.table("orders")
        .select("id, platform_order_id, total_price, currency")
        .eq("client_id", client_uuid)
        .gte("created_at", cutoff)
        .execute()
    ).data or []
    if not orders:
        return {"orders_scanned": 0, "items_inserted": 0, "skipped_existing": 0, "no_event": 0}

    order_ids = [o["id"] for o in orders]

    # Skip orders that already have items
    existing_with_items: set[str] = set()
    for i in range(0, len(order_ids), 500):
        chunk = order_ids[i : i + 500]
        r = (
            sb.table("order_items")
            .select("order_id")
            .in_("order_id", chunk)
            .execute()
        ).data or []
        for row in r:
            existing_with_items.add(row["order_id"])

    todo = [o for o in orders if o["id"] not in existing_with_items]
    if not todo:
        return {
            "orders_scanned":   len(orders),
            "items_inserted":   0,
            "skipped_existing": len(existing_with_items),
            "no_event":         0,
            "orders_processed": 0,
        }

    items_inserted    = 0
    no_event          = 0
    orders_processed  = 0
    cost_cache: dict  = {}

    for o in todo:
        # Find the matching event: client_id is TEXT (slug) on `events`
        # and the platform_order_id lives in order_data->>'id'.
        ev = (
            sb.table("events")
            .select("order_data")
            .eq("client_id", pixel_id)
            .filter("order_data->>id", "eq", o["platform_order_id"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        ).data or []
        if not ev:
            no_event += 1
            continue

        order_data = ev[0].get("order_data") or {}
        raw_items  = order_data.get("items") or []
        if not raw_items:
            no_event += 1
            continue

        # Build payloads + compute margin
        cogs_total          = 0.0
        revenue_from_items  = 0.0
        items_with_cost     = 0
        payloads: list[dict] = []

        for it in raw_items:
            qty        = int(it.get("quantity") or 1)
            unit_price = float(it.get("price") or 0)
            line_total = float(it.get("total") or unit_price * qty)
            revenue_from_items += line_total

            cost_unit = _lookup_cost(
                client_uuid,
                sku=it.get("sku"),
                platform_product_id=it.get("product_id"),
                cost_cache=cost_cache,
            )
            if cost_unit is not None:
                cogs_total += cost_unit * qty
                items_with_cost += 1

            payloads.append({
                "order_id":             o["id"],
                "client_id":            client_uuid,
                "platform_product_id":  it.get("product_id"),
                "sku":                  it.get("sku"),
                "name":                 it.get("name"),
                "quantity":             qty,
                "unit_price":           unit_price,
                "line_total":           line_total,
                "cost_price_snapshot":  cost_unit,
            })

        if payloads:
            try:
                sb.table("order_items").insert(payloads).execute()
                items_inserted += len(payloads)
                orders_processed += 1
            except Exception as exc:
                logger.warning("backfill items insert failed for order %s: %s", o["id"], exc)
                continue

        # Update orders row with cogs/profit if any costs were found
        if items_with_cost > 0:
            revenue = float(o.get("total_price") or revenue_from_items)
            gross_profit = round(revenue - cogs_total, 2)
            margin_pct = round((gross_profit / revenue) * 100, 2) if revenue > 0 else None
            try:
                sb.table("orders").update({
                    "gross_profit": gross_profit,
                    "cogs_total":   round(cogs_total, 2),
                    "margin_pct":   margin_pct,
                }).eq("id", o["id"]).execute()
            except Exception as exc:
                logger.debug("margin update failed for order %s: %s", o["id"], exc)

    return {
        "orders_scanned":   len(orders),
        "skipped_existing": len(existing_with_items),
        "orders_processed": orders_processed,
        "items_inserted":   items_inserted,
        "no_event":         no_event,
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
