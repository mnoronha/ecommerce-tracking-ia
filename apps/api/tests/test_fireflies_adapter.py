"""
Tests for app/core/fireflies_adapter.py

Coverage:
  - build_raw_text with sentences list
  - build_raw_text with empty sentences → fallback to title
  - build_participants with email strings (Free plan format)
  - build_participants with dict list
  - validate_auth success → authenticated=True, plan_access=AVAILABLE
  - validate_auth GQL error → authenticated=False, plan_access=UNAVAILABLE
  - validate_auth HTTP error → raises RuntimeError
  - fetch_transcript success → returns transcript dict
  - fetch_transcript GQL error → raises RuntimeError
  - fetch_transcript not found (null data) → raises RuntimeError
  - _api_key() raises when FIREFLIES_API_KEY not set
"""
from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.fireflies_adapter import (
    _api_key,
    _gql,
    build_participants,
    build_raw_text,
    fetch_transcript,
    validate_auth,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_response(data: dict) -> MagicMock:
    """Fake urlopen context-manager response."""
    body = json.dumps(data).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _make_http_error(code: int, body: str = "error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.fireflies.ai/graphql",
        code=code,
        msg="Error",
        hdrs={},  # type: ignore[arg-type]
        fp=io.BytesIO(body.encode()),
    )


# ── build_raw_text ─────────────────────────────────────────────────────────────

def test_build_raw_text_with_sentences():
    transcript = {
        "sentences": [
            {"speaker_name": "Alice", "text": "Hello world"},
            {"speaker_name": "Bob", "text": "Hi there"},
        ]
    }
    result = build_raw_text(transcript)
    assert result == "Alice: Hello world\nBob: Hi there"


def test_build_raw_text_empty_sentences_falls_back_to_title():
    transcript = {"sentences": [], "title": "My Meeting"}
    result = build_raw_text(transcript)
    assert result == "My Meeting"


def test_build_raw_text_none_sentences_falls_back_to_title():
    transcript = {"title": "Fallback Title"}
    result = build_raw_text(transcript)
    assert result == "Fallback Title"


def test_build_raw_text_unknown_speaker_when_name_missing():
    transcript = {
        "sentences": [
            {"speaker_name": "", "text": "Something said"},
        ]
    }
    result = build_raw_text(transcript)
    assert result == "Unknown: Something said"


def test_build_raw_text_skips_empty_text():
    transcript = {
        "sentences": [
            {"speaker_name": "Alice", "text": ""},
            {"speaker_name": "Bob", "text": "Real text"},
        ]
    }
    result = build_raw_text(transcript)
    assert result == "Bob: Real text"


# ── build_participants ─────────────────────────────────────────────────────────

def test_build_participants_email_strings_free_plan():
    transcript = {"participants": ["alice@example.com", "bob@example.com"]}
    result = build_participants(transcript)
    assert result == [
        {"email": "alice@example.com", "name": "alice"},
        {"email": "bob@example.com", "name": "bob"},
    ]


def test_build_participants_dict_list():
    transcript = {
        "participants": [
            {"email": "alice@example.com", "name": "Alice"},
            {"email": "bob@example.com", "name": "Bob"},
        ]
    }
    result = build_participants(transcript)
    assert result == [
        {"email": "alice@example.com", "name": "Alice"},
        {"email": "bob@example.com", "name": "Bob"},
    ]


def test_build_participants_empty_returns_empty():
    transcript = {"participants": []}
    result = build_participants(transcript)
    assert result == []


# ── _api_key ──────────────────────────────────────────────────────────────────

def test_api_key_raises_when_not_set():
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("FIREFLIES_API_KEY", None)
        with pytest.raises(RuntimeError, match="FIREFLIES_API_KEY not configured"):
            _api_key()


def test_api_key_returns_value_when_set():
    with patch.dict(os.environ, {"FIREFLIES_API_KEY": "test-key-123"}):
        assert _api_key() == "test-key-123"


# ── validate_auth ─────────────────────────────────────────────────────────────

def test_validate_auth_success():
    response_data = {
        "data": {
            "user": {
                "user_id": "user-abc",
                "name": "Alice",
                "email": "alice@example.com",
                "num_transcripts": 42,
            }
        }
    }
    with patch.dict(os.environ, {"FIREFLIES_API_KEY": "test-key"}):
        with patch("urllib.request.urlopen", return_value=_make_response(response_data)):
            result = validate_auth()

    assert result["authenticated"] is True
    assert result["plan_access"] == "AVAILABLE"
    assert result["transcript_read"] == "AVAILABLE"
    assert result["user_id"] == "user-abc"
    assert result["num_transcripts"] == 42


def test_validate_auth_gql_error():
    response_data = {
        "errors": [{"message": "Unauthorized"}]
    }
    with patch.dict(os.environ, {"FIREFLIES_API_KEY": "test-key"}):
        with patch("urllib.request.urlopen", return_value=_make_response(response_data)):
            result = validate_auth()

    assert result["authenticated"] is False
    assert result["plan_access"] == "UNAVAILABLE"
    assert result["transcript_read"] == "UNAVAILABLE"
    assert result["error"] == "Unauthorized"


def test_validate_auth_http_error_raises():
    with patch.dict(os.environ, {"FIREFLIES_API_KEY": "test-key"}):
        with patch("urllib.request.urlopen", side_effect=_make_http_error(401, "Unauthorized")):
            with pytest.raises(RuntimeError, match="Fireflies HTTP 401"):
                validate_auth()


def test_validate_auth_does_not_expose_api_key(caplog):
    """API key must never appear in logs."""
    import logging
    response_data = {"data": {"user": {"user_id": "u1", "name": "X", "email": "x@y.com", "num_transcripts": 0}}}
    with patch.dict(os.environ, {"FIREFLIES_API_KEY": "super-secret-key"}):
        with patch("urllib.request.urlopen", return_value=_make_response(response_data)):
            with caplog.at_level(logging.DEBUG):
                validate_auth()

    for record in caplog.records:
        assert "super-secret-key" not in record.getMessage()


# ── fetch_transcript ──────────────────────────────────────────────────────────

_SAMPLE_TRANSCRIPT = {
    "id": "meeting-123",
    "title": "Q3 Review",
    "date": 1700000000000,
    "duration": 3600,
    "participants": ["alice@example.com"],
    "sentences": [
        {"index": 0, "speaker_name": "Alice", "text": "Welcome everyone", "start_time": 0, "end_time": 5}
    ],
}


def test_fetch_transcript_success():
    response_data = {"data": {"transcript": _SAMPLE_TRANSCRIPT}}
    with patch.dict(os.environ, {"FIREFLIES_API_KEY": "test-key"}):
        with patch("urllib.request.urlopen", return_value=_make_response(response_data)):
            result = fetch_transcript("meeting-123")

    assert result["id"] == "meeting-123"
    assert result["title"] == "Q3 Review"


def test_fetch_transcript_gql_error_raises():
    response_data = {"errors": [{"message": "Meeting not accessible"}]}
    with patch.dict(os.environ, {"FIREFLIES_API_KEY": "test-key"}):
        with patch("urllib.request.urlopen", return_value=_make_response(response_data)):
            with pytest.raises(RuntimeError, match="Fireflies GQL error"):
                fetch_transcript("bad-id")


def test_fetch_transcript_not_found_raises():
    response_data = {"data": {"transcript": None}}
    with patch.dict(os.environ, {"FIREFLIES_API_KEY": "test-key"}):
        with patch("urllib.request.urlopen", return_value=_make_response(response_data)):
            with pytest.raises(RuntimeError, match="Transcript not found"):
                fetch_transcript("nonexistent-id")


def test_fetch_transcript_http_error_raises():
    with patch.dict(os.environ, {"FIREFLIES_API_KEY": "test-key"}):
        with patch("urllib.request.urlopen", side_effect=_make_http_error(403, "Forbidden")):
            with pytest.raises(RuntimeError, match="Fireflies HTTP 403"):
                fetch_transcript("meeting-123")
