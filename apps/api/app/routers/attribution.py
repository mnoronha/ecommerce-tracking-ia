"""
Attribution endpoints — drives the Unified Attribution panel in the dashboard.
"""

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from ..database import get_supabase
from ..services import attribution_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/attribution", tags=["attribution"])


def _resolve_client_uuid(pixel_id: str) -> Optional[str]:
    res = (
        get_supabase().table("clients")
        .select("id")
        .eq("pixel_id", pixel_id)
        .eq("is_active", True)
        .maybe_single()
        .execute()
    )
    return res.data["id"] if (res and res.data) else None


@router.post("/{pixel_id}/recompute")
async def recompute(pixel_id: str, background_tasks: BackgroundTasks, days: int = 90):
    """
    Trigger background re-attribution of all paid orders in the last N days.
    Returns immediately; computation continues in the background.
    """
    cuuid = _resolve_client_uuid(pixel_id)
    if not cuuid:
        raise HTTPException(404, f"Client not found: {pixel_id}")
    background_tasks.add_task(attribution_engine.recompute_for_client, cuuid, days)
    return {"status": "started", "client_id": cuuid, "days": days}


@router.get("/{pixel_id}/summary")
async def summary(
    pixel_id: str,
    model: str = Query("last_click", regex="^(last_click|first_click|linear|time_decay|position_based)$"),
    days:  int = Query(30, ge=1, le=365),
):
    """
    Return aggregated attribution by platform + source for the dashboard panel.
    """
    cuuid = _resolve_client_uuid(pixel_id)
    if not cuuid:
        raise HTTPException(404, f"Client not found: {pixel_id}")
    return attribution_engine.get_summary(cuuid, model=model, days=days)
