"""
Content Fact-Checker — verifica factualmente uma versão de peça gerada.

Usa modelo separado do gerador (Claude Haiku por default) para evitar vieses.
Retorna lista estruturada de claims a verificar e issues encontrados.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from ..config import settings
from ..database import get_supabase
from . import model_router

logger = logging.getLogger(__name__)

_FACTCHECK_PROMPT = """Você é fact-checker especializado em conteúdo de marketing digital em português.

Analise a peça abaixo e identifique pontos que precisam de verificação humana antes de publicar.

PEÇA:
{body}

Retorne APENAS um JSON válido (sem markdown, sem preâmbulo) com esta estrutura:

{{
  "overall_confidence": "high | medium | low",
  "facts_to_verify": [
    {{
      "claim": "claim factual específico extraído literalmente da peça",
      "location_hint": "primeira palavras do parágrafo onde aparece",
      "concern": "por que precisa verificação",
      "suggested_verification": "como verificar"
    }}
  ],
  "issues_found": [
    {{
      "type": "hallucination_risk | unsupported_claim | bias | factual_error | outdated_data",
      "description": "descrição objetiva do issue",
      "severity": "high | medium | low",
      "suggested_fix": "sugestão concreta de correção"
    }}
  ],
  "recommendation": "publicar como está | revisar pontos marcados | reescrever seções específicas"
}}

CRITÉRIOS:
- Especificações técnicas de produto sem fonte citada → facts_to_verify
- Estatísticas sem link ou autor → facts_to_verify
- Claims absolutos ("o melhor", "o único", "líder de mercado") → issues_found (unsupported_claim)
- Comparações subjetivas com competidores → issues_found (bias)
- Datas ou dados que podem estar desatualizados → facts_to_verify
- Se a peça for só texto geral sem claims específicos → facts_to_verify vazio, confidence high"""


def factcheck_version(piece_id: str, version_id: str, client_id: str) -> Optional[str]:
    """
    Executa fact-check em uma versão de peça.
    Retorna factcheck_id ou None se falhou.
    """
    sb = get_supabase()

    version_rows = (
        sb.table("content_piece_versions")
        .select("body_markdown,generation_model")
        .eq("id", version_id)
        .limit(1)
        .execute()
    ).data
    if not version_rows:
        logger.warning("factcheck: version %s not found", version_id)
        return None

    body = version_rows[0]["body_markdown"]

    # Usa modelo separado do gerador
    model_cfg = model_router.get_model_for_task(task="factcheck", client_id=client_id)
    model_id  = model_cfg["model_id"]

    prompt = _FACTCHECK_PROMPT.format(body=body[:8000])  # limita a 8k chars

    start = time.time()
    try:
        result_text = _call_model(model_cfg, prompt)
        duration_ms = int((time.time() - start) * 1000)
    except Exception as exc:
        logger.error("factcheck: call failed for piece %s: %s", piece_id, exc)
        return None

    # Parse JSON
    parsed = _safe_parse_json(result_text)

    overall    = parsed.get("overall_confidence", "medium")
    facts      = parsed.get("facts_to_verify", [])
    issues     = parsed.get("issues_found", [])
    rec        = parsed.get("recommendation", "revisar pontos marcados")

    tokens_est = len(prompt.split()) + len(result_text.split())
    cost_est   = tokens_est * 0.0000006  # Claude Haiku ~$0.25/1M input + $1.25/1M output

    try:
        row = (
            sb.table("content_factchecks").insert({
                "piece_id":         piece_id,
                "version_id":       version_id,
                "client_id":        client_id,
                "factcheck_model":  model_id,
                "overall_confidence": overall,
                "facts_to_verify":  facts,
                "issues_found":     issues,
                "recommendation":   rec,
                "tokens_used":      tokens_est,
                "cost_usd":         cost_est,
            }).execute()
        ).data[0]
        logger.info("factcheck: piece %s → confidence=%s, %d facts, %d issues", piece_id, overall, len(facts), len(issues))
        return row["id"]
    except Exception as exc:
        logger.error("factcheck: save failed: %s", exc)
        return None


def _call_model(model_cfg: dict, prompt: str) -> str:
    provider = model_cfg.get("provider", "anthropic")
    model_id = model_cfg["model_id"]

    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=model_id,
            max_tokens=model_cfg.get("max_tokens", 4000),
            temperature=float(model_cfg.get("temperature", 0.3)),
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    raise NotImplementedError(f"Provider {provider} não implementado")


def _safe_parse_json(text: str) -> dict:
    """Tenta extrair JSON mesmo se o modelo adicionou markdown."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("factcheck: could not parse JSON, returning empty result")
        return {"overall_confidence": "medium", "facts_to_verify": [], "issues_found": [], "recommendation": "revisar pontos marcados"}


# Needed for type hint inside factcheck_version
from typing import Optional
