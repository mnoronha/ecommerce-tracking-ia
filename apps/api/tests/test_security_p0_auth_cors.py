"""
Isolated security tests for PR #1 — Security P0 (PRD §21.2 S1–S7).

Strictly isolated: no calls to production Supabase or external services.
Covers:
  - S1: portal_users.py admin-only auth, agency_members takeover guard, audit trail
  - S2: all 27 legacy routers auth & least privilege + cross-tenant IDOR fixes
  - S3: /setup/admin/encrypt-credentials admin-only auth + audit trail
  - S5: explicit CORS allowlist (rejects arbitrary https:// origins) + tracking ingest exception
  - S7: minimal public /health & sanitized /api/v1/health + authenticated /agency/v1/admin/health-details
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from app.agency_api.v1 import auth as agency_auth
from app.config import settings
from app.main import _parse_cors_origins, app

ADMIN_KEY = "test-admin-key-p0-sec"
PLATFORM_KEY = "test-platform-key-p0-sec"
HERMES_KEY = "test-hermes-key-p0-sec"
JWT_SECRET = "test-supabase-jwt-secret-p0-sec-32bytes"


def _make_jwt(sub: str, email: str) -> str:
    return pyjwt.encode(
        {"sub": sub, "email": email, "aud": "authenticated", "exp": 9999999999},
        JWT_SECRET,
        algorithm="HS256",
    )


@pytest.fixture(autouse=True)
def _patch_auth_settings(monkeypatch):
    monkeypatch.setattr(settings, "AGENCY_API_ADMIN_KEY", ADMIN_KEY)
    monkeypatch.setattr(settings, "AGENCY_API_PLATFORM_KEY", PLATFORM_KEY)
    monkeypatch.setattr(settings, "AGENCY_API_HERMES_KEY", HERMES_KEY)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", JWT_SECRET)


@pytest.fixture(scope="module")
def client():
    # Do not enter TestClient context manager so APScheduler startup/network jobs never fire in isolated tests.
    return TestClient(app, raise_server_exceptions=False)


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── S1 & S3: Admin-only endpoints + audit trail ──────────────────────────────


class TestS1AndS3AdminAuth:
    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("GET", "/portal/users/px-test", None),
            ("POST", "/portal/users/px-test", {"email": "u@x.com", "password": "secret123"}),
            ("PATCH", "/portal/users/px-test/u@x.com/password", {"password": "secret123"}),
            ("DELETE", "/portal/users/px-test/u@x.com", None),
            ("POST", "/setup/admin/encrypt-credentials?confirm=encrypt-now", None),
            ("POST", "/setup/shopify/px-test/webhooks", None),
            ("POST", "/setup/shopify/px-test/install", None),
            ("GET", "/lgpd/px-test/subject?email=u@x.com", None),
            ("POST", "/lgpd/px-test/forget", {"email": "u@x.com"}),
            ("GET", "/audiences/px-test/status", None),
            ("POST", "/audiences/px-test/sync", None),
            ("POST", "/audiences/px-test/sync/high_ltv", None),
            ("POST", "/technical/px-test/schema/issues/iss-1/apply", None),
            ("POST", "/technical/px-test/llms-txt/apply", {"content": "# test"}),
            ("POST", "/agency/v1/canonical-reports/pilot-run?client_id=lk&report_type=weekly", None),
        ],
    )
    def test_admin_only_rejects_unauth_and_non_admin(self, client, method, path, body):
        # No Authorization header -> 401
        r_unauth = client.request(method, path, json=body)
        assert r_unauth.status_code == 401, f"{method} {path} returned {r_unauth.status_code}"

        # platform_web -> 403
        r_plat = client.request(method, path, json=body, headers=_h(PLATFORM_KEY))
        assert r_plat.status_code == 403, f"{method} {path} (platform) returned {r_plat.status_code}"

        # hermes_service -> 403
        r_hermes = client.request(method, path, json=body, headers=_h(HERMES_KEY))
        assert r_hermes.status_code == 403, f"{method} {path} (hermes) returned {r_hermes.status_code}"

    def test_encrypt_credentials_admin_succeeds_and_audits(self, client):
        with (
            patch("app.routers.setup.crypto.encrypt_existing_credentials", return_value={"encrypted": 0}) as mock_enc,
            patch("app.routers.setup.record_admin_audit") as mock_audit,
        ):
            r = client.post(
                "/setup/admin/encrypt-credentials?confirm=encrypt-now",
                headers=_h(ADMIN_KEY),
            )
            assert r.status_code == 200
            assert r.json() == {"encrypted": 0}
            mock_enc.assert_called_once()
            mock_audit.assert_called_once()
            assert mock_audit.call_args.args[0] == "ENCRYPT_CREDENTIALS"

    def test_portal_password_reset_blocks_unlinked_email(self, client):
        fake_sb = MagicMock()
        # _get_pixel returns active client; client_users lookup returns empty
        clients_tbl = MagicMock()
        clients_tbl.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = SimpleNamespace(
            data={"id": "c-uuid", "name": "LK"}
        )
        cu_tbl = MagicMock()
        cu_tbl.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(
            data=[]
        )

        def _table(name: str):
            return clients_tbl if name == "clients" else cu_tbl

        fake_sb.table.side_effect = _table

        with patch("app.routers.portal_users.get_supabase", return_value=fake_sb):
            r = client.patch(
                "/portal/users/px-test/owner@norolabs.com/password",
                json={"password": "newpassword123"},
                headers=_h(ADMIN_KEY),
            )
            assert r.status_code == 404


# ── S2: Legacy internal routers require service or operator token ─────────────


class TestS2LegacyRoutersProtected:
    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("PUT", "/setup/px-test/credentials", {"meta_pixel_id": "123"}),
            ("POST", "/setup/cname/px-test/init", {"cname": "t.example.com"}),
            ("GET", "/integrations/px-test/status", None),
            ("GET", "/sync/shopify/px-test/status", None),
            ("GET", "/diagnostics/px-test", None),
            ("GET", "/notifications/status", None),
            ("GET", "/alerts", None),
            ("GET", "/insights/px-test", None),
            ("GET", "/ai-visibility/costs", None),
            ("GET", "/annotations/px-test", None),
            ("GET", "/attribution/px-test/summary", None),
            ("GET", "/cogs/px-test/coverage", None),
            ("GET", "/content/costs", None),
            ("GET", "/creatives/px-test", None),
            ("GET", "/google-ads/px-test/overview", None),
            ("GET", "/journey/px-test/by-campaign", None),
            ("GET", "/live/px-test/stats", None),
            ("GET", "/merchant-center/px-test/summary", None),
            ("GET", "/meta-ads/px-test/overview", None),
            ("GET", "/pacing/px-test", None),
            ("GET", "/pinterest-ads/px-test/overview", None),
            ("GET", "/search-console/px-test/overview", None),
            ("GET", "/shopify/px-test/revenue", None),
            ("GET", "/technical/prompt-templates", None),
            ("GET", "/tiktok-ads/px-test/overview", None),
            ("GET", "/shopify/app/px-test/urls", None),
        ],
    )
    def test_legacy_routes_require_auth_and_reject_hermes(self, client, method, path, body):
        r_unauth = client.request(method, path, json=body)
        assert r_unauth.status_code == 401, f"{method} {path} returned {r_unauth.status_code}"

        r_hermes = client.request(method, path, json=body, headers=_h(HERMES_KEY))
        assert r_hermes.status_code == 403, f"{method} {path} (hermes) returned {r_hermes.status_code}"

    def test_operator_jwt_accepted_on_internal_route_and_rejected_if_not_agency_member(self, client):
        tok = _make_jwt("11111111-1111-1111-1111-111111111111", "op@norolabs.com")

        # Non-member JWT -> 401
        with patch.object(agency_auth, "_fetch_agency_member_role", return_value=None):
            r_denied = client.get("/shopify/app/px-test/urls", headers=_h(tok))
            assert r_denied.status_code == 401

        # Verified agency_members JWT -> 200 on internal operator route
        with patch.object(agency_auth, "_fetch_agency_member_role", return_value="owner"):
            r_ok = client.get("/shopify/app/px-test/urls", headers=_h(tok))
            assert r_ok.status_code == 200
            assert "app_url" in r_ok.json()

            # Still rejected on admin-only route (requires agency_admin service key)
            r_admin = client.get("/portal/users/px-test", headers=_h(tok))
            assert r_admin.status_code == 401

    def test_public_content_approval_token_route_remains_accessible(self, client):
        fake_sb = MagicMock()
        fake_sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(
            data=[]
        )
        with patch("app.routers.content.get_supabase", return_value=fake_sb):
            r = client.get("/content/approve/nonexistent-token")
            assert r.status_code == 404  # Reached handler (not 401)


# ── S2-C: Cross-tenant IDOR protection on /agency/v1 and /api/v1 ──────────────


class TestS2CrossTenantIsolation:
    def test_client_viewer_cannot_read_other_client_reports(self, client):
        tok = _make_jwt("22222222-2222-2222-2222-222222222222", "viewer@enutri.com")
        with (
            patch.object(agency_auth, "_fetch_pixel_ids_for_email", return_value=["enutri-4sph"]),
            patch("app.agency_api.v1.router._resolve_slug", return_value="lk-sneakers"),
            patch(
                "app.agency_api.v1.router._load_client_meta",
                return_value={"client_id": "lk-sneakers", "pixel_id": "lk-px"},
            ),
        ):
            r = client.get("/agency/v1/clients/lk-sneakers/reports", headers=_h(tok))
            assert r.status_code == 403

    def test_client_viewer_cannot_read_other_client_narrative_by_id(self, client):
        tok = _make_jwt("22222222-2222-2222-2222-222222222222", "viewer@enutri.com")
        narrative_id = "33333333-3333-3333-3333-333333333333"
        fake_db = MagicMock()
        narr_tbl = MagicMock()
        narr_tbl.select.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(
            data=[{"id": narrative_id, "status": "PUBLISHED", "report_contract_id": "44444444-4444-4444-4444-444444444444"}]
        )
        contract_tbl = MagicMock()
        contract_tbl.select.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(
            data=[{"id": "44444444-4444-4444-4444-444444444444", "client_slug": "lk-sneakers"}]
        )
        fake_db.table.side_effect = lambda name: narr_tbl if name == "core_report_narratives" else contract_tbl

        with (
            patch.object(agency_auth, "_fetch_pixel_ids_for_email", return_value=["enutri-4sph"]),
            patch("app.agency_api.v1.router._get_db", return_value=fake_db),
            patch(
                "app.agency_api.v1.router._load_client_meta",
                return_value={"client_id": "lk-sneakers", "pixel_id": "lk-px"},
            ),
        ):
            r = client.get(f"/agency/v1/report-narratives/{narrative_id}", headers=_h(tok))
            assert r.status_code == 403


# ── S5: CORS explicit allowlist + controlled tracking ingest exception ────────


class TestS5CorsHardening:
    def test_parse_cors_origins_never_returns_wildcard(self):
        origins = _parse_cors_origins("*")
        assert "*" not in origins
        assert "https://track.noroia.com.br" in origins

    def test_cors_allows_authorized_origin_and_blocks_evil_origin(self, client):
        r_ok = client.options(
            "/health",
            headers={
                "Origin": "https://track.noroia.com.br",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r_ok.headers.get("access-control-allow-origin") == "https://track.noroia.com.br"
        assert r_ok.headers.get("access-control-allow-credentials") == "true"

        r_evil = client.options(
            "/health",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r_evil.headers.get("access-control-allow-origin") is None

    def test_cors_allows_tracking_ingest_without_credentials(self, client):
        for origin in ("null", "https://loja-cliente.com.br"):
            r = client.options(
                "/agency/v1/clients/lk-sneakers/tracking/events",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "Authorization, Content-Type",
                },
            )
            assert r.status_code == 204
            assert r.headers.get("access-control-allow-origin") == "*"
            assert r.headers.get("access-control-allow-credentials") is None


# ── S7: Minimal public health & authenticated admin health-details ────────────


class TestS7PublicHealthMinimal:
    def test_public_health_exposes_only_minimal_fields(self, client):
        fake_sb = MagicMock()
        fake_sb.table.return_value.select.return_value.limit.return_value.execute.return_value = SimpleNamespace(
            data=[{"id": "1"}]
        )
        with patch("app.database.get_supabase", return_value=fake_sb):
            r = client.get("/health")
            assert r.status_code == 200
            body = r.json()
            assert set(body.keys()) == {"status", "version", "db"}
            assert body["status"] == "ok"
            assert body["db"] == "ok"
            for forbidden in ("jobs", "supabase_url", "db_error", "service_key_set", "credentials_key_set", "ga4_dispatch", "scheduler_running"):
                assert forbidden not in body

    def test_public_api_v1_health_sanitizes_db_error(self, client):
        fake_sb = MagicMock()
        fake_sb.table.side_effect = RuntimeError("postgres://secret-host/db connection refused")
        with patch("app.api.v1.routes.meta.get_supabase", return_value=fake_sb):
            r = client.get("/api/v1/health")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "degraded"
            assert body["dependencies"]["database"] == "error"
            assert "secret-host" not in str(body)

    def test_admin_health_details_requires_agency_admin(self, client):
        assert client.get("/agency/v1/admin/health-details").status_code == 401
        assert client.get("/agency/v1/admin/health-details", headers=_h(PLATFORM_KEY)).status_code == 403
        assert client.get("/agency/v1/admin/health-details", headers=_h(HERMES_KEY)).status_code == 403

        fake_sb = MagicMock()
        fake_sb.table.return_value.select.return_value.limit.return_value.execute.return_value = SimpleNamespace(
            data=[{"id": "1"}]
        )
        with patch("app.database.get_supabase", return_value=fake_sb):
            r = client.get("/agency/v1/admin/health-details", headers=_h(ADMIN_KEY))
            assert r.status_code == 200
            body = r.json()
            assert "jobs" in body
            assert "scheduler_running" in body
            assert "supabase_url" not in body
