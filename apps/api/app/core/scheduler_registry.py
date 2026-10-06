"""
Scheduler registry — thin singleton that bridges main.py's APScheduler instance
to the rest of the codebase without creating circular imports.

main.py calls register() once at module load.
Any other module calls get_scheduler() / get_job_runs().
"""

from __future__ import annotations

from typing import Any, Optional

_scheduler: Optional[Any] = None
_job_runs: dict = {}  # same dict object as main._JOB_RUNS; mutations are visible here


def register(scheduler: Any, job_runs: dict) -> None:
    global _scheduler, _job_runs
    _scheduler = scheduler
    _job_runs = job_runs  # reference, not copy — mutations in main.py are visible


def get_scheduler() -> Optional[Any]:
    return _scheduler


def get_job_runs() -> dict:
    return _job_runs
