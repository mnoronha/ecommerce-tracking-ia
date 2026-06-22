"""
Client endpoints.

  GET    /api/v1/clients
  GET    /api/v1/clients/{id}
  POST   /api/v1/clients
  PATCH  /api/v1/clients/{id}
  DELETE /api/v1/clients/{id}   (soft-archive)
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
router = APIRouter(prefix="/clients", tags=["clients"])

# ── Helpers ──────────────────────────────────────────────────────────────────

_SAFE_COLS = (
    "id, name, pixel_id, ecommerce_platform, client_type, is_active, logo_url, "
    "created_at, updated_at, "
    "meta_pixel_id, meta_ad_account_id, meta_token_health, "
    "google_ads_customer_id, google_ads_token_health, "
    "ga4_property_id, ga4_health, "
    "shopify_domain, shopify_health, shopify_last_sync_at, "
    "merchant_center_id, merchant_center_last_sync_at, "
    "notification_email, alert_emails, "
    "monthly_revenue_goal, monthly_ad_spend_goal, target_roas, cpa_target, "
    "meta_ads_budget, google_ads_budget, "
    "weekly_report_enabled, monthly_report_enabled"
)


def _serialize_client(c: dict) -> dict:
    """Map DB row to public API shape."""
    status = "active" if c.get("is_active") else "archived"

    integrations = {
        "meta_ads": {
            "connected": bool(c.get("meta_pixel_id") and c.get("meta_ad_account_id")),
            "account_id": c.get("meta_ad_account_id"),
            "health": c.get("meta_token_health"),
        },
        "google_ads": {
            "connected": bool(c.get("google_ads_customer_id")),
            "account_id": c.get("google_ads_customer_id"),
            "health": c.get("google_ads_token_health"),
        },
        "ga4": {
            "connected": bool(c.get("ga4_property_id")),
            "property_id": c.get("ga4_property_id"),
            "health": c.get("ga4_health"),
        },
        "shopify": {
            "connected": bool(c.get("shopify_domain")),
            "shop_url": c.get("shopify_domain"),
            "last_sync": c.get("shopify_last_sync_at"),
            "health": c.get("shopify_health"),
        },
        "merchant_center": {
            "connected": bool(c.get("merchant_center_id")),
            "account_id": c.get("merchant_center_id"),
            "last_sync": c.get("merchant_center_last_sync_at"),
        },
    }

    # Simple connectivity-based health score (0-100)
    connected = sum(1 for v in integrations.values() if v.get("connected"))
    healthy = sum(
        1 for v in integrations.values()
        if v.get("connected") and v.get("health") in ("healthy", None)
    )
    health_score = round((healthy / max(connected, 1)) * 100) if connected else 0

    goals = {
        "revenue_target": str(c["monthly_revenue_goal"]) if c.get("monthly_revenue_goal") else None,
        "roas_target":    str(c["target_roas"]) if c.get("target_roas") else None,
        "cpa_target":     str(c["cpa_target"]) if c.get("cpa_target") else None,
        "budget_meta":    str(c["meta_ads_budget"]) if c.get("meta_ads_budget") else None,
        "budget_google":  str(c["google_ads_budget"]) if c.get("google_ads_budget") else None,
        "budget_total":   str(
            (c.get("meta_ads_budget") or 0) + (c.get("google_ads_budget") or 0) or None
        ) if (c.get("meta_ads_budget") or c.get("google_ads_budget")) else None,
    }

    emails = c.get("alert_emails") or []
    if isinstance(emails, list):
        contacts = [{"email": e, "receives_reports": True, "receives_alerts": True}
                    for e in emails if e]
    else:
        contacts = []
    if c.get("notification_email") and c["notification_email"] not in emails:
        contacts.insert(0, {
            "email": c["notification_email"],
            "receives_reports": True,
            "receives_alerts": True,
        })

    return {
        "id":           c["id"],
        "name":         c["name"],
        "slug":         c.get("pixel_id") or c["id"],
        "type":         c.get("client_type") or "ecommerce",
        "status":       status,
        "logo_url":     c.get("logo_url"),
        "platform":     c.get("ecommerce_platform"),
        "health_score": health_score,
        "health_status": "healthy" if health_score >= 70 else ("warning" if health_score >= 40 else "critical"),
        "integrations":         integrations,
        "current_month_goals":  goals,
        "contacts":             contacts,
        "created_at":   c.get("created_at"),
        "updated_at":   c.get("updated_at") or c.get("created_at"),
    }


def _get_client_or_404(client_id: str) -> dict:
    sb = get_supabase()
    rows = (
        sb.table("clients")
        .select(_SAFE_COLS)
        .eq("id", client_id)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(404, "Client not found")
    return rows[0]


# ── List ────────────────────────────────────────────────────────────────────

@router.get("")
async def list_clients(
    request: Request,
    key: ApiKey,
    status: str = Query("active", enum=["active", "paused", "archived", "all"]),
    type: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    sort: str = Query("name", enum=["name", "created_at", "health_score"]),
    order: str = Query("asc", enum=["asc", "desc"]),
):
    req_id = get_request_id(request)
    t0 = datetime.now(timezone.utc)

    sb = get_supabase()
    q = sb.table("clients").select(_SAFE_COLS, count="exact")

    if status == "active":
        q = q.eq("is_active", True)
    elif status == "archived":
        q = q.eq("is_active", False)
    elif status != "all":
        q = q.eq("is_active", True)

    if type:
        q = q.eq("client_type", type)

    # Key scope restriction
    if key.scope_type == "client" and key.scope_client_id:
        q = q.eq("id", key.scope_client_id)

    if cursor:
        from ..pagination import decode_cursor
        c = decode_cursor(cursor)
        if c.get("id"):
            q = q.gt("id", c["id"])

    desc = order == "desc"
    q = q.order("name" if sort == "name" else "created_at", desc=desc).limit(limit)

    res = q.execute()
    rows = res.data or []
    total = res.count or 0

    serialized = [_serialize_client(r) for r in rows]
    ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    log_request(request, 200, ms)
    return paginated_response(serialized, total, limit, request_id=req_id)


# ── Detail ──────────────────────────────────────────────────────────────────

@router.get("/{client_id}")
async def get_client(request: Request, client_id: str, key: ApiKey):
    req_id = get_request_id(request)
    t0 = datetime.now(timezone.utc)
    key.assert_client_scope(client_id)
    row = _get_client_or_404(client_id)
    ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    log_request(request, 200, ms, client_id=client_id)
    return single_response(_serialize_client(row), req_id)


# ── Create ───────────────────────────────────────────────────────────────────

class CreateClientRequest(BaseModel):
    name: str
    slug: Optional[str] = None
    type: Optional[str] = "ecommerce"
    platform: Optional[str] = None
    website_url: Optional[str] = None
    notification_email: Optional[str] = None


@router.post("", status_code=201)
async def create_client(request: Request, key: ApiKey, body: CreateClientRequest):
    key.assert_write()
    req_id = get_request_id(request)
    t0 = datetime.now(timezone.utc)

    sb = get_supabase()
    pixel_id = body.slug or body.name.lower().replace(" ", "-")

    # Check slug uniqueness
    exists = (
        sb.table("clients").select("id").eq("pixel_id", pixel_id).limit(1).execute()
    ).data
    if exists:
        raise HTTPException(409, f"Client with slug '{pixel_id}' already exists")

    row = (
        sb.table("clients")
        .insert({
            "name": body.name,
            "pixel_id": pixel_id,
            "client_type": body.type,
            "ecommerce_platform": body.platform,
            "notification_email": body.notification_email,
            "is_active": True,
        })
        .execute()
    ).data[0]

    # Re-fetch with full columns
    full = _get_client_or_404(row["id"])
    ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    log_request(request, 201, ms, client_id=full["id"])
    return single_response(_serialize_client(full), req_id)


# ── Update ───────────────────────────────────────────────────────────────────

class PatchClientRequest(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None
    notification_email: Optional[str] = None
    current_month_goals: Optional[dict] = None


@router.patch("/{client_id}")
async def patch_client(request: Request, client_id: str, key: ApiKey, body: PatchClientRequest):
    key.assert_write()
    key.assert_client_scope(client_id)
    req_id = get_request_id(request)
    t0 = datetime.now(timezone.utc)

    _get_client_or_404(client_id)  # 404 guard

    sb = get_supabase()
    update: dict = {}
    if body.name is not None:
        update["name"] = body.name
    if body.notification_email is not None:
        update["notification_email"] = body.notification_email
    if body.current_month_goals:
        g = body.current_month_goals
        if "revenue_target" in g:
            update["monthly_revenue_goal"] = float(g["revenue_target"])
        if "roas_target" in g:
            update["target_roas"] = float(g["roas_target"])
        if "cpa_target" in g:
            update["cpa_target"] = float(g["cpa_target"])
        if "budget_meta" in g:
            update["meta_ads_budget"] = float(g["budget_meta"])
        if "budget_google" in g:
            update["google_ads_budget"] = float(g["budget_google"])

    if update:
        sb.table("clients").update(update).eq("id", client_id).execute()

    full = _get_client_or_404(client_id)
    ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    log_request(request, 200, ms, client_id=client_id)
    return single_response(_serialize_client(full), req_id)


# ── Archive (soft delete) ────────────────────────────────────────────────────

@router.delete("/{client_id}", status_code=204)
async def archive_client(request: Request, client_id: str, key: ApiKey):
    key.assert_write()
    key.assert_client_scope(client_id)
    _get_client_or_404(client_id)
    sb = get_supabase()
    sb.table("clients").update({"is_active": False}).eq("id", client_id).execute()
    log_request(request, 204, 0, client_id=client_id)
