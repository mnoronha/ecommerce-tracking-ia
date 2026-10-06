"""
Agency API v1 — Pipeline health P1 tests.

Invariants:
1. PipelineHealthOut.sources contains DataSourceDetail for each core_data_sources row
2. All DataSourceDetail fields propagate correctly (last_attempt_at, last_reconciled_at, last_error, etc.)
3. Null fields in DB → None in response (never fabricated)
4. Never-attempted source: last_attempt_at=None, last_data_at=None
5. Source with last_error → last_error propagated
6. schema_version always "1.1"
7. Backward-compatible: last_collection + certification still present
8. X-Request-ID echoed

Run from apps/api/:
    python -m pytest tests/test_agency_pipeline_health_p1.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agency_api.v1.auth import SCOPE_READ_ANY, AuthContext
from app.agency_api.v1.request_id import AgencyRequestIDMiddleware
from app.agency_api.v1.router import router as agency_router

_AUTH = AuthContext(scope="agency_admin")
_NOW_STR = "2026-10-04T12:00:00+00:00"
_NOW = datetime.fromisoformat(_NOW_STR)


def _make_source_row(**overrides) -> dict:
    base = {
        "source_key":          "meta_ads:act_123456789",
        "source_system":       "meta_ads",
        "semantic_domain":     "ADS",
        "source_state":        "READY",
        "last_attempt_at":     _NOW_STR,
        "last_data_at":        _NOW_STR,
        "last_validated_at":   _NOW_STR,
        "last_reconciled_at":  None,
        "reconciliation_state": "OK",
        "last_error":          None,
    }
    base.update(overrides)
    return base


def _make_db(sources: list[dict], snap_data: list[dict] | None = None) -> MagicMock:
    snap_data = snap_data or []

    def _table(name: str):
        m = MagicMock()
        for method in ("select", "eq", "order", "limit", "is_", "gte", "lte", "range", "in_"):
            getattr(m, method).return_value = m
        if name == "core_data_sources":
            m.execute.return_value = MagicMock(data=sources)
        elif name == "core_metric_snapshots":
            m.execute.return_value = MagicMock(data=snap_data)
        else:
            m.execute.return_value = MagicMock(data=[])
        return m

    db = MagicMock()
    db.table.side_effect = _table
    return db


def _build_app(db: MagicMock) -> FastAPI:
    app = FastAPI()
    app.add_middleware(AgencyRequestIDMiddleware)
    app.include_router(agency_router)
    app.dependency_overrides[SCOPE_READ_ANY] = lambda: _AUTH
    return app


class TestPipelineHealthP1:
    """sources list populated from core_data_sources."""

    def test_sources_populated_for_each_source(self):
        rows = [
            _make_source_row(source_system="meta_ads", source_key="meta_ads:123"),
            _make_source_row(source_system="google_ads", source_key="google_ads:456", semantic_domain="ADS"),
            _make_source_row(source_system="shopify", source_key="shopify:lk-sneakers", semantic_domain="BUSINESS"),
            _make_source_row(source_system="ga4", source_key="ga4:properties/789", semantic_domain="JOURNEY"),
        ]
        db = _make_db(rows)
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get("/agency/v1/clients/lk-sneakers/pipeline-health")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        sources = data["sources"]
        assert len(sources) == 4, f"Expected 4 sources, got {len(sources)}"

        keys = {s["source_key"] for s in sources}
        assert "meta_ads:123" in keys
        assert "google_ads:456" in keys

    def test_source_detail_fields_populated(self):
        row = _make_source_row(
            source_key="meta_ads:act_123456789",
            source_system="meta_ads",
            semantic_domain="ADS",
            source_state="READY",
            last_attempt_at=_NOW_STR,
            last_data_at=_NOW_STR,
            last_validated_at=_NOW_STR,
            last_reconciled_at=None,
            reconciliation_state="OK",
            last_error=None,
        )
        db = _make_db([row])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get("/agency/v1/clients/lk-sneakers/pipeline-health")

        s = resp.json()["sources"][0]
        assert s["source_key"] == "meta_ads:act_123456789"
        assert s["source_system"] == "meta_ads"
        assert s["semantic_domain"] == "ADS"
        assert s["source_state"] == "READY"
        assert s["last_attempt_at"] is not None
        assert s["last_data_at"] is not None
        assert s["last_validated_at"] is not None
        assert s["last_reconciled_at"] is None
        assert s["reconciliation_state"] == "OK"
        assert s["last_error"] is None

    def test_null_fields_propagate_as_none(self):
        """Source that has never been attempted — all timestamps None."""
        row = _make_source_row(
            source_state="NOT_CONTRACTED",
            last_attempt_at=None,
            last_data_at=None,
            last_validated_at=None,
            last_reconciled_at=None,
            reconciliation_state=None,
            last_error=None,
        )
        db = _make_db([row])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get("/agency/v1/clients/lk-sneakers/pipeline-health")

        s = resp.json()["sources"][0]
        assert s["last_attempt_at"] is None
        assert s["last_data_at"] is None
        assert s["last_validated_at"] is None
        assert s["reconciliation_state"] is None

    def test_last_error_propagated(self):
        row = _make_source_row(
            source_state="ERROR",
            last_error="token expired",
        )
        db = _make_db([row])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get("/agency/v1/clients/lk-sneakers/pipeline-health")

        s = resp.json()["sources"][0]
        assert s["source_state"] == "ERROR"
        assert s["last_error"] == "token expired"

    def test_backward_compat_last_collection_still_present(self):
        """Existing fields last_collection + certification must not disappear."""
        row = _make_source_row(source_system="meta_ads")
        db = _make_db([row])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get("/agency/v1/clients/lk-sneakers/pipeline-health")

        data = resp.json()
        assert "last_collection" in data
        assert "certification" in data
        assert "sources" in data
        assert "schema_version" in data
        assert data["schema_version"] == "1.1"

    def test_no_delta_tolerance_in_response(self):
        """delta/tolerance are NOT stored per-source — must never appear."""
        row = _make_source_row()
        db = _make_db([row])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get("/agency/v1/clients/lk-sneakers/pipeline-health")

        s = resp.json()["sources"][0]
        assert "delta" not in s
        assert "tolerance" not in s

    def test_empty_sources_when_no_data_sources(self):
        db = _make_db([])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get("/agency/v1/clients/lk-sneakers/pipeline-health")

        assert resp.status_code == 200
        assert resp.json()["sources"] == []

    def test_request_id_echoed(self):
        db = _make_db([_make_source_row()])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/agency/v1/clients/lk-sneakers/pipeline-health",
                    headers={"X-Request-ID": "p1-test-id"},
                )
        assert resp.headers.get("x-request-id") == "p1-test-id"
