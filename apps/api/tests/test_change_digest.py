"""Tests for core/change_digest.py — classifier, grouping, summary, API endpoints."""
import pytest
from datetime import datetime, timezone


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row(entity_type, before_val, after_val, campaign_id="camp1", entity_name="Test Entity",
         occurred_at="2026-10-19T10:00:00+00:00", change_type="OTHER", source="PLATFORM_DETECTED"):
    before = {"value": before_val} if before_val is not None else None
    after = {"value": after_val} if after_val is not None else None
    return {
        "id": "abc",
        "client_id": "test-client",
        "channel": "meta",
        "entity_type": entity_type,
        "entity_name_at_time": entity_name,
        "change_type": change_type,
        "before_state": before,
        "after_state": after,
        "campaign_id": campaign_id,
        "adset_or_adgroup_id": None,
        "ad_id": None,
        "reason": None,
        "description": None,
        "reported_by": None,
        "requested_by": None,
        "linked_action_id": None,
        "recommendation_id": None,
        "confidence": "CONFIRMED",
        "source": source,
        "occurred_at": occurred_at,
    }


# ── Classifier tests ──────────────────────────────────────────────────────────

from app.core.change_digest import _classify, _group_changes, _build_summary, build_digest, _fmt_brl


def test_budget_change_classified():
    row = _row(
        "CAMPAIGN_GROUP",
        {"type": "payment_amount", "currency": "BRL", "old_value": 425000, "additional_type": "status_string", "additional_value": "Orçamento total"},
        {"type": "payment_amount", "currency": "BRL", "new_value": 520000, "additional_type": "status_string", "additional_value": "Orçamento total"},
    )
    result = _classify(row)
    assert result is not None
    ct, impact, extra = result
    assert ct == "BUDGET_CHANGE"
    assert impact == "high"
    assert extra["before_brl_centavos"] == 425000
    assert extra["after_brl_centavos"] == 520000
    assert "R$" in extra["before_display"]
    assert "R$" in extra["after_display"]


def test_campaign_group_activation():
    row = _row("CAMPAIGN_GROUP", "Processo pendente", "Ativa")
    result = _classify(row)
    assert result is not None
    ct, impact, _ = result
    assert ct == "CAMPAIGN_GROUP_ACTIVATION"
    assert impact == "high"


def test_campaign_activation():
    row = _row("CAMPAIGN", "Processo pendente", "Ativo")
    result = _classify(row)
    assert result is not None
    ct, impact, _ = result
    assert ct == "CAMPAIGN_ACTIVATION"
    assert impact == "high"


def test_campaign_pause():
    row = _row("CAMPAIGN", "Ativo", "Pausado")
    result = _classify(row)
    assert result is not None
    ct, impact, _ = result
    assert ct == "CAMPAIGN_PAUSE"
    assert impact == "high"


def test_schedule_change():
    row = _row(
        "CAMPAIGN",
        {"time_stop": "out 31, 2026", "time_start": "set 1, 2026"},
        {"time_stop": "nov 30, 2026", "time_start": "set 1, 2026"},
    )
    result = _classify(row)
    assert result is not None
    ct, impact, _ = result
    assert ct == "SCHEDULE_CHANGE"
    assert impact == "medium"


def test_new_entity_created():
    row = {
        **_row("CAMPAIGN", None, None),
        "before_state": None,
        "after_state": {"value": {"name": "Semelhante BR 1%", "type": 4}},
    }
    result = _classify(row)
    assert result is not None
    ct, impact, extra = result
    assert ct == "NEW_ENTITY_CREATED"
    assert impact == "medium"


def test_ad_activation_is_low_impact():
    row = _row("ADGROUP", "Análise pendente", "Ativo")
    result = _classify(row)
    assert result is not None
    ct, impact, _ = result
    assert ct == "AD_ACTIVATION"
    assert impact == "low"


def test_ad_pause_is_high_impact():
    row = _row("ADGROUP", "Ativo", "Pausado")
    result = _classify(row)
    assert result is not None
    ct, impact, _ = result
    assert ct == "AD_PAUSE"
    assert impact == "high"


def test_noise_processo_pendente_analise_pendente():
    row = _row("ADGROUP", "Processo pendente", "Análise pendente")
    result = _classify(row)
    assert result is None


def test_noise_campaign_group_processo_pendente():
    row = _row("CAMPAIGN_GROUP", "Processo pendente", "Análise pendente")
    result = _classify(row)
    assert result is None


def test_audience_change():
    row = _row(
        "CAMPAIGN",
        [{"content": "Público personalizado:", "children": ["audience_a"]}],
        [{"content": "Público personalizado:", "children": ["audience_b"]}],
    )
    result = _classify(row)
    assert result is not None
    ct, impact, _ = result
    assert ct == "AUDIENCE_CHANGE"
    assert impact == "medium"


def test_google_balance_recharge():
    row = _row("GOOGLE_BALANCE_RECHARGE", None, {"amount": 500000})
    result = _classify(row)
    assert result is not None
    ct, impact, _ = result
    assert ct == "BALANCE_RECHARGE"
    assert impact == "medium"


def test_unknown_entity_returns_none():
    row = _row("ACCOUNT", None, {"value": 300285})
    result = _classify(row)
    assert result is None


# ── Grouping tests ────────────────────────────────────────────────────────────

def test_batch_ad_activations_grouped():
    times = [
        "2026-10-19T10:00:00+00:00",
        "2026-10-19T10:05:00+00:00",
        "2026-10-19T10:10:00+00:00",
    ]
    rows_and_results = [
        (_row("ADGROUP", "Análise pendente", "Ativo", campaign_id="camp1", entity_name=f"Ad {i}", occurred_at=t),
         ("AD_ACTIVATION", "low", {}))
        for i, t in enumerate(times)
    ]
    grouped = _group_changes(rows_and_results)
    assert len(grouped) == 1
    assert grouped[0]["count"] == 3
    assert grouped[0]["change_type"] == "AD_ACTIVATION"


def test_activations_different_campaigns_not_grouped():
    rows_and_results = [
        (_row("ADGROUP", "Análise pendente", "Ativo", campaign_id="camp1", occurred_at="2026-10-19T10:00:00+00:00"),
         ("AD_ACTIVATION", "low", {})),
        (_row("ADGROUP", "Análise pendente", "Ativo", campaign_id="camp2", occurred_at="2026-10-19T10:05:00+00:00"),
         ("AD_ACTIVATION", "low", {})),
    ]
    grouped = _group_changes(rows_and_results)
    assert len(grouped) == 2


def test_high_impact_not_grouped():
    rows_and_results = [
        (_row("CAMPAIGN_GROUP", "Ativo", "Processo pendente"), ("CAMPAIGN_GROUP_PAUSE", "high", {})),
        (_row("CAMPAIGN", "Ativo", "Pausado"), ("CAMPAIGN_PAUSE", "high", {})),
    ]
    grouped = _group_changes(rows_and_results)
    assert len(grouped) == 2


# ── Summary tests ─────────────────────────────────────────────────────────────

def test_summary_budget_change():
    changes = [{
        "change_type": "BUDGET_CHANGE",
        "impact": "high",
        "entity": "DP | MEIO | TRÁFEGO",
        "campaign_id": "camp1",
        "before_display": "R$ 4.250,00",
        "after_display": "R$ 5.200,00",
        "currency": "BRL",
        "reason": None,
        "linked_action_id": None,
        "changed_at": "2026-10-19T10:00:00+00:00",
    }]
    summary = _build_summary(changes)
    assert summary["high_impact_count"] == 1
    assert any("Budget" in m for m in summary["main_changes"])
    assert summary["budget_changes"][0]["before_display"] == "R$ 4.250,00"


def test_summary_unknown_reason_counted():
    changes = [
        {"change_type": "CAMPAIGN_PAUSE", "impact": "high", "entity": "X", "campaign_id": "c1",
         "reason": None, "linked_action_id": None, "changed_at": "2026-10-19T10:00:00+00:00"},
        {"change_type": "CAMPAIGN_ACTIVATION", "impact": "high", "entity": "Y", "campaign_id": "c2",
         "reason": "Teste A/B", "linked_action_id": None, "changed_at": "2026-10-19T10:00:00+00:00"},
    ]
    summary = _build_summary(changes)
    assert summary["unknown_reason_count"] == 1  # only the first one has no reason


# ── Format helpers ────────────────────────────────────────────────────────────

def test_fmt_brl():
    assert _fmt_brl(425000) == "R$ 4.250,00"
    assert _fmt_brl(100) == "R$ 1,00"
    assert _fmt_brl(0) == "R$ 0,00"


# ── build_digest with fake DB ─────────────────────────────────────────────────

class _FakeQuery:
    def __init__(self, data): self._data = data
    def eq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def range(self, *a, **k): return self
    def execute(self): return type("R", (), {"data": self._data})()


class _FakeTable:
    def __init__(self, data): self._data = data
    def select(self, *a, **k): return _FakeQuery(self._data)
    def upsert(self, payload, **k): return _FakeQuery([{**payload, "id": "new-uuid"}])


class _FakeDB:
    def __init__(self, rows): self._rows = rows
    def table(self, name): return _FakeTable(self._rows)


def test_build_digest_empty_period():
    db = _FakeDB([])
    digest = build_digest(db, "test-client", "meta", "2026-10-19", "2026-10-25")
    assert digest["total_raw_count"] == 0
    assert digest["relevant_count"] == 0
    assert digest["changes"] == []
    assert any("nenhum evento" in l for l in digest["limitations"])


def test_build_digest_with_mixed_rows():
    rows = [
        # Budget change (relevant)
        {**_row("CAMPAIGN_GROUP",
                {"type": "payment_amount", "currency": "BRL", "old_value": 100000},
                {"type": "payment_amount", "currency": "BRL", "new_value": 150000}),
         "occurred_at": "2026-10-19T09:00:00+00:00"},
        # Noise
        {**_row("ADGROUP", "Processo pendente", "Análise pendente"),
         "occurred_at": "2026-10-19T09:05:00+00:00"},
        # Activation (relevant)
        {**_row("CAMPAIGN", "Processo pendente", "Ativo"),
         "occurred_at": "2026-10-19T09:10:00+00:00"},
    ]
    db = _FakeDB(rows)
    digest = build_digest(db, "test-client", "meta", "2026-10-19", "2026-10-25")
    assert digest["total_raw_count"] == 3
    assert digest["noise_count"] == 1
    assert digest["relevant_count"] == 2
    assert digest["engine_version"] == "1"
    assert len(digest["changes"]) == 2


def test_build_digest_all_noise():
    rows = [_row("ADGROUP", "Processo pendente", "Análise pendente") for _ in range(5)]
    db = _FakeDB(rows)
    digest = build_digest(db, "test-client", "meta", "2026-10-19", "2026-10-25")
    assert digest["noise_count"] == 5
    assert digest["relevant_count"] == 0
