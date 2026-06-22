"""Authentication and shared dependencies for Noro Platform REST API."""

import hashlib
import logging
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ...database import get_supabase

logger = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)

KEY_PREFIX = "nrp_sk_"
_RATE_LIMIT_PER_HOUR = 1000
_RATE_LIMIT_BURST_PER_MINUTE = 100


def generate_api_key() -> str:
    """Generate a new API key. Returns plain-text key (shown once)."""
    return KEY_PREFIX + secrets.token_hex(24)   # nrp_sk_ + 48 hex = 55 chars total


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ── Context ──────────────────────────────────────────────────────────────────

class ApiKeyContext:
    def __init__(self, row: dict):
        self.key_id: str = row["id"]
        self.key_name: str = row["name"]
        self.permissions: list[str] = row.get("permissions") or ["read"]
        self.scope_type: str = row.get("scope_type") or "all"
        self.scope_client_id: Optional[str] = row.get("scope_client_id")

    @property
    def can_write(self) -> bool:
        return any(p in self.permissions for p in ("write", "full_access"))

    @property
    def can_admin(self) -> bool:
        return "full_access" in self.permissions

    def assert_write(self) -> None:
        if not self.can_write:
            raise HTTPException(status_code=403, detail="API key requires write permission")

    def assert_admin(self) -> None:
        if not self.can_admin:
            raise HTTPException(status_code=403, detail="API key requires full_access permission")

    def assert_client_scope(self, client_id: str) -> None:
        if self.scope_type == "client" and self.scope_client_id != client_id:
            raise HTTPException(
                status_code=403,
                detail="API key is not scoped to this client",
            )


# ── Auth dependency ───────────────────────────────────────────────────────────

async def get_api_key(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Security(_bearer),
) -> ApiKeyContext:
    if not creds or not creds.credentials:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization: Bearer <api_key> header",
        )

    token = creds.credentials
    if not token.startswith(KEY_PREFIX):
        raise HTTPException(status_code=401, detail="Invalid API key format")

    key_hash = hash_key(token)
    sb = get_supabase()

    rows = (
        sb.table("api_keys")
        .select("id, name, permissions, scope_type, scope_client_id, is_active, revoked_at")
        .eq("key_hash", key_hash)
        .limit(1)
        .execute()
    ).data or []

    if not rows:
        raise HTTPException(status_code=401, detail="Invalid API key")

    row = rows[0]
    if not row.get("is_active") or row.get("revoked_at"):
        raise HTTPException(status_code=401, detail="API key has been revoked")

    # Non-blocking last_used_at update
    try:
        sb.table("api_keys").update({
            "last_used_at": datetime.now(timezone.utc).isoformat(),
            "requests_count": row.get("requests_count", 0) + 1,
        }).eq("id", row["id"]).execute()
    except Exception:
        pass

    ctx = ApiKeyContext(row)
    request.state.api_key_ctx = ctx
    return ctx


# ── Request ID middleware helper ──────────────────────────────────────────────

def get_request_id(request: Request) -> str:
    req_id = getattr(request.state, "request_id", None)
    if not req_id:
        req_id = f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = req_id
    return req_id


# ── Audit log helper (fire-and-forget) ───────────────────────────────────────

def log_request(
    request: Request,
    status_code: int,
    response_time_ms: int,
    client_id: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    ctx: Optional[ApiKeyContext] = getattr(request.state, "api_key_ctx", None)
    if not ctx:
        return
    try:
        sb = get_supabase()
        params: dict = dict(request.query_params)
        # Strip sensitive params
        for k in list(params.keys()):
            if any(s in k.lower() for s in ("token", "key", "secret", "password")):
                params[k] = "***"
        sb.table("api_audit_log").insert({
            "api_key_id":       ctx.key_id,
            "api_key_name":     ctx.key_name,
            "method":           request.method,
            "path":             request.url.path,
            "query_params":     params or None,
            "status_code":      status_code,
            "response_time_ms": response_time_ms,
            "ip_address":       (request.client.host if request.client else None),
            "user_agent":       request.headers.get("user-agent"),
            "client_id":        client_id,
            "error_message":    error_message,
        }).execute()
    except Exception as exc:
        logger.debug("audit log failed: %s", exc)


ApiKey = Annotated[ApiKeyContext, Security(get_api_key)]
