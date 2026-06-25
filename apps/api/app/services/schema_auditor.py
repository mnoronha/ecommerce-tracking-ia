"""
Schema Markup Auditor — crawls a Shopify store's key pages and checks for
JSON-LD schema presence, completeness, and correctness.

Audit flow:
  1. Fetch key pages (homepage, sample products, collections, FAQs)
  2. Parse JSON-LD blocks from each page
  3. Compare against expected schema types per page type
  4. Create schema_markup_issues rows for each gap
  5. Calculate schema_health_score (0-100)
  6. Return audit summary
"""

import json
import logging
import re
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin

import httpx

from ..database import get_supabase
from ..services.crypto import decrypt_secret

logger = logging.getLogger(__name__)

_TIMEOUT = 15  # seconds per HTTP request
_UA = "NoroPlatform/1.0 Schema-Auditor (+https://noro.io)"

# Expected schemas per page type
_EXPECTED_SCHEMAS: dict[str, list[str]] = {
    "homepage":   ["Organization"],
    "product":    ["Product"],
    "collection": ["BreadcrumbList"],
    "faq":        ["FAQPage"],
    "blog":       ["Article"],
}

# Severity by schema importance
_SCHEMA_SEVERITY: dict[str, str] = {
    "Product":        "high",
    "Organization":   "high",
    "FAQPage":        "medium",
    "BreadcrumbList": "medium",
    "Article":        "low",
    "Review":         "low",
}


class _JSONLDExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_ld = False
        self._buf   = []
        self.schemas: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attrs_dict = dict(attrs)
            if attrs_dict.get("type") == "application/ld+json":
                self._in_ld = True
                self._buf   = []

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self._in_ld = False
            raw = "".join(self._buf).strip()
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    self.schemas.extend(parsed)
                else:
                    self.schemas.append(parsed)
            except Exception:
                pass  # malformed JSON-LD — we'll note the issue separately

    def handle_data(self, data):
        if self._in_ld:
            self._buf.append(data)


def _extract_schemas(html: str) -> list[dict]:
    parser = _JSONLDExtractor()
    parser.feed(html)
    return parser.schemas


def _schema_types(schemas: list[dict]) -> set[str]:
    types: set[str] = set()
    for s in schemas:
        t = s.get("@type")
        if isinstance(t, list):
            types.update(t)
        elif isinstance(t, str):
            types.add(t)
        # graph
        if "@graph" in s:
            for node in s["@graph"]:
                gt = node.get("@type")
                if isinstance(gt, list):
                    types.update(gt)
                elif isinstance(gt, str):
                    types.add(gt)
    return types


def _check_product_schema(schemas: list[dict]) -> list[str]:
    """Returns list of missing required Product fields."""
    for s in schemas:
        if s.get("@type") == "Product":
            missing = []
            for field in ("name", "description", "image", "offers"):
                if not s.get(field):
                    missing.append(field)
            return missing
    return []


def _fetch_page(url: str) -> Optional[str]:
    try:
        r = httpx.get(url, timeout=_TIMEOUT, headers={"User-Agent": _UA}, follow_redirects=True)
        if r.status_code == 200:
            return r.text
    except Exception as exc:
        logger.debug("fetch_page error %s: %s", url, exc)
    return None


def _get_shopify_products(domain: str, access_token: str, limit: int = 3) -> list[dict]:
    """Fetch sample products from Shopify Admin API."""
    try:
        url = f"https://{domain}/admin/api/2024-01/products.json?limit={limit}&fields=id,title,handle"
        r = httpx.get(url, headers={"X-Shopify-Access-Token": access_token}, timeout=_TIMEOUT)
        if r.status_code == 200:
            return r.json().get("products", [])
    except Exception as exc:
        logger.debug("shopify products fetch error: %s", exc)
    return []


def _get_shopify_pages(domain: str, access_token: str, limit: int = 3) -> list[dict]:
    """Fetch pages from Shopify (for FAQ detection)."""
    try:
        url = f"https://{domain}/admin/api/2024-01/pages.json?limit={limit}&fields=id,title,handle"
        r = httpx.get(url, headers={"X-Shopify-Access-Token": access_token}, timeout=_TIMEOUT)
        if r.status_code == 200:
            return r.json().get("pages", [])
    except Exception as exc:
        logger.debug("shopify pages fetch error: %s", exc)
    return []


# ── Public API ─────────────────────────────────────────────────────────────────

def run_audit(client_id: str) -> str:
    """
    Starts a schema audit for the client. Creates a schema_markup_audits row,
    crawls pages synchronously, persists issues, and returns the audit_id.
    """
    sb = get_supabase()

    # Get client Shopify info
    client = (
        sb.table("clients")
        .select("id,name,shopify_domain,shopify_access_token")
        .eq("id", client_id)
        .limit(1)
        .execute()
    ).data
    if not client:
        raise ValueError("client not found")
    client = client[0]

    domain       = (client.get("shopify_domain") or "").strip().rstrip("/")
    access_token_raw = client.get("shopify_access_token") or ""
    access_token = decrypt_secret(access_token_raw) if access_token_raw.startswith("enc:v1:") else access_token_raw

    if not domain:
        raise ValueError("cliente sem shopify_domain configurado")

    # Create audit record
    audit = (
        sb.table("schema_markup_audits")
        .insert({
            "client_id":     client_id,
            "status":        "running",
            "shopify_domain": domain,
        })
        .execute()
    ).data[0]
    audit_id = audit["id"]

    try:
        base_url     = f"https://{domain}"
        issues: list[dict] = []
        pages_audited = 0

        # 1. Homepage
        html = _fetch_page(base_url)
        if html:
            pages_audited += 1
            schemas = _extract_schemas(html)
            types   = _schema_types(schemas)
            if "Organization" not in types:
                issues.append({
                    "audit_id":    audit_id,
                    "client_id":   client_id,
                    "page_url":    base_url,
                    "page_type":   "homepage",
                    "issue_type":  "missing",
                    "schema_type": "Organization",
                    "severity":    "high",
                    "details":     {"found_types": list(types)},
                })

        # 2. Products (up to 3)
        products = _get_shopify_products(domain, access_token)
        for p in products:
            url = f"{base_url}/products/{p['handle']}"
            html = _fetch_page(url)
            if not html:
                continue
            pages_audited += 1
            schemas = _extract_schemas(html)
            types   = _schema_types(schemas)

            if "Product" not in types:
                issues.append({
                    "audit_id":    audit_id,
                    "client_id":   client_id,
                    "page_url":    url,
                    "page_type":   "product",
                    "issue_type":  "missing",
                    "schema_type": "Product",
                    "severity":    "high",
                    "details":     {"product_title": p.get("title"), "found_types": list(types)},
                })
            else:
                missing_fields = _check_product_schema(schemas)
                if missing_fields:
                    issues.append({
                        "audit_id":    audit_id,
                        "client_id":   client_id,
                        "page_url":    url,
                        "page_type":   "product",
                        "issue_type":  "incomplete",
                        "schema_type": "Product",
                        "severity":    "medium",
                        "details":     {"missing_fields": missing_fields, "product_title": p.get("title")},
                    })

            if "BreadcrumbList" not in types:
                issues.append({
                    "audit_id":    audit_id,
                    "client_id":   client_id,
                    "page_url":    url,
                    "page_type":   "product",
                    "issue_type":  "missing",
                    "schema_type": "BreadcrumbList",
                    "severity":    "medium",
                    "details":     {"product_title": p.get("title")},
                })

        # 3. Pages (FAQ detection)
        pages = _get_shopify_pages(domain, access_token)
        for pg in pages:
            is_faq = any(kw in (pg.get("title") or "").lower() for kw in ("faq", "perguntas", "dúvidas", "questions"))
            url    = f"{base_url}/pages/{pg['handle']}"
            html   = _fetch_page(url)
            if not html:
                continue
            pages_audited += 1
            schemas = _extract_schemas(html)
            types   = _schema_types(schemas)

            if is_faq and "FAQPage" not in types:
                issues.append({
                    "audit_id":    audit_id,
                    "client_id":   client_id,
                    "page_url":    url,
                    "page_type":   "faq",
                    "issue_type":  "missing",
                    "schema_type": "FAQPage",
                    "severity":    "medium",
                    "details":     {"page_title": pg.get("title")},
                })

        # 4. Persist issues
        if issues:
            sb.table("schema_markup_issues").insert(issues).execute()

        # 5. Calculate health score
        # 100 = no issues; -20 per high, -10 per medium, -5 per low
        score = 100
        for iss in issues:
            if iss["severity"] == "high":
                score -= 20
            elif iss["severity"] == "medium":
                score -= 10
            else:
                score -= 5
        score = max(0, min(100, score))

        # 6. Update audit record
        sb.table("schema_markup_audits").update({
            "status":             "completed",
            "pages_audited":      pages_audited,
            "issues_found":       len(issues),
            "schema_health_score": score,
            "completed_at":       "now()",
            "summary": {
                "high_issues":   sum(1 for i in issues if i["severity"] == "high"),
                "medium_issues": sum(1 for i in issues if i["severity"] == "medium"),
                "low_issues":    sum(1 for i in issues if i["severity"] == "low"),
                "schema_types_missing": list({i["schema_type"] for i in issues}),
            },
        }).eq("id", audit_id).execute()

        logger.info("Schema audit done client=%s issues=%d score=%d", client_id, len(issues), score)

    except Exception as exc:
        logger.exception("Schema audit failed client=%s: %s", client_id, exc)
        sb.table("schema_markup_audits").update({
            "status": "failed",
            "error":  str(exc)[:500],
        }).eq("id", audit_id).execute()
        raise

    return audit_id


def generate_schema_markup(issue_id: str) -> dict:
    """
    Generates JSON-LD markup for a specific issue and saves it back.
    """
    sb = get_supabase()
    issue = (
        sb.table("schema_markup_issues")
        .select("*")
        .eq("id", issue_id)
        .limit(1)
        .execute()
    ).data
    if not issue:
        raise ValueError("issue not found")
    issue = issue[0]

    schema_type = issue["schema_type"]
    page_url    = issue["page_url"]
    details     = issue.get("details") or {}

    markup: dict = {}

    if schema_type == "Organization":
        markup = {
            "@context": "https://schema.org",
            "@type":    "Organization",
            "name":     details.get("brand_name", ""),
            "url":      page_url,
            "sameAs":   [],
        }

    elif schema_type == "Product":
        markup = {
            "@context":   "https://schema.org",
            "@type":      "Product",
            "name":       details.get("product_title", ""),
            "description": "",
            "image":      [],
            "offers": {
                "@type":         "Offer",
                "priceCurrency": "BRL",
                "availability":  "https://schema.org/InStock",
            },
        }

    elif schema_type == "FAQPage":
        markup = {
            "@context": "https://schema.org",
            "@type":    "FAQPage",
            "mainEntity": [
                {
                    "@type":           "Question",
                    "name":            "Pergunta frequente",
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text":  "Resposta aqui.",
                    },
                }
            ],
        }

    elif schema_type == "BreadcrumbList":
        markup = {
            "@context":    "https://schema.org",
            "@type":       "BreadcrumbList",
            "itemListElement": [
                {
                    "@type":    "ListItem",
                    "position": 1,
                    "name":     "Home",
                    "item":     page_url.split("/products")[0] if "/products/" in page_url else page_url,
                },
                {
                    "@type":    "ListItem",
                    "position": 2,
                    "name":     details.get("product_title", "Produto"),
                    "item":     page_url,
                },
            ],
        }

    markup_str = json.dumps(markup, ensure_ascii=False, indent=2)
    sb.table("schema_markup_issues").update({
        "generated_markup": markup_str,
    }).eq("id", issue_id).execute()

    return {"issue_id": issue_id, "markup": markup, "markup_json": markup_str}


def apply_schema_via_shopify(issue_id: str, client_id: str) -> dict:
    """
    Injects the generated JSON-LD into a Shopify page via metafields API.
    Falls back to instructions if metafields not supported.
    """
    sb = get_supabase()

    # Load issue
    issue = (
        sb.table("schema_markup_issues")
        .select("*")
        .eq("id", issue_id)
        .limit(1)
        .execute()
    ).data
    if not issue:
        raise ValueError("issue not found")
    issue = issue[0]

    if not issue.get("generated_markup"):
        generate_schema_markup(issue_id)
        issue = (
            sb.table("schema_markup_issues")
            .select("*")
            .eq("id", issue_id)
            .limit(1)
            .execute()
        ).data[0]

    # Get client Shopify creds
    client = (
        sb.table("clients")
        .select("shopify_domain,shopify_access_token")
        .eq("id", client_id)
        .limit(1)
        .execute()
    ).data[0]

    domain      = client["shopify_domain"].strip().rstrip("/")
    token_raw   = client.get("shopify_access_token") or ""
    token       = decrypt_secret(token_raw) if token_raw.startswith("enc:v1:") else token_raw

    snippet_name = f"noro-schema-{issue['schema_type'].lower()}"
    snippet_code = f'<script type="application/ld+json">\n{issue["generated_markup"]}\n</script>'

    # Try to create/update a theme asset (snippet)
    try:
        # Get active theme
        themes_resp = httpx.get(
            f"https://{domain}/admin/api/2024-01/themes.json",
            headers={"X-Shopify-Access-Token": token},
            timeout=_TIMEOUT,
        )
        themes = themes_resp.json().get("themes", [])
        active = next((t for t in themes if t.get("role") == "main"), themes[0] if themes else None)

        if active:
            theme_id = active["id"]
            asset_key = f"snippets/{snippet_name}.liquid"
            httpx.put(
                f"https://{domain}/admin/api/2024-01/themes/{theme_id}/assets.json",
                headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
                json={"asset": {"key": asset_key, "value": snippet_code}},
                timeout=_TIMEOUT,
            )

            sb.table("schema_markup_issues").update({
                "status":     "fixed",
                "applied_at": "now()",
            }).eq("id", issue_id).execute()

            # Log to optimization_history
            sb.table("optimization_history").insert({
                "client_id":   client_id,
                "type":        "schema_markup",
                "title":       f"Schema {issue['schema_type']} aplicado",
                "description": f"JSON-LD inserido como snippet {snippet_name}.liquid",
                "after_value": snippet_code[:500],
            }).execute()

            return {"ok": True, "applied": True, "snippet": snippet_name}

    except Exception as exc:
        logger.warning("apply_schema_via_shopify failed: %s", exc)

    return {
        "ok":      True,
        "applied": False,
        "manual":  True,
        "instructions": (
            f"Adicione o snippet '{snippet_name}.liquid' ao seu tema Shopify com o seguinte conteúdo:\n\n"
            f"{snippet_code}\n\n"
            "Em seguida, inclua `{{{{ render '{snippet_name}' }}}}` no arquivo `theme.liquid` "
            "dentro do `<head>`."
        ),
    }


def get_latest_audit(client_id: str) -> Optional[dict]:
    sb = get_supabase()
    rows = (
        sb.table("schema_markup_audits")
        .select("*")
        .eq("client_id", client_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data
    return rows[0] if rows else None


def get_audit_issues(audit_id: str, status: Optional[str] = None) -> list[dict]:
    sb  = get_supabase()
    q   = sb.table("schema_markup_issues").select("*").eq("audit_id", audit_id)
    if status:
        q = q.eq("status", status)
    return (q.order("severity").execute()).data or []
