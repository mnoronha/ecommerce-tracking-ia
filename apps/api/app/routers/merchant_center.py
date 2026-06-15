"""
Merchant Center router.

  GET  /merchant-center/{pixel_id}/summary          — snapshot mais recente + trend 30d
  GET  /merchant-center/{pixel_id}/products         — lista produtos paginada
  GET  /merchant-center/{pixel_id}/issues           — top issues agrupados por código
  GET  /merchant-center/{pixel_id}/pricing          — price competitiveness summary
  GET  /merchant-center/{pixel_id}/health-history   — histórico de feed health score
  POST /merchant-center/{pixel_id}/sync             — força sync imediato
  POST /merchant-center/{pixel_id}/setup            — salva merchant_id + refresh_token
  GET  /merchant-center/{pixel_id}/suggestions      — sugestões de otimização da IA
  PATCH /merchant-center/suggestions/{id}           — atualiza status da sugestão
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..database import get_supabase
from ..services import merchant_center as svc
from ..services import crypto

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/merchant-center", tags=["merchant-center"])


def _get_client(pixel_id: str) -> dict:
    row = (
        get_supabase()
        .table("clients")
        .select("id,pixel_id,name,merchant_center_id,merchant_center_refresh_token")
        .eq("pixel_id", pixel_id)
        .limit(1)
        .execute()
    ).data
    if not row:
        raise HTTPException(status_code=404, detail="client not found")
    return crypto.decrypt_client_secrets(row[0])


# ── Setup ─────────────────────────────────────────────────────────────────────

class MerchantSetupPayload(BaseModel):
    merchant_id:    str
    refresh_token:  str


@router.post("/{pixel_id}/setup", summary="Configura Merchant Center para o cliente")
async def setup_merchant(pixel_id: str, body: MerchantSetupPayload):
    c  = _get_client(pixel_id)
    sb = get_supabase()
    sb.table("clients").update({
        "merchant_center_id":            body.merchant_id,
        "merchant_center_refresh_token": crypto.encrypt_secret(body.refresh_token),
    }).eq("id", c["id"]).execute()
    return {"ok": True, "merchant_id": body.merchant_id}


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/summary", summary="Snapshot mais recente do feed")
async def get_summary(pixel_id: str):
    c = _get_client(pixel_id)
    if not c.get("merchant_center_id"):
        return {"configured": False}

    snapshot = svc.get_latest_snapshot(c["id"])
    if not snapshot:
        return {"configured": True, "has_data": False}

    history = svc.get_feed_health(c["id"], days=7)
    return {
        "configured": True,
        "has_data":   True,
        "snapshot":   snapshot,
        "trend_7d":   history,
    }


@router.get("/{pixel_id}/health-history", summary="Histórico do Feed Health Score")
async def get_health_history(
    pixel_id: str,
    days: int = Query(30, ge=7, le=90),
):
    c = _get_client(pixel_id)
    return svc.get_feed_health(c["id"], days=days)


@router.get("/{pixel_id}/products", summary="Produtos do catálogo")
async def get_products(
    pixel_id:   str,
    date:       Optional[str] = Query(None, description="YYYY-MM-DD (default: último snapshot)"),
    status:     Optional[str] = Query(None),
    availability: Optional[str] = Query(None),
    page:       int = Query(1, ge=1),
    per_page:   int = Query(50, ge=10, le=200),
):
    c = _get_client(pixel_id)
    if not date:
        snap = svc.get_latest_snapshot(c["id"])
        if not snap:
            return {"products": [], "page": 1, "per_page": per_page}
        date = snap["snapshot_date"]
    return svc.get_products(c["id"], date, status, availability, page, per_page)


@router.get("/{pixel_id}/issues", summary="Issues agrupadas por código")
async def get_issues(
    pixel_id: str,
    date:     Optional[str] = Query(None),
):
    c = _get_client(pixel_id)
    if not date:
        snap = svc.get_latest_snapshot(c["id"])
        if not snap:
            return []
        date = snap["snapshot_date"]
    return svc.get_top_issues(c["id"], date)


@router.get("/{pixel_id}/pricing", summary="Price competitiveness summary")
async def get_pricing(
    pixel_id: str,
    date:     Optional[str] = Query(None),
):
    c = _get_client(pixel_id)
    if not date:
        snap = svc.get_latest_snapshot(c["id"])
        if not snap:
            return {"total_with_benchmark": 0}
        date = snap["snapshot_date"]
    return svc.get_price_summary(c["id"], date)


# ── Manual sync ───────────────────────────────────────────────────────────────

@router.post("/{pixel_id}/sync", summary="Força sincronização imediata")
async def force_sync(pixel_id: str):
    c = _get_client(pixel_id)
    if not c.get("merchant_center_id"):
        raise HTTPException(status_code=400, detail="merchant center not configured")
    result = svc.sync_client(c["id"])
    if result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])
    return result


# ── Suggestions ───────────────────────────────────────────────────────────────

@router.get("/{pixel_id}/suggestions", summary="Sugestões de otimização da IA")
async def get_suggestions(
    pixel_id: str,
    status:   str = Query("pending"),
):
    c  = _get_client(pixel_id)
    sb = get_supabase()
    q  = (
        sb.table("merchant_optimization_suggestions")
        .select("*")
        .eq("client_id", c["id"])
        .order("generated_at", desc=True)
    )
    if status != "all":
        q = q.eq("status", status)
    return q.limit(50).execute().data or []


class SuggestionUpdate(BaseModel):
    status: str  # 'applied' | 'dismissed'


@router.patch("/suggestions/{suggestion_id}", summary="Atualiza status de sugestão")
async def update_suggestion(suggestion_id: str, body: SuggestionUpdate):
    sb = get_supabase()
    from datetime import datetime, timezone
    update = {"status": body.status}
    if body.status == "applied":
        update["applied_at"] = datetime.now(timezone.utc).isoformat()
    sb.table("merchant_optimization_suggestions").update(update).eq("id", suggestion_id).execute()
    return {"ok": True}
