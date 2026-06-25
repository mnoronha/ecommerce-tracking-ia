"""
Shopify Page Publisher

Cria uma página no Online Store > Pages do Shopify com schema markup correto.
Requer que o token do cliente tenha scope write_content.

Campos mapeados:
  content_pieces.url_published  ← URL da página publicada
  content_pieces.status         → 'published' após sucesso
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)

_SHOPIFY_API_VERSION = "2024-01"

_SCHEMA_BY_TYPE = {
    "guide":          "Article",
    "pillar_article": "Article",
    "faq":            "FAQPage",
    "use_case":       "HowTo",
    "comparison":     "Article",
    "glossary":       "Article",
}


def _make_slug(title: str) -> str:
    slug = title.lower()
    for src, dst in [
        ("áàãâä", "a"), ("éèêë", "e"), ("íìîï", "i"),
        ("óòõôö", "o"), ("úùûü", "u"), ("ç", "c"),
    ]:
        for ch in src:
            slug = slug.replace(ch, dst)
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return re.sub(r"-+", "-", slug).strip("-")[:60]


def _inline_md(text: str) -> str:
    import html as _h
    text = _h.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    return text


def _markdown_to_html(md: str) -> str:
    lines    = md.split("\n")
    out      = []
    in_list  = False
    in_ol    = False
    ol_num   = 0

    for raw in lines:
        line = raw.rstrip()

        # Ordered list (1. 2. …)
        m_ol = re.match(r"^(\d+)\.\s+(.*)", line)
        if m_ol:
            if not in_ol:
                if in_list:
                    out.append("</ul>"); in_list = False
                out.append("<ol>"); in_ol = True
            out.append(f"<li>{_inline_md(m_ol.group(2))}</li>")
            continue
        if in_ol and not re.match(r"^\d+\.\s", line):
            out.append("</ol>"); in_ol = False

        # Unordered list
        if line.startswith("- ") or line.startswith("* "):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{_inline_md(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>"); in_list = False

        # Headings
        if line.startswith("### "):
            out.append(f"<h3>{_inline_md(line[4:])}</h3>"); continue
        if line.startswith("## "):
            out.append(f"<h2>{_inline_md(line[3:])}</h2>"); continue
        if line.startswith("# "):
            out.append(f"<h1>{_inline_md(line[2:])}</h1>"); continue

        # Blank / paragraph
        if not line:
            out.append(""); continue
        out.append(f"<p>{_inline_md(line)}</p>")

    if in_list: out.append("</ul>")
    if in_ol:   out.append("</ol>")
    return "\n".join(out)


def _build_schema_markup(
    content_type: str, title: str, description: str,
    body_md: str, url: str,
) -> str:
    schema_type = _SCHEMA_BY_TYPE.get(content_type, "Article")

    if schema_type == "FAQPage":
        qa_pairs: list[dict] = []
        current_q: Optional[str] = None
        current_a: list[str]     = []
        for line in body_md.split("\n"):
            if line.startswith("## "):
                if current_q:
                    qa_pairs.append({"q": current_q, "a": " ".join(current_a)})
                    current_a = []
                current_q = line[3:].strip()
            elif current_q and line.strip():
                current_a.append(line.strip())
        if current_q:
            qa_pairs.append({"q": current_q, "a": " ".join(current_a)})
        entities = ",".join(
            '{"@type":"Question","name":"%s","acceptedAnswer":{"@type":"Answer","text":"%s"}}'
            % (q["q"].replace('"', '\\"'), q["a"][:300].replace('"', '\\"'))
            for q in qa_pairs[:20]
        )
        return f'<script type="application/ld+json">{{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[{entities}]}}</script>'

    if schema_type == "HowTo":
        steps = [
            line.lstrip("# ").strip()
            for line in body_md.split("\n")
            if line.startswith("## ") or line.startswith("### ")
        ]
        steps_json = ",".join(
            '{"@type":"HowToStep","text":"%s"}' % s.replace('"', '\\"')
            for s in steps[:15]
        )
        return (
            f'<script type="application/ld+json">{{"@context":"https://schema.org","@type":"HowTo",'
            f'"name":"{title.replace(chr(34), chr(92)+chr(34))}","description":"{description[:200].replace(chr(34), chr(92)+chr(34))}",'
            f'"step":[{steps_json}]}}</script>'
        )

    # Article (default)
    return (
        f'<script type="application/ld+json">{{"@context":"https://schema.org","@type":"Article",'
        f'"headline":"{title.replace(chr(34), chr(92)+chr(34))}","description":"{description[:200].replace(chr(34), chr(92)+chr(34))}",'
        f'"url":"{url}"}}</script>'
    )


def _generate_seo_fields(title: str, body_md: str) -> tuple[str, str]:
    """Returns (meta_description, slug). Uses Claude if available, else heuristic."""
    slug = _make_slug(title)

    if settings.ANTHROPIC_API_KEY:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            resp = client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=80,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Meta description em português para '{title}'. "
                        f"Máximo 155 chars, inclua palavras-chave. "
                        f"Início: {body_md[:400]}\n\nSó a meta description, sem aspas."
                    ),
                }],
            )
            meta = resp.content[0].text.strip()[:155]
            return meta, slug
        except Exception as exc:
            logger.warning("seo via Claude falhou: %s", exc)

    # Fallback: first non-heading paragraph
    for line in body_md.split("\n"):
        s = line.strip()
        if s and not s.startswith("#"):
            return s[:155], slug
    return title[:155], slug


def publish_piece(piece_id: str, client_id: str) -> dict:
    """
    Publica a versão mais recente aprovada no Shopify como página.
    Retorna {"ok": True, "url": "..."} ou {"ok": False, "error": "..."}.
    """
    sb = get_supabase()

    # Cliente + Shopify creds
    client_rows = sb.table("clients").select(
        "id,name,shopify_domain,shopify_access_token"
    ).eq("id", client_id).limit(1).execute().data
    if not client_rows:
        return {"ok": False, "error": "cliente não encontrado"}
    client = client_rows[0]

    domain = (client.get("shopify_domain") or "").strip().rstrip("/")
    token  = client.get("shopify_access_token") or ""
    if token.startswith("enc:v1:"):
        from . import crypto as _crypto
        token = _crypto.decrypt(token)
    if not domain or not token:
        return {"ok": False, "error": "cliente sem shopify_domain ou shopify_access_token"}

    # Peça
    piece_rows = sb.table("content_pieces").select(
        "id,final_title,briefing_id,current_version"
    ).eq("id", piece_id).limit(1).execute().data
    if not piece_rows:
        return {"ok": False, "error": "peça não encontrada"}
    piece = piece_rows[0]

    content_type = "guide"
    if piece.get("briefing_id"):
        br = sb.table("content_briefings").select("content_type").eq("id", piece["briefing_id"]).limit(1).execute().data
        if br:
            content_type = br[0].get("content_type", "guide")

    # Versão mais recente
    ver_rows = (
        sb.table("content_piece_versions")
        .select("title,body_markdown")
        .eq("piece_id", piece_id)
        .order("version_number", desc=True)
        .limit(1)
        .execute()
    ).data
    if not ver_rows:
        return {"ok": False, "error": "nenhuma versão encontrada"}
    version  = ver_rows[0]
    title    = version.get("title") or piece.get("final_title") or "Sem título"
    body_md  = version.get("body_markdown") or ""

    # SEO
    meta_desc, handle = _generate_seo_fields(title, body_md)

    # HTML + schema
    body_html  = _markdown_to_html(body_md)
    page_url   = f"https://{domain}/pages/{handle}"
    schema     = _build_schema_markup(content_type, title, meta_desc, body_md, page_url)
    full_html  = f"{schema}\n{body_html}"

    # Shopify API
    api_url = f"https://{domain}/admin/api/{_SHOPIFY_API_VERSION}/pages.json"
    payload = {
        "page": {
            "title":      title,
            "body_html":  full_html,
            "handle":     handle,
            "published":  True,
            "metafields": [{
                "namespace": "global",
                "key":       "description_tag",
                "value":     meta_desc,
                "type":      "single_line_text_field",
            }],
        }
    }
    try:
        resp = httpx.post(
            api_url,
            headers={
                "X-Shopify-Access-Token": token,
                "Content-Type":           "application/json",
            },
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        published_handle = resp.json().get("page", {}).get("handle", handle)
        published_url    = f"https://{domain}/pages/{published_handle}"
    except httpx.HTTPStatusError as exc:
        err = exc.response.text[:400]
        logger.error("shopify_publisher: HTTP %s — %s", exc.response.status_code, err)
        return {"ok": False, "error": f"Shopify HTTP {exc.response.status_code}: {err}"}
    except Exception as exc:
        logger.error("shopify_publisher: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}

    # Persiste
    now = datetime.now(timezone.utc).isoformat()
    sb.table("content_pieces").update({
        "url_published":   published_url,
        "meta_description": meta_desc,
        "slug":            handle,
        "published_at":    now,
        "status":          "published",
        "updated_at":      now,
    }).eq("id", piece_id).execute()

    logger.info("shopify_publisher: published %s → %s", piece_id, published_url)
    return {"ok": True, "url": published_url}
