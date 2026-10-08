"""
Balance monitor for prepaid ad accounts.

run_balance_check_all_prepaid() — hourly; processes only clients marked prepaid.
check_client_balance(client_id, platform) — on-demand single-client check (trigger endpoint).

Spend reference: reads ad_spend table (3-day preferred, 7-day fallback).
Never treats campaign/daily budgets or spend_cap as balance.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from ..database import get_supabase
from ..services import crypto
from .collectors.balance import (
    BalanceSnapshot,
    collect_google_balance,
    collect_meta_balance,
)
from .alert_evaluator import evaluate_balance_alerts

logger = logging.getLogger(__name__)

_SPEND_WINDOW_PREFERRED = 3
_SPEND_WINDOW_FALLBACK  = 7
_MIN_SPEND_DAYS         = 2   # need at least 2 days with data for a reliable avg

# ad_spend.channel values per platform
_CHANNEL_MAP = {"google": "google_ads", "meta": "meta_ads"}


# ── Spend reference ────────────────────────────────────────────────────────────

def _get_avg_daily_spend(sb, client_uuid: str, platform: str, window_days: int) -> tuple[Optional[float], int, int]:
    """
    Returns (avg_daily_spend, actual_window_days_queried, days_with_nonzero_data).
    Uses client UUID (not slug) because ad_spend.client_id is UUID.
    Only includes days with spend > 0.
    """
    channel = _CHANNEL_MAP.get(platform)
    if not channel:
        return None, 0, 0

    cutoff = (date.today() - timedelta(days=window_days)).isoformat()
    try:
        res = (
            sb.table("ad_spend")
            .select("date, spend")
            .eq("client_id", client_uuid)
            .eq("channel", channel)
            .gt("spend", 0)
            .gte("date", cutoff)
            .order("date", desc=True)
            .execute()
        )
        rows = res.data or []
    except Exception as exc:
        logger.warning("balance_monitor: spend fetch failed %s/%s: %s", client_uuid, platform, exc)
        return None, window_days, 0

    if len(rows) < _MIN_SPEND_DAYS:
        return None, window_days, len(rows)

    total_spend = sum(float(r["spend"]) for r in rows)
    avg         = round(total_spend / len(rows), 2)
    return avg, window_days, len(rows)


def _resolve_spend_reference(sb, client_uuid: str, platform: str) -> tuple[Optional[float], Optional[int], Optional[int]]:
    """
    Try 3-day window first, fall back to 7-day.
    Returns (avg_daily_spend, window_days, data_days).
    """
    avg, w, d = _get_avg_daily_spend(sb, client_uuid, platform, _SPEND_WINDOW_PREFERRED)
    if avg is not None:
        return avg, w, d

    avg, w, d = _get_avg_daily_spend(sb, client_uuid, platform, _SPEND_WINDOW_FALLBACK)
    if avg is not None:
        return avg, w, d

    return None, _SPEND_WINDOW_FALLBACK, d


# ── Snapshot writer ───────────────────────────────────────────────────────────

def _write_snapshot(sb, client_id: str, platform: str, snap: BalanceSnapshot) -> None:
    try:
        sb.table("core_balance_snapshots").insert({
            "client_id":               client_id,
            "platform":                platform,
            "snapshot_at":             datetime.now(timezone.utc).isoformat(),
            "billing_model":           snap.get("billing_model"),
            "collection_status":       snap.get("collection_status"),
            "balance":                 snap.get("balance"),
            "balance_status":          snap["balance_status"],
            "avg_daily_spend":         snap.get("avg_daily_spend"),
            "spend_avg_window_days":   snap.get("spend_avg_window_days"),
            "spend_data_days":         snap.get("spend_data_days"),
            "estimated_days_remaining": snap.get("estimated_days_remaining"),
            "threshold_low":           snap.get("threshold_low"),
            "threshold_critical":      snap.get("threshold_critical"),
            "currency":                snap.get("currency"),
            "raw":                     snap.get("raw"),
            "error":                   snap.get("error"),
        }).execute()
    except Exception as exc:
        logger.error(
            "balance_monitor: snapshot write failed %s/%s: %s",
            client_id, platform, exc,
        )


# ── Per-client check ───────────────────────────────────────────────────────────

def _check_client(client: dict, sb) -> dict:
    """
    Run balance check for all prepaid platforms of a single client.
    Returns dict with per-platform snapshot results.
    """
    client_id   = client["client_id"]
    client_uuid = client["id"]
    secrets     = crypto.decrypt_client_secrets(client)
    results: dict = {"client_id": client_id, "platforms": {}}

    # ── Google ────────────────────────────────────────────────────────────────
    if client.get("google_prepaid"):
        cid           = client.get("google_ads_customer_id")
        refresh_token = secrets.get("google_ads_refresh_token")
        manager_id    = client.get("google_ads_login_customer_id")
        threshold     = float(client.get("google_balance_threshold") or 200)
        currency      = client.get("currency")
        token_health  = client.get("google_ads_token_health", "unknown")

        if not cid or not refresh_token:
            snap: BalanceSnapshot = BalanceSnapshot(
                billing_model=None, collection_status="BLOCKED",
                balance=None, balance_status="MISSING",
                avg_daily_spend=None, spend_avg_window_days=None,
                spend_data_days=None, estimated_days_remaining=None,
                threshold_low=threshold,
                threshold_critical=threshold * 0.5,
                currency=currency, raw=None,
                error="google_ads_customer_id or refresh_token missing",
            )
        elif token_health == "invalid":
            snap = BalanceSnapshot(
                billing_model=None, collection_status="BLOCKED",
                balance=None, balance_status="MISSING",
                avg_daily_spend=None, spend_avg_window_days=None,
                spend_data_days=None, estimated_days_remaining=None,
                threshold_low=threshold,
                threshold_critical=threshold * 0.5,
                currency=currency, raw=None,
                error=f"google_ads_token_health={token_health} — fix token before balance check",
            )
        else:
            avg_spend, w_days, d_days = _resolve_spend_reference(sb, client_uuid, "google")
            snap = collect_google_balance(
                customer_id=cid,
                refresh_token=refresh_token,
                threshold_low=threshold,
                currency=currency,
                manager_id=manager_id,
                avg_daily_spend=avg_spend,
                spend_avg_window_days=w_days,
                spend_data_days=d_days,
            )

        _write_snapshot(sb, client_id, "google", snap)
        results["platforms"]["google"] = {
            "billing_model":           snap.get("billing_model"),
            "collection_status":       snap.get("collection_status"),
            "balance":                 snap.get("balance"),
            "balance_status":          snap.get("balance_status"),
            "currency":                snap.get("currency"),
            "avg_daily_spend":         snap.get("avg_daily_spend"),
            "spend_avg_window_days":   snap.get("spend_avg_window_days"),
            "spend_data_days":         snap.get("spend_data_days"),
            "estimated_days_remaining": snap.get("estimated_days_remaining"),
            "error":                   snap.get("error"),
        }
        logger.info(
            "balance_monitor: %s/google status=%s balance=%s days=%s",
            client_id, snap.get("balance_status"), snap.get("balance"), snap.get("estimated_days_remaining"),
        )

    # ── Meta ──────────────────────────────────────────────────────────────────
    if client.get("meta_prepaid"):
        account_id   = client.get("meta_ad_account_id")
        access_token = secrets.get("meta_access_token")
        threshold    = float(client.get("meta_balance_threshold") or 200)
        currency     = client.get("currency")
        token_health = client.get("meta_token_health", "unknown")

        if not account_id or not access_token:
            snap = BalanceSnapshot(
                billing_model=None, collection_status="BLOCKED",
                balance=None, balance_status="MISSING",
                avg_daily_spend=None, spend_avg_window_days=None,
                spend_data_days=None, estimated_days_remaining=None,
                threshold_low=threshold,
                threshold_critical=threshold * 0.5,
                currency=currency, raw=None,
                error="meta_ad_account_id or access_token missing",
            )
        elif token_health in ("invalid", "expired"):
            snap = BalanceSnapshot(
                billing_model=None, collection_status="BLOCKED",
                balance=None, balance_status="MISSING",
                avg_daily_spend=None, spend_avg_window_days=None,
                spend_data_days=None, estimated_days_remaining=None,
                threshold_low=threshold,
                threshold_critical=threshold * 0.5,
                currency=currency, raw=None,
                error=f"meta_token_health={token_health}",
            )
        else:
            avg_spend, w_days, d_days = _resolve_spend_reference(sb, client_uuid, "meta")
            snap = collect_meta_balance(
                ad_account_id=account_id,
                access_token=access_token,
                threshold_low=threshold,
                currency=currency,
                avg_daily_spend=avg_spend,
                spend_avg_window_days=w_days,
                spend_data_days=d_days,
            )

        _write_snapshot(sb, client_id, "meta", snap)
        results["platforms"]["meta"] = {
            "billing_model":           snap.get("billing_model"),
            "collection_status":       snap.get("collection_status"),
            "balance":                 snap.get("balance"),
            "balance_status":          snap.get("balance_status"),
            "currency":                snap.get("currency"),
            "avg_daily_spend":         snap.get("avg_daily_spend"),
            "spend_avg_window_days":   snap.get("spend_avg_window_days"),
            "spend_data_days":         snap.get("spend_data_days"),
            "estimated_days_remaining": snap.get("estimated_days_remaining"),
            "error":                   snap.get("error"),
        }
        logger.info(
            "balance_monitor: %s/meta status=%s balance=%s days=%s",
            client_id, snap.get("balance_status"), snap.get("balance"), snap.get("estimated_days_remaining"),
        )

    # Trigger alert evaluation if any platform was checked
    if results["platforms"]:
        try:
            evaluate_balance_alerts(client_id)
        except Exception as exc:
            logger.error("balance_monitor: alert eval failed %s: %s", client_id, exc)

    return results


# ── Scheduled run ─────────────────────────────────────────────────────────────

def run_balance_check_all_prepaid() -> dict:
    """Called hourly by APScheduler. Only processes google_prepaid OR meta_prepaid clients."""
    sb  = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    try:
        res = (
            sb.table("clients")
            .select(
                "id, client_id, google_prepaid, meta_prepaid, "
                "google_ads_customer_id, google_ads_refresh_token, "
                "google_ads_login_customer_id, google_ads_token_health, "
                "google_balance_threshold, "
                "meta_ad_account_id, meta_access_token, meta_token_health, "
                "meta_balance_threshold, "
                "currency, is_active"
            )
            .eq("is_active", True)
            .execute()
        )
        clients = [
            c for c in (res.data or [])
            if c.get("google_prepaid") or c.get("meta_prepaid")
        ]
    except Exception as exc:
        logger.error("balance_monitor: client load failed: %s", exc)
        return {"error": str(exc)}

    if not clients:
        logger.info("balance_monitor: no prepaid clients, skipping")
        return {"checked": 0}

    results = []
    for client in clients:
        cid = client.get("client_id") or "unknown"
        try:
            results.append(_check_client(client, sb))
        except Exception as exc:
            logger.error("balance_monitor: client %s failed: %s", cid, exc)
            results.append({"client_id": cid, "error": str(exc)})

    logger.info("balance_monitor: checked %d prepaid client(s) at %s", len(results), now)
    return {"checked": len(results), "results": results}


# ── On-demand trigger ─────────────────────────────────────────────────────────

def trigger_balance_check(client_id: str, platform: Optional[str] = None) -> dict:
    """
    Synchronous on-demand check for a single client.
    Used by the trigger endpoint; returns the snapshot data directly.
    If platform is None, checks all configured prepaid platforms.
    """
    sb = get_supabase()

    try:
        res = (
            sb.table("clients")
            .select(
                "id, client_id, google_prepaid, meta_prepaid, "
                "google_ads_customer_id, google_ads_refresh_token, "
                "google_ads_login_customer_id, google_ads_token_health, "
                "google_balance_threshold, "
                "meta_ad_account_id, meta_access_token, meta_token_health, "
                "meta_balance_threshold, "
                "currency, is_active"
            )
            .eq("client_id", client_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        return {"error": f"DB error: {exc}"}

    if not (res and res.data):
        return {"error": f"client not found: {client_id}"}

    client = res.data[0]

    # Override prepaid flags if a specific platform is forced
    if platform == "google":
        client = {**client, "google_prepaid": True, "meta_prepaid": False}
    elif platform == "meta":
        client = {**client, "google_prepaid": False, "meta_prepaid": True}

    return _check_client(client, sb)
