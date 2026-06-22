"""
Alert endpoints.

  GET    /api/v1/alerts
  GET    /api/v1/alerts/{id}
  POST   /api/v1/alerts
  PATCH  /api/v1/alerts/{id}
  DELETE /api/v1/alerts/{id}
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ....database import get_supabase
from ..deps import ApiKey, get_request_id, log_request
from ..pagination import paginated_response, single_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alerts", tags=["alerts"])

_COLS = (
    "id, client_id, type, severity, title, message, data, "
    "status, is_resolved, occurrence_count, "
    "created_at, resolved_at, silenced_until"
)


def _serialize_alert(row: dict, client_name: Optional[str] = None) -> dict:
    return {
        "id":               row["id"],
        "client_id":        row.get("client_id"),
        "client_name":      client_name,
        "type":             row.get("type"),
        "severity":         row.get("severity") or "warning",
        "title":            row.get("title") or "",
        "message":          row.get("message") or "",
        "data":             row.get("data") or {},
        "suggested_action": (row.get("data") or {}).get("suggested_action"),
        "status":           row.get("status") or ("resolved" if row.get("is_resolved") else "active"),
        "occurrence_count": row.get("occurrence_count") or 1,
        "created_at":       row.get("created_at"),
        "resolved_at":      row.get("resolved_at"),
        "silenced_until":   row.get("silenced_until"),
    }


def _get_client_names(client_ids: list[str]) -> dict[str, str]:
    if not client_ids:
        return {}
    sb = get_supabase()
    rows = (
        sb.table("clients")
        .select("id, name")
        .in_("id", list(set(client_ids)))
        .execute()
    ).data or []
    return {r["id"]: r["name"] for r in rows}


# ── List ────────────────────────────────────────────────────────────────────

@router.get("")
async def list_alerts(
    request: Request,
    key: ApiKey,
    status: str = Query("active", enum=["active", "resolved", "silenced", "all"]),
    severity: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    client_id: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
):
    req_id = get_request_id(request)
    t0 = datetime.now(timezone.utc)

    # Scope restriction
    if key.scope_type == "client":
        client_id = key.scope_client_id

    sb = get_supabase()
    q = sb.table("alerts").select(_COLS, count="exact")

    if status == "active":
        q = q.eq("is_resolved", False)
    elif status == "resolved":
        q = q.eq("is_resolved", True)
    elif status != "all":
        q = q.eq("status", status)

    if severity:
        q = q.eq("severity", severity)
    if type:
        q = q.eq("type", type)
    if client_id:
        q = q.eq("client_id", client_id)
    if since:
        q = q.gte("created_at", since)

    if cursor:
        from ..pagination import decode_cursor
        c = decode_cursor(cursor)
        if c.get("created_at"):
            q = q.lt("created_at", c["created_at"])

    res = q.order("created_at", desc=True).limit(limit).execute()
    rows  = res.data or []
    total = res.count or 0

    names = _get_client_names([r["client_id"] for r in rows if r.get("client_id")])
    data  = [_serialize_alert(r, names.get(r.get("client_id"))) for r in rows]

    ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    log_request(request, 200, ms)
    return paginated_response(data, total, limit, request_id=req_id)


# ── Detail ──────────────────────────────────────────────────────────────────

@router.get("/{alert_id}")
async def get_alert(request: Request, alert_id: str, key: ApiKey):
    req_id = get_request_id(request)
    t0 = datetime.now(timezone.utc)

    sb = get_supabase()
    rows = (
        sb.table("alerts")
        .select(_COLS)
        .eq("id", alert_id)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(404, "Alert not found")

    row = rows[0]
    if key.scope_type == "client" and row.get("client_id") != key.scope_client_id:
        raise HTTPException(403, "Alert not in scope")

    names = _get_client_names([row["client_id"]] if row.get("client_id") else [])
    ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    log_request(request, 200, ms, client_id=row.get("client_id"))
    return single_response(_serialize_alert(row, names.get(row.get("client_id"))), req_id)


# ── Create ───────────────────────────────────────────────────────────────────

class CreateAlertRequest(BaseModel):
    client_id: str
    type: str
    severity: str = "warning"
    title: str
    message: str
    data: Optional[dict] = None
    suggested_action: Optional[str] = None


@router.post("", status_code=201)
async def create_alert(request: Request, key: ApiKey, body: CreateAlertRequest):
    key.assert_write()
    req_id = get_request_id(request)
    t0 = datetime.now(timezone.utc)

    sb = get_supabase()
    alert_data = body.data or {}
    if body.suggested_action:
        alert_data["suggested_action"] = body.suggested_action

    row = (
        sb.table("alerts")
        .insert({
            "client_id":        body.client_id,
            "type":             body.type,
            "severity":         body.severity,
            "title":            body.title,
            "message":          body.message,
            "data":             alert_data,
            "status":           "active",
            "is_resolved":      False,
            "occurrence_count": 1,
        })
        .execute()
    ).data[0]

    ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    log_request(request, 201, ms, client_id=body.client_id)
    return single_response(_serialize_alert(row), req_id)


# ── Update ───────────────────────────────────────────────────────────────────

class PatchAlertRequest(BaseModel):
    status: Optional[str] = None
    resolution_notes: Optional[str] = None
    silenced_until: Optional[str] = None
    silenced_reason: Optional[str] = None


@router.patch("/{alert_id}")
async def patch_alert(request: Request, alert_id: str, key: ApiKey, body: PatchAlertRequest):
    key.assert_write()
    req_id = get_request_id(request)
    t0 = datetime.now(timezone.utc)

    sb = get_supabase()
    rows = (
        sb.table("alerts").select("id, client_id, data").eq("id", alert_id).limit(1).execute()
    ).data or []
    if not rows:
        raise HTTPException(404, "Alert not found")

    row = rows[0]
    if key.scope_type == "client" and row.get("client_id") != key.scope_client_id:
        raise HTTPException(403, "Alert not in scope")

    update: dict = {}
    now = datetime.now(timezone.utc).isoformat()

    if body.status == "resolved":
        update["is_resolved"]  = True
        update["resolved_at"]  = now
        update["status"]       = "resolved"
        if body.resolution_notes:
            d = row.get("data") or {}
            d["resolution_notes"] = body.resolution_notes
            update["data"] = d
    elif body.status == "silenced":
        update["status"] = "silenced"
        if body.silenced_until:
            update["silenced_until"] = body.silenced_until
    elif body.status:
        update["status"] = body.status

    if update:
        sb.table("alerts").update(update).eq("id", alert_id).execute()

    result = (
        sb.table("alerts").select(_COLS).eq("id", alert_id).limit(1).execute()
    ).data[0]

    ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    log_request(request, 200, ms, client_id=row.get("client_id"))
    return single_response(_serialize_alert(result), req_id)


# ── Soft delete ──────────────────────────────────────────────────────────────

@router.delete("/{alert_id}", status_code=204)
async def delete_alert(request: Request, alert_id: str, key: ApiKey):
    key.assert_write()
    sb = get_supabase()
    rows = (
        sb.table("alerts").select("id, client_id").eq("id", alert_id).limit(1).execute()
    ).data or []
    if not rows:
        raise HTTPException(404, "Alert not found")
    row = rows[0]
    if key.scope_type == "client" and row.get("client_id") != key.scope_client_id:
        raise HTTPException(403, "Alert not in scope")
    sb.table("alerts").update({"is_resolved": True, "status": "resolved"}).eq("id", alert_id).execute()
    log_request(request, 204, 0, client_id=row.get("client_id"))
