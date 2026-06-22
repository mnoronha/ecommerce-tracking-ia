"""
Health, /me, and API key management endpoints.

  GET  /api/v1/health          — no auth
  GET  /api/v1/me              — auth
  GET  /api/v1/api-keys        — auth, full_access
  POST /api/v1/api-keys        — auth, full_access
  POST /api/v1/api-keys/{id}/rotate  — auth, full_access
  DELETE /api/v1/api-keys/{id}       — auth, full_access
"""

import logging
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel

from ....database import get_supabase
from ..deps import ApiKey, generate_api_key, get_request_id, hash_key, log_request
from ..pagination import single_response

logger = logging.getLogger(__name__)
router = APIRouter(tags=["meta"])


# ── Health ─────────────────────────────────────────────────────────────────────

@router.get("/health", include_in_schema=True)
async def api_health():
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    db_status = "healthy"
    try:
        sb = get_supabase()
        sb.table("clients").select("id").limit(1).execute()
    except Exception as exc:
        db_status = f"error: {exc}"

    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "version": "1.0.0",
        "timestamp": now,
        "dependencies": {
            "database": db_status,
        },
    }


# ── Me ─────────────────────────────────────────────────────────────────────────

@router.get("/me")
async def get_me(request: Request, key: ApiKey):
    req_id = get_request_id(request)
    t0 = datetime.now(timezone.utc)
    try:
        sb = get_supabase()
        now = datetime.now(timezone.utc)

        # Count requests in the last hour
        hour_ago = now.replace(minute=0, second=0, microsecond=0).isoformat()
        count_res = (
            sb.table("api_audit_log")
            .select("id", count="exact", head=True)
            .eq("api_key_id", key.key_id)
            .gte("created_at", hour_ago)
            .execute()
        )
        requests_this_hour = count_res.count or 0

        key_row = (
            sb.table("api_keys")
            .select("created_at, last_used_at")
            .eq("id", key.key_id)
            .limit(1)
            .execute()
        ).data or [{}]

        data = {
            "api_key_id": key.key_id,
            "api_key_name": key.key_name,
            "permissions": key.permissions,
            "scope": key.scope_type,
            "scope_client_id": key.scope_client_id,
            "created_at": key_row[0].get("created_at"),
            "last_used_at": key_row[0].get("last_used_at"),
            "requests_this_hour": requests_this_hour,
            "requests_remaining_this_hour": max(0, 1000 - requests_this_hour),
        }
        ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
        log_request(request, 200, ms)
        return single_response(data, req_id)
    except Exception as exc:
        ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
        log_request(request, 500, ms, error_message=str(exc))
        raise


# ── API Key management (requires full_access) ─────────────────────────────────

class CreateKeyRequest(BaseModel):
    name: str
    permissions: list[str] = ["read"]
    scope_type: str = "all"
    scope_client_id: Optional[str] = None
    ip_whitelist: Optional[list[str]] = None


@router.get("/api-keys")
async def list_api_keys(request: Request, key: ApiKey):
    key.assert_admin()
    req_id = get_request_id(request)
    sb = get_supabase()
    rows = (
        sb.table("api_keys")
        .select("id, name, key_prefix, permissions, scope_type, scope_client_id, is_active, created_at, last_used_at, requests_count, revoked_at")
        .eq("is_active", True)
        .order("created_at", desc=True)
        .execute()
    ).data or []
    return {"data": rows, "metadata": {"request_id": req_id}}


@router.post("/api-keys", status_code=201)
async def create_api_key(request: Request, key: ApiKey, body: CreateKeyRequest):
    key.assert_admin()
    req_id = get_request_id(request)

    valid_perms = {"read", "write", "full_access"}
    if not all(p in valid_perms for p in body.permissions):
        raise HTTPException(400, f"Invalid permissions. Allowed: {valid_perms}")

    plain_key = generate_api_key()
    prefix = plain_key[:12]
    key_hash = hash_key(plain_key)

    sb = get_supabase()
    row = (
        sb.table("api_keys")
        .insert({
            "name": body.name,
            "key_hash": key_hash,
            "key_prefix": prefix,
            "permissions": body.permissions,
            "scope_type": body.scope_type,
            "scope_client_id": body.scope_client_id,
            "ip_whitelist": body.ip_whitelist,
        })
        .execute()
    ).data[0]

    return single_response({
        **row,
        "key": plain_key,  # shown ONCE
    }, req_id)


@router.post("/api-keys/{key_id}/rotate", status_code=201)
async def rotate_api_key(request: Request, key_id: str, key: ApiKey):
    key.assert_admin()
    req_id = get_request_id(request)
    sb = get_supabase()

    # Get existing key info
    existing = (
        sb.table("api_keys")
        .select("id, name, permissions, scope_type, scope_client_id, ip_whitelist")
        .eq("id", key_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    ).data or []
    if not existing:
        raise HTTPException(404, "API key not found")

    old = existing[0]
    now = datetime.now(timezone.utc).isoformat()

    # Revoke old key
    sb.table("api_keys").update({"is_active": False, "revoked_at": now}).eq("id", key_id).execute()

    # Create new key with same settings
    plain_key = generate_api_key()
    new_row = (
        sb.table("api_keys")
        .insert({
            "name": old["name"],
            "key_hash": hash_key(plain_key),
            "key_prefix": plain_key[:12],
            "permissions": old["permissions"],
            "scope_type": old["scope_type"],
            "scope_client_id": old["scope_client_id"],
            "ip_whitelist": old["ip_whitelist"],
        })
        .execute()
    ).data[0]

    return single_response({**new_row, "key": plain_key}, req_id)


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(request: Request, key_id: str, key: ApiKey):
    key.assert_admin()
    sb = get_supabase()
    sb.table("api_keys").update({
        "is_active": False,
        "revoked_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", key_id).execute()
