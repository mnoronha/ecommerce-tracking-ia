"""
Meta Custom Audiences REST API.

Endpoints to trigger audience sync and check sync status.
All endpoints are protected by pixel_id (resolves to client_uuid).
"""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..database import get_supabase
from ..services import crypto, meta_audiences

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audiences", tags=["audiences"])


def _resolve_client_uuid(pixel_id: str) -> str:
    """Resolve pixel_id to client uuid. Raises 404 if not found."""
    try:
        result = (
            get_supabase().table("clients")
            .select("id, meta_ad_account_id, meta_access_token")
            .eq("pixel_id", pixel_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        if result.data:
            return crypto.decrypt_client_secrets(result.data[0])
    except Exception as exc:
        logger.warning("_resolve_client: %s", exc)
    raise HTTPException(status_code=404, detail=f"Client '{pixel_id}' not found")


@router.get("/{pixel_id}/status", summary="Get sync status for all audience types")
async def get_audience_status(pixel_id: str):
    """Return sync status for all 5 audience types."""
    row = _resolve_client_uuid(pixel_id)
    status = meta_audiences.get_sync_status(row["id"])
    return {"pixel_id": pixel_id, "audiences": status}


@router.post("/{pixel_id}/sync", summary="Trigger sync for all audience types")
async def sync_all(pixel_id: str, background_tasks: BackgroundTasks):
    """
    Trigger a full sync of all 5 audience types to Meta.
    Runs in background — returns immediately.
    """
    row = _resolve_client_uuid(pixel_id)

    if not row.get("meta_ad_account_id"):
        raise HTTPException(status_code=422, detail="meta_ad_account_id not configured for this client")
    if not row.get("meta_access_token"):
        raise HTTPException(status_code=422, detail="meta_access_token not configured for this client")

    background_tasks.add_task(
        meta_audiences.sync_all_audiences,
        row["id"],
        row["meta_ad_account_id"],
        row["meta_access_token"],
    )
    return {
        "status": "queued",
        "pixel_id": pixel_id,
        "message": "Sync queued for all 5 audience types",
    }


@router.post("/{pixel_id}/sync/{audience_type}", summary="Trigger sync for one audience type")
async def sync_one(pixel_id: str, audience_type: str, background_tasks: BackgroundTasks):
    """
    Trigger sync for a single audience type.
    audience_type: high_ltv | cart_abandoners | recent_buyers | top_customers | inactive
    """
    if audience_type not in meta_audiences.AUDIENCE_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown audience type '{audience_type}'. Valid: {', '.join(meta_audiences.AUDIENCE_CONFIGS)}",
        )

    row = _resolve_client_uuid(pixel_id)

    if not row.get("meta_ad_account_id"):
        raise HTTPException(status_code=422, detail="meta_ad_account_id not configured for this client")
    if not row.get("meta_access_token"):
        raise HTTPException(status_code=422, detail="meta_access_token not configured for this client")

    background_tasks.add_task(
        meta_audiences.sync_audience,
        row["id"],
        row["meta_ad_account_id"],
        row["meta_access_token"],
        audience_type,
    )
    return {
        "status": "queued",
        "pixel_id": pixel_id,
        "audience_type": audience_type,
        "message": f"Sync queued for '{audience_type}'",
    }
