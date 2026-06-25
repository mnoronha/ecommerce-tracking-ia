"""
Core Web Vitals — integrates with Google PageSpeed Insights API.
Checks LCP, FID/INP, CLS, TTFB for mobile + desktop.
"""

import logging
from typing import Optional

import httpx

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)
_PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
_TIMEOUT = 30


def _score_category(score: Optional[float]) -> str:
    if score is None:
        return "unknown"
    if score >= 0.9:
        return "good"
    if score >= 0.5:
        return "needs_improvement"
    return "poor"


def check_page(url: str, strategy: str = "mobile") -> dict:
    """
    Calls PageSpeed Insights API for a single URL.
    strategy: 'mobile' | 'desktop'
    """
    api_key = getattr(settings, "PAGESPEED_API_KEY", "") or ""
    params: dict = {"url": url, "strategy": strategy}
    if api_key:
        params["key"] = api_key

    try:
        r = httpx.get(_PSI_URL, params=params, timeout=_TIMEOUT)
        if r.status_code != 200:
            return {"error": f"PageSpeed API returned {r.status_code}", "url": url}
        data = r.json()
    except Exception as exc:
        return {"error": str(exc)[:200], "url": url}

    cats  = data.get("lighthouseResult", {}).get("categories", {})
    audits = data.get("lighthouseResult", {}).get("audits", {})

    perf_score = cats.get("performance", {}).get("score")
    seo_score  = cats.get("seo", {}).get("score")

    def audit_val(key: str) -> Optional[float]:
        a = audits.get(key, {})
        return a.get("numericValue")

    def audit_display(key: str) -> Optional[str]:
        return audits.get(key, {}).get("displayValue")

    metrics = {
        "lcp":  {"value": audit_val("largest-contentful-paint"),  "display": audit_display("largest-contentful-paint")},
        "cls":  {"value": audit_val("cumulative-layout-shift"),   "display": audit_display("cumulative-layout-shift")},
        "fid":  {"value": audit_val("max-potential-fid"),         "display": audit_display("max-potential-fid")},
        "ttfb": {"value": audit_val("server-response-time"),      "display": audit_display("server-response-time")},
        "fcp":  {"value": audit_val("first-contentful-paint"),    "display": audit_display("first-contentful-paint")},
        "tbt":  {"value": audit_val("total-blocking-time"),       "display": audit_display("total-blocking-time")},
        "speed_index": {"value": audit_val("speed-index"),        "display": audit_display("speed-index")},
    }

    return {
        "url":           url,
        "strategy":      strategy,
        "performance":   perf_score,
        "seo":           seo_score,
        "perf_category": _score_category(perf_score),
        "metrics":       metrics,
        "opportunities": [
            {
                "id":          a["id"],
                "title":       a.get("title", ""),
                "description": a.get("description", "")[:200],
                "savings_ms":  a.get("details", {}).get("overallSavingsMs"),
            }
            for a in audits.values()
            if a.get("score") is not None and a.get("score", 1) < 0.9
            and a.get("details", {}).get("type") == "opportunity"
        ][:5],
    }


def check_client(client_id: str) -> dict:
    """
    Runs PageSpeed check for the client's homepage (mobile + desktop)
    and persists to technical_seo_checks.
    """
    sb = get_supabase()
    client = (
        sb.table("clients")
        .select("id,shopify_domain,website_url")
        .eq("id", client_id)
        .limit(1)
        .execute()
    ).data
    if not client:
        raise ValueError("client not found")
    client = client[0]

    domain = (client.get("shopify_domain") or "").strip().rstrip("/")
    url    = client.get("website_url") or (f"https://{domain}" if domain else "")
    if not url:
        raise ValueError("cliente sem URL configurada")

    mobile  = check_page(url, "mobile")
    desktop = check_page(url, "desktop")

    mob_score  = mobile.get("performance")
    desk_score = desktop.get("performance")

    # Determine status
    worst = min((mob_score or 1), (desk_score or 1))
    status = _score_category(worst)
    if status == "good":
        seo_status = "ok"
    elif status == "needs_improvement":
        seo_status = "warning"
    else:
        seo_status = "error"

    result = {
        "url":     url,
        "mobile":  mobile,
        "desktop": desktop,
        "status":  seo_status,
    }

    sb.table("technical_seo_checks").upsert({
        "client_id":  client_id,
        "check_type": "core_web_vitals",
        "status":     seo_status,
        "data":       result,
        "checked_at": "now()",
    }, on_conflict="client_id,check_type").execute()

    # Create optimization if poor performance
    if worst is not None and worst < 0.5:
        existing = (
            sb.table("technical_optimizations")
            .select("id")
            .eq("client_id", client_id)
            .eq("type", "core_web_vitals")
            .eq("status", "pending")
            .limit(1)
            .execute()
        ).data
        if not existing:
            sb.table("technical_optimizations").insert({
                "client_id":        client_id,
                "type":             "core_web_vitals",
                "title":            f"Performance da página precisa de atenção (mobile: {int((mob_score or 0)*100)}/100)",
                "description":      "Páginas lentas prejudicam SEO, conversão e visibilidade em IA.",
                "severity":         "high" if worst < 0.3 else "medium",
                "estimated_impact": "Alto — LCP < 2.5s melhora posicionamento e UX",
                "estimated_time":   "2-8h dependendo das melhorias",
                "action_data":      {"mobile_score": mob_score, "desktop_score": desk_score,
                                     "opportunities": mobile.get("opportunities", [])},
            }).execute()

    return result
