"""
Deterministic Context Bundle Provider for Hermes & Operators (PRD §8.1 / R05).

Consolidates strategic and operational client context into a single compact,
deterministic JSON payload so H7/H8/H9 analyses never lose context on:
  1. client_truth      — goals/targets, active budgets, profile & constraints
  2. promotions        — active commercial promotions / timeline events in period
  3. meetings          — decisions & open action items from the last 3 meetings
  4. relevant_changes  — classified non-noise changes from core_change_log
  5. recent_outcomes   — recent non-superseded canonical outcomes (core_outcomes)
  6. approved_knowledge — top 5 approved learnings (client-scoped + shared)

Design invariants (AGENTS.md & PRD §8.1):
  - Read-only. Never mutates DB state or external platforms.
  - Zero fabricated data: empty sections return explicit empty lists and
    manifest status="EMPTY" (or "UNAVAILABLE" on read error).
  - Strict tenant isolation: resolves canonical client UUID & slug first;
    CLIENT_ONLY knowledge and tenant tables are filtered strictly to that client.
  - Reuses existing canonical foundations (`meeting_context`, `knowledge_retrieval`,
    `change_digest._classify`) without duplicating business logic.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from ..database import get_supabase
from .change_digest import _classify as _classify_change
from .knowledge_retrieval import load_knowledge_context
from .meeting_context import load_meeting_context

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "noro.context-bundle.v1"
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_IMPACT_ORDER = {"high": 0, "medium": 1, "low": 2}


def _resolve_client_identity(db, client_ref: str) -> Optional[dict]:
    """Resolve slug, pixel_id, or canonical UUID to `{id, client_id, pixel_id, name, business_model}`."""
    ref = (client_ref or "").strip()
    if not ref:
        return None
    cols = "id, client_id, pixel_id, name, business_model"
    order = ("id", "client_id", "pixel_id") if _UUID_RE.match(ref) else ("client_id", "pixel_id")
    for field in order:
        try:
            res = db.table("clients").select(cols).eq(field, ref).limit(1).execute()
            if res and res.data:
                row = res.data[0]
                return {
                    "id":             str(row["id"]),
                    "client_id":      row.get("client_id") or row.get("pixel_id") or ref,
                    "pixel_id":       row.get("pixel_id"),
                    "name":           row.get("name") or row.get("client_id") or ref,
                    "business_model": row.get("business_model"),
                }
        except Exception as exc:
            logger.warning("context_bundle: client lookup by %s failed for %s: %s", field, ref, exc)
    return None


def _normalize_period(
    period_start: Optional[str],
    period_end: Optional[str],
    now: Optional[datetime] = None,
) -> tuple[str, str]:
    """Return deterministic ISO YYYY-MM-DD bounds (defaults to last 14 days ending today UTC)."""
    today = (now or datetime.now(timezone.utc)).date()
    end_d = date.fromisoformat(period_end[:10]) if period_end else today
    start_d = (
        date.fromisoformat(period_start[:10])
        if period_start
        else (end_d - timedelta(days=13))
    )
    if start_d > end_d:
        start_d, end_d = end_d, start_d
    return start_d.isoformat(), end_d.isoformat()


def _dates_overlap(
    item_start: Optional[str],
    item_end: Optional[str],
    period_start: str,
    period_end: str,
) -> bool:
    """Return True if [item_start, item_end] overlaps [period_start, period_end] (or if undated)."""
    s = (item_start or "")[:10]
    e = (item_end or item_start or "")[:10]
    if not s and not e:
        return True
    if not s:
        return e >= period_start
    if not e:
        return s <= period_end
    return s <= period_end and e >= period_start


def _load_client_truth_and_budgets(
    db,
    slug: str,
    period_end: str,
) -> tuple[dict, dict, Optional[dict], Optional[dict]]:
    """
    Load `core_client_truth` + active `core_budget_config`.
    Returns `(section_payload, manifest_entry, raw_client_truth, raw_target_truth)`.
    """
    raw_ct: Optional[dict] = None
    raw_tt: Optional[dict] = None
    client_version: Optional[int] = None
    target_version: Optional[int] = None
    valid_from: Optional[str] = None
    truth_unavailable = False

    try:
        res = (
            db.table("core_client_truth")
            .select("client_id, client_version, client_truth, target_version, target_truth, valid_from")
            .eq("client_id", slug)
            .order("valid_from", desc=True)
            .limit(1)
            .execute()
        )
        if res and res.data:
            row = res.data[0]
            raw_ct = row.get("client_truth") if isinstance(row.get("client_truth"), dict) else {}
            raw_tt = row.get("target_truth") if isinstance(row.get("target_truth"), dict) else {}
            client_version = row.get("client_version")
            target_version = row.get("target_version")
            valid_from = str(row.get("valid_from")) if row.get("valid_from") else None
    except Exception as exc:
        logger.warning("context_bundle: core_client_truth load failed for %s: %s", slug, exc)
        truth_unavailable = True

    # Budgets from core_budget_config (prefer period_end's YYYY-MM, fallback to latest per platform)
    budgets: list[dict] = []
    target_month = period_end[:7]
    try:
        bres = (
            db.table("core_budget_config")
            .select("platform, period_label, monthly_budget, currency, monitoring_enabled")
            .eq("client_id", slug)
            .order("period_label", desc=True)
            .limit(20)
            .execute()
        )
        rows = bres.data or []
        exact = [r for r in rows if r.get("period_label") == target_month]
        selected = exact
        if not selected and rows:
            # Take the latest entry per platform deterministically
            seen_plat: set[str] = set()
            selected = []
            for r in rows:
                plat = str(r.get("platform") or "")
                if plat and plat not in seen_plat:
                    seen_plat.add(plat)
                    selected.append(r)
        budgets = [
            {
                "platform":           r.get("platform"),
                "period_label":       r.get("period_label"),
                "monthly_budget":     float(r["monthly_budget"]) if r.get("monthly_budget") is not None else None,
                "currency":           r.get("currency") or "BRL",
                "monitoring_enabled": bool(r.get("monitoring_enabled", True)),
            }
            for r in sorted(selected, key=lambda x: str(x.get("platform") or ""))
        ]
    except Exception as exc:
        logger.warning("context_bundle: core_budget_config load failed for %s: %s", slug, exc)

    ct = raw_ct or {}
    tt = raw_tt or {}

    constraints: list[Any] = []
    for key in ("constraints", "restrictions", "do_not_change", "guardrails"):
        val = ct.get(key)
        if isinstance(val, list):
            constraints.extend(val)
        elif isinstance(val, str) and val.strip():
            constraints.append(val.strip())

    priorities = ct.get("priorities") if isinstance(ct.get("priorities"), list) else []

    profile = {
        "name":           ct.get("name"),
        "business_model": ct.get("business_model"),
        "platform":       ct.get("platform"),
        "currency":       ct.get("currency"),
        "timezone":       ct.get("timezone"),
        "country":        ct.get("country"),
    }
    profile = {k: v for k, v in profile.items() if v is not None}

    has_truth = raw_ct is not None or raw_tt is not None or bool(budgets)
    status = "UNAVAILABLE" if (truth_unavailable and not has_truth) else ("AVAILABLE" if has_truth else "EMPTY")

    payload = {
        "client_version": client_version,
        "target_version": target_version,
        "valid_from":     valid_from,
        "profile":        profile,
        "targets":        tt,
        "budgets":        budgets,
        "constraints":    constraints,
        "priorities":     priorities,
    }
    manifest = {
        "status": status,
        "count":  (1 if raw_ct is not None else 0) + len(budgets),
        "source": "core_client_truth+core_budget_config",
    }
    return payload, manifest, raw_ct, raw_tt


def _load_promotions(
    db,
    client_uuid: str,
    slug: str,
    period_start: str,
    period_end: str,
    raw_ct: Optional[dict],
    raw_tt: Optional[dict],
) -> tuple[list[dict], dict]:
    """
    Collect active promotions / commercial events overlapping [period_start, period_end]:
      1. Structured promotions in client_truth / target_truth (if present)
      2. Timeline annotations in `timeline_annotations` within the period
      3. Manual OFFER changes in `core_change_log` within the period
    """
    items: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    sources_used: list[str] = []
    errors = 0

    def _add(entry: dict) -> None:
        title = str(entry.get("title") or "").strip()
        if not title:
            return
        dedup_key = (
            str(entry.get("source") or ""),
            title.lower(),
            str(entry.get("start_date") or ""),
        )
        if dedup_key in seen:
            return
        seen.add(dedup_key)
        items.append(entry)

    # 1. Structured promotions in client_truth / target_truth
    for src_name, container in (("client_truth", raw_ct or {}), ("target_truth", raw_tt or {})):
        for field in ("promotions", "active_promotions", "campaigns_calendar"):
            raw_list = container.get(field)
            if not isinstance(raw_list, list):
                continue
            for idx, p in enumerate(raw_list):
                if isinstance(p, str) and p.strip():
                    _add({
                        "id":         f"{src_name}:{field}:{idx}",
                        "title":      p.strip(),
                        "source":     src_name,
                        "start_date": None,
                        "end_date":   None,
                        "notes":      None,
                    })
                elif isinstance(p, dict):
                    ps = p.get("start_date") or p.get("starts_at") or p.get("date")
                    pe = p.get("end_date") or p.get("ends_at") or ps
                    if not _dates_overlap(
                        str(ps) if ps else None,
                        str(pe) if pe else None,
                        period_start,
                        period_end,
                    ):
                        continue
                    _add({
                        "id":         str(p.get("id") or f"{src_name}:{field}:{idx}"),
                        "title":      p.get("title") or p.get("name") or p.get("label") or "",
                        "source":     src_name,
                        "start_date": str(ps)[:10] if ps else None,
                        "end_date":   str(pe)[:10] if pe else None,
                        "notes":      p.get("notes") or p.get("description") or p.get("discount"),
                    })
            if raw_list:
                sources_used.append(src_name)

    # 2. timeline_annotations for client_uuid in period
    try:
        ares = (
            db.table("timeline_annotations")
            .select("id, date, label, created_at")
            .eq("client_id", client_uuid)
            .gte("date", period_start)
            .lte("date", period_end)
            .order("date", desc=False)
            .limit(30)
            .execute()
        )
        for row in (ares.data or []):
            _add({
                "id":         str(row["id"]),
                "title":      row.get("label") or "",
                "source":     "timeline_annotations",
                "start_date": str(row.get("date"))[:10] if row.get("date") else None,
                "end_date":   str(row.get("date"))[:10] if row.get("date") else None,
                "notes":      None,
            })
        if ares and ares.data:
            sources_used.append("timeline_annotations")
    except Exception as exc:
        logger.warning("context_bundle: timeline_annotations load failed for %s: %s", slug, exc)
        errors += 1

    # 3. Manual OFFER entries in core_change_log within period
    try:
        ores = (
            db.table("core_change_log")
            .select("id, occurred_at, entity_name_at_time, reason, after_state")
            .eq("client_id", slug)
            .eq("change_type", "OFFER")
            .gte("occurred_at", f"{period_start}T00:00:00+00:00")
            .lte("occurred_at", f"{period_end}T23:59:59+00:00")
            .order("occurred_at", desc=True)
            .limit(15)
            .execute()
        )
        for row in (ores.data or []):
            occ = str(row.get("occurred_at") or "")[:10] or None
            title = row.get("entity_name_at_time") or row.get("reason") or "Alteração de oferta/promoção"
            _add({
                "id":         str(row["id"]),
                "title":      title,
                "source":     "core_change_log:OFFER",
                "start_date": occ,
                "end_date":   occ,
                "notes":      row.get("reason"),
            })
        if ores and ores.data:
            sources_used.append("core_change_log:OFFER")
    except Exception as exc:
        logger.warning("context_bundle: OFFER change_log load failed for %s: %s", slug, exc)
        errors += 1

    items.sort(key=lambda x: (x.get("start_date") or "", x.get("title") or ""))
    status = "AVAILABLE" if items else ("UNAVAILABLE" if errors >= 2 else "EMPTY")
    manifest = {
        "status": status,
        "count":  len(items),
        "source": "+".join(dict.fromkeys(sources_used)) or "client_truth+timeline_annotations+core_change_log",
    }
    return items, manifest


def _load_meetings_section(slug: str, meeting_limit: int) -> tuple[dict, dict]:
    """Reuse canonical `load_meeting_context` capped to the last `meeting_limit` (default 3) meetings."""
    try:
        raw = load_meeting_context(slug, limit=meeting_limit)
    except Exception as exc:
        logger.warning("context_bundle: meeting_context load failed for %s: %s", slug, exc)
        return (
            {
                "recent_meetings":   [],
                "recent_decisions":  [],
                "open_action_items": [],
                "open_questions":    [],
                "total_meetings":    0,
            },
            {"status": "UNAVAILABLE", "count": 0, "source": "core_meetings+core_meeting_summaries"},
        )

    meetings = [
        {
            "id":                 m.get("id"),
            "title":              m.get("title"),
            "started_at":         m.get("started_at"),
            "processing_status":  m.get("processing_status"),
            "summary":            (m.get("summary") or "")[:300] or None,
            "decisions_count":    m.get("decisions_count", 0),
            "action_items_count": m.get("action_items_count", 0),
        }
        for m in (raw.get("recent_meetings") or [])[:meeting_limit]
    ]
    payload = {
        "recent_meetings":   meetings,
        "recent_decisions":  (raw.get("recent_decisions") or [])[:15],
        "open_action_items": (raw.get("open_action_items") or [])[:20],
        "open_questions":    (raw.get("open_questions") or [])[:10],
        "total_meetings":    len(meetings),
    }
    manifest = {
        "status": "AVAILABLE" if meetings else "EMPTY",
        "count":  len(meetings),
        "source": "core_meetings+core_meeting_summaries",
    }
    return payload, manifest


def _load_relevant_changes(
    db,
    slug: str,
    period_start: str,
    period_end: str,
    platform: Optional[str],
    change_limit: int,
) -> tuple[dict, dict]:
    """Load `core_change_log` for period and filter out non-signal noise via `change_digest._classify`."""
    try:
        q = (
            db.table("core_change_log")
            .select(
                "id, occurred_at, channel, entity_type, entity_id, entity_name_at_time, "
                "change_type, before_state, after_state, reason, source, reported_by, outcome_status"
            )
            .eq("client_id", slug)
            .gte("occurred_at", f"{period_start}T00:00:00+00:00")
            .lte("occurred_at", f"{period_end}T23:59:59+00:00")
            .order("occurred_at", desc=True)
            .limit(200)
        )
        if platform:
            q = q.eq("channel", platform.lower())
        rows = q.execute().data or []
    except Exception as exc:
        logger.warning("context_bundle: core_change_log load failed for %s: %s", slug, exc)
        return (
            {"items": [], "total_raw_in_period": 0, "total_relevant": 0, "by_channel": {}},
            {"status": "UNAVAILABLE", "count": 0, "source": "core_change_log"},
        )

    classified: list[dict] = []
    by_channel: dict[str, int] = {}

    for row in rows:
        ch = (row.get("channel") or "unknown").lower()
        by_channel[ch] = by_channel.get(ch, 0) + 1

        res = _classify_change(row)
        if res is None:
            continue
        label, impact, extra = res
        classified.append({
            "id":             str(row["id"]),
            "occurred_at":    row.get("occurred_at"),
            "channel":        ch,
            "entity_type":    row.get("entity_type"),
            "entity_name":    row.get("entity_name_at_time") or row.get("entity_id"),
            "change_type":    label,
            "impact":         impact,
            "source":         row.get("source"),
            "reason":         row.get("reason") or extra.get("reason"),
            "outcome_status": row.get("outcome_status"),
        })

    # Deterministic stable sort: impact asc (high -> medium -> low), then occurred_at desc, then id asc
    classified.sort(key=lambda item: str(item.get("id") or ""))
    classified.sort(key=lambda item: str(item.get("occurred_at") or ""), reverse=True)
    classified.sort(key=lambda item: _IMPACT_ORDER.get(str(item.get("impact") or "low"), 3))

    top_items = classified[:change_limit]
    payload = {
        "items":               top_items,
        "total_raw_in_period": len(rows),
        "total_relevant":      len(classified),
        "by_channel":          dict(sorted(by_channel.items())),
    }
    manifest = {
        "status": "AVAILABLE" if top_items else "EMPTY",
        "count":  len(top_items),
        "source": "core_change_log",
    }
    return payload, manifest


def _load_recent_outcomes(
    db,
    client_uuid: str,
    outcome_limit: int,
) -> tuple[list[dict], dict]:
    """Load recent active (non-SUPERSEDED) canonical outcomes enriched with action metadata."""
    try:
        ores = (
            db.table("core_outcomes")
            .select(
                "id, action_id, recommendation_id, status, confidence, evaluation_window_days, "
                "delta, data_health, measured_at, created_at, supersedes_outcome_id"
            )
            .eq("client_id", client_uuid)
            .order("created_at", desc=True)
            .limit(max(outcome_limit * 2, 20))
            .execute()
        )
        rows = ores.data or []
    except Exception as exc:
        # Fallback if supersedes_outcome_id column is not yet present in older mock/schema
        if "supersedes_outcome_id" in str(exc).lower():
            try:
                ores = (
                    db.table("core_outcomes")
                    .select(
                        "id, action_id, recommendation_id, status, confidence, evaluation_window_days, "
                        "delta, data_health, measured_at, created_at"
                    )
                    .eq("client_id", client_uuid)
                    .order("created_at", desc=True)
                    .limit(max(outcome_limit * 2, 20))
                    .execute()
                )
                rows = ores.data or []
            except Exception as exc2:
                logger.warning("context_bundle: core_outcomes load failed: %s", exc2)
                return [], {"status": "UNAVAILABLE", "count": 0, "source": "core_outcomes"}
        else:
            logger.warning("context_bundle: core_outcomes load failed: %s", exc)
            return [], {"status": "UNAVAILABLE", "count": 0, "source": "core_outcomes"}

    superseded_count = sum(1 for r in rows if r.get("status") == "SUPERSEDED")
    active_rows = [r for r in rows if r.get("status") != "SUPERSEDED"][:outcome_limit]

    action_ids = list({str(r["action_id"]) for r in active_rows if r.get("action_id")})
    actions_by_id: dict[str, dict] = {}
    if action_ids:
        try:
            ares = (
                db.table("core_actions")
                .select("id, action_type, platform, entity_type, executed_at")
                .in_("id", action_ids)
                .execute()
            )
            actions_by_id = {str(a["id"]): a for a in (ares.data or [])}
        except Exception as exc:
            logger.warning("context_bundle: core_actions enrichment failed: %s", exc)

    items: list[dict] = []
    for r in active_rows:
        aid = str(r["action_id"]) if r.get("action_id") else None
        act = actions_by_id.get(aid or "") or {}
        dh = r.get("data_health") if isinstance(r.get("data_health"), dict) else {}
        items.append({
            "id":                     str(r["id"]),
            "action_id":              aid,
            "recommendation_id":      str(r["recommendation_id"]) if r.get("recommendation_id") else None,
            "action_type":            act.get("action_type"),
            "platform":               act.get("platform"),
            "entity_type":            act.get("entity_type"),
            "status":                 r.get("status"),
            "confidence":             r.get("confidence"),
            "evaluation_window_days": r.get("evaluation_window_days"),
            "primary_metric":         dh.get("primary_metric"),
            "delta":                  r.get("delta"),
            "limitations":            dh.get("confounders") or dh.get("limitations") or [],
            "measured_at":            r.get("measured_at") or r.get("created_at"),
            "supersedes_outcome_id":  str(r["supersedes_outcome_id"]) if r.get("supersedes_outcome_id") else None,
        })

    manifest = {
        "status":           "AVAILABLE" if items else "EMPTY",
        "count":            len(items),
        "superseded_count": superseded_count,
        "source":           "core_outcomes+core_actions",
    }
    return items, manifest


def _load_approved_knowledge(
    slug: str,
    platform: Optional[str],
    knowledge_limit: int,
) -> tuple[dict, dict]:
    """
    Reuse canonical `load_knowledge_context` and return top `knowledge_limit` (default 5)
    approved items, prioritising client-specific knowledge before shared agency knowledge.
    """
    try:
        raw = load_knowledge_context(slug, platform=platform, limit=knowledge_limit)
    except Exception as exc:
        logger.warning("context_bundle: knowledge_retrieval failed for %s: %s", slug, exc)
        return (
            {"items": [], "client_count": 0, "shared_count": 0, "total_count": 0},
            {"status": "UNAVAILABLE", "count": 0, "source": "core_agency_knowledge"},
        )

    client_k = raw.get("client_knowledge") or []
    shared_k = raw.get("shared_knowledge") or []
    merged = (client_k + shared_k)[:knowledge_limit]

    payload = {
        "items":        merged,
        "client_count": len(client_k),
        "shared_count": len(shared_k),
        "total_count":  len(merged),
    }
    manifest = {
        "status": "AVAILABLE" if merged else "EMPTY",
        "count":  len(merged),
        "source": "core_agency_knowledge",
    }
    return payload, manifest


def build_client_context_bundle(
    client_id: str,
    *,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    platform: Optional[str] = None,
    meeting_limit: int = 3,
    change_limit: int = 15,
    outcome_limit: int = 10,
    knowledge_limit: int = 5,
    db=None,
    now: Optional[datetime] = None,
) -> dict:
    """
    Assemble the deterministic Context Bundle (PRD §8.1 / R05) for a client.

    Raises `LookupError` if `client_id` does not resolve to an existing client.
    """
    sb = db or get_supabase()
    identity = _resolve_client_identity(sb, client_id)
    if not identity:
        raise LookupError(f"client not found: {client_id!r}")

    ts = now or datetime.now(timezone.utc)
    ps, pe = _normalize_period(period_start, period_end, now=ts)
    slug = identity["client_id"]
    client_uuid = identity["id"]

    truth_payload, truth_manifest, raw_ct, raw_tt = _load_client_truth_and_budgets(sb, slug, pe)
    promotions, promo_manifest = _load_promotions(sb, client_uuid, slug, ps, pe, raw_ct, raw_tt)
    meetings, meetings_manifest = _load_meetings_section(slug, meeting_limit)
    changes, changes_manifest = _load_relevant_changes(sb, slug, ps, pe, platform, change_limit)
    outcomes, outcomes_manifest = _load_recent_outcomes(sb, client_uuid, outcome_limit)
    knowledge, knowledge_manifest = _load_approved_knowledge(slug, platform, knowledge_limit)

    return {
        "schema_version":     SCHEMA_VERSION,
        "client_id":          slug,
        "client_uuid":        client_uuid,
        "client_name":        identity["name"],
        "business_model":     identity.get("business_model") or truth_payload["profile"].get("business_model"),
        "period_start":       ps,
        "period_end":         pe,
        "platform_filter":    platform.lower() if platform else None,
        "generated_at":       ts.isoformat(),
        "client_truth":       truth_payload,
        "promotions":         promotions,
        "meetings":           meetings,
        "relevant_changes":   changes,
        "recent_outcomes":    outcomes,
        "approved_knowledge": knowledge,
        "manifest": {
            "schema_version": SCHEMA_VERSION,
            "generated_at":   ts.isoformat(),
            "sections": {
                "client_truth":       truth_manifest,
                "promotions":         promo_manifest,
                "meetings":           meetings_manifest,
                "relevant_changes":   changes_manifest,
                "recent_outcomes":    outcomes_manifest,
                "approved_knowledge": knowledge_manifest,
            },
        },
    }
