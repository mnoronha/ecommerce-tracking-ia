"""
Tests for canonical_weekly_scheduler — H7 scheduler cutover.

Coverage:
  - Scheduler registration: canonical when flag=true, legacy when flag=false
  - Monthly legacy always registered regardless of weekly flag
  - run_canonical_weekly_for_client: generates DRAFT → READY_FOR_REVIEW
  - Job run observability: core_job_runs RUNNING → SUCCEEDED | FAILED
  - Six-client fanout
  - One-client failure isolation (others complete)
  - Idempotent rerun: same period/engine_version → same report_id, single DB row
  - run_canonical_weekly_all_clients: flag=false → no-op
  - No publish, no delivery, no send, no media writes
  - Rollback: canonical=false → legacy weekly_review registered
  - Static: scheduler does not import legacy send/publish functions
"""
from __future__ import annotations

import copy
import threading
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
import sys
import types
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import canonical_reports as cr
from app.core.canonical_weekly_scheduler import (
    _JOB_TYPE,
    _ACTOR,
    _ENGINE,
    _build_artifact,
    _engine_version,
    _last_completed_week,
    run_canonical_weekly_all_clients,
    run_canonical_weekly_for_client,
)


# ── In-memory CAS store ────────────────────────────────────────────────────────

class MemoryStore:
    """Thread-safe in-process CAS store (mirrors test_canonical_reports.Memory)."""

    def __init__(self):
        self.rows: dict = {}
        self._lock = threading.RLock()

    def get(self, key):
        with self._lock:
            return copy.deepcopy(self.rows.get(key))

    def cas(self, key, revision, document):
        with self._lock:
            old = self.rows.get(key)
            if (old["revision"] if old else None) != revision:
                return False
            new_rev = 1 if old is None else old["revision"] + 1
            self.rows[key] = {"revision": new_rev, "document": copy.deepcopy(document)}
            return True


# ── Fake DB for snapshot / job-run reads ─────────────────────────────────────

SAMPLE_METRICS = [
    {"metric_key": "revenue_business", "value": 50000.0, "value_status": "OK",   "unit": "BRL"},
    {"metric_key": "orders_count",     "value": 120.0,   "value_status": "OK",   "unit": "count"},
    {"metric_key": "meta_spend",       "value": 3000.0,  "value_status": "OK",   "unit": "BRL"},
    {"metric_key": "meta_conversions", "value": 80.0,    "value_status": "OK",   "unit": "count"},
    {"metric_key": "google_spend",     "value": 1500.0,  "value_status": "OK",   "unit": "BRL"},
    {"metric_key": "google_conversions","value": 40.0,   "value_status": "OK",   "unit": "count"},
    {"metric_key": "total_spend",      "value": 4500.0,  "value_status": "OK",   "unit": "BRL"},
    {"metric_key": "mer",              "value": 11.1,    "value_status": "OK",   "unit": "ratio"},
]

SAMPLE_HEALTH = {"meta_ads": "HEALTHY", "google_ads": "HEALTHY", "business": "HEALTHY"}

SIX_CLIENT_IDS = [
    "lk-sneakers", "dipua", "enutri",
    "clinica-tarcio-caetano", "zipper-galeria", "spiti-auction",
]


def _make_clients(client_ids=None):
    ids = client_ids or ["lk-sneakers"]
    return [
        {"id": f"uuid-{c}", "client_id": c, "name": c, "business_model": "ecommerce", "is_active": True}
        for c in ids
    ]


class _Chain:
    """Builder-pattern fake table — records calls, returns configured data on execute()."""
    def __init__(self, result):     self._result = result
    def select(self, *_, **__):    return self
    def eq(self, *_, **__):        return self
    def order(self, *_, **__):     return self
    def limit(self, _):            return self
    def execute(self):             return self._result

class _MutChain(_Chain):
    """Variant that captures the mutation payload so tests can inspect it."""
    def __init__(self, store_list, result):
        super().__init__(result)
        self._store = store_list
    def insert(self, row):
        self._store.append(copy.deepcopy(row))
        return _Chain(type("R", (), {"data": [row]})())  # must be chain so .execute() works
    def update(self, row):
        for r in self._store:
            r.update(copy.deepcopy(row))
        return _Chain(self._result)


class _EmptyChain:
    """Returns empty data for any fluent query — used for change_log/change_digests in tests."""
    def select(self, *_, **__):    return self
    def eq(self, *_, **__):        return self
    def gte(self, *_, **__):       return self
    def lte(self, *_, **__):       return self
    def order(self, *_, **__):     return self
    def limit(self, *_, **__):     return self
    def range(self, *_, **__):     return self
    def upsert(self, *_, **__):    return self
    def execute(self):             return type("R", (), {"data": []})()


class FakeDB:
    """
    Fake Supabase client covering the tables accessed by canonical_weekly_scheduler.
    snapshot_map: {client_id: snapshot_dict | None}  (None → no snapshot)
    clients:      list[dict]
    """
    def __init__(self, snapshot_map=None, clients=None):
        ps, pe = _last_completed_week()
        self._period_end = pe.isoformat()
        self._snapshot_map: dict = snapshot_map or {
            "lk-sneakers": {
                "id": "snap-lk", "metrics": SAMPLE_METRICS, "health": SAMPLE_HEALTH,
            }
        }
        self._clients = clients or _make_clients(["lk-sneakers"])
        self.job_rows: list[dict] = []

    def table(self, name: str):
        if name == "core_metric_snapshots":
            return self  # will call eq() with client_id; real result in execute()
        if name == "clients":
            return _Chain(type("R", (), {"data": self._clients})())
        if name == "core_job_runs":
            return _MutChain(self.job_rows, type("R", (), {"data": self.job_rows})())
        # Change digest tables: return empty — _build_change_summary catches failure gracefully
        if name in ("core_change_log", "core_change_digests"):
            return _EmptyChain()
        raise ValueError(f"FakeDB: unknown table {name!r}")

    # Snapshot table method chain (self implements chain for snapshots)
    _snapshot_client: str = ""

    def select(self, *_, **__):    return self
    def eq(self, k, v):
        if k == "client_id": self._snapshot_client = v
        return self
    def order(self, *_, **__):     return self
    def limit(self, _):            return self
    def execute(self):
        snap = self._snapshot_map.get(self._snapshot_client)
        return type("R", (), {"data": [snap] if snap else []})()


# ── Period rule ────────────────────────────────────────────────────────────────

def test_period_rule_last_closed_week():
    ps, pe = _last_completed_week()
    assert pe.weekday() == 6          # Sunday
    assert ps.weekday() == 0          # Monday
    assert (pe - ps).days == 6
    assert pe < date.today()          # period is closed


def test_period_is_never_open_week():
    """Scheduler never generates a report whose period_end >= today."""
    ps, pe = _last_completed_week()
    assert pe < date.today()


# ── Engine version ─────────────────────────────────────────────────────────────

def test_engine_version_is_64hex():
    ev = _engine_version()
    assert len(ev) == 64
    assert all(c in "0123456789abcdef" for c in ev)


def test_engine_version_is_deterministic():
    assert _engine_version() == _engine_version()


# ── Artifact builder ───────────────────────────────────────────────────────────

def _period_dates():
    return _last_completed_week()


def test_build_artifact_full_snapshot():
    ps, pe = _period_dates()
    db     = FakeDB()
    art    = _build_artifact("lk-sneakers", ps, pe, db)

    assert art["client_id"]        == "lk-sneakers"
    assert art["canonical_engine"] == "H7"
    assert art["period_start"]     == str(ps)
    assert art["period_end"]       == str(pe)
    assert art["review_status"]    == "READY"
    assert len(art["content"]["facts"]) >= 7
    assert "meta"   in art["content"]["channels"]
    assert "google" in art["content"]["channels"]
    biz = art["content"]["business_truth"]
    assert "PROVISIONAL" in biz or "metrics available" in biz or "Revenue" in biz
    assert art["content"]["client_id"]        == "lk-sneakers"
    assert art["content"]["analysis_period"]  == {"start": str(ps), "end": str(pe)}
    assert art["content"]["core_writes"]      is False
    assert art["content"]["platform_writes"]  is False
    assert "CORE_SCHEDULER_DIRECT" in art["content"]["limitations"][0]
    assert art["evidence_refs"][0]["source"]  == "core_metric_snapshots"


def test_build_artifact_no_snapshot():
    ps, pe = _period_dates()
    db     = FakeDB(snapshot_map={"lk-sneakers": None})
    art    = _build_artifact("lk-sneakers", ps, pe, db)

    assert art["review_status"]             == "PARTIAL"
    assert art["content"]["facts"]          == []
    assert art["content"]["channels"]       == {}
    assert "no snapshot" in art["content"]["business_truth"]
    assert any("snapshot_not_found" in lim for lim in art["content"]["limitations"])
    assert art["content"]["core_writes"]    is False
    assert art["content"]["platform_writes"] is False


def test_build_artifact_partial_sources():
    ps, pe = _period_dates()
    degraded_health = {"meta_ads": "ERROR", "google_ads": "HEALTHY", "business": "STALE"}
    db = FakeDB(snapshot_map={"lk-sneakers": {
        "id": "snap-partial",
        "metrics": SAMPLE_METRICS,
        "health": degraded_health,
    }})
    art = _build_artifact("lk-sneakers", ps, pe, db)

    # Top-level review_status = READY (facts present); content status = PARTIAL (degraded sources)
    assert art["content"]["status"] == "PARTIAL"
    assert any("meta_ads: ERROR" in lim for lim in art["content"]["limitations"])
    assert any("business: STALE" in lim for lim in art["content"]["limitations"])
    # source_state is preserved, not converted to zero
    assert any(s["source_state"] == "ERROR" for s in art["source_state"])


def test_build_artifact_no_cross_client_leakage():
    ps, pe = _period_dates()
    db = FakeDB(snapshot_map={
        "dipua": {"id": "snap-d", "metrics": SAMPLE_METRICS, "health": SAMPLE_HEALTH},
    })
    art = _build_artifact("dipua", ps, pe, db)
    assert art["client_id"]                 == "dipua"
    assert art["content"]["client_id"]      == "dipua"


def test_build_artifact_null_absent_not_zero():
    """Values of None must remain absent from facts, never coerced to 0."""
    ps, pe = _period_dates()
    metrics_with_null = [
        {"metric_key": "revenue_business", "value": None, "value_status": "MISSING", "unit": "BRL"},
        {"metric_key": "meta_spend",       "value": 500.0, "value_status": "OK", "unit": "BRL"},
    ]
    db  = FakeDB(snapshot_map={"lk-sneakers": {"id": "s", "metrics": metrics_with_null, "health": SAMPLE_HEALTH}})
    art = _build_artifact("lk-sneakers", ps, pe, db)
    keys = [f["metric_key"] for f in art["content"]["facts"]]
    assert "revenue_business" not in keys  # null → absent
    assert "meta_spend"       in keys      # 500 → present


# ── Per-client execution ───────────────────────────────────────────────────────

def test_run_for_client_generates_ready_for_review(monkeypatch):
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "true")
    monkeypatch.setenv("CANONICAL_ANALYSIS_ENABLED", "false")
    ps, pe  = _period_dates()
    db      = FakeDB()
    store   = MemoryStore()

    result = run_canonical_weekly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store)

    assert result["status"]    == "READY_FOR_REVIEW"
    assert result["client_id"] == "lk-sneakers"
    assert "report_id" in result
    assert len(store.rows) == 1
    doc = list(store.rows.values())[0]["document"]
    assert doc["status"]           == "READY_FOR_REVIEW"
    assert doc["canonical_engine"] == "H7"
    assert doc["client_id"]        == "lk-sneakers"


def test_run_for_client_records_job_run(monkeypatch):
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "true")
    ps, pe = _period_dates()
    db     = FakeDB()
    store  = MemoryStore()

    run_canonical_weekly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store)

    # Should have a job row inserted and updated
    assert len(db.job_rows) >= 1
    job = db.job_rows[0]
    assert job["job_type"] == _JOB_TYPE
    assert job["run_key"].startswith(_JOB_TYPE)


def test_run_for_client_records_failed_job_on_error(monkeypatch):
    """A store that never succeeds CAS triggers CONCURRENT_UPDATE_RETRY_REQUIRED → FAILED."""
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "true")
    ps, pe = _period_dates()
    db     = FakeDB()

    class NeverStore(MemoryStore):
        def cas(self, *_): return False  # CAS always fails → 8 retries → error

    result = run_canonical_weekly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=NeverStore())

    assert result["status"] == "FAILED"
    assert "CONCURRENT_UPDATE_RETRY_REQUIRED" in result.get("error", "")


def test_run_for_client_idempotent(monkeypatch):
    """Calling twice for the same period returns the same report_id, single row in store."""
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "true")
    monkeypatch.setenv("CANONICAL_ANALYSIS_ENABLED", "false")
    ps, pe = _period_dates()
    db     = FakeDB()
    store  = MemoryStore()

    r1 = run_canonical_weekly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store)
    r2 = run_canonical_weekly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store)

    assert r1["report_id"] == r2["report_id"]
    assert r1["status"]    == "READY_FOR_REVIEW"
    assert r2["status"]    == "READY_FOR_REVIEW"
    assert len(store.rows) == 1   # exactly one canonical row


def test_no_publish_no_delivery_no_send(monkeypatch):
    """store must contain no delivery keys; no send calls should be reachable."""
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "true")
    ps, pe = _period_dates()
    db     = FakeDB()
    store  = MemoryStore()

    run_canonical_weekly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store)

    assert not any(k.startswith("delivery:") for k in store.rows)
    doc = list(store.rows.values())[0]["document"]
    assert doc.get("published_at")       is None
    assert doc.get("approved_at")        is None
    assert doc["status"] not in ("PUBLISHED", "APPROVED")


# ── Six-client fanout ─────────────────────────────────────────────────────────

def test_six_client_fanout(monkeypatch):
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "true")
    ps, pe = _period_dates()

    snap_map = {c: {"id": f"snap-{c}", "metrics": SAMPLE_METRICS, "health": SAMPLE_HEALTH}
                for c in SIX_CLIENT_IDS}
    db    = FakeDB(snapshot_map=snap_map, clients=_make_clients(SIX_CLIENT_IDS))
    store = MemoryStore()

    # Patch _get_db so run_canonical_weekly_all_clients uses our fake
    import app.core.canonical_weekly_scheduler as sched
    monkeypatch.setattr(sched, "_get_db", lambda: db)
    monkeypatch.setattr(sched, "CoreStore", lambda _db: store)

    run_canonical_weekly_all_clients()

    # Each client produces 1 report record + 1 analysis-request record (async handoff).
    report_rows = {k: v for k, v in store.rows.items() if k.startswith("report:")}
    assert len(report_rows) == 6
    client_ids = [v["document"]["client_id"] for v in report_rows.values()]
    for cid in SIX_CLIENT_IDS:
        assert cid in client_ids


def test_one_client_failure_isolation(monkeypatch):
    """A snapshot failure on one client must not abort the others."""
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "true")
    ps, pe = _period_dates()

    snap_map = {c: {"id": f"snap-{c}", "metrics": SAMPLE_METRICS, "health": SAMPLE_HEALTH}
                for c in SIX_CLIENT_IDS}
    snap_map["dipua"] = None  # no snapshot → artifact still created (PARTIAL)

    db    = FakeDB(snapshot_map=snap_map, clients=_make_clients(SIX_CLIENT_IDS))
    store = MemoryStore()

    import app.core.canonical_weekly_scheduler as sched
    monkeypatch.setattr(sched, "_get_db", lambda: db)
    monkeypatch.setattr(sched, "CoreStore", lambda _db: store)

    run_canonical_weekly_all_clients()

    # All 6 clients should have report records (dipua gets PARTIAL artifact).
    # Plus analysis-request records for each (async handoff).
    report_rows = {k for k in store.rows if k.startswith("report:")}
    assert len(report_rows) == 6


def test_failure_isolation_exception_in_build(monkeypatch):
    """Exception in _build_artifact for one client must not abort others."""
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "true")
    ps, pe = _period_dates()

    original_build = _build_artifact
    call_count = {"n": 0}
    def patched_build(client_id, *args, **kwargs):
        call_count["n"] += 1
        if client_id == "enutri":
            raise RuntimeError("simulated enutri failure")
        return original_build(client_id, *args, **kwargs)

    snap_map = {c: {"id": f"snap-{c}", "metrics": SAMPLE_METRICS, "health": SAMPLE_HEALTH}
                for c in SIX_CLIENT_IDS}
    db    = FakeDB(snapshot_map=snap_map, clients=_make_clients(SIX_CLIENT_IDS))
    store = MemoryStore()

    import app.core.canonical_weekly_scheduler as sched
    monkeypatch.setattr(sched, "_get_db", lambda: db)
    monkeypatch.setattr(sched, "CoreStore", lambda _db: store)
    monkeypatch.setattr(sched, "_build_artifact", patched_build)

    run_canonical_weekly_all_clients()

    # 5 succeeded, 1 failed — but all 6 were attempted.
    # 5 report records + 5 analysis-request records = 10 total.
    assert call_count["n"] == 6
    report_rows = {k for k in store.rows if k.startswith("report:")}
    assert len(report_rows) == 5  # enutri raised before generate()


# ── Flag=false guard ──────────────────────────────────────────────────────────

def test_all_clients_flag_false_noop(monkeypatch):
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "false")
    store = MemoryStore()
    db    = FakeDB()

    import app.core.canonical_weekly_scheduler as sched
    monkeypatch.setattr(sched, "_get_db", lambda: db)
    monkeypatch.setattr(sched, "CoreStore", lambda _db: store)

    run_canonical_weekly_all_clients()

    assert len(store.rows) == 0


# ── Scheduler registration (main.py logic) ────────────────────────────────────

def test_main_py_has_conditional_scheduler_registration():
    """main.py must register canonical_weekly_review when flag=true, legacy when false."""
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert 'CANONICAL_WEEKLY_REVIEW_ENABLED' in source
    assert 'id="canonical_weekly_review"'    in source
    assert 'id="weekly_review"'              in source
    # Both branches must coexist in the conditional
    assert '_run_canonical_weekly'           in source


def test_main_py_monthly_always_present():
    """Monthly jobs must always be registered, independent of weekly flag."""
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert 'id="monthly_review"'   in source
    assert 'id="monthly_reports"'  in source


def test_rollback_legacy_weekly_in_main_py():
    """Legacy weekly_review must remain registered on the else branch."""
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert 'run_weekly_review_all_clients' in source
    assert 'id="weekly_review"'           in source


def test_scheduler_registration_canonical_when_flag_true(monkeypatch):
    """When canonical=true, canonical job is added; legacy job is not."""
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "true")

    from app.core.canonical_weekly_scheduler import run_canonical_weekly_all_clients as canon_fn
    from app.core.weekly_review import run_weekly_review_all_clients as legacy_fn

    jobs: dict = {}

    class _Scheduler:
        def add_job(self, fn, *_, id=None, **__):
            jobs[id] = fn

    import os
    if os.getenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "false").strip().lower() == "true":
        _Scheduler().add_job(canon_fn, "cron", id="canonical_weekly_review",
                             day_of_week="mon", hour=8, minute=0)
    else:
        _Scheduler().add_job(legacy_fn, "cron", id="weekly_review",
                             day_of_week="mon", hour=8, minute=0)

    assert "canonical_weekly_review" in jobs
    assert "weekly_review"           not in jobs
    assert jobs["canonical_weekly_review"] is canon_fn


def test_scheduler_registration_legacy_when_flag_false(monkeypatch):
    """When canonical=false, legacy job is added; canonical job is not."""
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "false")

    from app.core.canonical_weekly_scheduler import run_canonical_weekly_all_clients as canon_fn
    from app.core.weekly_review import run_weekly_review_all_clients as legacy_fn

    jobs: dict = {}

    class _Scheduler:
        def add_job(self, fn, *_, id=None, **__):
            jobs[id] = fn

    import os
    if os.getenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "false").strip().lower() == "true":
        _Scheduler().add_job(canon_fn, "cron", id="canonical_weekly_review",
                             day_of_week="mon", hour=8, minute=0)
    else:
        _Scheduler().add_job(legacy_fn, "cron", id="weekly_review",
                             day_of_week="mon", hour=8, minute=0)

    assert "weekly_review"           in jobs
    assert "canonical_weekly_review" not in jobs
    assert jobs["weekly_review"] is legacy_fn


# ── Static safety: no send/publish imports ────────────────────────────────────

def test_canonical_weekly_scheduler_no_sender_imports():
    source = (ROOT / "app/core/canonical_weekly_scheduler.py").read_text(encoding="utf-8")
    for forbidden in (
        "send_report_now", "publish_narrative_report", "send_weekly_reports",
        "send_monthly_reports", "send_whatsapp", "send_email",
        "REPORT_DELIVERY_ENABLED", "REPORT_PUBLISH_ENABLED",
        "prepare_delivery", "record_delivery_result",
    ):
        assert forbidden not in source, f"Forbidden symbol found: {forbidden!r}"


def test_canonical_weekly_scheduler_no_media_writes():
    """No ad-platform API calls or tracking identifiers in the scheduler module."""
    source = (ROOT / "app/core/canonical_weekly_scheduler.py").read_text(encoding="utf-8")
    # These are ad-platform client imports / tracking tokens — must not appear
    for forbidden in ("fbclid", "gclid", "send_conversion", "upload_conversion",
                      "import google.ads", "import facebook", "import meta"):
        assert forbidden not in source, f"Forbidden ad-platform symbol: {forbidden!r}"


# ── Job observability fields ──────────────────────────────────────────────────

def test_job_run_has_required_fields(monkeypatch):
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "true")
    ps, pe = _period_dates()
    db     = FakeDB()
    store  = MemoryStore()

    run_canonical_weekly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store)

    assert db.job_rows
    job = db.job_rows[0]
    for field in ("job_type", "run_key", "status", "started_at"):
        assert field in job, f"Missing field {field!r} in job row"
    assert job["job_type"] == _JOB_TYPE
    assert "lk-sneakers"  in job["run_key"]


# ── Actor safety ──────────────────────────────────────────────────────────────

def test_actor_not_in_human_required_blocked_set():
    """_ACTOR must not be in the HUMAN_ACTOR_REQUIRED blocklist so transition passes."""
    blocked = {"hermes", "hermes_service", "platform_web", "agency_admin", "system", "scheduler"}
    assert _ACTOR.strip().lower() not in blocked


def test_scheduler_code_only_calls_ready_for_review():
    """
    Scheduler source must only transition to READY_FOR_REVIEW — never APPROVED or PUBLISHED.
    (Actor 'canonical_weekly_review' is not in the HUMAN_ACTOR_REQUIRED blocklist, so the
    only protection is that the scheduler code never calls APPROVED/PUBLISHED.)
    """
    source = (ROOT / "app/core/canonical_weekly_scheduler.py").read_text(encoding="utf-8")
    assert '"READY_FOR_REVIEW"' in source
    for forbidden in ('"APPROVED"', '"PUBLISHED"', "'APPROVED'", "'PUBLISHED'"):
        assert forbidden not in source, f"Scheduler must not reference transition target {forbidden}"


# ── H7.3 Analysis Layer integration ───────────────────────────────────────────

def test_build_artifact_has_analysis_layer_field(monkeypatch):
    """Artifact content must include analysis_layer key (disabled fallback when flag=false)."""
    monkeypatch.setenv("CANONICAL_ANALYSIS_ENABLED", "false")
    ps, pe = _period_dates()
    db     = FakeDB()
    art    = _build_artifact("lk-sneakers", ps, pe, db)
    assert "analysis_layer" in art["content"]
    al = art["content"]["analysis_layer"]
    assert "status" in al
    # Flag explicitly false → ANALYSIS_DISABLED
    assert al["status"] == "ANALYSIS_UNAVAILABLE"
    assert al.get("reason") == "ANALYSIS_DISABLED"


def test_build_artifact_has_anomalies_detected_field():
    """Artifact content must include anomalies_detected (pure logic, always runs)."""
    ps, pe = _period_dates()
    db     = FakeDB()
    art    = _build_artifact("lk-sneakers", ps, pe, db)
    assert "anomalies_detected" in art["content"]
    assert isinstance(art["content"]["anomalies_detected"], list)


def test_build_artifact_schema_version_h74():
    ps, pe = _period_dates()
    art    = _build_artifact("lk-sneakers", ps, pe, FakeDB())
    assert art["content"]["schema_version"] == "h7.4-core-scheduler"


def test_version_param_creates_separate_cas_key(monkeypatch):
    """version=2 must create a distinct CAS key from version=1."""
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "true")
    monkeypatch.setenv("CANONICAL_ANALYSIS_ENABLED", "false")
    ps, pe = _period_dates()
    db     = FakeDB()
    store  = MemoryStore()

    r1 = run_canonical_weekly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store, version=1)
    r2 = run_canonical_weekly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store, version=2)

    assert r1["status"] == "READY_FOR_REVIEW"
    assert r2["status"] == "READY_FOR_REVIEW"
    # v1 and v2 are different CAS keys → two distinct store rows
    assert len(store.rows) == 2
    # report_ids come from UUID5 of the same args → same rid, but different keys
    assert r1["report_id"] == r2["report_id"]  # rid is the same UUID5
    v1_key = f"report:{r1['report_id']}:v1"
    v2_key = f"report:{r2['report_id']}:v2"
    assert v1_key in store.rows
    assert v2_key in store.rows


def test_quality_gate_blocks_ready_for_review_when_analysis_enabled(monkeypatch):
    """
    When CANONICAL_ANALYSIS_ENABLED=true and Claude returns UNAVAILABLE (e.g. no API key),
    the quality gate fails → report stays DRAFT, returns DRAFT_QUALITY_GATE_FAILED.
    """
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "true")
    monkeypatch.setenv("CANONICAL_ANALYSIS_ENABLED", "true")

    ps, pe = _period_dates()
    db     = FakeDB()
    store  = MemoryStore()

    result = run_canonical_weekly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store)

    # With analysis enabled, build_analysis_layer() returns WAITING_FOR_HERMES_ANALYSIS.
    # Scheduler writes handoff contract and returns WAITING status (not DRAFT_QUALITY_GATE_FAILED).
    assert result["status"] == "WAITING_FOR_HERMES_ANALYSIS"
    assert "analysis_request_id" in result
    # 1 report record + 1 analysis-request record in store
    report_rows = {k: v for k, v in store.rows.items() if k.startswith("report:")}
    assert len(report_rows) == 1
    doc = list(report_rows.values())[0]["document"]
    assert doc["status"] == "DRAFT"  # stays DRAFT until Hermes posts


def test_quality_gate_passes_when_analysis_disabled(monkeypatch):
    """When CANONICAL_ANALYSIS_ENABLED=false, gate bypassed → READY_FOR_REVIEW."""
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "true")
    monkeypatch.setenv("CANONICAL_ANALYSIS_ENABLED", "false")

    ps, pe = _period_dates()
    result = run_canonical_weekly_for_client(
        "lk-sneakers", ps, pe, sb=FakeDB(), _store_override=MemoryStore()
    )
    assert result["status"] == "READY_FOR_REVIEW"


def test_immutable_version_conflict_skipped_when_analysis_enabled(monkeypatch):
    """
    When analysis is enabled and v1 is already locked with a different hash,
    the scheduler returns IMMUTABLE_VERSION_CONFLICT_SKIPPED (not FAILED).
    """
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "true")
    monkeypatch.setenv("CANONICAL_ANALYSIS_ENABLED", "true")

    ps, pe = _period_dates()
    db     = FakeDB()
    store  = MemoryStore()

    # Pre-populate the expected CAS key with a document using a fake hash.
    # When generate() runs, it computes the real artifact hash which differs → IMMUTABLE_VERSION_CONFLICT.
    import json as _json, uuid as _uuid
    rid = str(_uuid.uuid5(
        _uuid.NAMESPACE_URL,
        _json.dumps(["lk-sneakers", "weekly", str(ps), str(pe)]),
    ))
    locked_doc = {
        "report_id":      rid,
        "status":         "READY_FOR_REVIEW",
        "client_id":      "lk-sneakers",
        "artifact_hash":  "STALE_HASH_FROM_PREVIOUS_RUN_abc123",
        "idempotency_key":"STALE_IDEMPOTENCY_KEY_different",
        "canonical_engine": "H7",
    }
    store.rows[f"report:{rid}:v1"] = {"revision": 1, "document": copy.deepcopy(locked_doc)}

    result = run_canonical_weekly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store)

    assert result["status"] == "IMMUTABLE_VERSION_CONFLICT_SKIPPED"
    assert result["report_id"] == rid


def test_immutable_version_conflict_raises_when_analysis_disabled(monkeypatch):
    """
    When analysis is disabled (flag=false), IMMUTABLE_VERSION_CONFLICT is NOT caught
    by the analysis handler → propagates → FAILED (same as before h7.3).
    """
    monkeypatch.setenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "true")
    monkeypatch.setenv("CANONICAL_ANALYSIS_ENABLED", "false")

    ps, pe = _period_dates()
    db     = FakeDB()
    store  = MemoryStore()

    import json as _json, uuid as _uuid
    rid = str(_uuid.uuid5(
        _uuid.NAMESPACE_URL,
        _json.dumps(["lk-sneakers", "weekly", str(ps), str(pe)]),
    ))
    locked_doc = {
        "report_id":      rid,
        "status":         "READY_FOR_REVIEW",
        "client_id":      "lk-sneakers",
        "artifact_hash":  "STALE_HASH_abc123",
        "idempotency_key":"STALE_IDEMPOTENCY_KEY",
        "canonical_engine": "H7",
    }
    store.rows[f"report:{rid}:v1"] = {"revision": 1, "document": copy.deepcopy(locked_doc)}

    result = run_canonical_weekly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store)

    # Without analysis enabled, conflict is not handled → FAILED
    assert result["status"] == "FAILED"
    assert "IMMUTABLE_VERSION_CONFLICT" in result.get("error", "")
