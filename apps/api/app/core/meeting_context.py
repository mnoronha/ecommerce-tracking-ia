"""
Meeting Context Provider for Hermes.

Loads recent meeting data for a client so it can be injected into
weekly_deep_review, ad-hoc Hermes queries, or any analysis engine
that needs operational context from past meetings.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from ..database import get_supabase

logger = logging.getLogger(__name__)


def _parse_jsonb(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return []
    return v or []


def load_meeting_context(client_id: str, limit: int = 5) -> dict:
    """
    Return a summary of recent meetings for `client_id`.

    Args:
        client_id: Client slug (e.g. 'lk-sneakers').
        limit:     Max number of recent meetings to load (default 5).

    Returns:
        {
          "recent_meetings": [{id, title, started_at, summary, decisions_count, action_items_count}],
          "recent_decisions": [{decision, meeting_id, meeting_title, meeting_date, owner}],
          "open_action_items": [{task, owner, due_date, priority, meeting_id, meeting_title}],
          "open_questions": [str],
          "total_meetings": int,
        }
    """
    db = get_supabase()

    try:
        meetings_res = (
            db.table("core_meetings")
            .select("id, title, started_at, ended_at, processing_status, transcript_status")
            .eq("client_id", client_id)
            .order("started_at", desc=True)
            .limit(limit)
            .execute()
        )
        meetings = meetings_res.data or []
    except Exception as exc:
        logger.error("meeting_context: failed to load meetings for %s: %s", client_id, exc)
        return _empty_context()

    if not meetings:
        return _empty_context()

    meeting_ids = [str(m["id"]) for m in meetings]

    try:
        summaries_res = (
            db.table("core_meeting_summaries")
            .select("meeting_id, summary, decisions, commitments, follow_ups, open_questions, risks, processor")
            .in_("meeting_id", meeting_ids)
            .execute()
        )
        summaries_by_meeting = {
            str(s["meeting_id"]): s for s in (summaries_res.data or [])
        }
    except Exception as exc:
        logger.warning("meeting_context: failed to load summaries for %s: %s", client_id, exc)
        summaries_by_meeting = {}

    recent_meetings = []
    all_decisions: list[dict] = []
    all_action_items: list[dict] = []
    all_open_questions: list[str] = []

    for m in meetings:
        mid = str(m["id"])
        s = summaries_by_meeting.get(mid)
        decisions = _parse_jsonb(s.get("decisions")) if s else []
        commitments = _parse_jsonb(s.get("commitments")) if s else []
        open_qs = _parse_jsonb(s.get("open_questions")) if s else []

        recent_meetings.append({
            "id":                mid,
            "title":             m.get("title"),
            "started_at":        m.get("started_at"),
            "transcript_status": m.get("transcript_status"),
            "processing_status": m.get("processing_status"),
            "summary":           s.get("summary") if s else None,
            "decisions_count":   len(decisions),
            "action_items_count": len(commitments),
        })

        for d in decisions:
            all_decisions.append({
                "decision":     d.get("decision"),
                "owner":        d.get("owner"),
                "meeting_id":   mid,
                "meeting_title": m.get("title"),
                "meeting_date": m.get("started_at"),
            })

        for c in commitments:
            # commitments come from both manual (MeetingCommitment.what/who)
            # and analysis (action_item.task/owner mapped to what/who)
            all_action_items.append({
                "task":          c.get("what") or c.get("task"),
                "owner":         c.get("who") or c.get("owner"),
                "due_date":      c.get("due_date"),
                "priority":      c.get("priority", "MEDIUM"),
                "meeting_id":    mid,
                "meeting_title": m.get("title"),
            })

        all_open_questions.extend([str(q) for q in open_qs if q])

    return {
        "recent_meetings":   recent_meetings,
        "recent_decisions":  all_decisions[:20],
        "open_action_items": all_action_items[:30],
        "open_questions":    list(dict.fromkeys(all_open_questions))[:20],
        "total_meetings":    len(meetings),
    }


def _empty_context() -> dict:
    return {
        "recent_meetings":   [],
        "recent_decisions":  [],
        "open_action_items": [],
        "open_questions":    [],
        "total_meetings":    0,
    }


def format_for_hermes(ctx: dict) -> str:
    """
    Render meeting context as a compact text block suitable for
    insertion into a Hermes analysis prompt.
    """
    if not ctx["total_meetings"]:
        return "Nenhuma reunião recente registrada."

    lines = [f"REUNIÕES RECENTES ({ctx['total_meetings']} encontradas):"]

    for m in ctx["recent_meetings"]:
        date_str = m["started_at"][:10] if m.get("started_at") else "sem data"
        status = m.get("processing_status", "pending")
        lines.append(f"  • {date_str} — {m['title']} [{status}]")
        if m.get("summary"):
            lines.append(f"    Resumo: {m['summary'][:200]}")

    if ctx["recent_decisions"]:
        lines.append(f"\nDECISÕES RECENTES ({len(ctx['recent_decisions'])}):")
        for d in ctx["recent_decisions"][:10]:
            owner_str = f" (owner: {d['owner']})" if d.get("owner") else ""
            lines.append(f"  • {d['decision']}{owner_str}")

    if ctx["open_action_items"]:
        lines.append(f"\nACTION ITEMS ABERTOS ({len(ctx['open_action_items'])}):")
        for a in ctx["open_action_items"][:10]:
            owner_str = f" → {a['owner']}" if a.get("owner") else ""
            due_str = f" (até {a['due_date']})" if a.get("due_date") else ""
            lines.append(f"  • {a['task']}{owner_str}{due_str}")

    if ctx["open_questions"]:
        lines.append(f"\nQUESTÕES ABERTAS ({len(ctx['open_questions'])}):")
        for q in ctx["open_questions"][:5]:
            lines.append(f"  ? {q}")

    return "\n".join(lines)
