"""
Change Digest — derived layer over core_change_log.

Reads raw platform change events, classifies relevant changes, groups batches,
and produces a structured digest per (client_id, platform, period_start, period_end).

Design rules:
- Read-only from core_change_log. Never writes to change_log.
- Zero client sends. Zero media writes.
- All change_type in source is 'OTHER'; classification is by entity_type + state transitions.
- Batch grouping: multiple AD_ACTIVATION events in same campaign within 30 min → 1 entry.
- Core is canonical; this is a cache layer. Re-generate any time.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DIGEST_ENGINE_VERSION = "1"

# ── Status sets ───────────────────────────────────────────────────────────────

_ACTIVE = {"ativo", "ativa", "ativo em aprendizado", "active", "exibição iniciada"}
_INACTIVE = {"pausado", "inativo", "desativado", "paused", "inactive", "disabled"}
# Internal Meta review queue states — transitions between these are noise
_QUEUE_STATES = {"processo pendente", "em processamento", "análise pendente", "em análise", "em revisão"}

# ── Helpers ───────────────────────────────────────────────────────────────────


def _str_val(state: Optional[dict]) -> Optional[str]:
    """Extract state.value when it is a plain string."""
    if not state:
        return None
    v = state.get("value")
    return str(v).strip() if isinstance(v, str) else None


def _dict_val(state: Optional[dict]) -> Optional[dict]:
    """Extract state.value when it is a dict."""
    if not state:
        return None
    v = state.get("value")
    return v if isinstance(v, dict) else None


def _list_val(state: Optional[dict]) -> Optional[list]:
    """Extract state.value when it is a list."""
    if not state:
        return None
    v = state.get("value")
    return v if isinstance(v, list) else None


def _is_to_active(val: Optional[str]) -> bool:
    return bool(val and val.lower() in _ACTIVE)


def _is_to_inactive(val: Optional[str]) -> bool:
    return bool(val and val.lower() in _INACTIVE)


def _is_noise_transition(before_str: Optional[str], after_str: Optional[str]) -> bool:
    """True only when both states are internal Meta review-queue states (no real signal)."""
    before_q = bool(before_str and before_str.lower() in _QUEUE_STATES)
    after_q = bool(after_str and after_str.lower() in _QUEUE_STATES)
    return before_q and after_q


def _fmt_brl(centavos: int) -> str:
    reais = centavos / 100
    return f"R$ {reais:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ── Classifier ────────────────────────────────────────────────────────────────


def _classify(row: dict) -> Optional[tuple[str, str, dict]]:
    """
    Return (change_type, impact, extra) or None if noise.
    impact: 'high' | 'medium' | 'low'
    """
    entity = row.get("entity_type", "")
    before = row.get("before_state")
    after = row.get("after_state")
    before_str = _str_val(before)
    after_str = _str_val(after)
    before_dict = _dict_val(before)
    after_dict = _dict_val(after)
    before_list = _list_val(before)
    after_list = _list_val(after)

    # ── CAMPAIGN_GROUP ────────────────────────────────────────────────────────
    if entity == "CAMPAIGN_GROUP":
        # Budget change: after_state.value is dict with type=payment_amount
        if after_dict and after_dict.get("type") == "payment_amount":
            old_v = (before_dict or {}).get("old_value")
            new_v = after_dict.get("new_value")
            extra: dict = {"currency": after_dict.get("currency", "BRL")}
            if old_v is not None:
                extra["before_brl_centavos"] = old_v
                extra["before_display"] = _fmt_brl(old_v)
            if new_v is not None:
                extra["after_brl_centavos"] = new_v
                extra["after_display"] = _fmt_brl(new_v)
            return ("BUDGET_CHANGE", "high", extra)
        if _is_noise_transition(before_str, after_str):
            return None
        if after_str and _is_to_active(after_str):
            return ("CAMPAIGN_GROUP_ACTIVATION", "high", {})
        if after_str and _is_to_inactive(after_str):
            return ("CAMPAIGN_GROUP_PAUSE", "high", {})

    # ── CAMPAIGN ──────────────────────────────────────────────────────────────
    elif entity == "CAMPAIGN":
        # New entity (before=null, after has name/type)
        if before is None and after_dict and "name" in after_dict:
            return ("NEW_ENTITY_CREATED", "medium", {"name": after_dict.get("name", "")})
        # Schedule change (before or after has time_stop/time_start)
        if after_dict and ("time_stop" in after_dict or "time_start" in after_dict):
            return ("SCHEDULE_CHANGE", "medium", {
                "before": before_dict or {},
                "after": after_dict or {},
            })
        # Audience change: before and after are complex lists (Público personalizado etc.)
        if after_list is not None or before_list is not None:
            return ("AUDIENCE_CHANGE", "medium", {"audience_updated": True})
        if _is_noise_transition(before_str, after_str):
            return None
        if after_str and _is_to_active(after_str):
            return ("CAMPAIGN_ACTIVATION", "high", {})
        if after_str and _is_to_inactive(after_str):
            return ("CAMPAIGN_PAUSE", "high", {})

    # ── ADGROUP (individual ads) ───────────────────────────────────────────────
    elif entity == "ADGROUP":
        if _is_noise_transition(before_str, after_str):
            return None
        # Activation after review
        if after_str and _is_to_active(after_str):
            return ("AD_ACTIVATION", "low", {})
        if after_str and _is_to_inactive(after_str):
            return ("AD_PAUSE", "high", {})

    # ── Google balance recharge ────────────────────────────────────────────────
    elif entity == "GOOGLE_BALANCE_RECHARGE":
        return ("BALANCE_RECHARGE", "medium", {
            "before": before,
            "after": after,
        })

    return None


# ── Grouping ──────────────────────────────────────────────────────────────────

_GROUP_WINDOW_MINUTES = 30


def _group_changes(classified: list[tuple[dict, tuple]]) -> list[dict]:
    """
    Build structured change entries, collapsing batch events.
    Multiple AD_ACTIVATION for same campaign_id within 30 min → single entry.
    """
    results: list[dict] = []
    # Track open activation batches: campaign_id → {entry, last_occurred_at}
    activation_batches: dict[str, dict] = {}

    for row, (change_type, impact, extra) in classified:
        occurred = row.get("occurred_at") or row.get("created_at") or ""
        campaign_id = row.get("campaign_id") or ""
        entity_name = row.get("entity_name_at_time") or row.get("entity_id") or ""
        source = row.get("source") or "PLATFORM_DETECTED"

        base = {
            "change_type": change_type,
            "impact": impact,
            "entity_type": row.get("entity_type"),
            "entity": entity_name,
            "campaign_id": campaign_id,
            "changed_at": occurred,
            "source": source,
            "actor": row.get("reported_by") or row.get("requested_by") or None,
            "reason": row.get("reason") or row.get("description") or None,
            "confidence": row.get("confidence") or "CONFIRMED",
            "linked_action_id": str(row["linked_action_id"]) if row.get("linked_action_id") else None,
            "linked_recommendation_id": str(row["recommendation_id"]) if row.get("recommendation_id") else None,
            **extra,
        }

        # Batch AD_ACTIVATION events
        if change_type == "AD_ACTIVATION" and campaign_id:
            batch_key = campaign_id
            existing = activation_batches.get(batch_key)
            if existing:
                try:
                    existing_dt = datetime.fromisoformat(existing["changed_at"].replace("Z", "+00:00"))
                    current_dt = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
                    diff_minutes = abs((current_dt - existing_dt).total_seconds()) / 60
                    if diff_minutes <= _GROUP_WINDOW_MINUTES:
                        existing["count"] = existing.get("count", 1) + 1
                        existing["entity"] = f"{existing['count']} anúncios"
                        existing["changed_at"] = occurred  # update to latest
                        continue
                except Exception:
                    pass
            base["count"] = 1
            activation_batches[batch_key] = base
            results.append(base)
            continue

        results.append(base)

    return results


# ── Summary builder ───────────────────────────────────────────────────────────


def _build_summary(changes: list[dict]) -> dict:
    high = [c for c in changes if c.get("impact") == "high"]
    medium = [c for c in changes if c.get("impact") == "medium"]
    low = [c for c in changes if c.get("impact") == "low"]
    unknown_reason = [c for c in changes if not c.get("reason") and not c.get("linked_action_id")]

    main_changes: list[str] = []
    for c in sorted(changes, key=lambda x: (x.get("impact") != "high", x.get("changed_at", ""))):
        ct = c.get("change_type", "")
        entity = c.get("entity") or c.get("campaign_id") or ""
        if ct == "BUDGET_CHANGE":
            before_d = c.get("before_display", "?")
            after_d = c.get("after_display", "?")
            main_changes.append(f"Budget de '{entity}': {before_d} → {after_d}")
        elif ct == "CAMPAIGN_ACTIVATION":
            main_changes.append(f"Campanha ativada: {entity}")
        elif ct == "CAMPAIGN_PAUSE":
            main_changes.append(f"Campanha pausada: {entity}")
        elif ct == "CAMPAIGN_GROUP_ACTIVATION":
            main_changes.append(f"Grupo de campanha ativado: {entity}")
        elif ct == "CAMPAIGN_GROUP_PAUSE":
            main_changes.append(f"Grupo de campanha pausado: {entity}")
        elif ct == "AD_ACTIVATION":
            count = c.get("count", 1)
            main_changes.append(f"{count} anúncio(s) ativado(s) na campanha {entity or c.get('campaign_id', '')}")
        elif ct == "AD_PAUSE":
            main_changes.append(f"Anúncio pausado: {entity}")
        elif ct == "SCHEDULE_CHANGE":
            main_changes.append(f"Calendário alterado: {entity}")
        elif ct == "AUDIENCE_CHANGE":
            main_changes.append(f"Audiência atualizada: {entity}")
        elif ct == "NEW_ENTITY_CREATED":
            main_changes.append(f"Entidade criada: {c.get('name') or entity}")
        elif ct == "BALANCE_RECHARGE":
            main_changes.append("Recarga de saldo Google Ads")
        if len(main_changes) >= 10:
            break

    return {
        "main_changes": main_changes,
        "high_impact_count": len(high),
        "medium_impact_count": len(medium),
        "low_impact_count": len(low),
        "unknown_reason_count": len(unknown_reason),
        "budget_changes": [
            {
                "entity": c.get("entity"),
                "before_display": c.get("before_display"),
                "after_display": c.get("after_display"),
                "currency": c.get("currency", "BRL"),
            }
            for c in changes if c.get("change_type") == "BUDGET_CHANGE"
        ],
        "campaign_activations": [c.get("entity") for c in changes if c.get("change_type") == "CAMPAIGN_ACTIVATION"],
        "campaign_pauses": [c.get("entity") for c in changes if c.get("change_type") == "CAMPAIGN_PAUSE"],
    }


def _detect_limitations(rows: list[dict], platform: str, client_id: str) -> list[str]:
    lims: list[str] = []
    if not rows:
        lims.append(f"{platform}: nenhum evento no período")
        return lims
    # All OTHER type warning
    non_other = [r for r in rows if r.get("change_type") != "OTHER"]
    if not non_other:
        lims.append(
            f"{platform}: 100% das entradas têm change_type=OTHER; "
            "classificação por heurística de transição de estado"
        )
    # Check for budget-relevant changes in Google (only Meta has payment_amount today)
    if platform == "google":
        lims.append("google: dados limitados a GOOGLE_BALANCE_RECHARGE no momento; budget/bid changes não disponíveis via change_history")
    return lims


# ── Fetch ─────────────────────────────────────────────────────────────────────


def _fetch_raw(db, client_id: str, platform: str, period_start: str, period_end: str) -> list[dict]:
    """Read core_change_log for the period. Uses .range() to avoid PostgREST cap."""
    all_rows: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        res = (
            db.table("core_change_log")
            .select("*")
            .eq("client_id", client_id)
            .eq("channel", platform)
            .gte("occurred_at", period_start)
            .lte("occurred_at", period_end + "T23:59:59+00:00")
            .order("occurred_at", desc=False)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = res.data or []
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return all_rows


# ── Public API ────────────────────────────────────────────────────────────────


def build_digest(db, client_id: str, platform: str, period_start: str, period_end: str) -> dict:
    """
    Build and return a change digest dict (not persisted).
    Call save_digest() to upsert into core_change_digests.
    """
    rows = _fetch_raw(db, client_id, platform, period_start, period_end)
    total_raw = len(rows)

    classified: list[tuple[dict, tuple]] = []
    noise_count = 0
    for row in rows:
        result = _classify(row)
        if result is None:
            noise_count += 1
        else:
            classified.append((row, result))

    grouped = _group_changes(classified)
    summary = _build_summary(grouped)
    limitations = _detect_limitations(rows, platform, client_id)

    return {
        "client_id": client_id,
        "platform": platform,
        "period_start": period_start,
        "period_end": period_end,
        "engine_version": DIGEST_ENGINE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_raw_count": total_raw,
        "relevant_count": len(grouped),
        "noise_count": noise_count,
        "changes": grouped,
        "summary": summary,
        "limitations": limitations,
    }


def save_digest(db, digest: dict) -> dict:
    """
    Upsert digest into core_change_digests.
    Same (client_id, platform, period_start, period_end) → overwrites.
    Returns the upserted row.
    """
    import json

    payload = {
        "client_id": digest["client_id"],
        "platform": digest["platform"],
        "period_start": digest["period_start"],
        "period_end": digest["period_end"],
        "engine_version": digest["engine_version"],
        "generated_at": digest["generated_at"],
        "total_raw_count": digest["total_raw_count"],
        "relevant_count": digest["relevant_count"],
        "noise_count": digest["noise_count"],
        "changes": digest["changes"],
        "summary": digest["summary"],
        "limitations": digest["limitations"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    res = (
        db.table("core_change_digests")
        .upsert(payload, on_conflict="client_id,platform,period_start,period_end")
        .execute()
    )
    return (res.data or [{}])[0]


def get_digest(db, client_id: str, platform: str, period_start: str, period_end: str) -> Optional[dict]:
    """Return stored digest or None."""
    res = (
        db.table("core_change_digests")
        .select("*")
        .eq("client_id", client_id)
        .eq("platform", platform)
        .eq("period_start", period_start)
        .eq("period_end", period_end)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def list_digests(db, client_id: str, limit: int = 20) -> list[dict]:
    """Return stored digests for a client, most recent first."""
    res = (
        db.table("core_change_digests")
        .select("client_id,platform,period_start,period_end,engine_version,generated_at,total_raw_count,relevant_count,noise_count,summary,limitations")
        .eq("client_id", client_id)
        .order("period_start", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


def build_report_change_section(db, client_id: str, platform: str, period_start: str, period_end: str) -> dict:
    """
    Returns the 'Principais otimizações realizadas no período' section for Reports.
    Uses stored digest if available; falls back to building one on-demand (not saved).
    """
    stored = get_digest(db, client_id, platform, period_start, period_end)
    digest = stored if stored else build_digest(db, client_id, platform, period_start, period_end)
    summary = digest.get("summary") or {}
    return {
        "platform": platform,
        "period_start": period_start,
        "period_end": period_end,
        "main_changes": summary.get("main_changes", []),
        "high_impact_count": summary.get("high_impact_count", 0),
        "medium_impact_count": summary.get("medium_impact_count", 0),
        "low_impact_count": summary.get("low_impact_count", 0),
        "budget_changes": summary.get("budget_changes", []),
        "campaign_activations": summary.get("campaign_activations", []),
        "campaign_pauses": summary.get("campaign_pauses", []),
        "unknown_reason_count": summary.get("unknown_reason_count", 0),
        "total_changes_in_log": digest.get("total_raw_count", 0),
        "relevant_changes": digest.get("relevant_count", 0),
        "limitations": digest.get("limitations", []),
        "digest_generated_at": digest.get("generated_at"),
    }
