"""
Event-Driven Core → Hermes Webhook Dispatcher (PRD §3 & §16).

When a canonical report (H7 weekly / H8 monthly / pilot_run) writes a new
`WAITING_FOR_HERMES_ANALYSIS` handoff contract, Core fires a non-blocking,
authenticated webhook notification to Hermes so Hermes starts analysis
immediately instead of waiting up to 10 minutes for the polling tick.

Design invariants (AGENTS.md & PRD §3 / §16 / §21.4):
  - Non-blocking: NEVER blocks FastAPI's asyncio event loop or report jobs.
    Uses `asyncio.create_task(asyncio.to_thread(...))` when called inside an
    active event loop, or a daemon thread when called from sync code.
  - Safe fallback: if `HERMES_WEBHOOK_URL` is unset or all retries fail,
    logs the status and returns cleanly — the 10-minute Hermes polling loop
    (`/agency/v1/canonical-reports/analysis-requests/pending`) remains intact.
  - Authenticated: signs the JSON payload with HMAC-SHA256 (`X-Noro-Signature`)
    when `HERMES_WEBHOOK_SECRET` is set, and sends `Authorization: Bearer ...`
    when `HERMES_WEBHOOK_TOKEN` is set. Secrets are NEVER logged.
  - No mutation of immutable report artifacts or status transitions.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

EVENT_REPORT_WAITING = "report.waiting_for_hermes_analysis"
_RETRY_BACKOFF_SECONDS = (0.5, 1.5, 3.0)


def sign_hermes_payload(raw_body: bytes, secret: str) -> str:
    """Compute `sha256=<hex>` HMAC signature over raw UTF-8 JSON body."""
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_hermes_webhook_payload(
    analysis_request_id: str,
    handoff: dict,
    *,
    emitted_at: Optional[str] = None,
) -> dict[str, Any]:
    """Build compact, deterministic webhook event payload for Hermes."""
    client_id = handoff.get("client_id") or ""
    report_id = handoff.get("report_id") or ""
    report_version = handoff.get("report_version")
    period_start = handoff.get("period_start") or ""
    period_end = handoff.get("period_end") or ""

    qs = f"?period_start={period_start}&period_end={period_end}" if (period_start and period_end) else ""
    return {
        "event":                EVENT_REPORT_WAITING,
        "schema_version":       "1.0",
        "event_id":             analysis_request_id,
        "analysis_request_id":  analysis_request_id,
        "report_id":            report_id,
        "report_version":       report_version,
        "client_id":            client_id,
        "report_type":          handoff.get("report_type"),
        "period_start":         period_start,
        "period_end":           period_end,
        "context_bundle_url":   f"/agency/v1/clients/{client_id}/context-bundle{qs}" if client_id else None,
        "analysis_request_url": f"/agency/v1/canonical-reports/{report_id}/analysis-request" if report_id else None,
        "callback_url":         f"/agency/v1/canonical-reports/{report_id}/analysis" if report_id else None,
        "emitted_at":           emitted_at or datetime.now(timezone.utc).isoformat(),
    }


def dispatch_hermes_analysis_webhook_sync(
    payload: dict[str, Any],
    *,
    webhook_url: Optional[str] = None,
    webhook_secret: Optional[str] = None,
    webhook_token: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    max_retries: Optional[int] = None,
    http_post: Optional[Callable[..., Any]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """
    Synchronously send the Hermes webhook with exponential backoff retry.
    Safe to execute inside a worker thread / `asyncio.to_thread`. Never raises.
    """
    url = (webhook_url if webhook_url is not None else getattr(settings, "HERMES_WEBHOOK_URL", "")).strip()
    if not url:
        return {
            "status":      "SKIPPED_UNCONFIGURED",
            "attempts":    0,
            "http_status": None,
            "error":       None,
            "event_id":    payload.get("event_id"),
        }

    secret = (webhook_secret if webhook_secret is not None else getattr(settings, "HERMES_WEBHOOK_SECRET", "")).strip()
    token = (webhook_token if webhook_token is not None else getattr(settings, "HERMES_WEBHOOK_TOKEN", "")).strip()
    timeout = float(timeout_seconds if timeout_seconds is not None else getattr(settings, "HERMES_WEBHOOK_TIMEOUT_SECONDS", 5.0))
    retries = max(1, int(max_retries if max_retries is not None else getattr(settings, "HERMES_WEBHOOK_MAX_RETRIES", 3)))

    raw_body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    headers: dict[str, str] = {
        "Content-Type":     "application/json; charset=utf-8",
        "X-Noro-Event":     str(payload.get("event") or EVENT_REPORT_WAITING),
        "X-Noro-Delivery":  str(payload.get("event_id") or ""),
        "X-Noro-Timestamp": str(int(time.time())),
    }
    if secret:
        headers["X-Noro-Signature"] = sign_hermes_payload(raw_body, secret)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_status: Optional[int] = None
    last_error: Optional[str] = None

    for attempt in range(1, retries + 1):
        try:
            if http_post is not None:
                resp = http_post(url, content=raw_body, headers=headers, timeout=timeout)
            else:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.post(url, content=raw_body, headers=headers)

            status_code = int(getattr(resp, "status_code", 0) or 0)
            last_status = status_code

            if 200 <= status_code < 300:
                logger.info(
                    "hermes_webhook: DELIVERED event=%s client=%s report=%s v%s (attempt=%d/%d, http=%d)",
                    payload.get("event_id"),
                    payload.get("client_id"),
                    payload.get("report_id"),
                    payload.get("report_version"),
                    attempt,
                    retries,
                    status_code,
                )
                return {
                    "status":      "DELIVERED",
                    "attempts":    attempt,
                    "http_status": status_code,
                    "error":       None,
                    "event_id":    payload.get("event_id"),
                }

            # Non-retryable 4xx (except 429 Too Many Requests)
            if 400 <= status_code < 500 and status_code != 429:
                last_error = f"http_{status_code}"
                logger.warning(
                    "hermes_webhook: non-retryable HTTP %d for event=%s — falling back to Hermes polling",
                    status_code,
                    payload.get("event_id"),
                )
                break

            last_error = f"http_{status_code}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            logger.warning(
                "hermes_webhook: attempt %d/%d failed for event=%s: %s",
                attempt,
                retries,
                payload.get("event_id"),
                last_error,
            )

        if attempt < retries:
            delay = _RETRY_BACKOFF_SECONDS[min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)]
            sleep_fn(delay)

    logger.warning(
        "hermes_webhook: FAILED_FALLBACK_POLLING event=%s attempts=%d last_status=%s error=%s",
        payload.get("event_id"),
        retries,
        last_status,
        last_error,
    )
    return {
        "status":      "FAILED_FALLBACK_POLLING",
        "attempts":    retries,
        "http_status": last_status,
        "error":       last_error,
        "event_id":    payload.get("event_id"),
    }


def trigger_hermes_analysis_webhook_async(
    analysis_request_id: str,
    handoff: dict,
    *,
    webhook_url: Optional[str] = None,
    webhook_secret: Optional[str] = None,
    webhook_token: Optional[str] = None,
    http_post: Optional[Callable[..., Any]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """
    Fire-and-forget non-blocking trigger when a report enters `WAITING_FOR_HERMES_ANALYSIS`.

    - Returns immediately with `{dispatched: False, status: "SKIPPED_UNCONFIGURED"}`
      if `HERMES_WEBHOOK_URL` is not configured.
    - When inside a running asyncio event loop, schedules via `asyncio.create_task(asyncio.to_thread(...))`.
    - When called from sync code (FastAPI threadpool or APScheduler worker),
      spawns a daemon thread so the caller is never blocked.
    """
    url = (webhook_url if webhook_url is not None else getattr(settings, "HERMES_WEBHOOK_URL", "")).strip()
    if not url:
        return {
            "dispatched": False,
            "status":     "SKIPPED_UNCONFIGURED",
            "event_id":   analysis_request_id,
        }

    payload = build_hermes_webhook_payload(analysis_request_id, handoff)

    def _run() -> dict[str, Any]:
        return dispatch_hermes_analysis_webhook_sync(
            payload,
            webhook_url=url,
            webhook_secret=webhook_secret,
            webhook_token=webhook_token,
            http_post=http_post,
            sleep_fn=sleep_fn,
        )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(asyncio.to_thread(_run))
        mode = "asyncio_task"
    except RuntimeError:
        t = threading.Thread(
            target=_run,
            name=f"hermes-webhook-{analysis_request_id[:8]}",
            daemon=True,
        )
        t.start()
        mode = "daemon_thread"

    return {
        "dispatched": True,
        "status":     "QUEUED",
        "mode":       mode,
        "event_id":   analysis_request_id,
    }
