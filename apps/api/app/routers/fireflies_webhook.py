"""
Fireflies.ai webhook receiver.

POST /webhooks/fireflies — no auth token required (public endpoint).
Verification: compare payload["token"] against FIREFLIES_WEBHOOK_SECRET env var.
If secret not configured → accept all (lock down by IP at reverse proxy).

Background task:
  1. fetch transcript from Fireflies API
  2. resolve client_id (explicit mapping → UNRESOLVED)
  3. idempotent upsert core_meetings + core_meeting_transcripts
  4. update core_fireflies_events status
  5. leave processing_status=pending (human or auto-processes via /meetings/{id}/process)

Zero client sends. Zero media writes.
"""
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from ..database import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(tags=["webhooks"])


# ── Verification ──────────────────────────────────────────────────────────────

def _verify_payload(payload: dict) -> bool:
    expected = os.getenv("FIREFLIES_WEBHOOK_SECRET", "")
    if not expected:
        return True
    token = payload.get("token") or ""
    return hmac.compare_digest(str(token), expected)


# ── Client resolution ─────────────────────────────────────────────────────────

def _resolve_client(fireflies_meeting_id: str, db) -> tuple[Optional[str], str]:
    """
    Return (client_id, method). Priority:
      1. core_fireflies_client_mapping (explicit)
      2. UNRESOLVED (never guess)
    """
    res = (
        db.table("core_fireflies_client_mapping")
        .select("client_id")
        .eq("fireflies_meeting_id", fireflies_meeting_id)
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]["client_id"], "explicit"
    return None, "unresolved"


# ── Background processing ─────────────────────────────────────────────────────

def _process_event(event_id: str, fireflies_meeting_id: str) -> None:
    """Run after webhook response is sent. Fetches transcript and persists meeting data."""
    from ..core import fireflies_adapter as _ff

    db = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    try:
        # Fetch transcript from Fireflies API
        transcript = _ff.fetch_transcript(fireflies_meeting_id)
        participants = _ff.build_participants(transcript)
        raw_text = _ff.build_raw_text(transcript)

        # Resolve client
        client_id, method = _resolve_client(fireflies_meeting_id, db)
        if not client_id:
            db.table("core_fireflies_events").update({
                "status": "unresolved",
                "resolution_method": "unresolved",
                "processed_at": now,
            }).eq("id", event_id).execute()
            logger.warning("fireflies: unresolved client for meeting %s", fireflies_meeting_id)
            return

        # Idempotency: meeting
        existing = (
            db.table("core_meetings")
            .select("id")
            .eq("provider", "fireflies")
            .eq("external_id", fireflies_meeting_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            meeting_id = str(existing.data[0]["id"])
        else:
            meeting_id = _create_meeting(transcript, participants, client_id, fireflies_meeting_id, db, now)

        # Idempotency: transcript
        existing_t = (
            db.table("core_meeting_transcripts")
            .select("id")
            .eq("meeting_id", meeting_id)
            .limit(1)
            .execute()
        )
        if not existing_t.data and raw_text:
            db.table("core_meeting_transcripts").insert({
                "meeting_id": meeting_id,
                "raw_text":   raw_text,
                "language":   "pt",
                "word_count": len(raw_text.split()),
                "created_at": now,
            }).execute()
            db.table("core_meetings").update({
                "transcript_status": "ready",
                "updated_at": now,
            }).eq("id", meeting_id).execute()

        # Mark event processed
        db.table("core_fireflies_events").update({
            "status":            "processed",
            "client_id":         client_id,
            "resolution_method": method,
            "meeting_id":        meeting_id,
            "processed_at":      now,
        }).eq("id", event_id).execute()

        logger.info(
            "fireflies: processed meeting %s → client=%s meeting_id=%s",
            fireflies_meeting_id, client_id, meeting_id,
        )

    except Exception as exc:
        logger.error("fireflies: failed processing event %s: %s", event_id, exc)
        try:
            db.table("core_fireflies_events").update({
                "status":       "failed",
                "error":        str(exc)[:500],
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", event_id).execute()
        except Exception:
            pass


def _create_meeting(
    transcript: dict,
    participants: list[dict],
    client_id: str,
    fireflies_meeting_id: str,
    db,
    now: str,
) -> str:
    date_epoch = transcript.get("date")
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    if date_epoch:
        try:
            start_dt = datetime.fromtimestamp(int(date_epoch) / 1000, tz=timezone.utc)
            started_at = start_dt.isoformat()
            duration_s = transcript.get("duration")
            if duration_s:
                ended_at = (start_dt + timedelta(seconds=int(duration_s))).isoformat()
        except Exception:
            pass

    merged = participants

    row = {
        "client_id":         client_id,
        "provider":          "fireflies",
        "external_id":       fireflies_meeting_id,
        "title":             transcript.get("title") or f"Fireflies {fireflies_meeting_id}",
        "started_at":        started_at,
        "ended_at":          ended_at,
        "participants":      json.dumps(merged),
        "transcript_status": "pending",
        "processing_status": "pending",
        "created_at":        now,
        "updated_at":        now,
    }
    res = db.table("core_meetings").insert(row).execute()
    return str(res.data[0]["id"])


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/webhooks/fireflies")
async def fireflies_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive Fireflies webhook events.
    Returns 200 immediately; processing happens in background.
    Idempotent on (fireflies_meeting_id, event_type).
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)

    if not _verify_payload(payload):
        logger.warning("fireflies: webhook secret mismatch — rejected")
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

    meeting_id   = payload.get("meetingId") or payload.get("meeting_id") or ""
    event_type   = payload.get("webhook_type") or payload.get("event_type") or payload.get("event") or "unknown"

    if not meeting_id:
        return JSONResponse({"ok": False, "error": "missing_meetingId"}, status_code=400)

    db = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    # Idempotency: log event; skip if already received
    existing_event = (
        db.table("core_fireflies_events")
        .select("id, status")
        .eq("fireflies_meeting_id", meeting_id)
        .eq("event_type", event_type)
        .limit(1)
        .execute()
    )
    if existing_event.data:
        existing = existing_event.data[0]
        logger.info("fireflies: duplicate webhook %s/%s status=%s — noop", meeting_id, event_type, existing["status"])
        return JSONResponse({"ok": True, "idempotent": True, "event_id": existing["id"]})

    # Log event
    event_res = db.table("core_fireflies_events").insert({
        "fireflies_meeting_id": meeting_id,
        "event_type":           event_type,
        "raw_payload":          json.dumps(payload),
        "status":               "received",
        "received_at":          now,
    }).execute()
    event_id = str(event_res.data[0]["id"])

    # Process in background (fetch transcript, create meeting, etc.)
    background_tasks.add_task(_process_event, event_id, meeting_id)

    return JSONResponse({"ok": True, "event_id": event_id, "idempotent": False})
