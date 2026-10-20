import logging
import threading

from supabase import create_client

from .config import settings

logger = logging.getLogger(__name__)

# One Supabase client PER THREAD.
#
# Since routes run in FastAPI's threadpool (PR #14), many threads call Supabase
# at the same time. A single shared client means a single httpx client — and
# postgrest-py opens it with HTTP/2, whose connection handling is not safe to
# share across threads: on 2026-10-20 the API stopped answering (TCP/TLS ok, no
# HTTP bytes) a few hours after #14. A client per thread removes the sharing;
# the explicit timeout makes sure a stuck call frees its thread instead of
# holding it for postgrest's default 120 s.
_local = threading.local()
_DB_TIMEOUT_S = 20


def _new_client():
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in your .env"
        )
    try:
        from supabase import ClientOptions

        opts = ClientOptions(
            postgrest_client_timeout=_DB_TIMEOUT_S,
            storage_client_timeout=_DB_TIMEOUT_S,
        )
        return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY, options=opts)
    except Exception as exc:  # older/newer supabase-py signature — never block startup
        logger.warning("supabase ClientOptions unavailable (%s); using defaults", exc)
        return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


def get_supabase():
    """Return this thread's Supabase client (service-role key, server-side only)."""
    client = getattr(_local, "client", None)
    if client is None:
        client = _new_client()
        _local.client = client
    return client


def _cache_clear() -> None:
    """Drop this thread's client (tests)."""
    _local.client = None


# Backwards compatibility with code/tests that used the old lru_cache API.
get_supabase.cache_clear = _cache_clear  # type: ignore[attr-defined]
