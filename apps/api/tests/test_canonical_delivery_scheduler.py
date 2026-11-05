"""
Tests for canonical_delivery_scheduler.run_pending_deliveries.

Coverage:
  - flag gate: skips when REPORT_DELIVERY_ENABLED not set
  - weekly canonical send: PUBLISHED weekly + settings.weekly_enabled=True + active recipient
  - monthly canonical send: PUBLISHED monthly + settings.monthly_enabled=True + active recipient
  - no recipient: skips when recipients list is empty
  - inactive recipient: skips when active=False
  - no settings: skips when no settings row for client
  - not approved: skips DRAFT / APPROVED / READY_FOR_REVIEW reports
  - duplicate send: second run on DELIVERED receipt returns already_delivered count
  - deterministic delivery_id: same (report, version, email) always uses same delivery_id
  - failed send retry: DELIVERY_FAILED with attempt < MAX_RETRIES is retried
  - failed send max_retries: DELIVERY_FAILED with attempt >= MAX_RETRIES is skipped
  - cross-client isolation: settings for client_a do not cause delivery for client_b
  - weekly_disabled: monthly settings don't trigger weekly report delivery
  - monthly_disabled: weekly settings don't trigger monthly report delivery
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

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.canonical_reports import Reports, Flags, ReportError
from app.core.canonical_delivery_scheduler import (
    run_pending_deliveries,
    _delivery_id_for,
    _recipient_hash,
    MAX_RETRIES,
)


# ── In-memory CAS store (copied from test_canonical_reports_delivery.py) ───────

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

    def max_version(self, report_id):
        with self._lock:
            prefix = f"report:{report_id}:v"
            versions = [
                int(k[len(prefix):])
                for k in self.rows
                if k.startswith(prefix) and k[len(prefix):].isdigit()
            ]
            return max(versions) if versions else None


# ── Helpers ───────────────────────────────────────────────────────────────────

_ALL_FLAGS = Flags(
    CANONICAL_WEEKLY_REVIEW_ENABLED=True,
    CANONICAL_MONTHLY_REVIEW_ENABLED=True,
    REPORT_PUBLISH_ENABLED=True,
    REPORT_DELIVERY_ENABLED=True,
)


def _artifact(client_id: str = "acme", engine: str = "H7") -> dict:
    if engine == "H7":
        ps, pe, ga = "2026-10-12", "2026-10-18", "2026-10-19T12:00:00+00:00"
    else:
        ps, pe, ga = "2026-10-01", "2026-10-31", "2026-11-01T08:00:00+00:00"
    return {
        "client_id": client_id,
        "canonical_engine": engine,
        "engine_version": "a" * 64,
        "version": 1,
        "period_start": ps,
        "period_end": pe,
        "generated_at": ga,
        "source_state": [{"source": "meta", "source_state": "HEALTHY"}],
        "certification_status": [{"metric_key": "revenue", "certification_status": "PROVISIONAL"}],
        "review_status": "READY",
        "content": {
            "client_id": client_id,
            "analysis_period": {"start": ps, "end": pe},
            "business_truth": f"{client_id} report.",
            "facts": [{"metric_key": "revenue", "value": 10000.0}],
            "channels": {},
            "hypotheses": [],
            "recommendations": [],
            "limitations": ["PROVISIONAL"],
            "status": "READY_FOR_REVIEW",
            "core_writes": False,
            "platform_writes": False,
        },
        "evidence_refs": [{"source": "core_metric_snapshots", "client_id": client_id,
                           "period_start": ps, "period_end": pe, "metric_count": 1}],
    }


def _make_published(client_id: str = "acme", engine: str = "H7"):
    """Return (store, reports, published_doc)."""
    store = MemStore()
    rpts = Reports(store, _ALL_FLAGS)
    doc = rpts.generate(_artifact(client_id=client_id, engine=engine))
    at = datetime.now(timezone.utc).isoformat()
    for state in ("READY_FOR_REVIEW", "APPROVED", "PUBLISHED"):
        doc = rpts.transition(
            doc["report_id"], 1, state,
            actor="test-human", at=at,
            expected_hash=doc["artifact_hash"],
        )
    return store, rpts, doc


def _settings_row(
    client_id: str,
    weekly_enabled: bool = True,
    monthly_enabled: bool = False,
    email: str = "noro@example.com",
    active: bool = True,
) -> dict:
    return {
        "client_id": client_id,
        "weekly_enabled": weekly_enabled,
        "monthly_enabled": monthly_enabled,
        "human_approval_required": True,
        "recipients": [{"id": str(_uuid.uuid4()), "name": "Test", "email": email, "active": active}],
    }


class _FakeTable:
    """Query builder that routes to MemStore or settings_rows based on method chain."""

    def __init__(self, name: str, store: MemStore, settings_rows: list[dict]):
        self.name = name
        self.store = store
        self.settings_rows = settings_rows
        self._eq_key: str | None = None
        self._like_prefix: str | None = None
        self._contains_filter: dict | None = None

    def select(self, *args): return self
    def like(self, col: str, pattern: str):
        if col == "entity_key":
            self._like_prefix = pattern.replace("%", "")
        return self
    def contains(self, col: str, val: dict):
        if col == "document":
            self._contains_filter = val
        return self
    def eq(self, col: str, val: str):
        if col == "entity_key":
            self._eq_key = val
        return self
    def limit(self, n): return self
    def order(self, *args, **kwargs): return self

    def execute(self):
        class _R:
            pass
        r = _R()
        if self.name == "core_canonical_report_records":
            if self._eq_key:
                # Single key lookup (CoreStore.get / max_version)
                entry = self.store.rows.get(self._eq_key)
                r.data = [copy.deepcopy(entry)] if entry else []
            elif self._like_prefix:
                rows = []
                for key, entry in self.store.rows.items():
                    if not key.startswith(self._like_prefix):
                        continue
                    doc = entry.get("document", {})
                    if self._contains_filter is None or all(
                        doc.get(k) == v for k, v in self._contains_filter.items()
                    ):
                        rows.append({"entity_key": key, "document": copy.deepcopy(doc)})
                r.data = rows
            else:
                r.data = []
        elif self.name == "core_report_delivery_settings":
            r.data = copy.deepcopy(self.settings_rows)
        else:
            r.data = []
        return r


class _FakeDB:
    """Minimal fake DB client for delivery scheduler tests."""

    def __init__(self, store: MemStore, settings_rows: list[dict]):
        self.store = store
        self.settings_rows = settings_rows

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(name, self.store, self.settings_rows)

    def rpc(self, fn: str, params: dict):
        key = params["p_key"]
        revision = params["p_revision"]
        document = params["p_document"]
        old = self.store.rows.get(key)
        old_rev = old["revision"] if old else None

        class _Exec:
            pass
        ex = _Exec()
        if old_rev != revision:
            ex.data = False
        else:
            self.store.rows[key] = {
                "revision": 1 if old is None else old["revision"] + 1,
                "document": copy.deepcopy(document),
            }
            ex.data = True

        class _Res:
            pass
        res = _Res()
        res.execute = lambda: ex
        return res


def _make_db_mock(store: MemStore, settings_rows: list[dict]) -> _FakeDB:
    return _FakeDB(store, settings_rows)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestFlagGate:
    def test_skips_when_flag_not_set(self):
        with patch.dict(os.environ, {"REPORT_DELIVERY_ENABLED": "false"}):
            result = run_pending_deliveries()
        assert result.get("skipped") == "REPORT_DELIVERY_ENABLED_NOT_SET"

    def test_skips_when_flag_missing(self):
        env = {k: v for k, v in os.environ.items() if k != "REPORT_DELIVERY_ENABLED"}
        with patch.dict(os.environ, env, clear=True):
            result = run_pending_deliveries()
        assert result.get("skipped") == "REPORT_DELIVERY_ENABLED_NOT_SET"


class TestWeeklyCanonicalDelivery:
    def test_delivers_weekly_report_to_enabled_recipient(self):
        store, _, doc = _make_published(client_id="lk", engine="H7")
        email = "noro@test.com"
        settings = [_settings_row("lk", weekly_enabled=True, monthly_enabled=False, email=email)]
        db = _make_db_mock(store, settings)

        sent_to = []

        def _mock_send(to, subject, html, from_name=None):
            sent_to.append(to)
            return True, f"msg-{to[:4]}", None

        with (
            patch.dict(os.environ, {"REPORT_DELIVERY_ENABLED": "true"}),
            patch("app.services.resend.send_email_tracked", side_effect=_mock_send),
        ):
            result = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)

        assert result["dispatched"] == 1
        assert result["already_delivered"] == 0
        assert result["failed"] == 0
        assert email in sent_to

    def test_weekly_enabled_false_skips_weekly_report(self):
        store, _, doc = _make_published(client_id="lk", engine="H7")
        settings = [_settings_row("lk", weekly_enabled=False, monthly_enabled=False)]
        db = _make_db_mock(store, settings)

        with (
            patch.dict(os.environ, {"REPORT_DELIVERY_ENABLED": "true"}),
            patch("app.services.resend.send_email_tracked", return_value=(True, "msg", None)),
        ):
            result = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)

        assert result["dispatched"] == 0


class TestMonthlyCanonicalDelivery:
    def test_delivers_monthly_report_to_enabled_recipient(self):
        store, _, doc = _make_published(client_id="lk", engine="H8")
        email = "noro@test.com"
        settings = [_settings_row("lk", weekly_enabled=False, monthly_enabled=True, email=email)]
        db = _make_db_mock(store, settings)

        sent_to = []

        def _mock_send(to, subject, html, from_name=None):
            sent_to.append(to)
            return True, f"msg-{to[:4]}", None

        with (
            patch.dict(os.environ, {"REPORT_DELIVERY_ENABLED": "true"}),
            patch("app.services.resend.send_email_tracked", side_effect=_mock_send),
        ):
            result = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)

        assert result["dispatched"] == 1
        assert email in sent_to

    def test_monthly_disabled_skips_monthly_report(self):
        store, _, doc = _make_published(client_id="lk", engine="H8")
        settings = [_settings_row("lk", weekly_enabled=True, monthly_enabled=False)]
        db = _make_db_mock(store, settings)

        with (
            patch.dict(os.environ, {"REPORT_DELIVERY_ENABLED": "true"}),
            patch("app.services.resend.send_email_tracked", return_value=(True, "msg", None)),
        ):
            result = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)

        assert result["dispatched"] == 0


class TestNoRecipient:
    def test_skips_when_recipients_empty(self):
        store, _, _ = _make_published(client_id="lk")
        settings = [{
            "client_id": "lk",
            "weekly_enabled": True,
            "monthly_enabled": False,
            "recipients": [],
        }]
        db = _make_db_mock(store, settings)

        with (
            patch.dict(os.environ, {"REPORT_DELIVERY_ENABLED": "true"}),
            patch("app.services.resend.send_email_tracked", return_value=(True, "msg", None)),
        ):
            result = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)

        assert result["dispatched"] == 0
        assert result["skipped_no_settings"] >= 1

    def test_skips_inactive_recipient(self):
        store, _, _ = _make_published(client_id="lk")
        settings = [_settings_row("lk", weekly_enabled=True, email="noro@test.com", active=False)]
        db = _make_db_mock(store, settings)

        with (
            patch.dict(os.environ, {"REPORT_DELIVERY_ENABLED": "true"}),
            patch("app.services.resend.send_email_tracked", return_value=(True, "msg", None)),
        ):
            result = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)

        assert result["dispatched"] == 0

    def test_skips_when_no_settings_row(self):
        store, _, _ = _make_published(client_id="lk")
        db = _make_db_mock(store, [])  # no settings rows

        with (
            patch.dict(os.environ, {"REPORT_DELIVERY_ENABLED": "true"}),
            patch("app.services.resend.send_email_tracked", return_value=(True, "msg", None)),
        ):
            result = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)

        assert result["dispatched"] == 0
        assert result["skipped_no_settings"] >= 1


class TestNotPublished:
    def test_draft_report_not_delivered(self):
        store = MemStore()
        rpts = Reports(store, _ALL_FLAGS)
        doc = rpts.generate(_artifact("lk"))  # stays DRAFT
        settings = [_settings_row("lk", weekly_enabled=True)]
        db = _make_db_mock(store, settings)

        with (
            patch.dict(os.environ, {"REPORT_DELIVERY_ENABLED": "true"}),
            patch("app.services.resend.send_email_tracked", return_value=(True, "msg", None)),
        ):
            result = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)

        assert result["dispatched"] == 0

    def test_approved_not_published_not_delivered(self):
        store = MemStore()
        rpts = Reports(store, _ALL_FLAGS)
        doc = rpts.generate(_artifact("lk"))
        at = datetime.now(timezone.utc).isoformat()
        for state in ("READY_FOR_REVIEW", "APPROVED"):
            doc = rpts.transition(doc["report_id"], 1, state,
                                  actor="human", at=at, expected_hash=doc["artifact_hash"])
        settings = [_settings_row("lk", weekly_enabled=True)]
        db = _make_db_mock(store, settings)

        with (
            patch.dict(os.environ, {"REPORT_DELIVERY_ENABLED": "true"}),
            patch("app.services.resend.send_email_tracked", return_value=(True, "msg", None)),
        ):
            result = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)

        assert result["dispatched"] == 0


class TestDuplicateSend:
    def test_no_duplicate_on_second_run(self):
        store, _, doc = _make_published(client_id="lk")
        email = "noro@test.com"
        settings = [_settings_row("lk", weekly_enabled=True, email=email)]
        db = _make_db_mock(store, settings)

        call_count = [0]

        def _mock_send(to, subject, html, from_name=None):
            call_count[0] += 1
            return True, f"msg-{call_count[0]}", None

        env = {"REPORT_DELIVERY_ENABLED": "true"}
        with (
            patch.dict(os.environ, env),
            patch("app.services.resend.send_email_tracked", side_effect=_mock_send),
        ):
            r1 = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)
            r2 = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)

        assert r1["dispatched"] == 1
        assert r2["already_delivered"] == 1
        assert r2["dispatched"] == 0
        assert call_count[0] == 1  # provider called exactly once

    def test_deterministic_delivery_id(self):
        rh = _recipient_hash("noro@test.com")
        did1 = _delivery_id_for("report-abc", 1, rh)
        did2 = _delivery_id_for("report-abc", 1, rh)
        assert did1 == did2

    def test_different_version_different_delivery_id(self):
        rh = _recipient_hash("noro@test.com")
        did1 = _delivery_id_for("report-abc", 1, rh)
        did2 = _delivery_id_for("report-abc", 2, rh)
        assert did1 != did2

    def test_different_recipient_different_delivery_id(self):
        rh1 = _recipient_hash("a@test.com")
        rh2 = _recipient_hash("b@test.com")
        did1 = _delivery_id_for("report-abc", 1, rh1)
        did2 = _delivery_id_for("report-abc", 1, rh2)
        assert did1 != did2


class TestRetry:
    def test_retries_failed_delivery(self):
        store, _, doc = _make_published(client_id="lk")
        email = "noro@test.com"
        settings = [_settings_row("lk", weekly_enabled=True, email=email)]
        db = _make_db_mock(store, settings)

        attempt_no = [0]

        def _flaky_send(to, subject, html, from_name=None):
            attempt_no[0] += 1
            if attempt_no[0] == 1:
                return False, None, "timeout"
            return True, "msg-ok", None

        env = {"REPORT_DELIVERY_ENABLED": "true"}
        with (
            patch.dict(os.environ, env),
            patch("app.services.resend.send_email_tracked", side_effect=_flaky_send),
        ):
            r1 = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)
            r2 = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)

        assert r1["failed"] == 1
        assert r2["dispatched"] == 1

    def test_stops_at_max_retries(self):
        store, _, doc = _make_published(client_id="lk")
        email = "noro@test.com"
        settings = [_settings_row("lk", weekly_enabled=True, email=email)]
        db = _make_db_mock(store, settings)

        def _always_fail(to, subject, html, from_name=None):
            return False, None, "provider down"

        env = {"REPORT_DELIVERY_ENABLED": "true"}
        with (
            patch.dict(os.environ, env),
            patch("app.services.resend.send_email_tracked", side_effect=_always_fail),
        ):
            for _ in range(MAX_RETRIES):
                run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)
            # After MAX_RETRIES failures, one more run should skip (not retry)
            result = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)

        assert result["skipped_max_retries"] == 1
        assert result["dispatched"] == 0
        assert result["failed"] == 0


class TestCrossClientIsolation:
    def test_client_a_settings_do_not_trigger_client_b_delivery(self):
        store_a, _, doc_a = _make_published(client_id="client-a")
        store_b, _, doc_b = _make_published(client_id="client-b")

        # Merge both stores
        store = MemStore()
        store.rows.update(store_a.rows)
        store.rows.update(store_b.rows)

        # Only client-a has delivery settings
        settings = [_settings_row("client-a", weekly_enabled=True, email="a@test.com")]
        db = _make_db_mock(store, settings)

        sent_to = []

        def _mock_send(to, subject, html, from_name=None):
            sent_to.append(to)
            return True, f"msg-{to[:4]}", None

        with (
            patch.dict(os.environ, {"REPORT_DELIVERY_ENABLED": "true"}),
            patch("app.services.resend.send_email_tracked", side_effect=_mock_send),
        ):
            result = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)

        assert result["dispatched"] == 1
        assert sent_to == ["a@test.com"]  # client-b never sent

    def test_separate_recipients_per_client(self):
        store_a, _, _ = _make_published(client_id="client-a")
        store_b, _, _ = _make_published(client_id="client-b")

        store = MemStore()
        store.rows.update(store_a.rows)
        store.rows.update(store_b.rows)

        settings = [
            _settings_row("client-a", weekly_enabled=True, email="a@test.com"),
            _settings_row("client-b", weekly_enabled=True, email="b@test.com"),
        ]
        db = _make_db_mock(store, settings)

        sent_to = []

        def _mock_send(to, subject, html, from_name=None):
            sent_to.append(to)
            return True, f"msg-{to[:4]}", None

        with (
            patch.dict(os.environ, {"REPORT_DELIVERY_ENABLED": "true"}),
            patch("app.services.resend.send_email_tracked", side_effect=_mock_send),
        ):
            result = run_pending_deliveries(_db=db, _flags=_ALL_FLAGS)

        assert result["dispatched"] == 2
        assert set(sent_to) == {"a@test.com", "b@test.com"}
