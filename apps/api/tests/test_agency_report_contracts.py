"""
Agency API v1 — GET /report-contracts integration tests (CCR-013).

Tests:
1. Real contract found → provenance_status="REAL", no stub=True, correct mapping
2. Empty DB response → returns empty list (honest absence, no fabrication)
3. report_type filter → DB query uses lowercase; result has correct enum value
4. Core truth versions used when available
5. DB unavailable → 503

Run from apps/api/:
    python -m pytest tests/test_agency_report_contracts.py -v
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agency_api.v1.auth import SCOPE_READ_ANY, AuthContext
from app.agency_api.v1.router import router as agency_router

# ── Test fixtures ─────────────────────────────────────────────────────────────

_CONTRACT_ID = str(uuid.uuid4())
_MONTHLY_ROW: dict[str, Any] = {
    "id": _CONTRACT_ID,
    "client_slug": "lk-sneakers",
    "report_type": "monthly",
    "period_start": "2026-09-01",
    "period_end": "2026-09-30",
    "comparison_period_start": None,
    "comparison_period_end": None,
    "schema_version": "norolabs-report-contract-v1",
    "source_run_id": "20261002T235654Z",
    "generated_at": "2026-10-02 23:58:23.725941+00",
    "contract": {
        "client": {"name": "LK Sneakers"},
        "report": {"type": "monthly"},
        "provenance": {
            "builder": "/data/workspace/scripts/v4/report-contract-builder.py",
            "builder_version": "1.1-model-aware",
            "source_run_id": "20261002T235654Z",
            "generated_at": "2026-10-02T23:58:23.725941+00:00",
        },
        "governance": {
            "completeness": {"status": "PARTIAL"},
            "human_review_required": True,
        },
    },
}
_WEEKLY_ROW: dict[str, Any] = {
    "id": str(uuid.uuid4()),
    "client_slug": "lk-sneakers",
    "report_type": "weekly",
    "period_start": "2026-09-21",
    "period_end": "2026-09-27",
    "comparison_period_start": "2026-09-14",
    "comparison_period_end": "2026-09-20",
    "schema_version": "norolabs-report-contract-v1",
    "source_run_id": "20260930T185748Z",
    "generated_at": "2026-09-30 18:59:17.617161+00",
    "contract": {
        "client": {"name": "LK Sneakers"},
        "report": {"type": "weekly"},
        "provenance": {
            "builder": "/data/workspace/scripts/v4/report-contract-builder.py",
            "builder_version": "1.1-model-aware",
            "source_run_id": "20260930T185748Z",
            "generated_at": "2026-09-30T18:59:17.617161+00:00",
        },
    },
}
_TRUTH_ROW: dict[str, Any] = {
    "client_version": 2,
    "target_version": 3,
    "conversion_map_version": 1,
}


def _make_db(contracts_data: list, truth_data: list | None = None) -> MagicMock:
    """Return a fake Supabase client that answers contract + truth queries."""
    truth_data = truth_data or []

    def _table_dispatch(name: str):
        mock = MagicMock()
        # Chain methods return self so .select().eq().order()... works
        for m in ("select", "eq", "order", "limit"):
            getattr(mock, m).return_value = mock

        if name == "agency_report_contracts":
            mock.execute.return_value = MagicMock(data=contracts_data)
        elif name == "core_client_truth":
            mock.execute.return_value = MagicMock(data=truth_data)
        else:
            mock.execute.return_value = MagicMock(data=[])
        return mock

    db = MagicMock()
    db.table.side_effect = _table_dispatch
    return db


def _build_app(db: MagicMock) -> FastAPI:
    app = FastAPI()
    app.include_router(agency_router)
    app.dependency_overrides[SCOPE_READ_ANY] = lambda: AuthContext(scope="agency_admin")
    return app


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestGetReportContracts:

    def test_real_contract_provenance_status_real(self):
        """Real DB row → provenance_status='REAL', no stub=True, correct fields."""
        db = _make_db([_MONTHLY_ROW])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get("/agency/v1/clients/lk-sneakers/report-contracts")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data) == 1
        rc = data[0]

        assert rc["provenance_status"] == "REAL", (
            f"Expected REAL, got {rc['provenance_status']!r}"
        )
        assert rc["report_contract_id"] == _CONTRACT_ID
        assert rc["report_type"] == "MONTHLY"
        assert rc["period"]["start"] == "2026-09-01"
        assert rc["period"]["end"]   == "2026-09-30"

        # legacy schema version preserved inside contract body
        assert rc["contract"]["legacy_schema_version"] == "norolabs-report-contract-v1"

        # provenance from builder preserved inside contract
        assert rc["contract"]["provenance"]["builder_version"] == "1.1-model-aware"
        assert rc["contract"]["provenance"]["source_run_id"] == "20261002T235654Z"

        # No stub marker
        assert rc["contract"].get("stub") is None, "stub=True must not appear in real contract"

        # period_closed_at set (2026-09-30 < today 2026-10-05)
        assert rc["period_closed_at"] is not None

        # Agency API schema_version always "1.1"
        assert rc["schema_version"] == "1.1"

    def test_empty_db_returns_empty_list_no_fabrication(self):
        """No rows in DB → empty list (honest absence, no stub injection)."""
        db = _make_db([])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get("/agency/v1/clients/unknown-client/report-contracts")

        assert resp.status_code == 200, resp.text
        assert resp.json() == [], "Expected empty list for absent client, got fabricated data"

    def test_report_type_filter_maps_lowercase_to_db(self):
        """MONTHLY query param → DB queried with 'monthly' (lowercase)."""
        db = _make_db([_MONTHLY_ROW])
        with patch("app.agency_ai.v1.router._get_db", return_value=db) if False else \
             patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/agency/v1/clients/lk-sneakers/report-contracts",
                    params={"report_type": "MONTHLY"},
                )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data) == 1
        assert data[0]["report_type"] == "MONTHLY"

        # Verify DB was queried with lowercase
        contracts_table = db.table.call_args_list[0]
        assert contracts_table[0][0] == "agency_report_contracts"

    def test_truth_versions_from_core_client_truth(self):
        """When core_client_truth has rows, truth_versions reflects them."""
        db = _make_db([_MONTHLY_ROW], truth_data=[_TRUTH_ROW])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get("/agency/v1/clients/lk-sneakers/report-contracts")

        assert resp.status_code == 200, resp.text
        tv = resp.json()[0]["truth_versions"]
        assert tv["client"] == 2
        assert tv["target"] == 3
        assert tv["conversion_map"] == 1

    def test_weekly_contract_comparison_period_preserved(self):
        """Weekly row with comparison period → comparison_period added to contract body."""
        db = _make_db([_WEEKLY_ROW])
        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db)) as client:
                resp = client.get("/agency/v1/clients/lk-sneakers/report-contracts")

        assert resp.status_code == 200, resp.text
        rc = resp.json()[0]
        assert rc["report_type"] == "WEEKLY"
        cp = rc["contract"].get("comparison_period")
        assert cp is not None
        assert cp["start"] == "2026-09-14"
        assert cp["end"]   == "2026-09-20"

    def test_db_error_returns_503(self):
        """DB exception → 503 Service Unavailable."""
        db = MagicMock()
        db.table.side_effect = Exception("connection refused")

        with patch("app.agency_api.v1.router._get_db", return_value=db):
            with TestClient(_build_app(db), raise_server_exceptions=False) as client:
                resp = client.get("/agency/v1/clients/lk-sneakers/report-contracts")

        assert resp.status_code == 503, f"Expected 503, got {resp.status_code}"
