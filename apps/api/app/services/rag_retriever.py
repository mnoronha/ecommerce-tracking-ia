"""
RAG Retriever — busca chunks relevantes por similaridade vetorial.

Usa pgvector (cosine distance) via função match_rag_chunks no Supabase.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)

_VOYAGE_API = "https://api.voyageai.com/v1"


def _embed_query(query: str) -> list[float]:
    """Gera embedding para query (input_type='query' é diferente de 'document')."""
    api_key = getattr(settings, "VOYAGE_API_KEY", "")
    if not api_key:
        raise RuntimeError("VOYAGE_API_KEY não configurada")
    resp = httpx.post(
        f"{_VOYAGE_API}/embeddings",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": "voyage-3-large", "input": [query], "input_type": "query"},
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Voyage embed query {resp.status_code}: {resp.text[:200]}")
    return resp.json()["data"][0]["embedding"]


def retrieve(
    client_id: str,
    query: str,
    top_k: int = 15,
    categories: Optional[list[str]] = None,
    min_similarity: float = 0.5,
) -> list[dict]:
    """
    Busca chunks relevantes por similaridade vetorial.
    Retorna lista de chunks com score de similaridade.
    """
    sb = get_supabase()

    try:
        query_embedding = _embed_query(query)
    except Exception as exc:
        logger.error("rag_retriever: embed query failed: %s", exc)
        return []

    params: dict = {
        "query_embedding":  query_embedding,
        "p_client_id":      client_id,
        "match_threshold":  min_similarity,
        "match_count":      top_k,
    }
    if categories:
        params["p_categories"] = categories

    try:
        result = sb.rpc("match_rag_chunks", params).execute()
        chunks = result.data or []
    except Exception as exc:
        logger.error("rag_retriever: match_rag_chunks failed: %s", exc)
        return []

    # Atualiza retrieval_count em background (fire-and-forget, ignora erro)
    if chunks:
        ids = [c["id"] for c in chunks]
        try:
            now = datetime.now(timezone.utc).isoformat()
            for chunk_id in ids:
                sb.rpc("increment_rag_retrieval", {"p_chunk_id": chunk_id}).execute()
        except Exception:
            pass

    return chunks


def keyword_search(client_id: str, query: str, top_k: int = 15) -> list[dict]:
    """Busca por palavras-chave via PostgreSQL full-text search."""
    sb = get_supabase()
    try:
        # tsquery: palavras separadas por &
        terms = " & ".join(query.split()[:10])
        result = (
            sb.table("rag_chunks")
            .select("id,chunk_text,section_title,document_id")
            .eq("client_id", client_id)
            .text_search("chunk_text", terms, config="portuguese")
            .limit(top_k)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        logger.warning("rag_retriever: keyword_search failed: %s", exc)
        return []


def hybrid_retrieve(
    client_id: str,
    query: str,
    top_k: int = 15,
    categories: Optional[list[str]] = None,
) -> list[dict]:
    """
    Retrieval híbrido: combina similaridade vetorial + keyword search
    via Reciprocal Rank Fusion (RRF).
    """
    vector_results  = retrieve(client_id, query, top_k=top_k, categories=categories)
    keyword_results = keyword_search(client_id, query, top_k=top_k)

    # RRF: score = 1/(rank + 60) somado para cada lista
    k = 60
    scores: dict[str, float] = {}
    id_to_chunk: dict[str, dict] = {}

    for rank, chunk in enumerate(vector_results):
        cid = chunk["id"]
        scores[cid] = scores.get(cid, 0) + 1 / (rank + k)
        id_to_chunk[cid] = chunk

    for rank, chunk in enumerate(keyword_results):
        cid = chunk["id"]
        scores[cid] = scores.get(cid, 0) + 1 / (rank + k)
        if cid not in id_to_chunk:
            id_to_chunk[cid] = chunk

    ranked = sorted(scores.keys(), key=lambda x: -scores[x])
    return [id_to_chunk[cid] for cid in ranked[:top_k]]
