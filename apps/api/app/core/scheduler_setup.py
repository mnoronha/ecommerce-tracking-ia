"""
Shared APScheduler builder for Noro Platform (PRD §21.4).

Single source of truth for all background scheduled jobs so both:
  - Dedicated worker process (`python -m app.worker` with `pg_try_advisory_lock`)
  - Optional embedded mode (`ENABLE_EMBEDDED_SCHEDULER=true` in `app.main`)
register the exact same job set with explicit UTC timezone and `max_instances=1`.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.background import BackgroundScheduler

from .alert_evaluator import evaluate_system_alerts as _evaluate_system_alerts
from .balance_monitor import run_balance_check_all_prepaid
from .canonical_monthly_scheduler import run_canonical_monthly_all_clients as _run_canonical_monthly
from .canonical_weekly_scheduler import run_canonical_weekly_all_clients as _run_canonical_weekly
from .change_history_scheduler import run_change_history_all_clients as _run_change_history
from .core_scheduler import run_core_pipeline_all_clients
from .monthly_review import run_monthly_review_all_clients
from .notion_adapter import NotionTaskAdapter as _NotionAdapter
from .outcome_evaluator import evaluate_pending_outcomes as _evaluate_pending_outcomes
from .scheduler_registry import register as _register_scheduler
from .weekly_deep_review import run_weekly_deep_review_all_clients as _run_weekly_deep_review
from .weekly_review import run_weekly_review_all_clients
from ..database import get_supabase as _get_supabase_for_notion
from ..services import (
    ai_analyst,
    ai_visibility_analyst,
    ai_visibility_collector,
    alert_engine,
    alerts,
    anomalies,
    content_approval,
    creative_intelligence,
    creative_sync,
    crypto,
    finance_alerts,
    health_monitor,
    integrations_health,
    ltv_predictor,
    merchant_center,
    meta_attribution_sync,
    meta_audiences,
    meta_token_health,
    metrics_cache,
    reports,
    retention,
    search_console_sync,
    shopify_sync,
    spend_sync,
)

logger = logging.getLogger(__name__)


def embedded_scheduler_enabled() -> bool:
    """
    Return whether the HTTP API process (`app.main`) should start APScheduler.

    Set `ENABLE_EMBEDDED_SCHEDULER=false` on the Railway web service when running
    the dedicated `python -m app.worker` service so HTTP API replicas stay isolated
    from background cron execution (PRD §21.4).
    Defaults to True for zero-downtime backwards compatibility during cutover.
    """
    raw = os.getenv("ENABLE_EMBEDDED_SCHEDULER", "true").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _notion_sync_pending() -> None:
    _NotionAdapter(supabase=_get_supabase_for_notion()).sync_pending_tasks_to_notion()


def _notion_poll_updates() -> None:
    _NotionAdapter(supabase=_get_supabase_for_notion()).poll_notion_task_updates()


def _ga4_dispatch_pending() -> None:
    from .ga4_dispatch import run_pending
    run_pending()


def build_scheduler(job_runs: Optional[dict] = None) -> tuple[BackgroundScheduler, dict]:
    """
    Build and configure the APScheduler instance with all Noro Platform jobs.
    Does NOT start the scheduler — caller acquires any lock and calls `.start()`.
    """
    runs = job_runs if job_runs is not None else {}

    def _record_job_run(event) -> None:
        runs[event.job_id] = {
            "last_run":    datetime.now(timezone.utc).isoformat(),
            "last_status": "error" if event.exception else "ok",
        }

    scheduler = BackgroundScheduler(timezone=timezone.utc)
    _register_scheduler(scheduler, runs)
    scheduler.add_listener(_record_job_run, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

    scheduler.add_job(alerts.run_conversion_check, "interval", hours=6, id="conversion_alerts")

    canonical_delivery_enabled = os.getenv("REPORT_DELIVERY_ENABLED", "").strip().lower() == "true"
    if canonical_delivery_enabled:
        from .canonical_delivery_scheduler import run_pending_deliveries as _run_canonical_delivery
        scheduler.add_job(
            _run_canonical_delivery,
            "interval",
            minutes=30,
            id="canonical_delivery",
            max_instances=1,
            misfire_grace_time=1800,
        )
    else:
        scheduler.add_job(
            reports.send_weekly_reports,
            "cron",
            day_of_week="mon",
            hour=11,
            minute=0,
            id="weekly_reports",
        )
        scheduler.add_job(
            reports.send_monthly_reports,
            "cron",
            day=1,
            hour=11,
            minute=0,
            id="monthly_reports",
        )

    scheduler.add_job(meta_audiences.run_audience_sync_all_clients, "interval", hours=6, id="audience_sync")
    scheduler.add_job(meta_token_health.run_token_health_check, "interval", hours=6, id="meta_token_health")
    scheduler.add_job(anomalies.run_daily_anomaly_check, "cron", hour=11, minute=0, id="anomalies_daily")
    scheduler.add_job(integrations_health.run_hourly_for_all_clients, "interval", hours=1, id="integrations_health_hourly")
    scheduler.add_job(meta_attribution_sync.run_daily_sync_all_clients, "cron", hour=9, minute=0, id="meta_attribution_sync_night")
    scheduler.add_job(meta_attribution_sync.run_daily_sync_all_clients, "cron", hour=14, minute=0, id="meta_attribution_sync")
    scheduler.add_job(ltv_predictor.run_daily_for_all_clients, "cron", hour=5, minute=0, id="ltv_stats_refresh")
    scheduler.add_job(creative_sync.run_daily_for_all_clients, "cron", hour=10, minute=0, id="creative_sync")
    scheduler.add_job(creative_intelligence.run_weekly_for_all_clients, "cron", day_of_week="tue", hour=11, minute=0, id="creative_intelligence")
    scheduler.add_job(alert_engine.run_alert_engine, "interval", minutes=30, id="alert_engine")
    scheduler.add_job(spend_sync.run_daily_spend_sync, "cron", hour=6, minute=0, id="spend_sync_daily")
    scheduler.add_job(search_console_sync.run_daily_sync_all_clients, "cron", hour=5, minute=30, id="search_console_daily_sync")
    scheduler.add_job(metrics_cache.run_daily_metrics_cache, "cron", hour=6, minute=30, id="metrics_cache_daily")
    scheduler.add_job(ai_analyst.run_daily_insights_all_clients, "cron", hour=7, minute=30, id="daily_ai_insights")
    scheduler.add_job(health_monitor.run_daily_health_check_safe, "cron", hour=12, minute=30, id="daily_health_monitor")
    scheduler.add_job(finance_alerts.run_daily_finance_alerts, "cron", hour=12, minute=0, id="finance_alerts_daily")
    scheduler.add_job(retention.run_retention, "cron", hour=6, minute=0, id="event_retention")
    scheduler.add_job(crypto.encrypt_existing_credentials, "cron", hour=7, minute=0, id="credentials_encrypt")
    scheduler.add_job(run_core_pipeline_all_clients, "cron", hour=7, minute=15, id="core_pipeline_daily")

    if os.getenv("CANONICAL_WEEKLY_REVIEW_ENABLED", "false").strip().lower() == "true":
        scheduler.add_job(
            _run_canonical_weekly,
            "interval",
            hours=1,
            id="canonical_weekly_review",
            max_instances=1,
            misfire_grace_time=7200,
        )
    else:
        scheduler.add_job(
            run_weekly_review_all_clients,
            "cron",
            day_of_week="mon",
            hour=8,
            minute=0,
            id="weekly_review",
        )

    scheduler.add_job(
        _run_weekly_deep_review,
        "cron",
        day_of_week="mon",
        hour=8,
        minute=15,
        id="weekly_deep_review",
    )

    if os.getenv("CANONICAL_MONTHLY_REVIEW_ENABLED", "false").strip().lower() == "true":
        scheduler.add_job(
            _run_canonical_monthly,
            "interval",
            hours=1,
            id="canonical_monthly_review",
            max_instances=1,
            misfire_grace_time=7200,
        )
    else:
        scheduler.add_job(
            run_monthly_review_all_clients,
            "cron",
            day=1,
            hour=8,
            minute=30,
            id="monthly_review",
        )

    scheduler.add_job(_evaluate_system_alerts, "interval", minutes=30, id="core_alert_system_check")
    scheduler.add_job(run_balance_check_all_prepaid, "interval", hours=1, id="balance_check_prepaid")
    scheduler.add_job(shopify_sync.run_hourly_for_all_clients, "interval", hours=1, id="shopify_api_sync")
    scheduler.add_job(merchant_center.run_daily_sync_all_clients, "cron", hour=5, minute=0, id="merchant_center_daily_sync")
    scheduler.add_job(content_approval.run_auto_approve_check, "interval", hours=2, id="content_auto_approve")
    scheduler.add_job(content_approval.send_deadline_reminders, "cron", hour=10, minute=0, id="content_deadline_reminders")
    scheduler.add_job(ai_visibility_analyst.send_monthly_import_reminder, "cron", day=1, hour=11, minute=0, id="ai_visibility_monthly_reminder")
    scheduler.add_job(ai_visibility_collector.run_weekly_for_all_clients, "cron", day_of_week="mon", hour=3, minute=0, id="ai_visibility_weekly_collect")
    scheduler.add_job(ai_visibility_collector.reset_monthly_budgets, "cron", day=1, hour=0, minute=0, id="ai_visibility_budget_reset")

    # R04: hourly outcome evaluation (idempotent via core_job_runs + unique active outcome index)
    # so windows closing after 10:30 UTC are processed within 1h instead of waiting ~24h.
    scheduler.add_job(
        _evaluate_pending_outcomes,
        "interval",
        hours=1,
        id="outcome_evaluation",
        max_instances=1,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        _ga4_dispatch_pending,
        "interval",
        minutes=10,
        id="ga4_dispatch_pending",
        max_instances=1,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        _run_change_history,
        "interval",
        minutes=15,
        id="change_history_ingest",
        max_instances=1,
        misfire_grace_time=900,
    )
    scheduler.add_job(
        _notion_sync_pending,
        "interval",
        minutes=5,
        id="notion_sync_pending",
        max_instances=1,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        _notion_poll_updates,
        "interval",
        minutes=5,
        id="notion_poll_updates",
        max_instances=1,
        misfire_grace_time=300,
    )

    return scheduler, runs
