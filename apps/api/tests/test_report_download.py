"""
Tests for GET /agency/v1/canonical-reports/{report_id}/artifact (Fase D).

Coverage:
  HTTP layer:
  - PUBLISHED report → 200, integrity=VERIFIED, download_note=None
  - DRAFT report → 200, integrity=VERIFIED (all statuses allowed internally)
  - SUPERSEDED report → 200, download_note=SUPERSEDED
  - Unknown report → 404
  - Hash mismatch (stored hash tampered) → 409 ARTIFACT_INTEGRITY_ERROR
  - Wrong client scope → 403
  - All flags disabled → 404 CANONICAL_REPORTS_DISABLED
  - Response contains full report fields (content, artifact_hash, period_start)
"""
from __future__ import annotations

import copy
import os
import threading
import uuid as _uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.canonical_reports import Reports, CoreStore, Flags, ReportError
from app.agency_api.v1.auth import AuthContext, SCOPE_READ_ANY
from app.agency_api.v1.canonical_reports import router


# ── In-memory CAS store ────────────────────────────────────────────────────────

class MemStore:
    def __init__(self):
        self.rows: dict = {}
        self._lock = threading.RLock()

    def get(self, key):
        with self._lock:
            entry = self.rows.get(key)
            return copy.deepcopy(entry) if entry else None

    def cas(self, key, revision, document):
        with self._lock:
            old = self.rows.get(key)
            if (old["revision"] if old else None) != revision:
                return False
            self.rows[key] = {
                "revision": 1 if old is None else old["revision"] + 1,
                "document": copy.deepcopy(document),
            }
            return True


_ALL_FLAGS = Flags(
    CANONICAL_WEEKLY_REVIEW_ENABLED=True,
    CANONICAL_MONTHLY_REVIEW_ENABLED=False,
    REPORT_PUBLISH_ENABLED=True,
    REPORT_DELIVERY_ENABLED=True,
)


def _weekly_artifact(client_id="test-client"):
    return {
        "client_id":            client_id,
        "canonical_engine":     "H7",
        "engine_version":       "a" * 64,
        "version":              1,
        "period_start":         "2026-10-12",
        "period_end":           "2026-10-18",
        "generated_at":         "2026-10-19T12:00:00+00:00",
        "source_state":         [{"source": "meta", "source_state": "HEALTHY"}],
        "certification_status": [{"metric_key": "revenue", "certification_status": "PROVISIONAL"}],
        "review_status":        "READY",
        "content": {
            "client_id":       client_id,
            "analysis_period": {"start": "2026-10-12", "end": "2026-10-18"},
            "business_truth":  "Weekly test.",
            "facts":           [],
            "channels":        {},
            "hypotheses":      [],
            "recommendations": [],
            "limitations":     ["PROVISIONAL"],
            "status":          "READY_FOR_REVIEW",
            "core_writes":     False,
            "platform_writes": False,
        },
        "evidence_refs": [{"source": "core_metric_snapshots", "client_id": client_id,
                           "period_start": "2026-10-12", "period_end": "2026-10-18",
                           "metric_count": 1}],
    }


def _make_published(client_id="test-client"):
    store = MemStore()
    reports = Reports(store, _ALL_FLAGS)
    doc = reports.generate(_weekly_artifact(client_id=client_id))
    at = datetime.now(timezone.utc).isoformat()
    for state in ("READY_FOR_REVIEW", "APPROVED", "PUBLISHED"):
        doc = reports.transition(doc["report_id"], 1, state,
                                 actor="human", at=at, expected_hash=doc["artifact_hash"])
    return store, reports, doc


def _make_app(client_ids=None):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[SCOPE_READ_ANY] = lambda: AuthContext(
        scope="agency_admin", subject="test-actor", client_ids=client_ids,
    )
    return app


_ENV_ENABLED = {
    "CANONICAL_WEEKLY_REVIEW_ENABLED": "true",
    "CANONICAL_MONTHLY_REVIEW_ENABLED": "false",
    "REPORT_PUBLISH_ENABLED": "true",
    "REPORT_DELIVERY_ENABLED": "true",
}
_ENV_ALL_OFF = {
    "CANONICAL_WEEKLY_REVIEW_ENABLED": "false",
    "CANONICAL_MONTHLY_REVIEW_ENABLED": "false",
    "REPORT_PUBLISH_ENABLED": "false",
    "REPORT_DELIVERY_ENABLED": "false",
}


class TestArtifactDownload:
    def test_published_returns_200_integrity_verified(self):
        store, reports_inst, doc = _make_published()
        app = _make_app()
        with patch("app.agency_api.v1.canonical_reports.service", return_value=reports_inst), \
             patch.dict(os.environ, _ENV_ENABLED):
            r = TestClient(app).get(
                f"/agency/v1/canonical-reports/{doc['report_id']}/artifact?version=1"
            )
        assert r.status_code == 200
        data = r.json()
        assert data["integrity"] == "VERIFIED"
        assert data["download_note"] is None
        assert data["report"]["report_id"] == doc["report_id"]
        assert data["report"]["status"] == "PUBLISHED"

    def test_draft_returns_200(self):
        store = MemStore()
        reports = Reports(store, _ALL_FLAGS)
        doc = reports.generate(_weekly_artifact())
        app = _make_app()
        with patch("app.agency_api.v1.canonical_reports.service", return_value=reports), \
             patch.dict(os.environ, _ENV_ENABLED):
            r = TestClient(app).get(
                f"/agency/v1/canonical-reports/{doc['report_id']}/artifact?version=1"
            )
        assert r.status_code == 200
        assert r.json()["integrity"] == "VERIFIED"

    def test_superseded_returns_200_with_note(self):
        store, reports_inst, doc = _make_published()
        # Manually mark as SUPERSEDED in the store
        key = next(k for k in store.rows if k.startswith("report:"))
        entry = store.rows[key]
        tampered = copy.deepcopy(entry["document"])
        tampered["status"] = "SUPERSEDED"
        store.rows[key] = {"revision": entry["revision"], "document": tampered}

        app = _make_app()
        with patch("app.agency_api.v1.canonical_reports.service", return_value=reports_inst), \
             patch.dict(os.environ, _ENV_ENABLED):
            r = TestClient(app).get(
                f"/agency/v1/canonical-reports/{doc['report_id']}/artifact?version=1"
            )
        assert r.status_code == 200
        assert r.json()["download_note"] == "SUPERSEDED"

    def test_unknown_report_returns_404(self):
        store = MemStore()
        reports = Reports(store, _ALL_FLAGS)
        app = _make_app()
        with patch("app.agency_api.v1.canonical_reports.service", return_value=reports), \
             patch.dict(os.environ, _ENV_ENABLED):
            r = TestClient(app).get(
                f"/agency/v1/canonical-reports/{_uuid.uuid4()}/artifact?version=1"
            )
        assert r.status_code == 404

    def test_hash_mismatch_returns_409(self):
        store, reports_inst, doc = _make_published()
        app = _make_app()
        # Patch digest to return wrong hash so mismatch is triggered
        with patch("app.agency_api.v1.canonical_reports.service", return_value=reports_inst), \
             patch.dict(os.environ, _ENV_ENABLED), \
             patch("app.core.canonical_reports.digest", return_value="f" * 64):
            r = TestClient(app).get(
                f"/agency/v1/canonical-reports/{doc['report_id']}/artifact?version=1"
            )
        assert r.status_code == 409
        assert "ARTIFACT_INTEGRITY_ERROR" in r.text

    def test_wrong_client_scope_returns_403(self):
        store, reports_inst, doc = _make_published(client_id="client-a")
        app = _make_app(client_ids=["client-b"])
        with patch("app.agency_api.v1.canonical_reports.service", return_value=reports_inst), \
             patch.dict(os.environ, _ENV_ENABLED):
            r = TestClient(app, raise_server_exceptions=False).get(
                f"/agency/v1/canonical-reports/{doc['report_id']}/artifact?version=1"
            )
        assert r.status_code == 403

    def test_all_flags_off_returns_404(self):
        store, reports_inst, doc = _make_published()
        app = _make_app()
        with patch("app.agency_api.v1.canonical_reports.service", return_value=reports_inst), \
             patch.dict(os.environ, _ENV_ALL_OFF):
            r = TestClient(app).get(
                f"/agency/v1/canonical-reports/{doc['report_id']}/artifact?version=1"
            )
        assert r.status_code == 404
        assert "CANONICAL_REPORTS_DISABLED" in r.text

    def test_response_contains_full_report_fields(self):
        store, reports_inst, doc = _make_published()
        app = _make_app()
        with patch("app.agency_api.v1.canonical_reports.service", return_value=reports_inst), \
             patch.dict(os.environ, _ENV_ENABLED):
            r = TestClient(app).get(
                f"/agency/v1/canonical-reports/{doc['report_id']}/artifact?version=1"
            )
        assert r.status_code == 200
        report = r.json()["report"]
        for field in ("artifact_hash", "content", "period_start", "period_end", "client_id",
                      "report_type", "version", "status"):
            assert field in report, f"missing field: {field}"

    def test_version_2_returns_404_when_only_v1_exists(self):
        store, reports_inst, doc = _make_published()
        app = _make_app()
        with patch("app.agency_api.v1.canonical_reports.service", return_value=reports_inst), \
             patch.dict(os.environ, _ENV_ENABLED):
            r = TestClient(app).get(
                f"/agency/v1/canonical-reports/{doc['report_id']}/artifact?version=2"
            )
        assert r.status_code == 404
