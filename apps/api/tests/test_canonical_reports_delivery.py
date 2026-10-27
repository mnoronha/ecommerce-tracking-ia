"""
Tests for canonical report delivery receipt lifecycle.

Coverage:
  Core layer (Reports class):
  - prepare_delivery gated by REPORT_DELIVERY_ENABLED flag
  - DELIVERY_PENDING status + attempt=0 on creation
  - exact version binding (report_id, version, artifact_hash, client_id, channel, recipient)
  - only PUBLISHED reports can receive a delivery receipt
  - idempotency: same delivery_id + binding -> existing receipt returned
  - delivery_id conflict: same id, different binding -> DELIVERY_ID_CONFLICT
  - dispatch() always raises NO_DELIVERY_TRANSPORT_INSTALLED
  - record_delivery_result -> DELIVERED on success / DELIVERY_FAILED on error
  - record_delivery_result idempotent on same final state
  - record_delivery_result rejects wrong artifact_hash
  - record_delivery_result rejects unknown error_code
  - record_delivery_result conflicts when state already set differently
  - retry semantics: attempt increments to 1 after record_delivery_result

  HTTP layer (API endpoint):
  - REPORT_DELIVERY_ENABLED=false -> 409 DELIVERY_DISABLED
  - REPORT_DELIVERY_ENABLED=true + published -> 200 + DELIVERY_PENDING receipt
  - idempotency at HTTP level
  - auth required (SCOPE_PLATFORM_WRITE)
"""
from __future__ import annotations

import copy
import os
import threading
import uuid as _uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.canonical_reports import Reports, CoreStore, Flags, ReportError, DELIVERY_STATES
from app.agency_api.v1.auth import AuthContext, SCOPE_PLATFORM_WRITE
from app.agency_api.v1.canonical_reports import router
from app.config import settings


# ---- In-memory CAS store -----------------------------------------------------

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


# ---- Flags -------------------------------------------------------------------

_ALL_FLAGS = Flags(
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

# ---- Shared fixtures ---------------------------------------------------------

_RCPT = "a" * 64   # valid 64-hex recipient_hash


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
            "business_truth":  f"{client_id} Weekly. Revenue R$10000.",
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


def _make_published(client_id="acme"):
    """Return (store, reports, published_doc) after full DRAFT->PUBLISHED lifecycle."""
    store = MemStore()
    reports = Reports(store, _ALL_FLAGS)
    doc = reports.generate(_weekly_artifact(client_id=client_id))
    at = datetime.now(timezone.utc).isoformat()
    for state in ("READY_FOR_REVIEW", "APPROVED", "PUBLISHED"):
        doc = reports.transition(
            doc["report_id"], 1, state,
            actor="test-human", at=at,
            expected_hash=doc["artifact_hash"],
        )
    return store, reports, doc


# ==== CORE LAYER ==============================================================

class TestDeliveryFlagGate:
    def test_flag_off_raises_delivery_disabled(self):
        store = MemStore()
        reports = Reports(store, _ALL_FLAGS)
        doc = reports.generate(_weekly_artifact())
        at = datetime.now(timezone.utc).isoformat()
        for s in ("READY_FOR_REVIEW", "APPROVED", "PUBLISHED"):
            doc = reports.transition(doc["report_id"], 1, s,
                                     actor="human", at=at, expected_hash=doc["artifact_hash"])
        reports.flags = _NO_DELIVERY
        with pytest.raises(ReportError, match="DELIVERY_DISABLED"):
            reports.prepare_delivery(
                str(_uuid.uuid4()), doc["report_id"], 1,
                channel="EMAIL", recipient_hash=_RCPT,
                actor="human", at=at,
            )

    def test_flag_on_creates_pending_receipt(self):
        _, reports, doc = _make_published()
        receipt = reports.prepare_delivery(
            str(_uuid.uuid4()), doc["report_id"], 1,
            channel="EMAIL", recipient_hash=_RCPT,
            actor="human", at=datetime.now(timezone.utc).isoformat(),
        )
        assert receipt["status"] == "DELIVERY_PENDING"
        assert receipt["attempt"] == 0
        assert receipt["sent_at"] is None
        assert receipt["provider_message_id"] is None


class TestVersionBinding:
    def test_receipt_binds_all_immutable_fields(self):
        _, reports, doc = _make_published()
        receipt = reports.prepare_delivery(
            str(_uuid.uuid4()), doc["report_id"], 1,
            channel="EMAIL", recipient_hash=_RCPT,
            actor="human", at=datetime.now(timezone.utc).isoformat(),
        )
        assert receipt["report_id"]      == doc["report_id"]
        assert receipt["report_version"] == 1
        assert receipt["artifact_hash"]  == doc["artifact_hash"]
        assert receipt["client_id"]      == doc["client_id"]
        assert receipt["channel"]        == "EMAIL"
        assert receipt["recipient"]      == _RCPT

    def test_draft_report_blocked(self):
        store = MemStore()
        reports = Reports(store, _ALL_FLAGS)
        doc = reports.generate(_weekly_artifact())
        with pytest.raises(ReportError, match="EXACT_PUBLISHED_VERSION_REQUIRED"):
            reports.prepare_delivery(
                str(_uuid.uuid4()), doc["report_id"], 1,
                channel="EMAIL", recipient_hash=_RCPT,
                actor="human", at=datetime.now(timezone.utc).isoformat(),
            )

    def test_approved_not_yet_published_blocked(self):
        store = MemStore()
        reports = Reports(store, _ALL_FLAGS)
        doc = reports.generate(_weekly_artifact())
        at = datetime.now(timezone.utc).isoformat()
        for s in ("READY_FOR_REVIEW", "APPROVED"):
            doc = reports.transition(doc["report_id"], 1, s,
                                     actor="human", at=at, expected_hash=doc["artifact_hash"])
        with pytest.raises(ReportError, match="EXACT_PUBLISHED_VERSION_REQUIRED"):
            reports.prepare_delivery(
                str(_uuid.uuid4()), doc["report_id"], 1,
                channel="EMAIL", recipient_hash=_RCPT,
                actor="human", at=at,
            )

    def test_nonexistent_report_raises_not_found(self):
        store = MemStore()
        reports = Reports(store, _ALL_FLAGS)
        with pytest.raises(ReportError, match="REPORT_NOT_FOUND"):
            reports.prepare_delivery(
                str(_uuid.uuid4()), str(_uuid.uuid4()), 1,
                channel="EMAIL", recipient_hash=_RCPT,
                actor="human", at=datetime.now(timezone.utc).isoformat(),
            )


class TestIdempotency:
    def test_same_id_and_binding_returns_existing(self):
        _, reports, doc = _make_published()
        did = str(_uuid.uuid4())
        at = datetime.now(timezone.utc).isoformat()
        kw = dict(channel="EMAIL", recipient_hash=_RCPT, actor="human", at=at)
        a = reports.prepare_delivery(did, doc["report_id"], 1, **kw)
        b = reports.prepare_delivery(did, doc["report_id"], 1, **kw)
        assert a == b

    def test_attempt_not_incremented_on_repeat_creates(self):
        _, reports, doc = _make_published()
        did = str(_uuid.uuid4())
        at = datetime.now(timezone.utc).isoformat()
        kw = dict(channel="EMAIL", recipient_hash=_RCPT, actor="human", at=at)
        for _ in range(5):
            r = reports.prepare_delivery(did, doc["report_id"], 1, **kw)
        assert r["attempt"] == 0
        assert r["status"] == "DELIVERY_PENDING"

    def test_same_id_different_binding_raises_conflict(self):
        _, reports, doc = _make_published()
        did = str(_uuid.uuid4())
        at = datetime.now(timezone.utc).isoformat()
        reports.prepare_delivery(did, doc["report_id"], 1,
                                 channel="EMAIL", recipient_hash=_RCPT,
                                 actor="human", at=at)
        with pytest.raises(ReportError, match="DELIVERY_ID_CONFLICT"):
            reports.prepare_delivery(did, doc["report_id"], 1,
                                     channel="WHATSAPP", recipient_hash=_RCPT,
                                     actor="human", at=at)


class TestTransportGuard:
    def test_dispatch_raises_no_transport(self):
        _, reports, _ = _make_published()
        with pytest.raises(ReportError, match="NO_DELIVERY_TRANSPORT_INSTALLED"):
            reports.dispatch("any-id")

    def test_dispatch_no_args_raises(self):
        _, reports, _ = _make_published()
        with pytest.raises(ReportError, match="NO_DELIVERY_TRANSPORT_INSTALLED"):
            reports.dispatch()

    def test_dispatch_with_kwargs_raises(self):
        _, reports, _ = _make_published()
        with pytest.raises(ReportError, match="NO_DELIVERY_TRANSPORT_INSTALLED"):
            reports.dispatch(delivery_id="x", channel="EMAIL")


class TestDeliveryStates:
    def test_delivery_states_has_all_three_values(self):
        assert "DELIVERY_PENDING" in DELIVERY_STATES
        assert "DELIVERED"        in DELIVERY_STATES
        assert "DELIVERY_FAILED"  in DELIVERY_STATES

    def test_record_result_delivered_sets_state_and_increments_attempt(self):
        _, reports, doc = _make_published()
        did = str(_uuid.uuid4())
        at = datetime.now(timezone.utc).isoformat()
        reports.prepare_delivery(did, doc["report_id"], 1,
                                 channel="EMAIL", recipient_hash=_RCPT,
                                 actor="human", at=at)
        result = reports.record_delivery_result(
            did, artifact_hash=doc["artifact_hash"],
            provider_message_id="msg-abc-123",
        )
        assert result["status"] == "DELIVERED"
        assert result["attempt"] == 1
        assert result["provider_message_id"] == "msg-abc-123"
        assert result["sent_at"] is not None
        assert result["error"] is None

    def test_record_result_failed_sets_error_code(self):
        _, reports, doc = _make_published()
        did = str(_uuid.uuid4())
        at = datetime.now(timezone.utc).isoformat()
        reports.prepare_delivery(did, doc["report_id"], 1,
                                 channel="EMAIL", recipient_hash=_RCPT,
                                 actor="human", at=at)
        result = reports.record_delivery_result(
            did, artifact_hash=doc["artifact_hash"],
            error_code="TRANSPORT_UNAVAILABLE",
        )
        assert result["status"] == "DELIVERY_FAILED"
        assert result["attempt"] == 1
        assert result["error"] == "TRANSPORT_UNAVAILABLE"
        assert result["sent_at"] is None

    def test_record_result_wrong_hash_rejected(self):
        _, reports, doc = _make_published()
        did = str(_uuid.uuid4())
        at = datetime.now(timezone.utc).isoformat()
        reports.prepare_delivery(did, doc["report_id"], 1,
                                 channel="EMAIL", recipient_hash=_RCPT,
                                 actor="human", at=at)
        with pytest.raises(ReportError, match="DELIVERY_HASH_MISMATCH"):
            reports.record_delivery_result(
                did, artifact_hash="0" * 64,
                provider_message_id="msg-xyz",
            )

    def test_record_result_unknown_error_code_rejected(self):
        _, reports, doc = _make_published()
        did = str(_uuid.uuid4())
        at = datetime.now(timezone.utc).isoformat()
        reports.prepare_delivery(did, doc["report_id"], 1,
                                 channel="EMAIL", recipient_hash=_RCPT,
                                 actor="human", at=at)
        with pytest.raises(ReportError, match="SANITIZED_ERROR_CODE"):
            reports.record_delivery_result(
                did, artifact_hash=doc["artifact_hash"],
                error_code="MADE_UP_CODE",
            )

    def test_record_result_both_provider_and_error_raises(self):
        _, reports, doc = _make_published()
        did = str(_uuid.uuid4())
        at = datetime.now(timezone.utc).isoformat()
        reports.prepare_delivery(did, doc["report_id"], 1,
                                 channel="EMAIL", recipient_hash=_RCPT,
                                 actor="human", at=at)
        with pytest.raises(ReportError, match="EXACT_PROVIDER_RESULT_REQUIRED"):
            reports.record_delivery_result(
                did, artifact_hash=doc["artifact_hash"],
                provider_message_id="msg",
                error_code="TRANSPORT_UNAVAILABLE",
            )

    def test_record_result_neither_provider_nor_error_raises(self):
        _, reports, doc = _make_published()
        did = str(_uuid.uuid4())
        at = datetime.now(timezone.utc).isoformat()
        reports.prepare_delivery(did, doc["report_id"], 1,
                                 channel="EMAIL", recipient_hash=_RCPT,
                                 actor="human", at=at)
        with pytest.raises(ReportError, match="EXACT_PROVIDER_RESULT_REQUIRED"):
            reports.record_delivery_result(did, artifact_hash=doc["artifact_hash"])

    def test_record_result_idempotent_on_same_delivered_state(self):
        _, reports, doc = _make_published()
        did = str(_uuid.uuid4())
        at = datetime.now(timezone.utc).isoformat()
        reports.prepare_delivery(did, doc["report_id"], 1,
                                 channel="EMAIL", recipient_hash=_RCPT,
                                 actor="human", at=at)
        a = reports.record_delivery_result(
            did, artifact_hash=doc["artifact_hash"],
            provider_message_id="msg-idem",
        )
        b = reports.record_delivery_result(
            did, artifact_hash=doc["artifact_hash"],
            provider_message_id="msg-idem",
        )
        assert a == b

    def test_record_result_conflict_on_different_msg_id(self):
        _, reports, doc = _make_published()
        did = str(_uuid.uuid4())
        at = datetime.now(timezone.utc).isoformat()
        reports.prepare_delivery(did, doc["report_id"], 1,
                                 channel="EMAIL", recipient_hash=_RCPT,
                                 actor="human", at=at)
        reports.record_delivery_result(
            did, artifact_hash=doc["artifact_hash"],
            provider_message_id="msg-first",
        )
        with pytest.raises(ReportError, match="DELIVERY_RESULT_CONFLICT"):
            reports.record_delivery_result(
                did, artifact_hash=doc["artifact_hash"],
                provider_message_id="msg-different",
            )


class TestValidation:
    def test_invalid_channel_rejected(self):
        _, reports, doc = _make_published()
        with pytest.raises(ReportError, match="INVALID_DELIVERY_REQUEST"):
            reports.prepare_delivery(
                str(_uuid.uuid4()), doc["report_id"], 1,
                channel="TELEGRAM", recipient_hash=_RCPT,
                actor="human", at=datetime.now(timezone.utc).isoformat(),
            )

    def test_short_recipient_hash_rejected(self):
        _, reports, doc = _make_published()
        with pytest.raises(ReportError, match="HASHED_RECIPIENT"):
            reports.prepare_delivery(
                str(_uuid.uuid4()), doc["report_id"], 1,
                channel="EMAIL", recipient_hash="abc",
                actor="human", at=datetime.now(timezone.utc).isoformat(),
            )

    def test_non_hex_recipient_hash_rejected(self):
        _, reports, doc = _make_published()
        with pytest.raises(ReportError, match="HASHED_RECIPIENT"):
            reports.prepare_delivery(
                str(_uuid.uuid4()), doc["report_id"], 1,
                channel="EMAIL", recipient_hash="g" * 64,
                actor="human", at=datetime.now(timezone.utc).isoformat(),
            )

    def test_empty_actor_rejected(self):
        _, reports, doc = _make_published()
        with pytest.raises(ReportError, match="INVALID_DELIVERY_REQUEST"):
            reports.prepare_delivery(
                str(_uuid.uuid4()), doc["report_id"], 1,
                channel="EMAIL", recipient_hash=_RCPT,
                actor="", at=datetime.now(timezone.utc).isoformat(),
            )

    def test_invalid_uuid_delivery_id_raises(self):
        _, reports, doc = _make_published()
        with pytest.raises(ValueError):
            reports.prepare_delivery(
                "not-a-uuid", doc["report_id"], 1,
                channel="EMAIL", recipient_hash=_RCPT,
                actor="human", at=datetime.now(timezone.utc).isoformat(),
            )

    def test_whatsapp_channel_accepted(self):
        _, reports, doc = _make_published()
        receipt = reports.prepare_delivery(
            str(_uuid.uuid4()), doc["report_id"], 1,
            channel="WHATSAPP", recipient_hash=_RCPT,
            actor="human", at=datetime.now(timezone.utc).isoformat(),
        )
        assert receipt["channel"] == "WHATSAPP"
        assert receipt["status"] == "DELIVERY_PENDING"


# ==== HTTP LAYER ==============================================================

class TestHttpDelivery:
    def _app(self, reports_instance=None):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[SCOPE_PLATFORM_WRITE] = lambda: AuthContext(
            scope="agency_admin", subject="test-actor",
        )
        return app

    def _post(self, app, body, env=None):
        env = env or {"REPORT_DELIVERY_ENABLED": "true"}
        with patch.dict(os.environ, env):
            client = TestClient(app, raise_server_exceptions=False)
            return client.post("/agency/v1/canonical-reports/delivery-receipts", json=body)

    def test_flag_off_returns_409(self):
        app = self._app()
        body = {
            "delivery_id": str(_uuid.uuid4()), "report_id": str(_uuid.uuid4()),
            "report_version": 1, "channel": "EMAIL",
            "recipient_hash": _RCPT, "actor": "human",
        }
        r = self._post(app, body, env={"REPORT_DELIVERY_ENABLED": "false"})
        assert r.status_code == 409
        assert "DELIVERY_DISABLED" in r.text

    def test_flag_on_published_returns_delivery_pending(self):
        store, _, doc = _make_published()
        reports_inst = Reports(store, _ALL_FLAGS)
        app = self._app(reports_inst)
        body = {
            "delivery_id": str(_uuid.uuid4()), "report_id": doc["report_id"],
            "report_version": 1, "channel": "EMAIL",
            "recipient_hash": _RCPT, "actor": "human",
        }
        with patch("app.agency_api.v1.canonical_reports.service", return_value=reports_inst):
            r = self._post(app, body)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "DELIVERY_PENDING"
        assert data["attempt"] == 0
        assert data["artifact_hash"] == doc["artifact_hash"]
        assert data["sent_at"] is None

    def test_http_idempotency(self):
        store, _, doc = _make_published()
        reports_inst = Reports(store, _ALL_FLAGS)
        app = self._app()
        did = str(_uuid.uuid4())
        body = {
            "delivery_id": did, "report_id": doc["report_id"],
            "report_version": 1, "channel": "EMAIL",
            "recipient_hash": _RCPT, "actor": "human",
        }
        with (
            patch.dict(os.environ, {"REPORT_DELIVERY_ENABLED": "true"}),
            patch("app.agency_api.v1.canonical_reports.service", return_value=reports_inst),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            r1 = client.post("/agency/v1/canonical-reports/delivery-receipts", json=body)
            r2 = client.post("/agency/v1/canonical-reports/delivery-receipts", json=body)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json() == r2.json()

    def test_auth_required_without_override(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)
        with patch.object(settings, "AGENCY_API_ADMIN_KEY", "real-key"):
            r = client.post(
                "/agency/v1/canonical-reports/delivery-receipts",
                json={
                    "delivery_id": str(_uuid.uuid4()), "report_id": str(_uuid.uuid4()),
                    "report_version": 1, "channel": "EMAIL",
                    "recipient_hash": _RCPT, "actor": "human",
                },
                headers={"Authorization": "Bearer wrong-key"},
            )
        assert r.status_code in (401, 403)

    def test_unpublished_report_returns_409(self):
        store = MemStore()
        reports_inst = Reports(store, _ALL_FLAGS)
        doc = reports_inst.generate(_weekly_artifact())
        at = datetime.now(timezone.utc).isoformat()
        doc = reports_inst.transition(doc["report_id"], 1, "READY_FOR_REVIEW",
                                      actor="human", at=at, expected_hash=doc["artifact_hash"])
        app = self._app()
        body = {
            "delivery_id": str(_uuid.uuid4()), "report_id": doc["report_id"],
            "report_version": 1, "channel": "EMAIL",
            "recipient_hash": _RCPT, "actor": "human",
        }
        with patch("app.agency_api.v1.canonical_reports.service", return_value=reports_inst):
            r = self._post(app, body)
        assert r.status_code == 409
        assert "PUBLISHED" in r.text
