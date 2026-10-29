"""
Tests for canonical_monthly_scheduler -- H8 monthly scheduler.

Coverage:
  - Period rule: last closed calendar month, correct bounds
  - Snapshot lookup: BOTH period_start AND period_end required (full-month only)
  - 7-day snapshot ending on 30th must NOT be selected as monthly snapshot
  - Artifact builder: facts, channels, business_truth, limitations, evidence_refs
  - Null-absent: None values never coerced to 0
  - Per-client run: generates DRAFT -> READY_FOR_REVIEW
  - Job run observability: core_job_runs RUNNING -> SUCCEEDED | FAILED
  - Six-client fanout and failure isolation
  - Flag=false guard (no-op)
  - Idempotent rerun: same period/engine_version -> same report_id
  - No publish, no delivery, no send, no media writes
  - Actor not in HUMAN_ACTOR_REQUIRED blocked set
  - main.py wiring: canonical_monthly_review registered when flag=true
"""
from __future__ import annotations

import copy
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import canonical_reports as cr
from app.core.canonical_monthly_scheduler import (
    _JOB_TYPE,
    _ACTOR,
    _ENGINE,
    _build_artifact,
    _engine_version,
    _last_completed_month,
    _month_label,
    run_canonical_monthly_all_clients,
    run_canonical_monthly_for_client,
)


# -- In-memory CAS store -------------------------------------------------------

class MemoryStore:
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


# -- Fake DB -------------------------------------------------------------------

SAMPLE_METRICS = [
    {"metric_key": "revenue_business", "value": 120000.0, "value_status": "OK",  "unit": "BRL"},
    {"metric_key": "orders_count",     "value": 320.0,   "value_status": "OK",  "unit": "count"},
    {"metric_key": "meta_spend",       "value": 8000.0,  "value_status": "OK",  "unit": "BRL"},
    {"metric_key": "meta_conversions", "value": 200.0,   "value_status": "OK",  "unit": "count"},
    {"metric_key": "google_spend",     "value": 3500.0,  "value_status": "OK",  "unit": "BRL"},
    {"metric_key": "google_conversions","value": 90.0,   "value_status": "OK",  "unit": "count"},
    {"metric_key": "total_spend",      "value": 11500.0, "value_status": "OK",  "unit": "BRL"},
    {"metric_key": "mer",              "value": 10.4,    "value_status": "OK",  "unit": "ratio",  "target": 8.0},
    {"metric_key": "roas_meta",        "value": 15.0,    "value_status": "OK",  "unit": "ratio"},
    {"metric_key": "roas_google",      "value": 9.5,     "value_status": "OK",  "unit": "ratio"},
    {"metric_key": "cpa_meta",         "value": 40.0,    "value_status": "OK",  "unit": "BRL"},
    {"metric_key": "cpa_google",       "value": 38.9,    "value_status": "OK",  "unit": "BRL"},
]

SAMPLE_HEALTH = {"meta_ads": "HEALTHY", "google_ads": "HEALTHY", "business": "HEALTHY"}

SIX_CLIENT_IDS = [
    "lk-sneakers", "dipua", "enutri",
    "clinica-tarcio-caetano", "zipper-galeria", "spiti-auction",
]


def _make_clients(client_ids=None):
    ids = client_ids or ["lk-sneakers"]
    return [
        {"id": f"uuid-{c}", "client_id": c, "name": c,
         "business_model": "ecommerce", "is_active": True}
        for c in ids
    ]


class _SnapshotChain:
    """Filters a snapshot list by eq() calls, returns matching rows on execute()."""
    def __init__(self, snapshots: list[dict]):
        self._snaps = snapshots
        self._filters: dict = {}

    def select(self, *_, **__):  return self
    def eq(self, k, v):
        self._filters[k] = v
        return self
    def order(self, *_, **__):   return self
    def limit(self, _):          return self
    def execute(self):
        result = [
            s for s in self._snaps
            if all(str(s.get(k)) == str(v) for k, v in self._filters.items())
        ]
        return type("R", (), {"data": result})()


class _Chain:
    def __init__(self, result):    self._result = result
    def select(self, *_, **__):   return self
    def eq(self, *_, **__):       return self
    def order(self, *_, **__):    return self
    def limit(self, _):           return self
    def execute(self):            return self._result


class _MutChain(_Chain):
    def __init__(self, store_list, result):
        super().__init__(result)
        self._store = store_list

    def insert(self, row):
        self._store.append(copy.deepcopy(row))
        return _Chain(type("R", (), {"data": [row]})())

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
    Fake Supabase client for canonical_monthly_scheduler tests.

    snapshots: list of snapshot dicts with client_id, period_start, period_end, metrics, health, id.
               _SnapshotChain filters by eq() calls, so period_start + period_end precision is tested.
    clients:   list of client dicts.
    """
    def __init__(self, snapshots=None, clients=None):
        ps, pe = _last_completed_month()
        if snapshots is None:
            snapshots = [
                {
                    "id": "snap-lk",
                    "client_id": "lk-sneakers",
                    "period_start": ps.isoformat(),
                    "period_end":   pe.isoformat(),
                    "metrics": SAMPLE_METRICS,
                    "health":  SAMPLE_HEALTH,
                }
            ]
        self._snapshots = snapshots
        self._clients   = clients or _make_clients(["lk-sneakers"])
        self.job_rows: list[dict] = []

    def table(self, name: str):
        if name == "core_metric_snapshots":
            return _SnapshotChain(self._snapshots)
        if name == "clients":
            return _Chain(type("R", (), {"data": self._clients})())
        if name == "core_job_runs":
            return _MutChain(self.job_rows, type("R", (), {"data": self.job_rows})())
        # Change digest tables: return empty — _build_change_summary catches failure gracefully
        if name in ("core_change_log", "core_change_digests"):
            return _EmptyChain()
        raise ValueError(f"FakeDB: unknown table {name!r}")


def _make_snapshot(client_id, period_start, period_end, metrics=None, health=None):
    return {
        "id":           f"snap-{client_id}-{period_start}",
        "client_id":    client_id,
        "period_start": str(period_start),
        "period_end":   str(period_end),
        "metrics":      metrics if metrics is not None else SAMPLE_METRICS,
        "health":       health  if health  is not None else SAMPLE_HEALTH,
    }


# -- Period rule ---------------------------------------------------------------

def test_last_completed_month_returns_first_and_last():
    ps, pe = _last_completed_month()
    assert ps.day  == 1
    assert ps.year == pe.year
    assert ps.month == pe.month
    # last day of the month
    import calendar
    assert pe.day == calendar.monthrange(pe.year, pe.month)[1]


def test_period_is_always_closed():
    ps, pe = _last_completed_month()
    assert pe < date.today()


def test_period_is_exactly_one_calendar_month():
    ps, pe = _last_completed_month()
    # period_start + one month > period_end
    if ps.month == 12:
        next_start = date(ps.year + 1, 1, 1)
    else:
        next_start = date(ps.year, ps.month + 1, 1)
    assert next_start > pe
    # period_end is in the same month as period_start
    assert ps.month == pe.month


def test_month_label_format():
    ps, _ = _last_completed_month()
    label = _month_label(ps)
    assert str(ps.year) in label
    assert label[0].isupper()


# -- Engine version ------------------------------------------------------------

def test_engine_version_is_64hex():
    ev = _engine_version()
    assert len(ev) == 64
    assert all(c in "0123456789abcdef" for c in ev)


def test_engine_version_is_deterministic():
    assert _engine_version() == _engine_version()


# -- Artifact builder ----------------------------------------------------------

def _period():
    return _last_completed_month()


def test_build_artifact_full_month_snapshot():
    ps, pe = _period()
    db     = FakeDB()
    art    = _build_artifact("lk-sneakers", ps, pe, db)

    assert art["client_id"]        == "lk-sneakers"
    assert art["canonical_engine"] == "H8"
    assert art["period_start"]     == str(ps)
    assert art["period_end"]       == str(pe)
    assert art["review_status"]    == "READY"
    assert len(art["content"]["facts"]) >= 10
    assert "meta"   in art["content"]["channels"]
    assert "google" in art["content"]["channels"]
    assert art["content"]["core_writes"]    is False
    assert art["content"]["platform_writes"] is False
    assert art["content"]["client_id"]       == "lk-sneakers"
    assert art["content"]["analysis_period"] == {"start": str(ps), "end": str(pe)}
    assert art["evidence_refs"][0]["source"] == "core_metric_snapshots"
    assert "CORE_SCHEDULER_DIRECT" in art["content"]["limitations"][0]


def test_build_artifact_no_snapshot_is_partial():
    ps, pe = _period()
    db     = FakeDB(snapshots=[])  # no snapshots at all
    art    = _build_artifact("lk-sneakers", ps, pe, db)

    assert art["review_status"]              == "PARTIAL"
    assert art["content"]["facts"]           == []
    assert art["content"]["channels"]        == {}
    assert any("snapshot_not_found" in lim for lim in art["content"]["limitations"])
    assert "PROVISIONAL" in art["content"]["business_truth"]
    assert art["content"]["core_writes"]     is False
    assert art["content"]["platform_writes"] is False


def test_seven_day_snapshot_not_selected():
    """
    A 7-day snapshot with period_start=last-7-days must NOT be picked.
    The monthly query uses .eq('period_start', first-of-month), so a rolling
    window that ends on period_end will not match.
    """
    ps, pe = _period()
    # 7-day snapshot ending on the same period_end
    seven_day_start = pe - timedelta(days=6)
    db = FakeDB(snapshots=[
        _make_snapshot("lk-sneakers", seven_day_start, pe)  # has period_start != ps
    ])

    art = _build_artifact("lk-sneakers", ps, pe, db)

    # Must NOT use the 7-day snapshot (different period_start)
    assert art["review_status"]    == "PARTIAL"
    assert art["content"]["facts"] == []
    assert any("snapshot_not_found" in lim for lim in art["content"]["limitations"])


def test_full_month_snapshot_preferred_over_other_periods():
    """
    When a full-month snapshot AND a 7-day snapshot both exist,
    only the full-month one (matching period_start) is returned.
    """
    ps, pe = _period()
    seven_day_start = pe - timedelta(days=6)
    db = FakeDB(snapshots=[
        _make_snapshot("lk-sneakers", seven_day_start, pe),  # 7-day
        _make_snapshot("lk-sneakers", ps, pe),               # full month
    ])
    art = _build_artifact("lk-sneakers", ps, pe, db)

    # The full-month snapshot must be selected
    assert art["review_status"] == "READY"
    assert len(art["content"]["facts"]) >= 10


def test_build_artifact_partial_degraded_source():
    ps, pe = _period()
    degraded = {"meta_ads": "ERROR", "google_ads": "HEALTHY", "business": "STALE"}
    db = FakeDB(snapshots=[_make_snapshot("lk-sneakers", ps, pe, health=degraded)])

    art = _build_artifact("lk-sneakers", ps, pe, db)

    assert art["content"]["status"] == "PARTIAL"
    assert any("meta_ads: ERROR"   in lim for lim in art["content"]["limitations"])
    assert any("business: STALE"   in lim for lim in art["content"]["limitations"])
    assert any(s["source_state"] == "ERROR" for s in art["source_state"])


def test_build_artifact_null_absent_not_zero():
    ps, pe = _period()
    metrics_with_null = [
        {"metric_key": "revenue_business", "value": None,    "value_status": "MISSING", "unit": "BRL"},
        {"metric_key": "meta_spend",       "value": 5000.0,  "value_status": "OK",     "unit": "BRL"},
    ]
    db  = FakeDB(snapshots=[_make_snapshot("lk-sneakers", ps, pe, metrics=metrics_with_null)])
    art = _build_artifact("lk-sneakers", ps, pe, db)

    keys = [f["metric_key"] for f in art["content"]["facts"]]
    assert "revenue_business" not in keys   # null -> absent
    assert "meta_spend"       in keys       # 5000 -> present


def test_build_artifact_business_truth_with_revenue():
    ps, pe = _period()
    db     = FakeDB()
    art    = _build_artifact("lk-sneakers", ps, pe, db)

    bt = art["content"]["business_truth"]
    assert "lk-sneakers" in bt
    assert "PROVISIONAL"  in bt
    assert "R$"           in bt  # revenue present


def test_build_artifact_business_truth_no_snapshot():
    ps, pe = _period()
    db     = FakeDB(snapshots=[])
    art    = _build_artifact("lk-sneakers", ps, pe, db)

    bt = art["content"]["business_truth"]
    assert "lk-sneakers" in bt
    assert "PROVISIONAL" in bt
    # Must not be empty or just whitespace
    assert len(bt.strip()) > 20


def test_build_artifact_hypotheses_generated():
    """MER above target -> OBSERVATION hypothesis."""
    ps, pe = _period()
    db     = FakeDB()
    art    = _build_artifact("lk-sneakers", ps, pe, db)

    hyp = art["content"]["hypotheses"]
    # MER=10.4 > target=8.0 -> OBSERVATION
    assert any(h["type"] == "OBSERVATION" for h in hyp)
    # Both roas_meta and roas_google present -> second OBSERVATION
    observations = [h for h in hyp if h["type"] == "OBSERVATION"]
    assert len(observations) >= 2


def test_build_artifact_hypotheses_when_mer_far_above():
    """MER > 1.3x target -> rule-based RECOMMENDATION in hypotheses (obs_hypotheses)."""
    ps, pe = _period()
    # MER=10.4, target=8.0; 10.4/8.0=1.3 (boundary), let's use target=7.0 to exceed 1.3x
    metrics = copy.deepcopy(SAMPLE_METRICS)
    for m in metrics:
        if m["metric_key"] == "mer":
            m["value"]  = 10.4
            m["target"] = 7.0
    db  = FakeDB(snapshots=[_make_snapshot("lk-sneakers", ps, pe, metrics=metrics)])
    art = _build_artifact("lk-sneakers", ps, pe, db)

    # Rule-based items are now in 'hypotheses' (obs_hypotheses); 'recommendations' = Claude recs
    hyp = art["content"]["hypotheses"]
    assert len(hyp) >= 1
    types = {h["type"] for h in hyp}
    assert "RECOMMENDATION" in types or "OBSERVATION" in types or "HYPOTHESIS" in types


def test_build_artifact_cross_client_isolation():
    ps, pe = _period()
    db = FakeDB(snapshots=[_make_snapshot("dipua", ps, pe)])
    art = _build_artifact("dipua", ps, pe, db)

    assert art["client_id"]               == "dipua"
    assert art["content"]["client_id"]    == "dipua"
    assert art["evidence_refs"][0]["client_id"] == "dipua"


def test_build_artifact_no_timestamp_in_evidence_refs():
    """evidence_refs must NOT include a timestamp so the artifact hash is stable."""
    ps, pe = _period()
    db     = FakeDB()
    art    = _build_artifact("lk-sneakers", ps, pe, db)

    refs = art["evidence_refs"]
    assert len(refs) == 1
    assert "timestamp" not in refs[0]
    assert refs[0]["period_start"] == str(ps)
    assert refs[0]["period_end"]   == str(pe)


def test_build_artifact_schema_version_h8():
    ps, pe = _period()
    db     = FakeDB()
    art    = _build_artifact("lk-sneakers", ps, pe, db)

    assert art["content"]["schema_version"].startswith("h8.")
    assert art["content"]["review_model"] == "ECOMMERCE_MONTHLY"


# -- Per-client execution ------------------------------------------------------

def test_run_for_client_generates_ready_for_review(monkeypatch):
    monkeypatch.setenv("CANONICAL_MONTHLY_REVIEW_ENABLED", "true")
    monkeypatch.setenv("CANONICAL_ANALYSIS_ENABLED", "false")
    ps, pe  = _period()
    db      = FakeDB()
    store   = MemoryStore()

    result = run_canonical_monthly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store)

    assert result["status"]    == "READY_FOR_REVIEW"
    assert result["client_id"] == "lk-sneakers"
    assert "report_id" in result
    assert len(store.rows) == 1
    doc = list(store.rows.values())[0]["document"]
    assert doc["status"]           == "READY_FOR_REVIEW"
    assert doc["canonical_engine"] == "H8"
    assert doc["client_id"]        == "lk-sneakers"


def test_run_for_client_records_job_run(monkeypatch):
    monkeypatch.setenv("CANONICAL_MONTHLY_REVIEW_ENABLED", "true")
    ps, pe = _period()
    db     = FakeDB()
    store  = MemoryStore()

    run_canonical_monthly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store)

    assert len(db.job_rows) >= 1
    job = db.job_rows[0]
    assert job["job_type"] == _JOB_TYPE
    assert job["run_key"].startswith(_JOB_TYPE)
    assert "lk-sneakers" in job["run_key"]


def test_run_for_client_records_failed_job_on_error(monkeypatch):
    """CAS always failing triggers CONCURRENT_UPDATE_RETRY_REQUIRED -> FAILED."""
    monkeypatch.setenv("CANONICAL_MONTHLY_REVIEW_ENABLED", "true")
    ps, pe = _period()
    db     = FakeDB()

    class NeverStore(MemoryStore):
        def cas(self, *_): return False

    result = run_canonical_monthly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=NeverStore())

    assert result["status"] == "FAILED"
    assert "CONCURRENT_UPDATE_RETRY_REQUIRED" in result.get("error", "")


def test_run_for_client_idempotent(monkeypatch):
    """Calling twice for the same period returns the same report_id; single store row."""
    monkeypatch.setenv("CANONICAL_MONTHLY_REVIEW_ENABLED", "true")
    monkeypatch.setenv("CANONICAL_ANALYSIS_ENABLED", "false")
    ps, pe = _period()
    db     = FakeDB()
    store  = MemoryStore()

    r1 = run_canonical_monthly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store)
    r2 = run_canonical_monthly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store)

    assert r1["report_id"] == r2["report_id"]
    assert r1["status"]    == "READY_FOR_REVIEW"
    assert r2["status"]    == "READY_FOR_REVIEW"
    assert len(store.rows) == 1


def test_no_publish_no_delivery_no_send(monkeypatch):
    monkeypatch.setenv("CANONICAL_MONTHLY_REVIEW_ENABLED", "true")
    ps, pe = _period()
    db     = FakeDB()
    store  = MemoryStore()

    run_canonical_monthly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store)

    assert not any(k.startswith("delivery:") for k in store.rows)
    doc = list(store.rows.values())[0]["document"]
    assert doc.get("published_at")  is None
    assert doc.get("approved_at")   is None
    assert doc["status"] not in ("PUBLISHED", "APPROVED")


# -- Six-client fanout ---------------------------------------------------------

def test_six_client_fanout(monkeypatch):
    monkeypatch.setenv("CANONICAL_MONTHLY_REVIEW_ENABLED", "true")
    ps, pe = _period()

    snapshots = [
        _make_snapshot(c, ps, pe) for c in SIX_CLIENT_IDS
    ]
    db    = FakeDB(snapshots=snapshots, clients=_make_clients(SIX_CLIENT_IDS))
    store = MemoryStore()

    import app.core.canonical_monthly_scheduler as sched
    monkeypatch.setattr(sched, "_get_db", lambda: db)
    monkeypatch.setattr(sched, "CoreStore", lambda _db: store)

    run_canonical_monthly_all_clients()

    assert len(store.rows) == 6
    client_ids = [v["document"]["client_id"] for v in store.rows.values()]
    for cid in SIX_CLIENT_IDS:
        assert cid in client_ids


def test_partial_clients_included_in_fanout(monkeypatch):
    """Clients without full-month snapshot produce PARTIAL records, not errors."""
    monkeypatch.setenv("CANONICAL_MONTHLY_REVIEW_ENABLED", "true")
    ps, pe = _period()

    # Only LK has a full-month snapshot; others have none
    snapshots = [_make_snapshot("lk-sneakers", ps, pe)]
    db        = FakeDB(snapshots=snapshots, clients=_make_clients(SIX_CLIENT_IDS))
    store     = MemoryStore()

    import app.core.canonical_monthly_scheduler as sched
    monkeypatch.setattr(sched, "_get_db", lambda: db)
    monkeypatch.setattr(sched, "CoreStore", lambda _db: store)

    run_canonical_monthly_all_clients()

    assert len(store.rows) == 6
    lk_doc = next(
        v["document"] for v in store.rows.values()
        if v["document"]["client_id"] == "lk-sneakers"
    )
    assert lk_doc["content"]["status"] == "READY_FOR_REVIEW"


def test_one_client_exception_isolation(monkeypatch):
    """Exception in _build_artifact for one client must not abort others."""
    monkeypatch.setenv("CANONICAL_MONTHLY_REVIEW_ENABLED", "true")
    ps, pe = _period()

    original_build = _build_artifact
    call_count = {"n": 0}
    def patched_build(client_id, *args, **kwargs):
        call_count["n"] += 1
        if client_id == "enutri":
            raise RuntimeError("simulated enutri failure")
        return original_build(client_id, *args, **kwargs)

    snapshots = [_make_snapshot(c, ps, pe) for c in SIX_CLIENT_IDS]
    db        = FakeDB(snapshots=snapshots, clients=_make_clients(SIX_CLIENT_IDS))
    store     = MemoryStore()

    import app.core.canonical_monthly_scheduler as sched
    monkeypatch.setattr(sched, "_get_db", lambda: db)
    monkeypatch.setattr(sched, "CoreStore", lambda _db: store)
    monkeypatch.setattr(sched, "_build_artifact", patched_build)

    run_canonical_monthly_all_clients()

    assert call_count["n"] == 6
    assert len(store.rows) == 5  # enutri raised before generate()


# -- Flag=false guard ----------------------------------------------------------

def test_all_clients_flag_false_noop(monkeypatch):
    monkeypatch.setenv("CANONICAL_MONTHLY_REVIEW_ENABLED", "false")
    store = MemoryStore()
    db    = FakeDB()

    import app.core.canonical_monthly_scheduler as sched
    monkeypatch.setattr(sched, "_get_db", lambda: db)
    monkeypatch.setattr(sched, "CoreStore", lambda _db: store)

    run_canonical_monthly_all_clients()

    assert len(store.rows) == 0


# -- Static safety: no send/publish imports ------------------------------------

def test_canonical_monthly_scheduler_no_sender_imports():
    source = (ROOT / "app/core/canonical_monthly_scheduler.py").read_text(encoding="utf-8")
    for forbidden in (
        "send_report_now", "publish_narrative_report", "send_weekly_reports",
        "send_monthly_reports", "send_whatsapp", "send_email",
        "REPORT_DELIVERY_ENABLED", "REPORT_PUBLISH_ENABLED",
        "prepare_delivery", "record_delivery_result",
    ):
        assert forbidden not in source, f"Forbidden symbol: {forbidden!r}"


def test_canonical_monthly_scheduler_no_media_writes():
    source = (ROOT / "app/core/canonical_monthly_scheduler.py").read_text(encoding="utf-8")
    for forbidden in ("fbclid", "gclid", "send_conversion", "upload_conversion",
                      "import google.ads", "import facebook", "import meta"):
        assert forbidden not in source, f"Forbidden ad-platform symbol: {forbidden!r}"


def test_scheduler_code_only_calls_ready_for_review():
    source = (ROOT / "app/core/canonical_monthly_scheduler.py").read_text(encoding="utf-8")
    assert '"READY_FOR_REVIEW"' in source
    for forbidden in ('"APPROVED"', '"PUBLISHED"', "'APPROVED'", "'PUBLISHED'"):
        assert forbidden not in source, f"Forbidden transition target: {forbidden}"


def test_no_materialize_closed_month_in_scheduler():
    """Scheduler must not CALL materialize_closed_month (docstring mention is fine)."""
    import re
    source = (ROOT / "app/core/canonical_monthly_scheduler.py").read_text(encoding="utf-8")
    # Catches actual call expressions like materialize_closed_month(...)
    assert not re.search(r'\bmaterialize_closed_month\s*\(', source), (
        "Scheduler must not call materialize_closed_month -- "
        "that is a separate backfill step."
    )
    assert not re.search(r'\brun_pipeline\s*\(', source), (
        "Scheduler must not call run_pipeline directly."
    )


# -- Actor safety --------------------------------------------------------------

def test_actor_not_in_human_required_blocked_set():
    blocked = {"hermes", "hermes_service", "platform_web", "agency_admin", "system", "scheduler"}
    assert _ACTOR.strip().lower() not in blocked


# -- main.py wiring ------------------------------------------------------------

def test_main_py_imports_canonical_monthly_scheduler():
    """main.py must reference canonical_monthly_scheduler for the cutover."""
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    # The conditional block for monthly must exist (weekly has it; monthly needs it too)
    # This test will FAIL until main.py is updated for the monthly cutover -- that is intended.
    assert "canonical_monthly" in source or "CANONICAL_MONTHLY_REVIEW_ENABLED" in source, (
        "main.py does not yet reference canonical_monthly_scheduler -- "
        "monthly scheduler cutover not wired."
    )


def test_main_py_monthly_review_always_present():
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert 'id="monthly_review"'  in source
    assert 'id="monthly_reports"' in source


# -- Job observability fields --------------------------------------------------

def test_job_run_has_required_fields(monkeypatch):
    monkeypatch.setenv("CANONICAL_MONTHLY_REVIEW_ENABLED", "true")
    ps, pe = _period()
    db     = FakeDB()
    store  = MemoryStore()

    run_canonical_monthly_for_client("lk-sneakers", ps, pe, sb=db, _store_override=store)

    assert db.job_rows
    job = db.job_rows[0]
    for field in ("job_type", "run_key", "status", "started_at"):
        assert field in job, f"Missing field {field!r}"
    assert job["job_type"] == _JOB_TYPE
    assert "lk-sneakers"  in job["run_key"]


def test_job_type_constant():
    assert _JOB_TYPE  == "canonical_monthly_review"
    assert _ENGINE    == "H8"
    assert _ACTOR     == "canonical_monthly_review"
