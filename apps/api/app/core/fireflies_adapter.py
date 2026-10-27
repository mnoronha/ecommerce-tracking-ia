"""Fireflies.ai GraphQL adapter — read-only, no mutations, no side effects."""
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

_GQL_URL = "https://api.fireflies.ai/graphql"


def _api_key() -> str:
    key = os.getenv("FIREFLIES_API_KEY", "")
    if not key:
        raise RuntimeError("FIREFLIES_API_KEY not configured")
    return key


def _gql(query: str, variables: Optional[dict] = None) -> dict:
    """POST a GraphQL query to Fireflies. Raises RuntimeError on failure."""
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        _GQL_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()[:300]
        raise RuntimeError(f"Fireflies HTTP {exc.code}: {body}") from exc


def validate_auth() -> dict:
    """
    Read-only auth probe. Returns dict with auth/plan/transcript_read status.
    Never logs or returns the API key.
    """
    result = _gql("query { user { user_id name email num_transcripts } }")
    errors = result.get("errors")
    if errors:
        return {
            "authenticated": False,
            "error": errors[0].get("message", "unknown"),
            "plan_access": "UNAVAILABLE",
            "transcript_read": "UNAVAILABLE",
        }
    user = (result.get("data") or {}).get("user") or {}
    authenticated = bool(user.get("user_id"))
    return {
        "authenticated": authenticated,
        "user_id": user.get("user_id"),
        "name": user.get("name"),
        "num_transcripts": user.get("num_transcripts"),
        "plan_access": "AVAILABLE" if authenticated else "UNAVAILABLE",
        "transcript_read": "AVAILABLE" if authenticated else "UNAVAILABLE",
    }


_TRANSCRIPT_QUERY = """
query GetTranscript($id: String!) {
  transcript(id: $id) {
    id
    title
    date
    duration
    participants
    speakers { displayName }
    sentences {
      index
      speaker_name
      text
      start_time
      end_time
    }
  }
}
"""


def fetch_transcript(meeting_id: str) -> dict:
    """Fetch full transcript. Raises RuntimeError if not found."""
    result = _gql(_TRANSCRIPT_QUERY, {"id": meeting_id})
    errors = result.get("errors")
    if errors:
        raise RuntimeError(f"Fireflies GQL error: {errors[0].get('message', 'unknown')}")
    transcript = (result.get("data") or {}).get("transcript")
    if not transcript:
        raise RuntimeError(f"Transcript not found for meeting_id={meeting_id!r}")
    return transcript


def build_raw_text(transcript: dict) -> str:
    """Build flat speaker-labelled transcript text from sentences list."""
    sentences = transcript.get("sentences") or []
    lines = []
    for s in sentences:
        speaker = (s.get("speaker_name") or "").strip() or "Unknown"
        text = (s.get("text") or "").strip()
        if text:
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines) if lines else (transcript.get("title") or "")


def build_participants(transcript: dict) -> list[dict]:
    """
    Build normalized participant list.
    Fireflies returns participants as list[str] (emails) on Free plan.
    """
    raw = transcript.get("participants") or []
    if raw and isinstance(raw[0], str):
        return [{"email": e, "name": e.split("@")[0]} for e in raw if e]
    if raw and isinstance(raw[0], dict):
        return [{"email": p.get("email", ""), "name": p.get("name", "")} for p in raw]
    # Fall back to speakers
    speakers = transcript.get("speakers") or []
    return [{"name": s.get("displayName", ""), "email": ""} for s in speakers]
