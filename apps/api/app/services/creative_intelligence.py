"""
Creative Intelligence — Claude Vision-powered analysis of ad creatives.

Weekly job that, for each client:
  1. Joins ad_creatives with the last 30d of meta_ad_attributions to get
     per-ad spend / clicks / purchases / ROAS.
  2. Splits ads into top quartile and bottom quartile by ROAS (need at least
     8 ads with usable images).
  3. Sends both groups to Claude Sonnet 4.6 with vision input, asking it to
     identify visual/textual patterns that distinguish winners from losers.
  4. Persists the analysis as an ai_insight with type='creative_analysis'
     and severity='info'.

Cost guard: each run downloads ~16 images (8 top + 8 bottom) and one Claude
Vision call. Capped at ~$0.30 per client per week.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import anthropic
import httpx

from ..config import settings
from ..database import get_supabase

logger = logging.getLogger(__name__)

LOOKBACK_DAYS  = 30
MIN_TOTAL_ADS  = 8       # need at least N ads with metrics to run this
TOP_BOTTOM_N   = 6       # top-6 vs bottom-6 by ROAS
MAX_BODY_CHARS = 200     # body copy snippet sent to Claude


_SYSTEM_PROMPT = """You are a senior performance marketing analyst with deep
experience auditing ad creatives. You will receive two groups of ads from the
same advertiser — TOP performers (high ROAS) and BOTTOM performers (low ROAS) —
each with an image and metadata.

Compare them. Identify visual and textual patterns that distinguish the
winners from the losers. Be specific and actionable: a creative team should be
able to brief their next batch using only your output.

Return EXACTLY this JSON shape (no markdown fences, no preamble):

{
  "summary": "1-2 sentence headline that captures the biggest gap",
  "winning_patterns": [
    "Concrete observation about top creatives, with examples"
  ],
  "losing_patterns": [
    "Concrete observation about bottom creatives, with examples"
  ],
  "next_brief": "What the next creative should look/sound like. Imperative."
}

Rules:
- 3-5 items in each pattern list. Each item is one sentence.
- Reference what's visible in the images (composition, color, model presence,
  product framing, text overlay, headline style).
- Don't speculate about CTR/ROAS causes you can't see. Stick to observable traits.
- Respond in pt-BR."""


def _download_image_b64(url: str) -> Optional[tuple[bytes, str]]:
    """Fetch an image and return (bytes, media_type) for Claude Vision input.
    Skips on non-image responses or timeouts."""
    try:
        resp = httpx.get(url, timeout=10.0, follow_redirects=True)
        if resp.status_code != 200:
            return None
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            return None
        if len(resp.content) > 5 * 1024 * 1024:   # 5MB cap per image
            return None
        return resp.content, ctype
    except Exception as exc:
        logger.debug("creative image fetch failed (%s): %s", url, exc)
        return None


def _fetch_ad_performance(client_uuid: str) -> list[dict]:
    """
    Join ad_creatives with the last 30d of meta_ad_attributions, aggregated per
    ad. Returns rows with: ad_id, ad_name, image_url, spend, purchases, revenue, roas.
    """
    sb = get_supabase()
    since = (datetime.now(timezone.utc).date() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    creatives = (
        sb.table("ad_creatives")
        .select("ad_id, ad_name, image_url, thumbnail_url, body, headline, call_to_action")
        .eq("client_id", client_uuid)
        .execute()
    ).data or []
    if not creatives:
        return []
    by_ad = {c["ad_id"]: c for c in creatives}

    attributions = (
        sb.table("meta_ad_attributions")
        .select("ad_id, spend, clicks, purchases, purchase_value")
        .eq("client_id", client_uuid)
        .gte("date", since)
        .in_("ad_id", list(by_ad.keys()))
        .execute()
    ).data or []

    agg: dict[str, dict] = {}
    for a in attributions:
        ad_id = a["ad_id"]
        b = agg.setdefault(ad_id, {"spend": 0.0, "clicks": 0, "purchases": 0, "revenue": 0.0})
        b["spend"]     += float(a.get("spend") or 0)
        b["clicks"]    += int(a.get("clicks") or 0)
        b["purchases"] += int(a.get("purchases") or 0)
        b["revenue"]   += float(a.get("purchase_value") or 0)

    out: list[dict] = []
    for ad_id, perf in agg.items():
        creative = by_ad.get(ad_id) or {}
        img = creative.get("image_url") or creative.get("thumbnail_url")
        if not img:
            continue
        roas = (perf["revenue"] / perf["spend"]) if perf["spend"] > 0 else None
        out.append({
            "ad_id":          ad_id,
            "ad_name":        creative.get("ad_name") or "—",
            "image_url":      img,
            "body":           (creative.get("body") or "")[:MAX_BODY_CHARS],
            "headline":       creative.get("headline") or "",
            "call_to_action": creative.get("call_to_action") or "",
            "spend":          round(perf["spend"], 2),
            "clicks":         perf["clicks"],
            "purchases":      perf["purchases"],
            "revenue":        round(perf["revenue"], 2),
            "roas":           round(roas, 2) if roas is not None else None,
        })
    return out


def _build_vision_messages(top: list[dict], bottom: list[dict]) -> list[dict]:
    """Build the Anthropic API messages payload with both groups of ads."""
    import base64
    content: list[dict] = [{
        "type": "text",
        "text": (
            "Analise os dois grupos de criativos abaixo e identifique padrões.\n\n"
            f"=== GRUPO TOP ({len(top)} criativos, ROAS médio "
            f"{round(sum((a['roas'] or 0) for a in top) / max(1, len(top)), 2)}x) ===\n"
        ),
    }]

    for ad in top:
        img = _download_image_b64(ad["image_url"])
        if not img:
            continue
        raw, mime = img
        content.append({
            "type": "image",
            "source": {
                "type":       "base64",
                "media_type": mime,
                "data":       base64.standard_b64encode(raw).decode("ascii"),
            },
        })
        content.append({
            "type": "text",
            "text": (
                f"[TOP] ad='{ad['ad_name']}' ROAS={ad['roas']}x spend={ad['spend']} "
                f"purchases={ad['purchases']} headline='{ad['headline']}' "
                f"copy='{ad['body'][:150]}' cta='{ad['call_to_action']}'"
            ),
        })

    content.append({
        "type": "text",
        "text": (
            f"\n=== GRUPO BOTTOM ({len(bottom)} criativos, ROAS médio "
            f"{round(sum((a['roas'] or 0) for a in bottom) / max(1, len(bottom)), 2)}x) ===\n"
        ),
    })

    for ad in bottom:
        img = _download_image_b64(ad["image_url"])
        if not img:
            continue
        raw, mime = img
        content.append({
            "type": "image",
            "source": {
                "type":       "base64",
                "media_type": mime,
                "data":       base64.standard_b64encode(raw).decode("ascii"),
            },
        })
        content.append({
            "type": "text",
            "text": (
                f"[BOTTOM] ad='{ad['ad_name']}' ROAS={ad['roas']}x spend={ad['spend']} "
                f"purchases={ad['purchases']} headline='{ad['headline']}' "
                f"copy='{ad['body'][:150]}' cta='{ad['call_to_action']}'"
            ),
        })

    return [{"role": "user", "content": content}]


def analyze_client(client_uuid: str) -> Optional[dict]:
    """Run vision analysis for one client. Returns the saved insight payload or None."""
    if not settings.ANTHROPIC_API_KEY:
        logger.info("creative_intelligence: ANTHROPIC_API_KEY not configured")
        return None

    rows = _fetch_ad_performance(client_uuid)
    # Require ads with a measurable ROAS for ranking.
    rows = [r for r in rows if r["roas"] is not None and r["spend"] >= 5.0]
    if len(rows) < MIN_TOTAL_ADS:
        logger.info("creative_intelligence: client=%s has only %d ads (need %d)",
                    client_uuid, len(rows), MIN_TOTAL_ADS)
        return None

    rows.sort(key=lambda r: r["roas"] or 0)
    bottom = rows[:TOP_BOTTOM_N]
    top    = rows[-TOP_BOTTOM_N:][::-1]
    if not top or not bottom:
        return None

    messages = _build_vision_messages(top, bottom)
    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            messages=messages,  # type: ignore[arg-type]
        )
        raw = msg.content[0].text.strip()
    except Exception as exc:
        logger.error("creative_intelligence Claude call failed for %s: %s", client_uuid, exc)
        return None

    # Tolerant JSON extraction
    import json, re
    for fence in ("```json", "```"):
        if raw.startswith(fence):
            raw = raw[len(fence):].lstrip("\n")
            break
    raw = re.sub(r"```\s*$", "", raw).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("creative_intelligence: malformed JSON from Claude: %s", exc)
        return None

    insight = {
        "client_id": client_uuid,
        "type":      "creative_analysis",
        "severity":  "info",
        "title":     parsed.get("summary", "Análise de criativos")[:200],
        "content":   parsed.get("summary", ""),
        "data": {
            "winning_patterns": parsed.get("winning_patterns", []),
            "losing_patterns":  parsed.get("losing_patterns", []),
            "next_brief":       parsed.get("next_brief", ""),
            "top_ads":          [{"ad_id": a["ad_id"], "ad_name": a["ad_name"],
                                  "roas": a["roas"], "image_url": a["image_url"]} for a in top],
            "bottom_ads":       [{"ad_id": a["ad_id"], "ad_name": a["ad_name"],
                                  "roas": a["roas"], "image_url": a["image_url"]} for a in bottom],
            "lookback_days":    LOOKBACK_DAYS,
            "ads_analyzed":     len(top) + len(bottom),
        },
    }
    try:
        get_supabase().table("ai_insights").insert(insight).execute()
        logger.info("creative_intelligence: persisted insight for %s", client_uuid)
    except Exception as exc:
        logger.warning("creative_intelligence: insight save failed for %s: %s", client_uuid, exc)
    return insight


def run_weekly_for_all_clients() -> None:
    """Scheduler entry — analyze creatives for every active client with Meta creds."""
    sb = get_supabase()
    try:
        clients = (
            sb.table("clients")
            .select("id, meta_ad_account_id, meta_access_token")
            .eq("is_active", True)
            .execute()
        ).data or []
    except Exception as exc:
        logger.error("creative_intelligence: failed to list clients: %s", exc)
        return

    analyzed = 0
    for c in clients:
        if not (c.get("meta_ad_account_id") and c.get("meta_access_token")):
            continue
        try:
            if analyze_client(c["id"]):
                analyzed += 1
        except Exception as exc:
            logger.warning("creative_intelligence: client %s failed: %s", c.get("id"), exc)
    logger.info("creative_intelligence: weekly run complete, analyzed=%d", analyzed)
