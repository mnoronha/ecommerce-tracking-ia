"""
GA4 Data API — leitura de métricas de sessões/conversões para o dashboard.

Usado para clientes sem tracking ativo (Colab55, Enutri etc.) onde o GA4
é a única fonte de dados de comportamento e conversão.

Requer:
  - google_ads_refresh_token com scope analytics.readonly
  - ga4_property_id (ID numérico da propriedade, ex: 267533911)
"""

import logging
import time
from datetime import date, timedelta
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_TOKEN_URL   = "https://oauth2.googleapis.com/token"
_GA4_API_URL = "https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"

_token_cache: dict = {}


def _get_token(refresh_token: str) -> Optional[str]:
    now = time.time()
    key = refresh_token[:16]
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
        logger.warning("ga4_reporting: token refresh HTTP %s", resp.status_code)
    except Exception as exc:
        logger.warning("ga4_reporting: token refresh failed: %s", exc)
    return None


def fetch_overview(
    property_id: str,
    refresh_token: str,
    start_date: date,
    end_date: date,
) -> dict:
    """
    Busca métricas de visão geral: sessões, usuários, conversões, receita.
    Retorna dict com 'summary' e 'by_channel'.
    """
    token = _get_token(refresh_token)
    if not token:
        return {"error": "token_refresh_failed"}

    url = _GA4_API_URL.format(property_id=property_id)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # ── Relatório por canal ────────────────────────────────────────────────────
    body = {
        "dateRanges": [{"startDate": start_date.isoformat(), "endDate": end_date.isoformat()}],
        "dimensions": [{"name": "sessionDefaultChannelGrouping"}],
        "metrics": [
            {"name": "sessions"},
            {"name": "activeUsers"},
            {"name": "conversions"},
            {"name": "purchaseRevenue"},
        ],
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
        "limit": 20,
    }

    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=15.0)
        if resp.status_code != 200:
            logger.warning("ga4_reporting: runReport HTTP %s — %s", resp.status_code, resp.text[:200])
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text[:200]}

        data = resp.json()
        rows = data.get("rows", [])

        by_channel = []
        total_sessions = 0
        total_users    = 0
        total_conversions = 0
        total_revenue  = 0.0

        for row in rows:
            dims = row.get("dimensionValues", [])
            vals = row.get("metricValues", [])
            channel     = dims[0]["value"] if dims else "Unknown"
            sessions    = int(vals[0]["value"])   if len(vals) > 0 else 0
            users       = int(vals[1]["value"])   if len(vals) > 1 else 0
            conversions = float(vals[2]["value"]) if len(vals) > 2 else 0.0
            revenue     = float(vals[3]["value"]) if len(vals) > 3 else 0.0

            total_sessions    += sessions
            total_users       += users
            total_conversions += conversions
            total_revenue     += revenue

            by_channel.append({
                "channel":     channel,
                "sessions":    sessions,
                "users":       users,
                "conversions": round(conversions),
                "revenue":     round(revenue, 2),
            })

    except Exception as exc:
        logger.error("ga4_reporting: fetch_overview exception: %s", exc)
        return {"error": str(exc)}

    # ── Série temporal (diária) ────────────────────────────────────────────────
    body_daily = {
        "dateRanges": [{"startDate": start_date.isoformat(), "endDate": end_date.isoformat()}],
        "dimensions": [{"name": "date"}],
        "metrics":    [{"name": "sessions"}, {"name": "activeUsers"}, {"name": "conversions"}],
        "orderBys":   [{"dimension": {"dimensionName": "date"}}],
    }
    daily_series = []
    try:
        resp2 = httpx.post(url, json=body_daily, headers=headers, timeout=15.0)
        if resp2.status_code == 200:
            for row in resp2.json().get("rows", []):
                d    = row["dimensionValues"][0]["value"]  # YYYYMMDD
                vals = row.get("metricValues", [])
                daily_series.append({
                    "date":        f"{d[:4]}-{d[4:6]}-{d[6:]}",
                    "sessions":    int(vals[0]["value"])   if len(vals) > 0 else 0,
                    "users":       int(vals[1]["value"])   if len(vals) > 1 else 0,
                    "conversions": float(vals[2]["value"]) if len(vals) > 2 else 0,
                })
    except Exception as exc:
        logger.warning("ga4_reporting: daily series failed: %s", exc)

    return {
        "summary": {
            "sessions":    total_sessions,
            "users":       total_users,
            "conversions": round(total_conversions),
            "revenue":     round(total_revenue, 2),
        },
        "by_channel":   by_channel,
        "daily_series": daily_series,
        "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
    }
