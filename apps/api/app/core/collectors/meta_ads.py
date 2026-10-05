"""
Meta Ads Core Collector.

Reuses services/meta_ads.fetch_campaign_insights (unchanged).
Aggregates per-campaign rows into period totals.
Preserves legacy service — does not modify it.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from ...core.dqg import CollectionResult
from ...services.meta_ads import fetch_campaign_insights

logger = logging.getLogger(__name__)


def collect_meta_ads(
    account_id: str,
    access_token: str,
    period_start: date,
    period_end: date,
    client_currency: str = "BRL",
    client_timezone: str = "America/Sao_Paulo",
) -> CollectionResult:
    """
    Collect Meta Ads spend + Meta-reported conversions for the period.

    Aggregates campaign-level rows:
      meta_spend            = sum(spend)
      meta_conversions      = sum(meta_purchases)
      meta_conversion_value = sum(meta_revenue)
    """
    collected_at = datetime.now(timezone.utc)
    clean_id     = account_id.removeprefix("act_")

    try:
        rows = fetch_campaign_insights(
            account_id=clean_id,
            access_token=access_token,
            since=period_start.isoformat(),
            until=period_end.isoformat(),
        )
    except Exception as exc:
        logger.warning("core/meta_ads: collect failed for act_%s: %s", clean_id, exc)
        return CollectionResult(
            source_system="meta_ads",
            semantic_domain="ADS",
            account_id=clean_id,
            client_currency=client_currency,
            client_timezone=client_timezone,
            period_start=period_start,
            period_end=period_end,
            rows=[],
            aggregates={},
            collected_at=collected_at,
            error=str(exc),
        )

    spend = sum(float(r.get("spend") or 0) for r in rows)
    convs = sum(float(r.get("meta_purchases") or 0) for r in rows)
    value = sum(float(r.get("meta_revenue") or 0) for r in rows)

    return CollectionResult(
        source_system="meta_ads",
        semantic_domain="ADS",
        account_id=clean_id,
        client_currency=client_currency,
        client_timezone=client_timezone,
        period_start=period_start,
        period_end=period_end,
        rows=rows,
        aggregates={
            "meta_spend":            round(spend, 2),
            "meta_conversions":      round(convs, 2),
            "meta_conversion_value": round(value, 2),
        },
        collected_at=collected_at,
    )
