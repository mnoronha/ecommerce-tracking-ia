"""
Business Truth Collector — reads Shopify orders from the local DB.

Shopify is the Source of Record for revenue. No second API call.
Reads public.orders directly (already synced by webhook adapters).

Filters:
  - financial_status = 'paid'
  - platform_source NOT IN offline/POS sources

Zero orders in a period = 0 revenue (valid state, not absence).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from ...core.dqg import CollectionResult
from ...database import get_supabase

logger = logging.getLogger(__name__)

# Sources that are NOT online sales — never counted as business revenue
_OFFLINE_SOURCE_NAMES = frozenset({
    "pos", "draft_orders", "exchange", "subscription_contract",
})


def collect_business(
    client_uuid: str,    # UUID primary key of clients table
    client_id: str,      # canonical slug (e.g. "lk-sneakers")
    period_start: date,
    period_end: date,
    client_currency: str = "BRL",
    client_timezone: str = "America/Sao_Paulo",
) -> CollectionResult:
    """
    Aggregate paid online orders from the orders table for the period.

    Uses `created_at` in UTC for the period filter.
    Business revenue = SUM(total_price) for paid, non-POS orders.

    Note: period_start and period_end are treated as day boundaries in UTC.
    Timezone conversion to America/Sao_Paulo is documented but not applied here —
    UTC boundaries are used consistently with how orders are stored.
    """
    collected_at = datetime.now(timezone.utc)
    sb = get_supabase()

    period_start_ts = f"{period_start.isoformat()}T00:00:00+00:00"
    period_end_ts   = f"{period_end.isoformat()}T23:59:59+00:00"

    try:
        resp = (
            sb.table("orders")
            .select("id, total_price, currency, platform_source, financial_status")
            .eq("client_id", client_uuid)
            .eq("financial_status", "paid")
            .gte("created_at", period_start_ts)
            .lte("created_at", period_end_ts)
            .execute()
        )
    except Exception as exc:
        logger.warning("core/business: orders query failed for %s: %s", client_id, exc)
        return CollectionResult(
            source_system="shopify",
            semantic_domain="BUSINESS",
            account_id=client_id,
            client_currency=client_currency,
            client_timezone=client_timezone,
            period_start=period_start,
            period_end=period_end,
            rows=[],
            aggregates={},
            collected_at=collected_at,
            error=str(exc),
        )

    all_rows = resp.data or []

    # Filter out offline/POS sources
    online_rows = [
        r for r in all_rows
        if (r.get("platform_source") or "").lower() not in _OFFLINE_SOURCE_NAMES
    ]

    revenue = sum(float(r.get("total_price") or 0) for r in online_rows)
    count   = len(online_rows)

    return CollectionResult(
        source_system="shopify",
        semantic_domain="BUSINESS",
        account_id=client_id,
        client_currency=client_currency,
        client_timezone=client_timezone,
        period_start=period_start,
        period_end=period_end,
        rows=online_rows,
        aggregates={
            "revenue_business": round(revenue, 2),
            "orders_count":     float(count),
        },
        collected_at=collected_at,
    )
