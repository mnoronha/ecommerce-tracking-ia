"""
COGS (Cost of Goods Sold) management.

Endpoints:
  GET    /cogs/{pixel_id}                — list current costs
  POST   /cogs/{pixel_id}/import         — bulk import from JSON or CSV body
  PUT    /cogs/{pixel_id}/{sku_or_pid}   — update a single SKU
  DELETE /cogs/{pixel_id}/{sku_or_pid}   — remove
  POST   /cogs/{pixel_id}/recompute      — recalc margins on all historical orders
"""

import csv
import io
import logging
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from pydantic import BaseModel

from ..database import get_supabase
from ..services import profitability

logger = logging.getLogger(__name__)
router = APIRouter()


def _resolve_client(pixel_id: str) -> str:
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


class CogsRow(BaseModel):
    sku:                 Optional[str] = None
    platform_product_id: Optional[str] = None
    product_name:        Optional[str] = None
    cost_price:          float
    currency:            str = "BRL"


@router.get("/cogs/{pixel_id}", tags=["cogs"])
async def list_cogs(pixel_id: str, limit: int = 500):
    client_uuid = _resolve_client(pixel_id)
    sb = get_supabase()
    rows = (
        sb.table("product_costs")
        .select("id, sku, platform_product_id, product_name, cost_price, currency, updated_at")
        .eq("client_id", client_uuid)
        .order("updated_at", desc=True)
        .limit(min(limit, 2000))
        .execute()
    ).data or []
    return {"count": len(rows), "rows": rows}


@router.post("/cogs/{pixel_id}/import", tags=["cogs"])
async def import_cogs(
    pixel_id: str,
    body: dict = Body(..., description="Either {csv: '...'} or {rows: [...]}"),
):
    """
    Accepts:
      - {"rows": [{"sku": "...", "cost_price": 99.50}, ...]}
      - {"csv":  "sku,cost_price\\nABC-1,99.50\\n..."}

    CSV columns: sku, platform_product_id, product_name, cost_price, currency
    (any subset; sku OR platform_product_id required)
    """
    client_uuid = _resolve_client(pixel_id)
    rows: List[CogsRow] = []

    if "rows" in body and isinstance(body["rows"], list):
        for raw in body["rows"]:
            try:
                rows.append(CogsRow(**raw))
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid row: {exc}")
    elif "csv" in body and isinstance(body["csv"], str):
        reader = csv.DictReader(io.StringIO(body["csv"]))
        for line_num, row in enumerate(reader, start=2):
            try:
                cost = row.get("cost_price") or row.get("cost") or row.get("custo")
                if not cost:
                    continue
                rows.append(CogsRow(
                    sku=                row.get("sku") or None,
                    platform_product_id=row.get("platform_product_id")
                                        or row.get("product_id") or None,
                    product_name=       row.get("product_name") or row.get("name"),
                    cost_price=         float(str(cost).replace(",", ".")),
                    currency=           row.get("currency") or "BRL",
                ))
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"CSV line {line_num}: {exc}",
                )
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide either 'rows' (list) or 'csv' (string) in the request body",
        )

    if not rows:
        return {"inserted": 0, "updated": 0, "skipped": 0}

    sb = get_supabase()
    inserted = updated = skipped = 0

    for row in rows:
        if not (row.sku or row.platform_product_id):
            skipped += 1
            continue
        payload = {
            "client_id":           client_uuid,
            "sku":                 row.sku,
            "platform_product_id": row.platform_product_id,
            "product_name":        row.product_name,
            "cost_price":          row.cost_price,
            "currency":            row.currency,
            "updated_at":          "now()",
        }
        try:
            on_conflict = "client_id,sku" if row.sku else "client_id,platform_product_id"
            r = sb.table("product_costs").upsert(payload, on_conflict=on_conflict).execute()
            if r.data:
                inserted += 1  # upsert doesn't tell us insert vs update — count as inserted
        except Exception as exc:
            logger.warning("cogs upsert failed: %s", exc)
            skipped += 1

    return {"inserted": inserted, "updated": updated, "skipped": skipped, "total": len(rows)}


@router.delete("/cogs/{pixel_id}/{cost_id}", tags=["cogs"])
async def delete_cogs(pixel_id: str, cost_id: str):
    client_uuid = _resolve_client(pixel_id)
    sb = get_supabase()
    sb.table("product_costs").delete().eq("client_id", client_uuid).eq("id", cost_id).execute()
    return {"deleted": True}


@router.post("/cogs/{pixel_id}/recompute", tags=["cogs"])
async def recompute_margins(
    pixel_id: str,
    background_tasks: BackgroundTasks,
    days: int = 365,
):
    """
    Recalculates gross_profit on every order in the window using current
    product_costs. Runs in the background; clients can poll the dashboard
    to see margins update.
    """
    client_uuid = _resolve_client(pixel_id)
    background_tasks.add_task(profitability.recompute_all_orders, client_uuid, days)
    return {"status": "recompute_started", "days": days}


@router.get("/cogs/{pixel_id}/coverage", tags=["cogs"])
async def coverage_report(pixel_id: str, days: int = 30):
    """
    What % of orders / revenue has COGS data registered? Helps the merchant
    know if their import is complete enough to trust margin reports.
    """
    from datetime import datetime, timedelta, timezone

    client_uuid = _resolve_client(pixel_id)
    sb = get_supabase()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    orders = (
        sb.table("orders")
        .select("id, total_price, gross_profit, financial_status")
        .eq("client_id", client_uuid)
        .eq("financial_status", "paid")
        .gte("created_at", cutoff)
        .execute()
    ).data or []

    total_orders     = len(orders)
    with_margin      = sum(1 for o in orders if o.get("gross_profit") is not None)
    revenue_total    = sum(float(o.get("total_price") or 0) for o in orders)
    revenue_with_cogs = sum(
        float(o.get("total_price") or 0)
        for o in orders if o.get("gross_profit") is not None
    )

    return {
        "days":               days,
        "total_orders":       total_orders,
        "orders_with_margin": with_margin,
        "orders_pct":         round(with_margin / total_orders * 100, 1) if total_orders else 0,
        "revenue_total":      round(revenue_total, 2),
        "revenue_with_cogs":  round(revenue_with_cogs, 2),
        "revenue_pct":        round(revenue_with_cogs / revenue_total * 100, 1) if revenue_total else 0,
    }
