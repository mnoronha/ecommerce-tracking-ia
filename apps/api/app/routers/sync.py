"""
Rotas de sincronização manual via API.

POST /sync/shopify/{pixel_id}           — dispara sync imediato para um cliente
POST /sync/shopify/{pixel_id}/backfill  — sync completo sem filtro de data
GET  /sync/shopify/{pixel_id}/status    — retorna last_sync_at e estado
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..database import get_supabase
from ..services import crypto
from ..services import shopify_sync

router = APIRouter(prefix="/sync", tags=["sync"])


def _get_client(pixel_id: str) -> dict:
    sb = get_supabase()
    rows = (
        sb.table("clients")
        .select(
            "id, pixel_id, name, shopify_domain, shopify_access_token, "
            "shopify_sync_enabled, shopify_last_sync_at, is_active"
        )
        .eq("pixel_id", pixel_id)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Client not found")
    return rows[0]


@router.post("/shopify/{pixel_id}", summary="Trigger Shopify API sync")
async def trigger_shopify_sync(
    pixel_id: str,
    since: Optional[str] = Query(
        None,
        description="ISO 8601 datetime. If omitted, uses last_sync_at or 7 days ago.",
    ),
):
    """
    Dispara uma sincronização imediata de pedidos via Shopify Admin API.
    Pode ser chamado para qualquer cliente Shopify — não exige shopify_sync_enabled.
    """
    row = _get_client(pixel_id)
    client = crypto.decrypt_client_secrets(row)

    since_dt: Optional[datetime] = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid since date format")

    result = shopify_sync.sync_client(client, since=since_dt)
    return {
        "pixel_id": pixel_id,
        "client_name": row.get("name"),
        **result,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/shopify/{pixel_id}/backfill", summary="Full Shopify backfill")
async def trigger_shopify_backfill(pixel_id: str):
    """
    Importa TODOS os pedidos pagos desde sempre.
    Use apenas uma vez para novos clientes ou para reconstruir dados históricos.
    """
    row = _get_client(pixel_id)
    client = crypto.decrypt_client_secrets(row)
    result = shopify_sync.sync_client(client, full_backfill=True)
    return {
        "pixel_id": pixel_id,
        "client_name": row.get("name"),
        **result,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/shopify/{pixel_id}/status", summary="Shopify sync status")
async def shopify_sync_status(pixel_id: str):
    row = _get_client(pixel_id)
    return {
        "pixel_id":              pixel_id,
        "client_name":           row.get("name"),
        "shopify_domain":        row.get("shopify_domain"),
        "shopify_sync_enabled":  row.get("shopify_sync_enabled", False),
        "shopify_last_sync_at":  row.get("shopify_last_sync_at"),
        "is_active":             row.get("is_active", True),
    }


@router.patch("/shopify/{pixel_id}/enable", summary="Enable/disable Shopify API sync")
async def toggle_shopify_sync(pixel_id: str, enabled: bool = Query(...)):
    """Ativa ou desativa o polling horário para um cliente."""
    row = _get_client(pixel_id)
    get_supabase().table("clients").update(
        {"shopify_sync_enabled": enabled}
    ).eq("id", row["id"]).execute()
    return {"pixel_id": pixel_id, "shopify_sync_enabled": enabled}
