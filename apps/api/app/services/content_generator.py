"""
Content Generator — orquestra geração completa de conteúdo com RAG.

Usa Claude com prompt caching para economizar tokens.
Suporta múltiplos provedores via model_router.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from ..config import settings
from ..database import get_supabase
from . import model_router, rag_retriever

logger = logging.getLogger(__name__)

_LENGTH_GUIDE = {
    "short":  "800-1000 palavras",
    "medium": "1400-1800 palavras",
    "long":   "2200-2800 palavras",
    "pillar": "3500-4500 palavras",
}

_CONTENT_TYPE_GUIDE = {
    "comparison":    "Comparativo detalhado com tabela e análise ponto a ponto",
    "guide":         "Guia completo com passo a passo, dicas práticas e exemplos",
    "faq":           "FAQ estruturado com perguntas e respostas completas (mín. 8 perguntas)",
    "use_case":      "Caso de uso real com contexto, solução e resultado",
    "glossary":      "Glossário com definições claras e exemplos de uso",
    "pillar_article": "Artigo pilar abrangente que cobre o tema completamente, com seções linkáveis",
}

_FORBIDDEN_AI_PHRASES = [
    "ressaltar", "é importante notar", "vale destacar", "em última análise",
    "no que tange", "outrossim", "primordialmente", "cabe destacar",
    "nesse sentido", "no âmbito de", "à luz de", "em termos de",
    "certamente", "definitivamente", "evidentemente",
]


def _call_claude(model_id: str, prompt: str, system: str, temperature: float, max_tokens: int) -> dict:
    """Chama Claude API com prompt caching."""
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    start = time.time()

    msg = client.messages.create(
        model=model_id,
        max_tokens=max_tokens,
        temperature=temperature,
        system=[
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},  # cache do contexto estático
            }
        ],
        messages=[{"role": "user", "content": prompt}],
    )

    duration_ms = int((time.time() - start) * 1000)
    content = msg.content[0].text
    usage = msg.usage

    input_tokens  = usage.input_tokens
    output_tokens = usage.output_tokens
    # Preço aproximado Claude Sonnet 4.6: $3/1M input, $15/1M output
    cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000

    return {
        "content":       content,
        "tokens_input":  input_tokens,
        "tokens_output": output_tokens,
        "cost_usd":      cost,
        "duration_ms":   duration_ms,
    }


def _format_rag_context(chunks: list[dict]) -> str:
    if not chunks:
        return "(Nenhum contexto específico encontrado na base de conhecimento.)"
    by_doc: dict[str, list] = {}
    for chunk in chunks:
        key = chunk.get("document_title") or "Documento"
        by_doc.setdefault(key, []).append(chunk)

    sections = []
    for doc_title, doc_chunks in by_doc.items():
        section = f"### De '{doc_title}':\n\n"
        for chunk in doc_chunks[:5]:
            if chunk.get("section_title"):
                section += f"**{chunk['section_title']}**\n"
            section += f"{chunk['chunk_text']}\n\n"
        sections.append(section)
    return "\n".join(sections)


def _build_system_prompt(kb: dict, client_name: str) -> str:
    brand_dos   = "\n".join(f"- {x}" for x in (kb.get("brand_dos") or []))
    brand_donts = "\n".join(f"- {x}" for x in (kb.get("brand_donts") or []))
    forbidden   = ", ".join(kb.get("forbidden_terms") or [])
    forbidden_ai = ", ".join(_FORBIDDEN_AI_PHRASES)

    return f"""Você é redator especializado em conteúdo otimizado para descoberta por IA (ChatGPT, Gemini, Perplexity, Claude).
Está produzindo conteúdo para a marca {client_name}.

TOM DE VOZ DA MARCA:
{kb.get("brand_voice") or "Profissional, claro e direto."}

A MARCA SEMPRE FAZ:
{brand_dos or "- Usa linguagem clara e acessível\n- Cita fontes quando relevante"}

A MARCA NUNCA FAZ:
{brand_donts or "- Usa jargão desnecessário\n- Faz promessas não verificáveis"}

TERMOS PROIBIDOS: {forbidden or "Nenhum específico."}

MULETAS DE IA QUE VOCÊ NUNCA USA: {forbidden_ai}

REGRAS CRÍTICAS:
1. Português brasileiro natural. Sem muletas de IA listadas acima.
2. Parágrafos de 2-4 linhas. Headers semânticos (H2, H3).
3. NÃO invente especificações de produto — use apenas dados do contexto fornecido.
4. NÃO invente estatísticas. Se citar dado externo: [DADO A VERIFICAR].
5. Conteúdo autossuficiente — IA deve conseguir extrair resposta completa sem visitar outras páginas.
6. Evite linguagem genérica de marketing. Seja específico.
7. Produza em Markdown. Comece direto do título (H1). Sem preâmbulos."""


def generate_piece(briefing_id: str) -> dict:
    """
    Gera primeira versão de conteúdo a partir de um briefing.
    Retorna piece_id e version_id.
    """
    sb = get_supabase()

    # Carrega briefing
    briefing_rows = (
        sb.table("content_briefings")
        .select("*")
        .eq("id", briefing_id)
        .limit(1)
        .execute()
    ).data
    if not briefing_rows:
        return {"error": "briefing not found"}
    briefing = briefing_rows[0]
    client_id = briefing["client_id"]

    # Carrega knowledge base
    kb_rows = (
        sb.table("rag_knowledge_bases")
        .select("*")
        .eq("client_id", client_id)
        .limit(1)
        .execute()
    ).data
    kb = kb_rows[0] if kb_rows else {}

    # Carrega nome do cliente
    client_rows = (
        sb.table("clients").select("name").eq("id", client_id).limit(1).execute()
    ).data
    client_name = client_rows[0]["name"] if client_rows else "Cliente"

    # Determina modelo
    model_cfg = model_router.get_model_for_task(
        task="generation",
        client_id=client_id,
        content_type=briefing.get("content_type"),
    )
    provider  = model_cfg["provider"]
    model_id  = model_cfg["model_id"]
    temperature = float(model_cfg.get("temperature", 0.7))
    max_tokens  = int(model_cfg.get("max_tokens", 8000))

    # Recupera chunks do RAG
    rag_query = " ".join(filter(None, [
        briefing.get("working_title"),
        briefing.get("target_query"),
        " ".join(briefing.get("target_keywords") or []),
        " ".join(briefing.get("products_to_mention") or []),
    ]))
    chunks = rag_retriever.hybrid_retrieve(
        client_id=client_id,
        query=rag_query,
        top_k=20,
        categories=["brand_identity", "products", "existing_content", "market_data"],
    )

    # Monta prompts
    system_prompt = _build_system_prompt(kb, client_name)
    rag_context   = _format_rag_context(chunks)

    keywords   = ", ".join(briefing.get("target_keywords") or [])
    products   = ", ".join(briefing.get("products_to_mention") or [])
    competitors = ", ".join(briefing.get("competitors_to_cite") or [])
    length_guide = _LENGTH_GUIDE.get(briefing.get("required_length", "medium"), "1400-1800 palavras")
    type_guide   = _CONTENT_TYPE_GUIDE.get(briefing.get("content_type", "guide"), "")

    user_prompt = f"""CONTEXTO DA MARCA (da base de conhecimento):

{rag_context}

---

BRIEFING:

Tipo de conteúdo: {briefing.get("content_type")} — {type_guide}
Título de trabalho: {briefing.get("working_title")}
Pergunta que esta peça deve responder: "{briefing.get("target_query") or 'Ver título'}"
Público-alvo: {briefing.get("target_audience") or "Consumidor final"}
Comprimento: {length_guide}
Palavras-chave a usar naturalmente: {keywords or "Sem restrição"}
Produtos a mencionar: {products or "Qualquer produto relevante"}
Competidores a citar: {competitors or "Nenhum específico"}
Tom específico desta peça: {briefing.get("tone_override") or "Tom padrão da marca"}
Estrutura requerida: {briefing.get("required_structure") or "Livre (siga as melhores práticas do tipo)"}
Instruções adicionais: {briefing.get("additional_instructions") or "Nenhuma"}

PRODUZA A PEÇA COMPLETA EM MARKDOWN agora."""

    # Atualiza status do briefing
    sb.table("content_briefings").update({"status": "generating"}).eq("id", briefing_id).execute()

    # Cria piece placeholder
    now = datetime.now(timezone.utc).isoformat()
    piece_row = (
        sb.table("content_pieces").insert({
            "briefing_id":    briefing_id,
            "client_id":      client_id,
            "status":         "draft",
            "current_version": 1,
        }).execute()
    ).data[0]
    piece_id = piece_row["id"]

    try:
        if provider == "anthropic":
            response = _call_claude(model_id, user_prompt, system_prompt, temperature, max_tokens)
        else:
            raise NotImplementedError(f"Provider {provider} não implementado ainda")

        # Calcula word count
        word_count = len(response["content"].split())
        # Extrai título (primeira linha H1)
        lines = response["content"].strip().splitlines()
        title = lines[0].lstrip("# ").strip() if lines else briefing.get("working_title")

        # Salva versão 1
        version_row = (
            sb.table("content_piece_versions").insert({
                "piece_id":               piece_id,
                "client_id":              client_id,
                "version_number":         1,
                "version_type":           "ai_generated",
                "title":                  title,
                "body_markdown":          response["content"],
                "word_count":             word_count,
                "generation_model":       model_id,
                "generation_temperature": temperature,
                "rag_chunks_used":        [c["id"] for c in chunks],
                "tokens_input":           response["tokens_input"],
                "tokens_output":          response["tokens_output"],
                "generation_cost_usd":    response["cost_usd"],
                "generation_duration_ms": response["duration_ms"],
            }).execute()
        ).data[0]
        version_id = version_row["id"]

        # Atualiza piece com título
        sb.table("content_pieces").update({
            "final_title":     title,
            "current_version": 1,
            "updated_at":      now,
        }).eq("id", piece_id).execute()

        # Log de uso de IA
        _log_ai_usage(
            client_id=client_id,
            task="generation",
            entity_type="content_piece",
            entity_id=piece_id,
            provider=provider,
            model_id=model_id,
            tokens_input=response["tokens_input"],
            tokens_output=response["tokens_output"],
            cost_usd=response["cost_usd"],
            duration_ms=response["duration_ms"],
        )

        # Atualiza briefing
        sb.table("content_briefings").update({
            "status":     "generated",
            "updated_at": now,
        }).eq("id", briefing_id).execute()

        # Dispara fact-check assíncrono
        _trigger_factcheck(piece_id, version_id, client_id)

        return {"piece_id": piece_id, "version_id": version_id, "word_count": word_count}

    except Exception as exc:
        logger.error("content_generator: generation failed for briefing %s: %s", briefing_id, exc)
        sb.table("content_briefings").update({"status": "briefed"}).eq("id", briefing_id).execute()
        sb.table("content_pieces").delete().eq("id", piece_id).execute()
        return {"error": str(exc)}


def _log_ai_usage(
    client_id: str,
    task: str,
    entity_type: str,
    entity_id: str,
    provider: str,
    model_id: str,
    tokens_input: int,
    tokens_output: int,
    cost_usd: float,
    duration_ms: int,
) -> None:
    try:
        get_supabase().table("ai_usage_log").insert({
            "client_id":           client_id,
            "task":                task,
            "related_entity_type": entity_type,
            "related_entity_id":   entity_id,
            "provider":            provider,
            "model_id":            model_id,
            "tokens_input":        tokens_input,
            "tokens_output":       tokens_output,
            "cost_usd":            cost_usd,
            "duration_ms":         duration_ms,
            "was_successful":      True,
        }).execute()
    except Exception as exc:
        logger.warning("content_generator: ai_usage_log failed: %s", exc)


def _trigger_factcheck(piece_id: str, version_id: str, client_id: str) -> None:
    """Dispara fact-check em background — ignora erros para não bloquear geração."""
    try:
        from . import content_factchecker
        content_factchecker.factcheck_version(piece_id, version_id, client_id)
    except Exception as exc:
        logger.warning("content_generator: factcheck trigger failed: %s", exc)
