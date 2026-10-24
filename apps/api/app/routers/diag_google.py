"""
Temporary diagnostic router: Google Ads change_event GAQL query testing.

Runs incremental queries against the live Google Ads API for one client,
stopping at the first failure and returning the full raw API response.

Protected by x-diag-key header (must equal DEFAULT_WEBHOOK_SECRET).

DELETE this file and its include_router() in main.py once the root cause
of fetch_ERROR is confirmed and patched.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException

from ..config import settings
from ..database import get_supabase
from ..services.crypto import decrypt_client_secrets
from ..services.google_ads import _get_access_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/diag", tags=["diag"])

_ADS_API = "https://googleads.googleapis.com/v23"

# ── Incremental GAQL variants ──────────────────────────────────────────────────
# Each tuple: (variant_label, full_query_template).
# Variants A / A2 confirm the correct datetime field name first.
# The rest add one field at a time. Stop on first HTTP non-200.
_SINCE_PLACEHOLDER = "SINCE"
_UNTIL_PLACEHOLDER = "UNTIL"

_VARIANTS: list[tuple[str, str]] = [
    # A — minimal, using docs field name (change_date_time)
    ("A_change_date_time", (
        "SELECT change_event.resource_name, change_event.change_date_time "
        "FROM change_event "
        "WHERE change_event.change_date_time >= 'SINCE' "
        "  AND change_event.change_date_time < 'UNTIL' "
        "LIMIT 5"
    )),
    # A2 — same but with our current code's field name (change_datetime)
    ("A2_change_datetime_current_code", (
        "SELECT change_event.resource_name, change_event.change_datetime "
        "FROM change_event "
        "WHERE change_event.change_datetime >= 'SINCE' "
        "  AND change_event.change_datetime < 'UNTIL' "
        "LIMIT 5"
    )),
    # A_ORDER_BY — minimal A with ORDER BY (docs say this should work)
    ("A3_with_ORDER_BY", (
        "SELECT change_event.resource_name, change_event.change_date_time "
        "FROM change_event "
        "WHERE change_event.change_date_time >= 'SINCE' "
        "  AND change_event.change_date_time < 'UNTIL' "
        "ORDER BY change_event.change_date_time DESC "
        "LIMIT 5"
    )),
    # B — add change_resource_type
    ("B_change_resource_type", (
        "SELECT change_event.resource_name, change_event.change_date_time, "
        "change_event.change_resource_type "
        "FROM change_event "
        "WHERE change_event.change_date_time >= 'SINCE' "
        "  AND change_event.change_date_time < 'UNTIL' "
        "LIMIT 5"
    )),
    # C — add resource_change_operation
    ("C_resource_change_operation", (
        "SELECT change_event.resource_name, change_event.change_date_time, "
        "change_event.change_resource_type, change_event.resource_change_operation "
        "FROM change_event "
        "WHERE change_event.change_date_time >= 'SINCE' "
        "  AND change_event.change_date_time < 'UNTIL' "
        "LIMIT 5"
    )),
    # D — add client_type
    ("D_client_type", (
        "SELECT change_event.resource_name, change_event.change_date_time, "
        "change_event.change_resource_type, change_event.resource_change_operation, "
        "change_event.client_type "
        "FROM change_event "
        "WHERE change_event.change_date_time >= 'SINCE' "
        "  AND change_event.change_date_time < 'UNTIL' "
        "LIMIT 5"
    )),
    # E — add changed_fields
    ("E_changed_fields", (
        "SELECT change_event.resource_name, change_event.change_date_time, "
        "change_event.change_resource_type, change_event.resource_change_operation, "
        "change_event.client_type, change_event.changed_fields "
        "FROM change_event "
        "WHERE change_event.change_date_time >= 'SINCE' "
        "  AND change_event.change_date_time < 'UNTIL' "
        "LIMIT 5"
    )),
    # F — add old_resource
    ("F_old_resource", (
        "SELECT change_event.resource_name, change_event.change_date_time, "
        "change_event.change_resource_type, change_event.resource_change_operation, "
        "change_event.client_type, change_event.changed_fields, "
        "change_event.old_resource "
        "FROM change_event "
        "WHERE change_event.change_date_time >= 'SINCE' "
        "  AND change_event.change_date_time < 'UNTIL' "
        "LIMIT 5"
    )),
    # G — add new_resource
    ("G_new_resource", (
        "SELECT change_event.resource_name, change_event.change_date_time, "
        "change_event.change_resource_type, change_event.resource_change_operation, "
        "change_event.client_type, change_event.changed_fields, "
        "change_event.old_resource, change_event.new_resource "
        "FROM change_event "
        "WHERE change_event.change_date_time >= 'SINCE' "
        "  AND change_event.change_date_time < 'UNTIL' "
        "LIMIT 5"
    )),
    # H — add campaign
    ("H_campaign", (
        "SELECT change_event.resource_name, change_event.change_date_time, "
        "change_event.change_resource_type, change_event.resource_change_operation, "
        "change_event.client_type, change_event.changed_fields, "
        "change_event.old_resource, change_event.new_resource, "
        "change_event.campaign "
        "FROM change_event "
        "WHERE change_event.change_date_time >= 'SINCE' "
        "  AND change_event.change_date_time < 'UNTIL' "
        "LIMIT 5"
    )),
    # I — add ad_group
    ("I_ad_group", (
        "SELECT change_event.resource_name, change_event.change_date_time, "
        "change_event.change_resource_type, change_event.resource_change_operation, "
        "change_event.client_type, change_event.changed_fields, "
        "change_event.old_resource, change_event.new_resource, "
        "change_event.campaign, change_event.ad_group "
        "FROM change_event "
        "WHERE change_event.change_date_time >= 'SINCE' "
        "  AND change_event.change_date_time < 'UNTIL' "
        "LIMIT 5"
    )),
    # J — add ad
    ("J_ad", (
        "SELECT change_event.resource_name, change_event.change_date_time, "
        "change_event.change_resource_type, change_event.resource_change_operation, "
        "change_event.client_type, change_event.changed_fields, "
        "change_event.old_resource, change_event.new_resource, "
        "change_event.campaign, change_event.ad_group, change_event.ad "
        "FROM change_event "
        "WHERE change_event.change_date_time >= 'SINCE' "
        "  AND change_event.change_date_time < 'UNTIL' "
        "LIMIT 5"
    )),
    # K — add keyword
    ("K_keyword", (
        "SELECT change_event.resource_name, change_event.change_date_time, "
        "change_event.change_resource_type, change_event.resource_change_operation, "
        "change_event.client_type, change_event.changed_fields, "
        "change_event.old_resource, change_event.new_resource, "
        "change_event.campaign, change_event.ad_group, change_event.ad, "
        "change_event.keyword "
        "FROM change_event "
        "WHERE change_event.change_date_time >= 'SINCE' "
        "  AND change_event.change_date_time < 'UNTIL' "
        "LIMIT 5"
    )),
    # L — add feed_item (deprecated v17+; expected to fail)
    ("L_feed_item", (
        "SELECT change_event.resource_name, change_event.change_date_time, "
        "change_event.change_resource_type, change_event.resource_change_operation, "
        "change_event.client_type, change_event.changed_fields, "
        "change_event.old_resource, change_event.new_resource, "
        "change_event.campaign, change_event.ad_group, change_event.ad, "
        "change_event.keyword, change_event.feed_item "
        "FROM change_event "
        "WHERE change_event.change_date_time >= 'SINCE' "
        "  AND change_event.change_date_time < 'UNTIL' "
        "LIMIT 5"
    )),
]

# Also run the exact query currently in production for direct comparison
_CURRENT_PROD_QUERY = (
    "SELECT "
    "change_event.resource_name, "
    "change_event.change_datetime, "
    "change_event.change_resource_type, "
    "change_event.resource_change_operation, "
    "change_event.client_type, "
    "change_event.new_resource, "
    "change_event.old_resource, "
    "change_event.changed_fields, "
    "change_event.campaign, "
    "change_event.ad_group, "
    "change_event.ad, "
    "change_event.keyword, "
    "change_event.feed_item "
    "FROM change_event "
    "WHERE change_event.change_datetime >= 'SINCE' "
    "  AND change_event.change_datetime < 'UNTIL' "
    "LIMIT 10000"
)


def _raw_gaql(
    customer_id: str,
    refresh_token: str,
    manager_id: Optional[str],
    query: str,
    since_str: str,
    until_str: str,
) -> dict:
    """Execute one GAQL query. Returns full diagnostic dict. Never raises."""
    final_query = query.replace("SINCE", since_str).replace("UNTIL", until_str)

    access_token = _get_access_token(
        settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
        settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
        refresh_token,
    )
    if not access_token:
        return {
            "ok": False,
            "stage": "token_refresh",
            "http_status": None,
            "request_id": None,
            "body": "TOKEN_REFRESH_FAILED — check GOOGLE_ADS_OAUTH_CLIENT_ID/SECRET",
        }

    clean_cid = customer_id.replace("-", "").replace(" ", "")
    headers = {
        "Authorization":   f"Bearer {access_token}",
        "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        "Content-Type":    "application/json",
    }
    if manager_id:
        headers["login-customer-id"] = manager_id.replace("-", "").replace(" ", "")

    try:
        resp = httpx.post(
            f"{_ADS_API}/customers/{clean_cid}/googleAds:search",
            headers=headers,
            json={"query": final_query},
            timeout=20.0,
        )
    except Exception as exc:
        return {
            "ok": False,
            "stage": "http",
            "http_status": None,
            "request_id": None,
            "body": f"EXCEPTION: {exc}",
        }

    request_id = resp.headers.get("request-id") or resp.headers.get("x-request-id")
    try:
        body = resp.json()
    except Exception:
        body = resp.text[:3000]

    return {
        "ok": resp.status_code == 200,
        "stage": "api",
        "http_status": resp.status_code,
        "request_id": request_id,
        "body": body,
        "query_sent": final_query,
    }


@router.get("/google-change-history/{client_id}")
def diagnose_google_change_history(
    client_id: str,
    x_diag_key: Optional[str] = Header(default=None, alias="x-diag-key"),
) -> dict:
    """
    Runs incremental GAQL variants against the live Google Ads API for one client.
    Stops at the first non-200 and returns the full error body.
    Also runs the current production query for direct comparison.

    Auth: x-diag-key header must equal DEFAULT_WEBHOOK_SECRET.
    """
    if not settings.DEFAULT_WEBHOOK_SECRET or x_diag_key != settings.DEFAULT_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    sb = get_supabase()
    res = (
        sb.table("clients")
        .select(
            "client_id, google_ads_customer_id, google_ads_refresh_token, "
            "google_ads_login_customer_id"
        )
        .eq("client_id", client_id)
        .eq("is_active", True)
        .maybe_single()
        .execute()
    )
    row = res.data if res else None
    if not row:
        raise HTTPException(status_code=404, detail=f"client '{client_id}' not found or inactive")

    decrypt_client_secrets(row)

    customer_id   = row.get("google_ads_customer_id") or ""
    refresh_token = row.get("google_ads_refresh_token") or ""
    manager_id    = row.get("google_ads_login_customer_id") or ""

    if not customer_id or not refresh_token:
        return {"error": "client has no Google Ads credentials"}

    now       = datetime.now(timezone.utc)
    since_dt  = now - timedelta(days=7)
    since_str = since_dt.strftime("%Y-%m-%d %H:%M:%S")
    until_str = now.strftime("%Y-%m-%d %H:%M:%S")

    incremental_results: list[dict] = []
    first_failing_variant: Optional[str] = None

    for label, query_tmpl in _VARIANTS:
        result = _raw_gaql(customer_id, refresh_token, manager_id, query_tmpl, since_str, until_str)
        incremental_results.append({"variant": label, **result})
        if not result["ok"]:
            first_failing_variant = label
            break  # stop at first failure

    # Always run exact production query for comparison
    prod_result = _raw_gaql(
        customer_id, refresh_token, manager_id,
        _CURRENT_PROD_QUERY, since_str, until_str,
    )

    return {
        "google_api_version": "v23",
        "client_id": client_id,
        "customer_id": customer_id,
        "window": {"since": since_str, "until": until_str},
        "first_failing_variant": first_failing_variant,
        "incremental_results": incremental_results,
        "current_prod_query_result": {
            "variant": "PROD_CURRENT",
            **prod_result,
        },
    }
