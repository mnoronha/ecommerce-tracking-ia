"""
Tests for report email transport — controlled delivery pilot.

Coverage (core layer):
  - build_report_email_html renders required fields
  - dispatch_delivery: DELIVERY_HASH_MISMATCH when wrong recipient
  - dispatch_delivery: idempotent on DELIVERED (no resend)
  - dispatch_delivery: success → DELIVERED + provider_message_id
  - dispatch_delivery: provider failure → DELIVERY_FAILED + error_code, no auto-retry
  - dispatch_delivery: explicit retry from DELIVERY_FAILED succeeds
  - dispatch_delivery: explicit retry from DELIVERY_FAILED still fails → DELIVERY_FAILED again
  - dispatch_delivery: DELIVERY_PENDING missing → DELIVERY_NOT_FOUND
  - _classify_provider_error maps timeout / HTTP / unknown correctly

Coverage (HTTP endpoint):
  - REPORT_DELIVERY_ENABLED=false → 409 DELIVERY_DISABLED
  - delivery not found → 404
  - wrong recipient hash → 409 DELIVERY_HASH_MISMATCH
  - success → 200 + DELIVERED receipt
  - provider failure → 409 DELIVERY_FAILED
  - already delivered → 200 idempotent (no resend)
"""
from __future__ import annotations

import copy
import os
import threading
import uuid as _uuid
from datetime import datetime, timezone
from hashlib import sha256
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.canonical_reports import Reports, Flags, ReportError
from app.core.report_email_transport import (
    build_report_email_html,
    dispatch_delivery,
    _classify_provider_error,
)
from app.agency_api.v1.auth import AuthContext, SCOPE_PLATFORM_WRITE
from app.agency_api.v1.canonical_reports import router


# ── In-memory CAS store ────────────────────────────────────────────────────────

class MemStore:
    def __init__(self):
        self.rows: dict = {}
        self._lock = threading.RLock()

    def get(self, key):
        with self._lock:
            e = self.rows.get(key)
            return copy.deepcopy(e) if e else None

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


# ── Flags ──────────────────────────────────────────────────────────────────────

_FLAGS = Flags(
    CANONICAL_WEEKLY_REVIEW_ENABLED=True,
    CANONICAL_MONTHLY_REVIEW_ENABLED=False,
    REPORT_PUBLISH_ENABLED=True,
    REPORT_DELIVERY_ENABLED=True,
)
_NO_DELIVERY = Flags(
    CANONICAL_WEEKLY_REVIEW_ENABLED=True,
    CANONICAL_MONTHLY_REVIEW_ENABLED=False,
    REPORT_PUBLISH_ENABLED=True,
    REPORT_DELIVERY_ENABLED=False,
)

_PILOT_EMAIL = "contato@norolabs.com.br"
_PILOT_HASH  = sha256(_PILOT_EMAIL.encode()).hexdigest()


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _weekly_artifact(client_id="acme"):
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
            "business_truth":  f"{client_id} weekly report.",
            "facts":           [{"metric_key": "revenue", "value": 10000.0}],
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


def _make_published_with_receipt(client_id="acme", recipient_hash: str = _PILOT_HASH):
    """Return (reports, doc, receipt) with a DELIVERY_PENDING receipt ready."""
    store = MemStore()
    reports = Reports(store, _FLAGS)
    doc = reports.generate(_weekly_artifact(client_id=client_id))
    at = datetime.now(timezone.utc).isoformat()
    for state in ("READY_FOR_REVIEW", "APPROVED", "PUBLISHED"):
        doc = reports.transition(
            doc["report_id"], 1, state,
            actor="test-human", at=at,
            expected_hash=doc["artifact_hash"],
        )
    delivery_id = str(_uuid.uuid4())
    receipt = reports.prepare_delivery(
        delivery_id, doc["report_id"], 1,
        channel="EMAIL",
        recipient_hash=recipient_hash,
        actor="test-human",
        at=at,
    )
    return reports, doc, receipt


# ── Unit: build_report_email_html ─────────────────────────────────────────────

def test_html_contains_client_id():
    report = {"client_id": "lk-sneakers", "report_type": "weekly",
              "period_start": "2026-10-12", "period_end": "2026-10-18",
              "version": 1, "engine_version": "abc123", "canonical_engine": "H7",
              "published_at": "2026-10-19T10:00:00+00:00",
              "artifact_hash": "d" * 64}
    html = build_report_email_html(report)
    assert "lk-sneakers" in html
    assert "2026-10-12" in html
    assert "2026-10-18" in html
    assert "d" * 64 in html
    assert "Semanal" in html


def test_html_monthly_label():
    report = {"client_id": "acme", "report_type": "monthly",
              "period_start": "2026-09-01", "period_end": "2026-09-30",
              "version": 1, "engine_version": "x", "canonical_engine": "H8",
              "published_at": "", "artifact_hash": "e" * 64}
    html = build_report_email_html(report)
    assert "Mensal" in html


# ── Unit: _classify_provider_error ────────────────────────────────────────────

def test_classify_timeout():
    assert _classify_provider_error("read timeout") == "TIMEOUT_REQUIRES_RECONCILIATION"

def test_classify_http():
    assert _classify_provider_error("HTTP 422: body") == "PROVIDER_REJECTED"

def test_classify_unknown():
    assert _classify_provider_error("socket reset by peer") == "TRANSPORT_UNAVAILABLE"

def test_classify_none():
    assert _classify_provider_error(None) == "TRANSPORT_UNAVAILABLE"


# ── Unit: dispatch_delivery (core) ────────────────────────────────────────────

def test_dispatch_wrong_recipient_raises():
    reports, _, receipt = _make_published_with_receipt()
    with pytest.raises(ReportError, match="DELIVERY_HASH_MISMATCH"):
        dispatch_delivery(receipt["delivery_id"], "wrong@example.com", reports)


def test_dispatch_not_found_raises():
    store = MemStore()
    reports = Reports(store, _FLAGS)
    with pytest.raises(ReportError, match="DELIVERY_NOT_FOUND"):
        dispatch_delivery(str(_uuid.uuid4()), _PILOT_EMAIL, reports)


def test_dispatch_success_returns_delivered():
    reports, _, receipt = _make_published_with_receipt()
    with patch("app.services.resend.send_email_tracked", return_value=(True, "resend-id-123", None)):
        result = dispatch_delivery(receipt["delivery_id"], _PILOT_EMAIL, reports)
    assert result["status"] == "DELIVERED"
    assert result["provider_message_id"] == "resend-id-123"
    assert result["attempt"] == 1


def test_dispatch_already_delivered_is_idempotent():
    reports, _, receipt = _make_published_with_receipt()
    send_mock = MagicMock(return_value=(True, "resend-id-first", None))
    with patch("app.services.resend.send_email_tracked", send_mock):
        r1 = dispatch_delivery(receipt["delivery_id"], _PILOT_EMAIL, reports)
    # Second call — must NOT call send again
    send_mock2 = MagicMock(return_value=(True, "resend-id-second", None))
    with patch("app.services.resend.send_email_tracked", send_mock2):
        r2 = dispatch_delivery(receipt["delivery_id"], _PILOT_EMAIL, reports)
    send_mock2.assert_not_called()
    assert r2["status"] == "DELIVERED"
    assert r2["provider_message_id"] == "resend-id-first"


def test_dispatch_provider_failure_returns_delivery_failed():
    reports, _, receipt = _make_published_with_receipt()
    with patch("app.services.resend.send_email_tracked", return_value=(False, None, "HTTP 422: rejected")):
        result = dispatch_delivery(receipt["delivery_id"], _PILOT_EMAIL, reports)
    assert result["status"] == "DELIVERY_FAILED"
    assert result["error"] == "PROVIDER_REJECTED"
    assert result["provider_message_id"] is None


def test_dispatch_provider_failure_no_auto_retry():
    reports, _, receipt = _make_published_with_receipt()
    call_count = {"n": 0}
    def mock_send(to, subject, html, **_):
        call_count["n"] += 1
        return (False, None, "HTTP 500: server error")
    with patch("app.services.resend.send_email_tracked", side_effect=mock_send):
        dispatch_delivery(receipt["delivery_id"], _PILOT_EMAIL, reports)
    assert call_count["n"] == 1


def test_dispatch_explicit_retry_from_failed_succeeds():
    reports, _, receipt = _make_published_with_receipt()
    with patch("app.services.resend.send_email_tracked", return_value=(False, None, "HTTP 500: server error")):
        failed = dispatch_delivery(receipt["delivery_id"], _PILOT_EMAIL, reports)
    assert failed["status"] == "DELIVERY_FAILED"

    with patch("app.services.resend.send_email_tracked", return_value=(True, "resend-retry-id", None)):
        retried = dispatch_delivery(receipt["delivery_id"], _PILOT_EMAIL, reports)
    assert retried["status"] == "DELIVERED"
    assert retried["attempt"] == 2


def test_dispatch_retry_still_fails():
    reports, _, receipt = _make_published_with_receipt()
    with patch("app.services.resend.send_email_tracked", return_value=(False, None, "timeout")):
        r1 = dispatch_delivery(receipt["delivery_id"], _PILOT_EMAIL, reports)
    assert r1["status"] == "DELIVERY_FAILED"
    assert r1["error"] == "TIMEOUT_REQUIRES_RECONCILIATION"

    with patch("app.services.resend.send_email_tracked", return_value=(False, None, "HTTP 503: unavailable")):
        r2 = dispatch_delivery(receipt["delivery_id"], _PILOT_EMAIL, reports)
    assert r2["status"] == "DELIVERY_FAILED"
    assert r2["attempt"] == 2


# ── HTTP endpoint tests ────────────────────────────────────────────────────────

def _app(flags: Flags):
    app = FastAPI()
    app.include_router(router)
    def override_auth():
        return AuthContext(subject="test-human", client_ids=None, scope="PLATFORM_WRITE")
    app.dependency_overrides[SCOPE_PLATFORM_WRITE] = override_auth
    return app


def _http_client(flags: Flags, store: MemStore):
    app = _app(flags)
    with patch("app.agency_api.v1.canonical_reports.service",
               return_value=Reports(store, flags)), \
         patch("app.agency_api.v1.canonical_reports.Flags") as mock_flags:
        mock_flags.environment.return_value = flags
        return TestClient(app)


class TestSendEndpointDisabled:
    def test_disabled_flag_returns_409(self):
        store = MemStore()
        _, _, receipt = _make_published_with_receipt()
        app = FastAPI()
        app.include_router(router)
        def override_auth():
            return AuthContext(subject="test-human", client_ids=None, scope="PLATFORM_WRITE")
        app.dependency_overrides[SCOPE_PLATFORM_WRITE] = override_auth
        with patch("app.agency_api.v1.canonical_reports.Flags") as mf, \
             patch("app.agency_api.v1.canonical_reports.service") as msvc:
            mf.environment.return_value = _NO_DELIVERY
            msvc.return_value = Reports(MemStore(), _NO_DELIVERY)
            client = TestClient(app)
            resp = client.post(
                f"/agency/v1/canonical-reports/delivery-receipts/{receipt['delivery_id']}/send",
                json={"recipient": _PILOT_EMAIL, "actor": "test-human"},
            )
        assert resp.status_code == 409
        assert "DELIVERY_DISABLED" in resp.text


class TestSendEndpointHappyPath:
    def _setup(self):
        store = MemStore()
        reports = Reports(store, _FLAGS)
        doc = reports.generate(_weekly_artifact())
        at = datetime.now(timezone.utc).isoformat()
        for state in ("READY_FOR_REVIEW", "APPROVED", "PUBLISHED"):
            doc = reports.transition(doc["report_id"], 1, state,
                                     actor="test-human", at=at, expected_hash=doc["artifact_hash"])
        delivery_id = str(_uuid.uuid4())
        receipt = reports.prepare_delivery(
            delivery_id, doc["report_id"], 1,
            channel="EMAIL", recipient_hash=_PILOT_HASH,
            actor="test-human", at=at,
        )
        return store, reports, receipt

    def test_success_returns_delivered(self):
        store, reports, receipt = self._setup()
        app = FastAPI()
        app.include_router(router)
        def override():
            return AuthContext(subject="test-human", client_ids=None, scope="PLATFORM_WRITE")
        app.dependency_overrides[SCOPE_PLATFORM_WRITE] = override
        with patch("app.agency_api.v1.canonical_reports.Flags") as mf, \
             patch("app.agency_api.v1.canonical_reports.service", return_value=reports), \
             patch("app.services.resend.send_email_tracked", return_value=(True, "resend-http-id", None)):
            mf.environment.return_value = _FLAGS
            client = TestClient(app)
            resp = client.post(
                f"/agency/v1/canonical-reports/delivery-receipts/{receipt['delivery_id']}/send",
                json={"recipient": _PILOT_EMAIL, "actor": "test-human"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "DELIVERED"
        assert body["provider_message_id"] == "resend-http-id"

    def test_wrong_recipient_returns_409(self):
        store, reports, receipt = self._setup()
        app = FastAPI()
        app.include_router(router)
        def override():
            return AuthContext(subject="test-human", client_ids=None, scope="PLATFORM_WRITE")
        app.dependency_overrides[SCOPE_PLATFORM_WRITE] = override
        with patch("app.agency_api.v1.canonical_reports.Flags") as mf, \
             patch("app.agency_api.v1.canonical_reports.service", return_value=reports):
            mf.environment.return_value = _FLAGS
            client = TestClient(app)
            resp = client.post(
                f"/agency/v1/canonical-reports/delivery-receipts/{receipt['delivery_id']}/send",
                json={"recipient": "wrong@example.com", "actor": "test-human"},
            )
        assert resp.status_code == 409
        assert "DELIVERY_HASH_MISMATCH" in resp.text

    def test_not_found_returns_404(self):
        store = MemStore()
        reports = Reports(store, _FLAGS)
        app = FastAPI()
        app.include_router(router)
        def override():
            return AuthContext(subject="test-human", client_ids=None, scope="PLATFORM_WRITE")
        app.dependency_overrides[SCOPE_PLATFORM_WRITE] = override
        with patch("app.agency_api.v1.canonical_reports.Flags") as mf, \
             patch("app.agency_api.v1.canonical_reports.service", return_value=reports):
            mf.environment.return_value = _FLAGS
            client = TestClient(app)
            resp = client.post(
                f"/agency/v1/canonical-reports/delivery-receipts/{str(_uuid.uuid4())}/send",
                json={"recipient": _PILOT_EMAIL, "actor": "test-human"},
            )
        assert resp.status_code == 404

    def test_provider_failure_returns_409_delivery_failed(self):
        store, reports, receipt = self._setup()
        app = FastAPI()
        app.include_router(router)
        def override():
            return AuthContext(subject="test-human", client_ids=None, scope="PLATFORM_WRITE")
        app.dependency_overrides[SCOPE_PLATFORM_WRITE] = override
        with patch("app.agency_api.v1.canonical_reports.Flags") as mf, \
             patch("app.agency_api.v1.canonical_reports.service", return_value=reports), \
             patch("app.services.resend.send_email_tracked", return_value=(False, None, "HTTP 422: rejected")):
            mf.environment.return_value = _FLAGS
            client = TestClient(app)
            resp = client.post(
                f"/agency/v1/canonical-reports/delivery-receipts/{receipt['delivery_id']}/send",
                json={"recipient": _PILOT_EMAIL, "actor": "test-human"},
            )
        assert resp.status_code == 409
        assert "DELIVERY_FAILED" in resp.text or "DELIVERY_RESULT_CONFLICT" in resp.text

    def test_already_delivered_idempotent_200(self):
        store, reports, receipt = self._setup()
        # First send
        with patch("app.services.resend.send_email_tracked", return_value=(True, "id-first", None)):
            dispatch_delivery(receipt["delivery_id"], _PILOT_EMAIL, reports)

        app = FastAPI()
        app.include_router(router)
        def override():
            return AuthContext(subject="test-human", client_ids=None, scope="PLATFORM_WRITE")
        app.dependency_overrides[SCOPE_PLATFORM_WRITE] = override
        send_mock = MagicMock(return_value=(True, "id-second", None))
        with patch("app.agency_api.v1.canonical_reports.Flags") as mf, \
             patch("app.agency_api.v1.canonical_reports.service", return_value=reports), \
             patch("app.services.resend.send_email_tracked", send_mock):
            mf.environment.return_value = _FLAGS
            client = TestClient(app)
            resp = client.post(
                f"/agency/v1/canonical-reports/delivery-receipts/{receipt['delivery_id']}/send",
                json={"recipient": _PILOT_EMAIL, "actor": "test-human"},
            )
        send_mock.assert_not_called()
        assert resp.status_code == 200
        assert resp.json()["provider_message_id"] == "id-first"
