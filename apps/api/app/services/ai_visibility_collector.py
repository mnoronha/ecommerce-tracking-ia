"""
AI Visibility Collector — automatic data collection via DataForSEO API.

Replaces manual CSV import for DataForSEO-enabled clients.
Writes to the same ai_visibility_metrics / ai_visibility_imports tables
so all existing queries and the Claude analysis pipeline work unchanged.

Entry points:
  collect_for_client(client_id)  — single client, used by router (manual trigger)
  run_weekly_for_all_clients()   — APScheduler cron (Monday 03:00 UTC)
  reset_monthly_budgets()        — APScheduler cron (1st of month 00:00 UTC)
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from ..database import get_supabase
from ..services import ai_visibility as svc
from ..services.dataforseo_client import DataForSEOClient, DataForSEOError

logger = logging.getLogger(__name__)

_PLATFORM_NORMALIZE = {
    "chatgpt":    "chatgpt",
    "gpt":        "chatgpt",
    "gemini":     "gemini",
    "bard":       "gemini",
    "perplexity": "perplexity",
    "claude":     "claude",
    "copilot":    "copilot",
}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_config(client_id: str) -> Optional[dict]:
    rows = (
        get_supabase()
        .table("dataforseo_configs")
        .select("*")
        .eq("client_id", client_id)
        .limit(1)
        .execute()
    ).data
    return rows[0] if rows else None


def _get_active_prompts(client_id: str) -> list[dict]:
    return (
        get_supabase()
        .table("ai_visibility_prompts")
        .select("id,prompt_text")
        .eq("client_id", client_id)
        .eq("is_active", True)
        .execute()
    ).data or []


def _get_own_brand_and_domain(client_id: str) -> tuple[Optional[dict], Optional[str]]:
    sb = get_supabase()

    brand_rows = (
        sb.table("ai_visibility_brands")
        .select("brand_name,website_url,brand_aliases")
        .eq("client_id", client_id)
        .eq("is_own_brand", True)
        .limit(1)
        .execute()
    ).data
    brand = brand_rows[0] if brand_rows else None

    domain: Optional[str] = None
    if brand and brand.get("website_url"):
        domain = (
            brand["website_url"]
            .removeprefix("https://")
            .removeprefix("http://")
            .rstrip("/")
        )

    if not domain:
        client_rows = (
            sb.table("clients")
            .select("shopify_domain")
            .eq("id", client_id)
            .limit(1)
            .execute()
        ).data
        if client_rows and client_rows[0].get("shopify_domain"):
            domain = client_rows[0]["shopify_domain"].rstrip("/")

    return brand, domain


def _check_budget(config: dict, estimated_cost: float) -> bool:
    budget = float(config.get("budget_monthly_usd") or 50.0)
    used   = float(config.get("budget_used_this_month") or 0.0)
    return (used + estimated_cost) <= budget


def _log_usage(
    client_id: str,
    run_id: str,
    endpoint: str,
    units: int,
    cost_usd: float,
    api_task_id: Optional[str] = None,
) -> None:
    try:
        get_supabase().table("dataforseo_usage_log").insert({
            "client_id":         client_id,
            "collection_run_id": run_id,
            "endpoint":          endpoint,
            "request_units":     units,
            "cost_usd":          cost_usd,
            "api_task_id":       api_task_id,
        }).execute()
    except Exception as exc:
        logger.warning("dataforseo collector: usage log failed: %s", exc)


def _update_config_after_run(client_id: str, cost_usd: float, status: str) -> None:
    sb = get_supabase()
    try:
        current = _get_config(client_id)
        current_used = float((current or {}).get("budget_used_this_month") or 0)
        sb.table("dataforseo_configs").update({
            "budget_used_this_month": current_used + cost_usd,
            "last_collection_at":     datetime.now(timezone.utc).isoformat(),
            "last_collection_status": status,
            "updated_at":             datetime.now(timezone.utc).isoformat(),
        }).eq("client_id", client_id).execute()
    except Exception as exc:
        logger.warning("dataforseo collector: config update failed: %s", exc)


def _parse_and_write(
    response_data: dict,
    client_id: str,
    run_id: str,
    prompts_by_text: dict[str, str],
) -> tuple[int, int]:
    """Parse DataForSEO LLM mentions response and write metrics. Returns (processed, skipped)."""
    sb       = get_supabase()
    today    = date.today().isoformat()
    tasks    = response_data.get("tasks") or []
    processed, skipped = 0, 0

    for task in tasks:
        for result in task.get("result") or []:
            keyword  = result.get("keyword") or ""
            platform = _PLATFORM_NORMALIZE.get(
                (result.get("platform") or "").lower(),
                (result.get("platform") or "").lower(),
            )

            if not keyword or not platform:
                skipped += 1
                continue

            prompt_id = prompts_by_text.get(keyword)
            if not prompt_id:
                skipped += 1
                continue

            for item in result.get("items") or []:
                if item.get("type") != "llm_mention":
                    continue

                mentioned    = bool(item.get("mentioned", False))
                position     = item.get("position")
                context      = (item.get("context") or item.get("context_snippet") or "")[:1000]
                response_txt = (item.get("response_text") or "")[:2000]
                cited        = item.get("cited_sources") or None
                word_count   = item.get("response_word_count") or item.get("word_count") or None

                raw_sentiment = (item.get("sentiment") or "").lower().strip()
                sentiment = raw_sentiment if raw_sentiment in ("positive", "negative", "neutral") else None

                try:
                    metric_row = (
                        sb.table("ai_visibility_metrics").upsert({
                            "client_id":             client_id,
                            "prompt_id":             prompt_id,
                            "date":                  today,
                            "platform":              platform,
                            "own_brand_mentioned":   mentioned,
                            "own_brand_position":    position,
                            "own_brand_sentiment":   sentiment,
                            "own_brand_context":     context or None,
                            "import_id":             run_id,
                            "context_snippets":      [context] if context else None,
                            "cited_sources":         cited,
                            "response_word_count":   word_count,
                            "response_text":         response_txt or None,
                        }, on_conflict="client_id,prompt_id,date,platform")
                        .execute()
                    ).data

                    # Store raw response for audit trail
                    if response_txt and metric_row:
                        try:
                            sb.table("llm_responses_raw").insert({
                                "client_id":         client_id,
                                "collection_run_id": run_id,
                                "metric_id":         metric_row[0].get("id"),
                                "llm_platform":      platform,
                                "prompt_text":       keyword,
                                "response_text":     response_txt,
                                "response_date":     today,
                            }).execute()
                        except Exception:
                            pass

                    processed += 1
                except Exception as exc:
                    logger.warning(
                        "dataforseo collector: metric upsert failed [%s/%s]: %s",
                        keyword, platform, exc,
                    )
                    skipped += 1

    return processed, skipped


# ── Public API ────────────────────────────────────────────────────────────────

def collect_for_client(client_id: str, *, force: bool = False) -> dict:
    """
    Run one DataForSEO collection for a single client.

    Args:
        client_id: Supabase UUID of the client.
        force:     If True, bypass budget check (manual trigger).

    Returns dict with keys: run_id, processed, skipped, cost_usd, error (if failed).
    """
    sb = get_supabase()
    config = _get_config(client_id)

    if not config:
        return {"error": "not_configured", "client_id": client_id}
    if not config.get("is_enabled") and not force:
        return {"error": "disabled", "client_id": client_id}

    prompts = _get_active_prompts(client_id)
    if not prompts:
        return {"error": "no_prompts", "client_id": client_id}

    _brand, domain = _get_own_brand_and_domain(client_id)
    if not domain:
        return {"error": "no_domain", "client_id": client_id}

    llms          = config.get("llms_to_monitor") or ["chatgpt", "gemini", "perplexity"]
    location_code = config.get("location_code") or 2076
    language_code = config.get("language_code") or "pt"
    est_cost      = DataForSEOClient.estimate_cost(len(prompts), len(llms))

    if not force and not _check_budget(config, est_cost):
        logger.warning("dataforseo: client %s over budget (est. $%.4f)", client_id, est_cost)
        sb.table("dataforseo_configs").update({
            "last_collection_status": "budget_exceeded",
            "updated_at":             datetime.now(timezone.utc).isoformat(),
        }).eq("client_id", client_id).execute()
        return {"error": "budget_exceeded", "estimated_cost": est_cost, "client_id": client_id}

    # Create a collection run record in ai_visibility_imports
    today = date.today().isoformat()
    run_record = svc.create_import_record(
        client_id    = client_id,
        period_start = today,
        period_end   = today,
        source       = "dataforseo",
    )
    run_id = run_record["id"]
    try:
        sb.table("ai_visibility_imports").update({
            "source_type":  "api",
            "llms_queried": llms,
        }).eq("id", run_id).execute()
    except Exception:
        pass

    keywords        = [p["prompt_text"] for p in prompts]
    prompts_by_text = {p["prompt_text"]: p["id"] for p in prompts}

    try:
        dfs      = DataForSEOClient()
        response = dfs.get_llm_mentions(
            keywords      = keywords,
            target_domain = domain,
            location_code = location_code,
            language_code = language_code,
            llms          = llms,
        )
    except DataForSEOError as exc:
        svc.fail_import(run_id, str(exc))
        _update_config_after_run(client_id, 0, "error")
        logger.error("dataforseo: API error for client %s: %s", client_id, exc)
        return {"error": str(exc), "run_id": run_id, "client_id": client_id}

    processed, skipped = _parse_and_write(response, client_id, run_id, prompts_by_text)

    task_ids = [t.get("id") for t in (response.get("tasks") or []) if t.get("id")]
    _log_usage(client_id, run_id, "llm_mentions", len(keywords) * len(llms), est_cost)
    _update_config_after_run(client_id, est_cost, "ok")

    svc.complete_import(
        import_id      = run_id,
        rows_processed = processed,
        rows_skipped   = skipped,
        errors         = [],
    )
    try:
        sb.table("ai_visibility_imports").update({
            "collection_cost_usd": est_cost,
            "api_task_ids":        task_ids,
        }).eq("id", run_id).execute()
    except Exception:
        pass

    try:
        svc.recalc_monthly_summary(client_id, today)
    except Exception as exc:
        logger.warning("dataforseo: monthly summary recalc failed: %s", exc)

    logger.info(
        "dataforseo: client %s — processed=%d skipped=%d cost=$%.4f",
        client_id, processed, skipped, est_cost,
    )
    return {
        "run_id":    run_id,
        "processed": processed,
        "skipped":   skipped,
        "cost_usd":  est_cost,
        "client_id": client_id,
    }


def run_weekly_for_all_clients() -> None:
    """APScheduler entry point: weekly collection for all enabled clients."""
    try:
        configs = (
            get_supabase()
            .table("dataforseo_configs")
            .select("client_id")
            .eq("is_enabled", True)
            .execute()
        ).data or []

        if not configs:
            logger.info("dataforseo: no enabled clients, skipping weekly run")
            return

        logger.info("dataforseo: weekly collection starting for %d client(s)", len(configs))
        for cfg in configs:
            try:
                result = collect_for_client(cfg["client_id"])
                if result.get("error"):
                    logger.warning(
                        "dataforseo: client %s — %s",
                        cfg["client_id"], result["error"],
                    )
            except Exception as exc:
                logger.error("dataforseo: unhandled error for client %s: %s", cfg["client_id"], exc)

    except Exception as exc:
        logger.error("dataforseo: run_weekly_for_all_clients failed: %s", exc)


def reset_monthly_budgets() -> None:
    """APScheduler entry point: reset budget_used_this_month on the 1st of each month."""
    try:
        get_supabase().table("dataforseo_configs").update({
            "budget_used_this_month": 0,
            "budget_reset_at":        datetime.now(timezone.utc).isoformat(),
            "updated_at":             datetime.now(timezone.utc).isoformat(),
        }).execute()
        logger.info("dataforseo: monthly budgets reset for all clients")
    except Exception as exc:
        logger.error("dataforseo: budget reset failed: %s", exc)
