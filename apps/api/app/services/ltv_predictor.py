"""
LTV predictor — assigns a forward-looking value to each purchase based on the
customer cohort's repeat behavior.

Why: sending `total_price` as the conversion value tells Meta/Google to bid for
*this* purchase. Sending `predicted_ltv` tells them to bid for customers who
will *keep buying* — which is the optimization that actually grows the business.

Model (v1 — rule-based, no training data needed):

    predicted_ltv = total_price × (1 + repeat_rate(channel) × avg_repeat_orders)

Where:
  - repeat_rate(channel) = share of buyers from this channel that come back
    within 90 days. Computed per-client per-channel from the last 180 days.
  - avg_repeat_orders   = mean number of orders placed by repeat customers
    beyond their first one.

Both stats are recomputed daily by `run_daily_for_all_clients()` and cached on
`clients.ltv_stats` (jsonb). The runtime path just reads the cache, so the
write_order hot loop is sub-millisecond.

Floors and caps:
  - When stats are absent (cold start, or zero repeat data), we use a
    conservative global default of repeat_rate=0.18 and avg_repeat_orders=1.4
    (BR DTC midmarket benchmark) — better than 1.0× which would defeat the
    whole point.
  - The multiplier is capped at 5× to keep one runaway segment from
    dominating Meta's optimizer.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..database import get_supabase

logger = logging.getLogger(__name__)

DEFAULT_REPEAT_RATE        = 0.18
DEFAULT_AVG_REPEAT_ORDERS  = 1.4
MAX_MULTIPLIER             = 5.0
STATS_LOOKBACK_DAYS        = 180
REPEAT_WINDOW_DAYS         = 90


def _channel_key(utm_source: Optional[str], utm_medium: Optional[str]) -> str:
    """Coarse channel bucket used for repeat-rate aggregation."""
    src = (utm_source or "direct").lower().strip()
    med = (utm_medium or "").lower().strip()
    if "meta" in src or "facebook" in src or "instagram" in src or med == "paid_social":
        return "meta"
    if "google" in src and med in ("cpc", "paid_search"):
        return "google_ads"
    if med in ("organic", "organic_search") or src == "google" and med == "organic":
        return "organic_search"
    if med == "email" or src == "klaviyo":
        return "email"
    if src in ("direct", "(direct)") or not src:
        return "direct"
    return "other"


def compute_ltv_stats_for_client(client_uuid: str) -> dict:
    """
    Recompute repeat-rate stats per channel for one client. Reads the last
    180 days of orders. Writes to clients.ltv_stats.
    """
    sb = get_supabase()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STATS_LOOKBACK_DAYS)).isoformat()
    try:
        rows = (
            sb.table("orders")
            .select("email, utm_source, utm_medium, total_price, is_first_purchase, created_at")
            .eq("client_id", client_uuid)
            .gte("created_at", cutoff)
            .not_.is_("email", "null")
            .execute()
        ).data or []
    except Exception as exc:
        logger.warning("compute_ltv_stats: query failed for %s: %s", client_uuid, exc)
        return {}

    by_channel: dict[str, dict] = {}
    repeat_window = timedelta(days=REPEAT_WINDOW_DAYS)
    orders_by_email: dict[str, list[dict]] = {}
    for o in rows:
        orders_by_email.setdefault(o["email"], []).append(o)

    # Group customers by their first-order channel; count whether they came back.
    for email, customer_orders in orders_by_email.items():
        ordered = sorted(customer_orders, key=lambda r: r["created_at"])
        first = ordered[0]
        ch = _channel_key(first.get("utm_source"), first.get("utm_medium"))
        bucket = by_channel.setdefault(ch, {
            "first_buyers":  0,
            "returners":     0,
            "repeat_orders": 0,
        })
        bucket["first_buyers"] += 1

        first_dt = datetime.fromisoformat(first["created_at"].replace("Z", "+00:00"))
        repeats_within = [
            r for r in ordered[1:]
            if datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) - first_dt <= repeat_window
        ]
        if repeats_within:
            bucket["returners"]     += 1
            bucket["repeat_orders"] += len(repeats_within)

    stats: dict[str, dict] = {}
    for ch, b in by_channel.items():
        if b["first_buyers"] < 5:
            continue  # too few samples to be meaningful
        repeat_rate = b["returners"] / b["first_buyers"]
        avg_repeat  = (b["repeat_orders"] / b["returners"]) if b["returners"] else 0
        stats[ch] = {
            "repeat_rate":       round(repeat_rate, 4),
            "avg_repeat_orders": round(avg_repeat, 4),
            "sample_size":       b["first_buyers"],
        }

    try:
        sb.table("clients").update({
            "ltv_stats":            stats,
            "ltv_stats_updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", client_uuid).execute()
    except Exception as exc:
        logger.warning("compute_ltv_stats: clients update failed for %s: %s", client_uuid, exc)
    return stats


def _load_stats(client_uuid: str) -> dict:
    """Read cached stats from clients.ltv_stats. Empty dict on miss."""
    try:
        row = (
            get_supabase().table("clients")
            .select("ltv_stats")
            .eq("id", client_uuid)
            .limit(1)
            .execute()
        )
        if row and row.data:
            return row.data[0].get("ltv_stats") or {}
    except Exception as exc:
        logger.debug("_load_stats failed: %s", exc)
    return {}


def predict_ltv(
    client_uuid: str,
    total_price: float,
    utm_source: Optional[str],
    utm_medium: Optional[str],
) -> Optional[float]:
    """
    Compute predicted_ltv for one order. Returns None when input is invalid
    (we never want to mislead Meta with a junk number — better to fall back
    to total_price).
    """
    if not (client_uuid and total_price and total_price > 0):
        return None
    ch = _channel_key(utm_source, utm_medium)
    stats = _load_stats(client_uuid).get(ch, {})
    repeat_rate       = stats.get("repeat_rate",       DEFAULT_REPEAT_RATE)
    avg_repeat_orders = stats.get("avg_repeat_orders", DEFAULT_AVG_REPEAT_ORDERS)
    multiplier = 1.0 + (repeat_rate * avg_repeat_orders)
    multiplier = min(multiplier, MAX_MULTIPLIER)
    return round(float(total_price) * multiplier, 2)


def run_daily_for_all_clients() -> None:
    """Scheduler entry — refresh stats for every active client."""
    sb = get_supabase()
    try:
        clients = (
            sb.table("clients")
            .select("id")
            .eq("is_active", True)
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("ltv_predictor: failed to list clients: %s", exc)
        return

    for c in clients:
        try:
            compute_ltv_stats_for_client(c["id"])
        except Exception as exc:
            logger.warning("ltv_predictor: client %s failed: %s", c.get("id"), exc)
    logger.info("ltv_predictor: refreshed stats for %d clients", len(clients))
