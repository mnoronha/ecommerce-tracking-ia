"""
Conversion send guard — hard kill switch for ad-platform conversion sends.

Env var CONVERSION_SENDS_DISABLED (Railway):
    ""                      → no client blocked (default, current behaviour)
    "enutri"                → block one client (slug, pixel_id or UUID all work)
    "enutri,dipua-qe5p"     → block several
    "*"                     → block every client

When a client is blocked, NOTHING is sent to Meta CAPI, GA4 Measurement
Protocol, Google Ads, TikTok or Pinterest for it — purchases, refunds,
mid-funnel and browser-pixel events alike — even if its credentials are
filled in. Tracking (events, orders, tracking_order_matches, shadow payloads)
keeps working: this guard only sits in front of outbound sends.

Per-channel exceptions — env var CONVERSION_SENDS_ALLOW (Railway):
    "enutri:ga4_noro"       → let ONE channel through for a blocked client
    "enutri:ga4_noro,lk-sneakers:ga4_noro"
Entries are "<client>:<channel>" (client = slug, pixel_id or UUID). Only the
named channel is released; every other send for that client stays blocked.

Fail-closed: if the list is non-empty and the client cannot be resolved
(DB error), the send is blocked. A missed conversion is recoverable;
a conversion sent for a client that must stay in tracking-only mode is not.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

ENV_VAR = "CONVERSION_SENDS_DISABLED"
ALLOW_ENV_VAR = "CONVERSION_SENDS_ALLOW"
_CACHE_TTL_S = 300
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_alias_cache: dict[str, tuple[float, frozenset]] = {}


def blocked_identifiers() -> frozenset:
    raw = os.environ.get(ENV_VAR, "") or ""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def allowed_pairs() -> frozenset:
    raw = os.environ.get(ALLOW_ENV_VAR, "") or ""
    out = set()
    for p in raw.split(","):
        client, _, channel = p.strip().lower().partition(":")
        if client and channel:
            out.add((client, channel))
    return frozenset(out)


def _channel_allowed(ident: str, channel: str, sb=None) -> bool:
    """True when CONVERSION_SENDS_ALLOW releases this exact client+channel."""
    pairs = allowed_pairs()
    ch = (channel or "").strip().lower()
    if not pairs or not ch or not ident:
        return False
    wanted = {c for c, chan in pairs if chan == ch}
    if not wanted:
        return False
    if ident.lower() in wanted:
        return True
    aliases = _lookup_aliases(ident, sb)
    return bool(aliases and aliases & wanted)


def _lookup_aliases(identifier: str, sb=None) -> Optional[frozenset]:
    """All identifiers of the client (id, slug, pixel_id), lowercased. None on DB error."""
    now = time.monotonic()
    hit = _alias_cache.get(identifier)
    if hit and now - hit[0] < _CACHE_TTL_S:
        return hit[1]
    try:
        if sb is None:
            from ..database import get_supabase
            sb = get_supabase()
        columns = ["pixel_id", "client_id"] + (["id"] if _UUID_RE.match(identifier) else [])
        row = None
        for col in columns:
            res = sb.table("clients").select("id, client_id, pixel_id").eq(col, identifier).limit(1).execute()
            if res and res.data:
                row = res.data[0]
                break
        aliases = frozenset(
            str(v).strip().lower() for v in ((row or {}).get("id"), (row or {}).get("client_id"), (row or {}).get("pixel_id")) if v
        ) | {identifier.lower()}
        _alias_cache[identifier] = (now, aliases)
        return aliases
    except Exception as exc:
        logger.error("send_guard: client lookup failed for %s: %s", identifier, exc)
        return None


def sends_blocked(identifier, channel: str = "", sb=None) -> bool:
    """
    True when conversion sends must be skipped for this client.
    `identifier` may be the client's pixel_id, slug or UUID.
    """
    blocked = blocked_identifiers()
    if not blocked:
        return False
    ident = str(identifier or "").strip()
    reason = None
    if "*" in blocked:
        reason = "all clients"
    elif ident.lower() in blocked:
        reason = ident
    else:
        aliases = _lookup_aliases(ident, sb) if ident else None
        if aliases is None:
            reason = "client lookup failed (fail-closed)"
        elif aliases & blocked:
            reason = ident
    if reason and _channel_allowed(ident, channel, sb):
        logger.info("send_guard: %s send allowed for %s by %s", channel, ident, ALLOW_ENV_VAR)
        return False
    if reason:
        logger.info("send_guard: %s send skipped for %s — %s=%s",
                    channel or "conversion", ident or "?", ENV_VAR, reason)
        return True
    return False


def clear_cache() -> None:
    _alias_cache.clear()
