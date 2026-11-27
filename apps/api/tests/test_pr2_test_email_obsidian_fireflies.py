"""
Isolated unit tests for PR #2:
  1. R02 — Internal Test Email Sending (PRD §16.3)
  2. R08 — Obsidian Writer idempotency, 401/403 auth handling & safe retry (PRD §15)
  3. Fireflies Auto-Trigger — meeting creation + idempotent action-item extraction (PRD §17)

Strictly isolated: zero production DB writes, zero real Resend/GitHub/Fireflies calls.
"""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.canonical_reports import Flags, Reports, ReportError, digest as canonical_digest
from app.core.obsidian_writer import _write_to_github, promote_to_knowledge, retry_obsidian_sync
from app.core.report_email_transport import (
    INTERNAL_TEST_RECIPIENT,
    InMemoryTestDeliveryAuditStore,
    dispatch_test_email,
)
from app.main import app
from app.routers import fireflies_webhook as ff_wh

ADMIN_KEY = "test-pr2-admin-key"
PLATFORM_KEY = "test-pr2-platform-key"
HERMES_KEY = "test-pr2-hermes-key"


class MemStore:
    """In-memory CAS store matching CoreStore interface."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def get(self, key: str):
        row = self.rows.get(key)
        return json.loads(json.dumps(row)) if row else None

    def cas(self, key: str, expected_revision: int, doc: dict) -> int:
        cur = self.rows.get(key)
        cur_rev = cur["revision"] if cur else 0
        if cur_rev != expected_revision:
            raise ReportError("CAS_CONFLICT")
        new_rev = cur_rev + 1
        self.rows[key] = {"revision": new_rev, "document": json.loads(json.dumps(doc))}
        return new_rev


def _make_sample_report(
    store: MemStore,
    *,
    report_id: str = "rep-lk-001",
    client_id: str = "lk-sneakers",
    version: int = 2,
    status: str = "READY_FOR_REVIEW",
    report_type: str = "weekly",
) -> dict:
    doc = {
        "report_id":        report_id,
        "client_id":        client_id,
        "canonical_engine": "H7" if report_type == "weekly" else "H8",
        "engine_version":   "2.0.0",
        "version":          version,
        "report_type":      report_type,
        "generated_at":     "2026-11-23T08:00:00+00:00",
        "period_start":     "2026-11-16",
        "period_end":       "2026-11-22",
        "source_state":     [{"source_system": "meta_ads", "source_state": "READY"}],
        "certification_status": [{"domain": "performance", "status": "PROVISIONAL"}],
        "content": {
            "client_id": client_id,
            "analysis_period": {"start": "2026-11-16", "end": "2026-11-22"},
            "facts": [
                {"metric_key": "revenue_business", "value": 212366.44, "unit": "BRL", "value_status": "OK"},
                {"metric_key": "orders_count", "value": 126, "unit": "count", "value_status": "OK"},
                {"metric_key": "spend_total", "value": 12477.07, "unit": "BRL", "value_status": "OK"},
            ],
            "channels": {
                "meta": {"spend": 8000.0, "conversions": 80, "roas": 9.43, "source_state": "READY"},
                "google": {"spend": 4477.07, "conversions": 46, "roas": 3.22, "source_state": "READY"},
            },
            "analysis_layer": {
                "status": "COMPLETE",
                "executive_summary": {"bullets": ["Semana positiva com MER 17.02x."]},
            },
        },
        "quality_scores": {"data_label": "READY"},
    }
    doc["artifact_hash"] = canonical_digest(doc)
    doc["status"] = status
    store.rows[f"report:{report_id}:v{version}"] = {"revision": 1, "document": json.loads(json.dumps(doc))}
    return doc


@pytest.fixture(autouse=True)
def _patch_keys(monkeypatch):
    monkeypatch.setattr(settings, "AGENCY_API_ADMIN_KEY", ADMIN_KEY)
    monkeypatch.setattr(settings, "AGENCY_API_PLATFORM_KEY", PLATFORM_KEY)
    monkeypatch.setattr(settings, "AGENCY_API_HERMES_KEY", HERMES_KEY)


@pytest.fixture(scope="module")
def http_client():
    return TestClient(app, raise_server_exceptions=False)


# ══════════════════════════════════════════════════════════════════════════════
# 1. R02 — Internal Test Email Sending
# ══════════════════════════════════════════════════════════════════════════════


class TestR02InternalTestEmail:
    @pytest.mark.parametrize("status", ["DRAFT", "READY_FOR_REVIEW", "APPROVED", "PUBLISHED"])
    def test_sends_exclusively_to_allowlist_with_prefix_and_cta_false_without_mutating_report(self, status):
        store = MemStore()
        before = _make_sample_report(store, status=status)
        reports = Reports(store, Flags(REPORT_DELIVERY_ENABLED=False))
        audit = InMemoryTestDeliveryAuditStore()

        sent_calls = []

        def _fake_send(to: str, subject: str, html: str, from_name: str = "Noro Reports"):
            sent_calls.append({"to": to, "subject": subject, "html": html, "from_name": from_name})
            return True, "resend_msg_test_001", None

        with patch("app.services.resend.send_email_tracked", side_effect=_fake_send):
            res = dispatch_test_email(
                report_id="rep-lk-001",
                report_version=2,
                client_id="lk-sneakers",
                actor="maico@norolabs.com.br",
                idempotency_key=f"idem-key-{status.lower()}-001",
                reports=reports,
                audit_store=audit,
            )

        assert res["status"] == "SENT"
        assert res["event_type"] == "TEST_DELIVERY"
        assert res["recipient"] == INTERNAL_TEST_RECIPIENT
        assert res["cta_authorized"] is False
        assert res["subject"].startswith("[TESTE INTERNO]")
        assert res["provider_message_id"] == "resend_msg_test_001"
        assert res["idempotent_replay"] is False

        # Exactly 1 Resend call to contato@norolabs.com.br with CTA blocked
        assert len(sent_calls) == 1
        assert sent_calls[0]["to"] == "contato@norolabs.com.br"
        assert sent_calls[0]["subject"].startswith("[TESTE INTERNO]")
        assert "https://track.noroia.com.br/clients/lk-sneakers/canonical-reports" not in sent_calls[0]["html"]

        # Zero mutation on canonical report record & zero commercial delivery receipts
        after = store.rows["report:rep-lk-001:v2"]["document"]
        assert after == before
        assert not any(k.startswith("delivery:") for k in store.rows)
        assert len(audit.rows) == 1
        assert audit.rows[0]["event_type"] == "TEST_DELIVERY"

    def test_blocks_external_email_attempt_with_zero_resend_calls(self):
        store = MemStore()
        _make_sample_report(store, status="READY_FOR_REVIEW")
        reports = Reports(store, Flags(REPORT_DELIVERY_ENABLED=False))
        audit = InMemoryTestDeliveryAuditStore()

        with patch("app.services.resend.send_email_tracked") as mock_send:
            with pytest.raises(ReportError, match="INTERNAL_TEST_RECIPIENT_ONLY"):
                dispatch_test_email(
                    report_id="rep-lk-001",
                    report_version=2,
                    client_id="lk-sneakers",
                    actor="maico@norolabs.com.br",
                    idempotency_key="idem-ext-attempt-001",
                    reports=reports,
                    audit_store=audit,
                    requested_recipient="externo@cliente.com.br",
                )
            mock_send.assert_not_called()
            assert len(audit.rows) == 0

    def test_render_failed_blocks_resend_and_records_audit(self):
        store = MemStore()
        _make_sample_report(store, status="READY_FOR_REVIEW")
        reports = Reports(store, Flags(REPORT_DELIVERY_ENABLED=False))
        audit = InMemoryTestDeliveryAuditStore()

        with (
            patch(
                "app.core.canonical_email_renderer.render_weekly_email",
                side_effect=RuntimeError("template boom"),
            ),
            patch("app.services.resend.send_email_tracked") as mock_send,
        ):
            with pytest.raises(ReportError, match="RENDER_FAILED"):
                dispatch_test_email(
                    report_id="rep-lk-001",
                    report_version=2,
                    client_id="lk-sneakers",
                    actor="maico@norolabs.com.br",
                    idempotency_key="idem-render-fail-001",
                    reports=reports,
                    audit_store=audit,
                )
            mock_send.assert_not_called()

        assert len(audit.rows) == 1
        assert audit.rows[0]["status"] == "RENDER_FAILED"
        assert audit.rows[0]["error_code"] == "RENDER_FAILED"
        assert audit.rows[0]["provider_message_id"] is None

    def test_idempotency_and_rate_limit_and_superseded_rejection(self):
        store = MemStore()
        _make_sample_report(store, status="READY_FOR_REVIEW")
        reports = Reports(store, Flags(REPORT_DELIVERY_ENABLED=False))
        audit = InMemoryTestDeliveryAuditStore()

        with patch("app.services.resend.send_email_tracked", return_value=(True, "msg_1", None)) as mock_send:
            first = dispatch_test_email(
                report_id="rep-lk-001",
                report_version=2,
                client_id="lk-sneakers",
                actor="maico@norolabs.com.br",
                idempotency_key="idem-same-key-001",
                reports=reports,
                audit_store=audit,
            )
            replay = dispatch_test_email(
                report_id="rep-lk-001",
                report_version=2,
                client_id="lk-sneakers",
                actor="maico@norolabs.com.br",
                idempotency_key="idem-same-key-001",
                reports=reports,
                audit_store=audit,
            )
            assert first["id"] == replay["id"]
            assert replay["idempotent_replay"] is True
            assert mock_send.call_count == 1

            # New key within cooldown window -> rate limited
            with pytest.raises(ReportError, match="TEST_DELIVERY_RATE_LIMITED"):
                dispatch_test_email(
                    report_id="rep-lk-001",
                    report_version=2,
                    client_id="lk-sneakers",
                    actor="maico@norolabs.com.br",
                    idempotency_key="idem-second-key-002",
                    reports=reports,
                    audit_store=audit,
                )
            assert mock_send.call_count == 1

        # SUPERSEDED rejected
        _make_sample_report(store, report_id="rep-old", version=1, status="SUPERSEDED")
        with pytest.raises(ReportError, match="TEST_DELIVERY_STATUS_NOT_ALLOWED"):
            dispatch_test_email(
                report_id="rep-old",
                report_version=1,
                client_id="lk-sneakers",
                actor="maico@norolabs.com.br",
                idempotency_key="idem-superseded-001",
                reports=reports,
                audit_store=audit,
            )

    def test_http_endpoint_auth_external_block_and_independent_of_delivery_flag(self, http_client, monkeypatch):
        monkeypatch.setenv("REPORT_DELIVERY_ENABLED", "false")
        store = MemStore()
        _make_sample_report(store, status="DRAFT")
        reports = Reports(store, Flags(REPORT_DELIVERY_ENABLED=False))
        audit = InMemoryTestDeliveryAuditStore()

        url = "/agency/v1/canonical-reports/rep-lk-001/versions/2/test-email"
        headers_plat = {
            "Authorization": f"Bearer {PLATFORM_KEY}",
            "Idempotency-Key": "http-idem-0001",
        }

        # Unauthenticated -> 401; Hermes -> 403
        assert http_client.post(url, json={"client_id": "lk-sneakers", "actor": "a@b.com"}, headers={"Idempotency-Key": "http-idem-0001"}).status_code == 401
        assert http_client.post(
            url,
            json={"client_id": "lk-sneakers", "actor": "a@b.com"},
            headers={"Authorization": f"Bearer {HERMES_KEY}", "Idempotency-Key": "http-idem-0001"},
        ).status_code == 403

        with (
            patch("app.agency_api.v1.canonical_reports.service", return_value=reports),
            patch("app.agency_api.v1.canonical_reports.test_audit_store", return_value=audit),
            patch("app.agency_api.v1.router._resolve_slug", side_effect=lambda x: x),
            patch("app.services.resend.send_email_tracked", return_value=(True, "msg_http_1", None)) as mock_send,
        ):
            # External recipient attempt -> 403
            r_ext = http_client.post(
                url,
                json={"client_id": "lk-sneakers", "actor": "maico@norolabs.com.br", "recipient": "cliente@externo.com"},
                headers=headers_plat,
            )
            assert r_ext.status_code == 403
            mock_send.assert_not_called()

            # Cross-client mismatch -> 403
            r_cross = http_client.post(
                url,
                json={"client_id": "enutri", "actor": "maico@norolabs.com.br"},
                headers=headers_plat,
            )
            assert r_cross.status_code == 403
            mock_send.assert_not_called()

            # Valid internal test send on DRAFT with REPORT_DELIVERY_ENABLED=false -> 200
            r_ok = http_client.post(
                url,
                json={"client_id": "lk-sneakers", "actor": "maico@norolabs.com.br"},
                headers=headers_plat,
            )
            assert r_ok.status_code == 200, r_ok.text
            body = r_ok.json()
            assert body["status"] == "SENT"
            assert body["recipient"] == "contato@norolabs.com.br"
            assert body["cta_authorized"] is False
            assert body["subject"].startswith("[TESTE INTERNO]")
            assert mock_send.call_count == 1

            # Commercial prepare_delivery STILL blocked when REPORT_DELIVERY_ENABLED=false
            r_comm = http_client.post(
                "/agency/v1/canonical-reports/delivery-receipts",
                json={
                    "delivery_id": "del-comm-1",
                    "report_id": "rep-lk-001",
                    "report_version": 2,
                    "channel": "EMAIL",
                    "recipient_hash": "a" * 64,
                    "actor": "maico@norolabs.com.br",
                },
                headers={"Authorization": f"Bearer {PLATFORM_KEY}"},
            )
            assert r_comm.status_code == 409


# ══════════════════════════════════════════════════════════════════════════════
# 2. R08 — Obsidian Writer Idempotency, 401/403 Auth Handling & Safe Retry
# ══════════════════════════════════════════════════════════════════════════════


class _FakeObsidianDB:
    """In-memory mock for core_learning_candidates, core_agency_knowledge, core_knowledge_promotions."""

    def __init__(self) -> None:
        self.candidates: list[dict] = []
        self.knowledge: list[dict] = []
        self.promotions: list[dict] = []

    def table(self, name: str):
        return _FakeTable(self, name)


class _FakeTable:
    def __init__(self, db: _FakeObsidianDB, name: str) -> None:
        self.db = db
        self.name = name
        self._op = "select"
        self._filters: list[tuple[str, object]] = []
        self._payload = None

    def _rows(self) -> list[dict]:
        return {
            "core_learning_candidates": self.db.candidates,
            "core_agency_knowledge":    self.db.knowledge,
            "core_knowledge_promotions": self.db.promotions,
        }.get(self.name, [])

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def insert(self, payload: dict):
        self._op = "insert"
        self._payload = dict(payload)
        return self

    def update(self, payload: dict):
        self._op = "update"
        self._payload = dict(payload)
        return self

    def eq(self, col: str, val):
        self._filters.append((col, val))
        return self

    def limit(self, _n: int):
        return self

    def execute(self):
        rows = self._rows()
        if self._op == "select":
            matched = [
                dict(r) for r in rows
                if all(str(r.get(c)) == str(v) for c, v in self._filters)
            ]
            return SimpleNamespace(data=matched)
        if self._op == "insert":
            rec = dict(self._payload)
            rec.setdefault("id", f"{self.name[:4]}-{len(rows) + 1}")
            rows.append(rec)
            return SimpleNamespace(data=[dict(rec)])
        if self._op == "update":
            updated = []
            for r in rows:
                if all(str(r.get(c)) == str(v) for c, v in self._filters):
                    r.update(self._payload)
                    updated.append(dict(r))
            return SimpleNamespace(data=updated)
        return SimpleNamespace(data=[])


def _http_error(code: int, msg: str = "Bad credentials") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.github.com/repos/noro/vault/contents/k.md",
        code=code,
        msg=msg,
        hdrs=None,
        fp=io.BytesIO(json.dumps({"message": msg}).encode("utf-8")),
    )


class TestR08ObsidianWriter:
    def test_explicit_401_and_403_handling_fails_fast_without_leaking_token(self, monkeypatch):
        monkeypatch.setattr(settings, "OBSIDIAN_GITHUB_TOKEN", "ghp_super_secret_token_xyz")
        monkeypatch.setattr(settings, "OBSIDIAN_VAULT_REPO", "norolabs/vault")
        monkeypatch.setattr(settings, "OBSIDIAN_VAULT_BRANCH", "main")

        with patch("urllib.request.urlopen", side_effect=_http_error(401, "Bad credentials")) as mock_open:
            res_401 = _write_to_github(path="knowledge/a.md", content="# doc", message="msg")
            assert res_401["status"] == "failed"
            assert res_401["auth_failed"] is True
            assert res_401["error_code"] == "GITHUB_AUTH_401"
            assert "ghp_super_secret_token_xyz" not in str(res_401)
            # Failed fast on SHA check (1 HTTP call, no wasted PUT)
            assert mock_open.call_count == 1

        with patch("urllib.request.urlopen", side_effect=_http_error(403, "Resource not accessible")):
            res_403 = _write_to_github(path="knowledge/a.md", content="# doc", message="msg")
            assert res_403["status"] == "failed"
            assert res_403["auth_failed"] is True
            assert res_403["error_code"] == "GITHUB_AUTH_403"

    def test_safe_retry_after_401_updates_existing_knowledge_without_duplicating(self, monkeypatch):
        db = _FakeObsidianDB()
        db.candidates.append({
            "id": "cand-001",
            "status": "APPROVED",
            "pattern": "meta_budget_hold_bf",
            "scope": "CLIENT_ONLY",
            "statement": "Em Black Friday com 100+ mudanças concorrentes, evitar causalidade direta.",
            "context": {"platform": "meta"},
            "observed_outcome": {"outcome_status": "INCONCLUSIVE", "action_type": "NO_ACTION_REQUIRED"},
            "evidence_refs": ["out-1"],
            "evidence_level": "OBSERVATION",
        })

        monkeypatch.setattr(settings, "OBSIDIAN_GITHUB_TOKEN", "ghp_expired_token")
        monkeypatch.setattr(settings, "OBSIDIAN_VAULT_REPO", "norolabs/vault")
        monkeypatch.setattr(settings, "OBSIDIAN_VAULT_BRANCH", "main")

        # 1st attempt: token expired -> 401, knowledge saved ONCE with obsidian_status=FAILED
        with (
            patch("app.core.obsidian_writer.get_supabase", return_value=db),
            patch("urllib.request.urlopen", side_effect=_http_error(401)),
        ):
            r1 = promote_to_knowledge(learning_candidate_id="cand-001", actor="maico@norolabs.com.br")
            assert r1["status"] == "COMPLETED"
            assert r1["obsidian_status"] == "FAILED"
            assert r1["auth_failed"] is True
            assert r1["error_code"] == "GITHUB_AUTH_401"
            assert len(db.knowledge) == 1
            kid = r1["knowledge_id"]

        # Token rotated -> retry_obsidian_sync succeeds WITHOUT duplicating core_agency_knowledge
        monkeypatch.setattr(settings, "OBSIDIAN_GITHUB_TOKEN", "ghp_fresh_valid_token")

        class _FakeResp:
            def __init__(self, payload: dict):
                self._payload = payload

            def read(self):
                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def _fake_urlopen(req, timeout=15):
            if req.get_method() == "GET":
                raise _http_error(404, "Not Found")
            return _FakeResp({
                "content": {
                    "html_url": "https://github.com/norolabs/vault/blob/main/knowledge/client/meta/doc.md",
                    "sha": "sha123",
                }
            })

        with (
            patch("app.core.obsidian_writer.get_supabase", return_value=db),
            patch("urllib.request.urlopen", side_effect=_fake_urlopen) as mock_open,
        ):
            r2 = retry_obsidian_sync(kid, actor="maico@norolabs.com.br")
            assert r2["status"] == "WRITTEN"
            assert r2["obsidian_status"] == "WRITTEN"
            assert r2["obsidian_url"].startswith("https://github.com/norolabs/vault/")
            assert r2["auth_failed"] is False
            assert len(db.knowledge) == 1  # ZERO duplicate knowledge rows!
            assert db.knowledge[0]["obsidian_status"] == "WRITTEN"

            # 3rd call (already WRITTEN) -> idempotent no-op, ZERO GitHub calls
            mock_open.reset_mock()
            r3 = retry_obsidian_sync(kid, actor="maico@norolabs.com.br")
            assert r3["status"] == "ALREADY_WRITTEN"
            assert r3["retried"] is False
            mock_open.assert_not_called()

            # Re-promoting same candidate -> returns EXISTING_KNOWLEDGE with WRITTEN, zero duplicates
            r4 = promote_to_knowledge(learning_candidate_id="cand-001", actor="maico@norolabs.com.br")
            assert r4["status"] == "EXISTING_KNOWLEDGE"
            assert r4["obsidian_status"] == "WRITTEN"
            assert len(db.knowledge) == 1


# ══════════════════════════════════════════════════════════════════════════════
# 3. Fireflies Auto-Trigger & Idempotent Action-Item Extraction
# ══════════════════════════════════════════════════════════════════════════════


class _FakeFirefliesDB:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {
            "core_fireflies_client_mapping": [],
            "core_fireflies_mapping_rules": [],
            "core_fireflies_events": [],
            "core_meetings": [],
            "core_meeting_transcripts": [],
            "core_meeting_summaries": [],
            "core_tasks": [],
        }

    def table(self, name: str):
        return _FakeFFTable(self, name)


class _FakeFFTable:
    def __init__(self, db: _FakeFirefliesDB, name: str) -> None:
        self.db = db
        self.name = name
        self._op = "select"
        self._filters: list[tuple[str, object]] = []
        self._payload = None

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def insert(self, payload: dict):
        self._op = "insert"
        self._payload = dict(payload)
        return self

    def update(self, payload: dict):
        self._op = "update"
        self._payload = dict(payload)
        return self

    def eq(self, col: str, val):
        self._filters.append((col, val))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _n: int):
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self._op == "select":
            matched = [
                dict(r) for r in rows
                if all(str(r.get(c)) == str(v) for c, v in self._filters)
            ]
            return SimpleNamespace(data=matched)
        if self._op == "insert":
            rec = dict(self._payload)
            rec.setdefault("id", f"{self.name[:6]}-{len(rows) + 1}")
            rows.append(rec)
            return SimpleNamespace(data=[dict(rec)])
        if self._op == "update":
            updated = []
            for r in rows:
                if all(str(r.get(c)) == str(v) for c, v in self._filters):
                    r.update(self._payload)
                    updated.append(dict(r))
            return SimpleNamespace(data=updated)
        return SimpleNamespace(data=[])


class TestFirefliesAutoTrigger:
    def test_auto_trigger_creates_meeting_and_extracts_action_items_idempotently(self, monkeypatch):
        monkeypatch.delenv("FIREFLIES_AUTO_ANALYZE", raising=False)
        monkeypatch.delenv("FIREFLIES_AUTO_CREATE_TASKS", raising=False)

        db = _FakeFirefliesDB()
        db.tables["core_fireflies_client_mapping"].append({
            "fireflies_meeting_id": "ff-meet-99",
            "client_id": "lk-sneakers",
        })
        db.tables["core_fireflies_events"].append({
            "id": "ev-1",
            "fireflies_meeting_id": "ff-meet-99",
            "event_type": "Transcription completed",
            "status": "received",
        })

        fake_transcript = {
            "id": "ff-meet-99",
            "title": "Alinhamento Semanal LK Sneakers",
            "date": 1764000000000,
            "duration": 1800,
            "organizer_email": "maico@norolabs.com.br",
            "meeting_link": "https://app.fireflies.ai/view/ff-meet-99",
            "participants": ["maico@norolabs.com.br", "lk@lksneakers.com.br"],
            "sentences": [
                {"speaker_name": "Maico", "text": "Vamos revisar campanhas PMax até sexta."},
            ],
        }
        fake_analysis = {
            "summary": "Revisão semanal de performance e próximos passos.",
            "decisions": [{"decision": "Manter budget de Meta estável", "rationale": "Black Friday", "owner": "Maico"}],
            "action_items": [
                {"task": "Revisar campanhas PMax", "owner": "Maico", "due_date": "2026-11-28", "priority": "HIGH"},
                {"task": "Auditar divergência de pedidos Shopify", "owner": None, "due_date": None, "priority": "MEDIUM"},
            ],
            "risks": [],
            "open_questions": [],
            "follow_ups": [],
        }

        with (
            patch("app.routers.fireflies_webhook.get_supabase", return_value=db),
            patch("app.core.fireflies_adapter.fetch_transcript", return_value=fake_transcript),
            patch("app.core.meeting_analysis.analyze_transcript", return_value=fake_analysis) as mock_analyze,
        ):
            # 1st run: creates meeting, transcript, summary, and 2 action-item tasks
            ff_wh._process_event("ev-1", "ff-meet-99")
            assert len(db.tables["core_meetings"]) == 1
            assert len(db.tables["core_meeting_transcripts"]) == 1
            assert len(db.tables["core_meeting_summaries"]) == 1
            assert len(db.tables["core_tasks"]) == 2
            assert db.tables["core_meetings"][0]["processing_status"] == "done"
            assert db.tables["core_fireflies_events"][0]["status"] == "processed"
            commitments = json.loads(db.tables["core_meeting_summaries"][0]["commitments"])
            assert len(commitments) == 2
            assert mock_analyze.call_count == 1

            # 2nd run (replay / redelivery): 100% idempotent — zero duplicates
            ff_wh._process_event("ev-1", "ff-meet-99")
            assert len(db.tables["core_meetings"]) == 1
            assert len(db.tables["core_meeting_transcripts"]) == 1
            assert len(db.tables["core_meeting_summaries"]) == 1
            assert len(db.tables["core_tasks"]) == 2
            assert mock_analyze.call_count == 1

    def test_native_fireflies_action_items_fallback_when_claude_unavailable(self, monkeypatch):
        from app.core.meeting_analysis import MeetingAnalysisUnavailable

        db = _FakeFirefliesDB()
        db.tables["core_fireflies_client_mapping"].append({
            "fireflies_meeting_id": "ff-native-1",
            "client_id": "enutri",
        })
        db.tables["core_fireflies_events"].append({
            "id": "ev-2",
            "fireflies_meeting_id": "ff-native-1",
            "event_type": "Transcription completed",
            "status": "received",
        })
        fake_transcript = {
            "id": "ff-native-1",
            "title": "Sync Enutri",
            "participants": ["maico@norolabs.com.br"],
            "sentences": [{"speaker_name": "Maico", "text": "Ajustar pixel."}],
            "summary": {
                "overview": "Alinhamento técnico de tracking.",
                "action_items": "- Validar token read-only Shopify\n- Conferir eventos Pix no GA4",
            },
        }

        with (
            patch("app.routers.fireflies_webhook.get_supabase", return_value=db),
            patch("app.core.fireflies_adapter.fetch_transcript", return_value=fake_transcript),
            patch(
                "app.core.meeting_analysis.analyze_transcript",
                side_effect=MeetingAnalysisUnavailable("DEGRADED_BLOCKED_CREDITS"),
            ),
        ):
            ff_wh._process_event("ev-2", "ff-native-1")

        assert len(db.tables["core_meetings"]) == 1
        assert len(db.tables["core_meeting_summaries"]) == 1
        assert db.tables["core_meeting_summaries"][0]["processor"] == "fireflies_native"
        assert len(db.tables["core_tasks"]) == 2
        assert {t["title"] for t in db.tables["core_tasks"]} == {
            "Validar token read-only Shopify",
            "Conferir eventos Pix no GA4",
        }
