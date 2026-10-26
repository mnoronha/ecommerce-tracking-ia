"""
Tests for canonical_reports SUPERSEDED lifecycle and v2 versioning.

Coverage:
  - SUPERSEDED reachable from READY_FOR_REVIEW, APPROVED, PUBLISHED
  - SUPERSEDED is a terminal state (no further transitions)
  - superseded_at, superseded_by_actor, superseded_by_version fields set
  - DRAFT cannot be directly superseded
  - v2 generates independently as a new CAS key (same report_id, different key)
  - v2 artifact with supersedes_version field preserved in document
  - v1 PARTIAL + v2 READY_FOR_REVIEW coexist without conflict
"""
from __future__ import annotations

import copy
import threading
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import uuid as _uuid

import pytest

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.canonical_reports import (
    CoreStore, Flags, Reports, ReportError, LIFECYCLE,
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


def _flags(monthly=True):
    return Flags(
        CANONICAL_MONTHLY_REVIEW_ENABLED=monthly,
        CANONICAL_WEEKLY_REVIEW_ENABLED=True,
        REPORT_PUBLISH_ENABLED=False,
        REPORT_DELIVERY_ENABLED=False,
    )


def _make_artifact(client_id="lk-sneakers", version=1, supersedes_version=None):
    period_start = "2026-09-01"
    period_end   = "2026-09-30"
    now = datetime.now(timezone.utc).isoformat()
    art = {
        "client_id":            client_id,
        "canonical_engine":     "H8",
        "engine_version":       "a" * 64,
        "version":              version,
        "period_start":         period_start,
        "period_end":           period_end,
        "generated_at":         now,
        "source_state":         [{"source_system": "meta_ads", "source_state": "HEALTHY"}],
        "certification_status": [{"window": "analysis", "metric_key": "revenue_business",
                                   "certification_status": "PROVISIONAL", "value_status": "OK"}],
        "review_status":        "READY",
        "content": {
            "client_id":        client_id,
            "analysis_period":  {"start": period_start, "end": period_end},
            "business_truth":   f"{client_id} Sep/2026. Revenue R$120000. PROVISIONAL.",
            "facts":            [{"metric_key": "revenue_business", "value": 120000.0,
                                  "value_status": "OK", "certification_status": "PROVISIONAL"}],
            "channels":         {},
            "hypotheses":       [],
            "recommendations":  [],
            "limitations":      ["PROVISIONAL"],
            "status":           "READY_FOR_REVIEW",
            "core_writes":      False,
            "platform_writes":  False,
        },
        "evidence_refs": [{"source": "core_metric_snapshots", "client_id": client_id,
                           "period_start": period_start, "period_end": period_end,
                           "metric_count": 1, "snapshot_id": "snap-001"}],
    }
    if supersedes_version is not None:
        art["supersedes_version"] = supersedes_version
    return art


def _generate_and_ready(store, client_id="lk-sneakers", version=1, supersedes_version=None):
    """Helper: generate artifact at version N, transition to READY_FOR_REVIEW."""
    reports = Reports(store, flags=_flags())
    art = _make_artifact(client_id=client_id, version=version, supersedes_version=supersedes_version)
    doc = reports.generate(art)
    at  = datetime.now(timezone.utc).isoformat()
    doc = reports.transition(
        doc["report_id"], version, "READY_FOR_REVIEW",
        actor="canonical_monthly_review", at=at,
        expected_hash=doc["artifact_hash"],
    )
    return doc


# -- LIFECYCLE dict ------------------------------------------------------------

def test_superseded_in_lifecycle():
    assert "SUPERSEDED" in LIFECYCLE["READY_FOR_REVIEW"]
    assert "SUPERSEDED" in LIFECYCLE["APPROVED"]
    assert "SUPERSEDED" in LIFECYCLE["PUBLISHED"]
    assert LIFECYCLE.get("SUPERSEDED") == set()  # terminal


def test_draft_cannot_be_superseded():
    """DRAFT is not in any transition set that leads to SUPERSEDED."""
    assert "SUPERSEDED" not in LIFECYCLE.get("DRAFT", set())


# -- SUPERSEDED from READY_FOR_REVIEW ------------------------------------------

def test_supersede_from_ready_for_review():
    store   = MemoryStore()
    reports = Reports(store, flags=_flags())
    doc     = _generate_and_ready(store)

    at = datetime.now(timezone.utc).isoformat()
    doc = reports.transition(
        doc["report_id"], 1, "SUPERSEDED",
        actor="canonical_monthly_backfill",
        at=at,
        expected_hash=doc["artifact_hash"],
        superseded_by_version=2,
    )
    assert doc["status"] == "SUPERSEDED"
    assert doc["superseded_by_actor"] == "canonical_monthly_backfill"
    assert doc["superseded_at"] == at
    assert doc["superseded_by_version"] == 2


def test_supersede_sets_superseded_by_version_none_when_omitted():
    store   = MemoryStore()
    doc     = _generate_and_ready(store)
    reports = Reports(store, flags=_flags())

    doc = reports.transition(
        doc["report_id"], 1, "SUPERSEDED",
        actor="canonical_monthly_backfill",
        at=datetime.now(timezone.utc).isoformat(),
        expected_hash=doc["artifact_hash"],
    )
    assert doc["status"] == "SUPERSEDED"
    assert "superseded_by_version" not in doc  # not set when omitted


def test_superseded_is_terminal():
    """No transitions are allowed from SUPERSEDED."""
    store   = MemoryStore()
    doc     = _generate_and_ready(store)
    reports = Reports(store, flags=_flags())

    doc = reports.transition(
        doc["report_id"], 1, "SUPERSEDED",
        actor="canonical_monthly_backfill",
        at=datetime.now(timezone.utc).isoformat(),
        expected_hash=doc["artifact_hash"],
    )

    for target in ("READY_FOR_REVIEW", "APPROVED", "PUBLISHED", "SUPERSEDED"):
        with pytest.raises(ReportError,
                           match="INVALID_TRANSITION|REPORT_NOT_FOUND|APPROVAL_HASH_MISMATCH|PUBLICATION_DISABLED"):
            reports.transition(
                doc["report_id"], 1, target,
                actor="canonical_monthly_backfill",
                at=datetime.now(timezone.utc).isoformat(),
                expected_hash=doc["artifact_hash"],
            )


def test_supersede_requires_correct_hash():
    store   = MemoryStore()
    doc     = _generate_and_ready(store)
    reports = Reports(store, flags=_flags())

    with pytest.raises(ReportError, match="APPROVAL_HASH_MISMATCH"):
        reports.transition(
            doc["report_id"], 1, "SUPERSEDED",
            actor="canonical_monthly_backfill",
            at=datetime.now(timezone.utc).isoformat(),
            expected_hash="0" * 64,
        )


def test_supersede_blocked_for_human_required_actors():
    store   = MemoryStore()
    doc     = _generate_and_ready(store)
    reports = Reports(store, flags=_flags())

    with pytest.raises(ReportError, match="HUMAN_ACTOR_REQUIRED"):
        reports.transition(
            doc["report_id"], 1, "SUPERSEDED",
            actor="system",
            at=datetime.now(timezone.utc).isoformat(),
            expected_hash=doc["artifact_hash"],
        )


# -- v2 generation ---------------------------------------------------------

def test_v2_generates_independent_cas_key():
    """v2 must be a separate CAS key from v1; they coexist without conflict."""
    store = MemoryStore()

    doc_v1 = _generate_and_ready(store, version=1)
    doc_v2 = _generate_and_ready(store, version=2, supersedes_version=1)

    # Two separate rows in the store
    assert len(store.rows) == 2
    keys = list(store.rows.keys())
    assert any(":v1" in k for k in keys)
    assert any(":v2" in k for k in keys)

    assert doc_v1["status"] == "READY_FOR_REVIEW"
    assert doc_v2["status"] == "READY_FOR_REVIEW"


def test_v2_same_report_id_different_version_key():
    """Both v1 and v2 share the same UUID5 report_id but have distinct CAS keys."""
    store = MemoryStore()
    doc_v1 = _generate_and_ready(store, version=1)
    doc_v2 = _generate_and_ready(store, version=2, supersedes_version=1)

    assert doc_v1["report_id"] == doc_v2["report_id"]


def test_v2_artifact_supersedes_field_preserved():
    """supersedes_version field in artifact survives into the stored document."""
    store   = MemoryStore()
    doc_v2  = _generate_and_ready(store, version=2, supersedes_version=1)

    assert doc_v2.get("supersedes_version") == 1


def test_v1_partial_coexists_with_v2_ready():
    """v1 PARTIAL and v2 READY_FOR_REVIEW can coexist; v1 is unchanged."""
    store   = MemoryStore()
    reports = Reports(store, flags=_flags())

    # v1: PARTIAL artifact (no facts, snapshot_not_found)
    art_v1 = _make_artifact(version=1)
    art_v1["review_status"] = "PARTIAL"
    art_v1["content"]["facts"] = []
    art_v1["content"]["status"] = "PARTIAL"
    art_v1["content"]["business_truth"] = "lk-sneakers: sem snapshot. PROVISIONAL."
    art_v1["content"]["limitations"] = ["snapshot_not_found: pipeline did not run"]

    doc_v1 = reports.generate(art_v1)
    at = datetime.now(timezone.utc).isoformat()
    doc_v1 = reports.transition(
        doc_v1["report_id"], 1, "READY_FOR_REVIEW",
        actor="canonical_monthly_rollout", at=at,
        expected_hash=doc_v1["artifact_hash"],
    )

    assert doc_v1["status"] == "READY_FOR_REVIEW"
    assert doc_v1["content"]["status"] == "PARTIAL"

    # v2: corrected artifact with real metrics
    doc_v2 = _generate_and_ready(store, version=2, supersedes_version=1)
    assert doc_v2["status"] == "READY_FOR_REVIEW"
    assert doc_v2["content"]["status"] == "READY_FOR_REVIEW"

    # v1 still PARTIAL, unmodified
    v1_key = f"report:{doc_v1['report_id']}:v1"
    v1_row = store.get(v1_key)
    assert v1_row["document"]["status"]           == "READY_FOR_REVIEW"
    assert v1_row["document"]["content"]["status"] == "PARTIAL"


def test_supersede_v1_after_v2_exists():
    """After v2 is READY_FOR_REVIEW, v1 can be transitioned to SUPERSEDED."""
    store   = MemoryStore()
    reports = Reports(store, flags=_flags())

    # Generate v1 and v2
    doc_v1 = _generate_and_ready(store, version=1)
    doc_v2 = _generate_and_ready(store, version=2, supersedes_version=1)

    # Supersede v1 pointing to v2
    at = datetime.now(timezone.utc).isoformat()
    doc_v1_superseded = reports.transition(
        doc_v1["report_id"], 1, "SUPERSEDED",
        actor="canonical_monthly_backfill",
        at=at,
        expected_hash=doc_v1["artifact_hash"],
        superseded_by_version=2,
    )

    assert doc_v1_superseded["status"]               == "SUPERSEDED"
    assert doc_v1_superseded["superseded_by_version"] == 2

    # v2 unaffected
    v2_key = f"report:{doc_v2['report_id']}:v2"
    v2_row = store.get(v2_key)
    assert v2_row["document"]["status"] == "READY_FOR_REVIEW"


def test_v1_preserved_with_original_content_after_supersession():
    """Superseding v1 must not alter its content — just adds supersession metadata."""
    store   = MemoryStore()
    reports = Reports(store, flags=_flags())
    doc_v1  = _generate_and_ready(store, version=1)
    v1_hash = doc_v1["artifact_hash"]

    reports.transition(
        doc_v1["report_id"], 1, "SUPERSEDED",
        actor="canonical_monthly_backfill",
        at=datetime.now(timezone.utc).isoformat(),
        expected_hash=v1_hash,
        superseded_by_version=2,
    )

    v1_key = f"report:{doc_v1['report_id']}:v1"
    v1_doc = store.get(v1_key)["document"]

    # Original fields intact
    assert v1_doc["artifact_hash"]          == v1_hash
    assert v1_doc["client_id"]              == "lk-sneakers"
    assert v1_doc["canonical_engine"]       == "H8"
    assert v1_doc["content"]["business_truth"] != ""
    # Supersession metadata appended
    assert v1_doc["status"]                 == "SUPERSEDED"
    assert v1_doc["superseded_by_version"]  == 2
