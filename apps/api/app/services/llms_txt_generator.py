"""
llms.txt Generator — creates a machine-readable llms.txt for a Shopify store,
following the proposed llms.txt specification (https://llmstxt.org).

The file is placed at /llms.txt via a Shopify page with handle 'llms-txt'
and served via a custom route — or applied as a theme asset.

Content is assembled from:
  - Client/brand data (name, URL, description)
  - RAG knowledge base (top chunks with allow_llm=true)
  - Shopify product collections (top categories)
"""

import logging
import re
from typing import Optional

import httpx

from ..database import get_supabase
from ..services.crypto import decrypt_secret

logger = logging.getLogger(__name__)
_TIMEOUT = 15


def _get_client(client_id: str) -> dict:
    sb = get_supabase()
    rows = (
        sb.table("clients")
        .select("id,name,shopify_domain,shopify_access_token,website_url")
        .eq("id", client_id)
        .limit(1)
        .execute()
    ).data
    if not rows:
        raise ValueError("client not found")
    return rows[0]


def _get_rag_context(client_id: str) -> str:
    """Fetch top RAG knowledge snippets for brand context."""
    sb = get_supabase()
    try:
        # Get knowledge base
        kb = (
            sb.table("rag_knowledge_bases")
            .select("id")
            .eq("client_id", client_id)
            .limit(1)
            .execute()
        ).data
        if not kb:
            return ""
        kb_id = kb[0]["id"]

        # Get top documents
        docs = (
            sb.table("rag_documents")
            .select("title,description")
            .eq("knowledge_base_id", kb_id)
            .eq("status", "indexed")
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        ).data or []

        return "\n".join(
            f"- {d['title']}: {(d.get('description') or '')[:200]}"
            for d in docs
            if d.get("title")
        )
    except Exception as exc:
        logger.debug("RAG context fetch failed: %s", exc)
        return ""


def _get_shopify_collections(domain: str, token: str) -> list[str]:
    try:
        r = httpx.get(
            f"https://{domain}/admin/api/2024-01/custom_collections.json?limit=10&fields=title",
            headers={"X-Shopify-Access-Token": token},
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            return [c["title"] for c in r.json().get("custom_collections", [])]
    except Exception:
        pass
    return []


def generate_llms_txt(client_id: str) -> dict:
    """
    Generates llms.txt content and returns it as a string.
    Does NOT apply to the site automatically — user must confirm.
    """
    client       = _get_client(client_id)
    name         = client.get("name", "Loja")
    domain       = (client.get("shopify_domain") or "").strip().rstrip("/")
    website      = client.get("website_url") or (f"https://{domain}" if domain else "")
    token_raw    = client.get("shopify_access_token") or ""
    token        = decrypt_secret(token_raw) if token_raw.startswith("enc:v1:") else token_raw

    rag_context  = _get_rag_context(client_id)
    collections  = _get_shopify_collections(domain, token) if domain and token else []

    lines = [
        f"# {name}",
        "",
        f"> {name} é uma loja online brasileira. Este arquivo descreve o conteúdo e as políticas de uso para sistemas de IA.",
        "",
        "## Sobre",
        f"- **Site:** {website}",
        f"- **Domínio:** {domain}",
        f"- **Idioma principal:** Português (Brasil)",
        "",
    ]

    if collections:
        lines += [
            "## Categorias principais",
            *[f"- {c}" for c in collections[:8]],
            "",
        ]

    if rag_context:
        lines += [
            "## Base de conhecimento",
            rag_context,
            "",
        ]

    lines += [
        "## Política de uso por IA",
        f"- {name} permite que sistemas de IA (incluindo ChatGPT, Gemini, Perplexity e Claude) indexem e citem seu conteúdo.",
        "- Citações devem incluir o nome da marca e um link para o site original.",
        "- Dados de preços e disponibilidade podem estar desatualizados — sempre consulte o site oficial.",
        "- Para uso comercial ou redistribuição massiva de conteúdo, entre em contato.",
        "",
        "## Contato",
        f"- **Site:** {website}",
        "",
        "## Bots permitidos",
        "- GPTBot: allow",
        "- ClaudeBot: allow",
        "- PerplexityBot: allow",
        "- Google-Extended: allow",
        "- anthropic-ai: allow",
        "- cohere-ai: allow",
        "",
        "---",
        f"*Gerado automaticamente pela Noro Platform em {__import__('datetime').date.today().isoformat()}*",
    ]

    content = "\n".join(lines)
    return {
        "client_id":  client_id,
        "content":    content,
        "filename":   "llms.txt",
        "line_count": len(lines),
    }


def apply_llms_txt(client_id: str, content: str) -> dict:
    """
    Applies the llms.txt to the Shopify store as a theme asset at `/assets/llms.txt`.
    Also creates a Shopify page at /pages/llms-txt for HTTP access.
    """
    client    = _get_client(client_id)
    domain    = (client.get("shopify_domain") or "").strip().rstrip("/")
    token_raw = client.get("shopify_access_token") or ""
    token     = decrypt_secret(token_raw) if token_raw.startswith("enc:v1:") else token_raw

    if not domain or not token:
        raise ValueError("cliente sem shopify_domain ou shopify_access_token")

    try:
        # Create/update a Shopify page with handle llms-txt
        # Check if page exists
        pages_resp = httpx.get(
            f"https://{domain}/admin/api/2024-01/pages.json?handle=llms-txt&fields=id",
            headers={"X-Shopify-Access-Token": token},
            timeout=_TIMEOUT,
        )
        pages = pages_resp.json().get("pages", [])

        html_content = f"<pre>{content}</pre>"

        if pages:
            page_id = pages[0]["id"]
            httpx.put(
                f"https://{domain}/admin/api/2024-01/pages/{page_id}.json",
                headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
                json={"page": {"id": page_id, "body_html": html_content}},
                timeout=_TIMEOUT,
            )
        else:
            httpx.post(
                f"https://{domain}/admin/api/2024-01/pages.json",
                headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
                json={"page": {
                    "title":     "llms.txt",
                    "handle":    "llms-txt",
                    "body_html": html_content,
                    "published": True,
                }},
                timeout=_TIMEOUT,
            )

        url = f"https://{domain}/pages/llms-txt"

        # Log to optimization_history
        get_supabase().table("optimization_history").insert({
            "client_id":   client_id,
            "type":        "llms_txt",
            "title":       "llms.txt aplicado",
            "description": f"Arquivo llms.txt publicado em {url}",
            "after_value": content[:500],
        }).execute()

        return {"ok": True, "url": url, "domain": domain}

    except Exception as exc:
        logger.exception("apply_llms_txt failed: %s", exc)
        raise
