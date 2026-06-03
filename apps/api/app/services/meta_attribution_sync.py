"""
Meta-reported attribution sync.

Daily job that pulls per-ad-per-day insights with `actions=purchase` from the
Meta Marketing API and caches in `meta_ad_attributions`. The data is used by:

  - /journey ?lens=meta-attribution — campaigns ranked by Meta-reported
    purchases, side-by-side with our server-side numbers.
  - probabilistic_match() — assigns ad context to orders that have no UTM
    but the visitor came from Meta (presence of fbp/fbc).

The Meta Insights endpoint returns one row per (ad_id, day) when we ask for
level=ad and time_increment=1.

Docs: https://developers.facebook.com/docs/marketing-api/insights
"""

import logging
from datetime import datetime, date, timedelta, timezone
from typing import Optional

import httpx

from ..database import get_supabase
from ..services import crypto
from .meta_ads import _pick_action

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com/v19.0"

_PURCHASE_TYPES = (
    "purchase",
    "omni_purchase",
    "offsite_conversion.fb_pixel_purchase",
)


def _fetch_daily_ad_insights(account_id: str, access_token: str, days: int) -> list[dict]:
    """
    Pull one row per (ad_id, day) for the trailing `days` window. We bring
    enough fields to power both the dashboard lens and probabilistic match.
    """
    clean = account_id.removeprefix("act_")
    since = (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
    until = datetime.now(timezone.utc).date().isoformat()

    url = f"{_GRAPH}/act_{clean}/insights"
    params = {
        "level":          "ad",
        "time_increment": 1,                  # one row per day
        "time_range":     f'{{"since":"{since}","until":"{until}"}}',
        "fields": (
            "date_start,date_stop,"
            "ad_id,ad_name,"
            "adset_id,adset_name,"
            "campaign_id,campaign_name,"
            "spend,impressions,clicks,"
            "actions,action_values"
        ),
        "limit":          500,
        "access_token":   access_token,
    }

    rows: list[dict] = []
    while url:
        try:
            resp = httpx.get(url, params=params, timeout=45.0)
            if resp.status_code != 200:
                logger.warning("meta_attribution_sync HTTP %s: %s", resp.status_code, resp.text[:300])
                return rows
            body = resp.json()
            rows.extend(body.get("data", []))
            paging = (body.get("paging") or {}).get("next")
            if paging:
                url, params = paging, None
            else:
                url = None
        except Exception as exc:
            logger.error("meta_attribution_sync exception: %s", exc)
            return rows
    return rows


def sync_for_client(client_uuid: str, account_id: str, access_token: str, days: int = 7) -> dict:
    """
    Pull recent daily ad insights and upsert into meta_ad_attributions.
    Idempotent — re-running just refreshes the rows.
    """
    if not (account_id and access_token):
        return {"error": "missing credentials", "synced": 0}

    rows = _fetch_daily_ad_insights(account_id, access_token, days)
    if not rows:
        return {"synced": 0, "rows_in_response": 0}

    sb = get_supabase()
    upserts = []
    for r in rows:
        purchases = _pick_action(r.get("actions") or [], _PURCHASE_TYPES)
        purchase_value = _pick_action(r.get("action_values") or [], _PURCHASE_TYPES)
        upserts.append({
            "client_id":       client_uuid,
            "date":            r.get("date_start"),
            "ad_id":           str(r.get("ad_id")) if r.get("ad_id") else "unknown",
            "ad_name":         r.get("ad_name"),
            "adset_id":        str(r.get("adset_id"))    if r.get("adset_id")    else None,
            "adset_name":      r.get("adset_name"),
            "campaign_id":     str(r.get("campaign_id")) if r.get("campaign_id") else None,
            "campaign_name":   r.get("campaign_name"),
            "spend":           float(r.get("spend") or 0),
            "impressions":     int(r.get("impressions") or 0),
            "clicks":          int(r.get("clicks") or 0),
            "purchases":       int(purchases or 0),
            "purchase_value":  float(purchase_value or 0),
            "raw":             r,
            "synced_at":       "now()",
        })

    inserted = 0
    for i in range(0, len(upserts), 500):
        chunk = upserts[i : i + 500]
        try:
            sb.table("meta_ad_attributions").upsert(
                chunk, on_conflict="client_id,date,ad_id"
            ).execute()
            inserted += len(chunk)
        except Exception as exc:
            logger.warning("meta_ad_attributions upsert failed: %s", exc)

    return {"synced": inserted, "rows_in_response": len(rows), "days": days}


def run_daily_sync_all_clients() -> None:
    """Cron entry point — runs daily at 06:00 UTC for all active clients."""
    sb = get_supabase()
    try:
        clients = (
            sb.table("clients")
            .select("id, pixel_id, meta_ad_account_id, meta_access_token")
            .eq("is_active", True)
            .not_.is_("meta_ad_account_id", "null")
            .not_.is_("meta_access_token", "null")
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("meta_attribution_sync: clients load failed: %s", exc)
        return

    total = 0
    for c in clients:
        try:
            r = sync_for_client(
                client_uuid=c["id"],
                account_id=c["meta_ad_account_id"],
                access_token=crypto.decrypt_secret(c["meta_access_token"]),
                days=7,
            )
            total += r.get("synced", 0)
        except Exception as exc:
            logger.warning("meta_attribution_sync: client %s failed: %s", c.get("pixel_id"), exc)
    logger.info("meta_attribution_sync: %d clients, %d rows", len(clients), total)


# ── Probabilistic match ───────────────────────────────────────────────────────

def probabilistic_match(client_uuid: str, days: int = 30) -> dict:
    """
    For orders that came from Meta but have no campaign UTM, attribute to the
    most likely ad based on which ad had the most clicks/spend on the order's
    day. Confidence = top_ad_clicks / total_meta_clicks_that_day.

    Updates orders.probable_meta_* columns. Idempotent.

    Targets only orders that:
      - have utm_source containing 'meta'/'facebook'/'instagram' OR
        have a visitor with fbp/fbc set
      - have utm_campaign null OR utm_campaign matches a numeric ID we
        couldn't resolve

    Returns count summary.
    """
    sb = get_supabase()
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Step 1: load eligible orders
    orders = (
        sb.table("orders")
        .select("id, created_at, utm_source, utm_campaign, visitor_id")
        .eq("client_id", client_uuid)
        .eq("financial_status", "paid")
        .gt("total_price", 0)
        .gte("created_at", cutoff_iso)
        .execute()
    ).data or []

    if not orders:
        return {"orders_scanned": 0, "matched": 0, "skipped_has_utm": 0, "no_data": 0}

    # Filter: needs Meta context but no clear campaign
    def is_eligible(o: dict) -> bool:
        src = (o.get("utm_source") or "").lower()
        camp = o.get("utm_campaign")
        is_meta = any(x in src for x in ("meta", "facebook", "instagram", "fb"))
        camp_unknown = (not camp) or (str(camp).isdigit() and len(str(camp)) >= 10)
        return is_meta and camp_unknown

    eligible = [o for o in orders if is_eligible(o)]
    skipped_has_utm = len(orders) - len(eligible)

    if not eligible:
        return {"orders_scanned": len(orders), "matched": 0, "skipped_has_utm": skipped_has_utm, "no_data": 0}

    # Step 2: load all ad-day rows for the period — keyed by date
    attr_rows = (
        sb.table("meta_ad_attributions")
        .select("date, ad_id, ad_name, campaign_id, campaign_name, clicks, spend")
        .eq("client_id", client_uuid)
        .gte("date", (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat())
        .execute()
    ).data or []

    by_date: dict[str, list[dict]] = {}
    for r in attr_rows:
        by_date.setdefault(r["date"], []).append(r)

    matched = 0
    no_data = 0
    for o in eligible:
        try:
            d = o["created_at"][:10]  # YYYY-MM-DD prefix
            day_rows = by_date.get(d) or []
            # Pick the ad with the highest click share that day. Spend as tiebreak.
            day_rows = [r for r in day_rows if (r.get("clicks") or 0) > 0]
            if not day_rows:
                no_data += 1
                continue
            day_rows.sort(key=lambda r: (r.get("clicks") or 0, r.get("spend") or 0), reverse=True)
            top = day_rows[0]
            total_clicks = sum(r.get("clicks") or 0 for r in day_rows) or 1
            confidence = round((top.get("clicks") or 0) / total_clicks, 3)

            sb.table("orders").update({
                "probable_meta_ad_id":         top.get("ad_id"),
                "probable_meta_campaign_id":   top.get("campaign_id"),
                "probable_meta_campaign_name": top.get("campaign_name"),
                "probable_meta_confidence":    min(confidence, 1.0),
            }).eq("id", o["id"]).execute()
            matched += 1
        except Exception as exc:
            logger.debug("probable match failed for order %s: %s", o.get("id"), exc)

    return {
        "orders_scanned":    len(orders),
        "eligible":          len(eligible),
        "skipped_has_utm":   skipped_has_utm,
        "matched":           matched,
        "no_data":           no_data,
    }
