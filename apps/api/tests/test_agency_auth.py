"""
Agency API v1.1 — auth tests (Etapa 2).

Tests:
1. Service token resolution — admin/hermes/platform resolve to correct AuthContext
2. client_viewer has no static key path — AGENCY_API_CLIENT_KEY is NOT used
3. JWT resolution — valid JWT + DB hit → AuthContext(client_viewer, client_ids=[...])
4. JWT resolution failures — expired, wrong secret, missing email, no DB row
5. require_scopes 401/403/503 HTTP responses via FastAPI TestClient
6. AuthContext structure — client_ids None for service tokens, set for JWT

Run from apps/api/:
    python -m pytest tests/test_agency_auth.py -v
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.agency_api.v1.auth import (
    SCOPE_ADMIN_ONLY,
    SCOPE_ANY_AUTH,
    SCOPE_HERMES_WRITE,
    SCOPE_READ_ANY,
    AuthContext,
    _resolve_service_token,
    require_scopes,
)
from app.config import settings

try:
    import jwt as pyjwt
    _JWT_AVAILABLE = True
except ImportError:
    _JWT_AVAILABLE = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_test_jwt(secret: str, email: str, sub: str = "test-uuid-001",
                   expired: bool = False) -> str:
    """Create a minimal Supabase-style JWT for testing."""
    exp = int(time.time()) + (-60 if expired else 3600)
    return pyjwt.encode(
        {"sub": sub, "email": email, "aud": "authenticated",
         "role": "authenticated", "exp": exp},
        secret,
        algorithm="HS256",
    )


def _app_with_scope(dep) -> FastAPI:
    """Minimal FastAPI with GET /test that uses the given scope dependency."""
    app = FastAPI()

    @app.get("/test")
    async def _endpoint(auth: AuthContext = Depends(dep)):
        return {"scope": auth.scope, "client_ids": auth.client_ids}

    return app


def _mock_db_hit(pixel_ids: list[str]):
    """Context manager: mock httpx to return the given pixel_ids from client_users."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=[{"pixel_id": p} for p in pixel_ids])
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)
    return patch("app.agency_api.v1.auth.httpx.AsyncClient", return_value=mock_client)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Service token resolution (sync, no I/O)
# ═══════════════════════════════════════════════════════════════════════════════

class TestServiceTokenResolution:
    """_resolve_service_token must map env-var tokens to AuthContext."""

    def test_admin_token_resolves(self):
        with patch.object(settings, "AGENCY_API_ADMIN_KEY", "admin-token-abc"):
            ctx = _resolve_service_token("admin-token-abc")
        assert ctx is not None
        assert ctx.scope == "agency_admin"
        assert ctx.client_ids is None
        assert ctx.subject is None

    def test_hermes_token_resolves(self):
        with patch.object(settings, "AGENCY_API_HERMES_KEY", "hermes-xyz"):
            ctx = _resolve_service_token("hermes-xyz")
        assert ctx is not None
        assert ctx.scope == "hermes_service"

    def test_platform_token_resolves(self):
        with patch.object(settings, "AGENCY_API_PLATFORM_KEY", "platform-def"):
            ctx = _resolve_service_token("platform-def")
        assert ctx is not None
        assert ctx.scope == "platform_web"

    def test_wrong_token_returns_none(self):
        with patch.object(settings, "AGENCY_API_ADMIN_KEY", "real-token"):
            ctx = _resolve_service_token("wrong-token")
        assert ctx is None

    def test_empty_env_var_returns_none(self):
        with patch.object(settings, "AGENCY_API_ADMIN_KEY", ""):
            ctx = _resolve_service_token("")
        assert ctx is None

    def test_client_key_env_var_is_not_a_service_token(self):
        # AGENCY_API_CLIENT_KEY is deprecated — client_viewer is JWT-only.
        # Even if the env var is set, it MUST NOT resolve via the service token path.
        with patch.object(settings, "AGENCY_API_CLIENT_KEY", "old-client-key"):
            ctx = _resolve_service_token("old-client-key")
        assert ctx is None, "AGENCY_API_CLIENT_KEY must not grant any scope"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. JWT resolution (uses asyncio.run — no pytest-asyncio required)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(not _JWT_AVAILABLE, reason="PyJWT not installed")
class TestJWTResolution:
    """JWT resolution: valid JWT + DB hit → AuthContext(client_viewer)."""

    _SECRET = "test-jwt-secret-32-bytes-minimum!!"

    def test_valid_jwt_resolves_to_client_viewer(self):
        token = _make_test_jwt(self._SECRET, "user@example.com", "uuid-001")
        with (
            patch.object(settings, "SUPABASE_JWT_SECRET", self._SECRET),
            patch.object(settings, "SUPABASE_URL", "https://stub.supabase.co"),
            patch.object(settings, "SUPABASE_SERVICE_KEY", "stub-key"),
            _mock_db_hit(["dipua-qe5p"]),
        ):
            from app.agency_api.v1.auth import _resolve_jwt
            ctx = asyncio.run(_resolve_jwt(token))

        assert ctx is not None
        assert ctx.scope == "client_viewer"
        assert ctx.client_ids == ["dipua-qe5p"]
        assert ctx.subject == "uuid-001"

    def test_expired_jwt_returns_none(self):
        token = _make_test_jwt(self._SECRET, "user@example.com", expired=True)
        with patch.object(settings, "SUPABASE_JWT_SECRET", self._SECRET):
            from app.agency_api.v1.auth import _resolve_jwt
            ctx = asyncio.run(_resolve_jwt(token))
        assert ctx is None

    def test_wrong_secret_returns_none(self):
        token = _make_test_jwt("other-secret-32-bytes-minimum!!!!!", "user@example.com")
        with patch.object(settings, "SUPABASE_JWT_SECRET", self._SECRET):
            from app.agency_api.v1.auth import _resolve_jwt
            ctx = asyncio.run(_resolve_jwt(token))
        assert ctx is None

    def test_no_jwt_secret_configured_returns_none(self):
        token = _make_test_jwt(self._SECRET, "user@example.com")
        with patch.object(settings, "SUPABASE_JWT_SECRET", ""):
            from app.agency_api.v1.auth import _resolve_jwt
            ctx = asyncio.run(_resolve_jwt(token))
        assert ctx is None

    def test_valid_jwt_but_no_db_rows_returns_none(self):
        """JWT valid but email not in client_users → deny access."""
        token = _make_test_jwt(self._SECRET, "unknown@example.com")
        with (
            patch.object(settings, "SUPABASE_JWT_SECRET", self._SECRET),
            patch.object(settings, "SUPABASE_URL", "https://stub.supabase.co"),
            patch.object(settings, "SUPABASE_SERVICE_KEY", "stub-key"),
            _mock_db_hit([]),
        ):
            from app.agency_api.v1.auth import _resolve_jwt
            ctx = asyncio.run(_resolve_jwt(token))
        assert ctx is None

    def test_multiple_pixel_ids_all_returned(self):
        token = _make_test_jwt(self._SECRET, "multi@example.com")
        with (
            patch.object(settings, "SUPABASE_JWT_SECRET", self._SECRET),
            patch.object(settings, "SUPABASE_URL", "https://stub.supabase.co"),
            patch.object(settings, "SUPABASE_SERVICE_KEY", "stub-key"),
            _mock_db_hit(["client-a", "client-b"]),
        ):
            from app.agency_api.v1.auth import _resolve_jwt
            ctx = asyncio.run(_resolve_jwt(token))
        assert ctx is not None
        assert set(ctx.client_ids) == {"client-a", "client-b"}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. HTTP responses via FastAPI TestClient
# ═══════════════════════════════════════════════════════════════════════════════

class TestHTTPAuthResponses:
    """401/403/503 responses with real FastAPI dependency injection."""

    def test_503_when_no_keys_configured(self):
        app = _app_with_scope(SCOPE_READ_ANY)
        with (
            patch.object(settings, "AGENCY_API_ADMIN_KEY", ""),
            patch.object(settings, "AGENCY_API_HERMES_KEY", ""),
            patch.object(settings, "AGENCY_API_PLATFORM_KEY", ""),
            patch.object(settings, "SUPABASE_JWT_SECRET", ""),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/test", headers={"Authorization": "Bearer any-token"})
        assert resp.status_code == 503

    def test_401_missing_auth_header(self):
        app = _app_with_scope(SCOPE_READ_ANY)
        with patch.object(settings, "AGENCY_API_ADMIN_KEY", "real-key"):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/test")
        assert resp.status_code == 401

    def test_401_wrong_scheme(self):
        app = _app_with_scope(SCOPE_READ_ANY)
        with patch.object(settings, "AGENCY_API_ADMIN_KEY", "real-key"):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/test", headers={"Authorization": "Basic real-key"})
        assert resp.status_code == 401

    def test_401_unknown_token(self):
        app = _app_with_scope(SCOPE_READ_ANY)
        with (
            patch.object(settings, "AGENCY_API_ADMIN_KEY", "real-key"),
            patch.object(settings, "SUPABASE_JWT_SECRET", ""),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/test", headers={"Authorization": "Bearer wrong-key"})
        assert resp.status_code == 401

    def test_403_hermes_token_on_admin_only_endpoint(self):
        app = _app_with_scope(SCOPE_ADMIN_ONLY)
        with (
            patch.object(settings, "AGENCY_API_ADMIN_KEY", "admin-key"),
            patch.object(settings, "AGENCY_API_HERMES_KEY", "hermes-key"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/test", headers={"Authorization": "Bearer hermes-key"})
        assert resp.status_code == 403

    def test_200_valid_admin_token_on_read_any(self):
        app = _app_with_scope(SCOPE_READ_ANY)
        with patch.object(settings, "AGENCY_API_ADMIN_KEY", "admin-key"):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/test", headers={"Authorization": "Bearer admin-key"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "agency_admin"
        assert data["client_ids"] is None

    def test_200_hermes_on_read_any(self):
        app = _app_with_scope(SCOPE_READ_ANY)
        with patch.object(settings, "AGENCY_API_HERMES_KEY", "hermes-key"):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/test", headers={"Authorization": "Bearer hermes-key"})
        assert resp.status_code == 200
        assert resp.json()["scope"] == "hermes_service"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. AuthContext structure invariants
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuthContextStructure:
    def test_service_token_has_no_client_ids(self):
        with patch.object(settings, "AGENCY_API_ADMIN_KEY", "tok"):
            ctx = _resolve_service_token("tok")
        assert ctx.client_ids is None
        assert ctx.subject is None

    def test_auth_context_scope_is_string(self):
        ctx = AuthContext(scope="agency_admin")
        assert isinstance(ctx.scope, str)

    def test_auth_context_client_ids_default_none(self):
        ctx = AuthContext(scope="hermes_service")
        assert ctx.client_ids is None

    def test_auth_context_client_viewer_with_ids(self):
        ctx = AuthContext(scope="client_viewer", client_ids=["dipua-qe5p"], subject="uuid")
        assert ctx.client_ids == ["dipua-qe5p"]
        assert ctx.subject == "uuid"

    def test_require_scopes_raises_on_empty(self):
        with pytest.raises(ValueError, match="at least one scope"):
            require_scopes()
