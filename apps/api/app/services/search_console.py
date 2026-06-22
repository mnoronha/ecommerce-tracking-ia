"""
Google Search Console API — consultas de busca, páginas e visibilidade de marca.

Requer:
  - google_ads_refresh_token com scope webmasters.readonly
  - search_console_site_url configurado no cliente
    (ex: "sc-domain:lksneakers.com.br" ou "https://www.lksneakers.com.br/")

Se o refresh_token não tiver webmasters.readonly, retorna error="scope_missing"
para que o frontend exiba instruções de re-autenticação.
"""

import logging
import time
from datetime import date
from typing import Optional
from urllib.parse import quote

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GSC_URL   = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site_url}/searchAnalytics/query"

_token_cache: dict = {}


def _get_token(refresh_token: str) -> Optional[str]:
    now = time.time()
    key = f"gsc:{refresh_token[:16]}"
    cached = _token_cache.get(key, {})
    if cached.get("token") and cached.get("expires_at", 0) > now + 60:
        return cached["token"]
    try:
        resp = httpx.post(
            _TOKEN_URL,
            data={
                "client_id":     settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type":    "refresh_token",
            },
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            _token_cache[key] = {
                "token":      data["access_token"],
                "expires_at": now + data.get("expires_in", 3600),
            }
            return _token_cache[key]["token"]
        logger.warning("search_console: token refresh HTTP %s", resp.status_code)
    except Exception as exc:
        logger.warning("search_console: token refresh failed: %s", exc)
    return None


def _query(site_url: str, token: str, body: dict) -> Optional[dict]:
    url = _GSC_URL.format(site_url=quote(site_url, safe=""))
    try:
        resp = httpx.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=20.0,
        )
        if resp.status_code == 403:
            return {"__error__": "scope_missing", "detail": "Token does not have webmasters.readonly scope. Re-connect Google OAuth."}
        if resp.status_code == 400:
            return {"__error__": "bad_request", "detail": resp.text[:300]}
        if resp.status_code != 200:
            return {"__error__": f"HTTP {resp.status_code}", "detail": resp.text[:200]}
        return resp.json()
    except httpx.TimeoutException:
        return {"__error__": "timeout"}
    except Exception as exc:
        return {"__error__": str(exc)}


def fetch_overview(
    site_url: str,
    refresh_token: str,
    start_date: date,
    end_date: date,
) -> dict:
    """
    Visão geral: clicks, impressões, CTR, posição — por query, página e dia.
    """
    token = _get_token(refresh_token)
    if not token:
        return {"error": "token_refresh_failed"}

    sd  = start_date.isoformat()
    ed  = end_date.isoformat()
    dr  = {"startDate": sd, "endDate": ed}

    q_data = _query(site_url, token, {**dr, "dimensions": ["query"], "rowLimit": 50})
    if q_data and "__error__" in q_data:
        return {"error": q_data["__error__"], "detail": q_data.get("detail")}

    p_data = _query(site_url, token, {**dr, "dimensions": ["page"], "rowLimit": 50})
    d_data = _query(site_url, token, {**dr, "dimensions": ["date"], "rowLimit": 500})

    queries            = []
    total_clicks       = 0
    total_impressions  = 0
    weighted_pos_sum   = 0.0

    for row in (q_data or {}).get("rows", []):
        clicks = int(row.get("clicks", 0))
        impr   = int(row.get("impressions", 0))
        ctr    = round(row.get("ctr", 0) * 100, 2)
        pos    = round(row.get("position", 0), 1)
        total_clicks      += clicks
        total_impressions += impr
        weighted_pos_sum  += pos * impr
        queries.append({
            "query":       row["keys"][0] if row.get("keys") else "",
            "clicks":      clicks,
            "impressions": impr,
            "ctr":         ctr,
            "position":    pos,
        })

    pages = []
    for row in (p_data or {}).get("rows", []):
        pages.append({
            "page":        row["keys"][0] if row.get("keys") else "",
            "clicks":      int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr":         round(row.get("ctr", 0) * 100, 2),
            "position":    round(row.get("position", 0), 1),
        })

    daily = []
    for row in (d_data or {}).get("rows", []):
        daily.append({
            "date":        row["keys"][0] if row.get("keys") else "",
            "clicks":      int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr":         round(row.get("ctr", 0) * 100, 2),
            "position":    round(row.get("position", 0), 1),
        })

    avg_ctr      = round(total_clicks / total_impressions * 100, 2) if total_impressions > 0 else None
    avg_position = round(weighted_pos_sum / total_impressions, 1) if total_impressions > 0 else None

    return {
        "summary": {
            "clicks":       total_clicks,
            "impressions":  total_impressions,
            "avg_ctr":      avg_ctr,
            "avg_position": avg_position,
        },
        "top_queries": queries,
        "top_pages":   pages,
        "daily":       daily,
        "period":      {"start": sd, "end": ed},
    }


def fetch_opportunities(
    site_url: str,
    refresh_token: str,
    start_date: date,
    end_date: date,
) -> dict:
    """
    Oportunidades de crescimento:
    - Queries com 100+ impressões e CTR < 5% (conteúdo sem clique)
    - Pages+queries em posição 4-10 (um empurrãozinho vira 1ª página)
    """
    token = _get_token(refresh_token)
    if not token:
        return {"error": "token_refresh_failed"}

    sd  = start_date.isoformat()
    ed  = end_date.isoformat()
    dr  = {"startDate": sd, "endDate": ed}

    q_data = _query(site_url, token, {**dr, "dimensions": ["query"], "rowLimit": 1000})
    if q_data and "__error__" in q_data:
        return {"error": q_data["__error__"], "detail": q_data.get("detail")}

    pq_data = _query(site_url, token, {**dr, "dimensions": ["page", "query"], "rowLimit": 1000})

    low_ctr: list = []
    for row in (q_data or {}).get("rows", []):
        impr  = int(row.get("impressions", 0))
        ctr   = row.get("ctr", 0)
        pos   = row.get("position", 0)
        if impr >= 100 and ctr < 0.05:
            low_ctr.append({
                "query":       row["keys"][0] if row.get("keys") else "",
                "impressions": impr,
                "clicks":      int(row.get("clicks", 0)),
                "ctr":         round(ctr * 100, 2),
                "position":    round(pos, 1),
            })

    upgrade: list = []
    for row in (pq_data or {}).get("rows", []):
        pos   = row.get("position", 0)
        impr  = int(row.get("impressions", 0))
        if 4 <= pos <= 10 and impr >= 50:
            keys = row.get("keys", [])
            upgrade.append({
                "page":        keys[0] if keys else "",
                "query":       keys[1] if len(keys) > 1 else "",
                "position":    round(pos, 1),
                "clicks":      int(row.get("clicks", 0)),
                "impressions": impr,
                "ctr":         round(row.get("ctr", 0) * 100, 2),
            })

    low_ctr.sort(key=lambda x: x["impressions"], reverse=True)
    upgrade.sort(key=lambda x: x["impressions"], reverse=True)

    return {
        "low_ctr_queries":    low_ctr[:30],
        "upgrade_candidates": upgrade[:30],
        "period":             {"start": sd, "end": ed},
    }
