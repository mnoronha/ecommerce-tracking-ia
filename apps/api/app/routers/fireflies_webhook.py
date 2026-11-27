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
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
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


# ── Auto-analysis & Idempotent Action-Item Extraction (PRD §17) ───────────────

_DISABLED_FLAGS = frozenset({"0", "false", "off", "no"})


def _is_enabled(env_name: str, default: bool = True) -> bool:
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in _DISABLED_FLAGS


def _extract_fireflies_native_action_items(transcript: dict) -> tuple[Optional[str], list[dict]]:
    """
    Fallback deterministic extractor using Fireflies native `summary.overview` and
    `summary.action_items` when Claude analysis is unavailable.
    Never invents owners or due dates.
    """
    summary_obj = transcript.get("summary")
    if not isinstance(summary_obj, dict):
        return None, []
    overview = (summary_obj.get("overview") or "").strip() or None
    raw_items = summary_obj.get("action_items")
    commitments: list[dict] = []

    if isinstance(raw_items, str):
        for line in raw_items.splitlines():
            clean = re.sub(r"^\s*[-*•\d.)]+\s*", "", line).strip()
            if clean and not clean.endswith(":"):
                commitments.append({"who": None, "what": clean[:240], "due_date": None})
    elif isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, str) and item.strip():
                commitments.append({"who": None, "what": item.strip()[:240], "due_date": None})
            elif isinstance(item, dict):
                what = str(item.get("task") or item.get("what") or item.get("text") or "").strip()
                if what:
                    commitments.append({
                        "who": item.get("owner") or item.get("who") or None,
                        "what": what[:240],
                        "due_date": item.get("due_date") or None,
                    })
    return overview, commitments


def _materialize_meeting_tasks_idempotent(
    *,
    db,
    meeting_id: str,
    client_id: str,
    commitments: list[dict],
    now: str,
) -> list[dict]:
    """
    Idempotently create `core_tasks` from extracted meeting action items.
    Checks existing tasks for `(source_type='meeting', source_id=meeting_id)` by
    normalized title before inserting so retries never duplicate tasks.
    """
    if not commitments:
        return []

    existing_titles: dict[str, str] = {}
    try:
        ex_res = (
            db.table("core_tasks")
            .select("id, title")
            .eq("source_type", "meeting")
            .eq("source_id", meeting_id)
            .execute()
        )
        for row in (ex_res.data or []):
            t = (row.get("title") or "").strip().lower()
            if t:
                existing_titles[t] = str(row["id"])
    except Exception as exc:
        logger.warning("fireflies: existing task lookup failed for meeting %s: %s", meeting_id, exc)

    entries: list[dict] = []
    seen_in_batch: set[str] = set()

    for commitment in commitments:
        what = (commitment.get("what") or commitment.get("task") or "").strip()
        if not what:
            continue
        title = what[:200]
        norm = title.lower()
        if norm in seen_in_batch:
            continue
        seen_in_batch.add(norm)

        if norm in existing_titles:
            entries.append({
                "what": what,
                "title": title,
                "status": "existing",
                "task_id": existing_titles[norm],
            })
            continue

        task_row = {
            "client_id":        client_id,
            "title":            title,
            "owner":            commitment.get("who") or commitment.get("owner"),
            "due_date":         commitment.get("due_date"),
            "source_type":      "meeting",
            "source_id":        meeting_id,
            "status":           "OPEN",
            "priority":         str(commitment.get("priority") or "MEDIUM").upper(),
            "visibility_scope": "AGENCY_ONLY",
            "sync_state":       "none",
            "created_at":       now,
            "updated_at":       now,
        }
        entry: dict = {"what": what, "title": title}
        try:
            t_res = db.table("core_tasks").insert(task_row).execute()
            tid = str(t_res.data[0]["id"]) if (t_res and t_res.data) else None
            entry["status"] = "created"
            entry["task_id"] = tid
            if tid:
                existing_titles[norm] = tid
        except Exception as exc:
            err_msg = str(exc).lower()
            logger.warning("fireflies: task creation failed for %r: %s", title, exc)
            entry["status"] = "failed"
            entry["reason"] = (
                "UNIQUE_VIOLATION"
                if any(k in err_msg for k in ("unique", "duplicate", "23505"))
                else "DB_ERROR"
            )
        entries.append(entry)

    return entries


def _auto_analyze_meeting(
    meeting_id: str,
    transcript: dict,
    raw_text: str,
    db,
    now: str,
    client_id: Optional[str] = None,
) -> None:
    """
    Analyze meeting transcript and persist summary + action items (`commitments`)
    to `core_meeting_summaries` (and idempotently materialize tasks when enabled).

    Idempotent: no-op if summary already exists for `meeting_id`.
    Primary path: Claude (`meeting_analysis.analyze_transcript`).
    Fallback path when Claude is unavailable: Fireflies native `summary.action_items`.
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

    payload: Optional[dict] = None
    action_items_count = 0

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
        payload = meeting_analysis.to_process_payload(result)
        action_items_count = len(result.get("action_items") or [])
    except meeting_analysis.MeetingAnalysisUnavailable as exc:
        logger.info("fireflies: Claude unavailable for %s (%s) — checking native Fireflies summary", meeting_id, exc.reason)
        overview, native_commitments = _extract_fireflies_native_action_items(transcript)
        if overview or native_commitments:
            payload = {
                "summary":        overview,
                "decisions":      [],
                "commitments":    native_commitments,
                "follow_ups":     [],
                "open_questions": [],
                "risks":          [],
                "processor":      "fireflies_native",
            }
            action_items_count = len(native_commitments)
    except Exception as exc:
        logger.warning("fireflies: auto-analyze failed for %s: %s", meeting_id, exc)
        return

    if not payload:
        return

    commitments = payload.get("commitments") or []
    tasks_created: list[dict] = []
    if client_id and commitments and _is_enabled("FIREFLIES_AUTO_CREATE_TASKS", default=True):
        tasks_created = _materialize_meeting_tasks_idempotent(
            db=db,
            meeting_id=meeting_id,
            client_id=client_id,
            commitments=commitments,
            now=now,
        )

    import json as _json
    sum_row = {
        "meeting_id":     meeting_id,
        "summary":        payload.get("summary"),
        "decisions":      _json.dumps(payload.get("decisions") or []),
        "open_questions": _json.dumps(payload.get("open_questions") or []),
        "commitments":    _json.dumps(commitments),
        "follow_ups":     _json.dumps(payload.get("follow_ups") or []),
        "risks":          _json.dumps(payload.get("risks") or []),
        "tasks_created":  _json.dumps(tasks_created),
        "processor":      payload.get("processor", "claude"),
        "created_at":     now,
    }
    try:
        db.table("core_meeting_summaries").insert(sum_row).execute()
    except Exception as exc:
        err_str = str(exc).lower()
        # Migration 080 not applied: risks column missing — retry without it
        if "risks" in err_str and ("column" in err_str or "42703" in err_str):
            sum_row.pop("risks", None)
            db.table("core_meeting_summaries").insert(sum_row).execute()
        elif any(k in err_str for k in ("unique", "duplicate", "23505")):
            logger.info("fireflies: summary already inserted concurrently for %s", meeting_id)
            return
        else:
            raise

    db.table("core_meetings").update({
        "processing_status": "done",
        "updated_at": now,
    }).eq("id", meeting_id).execute()

    logger.info(
        "fireflies: auto-analyzed %s — processor=%s summary=%s decisions=%d action_items=%d tasks=%d",
        meeting_id,
        payload.get("processor"),
        "PRESENT" if payload.get("summary") else "NONE",
        len(payload.get("decisions") or []),
        action_items_count,
        len(tasks_created),
    )


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

        # Auto-analyze + action-item extraction (enabled by default; disable with FIREFLIES_AUTO_ANALYZE=0)
        if _is_enabled("FIREFLIES_AUTO_ANALYZE", default=True):
            _auto_analyze_meeting(meeting_id, transcript, raw_text, db, now, client_id=client_id)

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
    try:
        res = db.table("core_meetings").insert(row).execute()
        return str(res.data[0]["id"])
    except Exception as exc:
        if any(k in str(exc).lower() for k in ("unique", "duplicate", "23505")):
            existing = (
                db.table("core_meetings")
                .select("id")
                .eq("provider", "fireflies")
                .eq("external_id", fireflies_meeting_id)
                .limit(1)
                .execute()
            )
            if existing and existing.data:
                return str(existing.data[0]["id"])
        raise


# ── Endpoint ──────────────────────────────────────────────────────────────────

def _record_or_replay_fireflies_event(
    meeting_id: str,
    event_type: str,
    payload: dict,
    now: str,
) -> tuple[str, bool]:
    """
    Sync DB helper offloaded via asyncio.to_thread.
    Returns (event_id, is_idempotent_noop).
    Re-triggers processing if a previous attempt landed in `status='failed'`.
    """
    db = get_supabase()
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
        if existing.get("status") == "failed":
            db.table("core_fireflies_events").update({
                "status": "received",
                "error": None,
                "received_at": now,
            }).eq("id", existing["id"]).execute()
            logger.info("fireflies: retrying previously failed webhook %s/%s", meeting_id, event_type)
            return str(existing["id"]), False
        logger.info("fireflies: duplicate webhook %s/%s status=%s — noop", meeting_id, event_type, existing["status"])
        return str(existing["id"]), True

    event_res = db.table("core_fireflies_events").insert({
        "fireflies_meeting_id": meeting_id,
        "event_type":           event_type,
        "raw_payload":          json.dumps(payload),
        "status":               "received",
        "received_at":          now,
    }).execute()
    return str(event_res.data[0]["id"]), False


@router.post("/webhooks/fireflies")
async def fireflies_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive Fireflies webhook events.
    Returns 200 immediately; processing happens in background.
    Idempotent on (fireflies_meeting_id, event_type), allowing safe retry on failed events.
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)

    if not _verify_payload(payload):
        logger.warning("fireflies: webhook secret mismatch — rejected")
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

    meeting_id = payload.get("meetingId") or payload.get("meeting_id") or ""
    event_type = payload.get("webhook_type") or payload.get("event_type") or payload.get("event") or "unknown"

    if not meeting_id:
        return JSONResponse({"ok": False, "error": "missing_meetingId"}, status_code=400)

    now = datetime.now(timezone.utc).isoformat()
    event_id, is_noop = await asyncio.to_thread(
        _record_or_replay_fireflies_event,
        meeting_id,
        event_type,
        payload,
        now,
    )
    if is_noop:
        return JSONResponse({"ok": True, "idempotent": True, "event_id": event_id})

    # Process in background (fetch transcript, create meeting, extract action items)
    background_tasks.add_task(_process_event, event_id, meeting_id)

    return JSONResponse({"ok": True, "event_id": event_id, "idempotent": False})
