from datetime import date
from unittest.mock import patch

import pytest

from app.core.monthly_backfill import materialize_closed_month


@pytest.mark.parametrize("year,month,end", [(2026, 9, 30), (2024, 2, 29), (2025, 2, 28)])
def test_exact_calendar_month_uses_core_pipeline_without_daily_campaign_writes(year, month, end):
    with patch("app.core.monthly_backfill.run_pipeline") as pipeline, \
         patch("app.core.monthly_backfill._load_existing_snapshot", return_value=None):
        result = materialize_closed_month("lk-sneakers", year, month,
                                         timezone="America/Sao_Paulo", today=date(2026, 10, 19))
        pipeline.assert_called_once_with("lk-sneakers", date(year, month, 1),
                                        date(year, month, end), view="live", persist_campaign_rows=False)
        assert result is pipeline.return_value


@pytest.mark.parametrize("today", [date(2026, 9, 15), date(2026, 9, 30)])
def test_open_month_cannot_materialize(today):
    with patch("app.core.monthly_backfill.run_pipeline") as pipeline:
        with pytest.raises(ValueError, match="MONTH_NOT_CLOSED"):
            materialize_closed_month("lk-sneakers", 2026, 9,
                                     timezone="America/Sao_Paulo", today=today)
        pipeline.assert_not_called()


@pytest.mark.parametrize("persist", [True, False])
def test_pipeline_campaign_write_gate_preserves_snapshot(persist):
    from app.core.pipeline import run_pipeline
    from tests.test_core_pipeline import (_GOOD_META_RESULT, _GOOD_GOOGLE_RESULT,
                                          _GOOD_GA4_RESULT, _GOOD_BUSINESS_RESULT)
    from contextlib import ExitStack
    with ExitStack() as stack:
        mocks = {}
        for name, value in {"collect_meta_ads": _GOOD_META_RESULT,
                            "collect_google_ads": _GOOD_GOOGLE_RESULT,
                            "collect_ga4": _GOOD_GA4_RESULT,
                            "collect_business": _GOOD_BUSINESS_RESULT,
                            "write_snapshot": "monthly-snapshot",
                            "_upsert_source_state": None,
                            "update_source_reconciliation": None}.items():
            mocks[name] = stack.enter_context(patch("app.core.pipeline." + name, return_value=value))
        db = stack.enter_context(patch("app.core.pipeline.get_supabase"))
        db.return_value.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
            "id": "client-uuid", "client_id": "lk-sneakers", "currency": "BRL",
            "timezone": "America/Sao_Paulo", "meta_ad_account_id": "meta",
            "meta_access_token": "test", "google_ads_customer_id": "google",
            "google_ads_refresh_token": "test", "ga4_property_id": "ga4",
            "ecommerce_platform": "shopify",
        }
        db.return_value.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
        stack.enter_context(patch("app.core.pipeline.crypto.decrypt_client_secrets"))
        campaign = stack.enter_context(patch("app.core.pipeline._persist_campaign_rows"))
        result = run_pipeline("lk-sneakers", date(2026, 9, 1), date(2026, 9, 30),
                              persist_campaign_rows=persist)
        assert result.snapshot_id == "monthly-snapshot"
        assert campaign.call_count == (2 if persist else 0)
        mocks["write_snapshot"].assert_called_once()
        assert mocks["write_snapshot"].call_args.kwargs["period_start"] == date(2026, 9, 1)
