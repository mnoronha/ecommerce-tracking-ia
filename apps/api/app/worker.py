"""
Dedicated APScheduler Worker Entrypoint (PRD §21.4).

Runs all Noro Platform background jobs in an isolated Railway worker process,
completely decoupled from the FastAPI HTTP process (`uvicorn app.main:app`).

Concurrency safety:
  - Acquires a PostgreSQL session-level advisory lock (`pg_try_advisory_lock`)
    BEFORE starting APScheduler.
  - If another worker already holds the lock, refuses to start APScheduler
    (preventing duplicate job execution across deploys or replicas).
  - Monitors lock liveness via periodic heartbeat (`SELECT 1`); if the Postgres
    session drops, stops APScheduler immediately to prevent split-brain.
  - Handles SIGTERM / SIGINT cleanly: stops APScheduler and calls
    `pg_advisory_unlock`.

Usage (Railway worker service):
  python -m app.worker

Required env vars on Railway:
  - Web service:    ENABLE_EMBEDDED_SCHEDULER=false
  - Worker service: DATABASE_URL=<postgres_dsn>  (plus existing SUPABASE_* env vars)
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from typing import Any, Callable, Optional

from .config import settings
from .core.scheduler_setup import build_scheduler
from .core.worker_lock import DEFAULT_WORKER_LOCK_NAME, PostgresAdvisoryLock

logger = logging.getLogger("app.worker")


def run_worker(
    *,
    lock: Optional[PostgresAdvisoryLock] = None,
    scheduler_factory: Optional[Callable[[], tuple[Any, dict]]] = None,
    heartbeat_seconds: float = 30.0,
    stop_event: Optional[threading.Event] = None,
) -> int:
    """
    Acquire the PostgreSQL advisory lock, run APScheduler until stopped (or lock lost),
    and release cleanly on shutdown.

    Returns:
      0 — clean shutdown (SIGTERM/SIGINT or lock already held by another leader)
      1 — lock connection lost mid-run (triggers orchestrator restart)
    """
    worker_lock = lock or PostgresAdvisoryLock(
        lock_name=os.getenv("WORKER_LOCK_NAME", DEFAULT_WORKER_LOCK_NAME),
    )
    shutdown_evt = stop_event or threading.Event()
    make_scheduler = scheduler_factory or build_scheduler

    if not worker_lock.acquire():
        logger.warning(
            "worker: did not acquire pg_try_advisory_lock (%s) — exiting without starting APScheduler",
            worker_lock.lock_name,
        )
        return 0

    scheduler = None
    exit_code = 0
    try:
        scheduler, _ = make_scheduler()
        scheduler.start()
        job_count = len(scheduler.get_jobs()) if hasattr(scheduler, "get_jobs") else 0
        logger.info(
            "worker: APScheduler started under advisory lock %s (%d jobs)",
            worker_lock.lock_name,
            job_count,
        )

        while not shutdown_evt.wait(timeout=heartbeat_seconds):
            if not worker_lock.verify_alive():
                logger.error("worker: advisory lock connection lost — stopping APScheduler to avoid split-brain")
                exit_code = 1
                break
    finally:
        if scheduler is not None:
            try:
                scheduler.shutdown(wait=True)
            except Exception as exc:
                logger.warning("worker: scheduler shutdown error: %s", exc)
        worker_lock.release()
        logger.info("worker: stopped cleanly (exit_code=%d)", exit_code)

    return exit_code


def main() -> None:
    logging.basicConfig(
        level=logging.DEBUG if settings.DEBUG else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    stop_event = threading.Event()

    def _handle_signal(signum, _frame) -> None:
        logger.info("worker: received signal %s — initiating graceful shutdown", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    heartbeat_s = float(os.getenv("WORKER_HEARTBEAT_SECONDS", "30"))
    code = run_worker(heartbeat_seconds=heartbeat_s, stop_event=stop_event)
    sys.exit(code)


if __name__ == "__main__":
    main()
