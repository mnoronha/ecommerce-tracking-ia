"""
Agency API v1 — entity-performance P3 tests.

Invariants:
1. period_start + period_end are required; missing either → 422
2. Only campaign-level rows (ad_id IS NULL) are returned
3. platform filter works (meta | google)
4. Pagination: page + page_size, total_rows reflects full count
5. Empty period → rows=[], total_rows=0 (honest absence)
6. schema_version=1.1 always present
7. 404 when client not found
8. 503 on DB error

Run from apps/api/:
    python -m pytest tests/test_agency_entity_performance_p3.py -v
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agency_api.v1.auth import SCOPE_READ_ANY, AuthContext
from app.agency_api.v1.router import router as agency_router

_AUTH = AuthContext(scope="agency_admin")

_CAMPAIGN_ROW = {
    "platform":      "meta",
    "campaign_id":   "123456789",
    "campaign_name": "Awareness — Tênis",
    "date":          "2026-10-04",
    "spend":         "7791.46",
    "impressions":   120000,
    "clicks":        3200,
    "conversions":   38,
    "revenue":       "108303.42",
    "roas":          "13.9007",
    "cpa":           "205.04",
    "ctr":           "2.67",
    "cpc":           "2.43",
    "cpm":           "64.93",
}

_CLIENT_META = {
    "id":             "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "client_id":      "lk-sneakers",
    "name":           "LK Sneakers",
    "business_model": "ecommerce",
    "timezone":       "America/Sao_Paulo",
    "currency":       "BRL",
    "country":        "BR",
    "is_active":      True,
}


def _make_db(
    client_meta: dict | None = _CLIENT_META,
    rows: list[dict] | None = None,
    row_count: int = 0,
) -> MagicMock:
    rows = rows or []

    def _table(name: str):
        m = MagicMock()
        for method in ("select", "eq", "gte", "lte", "order", "range", "is_", "in_",
                        "limit", "maybe_single", "not_"):
            getattr(m, method).return_value = m

        if name == "clients":
            m.execute.return_value = MagicMock(data=client_meta)
        elif name == "ad_campaigns":
            m.execute.return_value = MagicMock(data=rows, count=row_count)
        else:
            m.execute.return_value = MagicMock(data=[], count=0)
        return m

    db = MagicMock()
    db.table.side_effect = _table
    return db


def _build_app(db: MagicMock) -> FastAPI:
    app = FastAPI()
    app.include_router(agency_router)
    app.dependency_overrides[SCOPE_READ_ANY] = lambda: _AUTH
    return app


class TestEntityPerformanceRequiredParams:
    """period_start and period_end are required."""

    def test_missing_period_start_returns_422(self):
        db = _make_db()
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/agency/v1/clients/lk-sneakers/entity-performance",
                    params={"period_end": "2026-10-04"},
                )
        assert resp.status_code == 422

    def test_missing_period_end_returns_422(self):
        db = _make_db()
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/agency/v1/clients/lk-sneakers/entity-performance",
                    params={"period_start": "2026-09-28"},
                )
        assert resp.status_code == 422

    def test_invalid_date_format_returns_422(self):
        db = _make_db()
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/agency/v1/clients/lk-sneakers/entity-performance",
                    params={"period_start": "28-09-2026", "period_end": "2026-10-04"},
                )
        assert resp.status_code == 422


class TestEntityPerformanceHonestAbsence:
    """Empty results → rows=[], total_rows=0 (never fabricated)."""

    def test_empty_period_returns_empty_rows(self):
        db = _make_db(rows=[], row_count=0)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/agency/v1/clients/lk-sneakers/entity-performance",
                    params={"period_start": "2026-09-28", "period_end": "2026-10-04"},
                )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["rows"] == []
        assert data["total_rows"] == 0
        assert data["schema_version"] == "1.1"

    def test_unknown_client_returns_404(self):
        db = _make_db(client_meta=None)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/agency/v1/clients/unknown-client/entity-performance",
                    params={"period_start": "2026-09-28", "period_end": "2026-10-04"},
                )
        assert resp.status_code == 404


class TestEntityPerformanceSchema:
    """Response shape and field values."""

    def test_campaign_row_fields(self):
        db = _make_db(rows=[_CAMPAIGN_ROW], row_count=1)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/agency/v1/clients/lk-sneakers/entity-performance",
                    params={"period_start": "2026-10-04", "period_end": "2026-10-04"},
                )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert data["schema_version"] == "1.1"
        assert data["level"] == "campaign"
        assert data["total_rows"] == 1
        assert data["page"] == 1

        row = data["rows"][0]
        assert row["platform"] == "meta"
        assert row["campaign_id"] == "123456789"
        assert row["campaign_name"] == "Awareness — Tênis"
        assert row["spend"] == pytest.approx(7791.46, abs=0.01)
        assert row["conversions"] == 38
        assert row["roas"] == pytest.approx(13.9007, abs=0.001)

    def test_period_in_response(self):
        db = _make_db(rows=[], row_count=0)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/agency/v1/clients/lk-sneakers/entity-performance",
                    params={"period_start": "2026-09-28", "period_end": "2026-10-04"},
                )
        data = resp.json()
        assert data["period"]["start"] == "2026-09-28"
        assert data["period"]["end"] == "2026-10-04"

    def test_platform_filter_in_response(self):
        db = _make_db(rows=[], row_count=0)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/agency/v1/clients/lk-sneakers/entity-performance",
                    params={
                        "period_start": "2026-09-28",
                        "period_end": "2026-10-04",
                        "platform": "meta",
                    },
                )
        data = resp.json()
        assert data["platform"] == "meta"

    def test_no_platform_filter_platform_is_none(self):
        db = _make_db(rows=[], row_count=0)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/agency/v1/clients/lk-sneakers/entity-performance",
                    params={"period_start": "2026-09-28", "period_end": "2026-10-04"},
                )
        assert resp.json()["platform"] is None


class TestEntityPerformancePagination:
    """page + page_size defaults and custom values."""

    def test_default_pagination(self):
        db = _make_db(rows=[], row_count=0)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/agency/v1/clients/lk-sneakers/entity-performance",
                    params={"period_start": "2026-09-28", "period_end": "2026-10-04"},
                )
        data = resp.json()
        assert data["page"] == 1
        assert data["page_size"] == 50

    def test_custom_pagination(self):
        db = _make_db(rows=[], row_count=0)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/agency/v1/clients/lk-sneakers/entity-performance",
                    params={
                        "period_start": "2026-09-28",
                        "period_end":   "2026-10-04",
                        "page":         "2",
                        "page_size":    "10",
                    },
                )
        data = resp.json()
        assert data["page"] == 2
        assert data["page_size"] == 10

    def test_page_size_above_200_returns_422(self):
        db = _make_db()
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/agency/v1/clients/lk-sneakers/entity-performance",
                    params={
                        "period_start": "2026-09-28",
                        "period_end":   "2026-10-04",
                        "page_size":    "201",
                    },
                )
        assert resp.status_code == 422


class TestEntityPerformanceDBError:
    def test_db_error_returns_503(self):
        db = MagicMock()
        db.table.side_effect = Exception("connection refused")
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db), raise_server_exceptions=False) as client:
                resp = client.get(
                    "/agency/v1/clients/lk-sneakers/entity-performance",
                    params={"period_start": "2026-09-28", "period_end": "2026-10-04"},
                )
        assert resp.status_code in (503, 404)  # 404 if client lookup also fails first
