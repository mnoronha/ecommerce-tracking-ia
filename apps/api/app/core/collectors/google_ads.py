"""
Google Ads Core Collector.

Reuses the token infrastructure from services/spend_sync (unchanged).
Issues a single GAQL query for the full date range instead of day-by-day.
Preserves legacy service — does not modify it.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

import httpx

from ...config import settings
from ...core.dqg import CollectionResult
from ...services.spend_sync import _get_google_token

logger = logging.getLogger(__name__)

_GOOGLE_ADS_API = "https://googleads.googleapis.com/v23"


def _fetch_google_range(
    customer_id: str,
    refresh_token: str,
    period_start: date,
    period_end: date,
    manager_id: Optional[str] = None,
) -> Optional[dict]:
    """
    Single GAQL query for the full period.
    Returns aggregated {spend, conversions, conversion_value} or None on failure.
    """
    if not all([
        settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
        settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
    ]):
        return None

    token = _get_google_token(refresh_token)
    if not token:
        return None

    clean_cid = customer_id.replace("-", "").replace(" ", "")
    query = (
        "SELECT metrics.cost_micros, metrics.impressions, metrics.clicks, "
        "metrics.conversions, metrics.conversions_value "
        "FROM customer "
        f"WHERE segments.date BETWEEN '{period_start.isoformat()}' AND '{period_end.isoformat()}'"
    )

    headers: dict = {
        "Authorization":   f"Bearer {token}",
        "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        "Content-Type":    "application/json",
    }
    if manager_id:
        headers["login-customer-id"] = manager_id.replace("-", "").replace(" ", "")

    url = f"{_GOOGLE_ADS_API}/customers/{clean_cid}/googleAds:search"
    try:
        resp = httpx.post(url, json={"query": query}, headers=headers, timeout=30.0)
        if resp.status_code != 200:
            logger.warning(
                "core/google_ads: HTTP %s for %s: %s",
                resp.status_code, customer_id, resp.text[:300],
            )
            return None
        results = resp.json().get("results") or []
        total_micros = 0
        total_convs  = 0.0
        total_value  = 0.0
        for row in results:
            m = row.get("metrics") or {}
            total_micros += int(m.get("costMicros", 0))
            total_convs  += float(m.get("conversions", 0))
            total_value  += float(m.get("conversionsValue", 0))
        return {
            "spend":            round(total_micros / 1_000_000, 2),
            "conversions":      round(total_convs, 2),
            "conversion_value": round(total_value, 2),
            "rows_count":       len(results),
        }
    except Exception as exc:
        logger.warning("core/google_ads: fetch failed for %s: %s", customer_id, exc)
        return None


def _fetch_google_purchase_range(
    customer_id: str,
    refresh_token: str,
    period_start: date,
    period_end: date,
    manager_id: Optional[str] = None,
) -> Optional[dict]:
    """
    GAQL query filtered to PURCHASE category actions only.
    Returns {purchase_conversions, purchase_conversion_value} or None on failure.
    Uses the same token infrastructure as _fetch_google_range.
    """
    if not all([
        settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
        settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
    ]):
        return None

    token = _get_google_token(refresh_token)
    if not token:
        return None

    clean_cid = customer_id.replace("-", "").replace(" ", "")
    # FROM conversion_action (not customer) so conversion_action.category is a valid filter.
    # segments.conversion_action_category cannot be used as a WHERE filter on the customer resource.
    query = (
        "SELECT conversion_action.id, conversion_action.category, "
        "metrics.conversions, metrics.conversions_value "
        "FROM conversion_action "
        f"WHERE segments.date BETWEEN '{period_start.isoformat()}' AND '{period_end.isoformat()}' "
        "AND conversion_action.category = 'PURCHASE'"
    )

    headers: dict = {
        "Authorization":   f"Bearer {token}",
        "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        "Content-Type":    "application/json",
    }
    if manager_id:
        headers["login-customer-id"] = manager_id.replace("-", "").replace(" ", "")

    url = f"{_GOOGLE_ADS_API}/customers/{clean_cid}/googleAds:search"
    try:
        resp = httpx.post(url, json={"query": query}, headers=headers, timeout=30.0)
        if resp.status_code != 200:
            logger.warning(
                "core/google_ads: purchase query HTTP %s for %s: %s",
                resp.status_code, customer_id, resp.text[:300],
            )
            return None
        results = resp.json().get("results") or []
        total_convs = 0.0
        total_value = 0.0
        for row in results:
            m = row.get("metrics") or {}
            total_convs += float(m.get("conversions", 0))
            total_value += float(m.get("conversionsValue", 0))
        return {
            "purchase_conversions":      round(total_convs, 2),
            "purchase_conversion_value": round(total_value, 2),
        }
    except Exception as exc:
        logger.warning("core/google_ads: purchase fetch failed for %s: %s", customer_id, exc)
        return None


def _fetch_google_campaigns(
    customer_id: str,
    refresh_token: str,
    period_start: date,
    period_end: date,
    manager_id: Optional[str] = None,
) -> list[dict]:
    """
    Campaign-level GAQL query for the period.
    Returns per-campaign rows with campaign_id/name/spend/impressions/clicks/conversions/conversions_value.
    Returns [] on any failure — caller falls back to summary_row for DQG continuity.
    """
    token = _get_google_token(refresh_token)
    if not token:
        return []

    clean_cid = customer_id.replace("-", "").replace(" ", "")
    query = (
        "SELECT campaign.id, campaign.name, "
        "metrics.cost_micros, metrics.impressions, metrics.clicks, "
        "metrics.conversions, metrics.conversions_value "
        "FROM campaign "
        f"WHERE segments.date BETWEEN '{period_start.isoformat()}' AND '{period_end.isoformat()}' "
        "AND campaign.status != 'REMOVED'"
    )

    headers: dict = {
        "Authorization":   f"Bearer {token}",
        "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        "Content-Type":    "application/json",
    }
    if manager_id:
        headers["login-customer-id"] = manager_id.replace("-", "").replace(" ", "")

    url = f"{_GOOGLE_ADS_API}/customers/{clean_cid}/googleAds:search"
    try:
        resp = httpx.post(url, json={"query": query}, headers=headers, timeout=30.0)
        if resp.status_code != 200:
            logger.warning(
                "core/google_ads: campaign query HTTP %s for %s: %s",
                resp.status_code, customer_id, resp.text[:300],
            )
            return []
        results = resp.json().get("results") or []
        rows = []
        for row in results:
            c = row.get("campaign") or {}
            m = row.get("metrics") or {}
            rows.append({
                "campaign_id":       str(c.get("id", "")),
                "campaign_name":     c.get("name", ""),
                "spend":             round(int(m.get("costMicros", 0)) / 1_000_000, 2),
                "impressions":       int(m.get("impressions", 0)),
                "clicks":            int(m.get("clicks", 0)),
                "conversions":       round(float(m.get("conversions", 0)), 2),
                "conversions_value": round(float(m.get("conversionsValue", 0)), 2),
            })
        logger.info("core/google_ads: %d campaign rows for %s", len(rows), customer_id)
        return rows
    except Exception as exc:
        logger.warning("core/google_ads: campaign fetch failed for %s: %s", customer_id, exc)
        return []


def collect_google_ads(
    customer_id: str,
    refresh_token: str,
    period_start: date,
    period_end: date,
    manager_id: Optional[str] = None,
    client_currency: str = "BRL",
    client_timezone: str = "America/Sao_Paulo",
) -> CollectionResult:
    """
    Collect Google Ads spend + conversions for the period.

    Aggregates:
      google_spend                     = total spend (BRL)
      google_conversions               = total conversions (all actions in metrics.conversions)
      google_conversion_value          = total conversion value (BRL)
      google_purchase_conversions      = PURCHASE category actions only
      google_purchase_conversion_value = PURCHASE category value only (BRL)

    Rows: per-campaign rows (for ad_campaigns persistence).
    Falls back to summary_row for DQG continuity if campaign query fails.
    """
    collected_at = datetime.now(timezone.utc)
    clean_cid    = customer_id.replace("-", "").replace(" ", "")

    data = _fetch_google_range(
        customer_id=customer_id,
        refresh_token=refresh_token,
        period_start=period_start,
        period_end=period_end,
        manager_id=manager_id,
    )

    if data is None:
        return CollectionResult(
            source_system="google_ads",
            semantic_domain="ADS",
            account_id=clean_cid,
            client_currency=client_currency,
            client_timezone=client_timezone,
            period_start=period_start,
            period_end=period_end,
            rows=[],
            aggregates={},
            collected_at=collected_at,
            error="google_ads API returned None (token or config failure)",
        )

    # PURCHASE-only aggregates — separate query filtered by conversion_action_category
    purchase_data = _fetch_google_purchase_range(
        customer_id=customer_id,
        refresh_token=refresh_token,
        period_start=period_start,
        period_end=period_end,
        manager_id=manager_id,
    )

    # Per-campaign rows for ad_campaigns persistence; fall back to summary_row for DQG
    campaign_rows = _fetch_google_campaigns(
        customer_id=customer_id,
        refresh_token=refresh_token,
        period_start=period_start,
        period_end=period_end,
        manager_id=manager_id,
    )
    summary_row = {
        "date":             f"{period_start.isoformat()}:{period_end.isoformat()}",
        "spend":            data["spend"],
        "conversions":      data["conversions"],
        "conversion_value": data["conversion_value"],
    }
    # Campaign rows drive ad_campaigns persistence; summary_row used only when campaign query fails
    final_rows = campaign_rows if campaign_rows else ([summary_row] if data["rows_count"] > 0 else [])

    aggregates: dict = {
        "google_spend":            data["spend"],
        "google_conversions":      data["conversions"],
        "google_conversion_value": data["conversion_value"],
    }
    if purchase_data is not None:
        aggregates["google_purchase_conversions"]      = purchase_data["purchase_conversions"]
        aggregates["google_purchase_conversion_value"] = purchase_data["purchase_conversion_value"]

    return CollectionResult(
        source_system="google_ads",
        semantic_domain="ADS",
        account_id=clean_cid,
        client_currency=client_currency,
        client_timezone=client_timezone,
        period_start=period_start,
        period_end=period_end,
        rows=final_rows,
        aggregates=aggregates,
        collected_at=collected_at,
    )
