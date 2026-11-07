"""
Claude-powered meeting transcript analysis engine.

Extracts: summary, decisions, action items → commitments (what/who/due_date),
open_questions, follow_ups.

Guards:
  - No invented owners: who/owner=None if person not explicitly in transcript
  - No invented dates: due_date=None if ambiguous or non-ISO
  - requires_human_review implied (all Claude output must be reviewed)

Gate: ANTHROPIC_CREDITS_OK env var must be set before calling analyze_transcript().
"""

import json
import logging
import os
import re
from datetime import date
from typing import Optional

import anthropic

from ..agency_api.v1.schemas import (
    MeetingCommitment,
    MeetingDecision,
    MeetingFollowUp,
    MeetingProcessCreate,
)

logger = logging.getLogger(__name__)

PROCESSOR_ID = "claude"
_MODEL = "claude-opus-4-8"

_SYSTEM_PROMPT = (
    "Você é um assistente especializado em resumir transcrições de reuniões de marketing digital. "
    "Responda SEMPRE com JSON válido no formato especificado. "
    "Não invente pessoas nem datas que não estejam explicitamente na transcrição. "
    "Responda apenas com o objeto JSON, sem texto adicional, sem markdown, sem blocos de código."
)

_USER_PROMPT_TEMPLATE = """\
Analise a transcrição de reunião abaixo e extraia as informações em JSON.

Título: {meeting_title}

Transcrição:
{transcript_text}

Responda com exatamente este formato JSON (sem markdown):
{{
  "summary": "resumo conciso da reunião em 2-4 parágrafos",
  "decisions": [
    {{"decision": "decisão tomada", "rationale": "motivo opcional ou null", "owner": "nome exato da pessoa ou null"}}
  ],
  "open_questions": ["questão em aberto"],
  "commitments": [
    {{"who": "nome exato da pessoa que se comprometeu ou null", "what": "o que ficou acordado", "due_date": "YYYY-MM-DD ou null"}}
  ],
  "follow_ups": [
    {{"action": "próximo passo", "owner": "nome exato ou null", "due_date": "YYYY-MM-DD ou null"}}
  ]
}}

Regras obrigatórias:
- Para "who" e "owner": use APENAS nomes mencionados EXPLICITAMENTE na transcrição. Se indefinido, use null.
- Para "due_date": use APENAS formato YYYY-MM-DD. Se a data for ambígua ou não mencionada, use null.
- Listas vazias [] são válidas se não houver itens.
"""


def _credits_ok() -> bool:
    return os.getenv("ANTHROPIC_CREDITS_OK", "").lower() in ("1", "true", "yes")


def _safe_date(value: Optional[object]) -> Optional[date]:
    """Parse ISO date string; return None if ambiguous or invalid."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _extract_json(text: str) -> dict:
    """Extract JSON object from Claude response, stripping any markdown fences."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
    return json.loads(text.strip())


def analyze_transcript(
    transcript_text: str,
    meeting_title: str,
    client_id: str,
    db,
) -> MeetingProcessCreate:
    """
    Analyze a meeting transcript using Claude.

    Args:
        transcript_text: Raw text from the transcript.
        meeting_title:   Title of the meeting for context.
        client_id:       Client slug (reserved for future context lookups).
        db:              Supabase client (reserved for future context lookups).

    Returns:
        MeetingProcessCreate with processor="claude" and create_tasks=False.
        Caller is responsible for setting create_tasks before persisting.

    Raises:
        ValueError:   ANTHROPIC_CREDITS_OK is not set.
        RuntimeError: Claude API call failed or response could not be parsed.
    """
    if not _credits_ok():
        raise ValueError(
            "ANTHROPIC_CREDITS_OK not set — Claude analysis unavailable"
        )

    client = anthropic.Anthropic()

    prompt = _USER_PROMPT_TEMPLATE.format(
        meeting_title=meeting_title or "Reunião",
        transcript_text=transcript_text,
    )

    try:
        with client.messages.stream(
            model=_MODEL,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()
    except Exception as exc:
        logger.error("meeting_analysis: Claude API call failed: %s", exc)
        raise RuntimeError(f"Claude API error: {exc}") from exc

    raw_text = ""
    for block in message.content:
        if block.type == "text":
            raw_text = block.text
            break

    if not raw_text:
        raise RuntimeError("Claude returned empty response")

    try:
        data = _extract_json(raw_text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error(
            "meeting_analysis: JSON parse failed: %s\nRaw (first 500): %.500s",
            exc,
            raw_text,
        )
        raise RuntimeError(f"Claude response parse error: {exc}") from exc

    decisions = []
    for d in (data.get("decisions") or []):
        if not isinstance(d, dict):
            continue
        decisions.append(MeetingDecision(
            decision=str(d.get("decision") or ""),
            rationale=d.get("rationale") or None,
            owner=d.get("owner") or None,
        ))

    commitments = []
    for c in (data.get("commitments") or []):
        if not isinstance(c, dict):
            continue
        commitments.append(MeetingCommitment(
            who=c.get("who") or None,
            what=str(c.get("what") or ""),
            due_date=_safe_date(c.get("due_date")),
        ))

    follow_ups = []
    for f in (data.get("follow_ups") or []):
        if not isinstance(f, dict):
            continue
        follow_ups.append(MeetingFollowUp(
            action=str(f.get("action") or ""),
            owner=f.get("owner") or None,
            due_date=_safe_date(f.get("due_date")),
        ))

    return MeetingProcessCreate(
        summary=data.get("summary") or None,
        decisions=decisions,
        open_questions=[str(q) for q in (data.get("open_questions") or []) if q],
        commitments=commitments,
        follow_ups=follow_ups,
        create_tasks=False,
        processor=PROCESSOR_ID,
    )
