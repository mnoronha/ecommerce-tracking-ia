"""
Core Metric Registry — LK Sneakers (ecommerce) — Bloco 1.

Minimum set for performance view and legacy equivalence check.
Only metrics derived from available sources; no speculative additions.

Metric taxonomy:
  BUSINESS   — revenue, orders (Shopify source of record)
  ADS        — spend, conversions, conversion_value (Meta/Google), derived MER
  JOURNEY    — sessions, purchases, revenue (GA4)
  CONVERSION — (Bloco 2+)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..agency_api.v1.enums import MetricDomain


@dataclass(frozen=True)
class MetricDef:
    key: str
    domain: MetricDomain
    source_system: str   # "shopify" | "meta_ads" | "google_ads" | "ga4" | "derived"
    unit: str
    aggregation: str     # "sum" | "count" | "derived"
    zero_semantics: str  # "no_sales" | "no_spend" | "ok" | "no_traffic"
    currency: Optional[str] = None


# fmt: off
METRIC_REGISTRY: dict[str, MetricDef] = {

    # ── BUSINESS (Shopify) ────────────────────────────────────────────────────
    "revenue_business": MetricDef(
        key="revenue_business", domain=MetricDomain.BUSINESS,
        source_system="shopify", unit="BRL", aggregation="sum",
        zero_semantics="no_sales", currency="BRL",
    ),
    "orders_count": MetricDef(
        key="orders_count", domain=MetricDomain.BUSINESS,
        source_system="shopify", unit="orders", aggregation="count",
        zero_semantics="no_sales",
    ),

    # ── ADS — Meta ────────────────────────────────────────────────────────────
    "meta_spend": MetricDef(
        key="meta_spend", domain=MetricDomain.ADS,
        source_system="meta_ads", unit="BRL", aggregation="sum",
        zero_semantics="no_spend", currency="BRL",
    ),
    "meta_conversions": MetricDef(
        key="meta_conversions", domain=MetricDomain.ADS,
        source_system="meta_ads", unit="conversions", aggregation="sum",
        zero_semantics="ok",
    ),
    "meta_conversion_value": MetricDef(
        key="meta_conversion_value", domain=MetricDomain.ADS,
        source_system="meta_ads", unit="BRL", aggregation="sum",
        zero_semantics="ok", currency="BRL",
    ),

    # ── ADS — Google ──────────────────────────────────────────────────────────
    "google_spend": MetricDef(
        key="google_spend", domain=MetricDomain.ADS,
        source_system="google_ads", unit="BRL", aggregation="sum",
        zero_semantics="no_spend", currency="BRL",
    ),
    "google_conversions": MetricDef(
        key="google_conversions", domain=MetricDomain.ADS,
        source_system="google_ads", unit="conversions", aggregation="sum",
        zero_semantics="ok",
    ),
    "google_conversion_value": MetricDef(
        key="google_conversion_value", domain=MetricDomain.ADS,
        source_system="google_ads", unit="BRL", aggregation="sum",
        zero_semantics="ok", currency="BRL",
    ),

    # ── ADS — Derived ─────────────────────────────────────────────────────────
    "total_spend": MetricDef(
        key="total_spend", domain=MetricDomain.ADS,
        source_system="derived", unit="BRL", aggregation="derived",
        zero_semantics="no_spend", currency="BRL",
    ),
    "mer": MetricDef(
        key="mer", domain=MetricDomain.ADS,
        source_system="derived", unit="x", aggregation="derived",
        zero_semantics="no_spend",
    ),

    # ── JOURNEY (GA4) ─────────────────────────────────────────────────────────
    "ga4_sessions": MetricDef(
        key="ga4_sessions", domain=MetricDomain.JOURNEY,
        source_system="ga4", unit="sessions", aggregation="sum",
        zero_semantics="no_traffic",
    ),
    "ga4_purchases": MetricDef(
        key="ga4_purchases", domain=MetricDomain.JOURNEY,
        source_system="ga4", unit="purchases", aggregation="sum",
        zero_semantics="ok",
    ),
    "ga4_revenue": MetricDef(
        key="ga4_revenue", domain=MetricDomain.JOURNEY,
        source_system="ga4", unit="BRL", aggregation="sum",
        zero_semantics="ok", currency="BRL",
    ),
}
# fmt: on

# Metrics that require ALL their input sources to be READY before computing.
DERIVED_DEPS: dict[str, list[str]] = {
    "total_spend": ["meta_spend", "google_spend"],
    "mer": ["revenue_business", "total_spend"],
}
