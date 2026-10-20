"""
Change history ingestor orchestrator.

Per-client flow:
  1. Load polling cursor (last_polled_at) from core_change_history_cursors.
  2. Fetch raw events from Google Ads and/or Meta Ads (read-only).
  3. Normalize each event into a core_change_log row.
  4. Deduplicate against existing external_change_id values.
  5. Insert new rows with source=PLATFORM_DETECTED.
  6. Attempt task matching (POSSIBLE_MATCH only — never closes tasks).
  7. Save cursor with updated last_polled_at and status.

Healthy account with zero changes in the period → status=READY, changes_count=0.
Not an error.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

OVERLAP_MINUTES = 2    # safe window overlap to avoid missing events at boundaries
BACKFILL_DAYS   = 30   # look-back window when no cursor exists (first run)


# ── Cursor ────────────────────────────────────────────────────────────────────

def _load_cursor(sb, client_id: str, platform: str) -> Optional[dict]:
    try:
        res = (
            sb.table("core_change_history_cursors")
            .select("last_polled_at, last_status")
            .eq("client_id", client_id)
            .eq("platform", platform)
            .maybe_single()
            .execute()
        )
        return res.data or None
    except Exception as exc:
        logger.warning("change_history: cursor load failed %s/%s: %s", client_id, platform, exc)
        return None


def _save_cursor(
    sb,
    client_id: str,
    platform: str,
    status: str,
    polled_at: datetime,
    changes_count: int = 0,
    error: Optional[str] = None,
) -> None:
    payload = {
        "client_id":      client_id,
        "platform":       platform,
        "last_polled_at": polled_at.isoformat(),
        "last_status":    status,
        "changes_count":  changes_count,
        "error_message":  error[:500] if error else None,
        "updated_at":     datetime.now(timezone.utc).isoformat(),
    }
    try:
        sb.table("core_change_history_cursors").upsert(
            payload, on_conflict="client_id,platform",
        ).execute()
    except Exception as exc:
        logger.warning("change_history: cursor save failed %s/%s: %s", client_id, platform, exc)


# ── Dedup + insert ────────────────────────────────────────────────────────────

def _dedup_and_insert(sb, rows: list[dict]) -> tuple[int, int, list[dict]]:
    """
    Dedup rows by external_change_id, then insert the new ones.

    Returns (inserted_count, skipped_count, inserted_rows).
    inserted_rows carries the full row dict with the DB-assigned `id` when available.
    """
    if not rows:
        return 0, 0, []

    # Group by (client_id, channel) — should be identical within one ingest batch
    by_key: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (row["client_id"], row["channel"])
        by_key.setdefault(key, []).append(row)

    total_inserted = 0
    total_skipped  = 0
    inserted_rows: list[dict] = []

    for (client_id, channel), group in by_key.items():
        ext_ids = [r["external_change_id"] for r in group if r.get("external_change_id")]

        existing_ids: set[str] = set()
        if ext_ids:
            try:
                res = (
                    sb.table("core_change_log")
                    .select("external_change_id")
                    .eq("client_id", client_id)
                    .eq("channel", channel)
                    .in_("external_change_id", ext_ids)
                    .execute()
                )
                for r in (res.data or []):
                    existing_ids.add(r["external_change_id"])
            except Exception as exc:
                logger.warning("change_history: dedup check failed: %s", exc)

        to_insert = [r for r in group if r.get("external_change_id") not in existing_ids]
        total_skipped += len(group) - len(to_insert)

        for row in to_insert:
            try:
                res = sb.table("core_change_log").insert(row).execute()
                total_inserted += 1
                # Capture DB-assigned id for downstream matching
                if res.data:
                    inserted_rows.append(res.data[0])
                else:
                    inserted_rows.append(row)
            except Exception as exc:
                err = str(exc).lower()
                if any(k in err for k in ("duplicate", "unique", "conflict")):
                    total_skipped += 1  # race-condition dedup by unique index
                else:
                    logger.warning("change_history: insert failed: %s", exc)

    return total_inserted, total_skipped, inserted_rows


# ── Task matching (POSSIBLE_MATCH only) ──────────────────────────────────────

def _try_match(sb, client_id: str, occurred_at_iso: str) -> str:
    """
    Check whether an open task exists for this client within ±72h of the change.
    Returns 'POSSIBLE_MATCH' or 'UNMATCHED'. Never raises. Never closes tasks.
    """
    if not client_id or not occurred_at_iso:
        return "UNMATCHED"
    try:
        occurred = datetime.fromisoformat(occurred_at_iso)
        window_start = (occurred - timedelta(hours=72)).isoformat()
        window_end   = (occurred + timedelta(hours=72)).isoformat()
        res = (
            sb.table("core_tasks")
            .select("id")
            .eq("client_id", client_id)
            .in_("status", ["OPEN", "IN_PROGRESS"])
            .gte("created_at", window_start)
            .lte("created_at", window_end)
            .limit(1)
            .execute()
        )
        if res.data:
            return "POSSIBLE_MATCH"
    except Exception:
        pass
    return "UNMATCHED"


def _update_match_status(sb, row_id: str, match_status: str) -> None:
    try:
        sb.table("core_change_log").update(
            {"match_status": match_status}
        ).eq("id", row_id).execute()
    except Exception as exc:
        logger.debug("change_history: match_status update failed %s: %s", row_id, exc)


# ── Per-platform ingest ───────────────────────────────────────────────────────

def _ingest_platform(sb, client_row: dict, platform: str, now: datetime) -> dict:
    """
    Run ingest for one client × platform.
    client_row must already have credentials decrypted.
    Returns stats dict.
    """
    client_id = client_row["client_id"]

    # Determine poll window from cursor
    cursor = _load_cursor(sb, client_id, platform)
    if cursor and cursor.get("last_polled_at"):
        try:
            last = datetime.fromisoformat(
                cursor["last_polled_at"].replace("Z", "+00:00")
            )
            since_dt = last - timedelta(minutes=OVERLAP_MINUTES)
        except Exception:
            since_dt = now - timedelta(days=BACKFILL_DAYS)
    else:
        since_dt = now - timedelta(days=BACKFILL_DAYS)

    if since_dt.tzinfo is None:
        since_dt = since_dt.replace(tzinfo=timezone.utc)
    until_dt = now

    # Fetch
    if platform == "google":
        customer_id   = client_row.get("google_ads_customer_id") or ""
        refresh_token = client_row.get("google_ads_refresh_token") or ""
        manager_id    = client_row.get("google_ads_login_customer_id") or ""
        if not customer_id or not refresh_token:
            _save_cursor(sb, client_id, platform, "READY", now, 0)
            return {"status": "READY", "note": "no_google_creds", "inserted": 0, "skipped": 0}

        from ..services.google_ads_change_history import fetch_change_events
        fetch_status, raw_events = fetch_change_events(
            customer_id=customer_id,
            refresh_token=refresh_token,
            since_dt=since_dt,
            until_dt=until_dt,
            manager_id=manager_id or None,
        )
        account_id = customer_id

    elif platform == "meta":
        account_id   = client_row.get("meta_ad_account_id") or ""
        access_token = client_row.get("meta_access_token") or ""
        if not account_id or not access_token:
            _save_cursor(sb, client_id, platform, "READY", now, 0)
            return {"status": "READY", "note": "no_meta_creds", "inserted": 0, "skipped": 0}

        from ..services.meta_ads_change_history import fetch_activities
        fetch_status, raw_events = fetch_activities(
            account_id=account_id,
            access_token=access_token,
            since_dt=since_dt,
            until_dt=until_dt,
        )

    else:
        return {"status": "ERROR", "error": f"unknown platform: {platform!r}", "inserted": 0, "skipped": 0}

    if fetch_status in ("PERMISSION_DENIED", "ERROR"):
        _save_cursor(sb, client_id, platform, fetch_status, now, 0, error=f"fetch_{fetch_status}")
        return {"status": fetch_status, "inserted": 0, "skipped": 0, "fetched": 0}

    # Normalize
    detected_at = now
    normalized: list[dict] = []
    if platform == "google":
        from .change_history_normalizer import normalize_google_event
        for event in raw_events:
            try:
                row = normalize_google_event(event, client_id, account_id, detected_at)
                if row is not None:
                    normalized.append(row)
            except Exception as exc:
                logger.debug("change_history: normalize_google failed: %s", exc)
    else:
        from .change_history_normalizer import normalize_meta_activity
        for activity in raw_events:
            try:
                row = normalize_meta_activity(activity, client_id, account_id, detected_at)
                if row is not None:
                    normalized.append(row)
            except Exception as exc:
                logger.debug("change_history: normalize_meta failed: %s", exc)

    # Dedup + insert
    inserted, skipped, inserted_rows = _dedup_and_insert(sb, normalized)

    # Task matching (best-effort, POSSIBLE_MATCH only)
    matched = 0
    for row in inserted_rows:
        row_id      = row.get("id") or ""
        occurred_at = row.get("occurred_at") or ""
        if not row_id:
            continue
        status = _try_match(sb, client_id, occurred_at)
        if status == "POSSIBLE_MATCH":
            _update_match_status(sb, row_id, status)
            matched += 1

    _save_cursor(sb, client_id, platform, "READY", now, inserted)
    return {
        "status":     "READY",
        "fetched":    len(raw_events),
        "normalized": len(normalized),
        "inserted":   inserted,
        "skipped":    skipped,
        "matched":    matched,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def ingest_for_client(client_id: str, sb) -> dict:
    """
    Run change history ingest for all platforms of one client.
    Returns combined stats dict. Never raises.
    """
    now = datetime.now(timezone.utc)

    # Load and decrypt client credentials
    try:
        res = (
            sb.table("clients")
            .select(
                "id, client_id, is_active, "
                "google_ads_customer_id, google_ads_refresh_token, google_ads_login_customer_id, "
                "meta_ad_account_id, meta_access_token"
            )
            .eq("client_id", client_id)
            .eq("is_active", True)
            .maybe_single()
            .execute()
        )
        client_row = res.data if res else None
    except Exception as exc:
        return {"error": str(exc), "google": None, "meta": None}

    if not client_row:
        return {"error": "client_not_found", "google": None, "meta": None}

    from ..services.crypto import decrypt_client_secrets
    decrypt_client_secrets(client_row)

    google_stats = _ingest_platform(sb, client_row, "google", now)
    meta_stats   = _ingest_platform(sb, client_row, "meta", now)

    return {
        "client_id":       client_id,
        "google":          google_stats,
        "meta":            meta_stats,
        "total_inserted":  (google_stats.get("inserted") or 0) + (meta_stats.get("inserted") or 0),
        "total_skipped":   (google_stats.get("skipped")  or 0) + (meta_stats.get("skipped")  or 0),
    }
