"""
GA4 Core Collector.

Reuses services/ga4_reporting.fetch_overview (unchanged).
Extracts summary totals: sessions, purchases (transactions), revenue.
Preserves legacy service — does not modify it.

GA4 note: data has a typical processing lag of 24–72h.
FRESHNESS_OK threshold is 72h (configured in dqg.py).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from ...core.dqg import CollectionResult
from ...services.ga4_reporting import fetch_overview

logger = logging.getLogger(__name__)


def collect_ga4(
    property_id: str,
    refresh_token: str,
    period_start: date,
    period_end: date,
    client_currency: str = "BRL",
    client_timezone: str = "America/Sao_Paulo",
) -> CollectionResult:
    """
    Collect GA4 overview metrics for the period.

    Aggregates (from fetch_overview summary):
      ga4_sessions  = total sessions
      ga4_purchases = total transactions (distinct from GA4 "conversions")
      ga4_revenue   = total purchaseRevenue (BRL)
    """
    collected_at = datetime.now(timezone.utc)

    try:
        result = fetch_overview(
            property_id=property_id,
            refresh_token=refresh_token,
            start_date=period_start,
            end_date=period_end,
        )
    except Exception as exc:
        logger.warning("core/ga4: fetch_overview failed for %s: %s", property_id, exc)
        return CollectionResult(
            source_system="ga4",
            semantic_domain="JOURNEY",
            account_id=property_id,
            client_currency=client_currency,
            client_timezone=client_timezone,
            period_start=period_start,
            period_end=period_end,
            rows=[],
            aggregates={},
            collected_at=collected_at,
            error=str(exc),
        )

    if "error" in result:
        return CollectionResult(
            source_system="ga4",
            semantic_domain="JOURNEY",
            account_id=property_id,
            client_currency=client_currency,
            client_timezone=client_timezone,
            period_start=period_start,
            period_end=period_end,
            rows=[],
            aggregates={},
            collected_at=collected_at,
            error=f"ga4 API error: {result['error']}",
        )

    summary = result.get("summary", {})
    by_channel = result.get("by_channel", [])

    sessions   = int(summary.get("sessions",  0))
    purchases  = int(summary.get("purchases", 0))
    revenue    = float(summary.get("revenue", 0.0))

    return CollectionResult(
        source_system="ga4",
        semantic_domain="JOURNEY",
        account_id=property_id,
        client_currency=client_currency,
        client_timezone=client_timezone,
        period_start=period_start,
        period_end=period_end,
        rows=by_channel,   # per-channel rows used for dedup/count checks
        aggregates={
            "ga4_sessions":  sessions,
            "ga4_purchases": purchases,
            "ga4_revenue":   round(revenue, 2),
        },
        collected_at=collected_at,
    )
