"""
PR #4 Isolated Unit Tests — Context Bundle (R05) & Core → Hermes Event-Driven Webhook (PRD §3, §8.1, §16).

100% isolated (in-memory fakes / mocks, zero live DB / zero real network calls).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.canonical_reports import Flags, Reports
from app.core.context_bundle import SCHEMA_VERSION, build_client_context_bundle
from app.core.hermes_webhook import (
    EVENT_REPORT_WAITING,
    build_hermes_webhook_payload,
    dispatch_hermes_analysis_webhook_sync,
    sign_hermes_payload,
    trigger_hermes_analysis_webhook_async,
)

CLIENT_UUID = "3e20e8b9-c1b5-449f-bc5c-eb0a00704387"
CLIENT_SLUG = "lk-sneakers"
FIXED_NOW = datetime(2026, 11, 27, 14, 0, 0, tzinfo=timezone.utc)


# ── In-memory fake Supabase query builder ─────────────────────────────────────


class _FakeQuery:
    def __init__(self, rows: list[dict]):
        self._rows = [dict(r) for r in rows]
        self._filters: list[tuple[str, str, Any]] = []
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None

    def select(self, *_args, **_kwargs):
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

    def order(self, col: str, desc: bool = False):
        self._order = (col, desc)
        return self

    def limit(self, n: int):
        self._limit = n
        return self

    def execute(self):
        out = list(self._rows)
        for op, col, val in self._filters:
            if op == "eq":
                out = [r for r in out if r.get(col) == val]
            elif op == "neq":
                out = [r for r in out if r.get(col) != val]
            elif op == "gte":
                out = [r for r in out if str(r.get(col) or "") >= str(val)]
            elif op == "lte":
                out = [r for r in out if str(r.get(col) or "") <= str(val)]
            elif op == "in":
                out = [r for r in out if str(r.get(col) or "") in val]
        if self._order:
            col, desc = self._order
            out.sort(key=lambda r: str(r.get(col) or ""), reverse=desc)
        if self._limit is not None:
            out = out[: self._limit]
        return MagicMock(data=out)


class _FakeDB:
    def __init__(self, tables: dict[str, list[dict]]):
        self._tables = tables

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self._tables.get(name, []))


def _make_full_lk_db() -> _FakeDB:
    return _FakeDB({
        "clients": [
            {
                "id": CLIENT_UUID,
                "client_id": CLIENT_SLUG,
                "pixel_id": "lk-sneakers-px",
                "name": "LK Sneakers",
                "business_model": "ecommerce",
            }
        ],
        "core_client_truth": [
            {
                "client_id": CLIENT_SLUG,
                "client_version": 2,
                "target_version": 4,
                "valid_from": "2026-11-01T00:00:00+00:00",
                "client_truth": {
                    "name": "LK Sneakers",
                    "business_model": "ecommerce",
                    "currency": "BRL",
                    "timezone": "America/Sao_Paulo",
                    "platform": "shopify",
                    "constraints": ["Não pausar campanhas institucionais sem aprovação"],
                    "do_not_change": ["Pixel Shopify v4"],
                    "priorities": ["Escalar Black Friday com MER >= 10x"],
                    "promotions": [
                        {
                            "id": "promo-bf",
                            "title": "Black Friday LK 2026",
                            "start_date": "2026-11-20",
                            "end_date": "2026-11-30",
                            "notes": "Até 40% OFF + cupom BF40",
                        },
                        {
                            "id": "promo-old",
                            "title": "Promoção de Inverno",
                            "start_date": "2026-06-01",
                            "end_date": "2026-06-30",
                            "notes": "Fora do período",
                        },
                    ],
                },
                "target_truth": {
                    "revenue_business": {"period": "monthly", "target": 1000000},
                    "mer": {"target": 10},
                },
            }
        ],
        "core_budget_config": [
            {
                "client_id": CLIENT_SLUG,
                "platform": "meta",
                "period_label": "2026-11",
                "monthly_budget": 45000.0,
                "currency": "BRL",
                "monitoring_enabled": True,
            },
            {
                "client_id": CLIENT_SLUG,
                "platform": "google",
                "period_label": "2026-11",
                "monthly_budget": 25000.0,
                "currency": "BRL",
                "monitoring_enabled": True,
            },
        ],
        "timeline_annotations": [
            {
                "id": "ann-1",
                "client_id": CLIENT_UUID,
                "date": "2026-11-22",
                "label": "Drop Influenciador X + Esquenta Black Friday",
                "created_at": "2026-11-22T12:00:00+00:00",
            },
            {
                "id": "ann-out",
                "client_id": CLIENT_UUID,
                "date": "2026-09-10",
                "label": "Evento fora do período",
                "created_at": "2026-09-10T12:00:00+00:00",
            },
        ],
        "core_change_log": [
            # High-impact manual budget change
            {
                "id": "chg-high",
                "client_id": CLIENT_SLUG,
                "occurred_at": "2026-11-21T15:00:00+00:00",
                "channel": "meta",
                "entity_type": "CAMPAIGN",
                "entity_id": "camp-1",
                "entity_name_at_time": "Scale BF Meta",
                "change_type": "BUDGET",
                "before_state": {"value": "R$ 1.000/dia"},
                "after_state": {"value": "R$ 1.500/dia"},
                "reason": "Aumento para Black Friday",
                "source": "MANUAL",
                "reported_by": "Pedro",
                "outcome_status": None,
            },
            # Manual OFFER change (also captured in promotions)
            {
                "id": "chg-offer",
                "client_id": CLIENT_SLUG,
                "occurred_at": "2026-11-23T10:00:00+00:00",
                "channel": "meta",
                "entity_type": "OFFER",
                "entity_id": "offer-1",
                "entity_name_at_time": "Frete Grátis acima de R$ 499",
                "change_type": "OFFER",
                "before_state": {},
                "after_state": {},
                "reason": "Ativação comercial semana BF",
                "source": "MANUAL",
                "reported_by": "Pedro",
                "outcome_status": None,
            },
            # Meta review queue noise — must be filtered out by change_digest._classify
            {
                "id": "chg-noise",
                "client_id": CLIENT_SLUG,
                "occurred_at": "2026-11-24T08:00:00+00:00",
                "channel": "meta",
                "entity_type": "ADGROUP",
                "entity_id": "ad-99",
                "entity_name_at_time": "Ad Review Noise",
                "change_type": "OTHER",
                "before_state": {"value": "Em análise"},
                "after_state": {"value": "Em revisão"},
                "reason": None,
                "source": "META_ACTIVITY",
                "reported_by": None,
                "outcome_status": None,
            },
        ],
        "core_outcomes": [
            {
                "id": "out-active-1",
                "client_id": CLIENT_UUID,
                "action_id": "act-1",
                "recommendation_id": "rec-1",
                "status": "INCONCLUSIVE",
                "confidence": "LOW",
                "evaluation_window_days": 7,
                "delta": {"roas_meta": {"before": 10.47, "after": 7.29, "delta_pct": -30.4}},
                "data_health": {
                    "primary_metric": "roas_meta",
                    "confounders": ["concurrent_changes_12", "hold_no_direct_causality"],
                },
                "measured_at": "2026-11-27T11:00:00+00:00",
                "created_at": "2026-11-27T11:00:00+00:00",
                "supersedes_outcome_id": "1968845f-4273-47f0-9b77-33f39c817bd5",
            },
            {
                "id": "1968845f-4273-47f0-9b77-33f39c817bd5",
                "client_id": CLIENT_UUID,
                "action_id": "act-1",
                "recommendation_id": "rec-1",
                "status": "SUPERSEDED",
                "confidence": "LOW",
                "evaluation_window_days": 7,
                "delta": None,
                "data_health": {"note": "hollow"},
                "measured_at": "2026-11-27T10:30:00+00:00",
                "created_at": "2026-11-27T10:30:00+00:00",
                "supersedes_outcome_id": None,
            },
        ],
        "core_actions": [
            {
                "id": "act-1",
                "action_type": "NO_ACTION_REQUIRED",
                "platform": "meta",
                "entity_type": "ACCOUNT",
                "executed_at": "2026-11-19T17:30:00+00:00",
            }
        ],
    })


# ── 1. Context Bundle (R05 — PRD §8.1) ────────────────────────────────────────


class TestContextBundleR05:
    def test_consolidates_all_six_domains_deterministically(self):
        fake_db = _make_full_lk_db()
        fake_meetings = {
            "recent_meetings": [
                {"id": "m1", "title": "Alinhamento BF", "started_at": "2026-11-25T14:00:00Z", "summary": "Definido teto MER 10x.", "decisions_count": 1, "action_items_count": 1},
                {"id": "m2", "title": "Revisão Criativos", "started_at": "2026-11-18T14:00:00Z", "summary": "Troca de ganchos.", "decisions_count": 1, "action_items_count": 1},
                {"id": "m3", "title": "Planejamento Nov", "started_at": "2026-11-11T14:00:00Z", "summary": "Calendário.", "decisions_count": 0, "action_items_count": 0},
                {"id": "m4", "title": "Antiga (deve ser truncada)", "started_at": "2026-11-01T14:00:00Z", "summary": "Old", "decisions_count": 0, "action_items_count": 0},
            ],
            "recent_decisions": [{"decision": "Manter teto de MER >= 10x", "owner": "Maico", "meeting_id": "m1"}],
            "open_action_items": [{"task": "Subir criativos BF até sexta", "owner": "Pedro", "due_date": "2026-11-27", "priority": "HIGH", "meeting_id": "m1"}],
            "open_questions": ["Confirmar estoque SKU 42"],
            "total_meetings": 4,
        }
        fake_knowledge = {
            "client_knowledge": [
                {"id": f"ck-{i}", "title": f"Client Learning {i}", "statement": f"Stmt {i}", "scope": "CLIENT_ONLY", "platform": "google"}
                for i in range(1, 4)
            ],
            "shared_knowledge": [
                {"id": f"sk-{i}", "title": f"Shared Learning {i}", "statement": f"Shared {i}", "scope": "AGENCY", "platform": "meta"}
                for i in range(1, 5)
            ],
            "total_count": 7,
            "client_id": CLIENT_SLUG,
            "platform_filter": None,
        }

        with (
            patch("app.core.context_bundle.load_meeting_context", return_value=fake_meetings),
            patch("app.core.context_bundle.load_knowledge_context", return_value=fake_knowledge),
        ):
            bundle1 = build_client_context_bundle(
                CLIENT_SLUG,
                period_start="2026-11-19",
                period_end="2026-11-26",
                db=fake_db,
                now=FIXED_NOW,
            )
            bundle2 = build_client_context_bundle(
                CLIENT_SLUG,
                period_start="2026-11-19",
                period_end="2026-11-26",
                db=fake_db,
                now=FIXED_NOW,
            )

        # Deterministic output
        assert bundle1 == bundle2
        assert bundle1["schema_version"] == SCHEMA_VERSION
        assert bundle1["client_id"] == CLIENT_SLUG
        assert bundle1["client_uuid"] == CLIENT_UUID

        # 1. Client Truth (targets, budgets, constraints)
        ct = bundle1["client_truth"]
        assert ct["targets"]["mer"]["target"] == 10
        assert len(ct["budgets"]) == 2
        assert "Não pausar campanhas institucionais sem aprovação" in ct["constraints"]
        assert "Pixel Shopify v4" in ct["constraints"]

        # 2. Active promotions overlapping period (excludes June & September out-of-window events)
        promo_titles = [p["title"] for p in bundle1["promotions"]]
        assert "Black Friday LK 2026" in promo_titles
        assert "Drop Influenciador X + Esquenta Black Friday" in promo_titles
        assert "Frete Grátis acima de R$ 499" in promo_titles
        assert "Promoção de Inverno" not in promo_titles
        assert "Evento fora do período" not in promo_titles

        # 3. Last 3 meetings capped strictly to 3
        assert bundle1["meetings"]["total_meetings"] == 3
        assert len(bundle1["meetings"]["recent_meetings"]) == 3
        assert len(bundle1["meetings"]["recent_decisions"]) == 1
        assert len(bundle1["meetings"]["open_action_items"]) == 1

        # 4. Relevant changes (Meta review noise excluded, high-impact first)
        ch_items = bundle1["relevant_changes"]["items"]
        ch_ids = [c["id"] for c in ch_items]
        assert "chg-noise" not in ch_ids
        assert ch_ids[0] == "chg-high"
        assert "chg-offer" in ch_ids

        # 5. Recent outcomes (excludes SUPERSEDED hollow outcome, records superseded_count)
        assert len(bundle1["recent_outcomes"]) == 1
        assert bundle1["recent_outcomes"][0]["id"] == "out-active-1"
        assert bundle1["recent_outcomes"][0]["action_type"] == "NO_ACTION_REQUIRED"
        assert bundle1["manifest"]["sections"]["recent_outcomes"]["superseded_count"] == 1

        # 6. Top 5 approved knowledge (3 client-specific first, then 2 shared = 5 total)
        top_k = bundle1["approved_knowledge"]["items"]
        assert len(top_k) == 5
        assert [k["id"] for k in top_k] == ["ck-1", "ck-2", "ck-3", "sk-1", "sk-2"]

    def test_honest_absence_when_client_has_empty_sections(self):
        empty_db = _FakeDB({
            "clients": [{"id": CLIENT_UUID, "client_id": "new-client", "pixel_id": None, "name": "New", "business_model": "leads"}]
        })
        empty_meetings = {
            "recent_meetings": [], "recent_decisions": [], "open_action_items": [],
            "open_questions": [], "total_meetings": 0,
        }
        empty_knowledge = {
            "client_knowledge": [], "shared_knowledge": [], "total_count": 0,
            "client_id": "new-client", "platform_filter": None,
        }
        with (
            patch("app.core.context_bundle.load_meeting_context", return_value=empty_meetings),
            patch("app.core.context_bundle.load_knowledge_context", return_value=empty_knowledge),
        ):
            bundle = build_client_context_bundle("new-client", db=empty_db, now=FIXED_NOW)

        for section in ("client_truth", "promotions", "meetings", "relevant_changes", "recent_outcomes", "approved_knowledge"):
            assert bundle["manifest"]["sections"][section]["status"] == "EMPTY"
        assert bundle["promotions"] == []
        assert bundle["recent_outcomes"] == []
        assert bundle["approved_knowledge"]["items"] == []

    def test_unknown_client_raises_lookup_error(self):
        empty_db = _FakeDB({"clients": []})
        with pytest.raises(LookupError):
            build_client_context_bundle("does-not-exist", db=empty_db, now=FIXED_NOW)

    def test_http_endpoint_auth_scopes_and_tenant_isolation(self):
        import importlib
        from app.main import app

        v1_router = importlib.import_module("app.agency_api.v1.router")
        client = TestClient(app)
        fake_db = _make_full_lk_db()

        with (
            patch.object(settings, "AGENCY_API_HERMES_KEY", "test-hermes-key"),
            patch.object(settings, "AGENCY_API_PLATFORM_KEY", "test-platform-key"),
            patch.object(settings, "AGENCY_API_ADMIN_KEY", "test-admin-key"),
            patch.object(v1_router, "_get_db", return_value=fake_db),
            patch.object(
                v1_router,
                "_load_client_meta",
                return_value={"id": CLIENT_UUID, "client_id": CLIENT_SLUG, "pixel_id": "lk-sneakers-px"},
            ),
            patch(
                "app.core.context_bundle.load_meeting_context",
                return_value={"recent_meetings": [], "recent_decisions": [], "open_action_items": [], "open_questions": [], "total_meetings": 0},
            ),
            patch(
                "app.core.context_bundle.load_knowledge_context",
                return_value={"client_knowledge": [], "shared_knowledge": [], "total_count": 0, "client_id": CLIENT_SLUG, "platform_filter": None},
            ),
        ):
            # 1. Unauthenticated -> 401
            r_unauth = client.get(f"/agency/v1/clients/{CLIENT_SLUG}/context-bundle")
            assert r_unauth.status_code == 401

            # 2. Hermes service -> 200
            r_hermes = client.get(
                f"/agency/v1/clients/{CLIENT_SLUG}/context-bundle?period_start=2026-11-19&period_end=2026-11-26",
                headers={"Authorization": "Bearer test-hermes-key"},
            )
            assert r_hermes.status_code == 200
            body = r_hermes.json()
            assert body["schema_version"] == SCHEMA_VERSION
            assert body["client_id"] == CLIENT_SLUG
            assert "client_truth" in body
            assert "approved_knowledge" in body

            # 3. Platform operator -> 200
            r_platform = client.get(
                f"/agency/v1/clients/{CLIENT_SLUG}/context-bundle",
                headers={"Authorization": "Bearer test-platform-key"},
            )
            assert r_platform.status_code == 200

            # 4. Invalid date -> 422
            r_bad_date = client.get(
                f"/agency/v1/clients/{CLIENT_SLUG}/context-bundle?period_start=not-a-date",
                headers={"Authorization": "Bearer test-hermes-key"},
            )
            assert r_bad_date.status_code == 422


# ── 2. Event-Driven Core → Hermes Webhook (PRD §3 & §16) ──────────────────────


class _MemStore:
    def __init__(self):
        self._data: dict[str, tuple[int, dict]] = {}

    def get(self, key: str):
        item = self._data.get(key)
        return {"revision": item[0], "document": item[1]} if item else None

    def cas(self, key: str, revision: int | None, document: dict) -> bool:
        cur = self._data.get(key)
        expected = cur[0] if cur else None
        if expected != revision:
            return False
        next_rev = (cur[0] + 1) if cur else 1
        self._data[key] = (next_rev, document)
        return True


class TestCoreToHermesEventWebhook:
    def _sample_handoff(self) -> dict:
        return {
            "analysis_request_id": "req-lk-h7-001",
            "report_id": "rep-lk-h7-001",
            "report_version": 1,
            "client_id": CLIENT_SLUG,
            "report_type": "weekly",
            "period_start": "2026-11-16",
            "period_end": "2026-11-22",
            "status": "PENDING",
        }

    def test_skipped_cleanly_when_webhook_url_unconfigured(self):
        payload = build_hermes_webhook_payload("req-lk-h7-001", self._sample_handoff())
        mock_post = MagicMock()
        res = dispatch_hermes_analysis_webhook_sync(
            payload,
            webhook_url="",
            http_post=mock_post,
        )
        assert res["status"] == "SKIPPED_UNCONFIGURED"
        assert res["attempts"] == 0
        mock_post.assert_not_called()

    def test_authenticated_hmac_and_bearer_delivery_with_retry_on_503(self):
        handoff = self._sample_handoff()
        payload = build_hermes_webhook_payload("req-lk-h7-001", handoff)
        secret = "unit-test-hmac-secret"
        token = "unit-test-bearer-token"

        captured: list[dict] = []
        sleeps: list[float] = []
        responses = [
            MagicMock(status_code=503),
            RuntimeError("temporary timeout"),
            MagicMock(status_code=202),
        ]

        def fake_post(url, *, content, headers, timeout):
            captured.append({"url": url, "content": content, "headers": headers, "timeout": timeout})
            nxt = responses.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        res = dispatch_hermes_analysis_webhook_sync(
            payload,
            webhook_url="https://hermes.internal.test/hooks/analysis-request",
            webhook_secret=secret,
            webhook_token=token,
            max_retries=3,
            http_post=fake_post,
            sleep_fn=sleeps.append,
        )

        assert res["status"] == "DELIVERED"
        assert res["attempts"] == 3
        assert res["http_status"] == 202
        assert len(sleeps) == 2

        req = captured[-1]
        assert req["headers"]["X-Noro-Event"] == EVENT_REPORT_WAITING
        assert req["headers"]["X-Noro-Delivery"] == "req-lk-h7-001"
        assert req["headers"]["Authorization"] == f"Bearer {token}"
        expected_sig = sign_hermes_payload(req["content"], secret)
        assert req["headers"]["X-Noro-Signature"] == expected_sig

        decoded = json.loads(req["content"].decode("utf-8"))
        assert decoded["context_bundle_url"] == (
            f"/agency/v1/clients/{CLIENT_SLUG}/context-bundle?period_start=2026-11-16&period_end=2026-11-22"
        )
        assert decoded["callback_url"] == "/agency/v1/canonical-reports/rep-lk-h7-001/analysis"

    def test_non_retryable_4xx_fails_fast_and_falls_back_to_polling(self):
        payload = build_hermes_webhook_payload("req-lk-h7-002", self._sample_handoff())
        calls = 0

        def fake_post(url, *, content, headers, timeout):
            nonlocal calls
            calls += 1
            return MagicMock(status_code=401)

        res = dispatch_hermes_analysis_webhook_sync(
            payload,
            webhook_url="https://hermes.internal.test/hooks/analysis-request",
            max_retries=3,
            http_post=fake_post,
            sleep_fn=lambda _: None,
        )
        assert calls == 1
        assert res["status"] == "FAILED_FALLBACK_POLLING"
        assert res["http_status"] == 401

    def test_async_trigger_is_non_blocking_in_event_loop(self):
        done = threading.Event()
        handoff = self._sample_handoff()

        def slow_post(url, *, content, headers, timeout):
            time.sleep(0.05)
            done.set()
            return MagicMock(status_code=200)

        async def _run():
            t0 = time.perf_counter()
            res = trigger_hermes_analysis_webhook_async(
                "req-lk-async",
                handoff,
                webhook_url="https://hermes.internal.test/hooks/analysis-request",
                http_post=slow_post,
                sleep_fn=lambda _: None,
            )
            elapsed = time.perf_counter() - t0
            assert res["dispatched"] is True
            assert res["mode"] == "asyncio_task"
            # Must return immediately without waiting for slow_post
            assert elapsed < 0.04
            await asyncio.sleep(0.12)
            assert done.is_set()

        asyncio.run(_run())

    def test_write_analysis_request_triggers_webhook_once_and_preserves_idempotency(self):
        store = _MemStore()
        reports = Reports(store, flags=Flags())
        handoff = self._sample_handoff()

        with patch("app.core.hermes_webhook.trigger_hermes_analysis_webhook_async") as mock_trigger:
            saved1 = reports.write_analysis_request("req-lk-h7-001", handoff)
            # Idempotent replay with same id must NOT re-trigger webhook
            saved2 = reports.write_analysis_request("req-lk-h7-001", handoff)

        assert saved1["analysis_request_id"] == "req-lk-h7-001"
        assert saved2["analysis_request_id"] == "req-lk-h7-001"
        assert mock_trigger.call_count == 1
        mock_trigger.assert_called_once_with("req-lk-h7-001", saved1)
