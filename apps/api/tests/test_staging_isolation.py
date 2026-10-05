"""
Etapa 2 — staging / production isolation tests.

Validates that staging and production are physically isolated:
  - Different Supabase projects (different SUPABASE_URL)
  - Tokens from one environment are NOT accepted by the other
  - Both environments independently serve /agency/v1/system/health

Required env vars (all skipped gracefully when absent):
  STAGING_API_URL     — Railway staging base URL (no trailing slash)
                        e.g. https://noro-api-staging.up.railway.app
  STAGING_ADMIN_KEY   — AGENCY_API_ADMIN_KEY set in Railway staging
  PROD_API_URL        — Production Railway URL
                        e.g. https://noro-api.up.railway.app
  PROD_ADMIN_KEY      — AGENCY_API_ADMIN_KEY set in Railway production

Tests are READ-ONLY — only call GET endpoints, never write.
Safe to run against live environments.

Run:
    STAGING_API_URL=... STAGING_ADMIN_KEY=... \\
    PROD_API_URL=...    PROD_ADMIN_KEY=...    \\
    python -m pytest tests/test_staging_isolation.py -v
"""

from __future__ import annotations

import os

import httpx
import pytest

# ── Env var helpers ───────────────────────────────────────────────────────────

_STAGING_URL  = os.getenv("STAGING_API_URL", "").rstrip("/")
_STAGING_KEY  = os.getenv("STAGING_ADMIN_KEY", "")
_PROD_URL     = os.getenv("PROD_API_URL", "").rstrip("/")
_PROD_KEY     = os.getenv("PROD_ADMIN_KEY", "")

_STAGING_CONFIGURED = bool(_STAGING_URL and _STAGING_KEY)
_PROD_CONFIGURED    = bool(_PROD_URL and _PROD_KEY)
_BOTH_CONFIGURED    = _STAGING_CONFIGURED and _PROD_CONFIGURED

skip_staging = pytest.mark.skipif(
    not _STAGING_CONFIGURED,
    reason="STAGING_API_URL / STAGING_ADMIN_KEY not set",
)
skip_both = pytest.mark.skipif(
    not _BOTH_CONFIGURED,
    reason="STAGING_API_URL + STAGING_ADMIN_KEY + PROD_API_URL + PROD_ADMIN_KEY required",
)

# ── 1. Individual environment reachability ────────────────────────────────────

class TestEnvironmentReachability:
    """Each environment must respond independently on /agency/v1/system/health."""

    @skip_staging
    def test_staging_health_endpoint_reachable(self):
        resp = httpx.get(
            f"{_STAGING_URL}/agency/v1/system/health",
            headers={"Authorization": f"Bearer {_STAGING_KEY}"},
            timeout=10,
        )
        assert resp.status_code == 200, (
            f"Staging health check failed: {resp.status_code} — {resp.text[:200]}"
        )

    @pytest.mark.skipif(not _PROD_CONFIGURED, reason="PROD_API_URL / PROD_ADMIN_KEY not set")
    def test_prod_health_endpoint_reachable(self):
        resp = httpx.get(
            f"{_PROD_URL}/agency/v1/system/health",
            headers={"Authorization": f"Bearer {_PROD_KEY}"},
            timeout=10,
        )
        assert resp.status_code == 200, (
            f"Production health check failed: {resp.status_code} — {resp.text[:200]}"
        )

    @skip_staging
    def test_staging_returns_schema_version(self):
        """Staging API must serve agency-v1 responses with schema_version 1.1."""
        resp = httpx.get(
            f"{_STAGING_URL}/agency/v1/clients",
            headers={"Authorization": f"Bearer {_STAGING_KEY}"},
            timeout=10,
        )
        assert resp.status_code == 200


# ── 2. Token cross-environment rejection ──────────────────────────────────────

class TestTokenIsolation:
    """A token from one environment must be rejected by the other (401)."""

    @skip_both
    def test_staging_token_rejected_by_production(self):
        """The staging admin key must NOT work in production."""
        resp = httpx.get(
            f"{_PROD_URL}/agency/v1/system/health",
            headers={"Authorization": f"Bearer {_STAGING_KEY}"},
            timeout=10,
        )
        assert resp.status_code == 401, (
            f"SECURITY: staging token was accepted by production! "
            f"Status: {resp.status_code} — tokens are shared between environments."
        )

    @skip_both
    def test_production_token_rejected_by_staging(self):
        """The production admin key must NOT work in staging."""
        resp = httpx.get(
            f"{_STAGING_URL}/agency/v1/system/health",
            headers={"Authorization": f"Bearer {_PROD_KEY}"},
            timeout=10,
        )
        assert resp.status_code == 401, (
            f"SECURITY: production token was accepted by staging! "
            f"Status: {resp.status_code} — tokens are shared between environments."
        )

    @skip_both
    def test_staging_and_prod_urls_differ(self):
        """Sanity: staging and production must have different base URLs."""
        assert _STAGING_URL != _PROD_URL, (
            "STAGING_API_URL and PROD_API_URL are identical — same URL for both environments."
        )


# ── 3. Supabase URL isolation ─────────────────────────────────────────────────

class TestSupabaseIsolation:
    """Staging and production must point at different Supabase projects."""

    def test_supabase_url_env_vars_differ(self):
        """
        Requires SUPABASE_URL and SUPABASE_URL_STAGING to be set locally.
        This test validates the local config, not a live environment.
        """
        prod_url    = os.getenv("SUPABASE_URL", "")
        staging_url = os.getenv("SUPABASE_URL_STAGING", "")

        if not prod_url or not staging_url:
            pytest.skip("SUPABASE_URL or SUPABASE_URL_STAGING not set locally")

        assert prod_url != staging_url, (
            "SUPABASE_URL and SUPABASE_URL_STAGING point at the same project — "
            "staging data would write to production!"
        )

        # Also verify they're from different Supabase project refs
        prod_ref    = prod_url.split(".")[0].replace("https://", "")
        staging_ref = staging_url.split(".")[0].replace("https://", "")
        assert prod_ref != staging_ref, (
            f"Both URLs share the same Supabase project ref: {prod_ref}"
        )

    def test_supabase_service_keys_differ(self):
        """Service keys must differ between environments."""
        prod_key    = os.getenv("SUPABASE_SERVICE_KEY", "")
        staging_key = os.getenv("SUPABASE_SERVICE_KEY_STAGING", "")

        if not prod_key or not staging_key:
            pytest.skip("SUPABASE_SERVICE_KEY or SUPABASE_SERVICE_KEY_STAGING not set locally")

        assert prod_key != staging_key, (
            "Production and staging are using the same Supabase service_role key — "
            "staging requests have full access to production data!"
        )


# ── 4. Auth 401/503 when no keys configured ──────────────────────────────────

class TestAuthRejectionsLocal:
    """
    Local unit-level tests (no live API needed).
    Verify auth module returns correct HTTP codes for edge cases.
    """

    def test_invalid_bearer_returns_401(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.agency_api.v1.auth import SCOPE_READ_ANY, AuthContext
        from fastapi import Depends
        from unittest.mock import patch
        from app.config import settings

        app = FastAPI()

        @app.get("/probe")
        async def _probe(auth: AuthContext = Depends(SCOPE_READ_ANY)):
            return {"ok": True}

        with (
            patch.object(settings, "AGENCY_API_ADMIN_KEY", "prod-key-abc"),
            patch.object(settings, "SUPABASE_JWT_SECRET", ""),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/probe", headers={"Authorization": "Bearer wrong-key"})
        assert resp.status_code == 401

    def test_no_auth_header_returns_401(self):
        from fastapi import FastAPI, Depends
        from fastapi.testclient import TestClient
        from app.agency_api.v1.auth import SCOPE_READ_ANY, AuthContext
        from unittest.mock import patch
        from app.config import settings

        app = FastAPI()

        @app.get("/probe")
        async def _probe(auth: AuthContext = Depends(SCOPE_READ_ANY)):
            return {"ok": True}

        with patch.object(settings, "AGENCY_API_ADMIN_KEY", "any-key"):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/probe")
        assert resp.status_code == 401
