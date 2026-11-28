"""
Read-Only Shopify Reconciliation Ledger (PRD §6 / R06).

Compares Shopify Admin API (Source of Record, read-only GET) against Core
`public.orders` and `public.refunds` for an arbitrary client and date window
in the store's local timezone (e.g. `America/Sao_Paulo`).

Design invariants (AGENTS.md & PRD §6):
  - 100% READ-ONLY: NEVER mutates Shopify or Supabase (`public.orders` / `public.refunds`).
  - Never fabricates metrics or silently overwrites history.
  - Produces a deterministic discrepancy ledger + a non-destructive `enrichment_plan`
    identifying missing `order_source_name` (e.g. LK Sneakers Oct 1–15 backfill),
    missing `paid_at`, `cancelled_at`, POS orders polluting digital revenue,
    cancelled orders still marked `paid`, refund gaps, and UTC vs BRT boundary shifts.
  - Supports injectable `http_get` and `db` for 100% isolated unit tests.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

import httpx

from ..database import get_supabase
from ..services import crypto
from .collectors.business import (
    ELIGIBLE_FINANCIAL_STATUSES,
    OFFLINE_SOURCE_NAMES,
    _parse_ts_utc,
    compute_local_period_utc_bounds,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "noro.shopify-reconciliation-ledger.v1"
_SHOPIFY_API_VERSION = "2024-10"
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_RECONCILE_FIELDS = (
    "id,name,order_number,created_at,processed_at,cancelled_at,updated_at,"
    "source_name,financial_status,fulfillment_status,total_price,subtotal_price,"
    "total_discounts,total_tax,total_shipping_price_set,currency,test,refunds"
)


def _parse_next_link(link_header: str) -> Optional[str]:
    for part in (link_header or "").split(","):
        url_part, *rel_parts = part.strip().split(";")
        if any("next" in r for r in rel_parts):
            return url_part.strip().strip("<>")
    return None


def _extract_shopify_refund_total(raw_order: dict) -> tuple[float, list[dict]]:
    """
    Compute total successful refund amount from a Shopify order's `refunds` array.
    Returns `(total_refunded, compact_refund_entries)`.
    """
    total = 0.0
    entries: list[dict] = []
    for ref in (raw_order.get("refunds") or []):
        if not isinstance(ref, dict):
            continue
        rid = str(ref.get("id") or "")
        ref_amt = 0.0
        for tx in (ref.get("transactions") or []):
            if not isinstance(tx, dict):
                continue
            if tx.get("kind") == "refund" and tx.get("status") == "success":
                try:
                    ref_amt += float(tx.get("amount") or 0)
                except (TypeError, ValueError):
                    pass
        if ref_amt > 0:
            total += ref_amt
            entries.append({
                "platform_refund_id": rid or None,
                "amount":             round(ref_amt, 2),
                "created_at":         ref.get("created_at"),
            })
    return round(total, 2), entries


def _fetch_shopify_orders_readonly(
    domain: str,
    access_token: str,
    start_utc: datetime,
    end_utc: datetime,
    *,
    http_get: Optional[Callable[..., Any]] = None,
) -> list[dict]:
    """
    Paginate Shopify Admin API `GET /admin/api/{ver}/orders.json` (read-only).
    Uses a ±1 day buffer on `created_at_min`/`created_at_max` so boundary orders
    are never dropped before local-timezone filtering.
    """
    clean_domain = domain.strip().removeprefix("https://").removeprefix("http://").rstrip("/")
    base_url = f"https://{clean_domain}/admin/api/{_SHOPIFY_API_VERSION}/orders.json"
    buf_min = (start_utc - timedelta(days=1)).isoformat()
    buf_max = (end_utc + timedelta(days=1)).isoformat()

    next_url: Optional[str] = base_url
    params: Optional[dict] = {
        "status":         "any",
        "created_at_min": buf_min,
        "created_at_max": buf_max,
        "limit":          250,
        "fields":         _RECONCILE_FIELDS,
    }
    headers = {"X-Shopify-Access-Token": access_token}
    all_orders: list[dict] = []
    seen_urls: set[str] = set()

    for _ in range(100):
        if not next_url or next_url in seen_urls:
            break
        seen_urls.add(next_url)

        if http_get is not None:
            resp = http_get(next_url, headers=headers, params=params, timeout=30.0)
        else:
            resp = httpx.get(next_url, headers=headers, params=params, timeout=30.0)
        params = None  # Link pagination URL already carries query params

        status = int(getattr(resp, "status_code", 0) or 0)
        if status != 200:
            raise RuntimeError(f"Shopify Admin API HTTP {status}")

        payload = resp.json() if hasattr(resp, "json") else {}
        all_orders.extend(payload.get("orders") or [])

        resp_headers = getattr(resp, "headers", None) or {}
        link_hdr = resp_headers.get("Link") or resp_headers.get("link") or ""
        next_url = _parse_next_link(link_hdr)

    return all_orders


def _resolve_client_for_reconciliation(db, client_ref: str) -> Optional[dict]:
    ref = (client_ref or "").strip()
    if not ref:
        return None
    cols = (
        "id, client_id, pixel_id, name, timezone, currency, "
        "shopify_domain, shopify_store_domain, shopify_access_token"
    )
    order = ("id", "client_id", "pixel_id") if _UUID_RE.match(ref) else ("client_id", "pixel_id")
    for field in order:
        try:
            res = db.table("clients").select(cols).eq(field, ref).limit(1).execute()
            if res and res.data:
                row = dict(res.data[0])
                try:
                    crypto.decrypt_client_secrets(row)
                except Exception:
                    pass
                return row
        except Exception as exc:
            logger.warning("shopify_reconciliation: client lookup by %s failed: %s", field, exc)
    return None


def _load_core_orders_and_refunds(
    db,
    client_uuid: str,
    start_utc: datetime,
    end_utc: datetime,
) -> tuple[list[dict], dict[str, float]]:
    """Read Core `public.orders` and `public.refunds` around the target window."""
    buf_min = (start_utc - timedelta(days=1)).isoformat()
    buf_max = (end_utc + timedelta(days=1)).isoformat()

    cols_v2 = (
        "id, platform_order_id, platform_order_number, platform_source, "
        "order_source_name, financial_status, total_price, paid_at, created_at, cancelled_at"
    )
    try:
        ores = (
            db.table("orders")
            .select(cols_v2)
            .eq("client_id", client_uuid)
            .gte("created_at", buf_min)
            .lte("created_at", buf_max)
            .execute()
        )
        orders = ores.data or []
    except Exception:
        ores = (
            db.table("orders")
            .select("id, platform_order_id, platform_order_number, platform_source, financial_status, total_price, created_at")
            .eq("client_id", client_uuid)
            .gte("created_at", buf_min)
            .lte("created_at", buf_max)
            .execute()
        )
        orders = ores.data or []

    order_uuids = [str(o["id"]) for o in orders if o.get("id")]
    refunds_by_order_uuid: dict[str, float] = {}
    if order_uuids:
        for idx in range(0, len(order_uuids), 200):
            chunk = order_uuids[idx : idx + 200]
            try:
                rres = (
                    db.table("refunds")
                    .select("order_id, amount")
                    .eq("client_id", client_uuid)
                    .in_("order_id", chunk)
                    .execute()
                )
                for r in (rres.data or []):
                    oid = str(r.get("order_id") or "")
                    amt = float(r.get("amount") or 0)
                    if oid and amt > 0:
                        refunds_by_order_uuid[oid] = round(refunds_by_order_uuid.get(oid, 0.0) + amt, 2)
            except Exception as exc:
                logger.warning("shopify_reconciliation: refunds read failed: %s", exc)

    return orders, refunds_by_order_uuid


def build_shopify_reconciliation_ledger(
    client_id: str,
    period_start: date,
    period_end: date,
    *,
    db=None,
    http_get: Optional[Callable[..., Any]] = None,
    shopify_orders_override: Optional[list[dict]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """
    Build a read-only Shopify ↔ Core Reconciliation Ledger for `[period_start, period_end]`.

    Never mutates Shopify or Supabase. Raises `LookupError` if client not found,
    or `ValueError` if Shopify credentials are missing (unless `shopify_orders_override` is passed).
    """
    sb = db or get_supabase()
    client = _resolve_client_for_reconciliation(sb, client_id)
    if not client:
        raise LookupError(f"client not found: {client_id!r}")

    slug = client.get("client_id") or client_id
    client_uuid = str(client["id"])
    tz_name = client.get("timezone") or "America/Sao_Paulo"
    start_utc, end_utc, tz = compute_local_period_utc_bounds(period_start, period_end, tz_name)

    if shopify_orders_override is not None:
        raw_shopify = list(shopify_orders_override)
    else:
        domain = (client.get("shopify_domain") or client.get("shopify_store_domain") or "").strip()
        token = (client.get("shopify_access_token") or "").strip()
        if not domain or not token:
            raise ValueError(f"Shopify credentials missing for client {slug!r}")
        raw_shopify = _fetch_shopify_orders_readonly(
            domain, token, start_utc, end_utc, http_get=http_get
        )

    raw_core, core_refunds_by_uuid = _load_core_orders_and_refunds(
        sb, client_uuid, start_utc, end_utc
    )

    # Index Shopify orders in local period
    shopify_by_id: dict[str, dict] = {}
    timezone_boundary_shifts: list[dict] = []

    sh_all = 0
    sh_digital_paid = 0
    sh_pos = 0
    sh_draft = 0
    sh_cancelled = 0
    sh_test = 0
    sh_gross_digital = 0.0
    sh_refunds_digital = 0.0
    sh_net_digital = 0.0

    for so in raw_shopify:
        oid = str(so.get("id") or "")
        if not oid:
            continue
        eff_dt = _parse_ts_utc(so.get("processed_at")) or _parse_ts_utc(so.get("created_at"))
        if eff_dt is None:
            continue
        local_date = eff_dt.astimezone(tz).date()
        utc_date = eff_dt.date()
        in_local = period_start <= local_date <= period_end
        in_utc = period_start <= utc_date <= period_end

        if in_local != in_utc:
            timezone_boundary_shifts.append({
                "platform_order_id": oid,
                "order_number":      str(so.get("order_number") or so.get("name") or ""),
                "utc_date":          utc_date.isoformat(),
                "local_date":        local_date.isoformat(),
                "included_in_local": in_local,
                "total_price":       float(so.get("total_price") or 0),
            })
        if not in_local:
            continue

        src = (so.get("source_name") or "").strip().lower()
        fin = (so.get("financial_status") or "").strip().lower()
        is_test = bool(so.get("test"))
        is_cancelled = bool(so.get("cancelled_at"))
        is_offline = src in OFFLINE_SOURCE_NAMES
        total_price = float(so.get("total_price") or 0.0)
        refund_total, refund_entries = _extract_shopify_refund_total(so)
        net_price = max(0.0, total_price - min(refund_total, max(total_price, 0.0)))

        is_digital_paid = (
            not is_test
            and not is_cancelled
            and not is_offline
            and fin in ELIGIBLE_FINANCIAL_STATUSES
        )

        sh_all += 1
        if is_test:
            sh_test += 1
        if is_cancelled:
            sh_cancelled += 1
        if src == "pos":
            sh_pos += 1
        elif src in ("shopify_draft_order", "draft_orders", "draft"):
            sh_draft += 1
        if is_digital_paid:
            sh_digital_paid += 1
            sh_gross_digital += total_price
            sh_refunds_digital += min(refund_total, max(total_price, 0.0))
            sh_net_digital += net_price

        shopify_by_id[oid] = {
            "platform_order_id": oid,
            "order_number":      str(so.get("order_number") or ""),
            "source_name":       src or None,
            "financial_status":  fin or None,
            "is_test":           is_test,
            "is_cancelled":      is_cancelled,
            "is_offline":        is_offline,
            "is_digital_paid":   is_digital_paid,
            "total_price":       round(total_price, 2),
            "refund_total":      refund_total,
            "refund_entries":    refund_entries,
            "net_price":         round(net_price, 2),
            "paid_at":           so.get("processed_at") or so.get("created_at") if fin in ("paid", "partially_refunded", "refunded") else None,
            "cancelled_at":      so.get("cancelled_at"),
            "created_at":        so.get("created_at"),
        }

    # Index Core orders in local period (using paid_at -> created_at fallback)
    core_by_id: dict[str, dict] = {}
    core_paid_rows = 0
    core_gross = 0.0
    core_refunds = 0.0
    core_net = 0.0
    missing_source_name = 0
    missing_paid_at = 0

    for co in raw_core:
        oid = str(co.get("platform_order_id") or "")
        if not oid:
            continue
        eff_dt = _parse_ts_utc(co.get("paid_at")) or _parse_ts_utc(co.get("created_at"))
        if eff_dt is None:
            continue
        local_date = eff_dt.astimezone(tz).date()
        if not (period_start <= local_date <= period_end):
            continue

        ouuid = str(co.get("id") or "")
        fin = (co.get("financial_status") or "").strip().lower()
        src_name = (co.get("order_source_name") or "").strip().lower() or None
        total = float(co.get("total_price") or 0.0)
        ref_amt = core_refunds_by_uuid.get(ouuid, 0.0)
        is_cancelled = bool(co.get("cancelled_at"))
        is_offline = (src_name in OFFLINE_SOURCE_NAMES) or (
            (co.get("platform_source") or "").strip().lower() in OFFLINE_SOURCE_NAMES
        )

        if fin in ELIGIBLE_FINANCIAL_STATUSES and not is_cancelled and not is_offline:
            core_paid_rows += 1
            core_gross += total
            deducted = min(ref_amt, max(total, 0.0))
            core_refunds += deducted
            core_net += max(0.0, total - deducted)
            if not src_name:
                missing_source_name += 1
            if not co.get("paid_at"):
                missing_paid_at += 1

        core_by_id[oid] = {
            "order_uuid":        ouuid,
            "platform_order_id": oid,
            "order_source_name": src_name,
            "financial_status":  fin or None,
            "total_price":       round(total, 2),
            "refund_recorded":   ref_amt,
            "paid_at":           co.get("paid_at"),
            "cancelled_at":      co.get("cancelled_at"),
            "created_at":        co.get("created_at"),
        }

    # Build discrepancy lists & non-destructive enrichment plan
    missing_in_core: list[dict] = []
    extra_in_core: list[dict] = []
    pos_polluting_core: list[dict] = []
    cancelled_still_paid_in_core: list[dict] = []
    refund_gaps: list[dict] = []
    amount_mismatches: list[dict] = []
    enrichment_plan: list[dict] = []

    for oid, sh in shopify_by_id.items():
        co = core_by_id.get(oid)
        if co is None:
            if sh["is_digital_paid"]:
                missing_in_core.append({
                    "platform_order_id": oid,
                    "order_number":      sh["order_number"],
                    "source_name":       sh["source_name"],
                    "total_price":       sh["total_price"],
                    "net_price":         sh["net_price"],
                    "paid_at":           sh["paid_at"],
                })
            continue

        co_fin = co["financial_status"] or ""
        if sh["is_offline"] and co_fin in ELIGIBLE_FINANCIAL_STATUSES and not co["order_source_name"]:
            pos_polluting_core.append({
                "platform_order_id": oid,
                "order_number":      sh["order_number"],
                "shopify_source":    sh["source_name"],
                "total_price":       co["total_price"],
            })

        if sh["is_cancelled"] and co_fin in ELIGIBLE_FINANCIAL_STATUSES and not co["cancelled_at"]:
            cancelled_still_paid_in_core.append({
                "platform_order_id": oid,
                "order_number":      sh["order_number"],
                "shopify_cancelled_at": sh["cancelled_at"],
                "core_financial_status": co_fin,
                "total_price":       co["total_price"],
            })

        if sh["refund_total"] > 0 and abs(sh["refund_total"] - co["refund_recorded"]) > 0.01:
            refund_gaps.append({
                "platform_order_id": oid,
                "shopify_refunds":   sh["refund_total"],
                "core_refunds":      co["refund_recorded"],
                "delta":             round(sh["refund_total"] - co["refund_recorded"], 2),
            })

        if abs(sh["total_price"] - co["total_price"]) > 0.01:
            amount_mismatches.append({
                "platform_order_id": oid,
                "shopify_total":     sh["total_price"],
                "core_total":        co["total_price"],
                "delta":             round(co["total_price"] - sh["total_price"], 2),
            })

        # Non-destructive enrichment plan (never applied here!)
        patch_fields: dict[str, Any] = {}
        if sh["source_name"] and co["order_source_name"] != sh["source_name"]:
            patch_fields["order_source_name"] = sh["source_name"]
        if sh["paid_at"] and not co["paid_at"]:
            patch_fields["paid_at"] = sh["paid_at"]
        if sh["cancelled_at"] and not co["cancelled_at"]:
            patch_fields["cancelled_at"] = sh["cancelled_at"]
        if sh["refund_total"] > co["refund_recorded"] + 0.01:
            patch_fields["missing_refunds"] = sh["refund_entries"]
        if patch_fields:
            enrichment_plan.append({
                "platform_order_id": oid,
                "order_uuid":        co["order_uuid"],
                "patch":             patch_fields,
            })

    for oid, co in core_by_id.items():
        if oid not in shopify_by_id and (co["financial_status"] or "") in ELIGIBLE_FINANCIAL_STATUSES:
            extra_in_core.append({
                "platform_order_id": oid,
                "total_price":       co["total_price"],
                "created_at":        co["created_at"],
            })

    ts = now or datetime.now(timezone.utc)
    rev_diff = round(core_net - sh_net_digital, 2)
    ord_diff = core_paid_rows - sh_digital_paid
    reconciled = (
        abs(rev_diff) <= 1.0
        and ord_diff == 0
        and not pos_polluting_core
        and not cancelled_still_paid_in_core
        and not refund_gaps
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "read_only":      True,
        "generated_at":   ts.isoformat(),
        "client_id":      slug,
        "client_uuid":    client_uuid,
        "timezone":       str(tz),
        "period_start":   period_start.isoformat(),
        "period_end":     period_end.isoformat(),
        "reconciled":     reconciled,
        "shopify_summary": {
            "total_orders_all":     sh_all,
            "digital_paid_orders":  sh_digital_paid,
            "pos_orders":           sh_pos,
            "draft_orders":         sh_draft,
            "cancelled_orders":     sh_cancelled,
            "test_orders":          sh_test,
            "gross_digital_revenue": round(sh_gross_digital, 2),
            "refunds_deducted":     round(sh_refunds_digital, 2),
            "net_digital_revenue":  round(sh_net_digital, 2),
        },
        "core_summary": {
            "paid_digital_rows":          core_paid_rows,
            "gross_revenue":              round(core_gross, 2),
            "refunds_recorded":           round(core_refunds, 2),
            "net_revenue":                round(core_net, 2),
            "missing_source_name_count":  missing_source_name,
            "missing_paid_at_count":      missing_paid_at,
        },
        "delta": {
            "revenue_diff": rev_diff,
            "orders_diff":  ord_diff,
        },
        "discrepancies": {
            "missing_in_core_count":             len(missing_in_core),
            "extra_in_core_count":               len(extra_in_core),
            "pos_polluting_core_count":          len(pos_polluting_core),
            "cancelled_still_paid_count":        len(cancelled_still_paid_in_core),
            "refund_gaps_count":                 len(refund_gaps),
            "amount_mismatches_count":           len(amount_mismatches),
            "timezone_boundary_shifts_count":    len(timezone_boundary_shifts),
            "missing_in_core":                   missing_in_core[:100],
            "extra_in_core":                     extra_in_core[:100],
            "pos_polluting_core":                pos_polluting_core[:100],
            "cancelled_still_paid_in_core":      cancelled_still_paid_in_core[:100],
            "refund_gaps":                       refund_gaps[:100],
            "amount_mismatches":                 amount_mismatches[:100],
            "timezone_boundary_shifts":          timezone_boundary_shifts[:50],
        },
        "enrichment_plan_count": len(enrichment_plan),
        "enrichment_plan":       enrichment_plan[:250],
    }
