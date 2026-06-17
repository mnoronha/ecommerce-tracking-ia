"""
RAG Indexer — pipeline de indexação de documentos.

Fluxo: upload → extração de texto → chunking → embedding (Voyage AI) → salva no pgvector.
Suporta: PDF, DOCX, TXT, MD.
"""

from __future__ import annotations

import io
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import httpx

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)

_VOYAGE_API = "https://api.voyageai.com/v1"
_CHUNK_SIZE = 400        # palavras por chunk (aprox 500 tokens)
_CHUNK_OVERLAP = 40      # palavras de overlap
_BATCH_EMB = 128         # max textos por chamada Voyage


# ── Embedding ─────────────────────────────────────────────────────────────────

def _voyage_embed(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """Chama Voyage AI para gerar embeddings em batch."""
    api_key = getattr(settings, "VOYAGE_API_KEY", "")
    if not api_key:
        raise RuntimeError("VOYAGE_API_KEY não configurada")

    embeddings: list[list[float]] = []
    for i in range(0, len(texts), _BATCH_EMB):
        batch = texts[i : i + _BATCH_EMB]
        resp = httpx.post(
            f"{_VOYAGE_API}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": "voyage-3-large", "input": batch, "input_type": input_type},
            timeout=60.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Voyage API {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        for item in sorted(data["data"], key=lambda x: x["index"]):
            embeddings.append(item["embedding"])
        time.sleep(0.2)

    return embeddings


# ── Extração de texto ─────────────────────────────────────────────────────────

def extract_text(raw_bytes: bytes, mime_type: str) -> str:
    """Extrai texto de PDF, DOCX, TXT ou MD."""
    if mime_type == "application/pdf":
        return _extract_pdf(raw_bytes)
    if mime_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ):
        return _extract_docx(raw_bytes)
    return raw_bytes.decode("utf-8", errors="replace")


def _extract_pdf(raw: bytes) -> str:
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(raw))
        pages = []
        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text.strip())
        return "\n\n".join(p for p in pages if p)
    except ImportError:
        raise RuntimeError("pypdf não instalado — adicione ao requirements.txt")


def _extract_docx(raw: bytes) -> str:
    try:
        import docx
        doc = docx.Document(io.BytesIO(raw))
        paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paras)
    except ImportError:
        raise RuntimeError("python-docx não instalado — adicione ao requirements.txt")


# ── Chunking ──────────────────────────────────────────────────────────────────

def create_chunks(text: str, title: Optional[str] = None) -> list[dict]:
    """
    Divide texto em chunks com overlap.
    Respeita parágrafos e preserva section_title de headers Markdown.
    """
    words = text.split()
    if not words:
        return []

    chunks: list[dict] = []
    start = 0
    current_section: Optional[str] = None
    # Pre-index de seções (headers markdown)
    header_positions = _find_section_headers(words)

    while start < len(words):
        end = min(start + _CHUNK_SIZE, len(words))
        chunk_words = words[start:end]
        chunk_text = " ".join(chunk_words)

        # Detecta section_title do trecho
        section = _get_section_at(header_positions, start) or current_section
        if section:
            current_section = section

        chunks.append({
            "text":          chunk_text,
            "section_title": section,
            "index":         len(chunks),
        })

        if end >= len(words):
            break
        start = end - _CHUNK_OVERLAP

    return chunks


def _find_section_headers(words: list[str]) -> list[tuple[int, str]]:
    """Retorna lista de (posição, título) para headers # encontrados."""
    positions = []
    i = 0
    while i < len(words):
        w = words[i]
        if w.startswith("#"):
            level = len(w) - len(w.lstrip("#"))
            if 1 <= level <= 3:
                title_words = []
                j = i + 1
                while j < len(words) and not words[j].startswith("#"):
                    title_words.append(words[j])
                    j += 1
                    if len(title_words) > 10:
                        break
                title = " ".join(title_words)
                positions.append((i, title))
        i += 1
    return positions


def _get_section_at(positions: list[tuple[int, str]], word_pos: int) -> Optional[str]:
    """Retorna a seção vigente na posição word_pos."""
    current = None
    for pos, title in positions:
        if pos <= word_pos:
            current = title
        else:
            break
    return current


# ── Pipeline principal ────────────────────────────────────────────────────────

def index_document(document_id: str) -> dict:
    """
    Pipeline completo de indexação de um documento.
    Retorna resumo do processamento.
    """
    sb = get_supabase()

    def _update_status(status: str, error: str = None):
        patch = {"processing_status": status}
        if error:
            patch["processing_error"] = error[:500]
        if status == "completed":
            patch["processed_at"] = datetime.now(timezone.utc).isoformat()
        sb.table("rag_documents").update(patch).eq("id", document_id).execute()

    _update_status("extracting")
    try:
        doc_rows = (
            sb.table("rag_documents")
            .select("*")
            .eq("id", document_id)
            .limit(1)
            .execute()
        ).data
        if not doc_rows:
            raise ValueError(f"Document {document_id} not found")
        doc = doc_rows[0]

        # Extrai texto se necessário
        raw_text = doc.get("raw_text") or ""
        if not raw_text and doc.get("file_path"):
            file_bytes = _download_file(doc["file_path"])
            raw_text = extract_text(file_bytes, doc.get("file_mime_type", "text/plain"))
            word_count = len(raw_text.split())
            sb.table("rag_documents").update({
                "raw_text":   raw_text,
                "word_count": word_count,
            }).eq("id", document_id).execute()

        if not raw_text.strip():
            raise ValueError("Documento sem conteúdo textual")

        # Chunking
        _update_status("chunking")
        chunks = create_chunks(raw_text, doc.get("title"))

        # Embedding
        _update_status("embedding")
        texts = [c["text"] for c in chunks]
        embeddings = _voyage_embed(texts, input_type="document")

        # Deleta chunks antigos (re-indexação)
        sb.table("rag_chunks").delete().eq("document_id", document_id).execute()

        # Salva chunks em batch de 200
        records = []
        iso = datetime.now(timezone.utc).isoformat()
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            records.append({
                "document_id":        document_id,
                "knowledge_base_id":  doc["knowledge_base_id"],
                "client_id":          doc["client_id"],
                "chunk_text":         chunk["text"],
                "chunk_index":        i,
                "total_chunks_in_doc": len(chunks),
                "section_title":      chunk.get("section_title"),
                "embedding":          emb,
                "embedding_model":    "voyage-3-large",
            })

        for i in range(0, len(records), 200):
            sb.table("rag_chunks").insert(records[i : i + 200]).execute()

        # Atualiza meta da knowledge base
        kb_id = doc.get("knowledge_base_id")
        if kb_id:
            _refresh_kb_meta(kb_id)

        _update_status("completed")
        return {"chunks": len(chunks), "status": "completed"}

    except Exception as exc:
        logger.error("rag_indexer: document %s failed: %s", document_id, exc)
        _update_status("failed", str(exc))
        return {"error": str(exc), "status": "failed"}


def _download_file(file_path: str) -> bytes:
    """Baixa arquivo do Supabase Storage."""
    from ..database import get_supabase
    sb = get_supabase()
    bucket = "rag-documents"
    resp = sb.storage.from_(bucket).download(file_path)
    return resp


def _refresh_kb_meta(knowledge_base_id: str) -> None:
    """Atualiza total_documents e total_chunks na knowledge base."""
    sb = get_supabase()
    doc_count = (
        sb.table("rag_documents")
        .select("id", count="exact")
        .eq("knowledge_base_id", knowledge_base_id)
        .eq("is_active", True)
        .eq("processing_status", "completed")
        .execute()
    ).count or 0
    chunk_count = (
        sb.table("rag_chunks")
        .select("id", count="exact")
        .eq("knowledge_base_id", knowledge_base_id)
        .execute()
    ).count or 0
    sb.table("rag_knowledge_bases").update({
        "total_documents":    doc_count,
        "total_chunks":       chunk_count,
        "last_reindexed_at":  datetime.now(timezone.utc).isoformat(),
        "updated_at":         datetime.now(timezone.utc).isoformat(),
    }).eq("id", knowledge_base_id).execute()
