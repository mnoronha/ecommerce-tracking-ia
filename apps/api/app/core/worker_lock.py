"""
PostgreSQL Advisory Lock for single-leader APScheduler worker coordination (PRD §21.4).

Uses session-level `SELECT pg_try_advisory_lock(%s)` / `SELECT pg_advisory_unlock(%s)`
over a dedicated persistent PostgreSQL connection (`DATABASE_URL` or `SUPABASE_DB_URL`).

Why session-level advisory locks?
  - If the worker container crashes or receives SIGKILL, PostgreSQL closes the
    TCP session and automatically releases the advisory lock so a standby worker
    can take over without stale lockfiles.
  - Prevents duplicate APScheduler execution across multiple Railway replicas or
    multi-worker uvicorn deployments.

Supports injectable `connection_factory` for 100% isolated unit tests.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Deterministic 64-bit signed Postgres bigint key for the Noro scheduler leader lock
DEFAULT_WORKER_LOCK_NAME = "noro:apscheduler:leader"


def lock_name_to_key(name: str) -> int:
    """Map an arbitrary lock name deterministically to a signed 64-bit Postgres bigint."""
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _default_pg_connect(dsn: str) -> Any:
    """Connect to PostgreSQL using psycopg (v3) or psycopg2 if installed."""
    try:
        import psycopg  # type: ignore[import-not-found]
        return psycopg.connect(dsn, autocommit=True)
    except ImportError:
        pass
    try:
        import psycopg2  # type: ignore[import-not-found]
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        return conn
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL driver (psycopg or psycopg2) is not installed; "
            "install psycopg[binary] to enable pg_try_advisory_lock"
        ) from exc


class PostgresAdvisoryLock:
    """
    Session-level PostgreSQL advisory lock (`pg_try_advisory_lock` / `pg_advisory_unlock`).

    Usage:
        lock = PostgresAdvisoryLock(dsn=os.getenv("DATABASE_URL"))
        if not lock.acquire():
            sys.exit(0)  # another worker already holds the scheduler lock
        try:
            ...
        finally:
            lock.release()
    """

    def __init__(
        self,
        *,
        lock_name: str = DEFAULT_WORKER_LOCK_NAME,
        dsn: Optional[str] = None,
        connection_factory: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self.lock_name = lock_name
        self.lock_key = lock_name_to_key(lock_name)
        self.dsn = dsn if dsn is not None else (
            os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL") or ""
        )
        self._connect = connection_factory or _default_pg_connect
        self._conn: Any = None
        self._held: bool = False

    @property
    def held(self) -> bool:
        return self._held and self._conn is not None

    def acquire(self) -> bool:
        """
        Attempt to acquire `pg_try_advisory_lock(lock_key)` without blocking.

        Returns True iff this process now holds the lock.
        Returns False if another session already holds it or if acquisition fails.
        """
        if self.held:
            return True
        if not self.dsn:
            logger.error(
                "worker_lock: DATABASE_URL / SUPABASE_DB_URL not set — "
                "cannot acquire pg_try_advisory_lock(%s)",
                self.lock_key,
            )
            return False

        conn = None
        try:
            conn = self._connect(self.dsn)
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (self.lock_key,))
                row = cur.fetchone()
            acquired = bool(row and row[0])
            if acquired:
                self._conn = conn
                self._held = True
                logger.info(
                    "worker_lock: acquired pg_try_advisory_lock name=%s key=%s",
                    self.lock_name,
                    self.lock_key,
                )
                return True

            logger.warning(
                "worker_lock: lock already held by another worker (name=%s key=%s)",
                self.lock_name,
                self.lock_key,
            )
            try:
                conn.close()
            except Exception:
                pass
            return False
        except Exception as exc:
            logger.error(
                "worker_lock: pg_try_advisory_lock failed (name=%s): %s",
                self.lock_name,
                exc,
            )
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            return False

    def verify_alive(self) -> bool:
        """Heartbeat check ensuring the advisory-lock session is still open."""
        if not self.held:
            return False
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception as exc:
            logger.error("worker_lock: connection lost (%s) — marking lock released", exc)
            self._held = False
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            return False

    def release(self) -> bool:
        """Explicitly call `pg_advisory_unlock(lock_key)` and close the session."""
        if self._conn is None:
            self._held = False
            return False
        unlocked = False
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (self.lock_key,))
                row = cur.fetchone()
                unlocked = bool(row and row[0])
        except Exception as exc:
            logger.warning("worker_lock: pg_advisory_unlock failed: %s", exc)
        finally:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            self._held = False
            logger.info(
                "worker_lock: released name=%s key=%s unlocked=%s",
                self.lock_name,
                self.lock_key,
                unlocked,
            )
        return unlocked
