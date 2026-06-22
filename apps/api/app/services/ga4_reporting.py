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


_AI_REFERRER_DOMAINS = [
    "chatgpt.com", "chat.openai.com", "perplexity.ai",
    "gemini.google.com", "claude.ai", "copilot.microsoft.com",
    "bard.google.com", "you.com", "phind.com",
]


def fetch_conversion_funnel(
    property_id: str,
    refresh_token: str,
    start_date: date,
    end_date: date,
) -> dict:
    """
    Funil de conversão por canal: sessions → add_to_cart → checkout → purchase.
    """
    token = _get_token(refresh_token)
    if not token:
        return {"error": "token_refresh_failed"}

    url     = _GA4_API_URL.format(property_id=property_id)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    dr      = [{"startDate": start_date.isoformat(), "endDate": end_date.isoformat()}]

    try:
        r1 = httpx.post(url, headers=headers, timeout=15.0, json={
            "dateRanges": dr,
            "dimensions": [{"name": "sessionDefaultChannelGrouping"}],
            "metrics":    [{"name": "sessions"}],
            "limit": 20,
        })
        r2 = httpx.post(url, headers=headers, timeout=15.0, json={
            "dateRanges": dr,
            "dimensions": [{"name": "sessionDefaultChannelGrouping"}, {"name": "eventName"}],
            "metrics":    [{"name": "eventCount"}],
            "dimensionFilter": {
                "filter": {
                    "fieldName": "eventName",
                    "inListFilter": {"values": ["add_to_cart", "begin_checkout", "purchase"]},
                }
            },
            "limit": 500,
        })

        if r1.status_code != 200 or r2.status_code != 200:
            return {"error": f"GA4 HTTP {r1.status_code}/{r2.status_code}"}

        sessions_by_ch: dict[str, int] = {}
        for row in r1.json().get("rows", []):
            ch = row["dimensionValues"][0]["value"]
            sessions_by_ch[ch] = int(row["metricValues"][0]["value"])

        events_by_ch: dict[str, dict[str, int]] = {}
        totals = {"add_to_cart": 0, "begin_checkout": 0, "purchase": 0}
        for row in r2.json().get("rows", []):
            ch  = row["dimensionValues"][0]["value"]
            ev  = row["dimensionValues"][1]["value"]
            cnt = int(row["metricValues"][0]["value"])
            events_by_ch.setdefault(ch, {})[ev] = cnt
            if ev in totals:
                totals[ev] += cnt

        all_channels = sorted(
            set(sessions_by_ch) | set(events_by_ch),
            key=lambda c: sessions_by_ch.get(c, 0), reverse=True,
        )
        total_sessions = sum(sessions_by_ch.values())

        by_channel = []
        for ch in all_channels:
            sess = sessions_by_ch.get(ch, 0)
            ev   = events_by_ch.get(ch, {})
            atc  = ev.get("add_to_cart", 0)
            bc   = ev.get("begin_checkout", 0)
            pur  = ev.get("purchase", 0)
            by_channel.append({
                "channel":        ch,
                "sessions":       sess,
                "add_to_cart":    atc,
                "begin_checkout": bc,
                "purchases":      pur,
                "atc_rate":      round(atc / sess * 100, 1) if sess > 0 else None,
                "checkout_rate": round(bc  / sess * 100, 1) if sess > 0 else None,
                "purchase_rate": round(pur / sess * 100, 1) if sess > 0 else None,
            })

        return {
            "summary": {
                "sessions":       total_sessions,
                "add_to_cart":    totals["add_to_cart"],
                "begin_checkout": totals["begin_checkout"],
                "purchases":      totals["purchase"],
                "atc_rate":      round(totals["add_to_cart"]    / total_sessions * 100, 1) if total_sessions > 0 else None,
                "checkout_rate": round(totals["begin_checkout"] / total_sessions * 100, 1) if total_sessions > 0 else None,
                "purchase_rate": round(totals["purchase"]       / total_sessions * 100, 1) if total_sessions > 0 else None,
            },
            "by_channel": by_channel,
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        }
    except Exception as exc:
        logger.error("ga4_reporting: fetch_conversion_funnel: %s", exc)
        return {"error": str(exc)}


def fetch_ai_traffic(
    property_id: str,
    refresh_token: str,
    start_date: date,
    end_date: date,
) -> dict:
    """
    Sessões vindas de ferramentas de IA (ChatGPT, Perplexity, Gemini etc.).
    """
    token = _get_token(refresh_token)
    if not token:
        return {"error": "token_refresh_failed"}

    url     = _GA4_API_URL.format(property_id=property_id)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    dr      = [{"startDate": start_date.isoformat(), "endDate": end_date.isoformat()}]

    try:
        r_ai = httpx.post(url, headers=headers, timeout=15.0, json={
            "dateRanges": dr,
            "dimensions": [{"name": "sessionSource"}, {"name": "sessionMedium"}],
            "metrics":    [
                {"name": "sessions"}, {"name": "activeUsers"},
                {"name": "conversions"}, {"name": "purchaseRevenue"},
            ],
            "dimensionFilter": {
                "filter": {
                    "fieldName": "sessionSource",
                    "inListFilter": {"values": _AI_REFERRER_DOMAINS},
                }
            },
            "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
            "limit": 100,
        })
        r_total = httpx.post(url, headers=headers, timeout=10.0, json={
            "dateRanges": dr,
            "metrics":    [{"name": "sessions"}],
        })

        if r_ai.status_code != 200:
            return {"error": f"GA4 HTTP {r_ai.status_code}", "detail": r_ai.text[:200]}

        rows       = r_ai.json().get("rows", [])
        total_sess = total_users = total_conv = 0
        total_rev  = 0.0
        by_source  = []

        for row in rows:
            dims   = row["dimensionValues"]
            vals   = row["metricValues"]
            source = dims[0]["value"]
            medium = dims[1]["value"]
            sess   = int(vals[0]["value"])
            users  = int(vals[1]["value"])
            conv   = float(vals[2]["value"])
            rev    = float(vals[3]["value"])
            total_sess  += sess
            total_users += users
            total_conv  += conv
            total_rev   += rev
            by_source.append({
                "source":      source,
                "medium":      medium,
                "sessions":    sess,
                "users":       users,
                "conversions": round(conv),
                "revenue":     round(rev, 2),
            })

        site_sessions = 0
        if r_total.status_code == 200:
            total_rows = r_total.json().get("rows", [])
            if total_rows:
                site_sessions = int(total_rows[0]["metricValues"][0]["value"])

        return {
            "summary": {
                "sessions":       total_sess,
                "users":          total_users,
                "conversions":    round(total_conv),
                "revenue":        round(total_rev, 2),
                "share_of_total": round(total_sess / site_sessions * 100, 2) if site_sessions > 0 else None,
            },
            "by_source":            by_source,
            "ai_domains_monitored": _AI_REFERRER_DOMAINS,
            "period":               {"start": start_date.isoformat(), "end": end_date.isoformat()},
        }
    except Exception as exc:
        logger.error("ga4_reporting: fetch_ai_traffic: %s", exc)
        return {"error": str(exc)}


def fetch_top_product_pages(
    property_id: str,
    refresh_token: str,
    start_date: date,
    end_date: date,
    limit: int = 25,
) -> dict:
    """
    Top páginas de produto por sessões e conversões (filtra paths com /products/, /produto/ etc.).
    """
    token = _get_token(refresh_token)
    if not token:
        return {"error": "token_refresh_failed"}

    url     = _GA4_API_URL.format(property_id=property_id)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    dr      = [{"startDate": start_date.isoformat(), "endDate": end_date.isoformat()}]

    try:
        resp = httpx.post(url, headers=headers, timeout=15.0, json={
            "dateRanges": dr,
            "dimensions": [{"name": "pagePath"}, {"name": "pageTitle"}],
            "metrics":    [
                {"name": "sessions"},
                {"name": "screenPageViews"},
                {"name": "conversions"},
                {"name": "purchaseRevenue"},
                {"name": "bounceRate"},
            ],
            "dimensionFilter": {
                "orGroup": {
                    "expressions": [
                        {"filter": {"fieldName": "pagePath", "stringFilter": {"matchType": "CONTAINS", "value": "/products/"}}},
                        {"filter": {"fieldName": "pagePath", "stringFilter": {"matchType": "CONTAINS", "value": "/produto/"}}},
                        {"filter": {"fieldName": "pagePath", "stringFilter": {"matchType": "CONTAINS", "value": "/item/"}}},
                    ]
                }
            },
            "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
            "limit": limit,
        })

        if resp.status_code != 200:
            return {"error": f"GA4 HTTP {resp.status_code}", "detail": resp.text[:200]}

        pages = []
        for row in resp.json().get("rows", []):
            dims      = row["dimensionValues"]
            vals      = row["metricValues"]
            path      = dims[0]["value"]
            title     = dims[1]["value"]
            sessions  = int(vals[0]["value"])
            pageviews = int(vals[1]["value"])
            conv      = float(vals[2]["value"])
            rev       = float(vals[3]["value"])
            bounce    = float(vals[4]["value"])
            pages.append({
                "path":        path,
                "title":       title,
                "sessions":    sessions,
                "pageviews":   pageviews,
                "conversions": round(conv),
                "revenue":     round(rev, 2),
                "bounce_rate": round(bounce * 100, 1),
                "conv_rate":   round(conv / sessions * 100, 2) if sessions > 0 else None,
            })

        return {
            "pages":  pages,
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        }
    except Exception as exc:
        logger.error("ga4_reporting: fetch_top_product_pages: %s", exc)
        return {"error": str(exc)}


def fetch_new_vs_returning(
    property_id: str,
    refresh_token: str,
    start_date: date,
    end_date: date,
) -> dict:
    """
    Novos vs recorrentes: usuários, conversões, ticket médio e série diária.
    """
    token = _get_token(refresh_token)
    if not token:
        return {"error": "token_refresh_failed"}

    url     = _GA4_API_URL.format(property_id=property_id)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    dr      = [{"startDate": start_date.isoformat(), "endDate": end_date.isoformat()}]

    try:
        r1 = httpx.post(url, headers=headers, timeout=15.0, json={
            "dateRanges": dr,
            "dimensions": [{"name": "newVsReturning"}],
            "metrics":    [
                {"name": "activeUsers"},
                {"name": "sessions"},
                {"name": "conversions"},
                {"name": "purchaseRevenue"},
                {"name": "engagementRate"},
            ],
            "limit": 10,
        })
        r2 = httpx.post(url, headers=headers, timeout=15.0, json={
            "dateRanges": dr,
            "dimensions": [{"name": "date"}, {"name": "newVsReturning"}],
            "metrics":    [{"name": "activeUsers"}, {"name": "conversions"}],
            "orderBys":   [{"dimension": {"dimensionName": "date"}}],
            "limit": 500,
        })

        if r1.status_code != 200:
            return {"error": f"GA4 HTTP {r1.status_code}"}

        cohorts     = []
        total_users = 0
        for row in r1.json().get("rows", []):
            dims     = row["dimensionValues"]
            vals     = row["metricValues"]
            label    = dims[0]["value"]
            users    = int(vals[0]["value"])
            sessions = int(vals[1]["value"])
            conv     = float(vals[2]["value"])
            rev      = float(vals[3]["value"])
            eng      = float(vals[4]["value"])
            total_users += users
            cohorts.append({
                "cohort":           label,
                "users":            users,
                "sessions":         sessions,
                "conversions":      round(conv),
                "revenue":          round(rev, 2),
                "engagement_rate":  round(eng * 100, 1),
                "conv_rate":        round(conv / sessions * 100, 2) if sessions > 0 else None,
                "avg_ticket":       round(rev / conv, 2) if conv > 0 else None,
                "revenue_per_user": round(rev / users, 2) if users > 0 else None,
            })
        for c in cohorts:
            c["user_share"] = round(c["users"] / total_users * 100, 1) if total_users > 0 else None

        daily_series: list = []
        if r2.status_code == 200:
            daily_map: dict = {}
            for row in r2.json().get("rows", []):
                d     = row["dimensionValues"][0]["value"]
                label = row["dimensionValues"][1]["value"]
                users = int(row["metricValues"][0]["value"])
                conv  = float(row["metricValues"][1]["value"])
                ds    = f"{d[:4]}-{d[4:6]}-{d[6:]}"
                if ds not in daily_map:
                    daily_map[ds] = {"date": ds, "new": 0, "returning": 0, "new_conv": 0.0, "returning_conv": 0.0}
                daily_map[ds][label]           = users
                daily_map[ds][f"{label}_conv"] = round(conv)
            daily_series = sorted(daily_map.values(), key=lambda x: x["date"])

        return {
            "cohorts":      cohorts,
            "daily_series": daily_series,
            "period":       {"start": start_date.isoformat(), "end": end_date.isoformat()},
        }
    except Exception as exc:
        logger.error("ga4_reporting: fetch_new_vs_returning: %s", exc)
        return {"error": str(exc)}
