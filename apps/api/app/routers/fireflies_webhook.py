"""
Fireflies.ai webhook receiver.

POST /webhooks/fireflies — no auth token required (public endpoint).
Verification: compare payload["token"] against FIREFLIES_WEBHOOK_SECRET env var.
If secret not configured → accept all (lock down by IP at reverse proxy).

Background task:
  1. fetch transcript from Fireflies API
  2. resolve client_id — priority chain:
       P1. core_fireflies_client_mapping (explicit meeting-id override)
       P2. core_fireflies_mapping_rules match_type=participant_domain
       P3. core_fireflies_mapping_rules match_type=title_pattern
       fallback → UNRESOLVED (never guesses)
  3. idempotent upsert core_meetings + core_meeting_transcripts
  4. auto-analyze via Claude if FIREFLIES_AUTO_ANALYZE=1 (requires ANTHROPIC_CREDITS_OK=1)
  5. update core_fireflies_events status
  6. set processing_status=done (analyzed) or pending (transcript only)

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

def _resolve_client(
    fireflies_meeting_id: str,
    db,
    transcript: Optional[dict] = None,
) -> tuple[Optional[str], str]:
    """
    Return (client_id, method). Priority chain:
      P1. core_fireflies_client_mapping  — explicit meeting-id override (highest priority)
      P2. participant_domain rule         — non-noro domain in participants emails
      P3. title_pattern rule              — regex match on meeting title
      fallback → (None, "unresolved")    — never guesses
    """
    # P1: explicit per-meeting mapping
    res = (
        db.table("core_fireflies_client_mapping")
        .select("client_id")
        .eq("fireflies_meeting_id", fireflies_meeting_id)
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]["client_id"], "explicit"

    # P2 + P3: rule-based (requires transcript data)
    if transcript:
        try:
            rules = (
                db.table("core_fireflies_mapping_rules")
                .select("client_id, match_type, match_value")
                .eq("enabled", True)
                .order("priority")
                .execute()
                .data or []
            )
        except Exception:
            # table may not exist yet (migration 084 pending)
            rules = []

        if rules:
            from ..core import fireflies_adapter as _ff
            ps = _ff.build_participants(transcript)
            # Participant domains, excluding the Noro organizer domain
            participant_domains = {
                e.split("@")[1].lower()
                for p in ps
                for e in [p.get("email", "")]
                if "@" in e and not e.lower().endswith("@norolabs.com.br")
            }
            title = transcript.get("title") or ""

            for rule in rules:
                match_type  = rule.get("match_type", "")
                match_value = rule.get("match_value", "")
                client_id   = rule.get("client_id", "")
                if not (match_type and match_value and client_id):
                    continue
                if match_type == "participant_domain":
                    if match_value.lower() in participant_domains:
                        return client_id, "participant_domain"
                elif match_type == "title_pattern":
                    import re as _re
                    try:
                        if _re.search(match_value, title, _re.IGNORECASE):
                            return client_id, "title_pattern"
                    except _re.error:
                        continue

    return None, "unresolved"


# ── Auto-analysis ─────────────────────────────────────────────────────────────

def _auto_analyze_meeting(
    meeting_id: str,
    transcript: dict,
    raw_text: str,
    db,
    now: str,
) -> None:
    """
    Analyze meeting transcript via Claude and persist to core_meeting_summaries.
    Idempotent: no-op if summary already exists.
    Gated by FIREFLIES_AUTO_ANALYZE=1 (caller's responsibility) and
    ANTHROPIC_CREDITS_OK=1 (enforced inside meeting_analysis.analyze_transcript).
    """
    existing = (
        db.table("core_meeting_summaries")
        .select("id")
        .eq("meeting_id", meeting_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        logger.info("fireflies: auto-analyze skipped for %s (summary exists)", meeting_id)
        return

    from ..core import meeting_analysis
    try:
        meeting_date = None
        date_ms = transcript.get("date")
        if date_ms:
            try:
                meeting_date = datetime.fromtimestamp(int(date_ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            except Exception:
                pass

        result = meeting_analysis.analyze_transcript(
            transcript_text=raw_text,
            meeting_title=transcript.get("title") or "",
            meeting_date=meeting_date,
        )
        payload = meeting_analysis.to_process_payload(result)  # create_tasks=False

        import json as _json
        sum_row = {
            "meeting_id":     meeting_id,
            "summary":        payload.get("summary"),
            "decisions":      _json.dumps(payload.get("decisions") or []),
            "open_questions": _json.dumps(payload.get("open_questions") or []),
            "commitments":    _json.dumps(payload.get("commitments") or []),
            "follow_ups":     _json.dumps(payload.get("follow_ups") or []),
            "risks":          _json.dumps(payload.get("risks") or []),
            "tasks_created":  _json.dumps([]),
            "processor":      payload.get("processor", "claude"),
            "created_at":     now,
        }
        try:
            db.table("core_meeting_summaries").insert(sum_row).execute()
        except Exception as exc:
            # Migration 080 not applied: risks column missing — retry without it
            if "risks" in str(exc).lower() and ("column" in str(exc).lower() or "42703" in str(exc)):
                sum_row.pop("risks", None)
                db.table("core_meeting_summaries").insert(sum_row).execute()
            else:
                raise

        db.table("core_meetings").update({
            "processing_status": "done",
            "updated_at": now,
        }).eq("id", meeting_id).execute()

        logger.info(
            "fireflies: auto-analyzed %s — summary=%s decisions=%d action_items=%d",
            meeting_id,
            "PRESENT" if result.get("summary") else "NONE",
            len(result.get("decisions") or []),
            len(result.get("action_items") or []),
        )

    except meeting_analysis.MeetingAnalysisUnavailable as exc:
        logger.info("fireflies: auto-analyze unavailable for %s: %s", meeting_id, exc.reason)
    except Exception as exc:
        logger.warning("fireflies: auto-analyze failed for %s: %s", meeting_id, exc)


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

        # Resolve client via 3-tier priority chain
        client_id, method = _resolve_client(fireflies_meeting_id, db, transcript=transcript)
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
            meeting_created = False
        else:
            meeting_id = _create_meeting(transcript, participants, client_id, fireflies_meeting_id, db, now)
            meeting_created = True

        # Idempotency: transcript
        existing_t = (
            db.table("core_meeting_transcripts")
            .select("id")
            .eq("meeting_id", meeting_id)
            .limit(1)
            .execute()
        )
        transcript_created = False
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
            transcript_created = True

        # Auto-analyze via Claude (gated by env var + ANTHROPIC_CREDITS_OK)
        if os.getenv("FIREFLIES_AUTO_ANALYZE", "").lower() in ("1", "true"):
            _auto_analyze_meeting(meeting_id, transcript, raw_text, db, now)

        # Enrich event log with Fireflies metadata (organizer, meeting URL)
        event_update: dict = {
            "status":            "processed",
            "client_id":         client_id,
            "resolution_method": method,
            "meeting_id":        meeting_id,
            "processed_at":      now,
        }
        organizer_email = transcript.get("organizer_email")
        meeting_link    = transcript.get("meeting_link")
        if organizer_email:
            event_update["organizer_email"] = organizer_email
        if meeting_link:
            event_update["meeting_link"] = meeting_link

        db.table("core_fireflies_events").update(event_update).eq("id", event_id).execute()

        logger.info(
            "fireflies: processed meeting %s → client=%s method=%s meeting_id=%s"
            " new_meeting=%s new_transcript=%s",
            fireflies_meeting_id, client_id, method, meeting_id,
            meeting_created, transcript_created,
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
