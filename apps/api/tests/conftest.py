"""
pytest conftest for real-DB integration tests.

Fixtures are skipped automatically when SUPABASE_URL / SUPABASE_SERVICE_KEY
are absent (i.e., in CI without secrets or in unit-test-only runs).

Usage:
    pytest apps/api/tests/test_h4_integration.py -v   # requires real DB
    pytest apps/api/tests/ -v                          # unit tests run; integration skipped
"""
from __future__ import annotations

import os
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def supabase_service():
    """Real Supabase service-role client. Skips entire session if env not set."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        pytest.skip("SUPABASE_URL / SUPABASE_SERVICE_KEY not set — skipping real-DB tests")
    from supabase import create_client
    return create_client(url, key)


@pytest.fixture(scope="session")
def app_real_db(supabase_service):
    """FastAPI TestClient wired to the real Supabase client."""
    from app.agency_api.v1.router import router as agency_router
    from app.agency_api.v1.auth import (
        SCOPE_ADMIN_ONLY, SCOPE_ANY_AUTH, SCOPE_HERMES_WRITE,
        SCOPE_PLATFORM_WRITE, SCOPE_READ_ANY, SCOPE_WRITE_ANY,
        AuthContext,
    )

    _auth = AuthContext(scope="agency_admin")

    application = FastAPI()
    application.include_router(agency_router)
    application.dependency_overrides[SCOPE_READ_ANY]       = lambda: _auth
    application.dependency_overrides[SCOPE_WRITE_ANY]      = lambda: _auth
    application.dependency_overrides[SCOPE_ADMIN_ONLY]     = lambda: _auth
    application.dependency_overrides[SCOPE_HERMES_WRITE]   = lambda: _auth
    application.dependency_overrides[SCOPE_PLATFORM_WRITE] = lambda: _auth

    # Patch _get_db to return the real supabase client for the duration of the session
    import app.agency_api.v1.router as _router_mod
    original_get_db = _router_mod._get_db

    def _real_db():
        return supabase_service

    _router_mod._get_db = _real_db
    yield TestClient(application)
    _router_mod._get_db = original_get_db
