from app.core import change_digest
from app.core.analysis_layer import build_hermes_handoff_from_artifact


def test_report_handoff_refreshes_google_context(monkeypatch):
    events = [
        {"id": "provider-1", "channel": "google", "entity_type": "CAMPAIGN",
         "change_type": "TARGET_ROAS", "source": "PLATFORM_DETECTED", "occurred_at": "2026-10-30T12:00:00Z"},
        {"id": "manual-1", "channel": "google", "entity_type": "CAMPAIGN",
         "change_type": "BUDGET", "source": "MANUAL", "occurred_at": "2026-10-30T13:00:00Z"},
    ]
    def fetch(db, client, platform, start, end, *, window):
        assert window["end_exclusive"] == "2026-11-02T00:00:00-03:00"
        return events if platform == "google" else []

    monkeypatch.setattr(change_digest, "_fetch_raw", fetch)
    monkeypatch.setattr(change_digest, "save_digest", lambda db, digest: None)
    monkeypatch.setattr(change_digest, "get_digest", lambda *args: {"total_raw_count": 0})
    summary = change_digest.build_report_change_summary(None, "lk-sneakers", "2026-10-26", "2026-11-01")
    handoff = build_hermes_handoff_from_artifact("request", "report", 1, {
        "client_id": "lk-sneakers", "period_start": "2026-10-26", "period_end": "2026-11-01",
        "content": {"change_summary": summary},
    })
    manifest = handoff["context_manifest"]
    assert manifest["platform_counts"]["google"] == {"raw": 2, "relevant": 2, "status": "READY"}
    assert manifest["change_context_status"] == "PARTIAL"
    examples = manifest["change_summary"]["google"]["event_examples"]
    assert {e["source"] for e in examples} == {"MANUAL", "PLATFORM_DETECTED"}
    assert {event_id for e in examples for event_id in e["event_ids"]} == {"provider-1", "manual-1"}


def test_empty_requires_confirmation_for_complete(monkeypatch):
    monkeypatch.setattr(change_digest, "_fetch_raw", lambda *args, **kwargs: [])
    monkeypatch.setattr(change_digest, "save_digest", lambda *args: None)
    args = (None, "lk-sneakers", "2026-10-26", "2026-11-01")
    uncertain = change_digest.build_report_change_summary(*args)
    confirmed = change_digest.build_report_change_summary(*args, complete_platforms=frozenset({"google"}))
    assert uncertain["google"]["change_context_status"] == "PARTIAL"
    assert confirmed["google"]["change_context_status"] == "COMPLETE"
    assert confirmed["google"]["total_changes_in_log"] == 0


def test_unavailable_never_becomes_zero(monkeypatch):
    def fail(*args, **kwargs):
        raise ConnectionError("fixture")
    monkeypatch.setattr(change_digest, "_fetch_raw", fail)
    summary = change_digest.build_report_change_summary(None, "lk-sneakers", "2026-10-26", "2026-11-01")
    assert summary["google"]["change_context_status"] == "UNKNOWN"
    assert summary["google"]["total_changes_in_log"] is None
    assert summary["google"]["relevant_changes"] is None
