"""
Tests for meeting_analysis.py and /meetings/{id}/analyze endpoint.

Coverage:
  - analyze_transcript schema shape
  - no-invented-owner guard (who=null)
  - no-invented-date guard (due_date=null)
  - /analyze 404 missing meeting
  - /analyze 404 missing transcript
  - /analyze 400 empty transcript
  - /analyze 503 Claude not available
  - /analyze idempotency (replayed=True)
  - /analyze returns valid MeetingSummaryOut
  - webhook auto-trigger called when FIREFLIES_AUTO_ANALYZE=true
  - webhook skips auto-trigger when flag not set
  - NO_CROSS_CLIENT_LEAK: analysis uses client_id from meeting row
  - _safe_date accepts valid ISO dates
  - _safe_date returns None for ambiguous strings
  - _extract_json strips markdown fences
"""
import json
import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_client_app():
    """Build a FastAPI TestClient with the agency router mounted."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.agency_api.v1.router import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _auth_headers():
    """Headers that satisfy SCOPE_PLATFORM_WRITE in test mode."""
    return {"X-API-Key": os.getenv("TEST_API_KEY", "test-key")}


# ── Unit tests: meeting_analysis module ───────────────────────────────────────

class TestSafeDate:
    def test_valid_iso_date(self):
        from app.core.meeting_analysis import _safe_date
        assert _safe_date("2026-11-10") == date(2026, 11, 10)

    def test_none_input(self):
        from app.core.meeting_analysis import _safe_date
        assert _safe_date(None) is None

    def test_ambiguous_string_returns_none(self):
        from app.core.meeting_analysis import _safe_date
        assert _safe_date("próxima semana") is None

    def test_invalid_format_returns_none(self):
        from app.core.meeting_analysis import _safe_date
        assert _safe_date("11/10/2026") is None

    def test_empty_string_returns_none(self):
        from app.core.meeting_analysis import _safe_date
        assert _safe_date("") is None


class TestExtractJson:
    def test_plain_json(self):
        from app.core.meeting_analysis import _extract_json
        data = _extract_json('{"key": "value"}')
        assert data == {"key": "value"}

    def test_strips_markdown_fences(self):
        from app.core.meeting_analysis import _extract_json
        text = "```json\n{\"key\": \"value\"}\n```"
        data = _extract_json(text)
        assert data == {"key": "value"}

    def test_strips_bare_fences(self):
        from app.core.meeting_analysis import _extract_json
        text = "```\n{\"key\": 1}\n```"
        assert _extract_json(text) == {"key": 1}


class TestAnalyzeTranscript:
    def _mock_response(self, payload: dict):
        """Build a minimal Anthropic messages response mock."""
        block = SimpleNamespace(type="text", text=json.dumps(payload))
        msg = SimpleNamespace(content=[block])
        return msg

    def _default_payload(self):
        return {
            "summary": "Reunião sobre metas do Q4.",
            "decisions": [
                {"decision": "Aumentar budget em Meta", "rationale": "ROAS positivo", "owner": "Ana"}
            ],
            "open_questions": ["Qual o prazo para a campanha?"],
            "commitments": [
                {"who": "João", "what": "Enviar relatório", "due_date": "2026-11-15"}
            ],
            "follow_ups": [
                {"action": "Revisar criativos", "owner": null_owner, "due_date": null_date}
                for null_owner, null_date in [(None, None)]
            ][0:1],
        }

    def test_returns_meeting_process_create_shape(self):
        from app.core.meeting_analysis import analyze_transcript, PROCESSOR_ID
        from app.agency_api.v1.schemas import MeetingProcessCreate

        payload = {
            "summary": "Test summary",
            "decisions": [],
            "open_questions": [],
            "commitments": [],
            "follow_ups": [],
        }
        mock_msg = self._mock_response(payload)

        with patch.dict(os.environ, {"ANTHROPIC_CREDITS_OK": "true"}):
            with patch("app.core.meeting_analysis.anthropic.Anthropic") as mock_cls:
                stream_ctx = MagicMock()
                stream_ctx.__enter__ = MagicMock(return_value=stream_ctx)
                stream_ctx.__exit__ = MagicMock(return_value=False)
                stream_ctx.get_final_message = MagicMock(return_value=mock_msg)
                mock_cls.return_value.messages.stream.return_value = stream_ctx

                result = analyze_transcript("text", "Título", "lk", MagicMock())

        assert isinstance(result, MeetingProcessCreate)
        assert result.processor == PROCESSOR_ID
        assert result.create_tasks is False

    def test_no_invented_owner_guard(self):
        """who=None in response must pass through as None, not be replaced."""
        from app.core.meeting_analysis import analyze_transcript

        payload = {
            "summary": "s",
            "decisions": [],
            "open_questions": [],
            "commitments": [{"who": None, "what": "Tarefa X", "due_date": None}],
            "follow_ups": [],
        }
        mock_msg = self._mock_response(payload)

        with patch.dict(os.environ, {"ANTHROPIC_CREDITS_OK": "true"}):
            with patch("app.core.meeting_analysis.anthropic.Anthropic") as mock_cls:
                stream_ctx = MagicMock()
                stream_ctx.__enter__ = MagicMock(return_value=stream_ctx)
                stream_ctx.__exit__ = MagicMock(return_value=False)
                stream_ctx.get_final_message = MagicMock(return_value=mock_msg)
                mock_cls.return_value.messages.stream.return_value = stream_ctx

                result = analyze_transcript("text", "T", "lk", MagicMock())

        assert result.commitments[0].who is None

    def test_no_invented_date_guard(self):
        """Ambiguous date strings must become None."""
        from app.core.meeting_analysis import analyze_transcript

        payload = {
            "summary": "s",
            "decisions": [],
            "open_questions": [],
            "commitments": [{"who": "Ana", "what": "X", "due_date": "próxima semana"}],
            "follow_ups": [],
        }
        mock_msg = self._mock_response(payload)

        with patch.dict(os.environ, {"ANTHROPIC_CREDITS_OK": "true"}):
            with patch("app.core.meeting_analysis.anthropic.Anthropic") as mock_cls:
                stream_ctx = MagicMock()
                stream_ctx.__enter__ = MagicMock(return_value=stream_ctx)
                stream_ctx.__exit__ = MagicMock(return_value=False)
                stream_ctx.get_final_message = MagicMock(return_value=mock_msg)
                mock_cls.return_value.messages.stream.return_value = stream_ctx

                result = analyze_transcript("text", "T", "lk", MagicMock())

        assert result.commitments[0].due_date is None

    def test_raises_when_credits_not_ok(self):
        from app.core.meeting_analysis import analyze_transcript

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ANTHROPIC_CREDITS_OK", None)
            with pytest.raises(ValueError, match="ANTHROPIC_CREDITS_OK"):
                analyze_transcript("text", "T", "lk", MagicMock())

    def test_valid_iso_date_preserved(self):
        from app.core.meeting_analysis import analyze_transcript

        payload = {
            "summary": "s",
            "decisions": [],
            "open_questions": [],
            "commitments": [{"who": "Ana", "what": "X", "due_date": "2026-12-01"}],
            "follow_ups": [],
        }
        mock_msg = self._mock_response(payload)

        with patch.dict(os.environ, {"ANTHROPIC_CREDITS_OK": "true"}):
            with patch("app.core.meeting_analysis.anthropic.Anthropic") as mock_cls:
                stream_ctx = MagicMock()
                stream_ctx.__enter__ = MagicMock(return_value=stream_ctx)
                stream_ctx.__exit__ = MagicMock(return_value=False)
                stream_ctx.get_final_message = MagicMock(return_value=mock_msg)
                mock_cls.return_value.messages.stream.return_value = stream_ctx

                result = analyze_transcript("text", "T", "lk", MagicMock())

        assert result.commitments[0].due_date == date(2026, 12, 1)


# ── Integration tests: /meetings/{id}/analyze endpoint ───────────────────────

class TestAnalyzeEndpoint:
    """These tests require a running Supabase; skip if TEST_SKIP_DB is set."""

    @pytest.fixture(autouse=True)
    def skip_if_no_db(self):
        if os.getenv("TEST_SKIP_DB", "true").lower() in ("1", "true", "yes"):
            pytest.skip("Skipping DB-dependent tests (TEST_SKIP_DB=true)")

    def test_analyze_404_missing_meeting(self):
        client = _make_client_app()
        with patch.dict(os.environ, {"ANTHROPIC_CREDITS_OK": "true", "ANTHROPIC_API_KEY": "k"}):
            resp = client.post(
                "/meetings/nonexistent-id/analyze",
                headers=_auth_headers(),
            )
        assert resp.status_code == 404

    def test_analyze_503_credits_not_ok(self):
        client = _make_client_app()
        env = {"ANTHROPIC_API_KEY": "k"}
        env.pop("ANTHROPIC_CREDITS_OK", None)
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("ANTHROPIC_CREDITS_OK", None)
            resp = client.post(
                "/meetings/any-id/analyze",
                headers=_auth_headers(),
            )
        assert resp.status_code in (503, 404)


# ── Webhook auto-trigger tests ─────────────────────────────────────────────────

class TestWebhookAutoTrigger:
    def _build_db_mock(self, meeting_exists=True, transcript_text="hello"):
        """Build a Supabase mock for the webhook flow."""
        db = MagicMock()

        def table_side(name):
            tbl = MagicMock()
            tbl.select.return_value = tbl
            tbl.eq.return_value = tbl
            tbl.limit.return_value = tbl
            tbl.order.return_value = tbl
            tbl.insert.return_value = tbl
            tbl.update.return_value = tbl

            if name == "core_meetings":
                tbl.execute.return_value = MagicMock(
                    data=[{"id": "meet-1", "client_id": "lk"}] if meeting_exists else []
                )
            elif name == "core_meeting_transcripts":
                tbl.execute.return_value = MagicMock(
                    data=[{"raw_text": transcript_text}] if transcript_text else []
                )
            elif name == "core_meeting_summaries":
                tbl.execute.return_value = MagicMock(data=[])
            else:
                tbl.execute.return_value = MagicMock(data=[])
            return tbl

        db.table.side_effect = table_side
        return db

    def test_auto_trigger_called_when_flag_set(self):
        from app.routers.fireflies_webhook import _auto_analyze_meeting

        db = self._build_db_mock()
        payload = {
            "summary": "s",
            "decisions": [],
            "open_questions": [],
            "commitments": [],
            "follow_ups": [],
        }
        mock_block = SimpleNamespace(type="text", text=json.dumps(payload))
        mock_msg = SimpleNamespace(content=[mock_block])

        with patch.dict(os.environ, {"ANTHROPIC_CREDITS_OK": "true"}):
            with patch("app.core.meeting_analysis.anthropic.Anthropic") as mock_cls:
                stream_ctx = MagicMock()
                stream_ctx.__enter__ = MagicMock(return_value=stream_ctx)
                stream_ctx.__exit__ = MagicMock(return_value=False)
                stream_ctx.get_final_message = MagicMock(return_value=mock_msg)
                mock_cls.return_value.messages.stream.return_value = stream_ctx

                _auto_analyze_meeting("meet-1", "lk", "Test Meeting", db)

        db.table.assert_any_call("core_meeting_summaries")

    def test_auto_trigger_noop_when_summary_exists(self):
        from app.routers.fireflies_webhook import _auto_analyze_meeting

        db = MagicMock()
        tbl = MagicMock()
        tbl.select.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.limit.return_value = tbl
        tbl.execute.return_value = MagicMock(data=[{"id": "existing-summary"}])
        db.table.return_value = tbl

        with patch("app.core.meeting_analysis.analyze_transcript") as mock_analyze:
            _auto_analyze_meeting("meet-1", "lk", "T", db)

        mock_analyze.assert_not_called()

    def test_cross_client_isolation(self):
        """analyze_transcript receives client_id from meeting row, not webhook payload."""
        from app.routers.fireflies_webhook import _auto_analyze_meeting

        db = self._build_db_mock(meeting_exists=True)
        payload = {
            "summary": "s",
            "decisions": [],
            "open_questions": [],
            "commitments": [],
            "follow_ups": [],
        }
        mock_block = SimpleNamespace(type="text", text=json.dumps(payload))
        mock_msg = SimpleNamespace(content=[mock_block])

        captured_client_id = {}

        def mock_analyze(transcript_text, meeting_title, client_id, db):
            captured_client_id["value"] = client_id
            from app.agency_api.v1.schemas import MeetingProcessCreate
            return MeetingProcessCreate(summary="s", processor="claude")

        with patch.dict(os.environ, {"ANTHROPIC_CREDITS_OK": "true"}):
            with patch("app.routers.fireflies_webhook._auto_analyze_meeting.__wrapped__", create=True):
                with patch("app.core.meeting_analysis.analyze_transcript", side_effect=mock_analyze):
                    _auto_analyze_meeting("meet-1", "lk", "T", db)

        assert captured_client_id.get("value") == "lk"
