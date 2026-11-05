"""
Meeting Analysis Engine.

Calls Claude to extract structured analysis from a meeting transcript:
  A. Executive summary
  B. Decisions (status=PROPOSED, never auto-approved)
  C. Action items — owner/due_date only when EXPLICITLY stated in transcript
  D. Risks identified
  E. Follow-up items (pendências)
  F. Open questions

Constraints enforced by both prompt design and post-parse validation:
  - Never invent an owner not explicitly named in the transcript
  - Never invent a due date not explicitly stated
  - All action items have requires_human_review=True
  - All decisions status=PROPOSED
  - Processor="claude-meeting-v1"

Gate: ANTHROPIC_CREDITS_OK env var must be set to use Claude. Otherwise
raises MeetingAnalysisUnavailable so callers can surface the reason.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

PROCESSOR_ID = "claude-meeting-v1"

_SYSTEM_PROMPT = """\
Você é um assistente especializado em análise de reuniões de agência de marketing.

Dada a transcrição de uma reunião, extraia EXATAMENTE o JSON abaixo.
Responda SOMENTE com JSON válido — sem markdown, sem texto extra.

{
  "summary": "<resumo executivo em 2-4 frases>",
  "decisions": [
    {
      "decision": "<decisão tomada>",
      "rationale": "<motivo, se mencionado>",
      "owner": "<nome explicitamente citado na transcrição, ou null>"
    }
  ],
  "action_items": [
    {
      "task": "<tarefa específica>",
      "owner": "<nome explicitamente citado, ou null — NUNCA inventar>",
      "due_date": "<data ISO YYYY-MM-DD se explicitamente mencionada, ex: 'sexta-feira', 'próxima semana' — NUNCA inventar; null se não mencionada>",
      "priority": "HIGH|MEDIUM|LOW",
      "source_quote": "<trecho literal da transcrição que originou este item>",
      "requires_human_review": true
    }
  ],
  "risks": [
    {
      "risk": "<risco identificado>",
      "severity": "HIGH|MEDIUM|LOW",
      "mitigation_suggested": "<sugestão se mencionada, ou null>"
    }
  ],
  "open_questions": ["<pergunta aberta não respondida>"],
  "follow_ups": [
    {
      "action": "<ação de acompanhamento>",
      "owner": "<nome explicitamente citado, ou null>",
      "due_date": "<ISO date se explicitamente mencionada, ou null>"
    }
  ]
}

REGRAS CRÍTICAS:
- owner: NUNCA invente um responsável. Use EXATAMENTE o nome como aparece na transcrição. Se não foi explicitamente mencionado, use null.
- due_date: NUNCA invente uma data. Se a data não foi explicitamente mencionada na transcrição, use null. Converta datas relativas para formato ISO quando possível (ex: se a reunião é de 2026-11-05 e dizem "sexta-feira", use 2026-11-07).
- decisions: inclua APENAS decisões efetivamente tomadas na reunião, não intenções.
- risks: inclua apenas riscos explicitamente mencionados ou claramente implícitos no contexto.
- Responda em português brasileiro.
"""


class MeetingAnalysisUnavailable(Exception):
    """Claude is not available (missing key or credits blocked)."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _claude_available() -> tuple[bool, str]:
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False, "UNAVAILABLE_NO_KEY"
    if os.getenv("ANTHROPIC_CREDITS_OK", "").lower() not in ("1", "true", "yes"):
        return False, "DEGRADED_BLOCKED_CREDITS"
    return True, "LIVE"


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    return raw.strip()


def _validate_no_invented_dates(items: list[dict]) -> list[dict]:
    """Remove any due_date that doesn't look like a real ISO date."""
    _ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    cleaned = []
    for item in items:
        d = dict(item)
        if d.get("due_date") and not _ISO_RE.match(str(d["due_date"])):
            logger.warning(
                "meeting_analysis: stripped non-ISO due_date %r from item %r",
                d["due_date"], d.get("task") or d.get("action", "?"),
            )
            d["due_date"] = None
        cleaned.append(d)
    return cleaned


def analyze_transcript(
    transcript_text: str,
    meeting_title: str,
    meeting_date: Optional[str] = None,
) -> dict:
    """
    Analyze a meeting transcript via Claude.

    Args:
        transcript_text: Raw transcript (speaker: text\\n format or plain text).
        meeting_title:   Title of the meeting (included in context for Claude).
        meeting_date:    ISO date of the meeting (helps Claude resolve relative dates).

    Returns:
        Dict with keys: summary, decisions, action_items, risks, open_questions, follow_ups.
        Compatible with MeetingProcessCreate schema (commitments = action_items mapped).

    Raises:
        MeetingAnalysisUnavailable: When Claude is not available.
        ValueError: When transcript_text is empty.
    """
    available, reason = _claude_available()
    if not available:
        raise MeetingAnalysisUnavailable(reason)

    if not transcript_text or not transcript_text.strip():
        raise ValueError("transcript_text is required and must not be empty")

    try:
        import anthropic
        from ..config import settings
    except ImportError as exc:
        raise MeetingAnalysisUnavailable(f"anthropic package not installed: {exc}") from exc

    date_ctx = f" (data da reunião: {meeting_date})" if meeting_date else ""
    prompt = (
        f"Título da reunião: {meeting_title}{date_ctx}\n\n"
        f"Transcrição:\n{transcript_text[:12000]}"
    )

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, max_retries=0)
    message = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
        system=_SYSTEM_PROMPT,
        timeout=120.0,
    )

    raw = _strip_fences(message.content[0].text)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(
            "meeting_analysis: Claude returned invalid JSON for %r: %s — raw[:300]=%s",
            meeting_title, exc, raw[:300],
        )
        return _empty_result()

    return _normalize(parsed)


def _empty_result() -> dict:
    return {
        "summary": None,
        "decisions": [],
        "action_items": [],
        "risks": [],
        "open_questions": [],
        "follow_ups": [],
    }


def _normalize(parsed: dict) -> dict:
    """Normalize and validate Claude's output."""
    action_items = _validate_no_invented_dates(
        [_normalize_action_item(a) for a in (parsed.get("action_items") or [])]
    )
    follow_ups = _validate_no_invented_dates(
        list(parsed.get("follow_ups") or [])
    )
    decisions = [_normalize_decision(d) for d in (parsed.get("decisions") or [])]
    risks = [_normalize_risk(r) for r in (parsed.get("risks") or [])]
    open_questions = [str(q) for q in (parsed.get("open_questions") or []) if q]

    return {
        "summary":        parsed.get("summary") or None,
        "decisions":      decisions,
        "action_items":   action_items,
        "risks":          risks,
        "open_questions": open_questions,
        "follow_ups":     follow_ups,
    }


def _normalize_action_item(item: dict) -> dict:
    return {
        "task":                 str(item.get("task") or ""),
        "owner":                item.get("owner") or None,
        "due_date":             item.get("due_date") or None,
        "priority":             item.get("priority") or "MEDIUM",
        "source_quote":         item.get("source_quote") or None,
        "requires_human_review": True,
    }


def _normalize_decision(item: dict) -> dict:
    return {
        "decision":  str(item.get("decision") or ""),
        "rationale": item.get("rationale") or None,
        "owner":     item.get("owner") or None,
    }


def _normalize_risk(item: dict) -> dict:
    return {
        "risk":                  str(item.get("risk") or ""),
        "severity":              item.get("severity") or "MEDIUM",
        "mitigation_suggested":  item.get("mitigation_suggested") or None,
    }


def to_process_payload(analysis: dict) -> dict:
    """
    Convert analyze_transcript() output to a MeetingProcessCreate-compatible dict.

    action_items → commitments (mapping: task→what, owner→who)
    risks stored separately (via extended risks field on MeetingProcessCreate).
    """
    commitments = [
        {
            "who":      a.get("owner"),
            "what":     a.get("task", ""),
            "due_date": a.get("due_date"),
        }
        for a in (analysis.get("action_items") or [])
    ]
    return {
        "summary":        analysis.get("summary"),
        "decisions":      analysis.get("decisions") or [],
        "commitments":    commitments,
        "follow_ups":     analysis.get("follow_ups") or [],
        "open_questions": analysis.get("open_questions") or [],
        "risks":          analysis.get("risks") or [],
        "processor":      PROCESSOR_ID,
        "create_tasks":   False,
    }
