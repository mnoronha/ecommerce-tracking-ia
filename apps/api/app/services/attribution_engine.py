"""
Unified cross-platform attribution engine — the flagship feature.

Each ad platform self-reports conversions:
  Meta says: "I drove 50 sales"
  Google says: "I drove 35 sales"
  TikTok says: "I drove 20 sales"
  Total: 105 → but the merchant only had 80 actual sales.

This engine resolves overlap by reading visitors.utm_history (full multi-touch
journey captured by the pixel) and assigning fractional credit per touchpoint
based on the chosen attribution model. Dashboard then shows unified ROAS by
platform alongside platform-reported ROAS — overclaim becomes visible.

Models implemented:
  last_click       — 100% credit to last touchpoint (Meta default)
  first_click      — 100% credit to first touchpoint (acquisition view)
  linear           — equal credit across all touchpoints (1/n each)
  time_decay       — exponential decay, half-life 7 days (recent matters more)
  position_based   — 40% first + 40% last + 20% middle (split equally)
"""

import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Optional, Iterable

from ..database import get_supabase

logger = logging.getLogger(__name__)

ALL_MODELS = ('last_click', 'first_click', 'linear', 'time_decay', 'position_based')

# Half-life for time_decay (in days)
_TIME_DECAY_HALFLIFE_DAYS = 7.0


# ── Platform inference from UTM source/medium ─────────────────────────────────

def _infer_platform(source: Optional[str], medium: Optional[str], gclid_present: bool) -> str:
    s = (source or '').lower()
    m = (medium or '').lower()

    if gclid_present or 'google' in s or m in ('cpc', 'paid_search', 'ppc'):
        return 'google'
    if 'facebook' in s or 'instagram' in s or 'meta' in s or 'fb' in s:
        return 'meta'
    if 'tiktok' in s:
        return 'tiktok'
    if 'pinterest' in s:
        return 'pinterest'
    if not s and not m:
        return 'direct'
    if m in ('email', 'newsletter'):
        return 'email'
    if m in ('organic',) or 'google' in s and m == 'organic':
        return 'organic'
    if 'shopify' in s:
        return 'shopify'
    return 'other'


# ── Model implementations ─────────────────────────────────────────────────────
# Each takes a list of touchpoints + the order timestamp, returns list of
# credits (one per touchpoint) summing to 1.0.

def _last_click(touches: list, order_ts: datetime) -> list[float]:
    n = len(touches)
    if n == 0:
        return []
    return [0.0] * (n - 1) + [1.0]


def _first_click(touches: list, order_ts: datetime) -> list[float]:
    n = len(touches)
    if n == 0:
        return []
    return [1.0] + [0.0] * (n - 1)


def _linear(touches: list, order_ts: datetime) -> list[float]:
    n = len(touches)
    if n == 0:
        return []
    each = 1.0 / n
    return [each] * n


def _time_decay(touches: list, order_ts: datetime) -> list[float]:
    """Exponential decay; weight = 0.5^(days_before_order / halflife)."""
    if not touches:
        return []
    halflife = _TIME_DECAY_HALFLIFE_DAYS * 24 * 3600
    weights = []
    for t in touches:
        ts = _parse_ts(t.get('ts'))
        delta_s = max(0.0, (order_ts - ts).total_seconds()) if ts else halflife
        w = 0.5 ** (delta_s / halflife)
        weights.append(w)
    total = sum(weights)
    if total == 0:
        return _linear(touches, order_ts)
    return [w / total for w in weights]


def _position_based(touches: list, order_ts: datetime) -> list[float]:
    """40% first + 40% last + 20% middle (split equally across middle touches)."""
    n = len(touches)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    if n == 2:
        return [0.5, 0.5]
    middle_count = n - 2
    middle_each = 0.20 / middle_count
    return [0.40] + [middle_each] * middle_count + [0.40]


_MODEL_FUNCS = {
    'last_click':     _last_click,
    'first_click':    _first_click,
    'linear':         _linear,
    'time_decay':     _time_decay,
    'position_based': _position_based,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        return None


def _build_journey(visitor: dict, order_created_at: datetime) -> list[dict]:
    """
    Build the multi-touch journey for one visitor leading up to an order.

    Combines:
      - visitors.utm_history (list of {ts, source, medium, campaign})
      - Plus an implicit "first touch" from first_utm_* if utm_history is empty

    Filters to touchpoints before the order timestamp.
    """
    history = visitor.get('utm_history') or []
    if not isinstance(history, list):
        history = []

    # Fallback: synthesize a single touchpoint from first_utm_* fields
    if not history and visitor.get('first_utm_source'):
        history = [{
            'ts':       visitor.get('first_seen_at') or visitor.get('last_seen_at'),
            'source':   visitor.get('first_utm_source'),
            'medium':   visitor.get('first_utm_medium'),
            'campaign': visitor.get('first_utm_campaign'),
        }]

    # Filter to before order, drop entries without source
    valid = []
    for h in history:
        if not isinstance(h, dict):
            continue
        if not h.get('source'):
            continue
        ts = _parse_ts(h.get('ts'))
        if ts and ts > order_created_at:
            continue
        valid.append({
            'ts':       h.get('ts'),
            'source':   h.get('source'),
            'medium':   h.get('medium'),
            'campaign': h.get('campaign'),
        })

    return valid


# ── Main entry: compute attribution for a single order ────────────────────────

def attribute_order(order: dict, visitor: Optional[dict]) -> int:
    """
    Compute and persist attribution credits for one order, across all models.
    Returns number of attribution rows inserted.
    """
    if not order or not order.get('id'):
        return 0
    sb = get_supabase()

    order_total = float(order.get('total_price') or 0)
    if order_total <= 0:
        return 0

    order_ts = _parse_ts(order.get('created_at')) or datetime.now(timezone.utc)
    journey  = _build_journey(visitor or {}, order_ts) if visitor else []

    # ── Fallback 1: order's own UTM fields (set by webhook adapter) ─────────
    # The pixel doesn't always persist utm_history on the visitor row, so
    # we use orders.utm_* as a strong signal when present.
    if not journey and order.get('utm_source'):
        journey = [{
            'ts':       order.get('created_at'),
            'source':   order.get('utm_source'),
            'medium':   order.get('utm_medium'),
            'campaign': order.get('utm_campaign'),
        }]

    # ── Fallback 2: attribution_cookies (recent visitor session) ────────────
    # We persist a rolling list of UTM-tagged visits keyed by visitor_cookie
    # and email. Use the most recent matching cookie to recover attribution
    # when the visitor record was created fresh from a webhook (guest PIX flow).
    if not journey and order.get('client_id'):
        try:
            visitor_cookie = (visitor or {}).get('visitor_id')
            cookie_q = (
                sb.table('attribution_cookies')
                .select('utm_source, utm_medium, utm_campaign, utm_content, created_at')
                .eq('client_id', order['client_id'])
                .not_.is_('utm_source', None)
                .order('created_at', desc=True)
                .limit(1)
            )
            if visitor_cookie:
                cookie_q = cookie_q.eq('visitor_cookie_id', visitor_cookie)
            elif order.get('email'):
                cookie_q = cookie_q.eq('email', str(order['email']).strip().lower())
            else:
                cookie_q = None
            if cookie_q is not None:
                res = cookie_q.execute()
                if res and res.data:
                    c = res.data[0]
                    journey = [{
                        'ts':       c.get('created_at') or order.get('created_at'),
                        'source':   c.get('utm_source'),
                        'medium':   c.get('utm_medium'),
                        'campaign': c.get('utm_campaign'),
                    }]
        except Exception as exc:
            logger.debug('attribution_cookies fallback failed for %s: %s', order.get('id'), exc)

    # If no journey, attribute 100% to "direct" for all models
    if not journey:
        journey = [{
            'ts':       order.get('created_at'),
            'source':   None,
            'medium':   None,
            'campaign': None,
        }]

    gclid_present = bool((visitor or {}).get('gclid'))

    # Delete any prior attributions for this order (re-attribute is idempotent)
    try:
        sb.table('order_attributions').delete().eq('order_id', order['id']).execute()
    except Exception as exc:
        logger.debug('clear prior attribution for %s: %s', order['id'], exc)

    rows_to_insert = []
    for model in ALL_MODELS:
        credits = _MODEL_FUNCS[model](journey, order_ts)
        for idx, (tp, credit) in enumerate(zip(journey, credits)):
            if credit <= 0:
                continue
            rows_to_insert.append({
                'client_id':          order['client_id'],
                'order_id':           order['id'],
                'touchpoint_index':   idx,
                'total_touchpoints':  len(journey),
                'source':             tp.get('source'),
                'medium':             tp.get('medium'),
                'campaign':           tp.get('campaign'),
                'platform':           _infer_platform(tp.get('source'), tp.get('medium'), gclid_present and idx == len(journey) - 1),
                'touchpoint_at':      tp.get('ts'),
                'model':              model,
                'credit':             round(float(credit), 5),
                'attributed_revenue': round(order_total * credit, 2),
            })

    if not rows_to_insert:
        return 0

    try:
        sb.table('order_attributions').insert(rows_to_insert).execute()
    except Exception as exc:
        logger.error('attribute_order insert failed for %s: %s', order['id'], exc)
        return 0

    return len(rows_to_insert)


def recompute_for_client(client_uuid: str, days: int = 90) -> dict:
    """
    Recompute attribution for all paid orders of one client in the last N days.
    Returns counters for monitoring.
    """
    sb = get_supabase()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    orders_resp = (
        sb.table('orders')
        .select('id, client_id, visitor_id, total_price, created_at, '
                'utm_source, utm_medium, utm_campaign, utm_content, email')
        .eq('client_id', client_uuid)
        .eq('financial_status', 'paid')
        .gte('created_at', cutoff)
        .limit(5000)
        .execute()
    )
    orders = orders_resp.data or []

    if not orders:
        return {'orders_processed': 0, 'attributions_written': 0}

    # Pre-fetch visitors in one batch (avoid N+1)
    visitor_ids = {o['visitor_id'] for o in orders if o.get('visitor_id')}
    visitors_by_id: dict = {}
    if visitor_ids:
        v_resp = (
            sb.table('visitors')
            .select('id, gclid, utm_history, first_utm_source, first_utm_medium, first_utm_campaign, first_seen_at, last_seen_at')
            .in_('id', list(visitor_ids))
            .execute()
        )
        for v in (v_resp.data or []):
            visitors_by_id[v['id']] = v

    total_attr = 0
    for o in orders:
        v = visitors_by_id.get(o.get('visitor_id'))
        total_attr += attribute_order(o, v)

    return {
        'orders_processed':     len(orders),
        'attributions_written': total_attr,
    }


# ── Summary aggregation ───────────────────────────────────────────────────────

def get_summary(client_uuid: str, model: str = 'last_click', days: int = 30) -> dict:
    """
    Aggregate attribution data for the dashboard.

    Returns:
      {
        model: 'last_click',
        days:  30,
        total_revenue: 12345.67,
        by_platform: [{platform, revenue, conversions, share_pct}, ...],
        by_source:   [{source, medium, campaign, revenue, conversions}, ...],
      }
    """
    if model not in ALL_MODELS:
        return {'error': f'invalid model: {model}'}

    sb = get_supabase()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Fetch all attributions for this client + model + period
    resp = (
        sb.table('order_attributions')
        .select('platform, source, medium, campaign, attributed_revenue, credit, order_id')
        .eq('client_id', client_uuid)
        .eq('model', model)
        .gte('computed_at', cutoff)
        .limit(20000)
        .execute()
    )
    rows = resp.data or []

    total_revenue = round(sum(float(r.get('attributed_revenue') or 0) for r in rows), 2)

    # By platform
    plat_agg: dict = {}
    for r in rows:
        p = r.get('platform') or 'other'
        if p not in plat_agg:
            plat_agg[p] = {'revenue': 0.0, 'conversions': 0.0, 'orders': set()}
        plat_agg[p]['revenue']     += float(r.get('attributed_revenue') or 0)
        plat_agg[p]['conversions'] += float(r.get('credit') or 0)
        plat_agg[p]['orders'].add(r.get('order_id'))

    by_platform = sorted([
        {
            'platform':    p,
            'revenue':     round(d['revenue'], 2),
            'conversions': round(d['conversions'], 2),
            'orders':      len(d['orders']),
            'share_pct':   round(d['revenue'] / total_revenue * 100, 1) if total_revenue > 0 else 0.0,
        }
        for p, d in plat_agg.items()
    ], key=lambda x: x['revenue'], reverse=True)

    # By source/medium/campaign
    src_agg: dict = {}
    for r in rows:
        key = (r.get('source') or 'direct', r.get('medium') or '', r.get('campaign') or '')
        if key not in src_agg:
            src_agg[key] = {'revenue': 0.0, 'conversions': 0.0}
        src_agg[key]['revenue']     += float(r.get('attributed_revenue') or 0)
        src_agg[key]['conversions'] += float(r.get('credit') or 0)

    by_source = sorted([
        {
            'source':      k[0],
            'medium':      k[1] or None,
            'campaign':    k[2] or None,
            'revenue':     round(d['revenue'], 2),
            'conversions': round(d['conversions'], 2),
        }
        for k, d in src_agg.items()
    ], key=lambda x: x['revenue'], reverse=True)[:50]

    return {
        'model':         model,
        'days':          days,
        'total_revenue': total_revenue,
        'by_platform':   by_platform,
        'by_source':     by_source,
    }
