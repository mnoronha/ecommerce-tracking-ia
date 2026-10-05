"""
Agency API v1 — X-Request-ID middleware integration tests (CCR-012).

Tests:
1. Request with known X-Request-ID → 200 response echoes same value unchanged
2. Request without X-Request-ID → 200 response includes generated req_<hex12>
3. Error response (403 — no auth) → X-Request-ID still echoed in response
4. Non-agency route → X-Request-ID NOT added to response

Run from apps/api/:
    python -m pytest tests/test_agency_request_id.py -v
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agency_api.v1.auth import SCOPE_READ_ANY, AuthContext
from app.agency_api.v1.request_id import AgencyRequestIDMiddleware
from app.agency_api.v1.router import router as agency_router

_HEADER = "x-request-id"        # lowercase — TestClient normalises response headers
_KNOWN_ID = "ccr-012-test-id-known-abc"


def _app_with_auth() -> FastAPI:
    """Minimal app: middleware + agency router with auth bypassed."""
    app = FastAPI()
    app.add_middleware(AgencyRequestIDMiddleware)
    app.include_router(agency_router)
    app.dependency_overrides[SCOPE_READ_ANY] = lambda: AuthContext(scope="agency_admin")

    @app.get("/healthz")   # non-agency route for test 4
    def healthz():
        return {"ok": True}

    return app


def _app_no_auth() -> FastAPI:
    """Minimal app: middleware + agency router, NO auth override."""
    app = FastAPI()
    app.add_middleware(AgencyRequestIDMiddleware)
    app.include_router(agency_router)
    return app


class TestAgencyRequestIDMiddleware:
    """
    Integration tests for CCR-012: X-Request-ID echo middleware.
    Uses real agency router + AgencyRequestIDMiddleware via TestClient.
    Route under test: GET /agency/v1/system/health (stub — no DB calls).
    """

    def test_known_request_id_echoed_on_200(self):
        """Supplied X-Request-ID must be echoed unchanged on a 200 response."""
        with TestClient(_app_with_auth()) as client:
            resp = client.get(
                "/agency/v1/system/health",
                headers={"X-Request-ID": _KNOWN_ID},
            )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        echoed = resp.headers.get(_HEADER)
        assert echoed == _KNOWN_ID, (
            f"X-Request-ID not echoed correctly.\n"
            f"  Sent:     {_KNOWN_ID!r}\n"
            f"  Received: {echoed!r}"
        )

    def test_generated_when_absent(self):
        """Missing X-Request-ID → server generates req_<hex12> and returns it."""
        with TestClient(_app_with_auth()) as client:
            resp = client.get("/agency/v1/system/health")   # no X-Request-ID header
        assert resp.status_code == 200
        rid = resp.headers.get(_HEADER)
        assert rid is not None, (
            "X-Request-ID must be present in response even when not sent in request"
        )
        assert rid.startswith("req_"), (
            f"Generated X-Request-ID must start with 'req_', got: {rid!r}"
        )
        assert len(rid) >= 16, f"Generated ID unexpectedly short: {rid!r}"

    def test_error_response_also_echoes(self):
        """403 (no auth) must still echo the supplied X-Request-ID."""
        with TestClient(_app_no_auth(), raise_server_exceptions=False) as client:
            resp = client.get(
                "/agency/v1/system/health",
                headers={"X-Request-ID": _KNOWN_ID},
            )
        assert resp.status_code in (401, 403), (
            f"Expected 401/403 without auth, got {resp.status_code}"
        )
        echoed = resp.headers.get(_HEADER)
        assert echoed == _KNOWN_ID, (
            f"X-Request-ID must be echoed on error responses.\n"
            f"  Sent:     {_KNOWN_ID!r}\n"
            f"  Received: {echoed!r}"
        )

    def test_non_agency_route_not_affected(self):
        """Routes outside /agency/v1 must NOT have X-Request-ID injected."""
        with TestClient(_app_with_auth()) as client:
            resp = client.get("/healthz", headers={"X-Request-ID": _KNOWN_ID})
        assert resp.status_code == 200
        assert _HEADER not in resp.headers, (
            f"X-Request-ID must NOT be present on non-agency responses. "
            f"Got: {resp.headers.get(_HEADER)!r}"
        )
