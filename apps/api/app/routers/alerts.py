"""
Alerts router — read alerts + manual trigger of the alert engine.

  GET  /alerts/{pixel_id}        — list open alerts for a client
  GET  /alerts                   — list open alerts for all clients of an agency
  POST /alerts/run               — manually trigger the engine (returns counters)
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..database import get_supabase
from ..services import alert_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.post("/run", summary="Manually run the alert engine")
async def trigger_alert_engine():
    """
    Runs all enabled alert_rules now and returns counters. Useful after editing
    a rule's config or seeding goals/budgets — surfaces new alerts immediately
    instead of waiting for the 30-min cron tick.
    """
    return alert_engine.run_alert_engine()


@router.get("/{pixel_id}", summary="List open alerts for a client")
async def list_alerts_for_client(
    pixel_id: str,
    include_resolved: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
):
    sb = get_supabase()
    client = (
        sb.table("clients").select("id, agency_id")
        .eq("pixel_id", pixel_id).limit(1).execute()
    )
    if not (client and client.data):
        raise HTTPException(status_code=404, detail="client not found")

    q = (
        sb.table("alerts")
        .select("id, severity, fingerprint, title, message, data, created_at, resolved_at, alert_rule_id")
        .eq("client_id", client.data[0]["id"])
        .order("created_at", desc=True)
        .limit(limit)
    )
    if not include_resolved:
        q = q.is_("resolved_at", "null")
    rows = q.execute().data or []
    return {"alerts": rows, "count": len(rows)}


@router.get("", summary="List open alerts across all clients of an agency")
async def list_alerts_for_agency(
    agency_slug: str = Query(..., description="Agency slug (e.g. 'pareto-plus')"),
    include_resolved: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
):
    sb = get_supabase()
    agency = (
        sb.table("agencies").select("id, slug")
        .eq("slug", agency_slug).limit(1).execute()
    )
    if not (agency and agency.data):
        raise HTTPException(status_code=404, detail="agency not found")

    q = (
        sb.table("alerts")
        .select("id, client_id, severity, fingerprint, title, message, data, created_at, resolved_at, alert_rule_id")
        .eq("agency_id", agency.data[0]["id"])
        .order("created_at", desc=True)
        .limit(limit)
    )
    if not include_resolved:
        q = q.is_("resolved_at", "null")
    rows = q.execute().data or []
    return {"alerts": rows, "count": len(rows)}
