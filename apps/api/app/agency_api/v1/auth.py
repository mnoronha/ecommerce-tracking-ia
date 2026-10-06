"""
Agency API v1 — dual-path Bearer token auth (Etapa 2).

Service tokens (env vars, server-side only, never in DB):
  agency_admin   → AGENCY_API_ADMIN_KEY   (full read/write)
  hermes_service → AGENCY_API_HERMES_KEY  (reads truth_*/core_*; writes intel_*)
  platform_web   → AGENCY_API_PLATFORM_KEY (reads agency view; writes action-events)

Client sessions (Supabase JWT — NOT a static shared key):
  client_viewer  → valid Supabase Auth JWT signed with SUPABASE_JWT_SECRET
                   user must exist in client_users with a pixel_id row
                   AuthContext.client_ids is set to their allowed pixel_ids
                   Etapa 3 upgrades client_ids to canonical client_id slugs
                   (adds clients.client_id + client_users.client_id columns)

Deliberate: client_viewer has NO static env-var key (PRD §3, decision C from Etapa 1).
AGENCY_API_CLIENT_KEY in config.py is deprecated; auth module does not read it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

import httpx
from fastapi import Header, HTTPException

from ...config import settings

try:
    import jwt as pyjwt
    from jwt import InvalidTokenError as _JWTError

    _JWT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _JWT_AVAILABLE = False

logger = logging.getLogger(__name__)


# ── AuthContext ────────────────────────────────────────────────────────────────


@dataclass
class AuthContext:
    """Resolved auth state carried through the request."""

    scope: str
    # For client_viewer: pixel_ids of clients this user may read.
    # None = unrestricted (service tokens have access to all clients).
    # Etapa 3 upgrades to canonical slug-based client_id via clients.client_id.
    client_ids: list[str] | None = field(default=None)
    # Supabase auth.users.id (JWT sub claim). Populated only for JWT auth.
    subject: str | None = field(default=None)


# ── Service token resolution (sync, no I/O) ──────────────────────────────────

# Deliberately excludes client_viewer — client_viewer is JWT-only (no static key).
_SERVICE_SCOPE_SETTINGS: dict[str, str] = {
    "agency_admin":   "AGENCY_API_ADMIN_KEY",
    "hermes_service": "AGENCY_API_HERMES_KEY",
    "platform_web":   "AGENCY_API_PLATFORM_KEY",
}


def _resolve_service_token(token: str) -> AuthContext | None:
    """Return AuthContext for a matching service token, or None."""
    for scope, attr in _SERVICE_SCOPE_SETTINGS.items():
        expected: str = getattr(settings, attr, "") or ""
        if expected and token == expected:
            return AuthContext(scope=scope)
    return None


# ── JWT (Supabase Auth) resolution ────────────────────────────────────────────


async def _resolve_jwt(token: str) -> AuthContext | None:
    """
    Validate a Supabase Auth JWT and return AuthContext(scope='client_viewer').

    Steps:
    1. Decode + verify HS256 against SUPABASE_JWT_SECRET (PyJWT handles exp + aud)
    2. Extract email + sub from payload
    3. Query client_users by email to get allowed pixel_ids
    4. Return AuthContext or None if user has no client_users rows

    Returns None (not raises) for any token or lookup failure —
    the caller decides 401 vs 503 based on what else is configured.
    """
    if not _JWT_AVAILABLE:
        return None

    jwt_secret: str = getattr(settings, "SUPABASE_JWT_SECRET", "") or ""
    if not jwt_secret:
        return None

    try:
        payload: dict = pyjwt.decode(
            token,
            jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except _JWTError:
        return None

    email: str = payload.get("email") or ""
    subject: str = payload.get("sub") or ""
    if not email:
        return None

    pixel_ids = await _fetch_pixel_ids_for_email(email)
    if not pixel_ids:
        # JWT is cryptographically valid but this email has no client_users row.
        # Deny access — don't reveal whether the user exists.
        return None

    return AuthContext(scope="client_viewer", client_ids=pixel_ids, subject=subject)


async def _fetch_pixel_ids_for_email(email: str) -> list[str]:
    """
    Query client_users for pixel_ids accessible to this email.

    Uses service key so it bypasses RLS — the JWT was already validated;
    we only need the pixel_id list, not the full row.

    Etapa 3: once client_users.client_id is populated this selects client_id instead.
    """
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{settings.SUPABASE_URL}/rest/v1/client_users",
                headers={
                    "apikey": settings.SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
                },
                params={"select": "pixel_id", "email": f"eq.{email}"},
            )
            resp.raise_for_status()
            return [row["pixel_id"] for row in resp.json() if row.get("pixel_id")]
    except Exception:
        logger.exception("client_users lookup failed during JWT auth for %s", email[:3] + "***")
        return []


# ── require_scopes factory ─────────────────────────────────────────────────────


def require_scopes(*allowed_scopes: str) -> Callable:
    """
    FastAPI dependency factory — returns an async dependency → AuthContext.

    Resolution order per request:
    1. Service token (env-var match) → agency_admin | hermes_service | platform_web
    2. Supabase JWT → client_viewer  (only attempted when 'client_viewer' in allowed_scopes)

    HTTP responses:
      503 — server has no keys configured at all (misconfigured deployment)
      401 — token not recognised / JWT invalid / JWT valid but email not in client_users
      403 — token recognised but scope not in allowed_scopes
    """
    if not allowed_scopes:
        raise ValueError("require_scopes: specify at least one scope")

    _try_jwt = "client_viewer" in allowed_scopes

    async def _dep(authorization: str = Header(default="")) -> AuthContext:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(
                status_code=401,
                detail="Missing or malformed Authorization header. Expected: Bearer <token>",
            )

        # 1. Try static service token (no I/O)
        ctx = _resolve_service_token(token)
        if ctx is not None:
            if ctx.scope in allowed_scopes:
                return ctx
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Scope '{ctx.scope}' is not permitted for this endpoint "
                    f"(requires one of: {', '.join(allowed_scopes)})"
                ),
            )

        # 2. Try Supabase JWT (async, only when client_viewer is accepted here)
        if _try_jwt:
            ctx = await _resolve_jwt(token)
            if ctx is not None:
                # scope is always client_viewer; already in allowed_scopes
                return ctx

        # Neither path matched — determine 503 vs 401
        any_service_key = any(
            getattr(settings, attr, "") for attr in _SERVICE_SCOPE_SETTINGS.values()
        )
        jwt_configured = bool(getattr(settings, "SUPABASE_JWT_SECRET", "") or "")

        if not any_service_key and not jwt_configured:
            raise HTTPException(
                status_code=503,
                detail="Agency API auth is not configured on this server",
            )
        raise HTTPException(status_code=401, detail="Invalid or unrecognised token")

    return _dep


# ── Pre-built scope combinations ──────────────────────────────────────────────
# Import these in router.py via Depends(SCOPE_*).
#
# READ scopes   — for GET endpoints (includes human-relay roles but still excludes client_viewer)
# WRITE scopes  — for POST/PATCH endpoints; READ ≠ WRITE to make grants explicit
#
# Matrix:
#   SCOPE_READ_ANY       = admin + hermes + platform  (GET endpoints)
#   SCOPE_WRITE_ANY      = admin + hermes + platform  (POST endpoints where all three write)
#   SCOPE_HERMES_WRITE   = admin + hermes             (Hermes-authored objects: diagnoses, changes, narratives)
#   SCOPE_PLATFORM_WRITE = admin + platform           (human-authored objects: outcomes, UI decisions)
#   SCOPE_ADMIN_ONLY     = admin                      (system operations, job replay)
#
# Note: SCOPE_READ_ANY and SCOPE_WRITE_ANY have the same grant matrix deliberately.
# They are separate callables so router.py can document intent and tests can override
# read vs. write independently.

SCOPE_READ_ANY       = require_scopes("agency_admin", "hermes_service", "platform_web")
SCOPE_WRITE_ANY      = require_scopes("agency_admin", "hermes_service", "platform_web")
SCOPE_HERMES_WRITE   = require_scopes("agency_admin", "hermes_service")
SCOPE_PLATFORM_WRITE = require_scopes("agency_admin", "platform_web")
SCOPE_ADMIN_ONLY     = require_scopes("agency_admin")
SCOPE_ANY_AUTH       = require_scopes("agency_admin", "hermes_service", "platform_web", "client_viewer")
SCOPE_CLIENT_VIEWER  = require_scopes("agency_admin", "platform_web", "client_viewer")
