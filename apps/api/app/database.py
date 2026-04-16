from functools import lru_cache
from supabase import create_client
from .config import settings


@lru_cache()
def get_supabase():
    """Return a cached Supabase client (service-role key for server-side use)."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in your .env"
        )
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
