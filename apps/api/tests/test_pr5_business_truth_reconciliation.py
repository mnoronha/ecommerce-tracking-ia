"""
PR #5 Isolated Unit Tests — Business Truth Collector, Shopify Writer, and Read-Only Reconciliation Ledger (PRD §6 / R06).

Covers:
  1. POS / offline channel exclusion (`order_source_name` & legacy `platform_source`)
  2. Cancellation exclusion (`cancelled_at IS NOT NULL`, `voided`, status precedence preventing resurrection)
  3. Refund deduction (`public.refunds` net revenue, clamped >= 0, idempotent `write_refund` + inline `sync_order_refunds`)
  4. Non-order webhook guard (`EventType.CUSTOM` / `EventType.ORDER_REFUNDED` never pollute `public.orders`)
  5. Timezone `America/Sao_Paulo` boundaries + `paid_at` anchor with safe historical fallback to `created_at`
  6. Read-only Shopify Reconciliation Ledger (discrepancies, Oct 1-15 backfill enrichment plan, admin auth 401/403/200)
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.config import settings
from app.core.collectors.business import collect_business, compute_local_period_utc_bounds
from app.core.shopify_reconciliation import SCHEMA_VERSION, build_shopify_reconciliation_ledger
from app.models.events import EventType, NormalizedEvent, OrderData
from app.services import writer
from app.services.adapters.shopify_adapter import ShopifyAdapter

CLIENT_UUID = "3e20e8b9-c1b5-449f-bc5c-eb0a00704387"
CLIENT_SLUG = "lk-sneakers"


# ── In-memory fake Supabase DB ────────────────────────────────────────────────


class _FakeTable:
    def __init__(self, rows: list[dict], name: str):
        self._rows = rows
        self._name = name
        self._op = "select"
        self._payload: Any = None
        self._on_conflict: str | None = None
        self._filters: list[tuple[str, str, Any]] = []
        self._limit: int | None = None

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def insert(self, payload: dict):
        self._op = "insert"
        self._payload = dict(payload)
        return self

    def upsert(self, payload: dict, on_conflict: str | None = None):
        self._op = "upsert"
        self._payload = dict(payload)
        self._on_conflict = on_conflict
        return self

    def update(self, payload: dict):
        self._op = "update"
        self._payload = dict(payload)
        return self

    def eq(self, col: str, val: Any):
        self._filters.append(("eq", col, val))
        return self

    def neq(self, col: str, val: Any):
        self._filters.append(("neq", col, val))
        return self

    def gte(self, col: str, val: Any):
        self._filters.append(("gte", col, val))
        return self

    def lte(self, col: str, val: Any):
        self._filters.append(("lte", col, val))
        return self

    def in_(self, col: str, vals: list[Any]):
        self._filters.append(("in", col, set(vals)))
        return self

    def or_(self, _expr: str):
        # Filtering on exact local period is performed in Python by collect_business;
        # returning all rows matching preceding eq/in filters simulates broad DB fetch.
        return self

    def limit(self, n: int):
        self._limit = n
        return self

    def _matches(self, row: dict) -> bool:
        for op, col, val in self._filters:
            rv = row.get(col)
            if op == "eq" and rv != val:
                return False
            if op == "neq" and rv == val:
                return False
            if op == "gte" and str(rv or "") < str(val):
                return False
            if op == "lte" and str(rv or "") > str(val):
                return False
            if op == "in" and rv not in val and str(rv or "") not in val:
                return False
        return True

    def execute(self):
        if self._op == "select":
            out = [dict(r) for r in self._rows if self._matches(r)]
            if self._limit is not None:
                out = out[: self._limit]
            return MagicMock(data=out, count=len(out))

        if self._op == "insert":
            rec = dict(self._payload)
            rec.setdefault("id", f"{self._name}-{len(self._rows) + 1}")
            self._rows.append(rec)
            return MagicMock(data=[dict(rec)])

        if self._op == "upsert":
            rec = dict(self._payload)
            if self._on_conflict == "client_id,platform_order_id":
                for existing in self._rows:
                    if (
                        existing.get("client_id") == rec.get("client_id")
                        and existing.get("platform_order_id") == rec.get("platform_order_id")
                    ):
                        existing.update(rec)
                        return MagicMock(data=[dict(existing)])
            rec.setdefault("id", f"{self._name}-{len(self._rows) + 1}")
            self._rows.append(rec)
            return MagicMock(data=[dict(rec)])

        if self._op == "update":
            updated = []
            for r in self._rows:
                if self._matches(r):
                    r.update(self._payload)
                    updated.append(dict(r))
            return MagicMock(data=updated)

        return MagicMock(data=[])


class _FakeDB:
    def __init__(self, tables: dict[str, list[dict]] | None = None):
        self.tables: dict[str, list[dict]] = tables or {}

    def table(self, name: str) -> _FakeTable:
        self.tables.setdefault(name, [])
        return _FakeTable(self.tables[name], name)


# ── 1. Business Truth Collector (POS, Cancellations, Refunds, America/Sao_Paulo, paid_at) ──


class TestBusinessTruthCollectorR06:
    def test_excludes_pos_draft_cancelled_deducts_refunds_and_uses_sao_paulo_paid_at(self):
        # October 2026 in America/Sao_Paulo (UTC-3):
        #   2026-10-01 00:00 BRT == 2026-10-01T03:00:00Z
        #   2026-10-31 23:59 BRT == 2026-11-01T02:59:59Z
        db = _FakeDB({
            "orders": [
                # 1. Valid digital paid order with paid_at in Oct BRT -> +1000.00
                {
                    "id": "ord-web-1",
                    "client_id": CLIENT_UUID,
                    "platform_source": "shopify",
                    "order_source_name": "web",
                    "financial_status": "paid",
                    "total_price": 1000.00,
                    "created_at": "2026-10-10T15:00:00+00:00",
                    "paid_at": "2026-10-10T15:05:00+00:00",
                    "cancelled_at": None,
                },
                # 2. POS physical store order (platform_source='shopify', order_source_name='pos') -> EXCLUDED
                {
                    "id": "ord-pos-1",
                    "client_id": CLIENT_UUID,
                    "platform_source": "shopify",
                    "order_source_name": "pos",
                    "financial_status": "paid",
                    "total_price": 5000.00,
                    "created_at": "2026-10-12T18:00:00+00:00",
                    "paid_at": "2026-10-12T18:00:00+00:00",
                    "cancelled_at": None,
                },
                # 3. Draft / manual Shopify order -> EXCLUDED
                {
                    "id": "ord-draft-1",
                    "client_id": CLIENT_UUID,
                    "platform_source": "shopify",
                    "order_source_name": "shopify_draft_order",
                    "financial_status": "paid",
                    "total_price": 3000.00,
                    "created_at": "2026-10-14T12:00:00+00:00",
                    "paid_at": "2026-10-14T12:00:00+00:00",
                    "cancelled_at": None,
                },
                # 4. Cancelled order still carrying financial_status='paid' -> EXCLUDED via cancelled_at
                {
                    "id": "ord-cancelled-1",
                    "client_id": CLIENT_UUID,
                    "platform_source": "shopify",
                    "order_source_name": "web",
                    "financial_status": "paid",
                    "total_price": 2500.00,
                    "created_at": "2026-10-15T14:00:00+00:00",
                    "paid_at": "2026-10-15T14:01:00+00:00",
                    "cancelled_at": "2026-10-16T10:00:00+00:00",
                },
                # 5. Partially refunded order (800 - 250 refund = 550 net) -> INCLUDED (+550.00)
                {
                    "id": "ord-partial-ref",
                    "client_id": CLIENT_UUID,
                    "platform_source": "shopify",
                    "order_source_name": "web",
                    "financial_status": "partially_refunded",
                    "total_price": 800.00,
                    "created_at": "2026-10-18T14:00:00+00:00",
                    "paid_at": "2026-10-18T14:02:00+00:00",
                    "cancelled_at": None,
                },
                # 6. Timezone boundary: paid 2026-10-01T01:30:00Z = 2026-09-30 22:30 BRT -> EXCLUDED from Oct!
                {
                    "id": "ord-sep-brt",
                    "client_id": CLIENT_UUID,
                    "platform_source": "shopify",
                    "order_source_name": "web",
                    "financial_status": "paid",
                    "total_price": 777.00,
                    "created_at": "2026-10-01T01:30:00+00:00",
                    "paid_at": "2026-10-01T01:30:00+00:00",
                    "cancelled_at": None,
                },
                # 7. Timezone boundary: paid 2026-11-01T02:15:00Z = 2026-10-31 23:15 BRT -> INCLUDED (+450.00)
                {
                    "id": "ord-oct-late-brt",
                    "client_id": CLIENT_UUID,
                    "platform_source": "shopify",
                    "order_source_name": "web",
                    "financial_status": "paid",
                    "total_price": 450.00,
                    "created_at": "2026-11-01T02:10:00+00:00",
                    "paid_at": "2026-11-01T02:15:00+00:00",
                    "cancelled_at": None,
                },
                # 8. Created Sep 30, paid Oct 01 BRT (PIX paid next day) -> INCLUDED via paid_at (+600.00)
                {
                    "id": "ord-pix-next-day",
                    "client_id": CLIENT_UUID,
                    "platform_source": "shopify",
                    "order_source_name": "web",
                    "financial_status": "paid",
                    "total_price": 600.00,
                    "created_at": "2026-09-30T22:00:00+00:00",
                    "paid_at": "2026-10-01T12:00:00+00:00",
                    "cancelled_at": None,
                },
                # 9. Historical pre-089 row with paid_at=None, order_source_name=None -> safe fallback to created_at (+300.00)
                {
                    "id": "ord-legacy-fallback",
                    "client_id": CLIENT_UUID,
                    "platform_source": "shopify",
                    "order_source_name": None,
                    "financial_status": "paid",
                    "total_price": 300.00,
                    "created_at": "2026-10-05T14:00:00+00:00",
                    "paid_at": None,
                    "cancelled_at": None,
                },
            ],
            "refunds": [
                {"id": "ref-1", "client_id": CLIENT_UUID, "order_id": "ord-partial-ref", "amount": 250.00},
            ],
        })

        res = collect_business(
            client_uuid=CLIENT_UUID,
            client_id=CLIENT_SLUG,
            period_start=date(2026, 10, 1),
            period_end=date(2026, 10, 31),
            client_timezone="America/Sao_Paulo",
            db=db,
        )

        # Eligible: ord-web-1 (1000) + ord-partial-ref (800-250=550) + ord-oct-late-brt (450)
        #           + ord-pix-next-day (600) + ord-legacy-fallback (300) = 2900.00 across 5 orders
        assert res.error is None
        assert res.aggregates["orders_count"] == 5.0
        assert res.aggregates["revenue_business"] == 2900.00
        ids = {r["id"] for r in res.rows}
        assert ids == {
            "ord-web-1",
            "ord-partial-ref",
            "ord-oct-late-brt",
            "ord-pix-next-day",
            "ord-legacy-fallback",
        }

    def test_local_period_bounds_america_sao_paulo(self):
        start_utc, end_utc, tz = compute_local_period_utc_bounds(
            date(2026, 10, 1), date(2026, 10, 31), "America/Sao_Paulo"
        )
        assert str(tz) == "America/Sao_Paulo"
        assert start_utc == datetime(2026, 10, 1, 3, 0, 0, tzinfo=timezone.utc)
        assert end_utc.year == 2026 and end_utc.month == 11 and end_utc.day == 1 and end_utc.hour == 2


# ── 2. Shopify Adapter + Writer Lifecycle Protection ──────────────────────────


class TestWriterBusinessTruthLifecycle:
    def test_writer_persists_source_name_paid_at_cancelled_at_and_blocks_resurrection(self):
        db = _FakeDB()
        adapter = ShopifyAdapter()

        # 1. orders/paid from POS
        paid_payload = {
            "id": 7700112233,
            "order_number": 9501,
            "source_name": "pos",
            "financial_status": "paid",
            "total_price": "1999.90",
            "currency": "BRL",
            "created_at": "2026-10-20T14:00:00-03:00",
            "processed_at": "2026-10-20T14:01:00-03:00",
        }
        ev_paid = adapter.normalize(paid_payload, CLIENT_SLUG, {"x-shopify-topic": "orders/paid"})

        # 2. orders/cancelled for same order
        cancel_payload = {
            **paid_payload,
            "financial_status": "voided",
            "cancelled_at": "2026-10-21T10:00:00-03:00",
        }
        ev_cancel = adapter.normalize(cancel_payload, CLIENT_SLUG, {"x-shopify-topic": "orders/cancelled"})

        # 3. Stale orders/updated carrying financial_status='paid' (must NOT resurrect!)
        stale_payload = {
            **paid_payload,
            "financial_status": "paid",
            "cancelled_at": None,
        }
        ev_stale = adapter.normalize(stale_payload, CLIENT_SLUG, {"x-shopify-topic": "orders/updated"})

        with patch("app.services.writer.get_supabase", return_value=db):
            order_uuid = writer.write_order(CLIENT_UUID, None, ev_paid)
            assert order_uuid is not None
            saved = db.tables["orders"][0]
            assert saved["order_source_name"] == "pos"
            assert saved["paid_at"] == "2026-10-20T14:01:00-03:00"
            assert saved["cancelled_at"] is None
            assert saved["financial_status"] == "paid"

            # Apply cancellation
            writer.write_order(CLIENT_UUID, None, ev_cancel)
            assert db.tables["orders"][0]["financial_status"] == "voided"
            assert db.tables["orders"][0]["cancelled_at"] == "2026-10-21T10:00:00-03:00"

            # Apply stale update — must keep voided & cancelled_at!
            writer.write_order(CLIENT_UUID, None, ev_stale)
            assert db.tables["orders"][0]["financial_status"] == "voided"
            assert db.tables["orders"][0]["cancelled_at"] == "2026-10-21T10:00:00-03:00"

    def test_writer_skips_custom_and_refund_events_and_deduplicates_refunds(self):
        db = _FakeDB({
            "orders": [
                {
                    "id": "ord-uuid-1",
                    "client_id": CLIENT_UUID,
                    "platform_order_id": "77990011",
                    "total_price": 1000.00,
                    "financial_status": "paid",
                }
            ],
            "refunds": [],
        })
        adapter = ShopifyAdapter()

        # Unknown / custom topic must NEVER insert a zero-price NULL-status row in orders
        ev_custom = NormalizedEvent(
            event_id="ev-custom-1",
            event_type=EventType.CUSTOM,
            platform="shopify",
            client_id=CLIENT_SLUG,
            timestamp=datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc),
            order=OrderData(id="6861598425310", number="", status=None, total=0.0, currency="BRL"),
        )

        # refunds/create webhook
        refund_payload = {
            "id": 990011,
            "order_id": 77990011,
            "note": "Troca de numeração",
            "created_at": "2026-10-23T15:00:00Z",
            "transactions": [
                {"kind": "refund", "status": "success", "amount": "350.00", "currency": "BRL"}
            ],
        }
        ev_refund = adapter.normalize(refund_payload, CLIENT_SLUG, {"x-shopify-topic": "refunds/create"})

        with patch("app.services.writer.get_supabase", return_value=db):
            # write_order must ignore CUSTOM and ORDER_REFUNDED
            assert writer.write_order(CLIENT_UUID, None, ev_custom) is None
            assert writer.write_order(CLIENT_UUID, None, ev_refund) is None
            assert len(db.tables["orders"]) == 1

            # write_refund called twice -> idempotent single refund row + status partially_refunded
            assert writer.write_refund(CLIENT_UUID, ev_refund) == "ord-uuid-1"
            assert writer.write_refund(CLIENT_UUID, ev_refund) == "ord-uuid-1"
            assert len(db.tables["refunds"]) == 1
            assert db.tables["refunds"][0]["amount"] == 350.0
            assert db.tables["refunds"][0]["is_partial"] is True
            assert db.tables["orders"][0]["financial_status"] == "partially_refunded"
            assert db.tables["orders"][0]["total_price"] == 1000.00


# ── 3. Read-Only Shopify Reconciliation Ledger (PRD §6 / R06) ─────────────────


class TestShopifyReconciliationLedger:
    def test_reconciliation_ledger_detects_pos_cancelled_missing_refunds_and_tz_shifts_readonly(self):
        db = _FakeDB({
            "clients": [
                {
                    "id": CLIENT_UUID,
                    "client_id": CLIENT_SLUG,
                    "pixel_id": "lk-sneakers-px",
                    "name": "LK Sneakers",
                    "timezone": "America/Sao_Paulo",
                    "currency": "BRL",
                    "shopify_domain": "lksneakers.myshopify.com",
                    "shopify_access_token": "shpat_unit_test_only",
                }
            ],
            "orders": [
                # Order 1: Oct 05 backfill missing order_source_name & paid_at — actually POS in Shopify!
                {
                    "id": "co-1",
                    "client_id": CLIENT_UUID,
                    "platform_order_id": "1001",
                    "platform_order_number": "",
                    "platform_source": "shopify",
                    "order_source_name": None,
                    "financial_status": "paid",
                    "total_price": 4000.00,
                    "paid_at": None,
                    "cancelled_at": None,
                    "created_at": "2026-10-05T15:00:00+00:00",
                },
                # Order 2: Web order cancelled in Shopify, still paid in Core
                {
                    "id": "co-2",
                    "client_id": CLIENT_UUID,
                    "platform_order_id": "1002",
                    "platform_order_number": "9302",
                    "platform_source": "shopify",
                    "order_source_name": "web",
                    "financial_status": "paid",
                    "total_price": 1500.00,
                    "paid_at": "2026-10-12T15:00:00+00:00",
                    "cancelled_at": None,
                    "created_at": "2026-10-12T15:00:00+00:00",
                },
                # Order 3: Web order with R$ 300 refund in Shopify, 0 in Core refunds
                {
                    "id": "co-3",
                    "client_id": CLIENT_UUID,
                    "platform_order_id": "1003",
                    "platform_order_number": "9303",
                    "platform_source": "shopify",
                    "order_source_name": "web",
                    "financial_status": "paid",
                    "total_price": 2000.00,
                    "paid_at": "2026-10-18T15:00:00+00:00",
                    "cancelled_at": None,
                    "created_at": "2026-10-18T15:00:00+00:00",
                },
            ],
            "refunds": [],
        })

        shopify_orders = [
            {
                "id": 1001,
                "order_number": 9201,
                "source_name": "pos",
                "financial_status": "paid",
                "total_price": "4000.00",
                "created_at": "2026-10-05T15:00:00+00:00",
                "processed_at": "2026-10-05T15:00:00+00:00",
                "cancelled_at": None,
                "test": False,
                "refunds": [],
            },
            {
                "id": 1002,
                "order_number": 9302,
                "source_name": "web",
                "financial_status": "voided",
                "total_price": "1500.00",
                "created_at": "2026-10-12T15:00:00+00:00",
                "processed_at": "2026-10-12T15:00:00+00:00",
                "cancelled_at": "2026-10-13T10:00:00+00:00",
                "test": False,
                "refunds": [],
            },
            {
                "id": 1003,
                "order_number": 9303,
                "source_name": "web",
                "financial_status": "partially_refunded",
                "total_price": "2000.00",
                "created_at": "2026-10-18T15:00:00+00:00",
                "processed_at": "2026-10-18T15:00:00+00:00",
                "cancelled_at": None,
                "test": False,
                "refunds": [
                    {
                        "id": 5001,
                        "created_at": "2026-10-19T12:00:00+00:00",
                        "transactions": [
                            {"kind": "refund", "status": "success", "amount": "300.00", "currency": "BRL"}
                        ],
                    }
                ],
            },
            # Order 4: Missing from Core + crosses UTC/BRT boundary (Nov 1 01:30Z = Oct 31 22:30 BRT)
            {
                "id": 1004,
                "order_number": 9499,
                "source_name": "web",
                "financial_status": "paid",
                "total_price": "900.00",
                "created_at": "2026-11-01T01:30:00+00:00",
                "processed_at": "2026-11-01T01:30:00+00:00",
                "cancelled_at": None,
                "test": False,
                "refunds": [],
            },
        ]

        ledger = build_shopify_reconciliation_ledger(
            CLIENT_SLUG,
            date(2026, 10, 1),
            date(2026, 10, 31),
            db=db,
            shopify_orders_override=shopify_orders,
        )

        assert ledger["schema_version"] == SCHEMA_VERSION
        assert ledger["read_only"] is True
        assert ledger["reconciled"] is False

        # Shopify digital paid: 1003 (2000 - 300 = 1700) + 1004 (900) = 2600 across 2 orders
        assert ledger["shopify_summary"]["digital_paid_orders"] == 2
        assert ledger["shopify_summary"]["pos_orders"] == 1
        assert ledger["shopify_summary"]["cancelled_orders"] == 1
        assert ledger["shopify_summary"]["net_digital_revenue"] == 2600.00

        # Core had 3 paid rows totaling 7500 (including 4000 POS + 1500 cancelled + 300 un-deducted refund)
        assert ledger["core_summary"]["paid_digital_rows"] == 3
        assert ledger["core_summary"]["net_revenue"] == 7500.00
        assert ledger["delta"]["revenue_diff"] == 4900.00

        disc = ledger["discrepancies"]
        assert disc["pos_polluting_core_count"] == 1
        assert disc["cancelled_still_paid_count"] == 1
        assert disc["refund_gaps_count"] == 1
        assert disc["missing_in_core_count"] == 1
        assert disc["timezone_boundary_shifts_count"] == 1

        # Enrichment plan generated for 1001 (order_source_name='pos' + paid_at), 1002 (cancelled_at), 1003 (missing_refunds)
        assert ledger["enrichment_plan_count"] == 3
        # Guarantee zero mutation of DB rows (100% read-only)
        assert db.tables["orders"][0]["order_source_name"] is None
        assert db.tables["orders"][1]["cancelled_at"] is None
        assert len(db.tables["refunds"]) == 0

    def test_admin_reconciliation_endpoint_requires_agency_admin(self):
        from app.main import app

        client = TestClient(app)
        fake_ledger = {"schema_version": SCHEMA_VERSION, "read_only": True, "reconciled": True}

        with (
            patch.object(settings, "AGENCY_API_ADMIN_KEY", "test-admin-key"),
            patch.object(settings, "AGENCY_API_HERMES_KEY", "test-hermes-key"),
            patch.object(settings, "AGENCY_API_PLATFORM_KEY", "test-platform-key"),
            patch(
                "app.core.shopify_reconciliation.build_shopify_reconciliation_ledger",
                return_value=fake_ledger,
            ),
        ):
            r_unauth = client.get(
                "/agency/v1/admin/clients/lk-sneakers/reconciliation-ledger?period_start=2026-10-01&period_end=2026-10-31"
            )
            assert r_unauth.status_code == 401

            r_hermes = client.get(
                "/agency/v1/admin/clients/lk-sneakers/reconciliation-ledger?period_start=2026-10-01&period_end=2026-10-31",
                headers={"Authorization": "Bearer test-hermes-key"},
            )
            assert r_hermes.status_code == 403

            r_platform = client.get(
                "/agency/v1/admin/clients/lk-sneakers/reconciliation-ledger?period_start=2026-10-01&period_end=2026-10-31",
                headers={"Authorization": "Bearer test-platform-key"},
            )
            assert r_platform.status_code == 403

            r_admin = client.get(
                "/agency/v1/admin/clients/lk-sneakers/reconciliation-ledger?period_start=2026-10-01&period_end=2026-10-31",
                headers={"Authorization": "Bearer test-admin-key"},
            )
            assert r_admin.status_code == 200
            assert r_admin.json()["read_only"] is True
