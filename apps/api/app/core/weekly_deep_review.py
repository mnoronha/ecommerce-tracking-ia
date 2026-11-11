"""
Weekly Deep Review — internal agency analysis engine.

Sections:
  A. Executive State — highlights, review_status, top metrics
  B. Channel Review — per-channel health with contract awareness
  C. Campaign Review — campaign/budget changes from change log
  D. Change Impact Review — digest integration + causality classification
  E. Tracking / Data Quality — source health details
  F. Diagnoses (max 5) — deterministic rule-based findings
  G. Recommended Actions (max 5) — PROPOSED, requires human approval
  H. Watchlist — items to monitor next week

CONSTRAINTS (must not change):
- NOT client-facing. Never delivered to clients.
- All actions are PROPOSED (requires_human_approval=True).
- Causality: LIKELY_RELATED / POSSIBLY_RELATED / NO_EVIDENCE / NEEDS_MORE_DATA.
  Never declare causality without evidence.
- NOT_CONTRACTED != MISSING. Channels with no ad account = NOT_CONTRACTED = healthy.
- Stored in agency_report_contracts with report_type="weekly_deep_review".
- Never modifies core_change_log or core_change_digests.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_JOB_TYPE    = "weekly_deep_review"
_SCHEMA_VER  = "weekly-deep-review-v1"
_ERROR_TRUNC = 2000

_MAX_DIAGNOSES = 5
_MAX_ACTIONS   = 5

_DEGRADED_STATES = frozenset({"ERROR", "ACCESS_MISSING", "PERMISSION_DENIED", "MISSING", "STALE"})

# WoW shift that counts as significant for causality assessment
_CAUSALITY_METRIC_THRESHOLD_PCT = 5.0


# ── DB ─────────────────────────────────────────────────────────────────────────

def _get_db():
    from ..database import get_supabase
    return get_supabase()


# ── Channel contract detection ─────────────────────────────────────────────────

def _load_channel_contracts(sb, client_id: str) -> dict[str, bool]:
    """
    Returns {channel_key: contracted} based on client config.
    NOT_CONTRACTED (no ad account) is NOT an error — it means that channel
    was never part of the client's scope.
    """
    try:
        res = (
            sb.table("clients")
            .select("meta_ad_account_id, google_ads_customer_id")
            .eq("client_id", client_id)
            .single()
            .execute()
        )
        row = res.data or {}
    except Exception:
        row = {}
    return {
        "meta":     bool(row.get("meta_ad_account_id")),
        "google":   bool(row.get("google_ads_customer_id")),
        "business": True,
        "ga4":      False,
    }


# ── Change log loaders ─────────────────────────────────────────────────────────

def _load_campaign_changes(sb, client_id: str, period_start: date, period_end: date) -> list[dict]:
    """Load CAMPAIGN and BUDGET change log entries for the period (Section C + D)."""
    try:
        res = (
            sb.table("core_change_log")
            .select(
                "id, channel, entity_type, entity_id, entity_name_at_time, "
                "before_state, after_state, occurred_at"
            )
            .eq("client_id", client_id)
            .in_("entity_type", ["CAMPAIGN", "BUDGET"])
            .gte("occurred_at", period_start.isoformat())
            .lt("occurred_at", (period_end + timedelta(days=1)).isoformat())
            .order("occurred_at", desc=False)
            .limit(200)
            .execute()
        )
        return res.data or []
    except Exception as exc:
        logger.warning("weekly_deep_review: campaign changes load failed %s: %s", client_id, exc)
        return []


# ── Digest loader ──────────────────────────────────────────────────────────────

def _load_digest(sb, client_id: str, platform: str, period_start: date, period_end: date) -> Optional[dict]:
    """Return cached digest or build on-demand. Never raises."""
    try:
        from .change_digest import get_digest, build_digest
        cached = get_digest(sb, client_id, platform, period_start.isoformat(), period_end.isoformat())
        if cached:
            return cached
        return build_digest(sb, client_id, platform, period_start.isoformat(), period_end.isoformat())
    except Exception as exc:
        logger.warning("weekly_deep_review: digest load failed %s/%s: %s", client_id, platform, exc)
        return None


# ── Causality assessment ───────────────────────────────────────────────────────

# Maps change_type → list of (metric_key, expected_direction)
# "any" means any significant shift in that metric counts, regardless of sign
_CAUSAL_PAIRS: dict[str, list[tuple[str, str]]] = {
    "CAMPAIGN_PAUSE":      [("meta_spend", "down"), ("google_spend", "down"),
                            ("meta_conversions", "down"), ("google_conversions", "down")],
    "CAMPAIGN_ACTIVATION": [("meta_spend", "up"), ("google_spend", "up"),
                            ("meta_conversions", "up"), ("google_conversions", "up")],
    "BUDGET_CHANGE":       [("meta_spend", "any"), ("google_spend", "any")],
    "AD_PAUSE":            [("meta_spend", "down"), ("meta_conversions", "down")],
    "AD_ACTIVATION":       [("meta_spend", "up"), ("meta_conversions", "up")],
    "ADSET_PAUSE":         [("meta_spend", "down")],
    "ADSET_ACTIVATION":    [("meta_spend", "up")],
}


def _direction(pct: Optional[float]) -> Optional[str]:
    if pct is None:
        return None
    return "up" if pct > 0 else "down"


def _assess_causality(change: dict, wow_changes: dict) -> str:
    """
    Assess causality between a digest change entry and WoW metric shifts.

    Returns one of:
      LIKELY_RELATED    — change_type matches expected metric direction at >5% shift
      POSSIBLY_RELATED  — high-impact change + any significant metric shift in period
      NO_EVIDENCE       — change occurred but no corroborating metric shift
      NEEDS_MORE_DATA   — cannot assess (no prior snapshot, source degraded)

    Rules are conservative by design — never invent causality.
    """
    if not wow_changes:
        return "NEEDS_MORE_DATA"

    change_type = change.get("change_type") or ""
    impact      = change.get("impact") or "low"

    expected_pairs = _CAUSAL_PAIRS.get(change_type, [])
    if expected_pairs:
        for metric_key, expected_dir in expected_pairs:
            wow = wow_changes.get(metric_key)
            if not wow:
                continue
            pct = wow.get("change_pct")
            if pct is None:
                continue
            if abs(pct) < _CAUSALITY_METRIC_THRESHOLD_PCT:
                continue
            actual_dir = _direction(pct)
            if expected_dir == "any" or actual_dir == expected_dir:
                return "LIKELY_RELATED"

    # POSSIBLY_RELATED only when there is NO expected direction for this change_type.
    # If we have expected_pairs but none matched, the signal is contradicted → NO_EVIDENCE.
    if impact == "high" and not expected_pairs:
        significant = any(
            abs(v.get("change_pct") or 0) >= _CAUSALITY_METRIC_THRESHOLD_PCT
            for v in wow_changes.values()
            if v.get("change_pct") is not None
        )
        if significant:
            return "POSSIBLY_RELATED"

    return "NO_EVIDENCE"


# ── Section A — Executive State ────────────────────────────────────────────────

def _build_section_a(base: dict) -> dict:
    review_status = base.get("review_status", "BLOCKED")
    wow           = base.get("wow_changes", {})

    top_moves = sorted(
        [
            {
                "metric":     k,
                "change_pct": v["change_pct"],
                "current":    v["current"],
                "prior":      v["prior"],
            }
            for k, v in wow.items()
            if v.get("change_pct") is not None
        ],
        key=lambda x: abs(x["change_pct"]),
        reverse=True,
    )[:3]

    alerts          = base.get("active_alerts", [])
    critical_alerts = [a for a in alerts if a.get("severity") == "critical"]

    return {
        "review_status":    review_status,
        "week_label":       base.get("period", {}).get("label", ""),
        "top_metric_moves": top_moves,
        "critical_alerts":  critical_alerts,
        "data_quality":     base.get("data_quality", {}).get("overall", "UNKNOWN"),
    }


# ── Section B — Channel Review ─────────────────────────────────────────────────

_CHANNEL_METRICS: dict[str, list[str]] = {
    "meta":     ["meta_spend", "meta_conversions", "meta_conversion_value", "roas_meta", "cpa_meta"],
    "google":   ["google_spend", "google_conversions", "google_conversion_value", "roas_google", "cpa_google"],
    "business": ["revenue_business", "orders_count", "mer"],
    "ga4":      ["sessions"],
}

_SOURCE_KEY: dict[str, str] = {
    "meta":     "meta_ads",
    "google":   "google_ads",
    "business": "business",
    "ga4":      "ga4",
}


def _build_section_b(base: dict, channel_contracts: dict) -> dict:
    health  = base.get("data_quality", {}).get("sources", {})
    current = base.get("metrics_current", {})
    wow     = base.get("wow_changes", {})

    def _ch(channel_key: str) -> dict:
        contracted  = channel_contracts.get(channel_key, False)
        source_key  = _SOURCE_KEY.get(channel_key, channel_key)
        source_health = health.get(source_key, "UNKNOWN")

        if not contracted:
            return {"contracted": False, "health": "NOT_CONTRACTED"}

        metrics: dict = {}
        for mk in _CHANNEL_METRICS.get(channel_key, []):
            m = current.get(mk)
            if m:
                entry: dict = {
                    "value":  m.get("value"),
                    "status": m.get("value_status"),
                    "unit":   m.get("unit"),
                }
                w = wow.get(mk)
                if w:
                    entry["wow_pct"] = w.get("change_pct")
                metrics[mk] = entry

        return {"contracted": True, "health": source_health, "metrics": metrics}

    return {
        "meta":     _ch("meta"),
        "google":   _ch("google"),
        "business": _ch("business"),
        "ga4":      _ch("ga4"),
    }


# ── Section C — Campaign Review ────────────────────────────────────────────────

def _build_section_c(campaign_changes: list[dict]) -> dict:
    """Group CAMPAIGN/BUDGET changes by entity_id for the period."""
    grouped: dict[str, dict] = {}

    for ch in campaign_changes:
        eid = ch.get("entity_id") or str(ch.get("id", ""))
        if eid not in grouped:
            grouped[eid] = {
                "entity_id":   eid,
                "entity_name": ch.get("entity_name_at_time"),
                "entity_type": ch.get("entity_type"),
                "channel":     ch.get("channel"),
                "changes":     [],
            }

        entry: dict = {"occurred_at": ch.get("occurred_at"), "entity_type": ch.get("entity_type")}
        before = ch.get("before_state") or {}
        after  = ch.get("after_state")  or {}

        if ch.get("entity_type") == "CAMPAIGN":
            # Google uses before_state.status; Meta uses before_state.value
            before_status = (
                before.get("status") or
                (before.get("value") if isinstance(before.get("value"), str) else None)
            )
            after_status = (
                after.get("status") or
                (after.get("value") if isinstance(after.get("value"), str) else None)
            )
            if before_status or after_status:
                entry["before_status"] = before_status
                entry["after_status"]  = after_status

        elif ch.get("entity_type") == "BUDGET":
            before_micros = before.get("amountMicros")
            after_micros  = after.get("amountMicros")
            try:
                if before_micros is not None:
                    entry["before_budget_brl"] = f"R$ {int(before_micros) / 1_000_000:,.2f}"
                if after_micros is not None:
                    entry["after_budget_brl"] = f"R$ {int(after_micros) / 1_000_000:,.2f}"
            except (ValueError, TypeError):
                pass

        grouped[eid]["changes"].append(entry)

    return {
        "campaigns":     list(grouped.values()),
        "total_changes": len(campaign_changes),
    }


# ── Section D — Change Impact Review ──────────────────────────────────────────

def _build_section_d(
    digest_meta:      Optional[dict],
    digest_google:    Optional[dict],
    wow_changes:      dict,
) -> dict:
    """Change impact review with causality classification for each HIGH/MEDIUM change."""
    impact_items: list[dict] = []

    for digest, platform in [(digest_meta, "meta"), (digest_google, "google")]:
        if not digest:
            continue
        for change in (digest.get("changes") or []):
            if change.get("impact") in ("high", "medium"):
                causality = _assess_causality(change, wow_changes)
                impact_items.append({
                    "platform":  platform,
                    "change":    change,
                    "causality": causality,
                })

    causality_summary: dict[str, int] = {
        "LIKELY_RELATED": 0, "POSSIBLY_RELATED": 0,
        "NO_EVIDENCE": 0,    "NEEDS_MORE_DATA": 0,
    }
    for item in impact_items:
        c = item.get("causality", "NO_EVIDENCE")
        causality_summary[c] = causality_summary.get(c, 0) + 1

    return {
        "digest_meta":       digest_meta,
        "digest_google":     digest_google,
        "impact_assessment": impact_items,
        "causality_summary": causality_summary,
    }


# ── Section E — Tracking / Data Quality ───────────────────────────────────────

def _build_section_e(base: dict, channel_contracts: dict) -> dict:
    health      = base.get("data_quality", {}).get("sources", {})
    limitations = base.get("data_quality", {}).get("limitations", [])

    issues: list[dict] = []
    for source, state in health.items():
        if state not in _DEGRADED_STATES:
            continue
        # Only flag as critical if the source is for a contracted channel
        is_contracted = True
        if source == "meta_ads":
            is_contracted = channel_contracts.get("meta", False)
        elif source == "google_ads":
            is_contracted = channel_contracts.get("google", False)
        elif source == "ga4":
            is_contracted = False

        issues.append({
            "source":   source,
            "state":    state,
            "severity": "critical" if is_contracted else "info",
        })

    return {
        "sources":     health,
        "issues":      issues,
        "limitations": limitations,
        "overall":     base.get("data_quality", {}).get("overall", "UNKNOWN"),
    }


# ── Section F — Diagnoses ──────────────────────────────────────────────────────

def _build_diagnoses(
    base:             dict,
    section_d:        dict,
    channel_contracts: dict,
) -> list[dict]:
    """
    Deterministic rule-based diagnoses. Sorted by business_impact (HIGH first),
    capped at _MAX_DIAGNOSES.
    """
    diagnoses: list[dict] = []
    wow     = base.get("wow_changes", {})
    health  = base.get("data_quality", {}).get("sources", {})
    current = base.get("metrics_current", {})

    # Rule: SPEND_DROP — total contracted spend down > 15% WoW
    spend_evidence: dict = {}
    spend_pcts: list[float] = []
    for ch, mk in [("meta", "meta_spend"), ("google", "google_spend")]:
        if not channel_contracts.get(ch):
            continue
        w = wow.get(mk)
        if w and w.get("change_pct") is not None:
            spend_evidence[mk] = w
            spend_pcts.append(w["change_pct"])

    if spend_pcts:
        avg_spend_pct = sum(spend_pcts) / len(spend_pcts)
        if avg_spend_pct < -15:
            diagnoses.append({
                "id": "SPEND_DROP",
                "code": "SPEND_DROP",
                "finding": f"Investimento caiu {abs(avg_spend_pct):.1f}% WoW",
                "evidence": spend_evidence,
                "confidence": "HIGH",
                "business_impact": "HIGH",
            })

    # Rule: ROAS_DECLINE — roas below -10% WoW for contracted channel
    for ch, roas_mk, label in [("meta", "roas_meta", "Meta"), ("google", "roas_google", "Google")]:
        if not channel_contracts.get(ch):
            continue
        w = wow.get(roas_mk)
        if w and w.get("change_pct") is not None and w["change_pct"] < -10:
            curr_val = w.get("current")
            diagnoses.append({
                "id": f"ROAS_DECLINE_{ch.upper()}",
                "code": "ROAS_DECLINE",
                "finding": (
                    f"ROAS {label} caiu {abs(w['change_pct']):.1f}% WoW"
                    + (f" (atual: {curr_val:.2f}x)" if curr_val is not None else "")
                ),
                "evidence": {roas_mk: w},
                "confidence": "HIGH",
                "business_impact": "HIGH",
            })

    # Rule: REVENUE_DROP — revenue_business down > 10% WoW
    rev_wow = wow.get("revenue_business")
    if rev_wow and rev_wow.get("change_pct") is not None and rev_wow["change_pct"] < -10:
        diagnoses.append({
            "id": "REVENUE_DROP",
            "code": "REVENUE_DROP",
            "finding": f"Receita caiu {abs(rev_wow['change_pct']):.1f}% WoW",
            "evidence": {"revenue_business": rev_wow},
            "confidence": "HIGH",
            "business_impact": "HIGH",
        })

    # Rule: DATA_QUALITY_DEGRADED — contracted source in degraded state
    degraded: list[str] = []
    for source, state in health.items():
        if state not in _DEGRADED_STATES:
            continue
        if source == "meta_ads" and not channel_contracts.get("meta"):
            continue
        if source == "google_ads" and not channel_contracts.get("google"):
            continue
        degraded.append(f"{source}: {state}")

    if degraded:
        diagnoses.append({
            "id": "DATA_QUALITY_DEGRADED",
            "code": "DATA_QUALITY_DEGRADED",
            "finding": f"{len(degraded)} fonte(s) degradada(s): {', '.join(degraded)}",
            "evidence": {"degraded_sources": degraded},
            "confidence": "HIGH",
            "business_impact": "HIGH",
        })

    # Rule: CAMPAIGN_PAUSED — high-impact pause detected in period
    pause_events = [
        i for i in (section_d.get("impact_assessment") or [])
        if i.get("change", {}).get("change_type") in ("CAMPAIGN_PAUSE", "AD_PAUSE", "ADSET_PAUSE")
    ]
    if pause_events:
        diagnoses.append({
            "id": "CAMPAIGN_PAUSED",
            "code": "CAMPAIGN_PAUSED",
            "finding": f"{len(pause_events)} pausa(s) detectada(s) no período",
            "evidence": {
                "paused_items": [
                    {"change_type": p["change"].get("change_type"), "platform": p["platform"]}
                    for p in pause_events[:3]
                ],
            },
            "confidence": "HIGH",
            "business_impact": "MEDIUM",
        })

    # Rule: TARGET_MISS — any metric with target_ratio < 0.8
    misses = [
        (k, v) for k, v in current.items()
        if isinstance(v.get("target_ratio"), (int, float))
        and v["target_ratio"] < 0.8
        and v.get("value") is not None
    ]
    if misses:
        worst_key, worst_val = min(misses, key=lambda x: x[1]["target_ratio"])
        diagnoses.append({
            "id": f"TARGET_MISS_{worst_key.upper()}",
            "code": "TARGET_MISS",
            "finding": (
                f"{worst_key} abaixo da meta "
                f"({worst_val['target_ratio']:.0%} do target)"
            ),
            "evidence": {
                "metric":       worst_key,
                "value":        worst_val.get("value"),
                "target":       worst_val.get("target"),
                "target_ratio": worst_val.get("target_ratio"),
            },
            "confidence": "HIGH",
            "business_impact": "MEDIUM",
        })

    _IMPACT_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    diagnoses.sort(key=lambda d: _IMPACT_ORDER.get(d.get("business_impact", "LOW"), 2))
    return diagnoses[:_MAX_DIAGNOSES]


# ── Section G — Recommended Actions ───────────────────────────────────────────

_ACTION_TEMPLATES: dict[str, dict] = {
    "SPEND_DROP": {
        "action":          "Investigar causa da queda de investimento (pausa, orçamento, aprovação)",
        "owner_role":      "AGENCY",
        "priority":        "HIGH",
        "expected_effect": "Identificar e corrigir bloqueio de entrega",
    },
    "ROAS_DECLINE": {
        "action":          "Revisar campanhas com ROAS abaixo do target",
        "owner_role":      "AGENCY",
        "priority":        "HIGH",
        "expected_effect": "Identificar criativos/segmentações com baixa eficiência",
    },
    "REVENUE_DROP": {
        "action":          "Verificar pipeline completo: atribuição, produto, operação",
        "owner_role":      "AGENCY",
        "priority":        "HIGH",
        "expected_effect": "Isolar se queda é de mídia ou operacional",
    },
    "DATA_QUALITY_DEGRADED": {
        "action":          "Restaurar conexão com fonte(s) degradada(s) e validar dados",
        "owner_role":      "AGENCY",
        "priority":        "HIGH",
        "expected_effect": "Restaurar visibilidade completa para análise da semana",
    },
    "CAMPAIGN_PAUSED": {
        "action":          "Confirmar se pausas foram intencionais ou incidentais",
        "owner_role":      "AGENCY",
        "priority":        "MEDIUM",
        "expected_effect": "Garantir que apenas campanhas corretas estão pausadas",
    },
    "TARGET_MISS": {
        "action":          "Revisar meta e estratégia de otimização",
        "owner_role":      "AGENCY",
        "priority":        "MEDIUM",
        "expected_effect": "Ajustar bid/budget/targeting para retornar à meta",
    },
}


def _build_actions(diagnoses: list[dict]) -> list[dict]:
    """
    Generate PROPOSED actions from diagnoses. All require human approval.
    Max _MAX_ACTIONS total.
    """
    actions: list[dict] = []
    seen_codes: set[str] = set()

    for diag in diagnoses:
        code = diag.get("code", "")
        if code in seen_codes:
            continue
        seen_codes.add(code)
        tmpl = _ACTION_TEMPLATES.get(code)
        if not tmpl:
            continue
        actions.append({
            "id":                    str(uuid.uuid4()),
            "code":                  code,
            "action":                tmpl["action"],
            "priority":              tmpl["priority"],
            "owner_role":            tmpl["owner_role"],
            "expected_effect":       tmpl["expected_effect"],
            "evidence":              {"diagnosis_id": diag.get("id"), "finding": diag.get("finding")},
            "status":                "PROPOSED",
            "requires_human_approval": True,
        })
        if len(actions) >= _MAX_ACTIONS:
            break

    return actions


# ── Section H — Watchlist ──────────────────────────────────────────────────────

def _build_watchlist(base: dict) -> list[dict]:
    watchlist: list[dict] = []
    wow     = base.get("wow_changes", {})
    alerts  = base.get("active_alerts", [])
    current = base.get("metrics_current", {})

    # Metrics declining 5–15% (warning zone, not yet at diagnosis threshold)
    for mk, w in wow.items():
        pct = w.get("change_pct")
        if pct is not None and -15 < pct <= -5:
            watchlist.append({
                "type":          "DECLINING_METRIC",
                "item":          mk,
                "reason":        f"Queda de {abs(pct):.1f}% esta semana — monitorar tendência",
                "current_value": w.get("current"),
                "change_pct":    pct,
            })

    # Metrics approaching target miss (0.80–0.95 of target)
    for mk, mv in current.items():
        tr = mv.get("target_ratio")
        if isinstance(tr, float) and 0.80 <= tr < 0.95:
            watchlist.append({
                "type":         "APPROACHING_TARGET_MISS",
                "item":         mk,
                "reason":       f"{mk} em {tr:.0%} da meta — risco de miss na próxima semana",
                "target_ratio": tr,
            })

    # Pending recommendations not yet executed
    open_recs = base.get("open_recommendations") or []
    if open_recs:
        watchlist.append({
            "type":   "PENDING_RECOMMENDATIONS",
            "item":   "recommendations",
            "reason": f"{len(open_recs)} recomendação(ões) pendente(s) de execução",
            "count":  len(open_recs),
        })

    # Active alerts
    if alerts:
        watchlist.append({
            "type":   "ACTIVE_ALERTS",
            "item":   "alerts",
            "reason": f"{len(alerts)} alerta(s) ativo(s)",
            "count":  len(alerts),
        })

    return watchlist


# ── Main contract builder ──────────────────────────────────────────────────────

def build_weekly_deep_review(
    client_id:    str,
    period_start: date,
    period_end:   date,
) -> dict:
    """
    Build the full Weekly Deep Review contract for one client.

    Pure data layer — no narrative generation, no Hermes call, no client delivery.
    Stored via run_weekly_deep_review_for_client() in agency_report_contracts.
    """
    sb = _get_db()

    # ── Base contract from weekly_review ──────────────────────────────────────
    from .weekly_review import build_weekly_review_contract
    base = build_weekly_review_contract(client_id, period_start, period_end)

    if base.get("review_status") == "BLOCKED" and base.get("error"):
        return {
            "schema_version": _SCHEMA_VER,
            "review_status":  "BLOCKED",
            "error":          base.get("error"),
            "period":         {"start": period_start.isoformat(), "end": period_end.isoformat()},
        }

    # ── Channel contracts ──────────────────────────────────────────────────────
    channel_contracts = _load_channel_contracts(sb, client_id)

    # ── Change data ────────────────────────────────────────────────────────────
    campaign_changes = _load_campaign_changes(sb, client_id, period_start, period_end)
    digest_meta   = _load_digest(sb, client_id, "meta",   period_start, period_end) if channel_contracts.get("meta")   else None
    digest_google = _load_digest(sb, client_id, "google", period_start, period_end) if channel_contracts.get("google") else None

    # ── Sections ───────────────────────────────────────────────────────────────
    wow = base.get("wow_changes", {})

    section_a = _build_section_a(base)
    section_b = _build_section_b(base, channel_contracts)
    section_c = _build_section_c(campaign_changes)
    section_d = _build_section_d(digest_meta, digest_google, wow)
    section_e = _build_section_e(base, channel_contracts)
    diagnoses = _build_diagnoses(base, section_d, channel_contracts)
    actions   = _build_actions(diagnoses)
    watchlist = _build_watchlist(base)

    return {
        "schema_version":  _SCHEMA_VER,
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "review_status":   base.get("review_status", "BLOCKED"),
        "client":          base.get("client", {}),
        "period":          base.get("period", {}),
        "comparison_period": base.get("comparison_period", {}),

        "section_a_executive_state":     section_a,
        "section_b_channel_review":      section_b,
        "section_c_campaign_review":     section_c,
        "section_d_change_impact":       section_d,
        "section_e_tracking_quality":    section_e,
        "section_f_diagnoses":           diagnoses,
        "section_g_recommended_actions": actions,
        "section_h_watchlist":           watchlist,

        # Carry forward base fields for compatibility with tooling that reads weekly contracts
        "data_quality":         base.get("data_quality"),
        "metrics_current":      base.get("metrics_current"),
        "metrics_prior":        base.get("metrics_prior"),
        "wow_changes":          wow,
        "target_truth":         base.get("target_truth"),
        "active_alerts":        base.get("active_alerts"),
        "executed_actions":     base.get("executed_actions"),
        "open_recommendations": base.get("open_recommendations"),
        "manual_changes":       base.get("manual_changes"),
        "source_snapshot_id":        base.get("source_snapshot_id"),
        "source_prior_snapshot_id":  base.get("source_prior_snapshot_id"),
    }


# ── Persistence ────────────────────────────────────────────────────────────────

def _write_contract(
    sb,
    client_id:    str,
    period_start: date,
    period_end:   date,
    contract:     dict,
    source_run_id: Optional[str] = None,
) -> Optional[str]:
    """
    Delete+insert idempotency on (client_slug="weekly_deep_review", period_end).
    Returns the inserted contract UUID or None on failure.
    """
    prior_start    = period_start - timedelta(days=7)
    prior_end      = period_end   - timedelta(days=7)
    business_model = contract.get("client", {}).get("business_model", "ecommerce")
    now            = datetime.now(timezone.utc).isoformat()

    try:
        sb.table("agency_report_contracts").delete() \
            .eq("client_slug", client_id) \
            .eq("report_type", "weekly_deep_review") \
            .eq("period_end",  period_end.isoformat()) \
            .execute()
    except Exception as exc:
        logger.warning("weekly_deep_review: contract delete failed %s: %s", client_id, exc)

    try:
        res = sb.table("agency_report_contracts").insert({
            "client_slug":             client_id,
            "report_type":             "weekly_deep_review",
            "business_model":          business_model,
            "period_start":            period_start.isoformat(),
            "period_end":              period_end.isoformat(),
            "comparison_period_start": prior_start.isoformat(),
            "comparison_period_end":   prior_end.isoformat(),
            "schema_version":          _SCHEMA_VER,
            "source_run_id":           source_run_id,
            "generated_at":            now,
            "contract":                contract,
            "created_at":              now,
            "updated_at":              now,
        }).execute()
        row = res.data[0] if res.data else None
        return str(row["id"]) if row else None
    except Exception as exc:
        logger.error("weekly_deep_review: contract insert failed %s: %s", client_id, exc)
        return None


# ── Recommendation materialisation ────────────────────────────────────────────

def _load_client_uuid(sb, client_id: str) -> Optional[str]:
    """Return the UUID (`id`) for a client slug, or None if not found."""
    try:
        res = (
            sb.table("clients")
            .select("id")
            .eq("client_id", client_id)
            .single()
            .execute()
        )
        row = res.data or {}
        return str(row["id"]) if row.get("id") else None
    except Exception:
        return None


def _materialize_dr_recommendations(
    sb,
    client_id:   str,
    client_uuid: Optional[str],
    period_end:  date,
    contract:    dict,
    contract_id: str,
) -> int:
    """
    Materialise weekly DR actions → core_diagnoses + core_recommendations.

    Idempotency key: dr:{client_id}:{period_end}:{code}
    Uses contract_id as synthetic alert_id on core_diagnoses (no FK enforcement).
    Never touches recommendations that are already APPROVED / REJECTED / DEFERRED.
    """
    diagnoses_json = contract.get("section_f_diagnoses") or []
    actions_json   = contract.get("section_g_recommended_actions") or []
    if not actions_json:
        return 0

    code_to_diag = {d.get("code"): d for d in diagnoses_json if d.get("code")}
    materialized = 0

    for action in actions_json:
        code = action.get("code")
        if not code:
            continue

        rec_fp  = f"dr:{client_id}:{period_end.isoformat()}:{code}"
        diag_fp = f"dr-diag:{client_id}:{period_end.isoformat()}:{code}"

        # Idempotency: skip if recommendation already exists (any status)
        try:
            dup = (
                sb.table("core_recommendations")
                .select("id, status")
                .eq("fingerprint", rec_fp)
                .limit(1)
                .execute()
            )
            if dup and dup.data:
                continue
        except Exception:
            pass

        # Ensure core_diagnoses row exists
        diagnosis_id: Optional[str] = None
        try:
            dup_diag = (
                sb.table("core_diagnoses")
                .select("id")
                .eq("fingerprint", diag_fp)
                .limit(1)
                .execute()
            )
            if dup_diag and dup_diag.data:
                diagnosis_id = str(dup_diag.data[0]["id"])
        except Exception:
            pass

        if not diagnosis_id:
            diag_data = code_to_diag.get(code, {})
            summary   = (
                diag_data.get("finding")
                or diag_data.get("summary")
                or action.get("action", "")
            )[:500]
            confidence = (
                "HIGH"
                if diag_data.get("severity") in ("critical", "high")
                else "MEDIUM"
            )
            try:
                diag_ins = sb.table("core_diagnoses").insert({
                    "alert_id":              contract_id,
                    "client_id":             client_uuid,
                    "status":                "OPEN",
                    "summary":               summary,
                    "root_cause_hypotheses": [],
                    "evidence":              {"contract_id": contract_id, "code": code},
                    "confidence":            confidence,
                    "limitations":           None,
                    "fingerprint":           diag_fp,
                    "created_by":            "weekly_deep_review",
                }).execute()
                diag_row = diag_ins.data[0] if (diag_ins and diag_ins.data) else None
                diagnosis_id = str(diag_row["id"]) if diag_row else None
            except Exception as exc:
                logger.warning(
                    "weekly_deep_review: diag materialisation failed %s %s: %s",
                    client_id, code, exc,
                )
                continue

        if not diagnosis_id:
            continue

        # Insert recommendation
        diag_data = code_to_diag.get(code, {})
        try:
            sb.table("core_recommendations").insert({
                "diagnosis_id":            diagnosis_id,
                "alert_id":                contract_id,
                "client_id":               client_uuid,
                "title":                   action.get("action", "")[:200],
                "action":                  action.get("action", ""),
                "rationale":               (diag_data.get("finding") or "")[:500],
                "priority":                action.get("priority", "MEDIUM"),
                "risk":                    action.get("risk", "LOW"),
                "expected_impact":         action.get("expected_effect"),
                "requires_human_approval": True,
                "status":                  "PROPOSED",
                "fingerprint":             rec_fp,
                "created_by":              "weekly_deep_review",
            }).execute()
            materialized += 1
        except Exception as exc:
            logger.warning(
                "weekly_deep_review: rec materialisation failed %s %s: %s",
                client_id, code, exc,
            )

    logger.info(
        "weekly_deep_review: materialised %d/%d recommendations for %s %s",
        materialized, len(actions_json), client_id, period_end,
    )
    return materialized


# ── Job tracking ───────────────────────────────────────────────────────────────

def _insert_running_job(sb, client_id: str, period_end: date) -> Optional[str]:
    run_key = f"{_JOB_TYPE}:{client_id}:{period_end.isoformat()}"
    job_id  = str(uuid.uuid4())
    try:
        res = sb.table("core_job_runs").insert({
            "id":         job_id,
            "job_type":   _JOB_TYPE,
            "run_key":    run_key,
            "status":     "RUNNING",
            "attempt":    1,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        return res.data[0]["id"] if res.data else job_id
    except Exception as exc:
        logger.warning("weekly_deep_review: job insert failed %s: %s", client_id, exc)
        return None


def _finish_job(sb, job_id: str, status: str, error: Optional[str] = None) -> None:
    try:
        payload: dict[str, Any] = {
            "status":      status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        if error:
            payload["error"] = error[:_ERROR_TRUNC]
        sb.table("core_job_runs").update(payload).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("weekly_deep_review: job finish failed %s: %s", job_id, exc)


# ── Public entrypoints ─────────────────────────────────────────────────────────

def run_weekly_deep_review_for_client(
    client_id:    str,
    period_start: date,
    period_end:   date,
    job_id:       Optional[str] = None,
) -> None:
    sb = _get_db()

    if job_id:
        try:
            upd = sb.table("core_job_runs").update({"status": "RUNNING"}).eq("id", job_id).execute()
            if not (upd and upd.data):
                job_id = _insert_running_job(sb, client_id, period_end) or job_id
        except Exception as exc:
            logger.warning("weekly_deep_review: replay → RUNNING failed %s: %s", job_id, exc)
    else:
        job_id = _insert_running_job(sb, client_id, period_end)

    run_id = f"{_JOB_TYPE}:{client_id}:{period_end.isoformat()}"
    try:
        contract    = build_weekly_deep_review(client_id, period_start, period_end)
        contract_id = _write_contract(sb, client_id, period_start, period_end, contract, source_run_id=run_id)
        # _write_contract returns the UUID from the INSERT response.
        # If the response body was empty (Supabase returning HTTP 201 without data),
        # fall back to a SELECT so materialisation is never silently skipped.
        if not contract_id:
            try:
                fb = (
                    sb.table("agency_report_contracts")
                    .select("id")
                    .eq("client_slug", client_id)
                    .eq("report_type", "weekly_deep_review")
                    .eq("period_end",  period_end.isoformat())
                    .order("generated_at", desc=True)
                    .limit(1)
                    .execute()
                )
                contract_id = str(fb.data[0]["id"]) if (fb and fb.data) else None
            except Exception as _fb_exc:
                logger.warning(
                    "weekly_deep_review: contract_id fallback query failed %s: %s",
                    client_id, _fb_exc,
                )
        if contract_id and contract.get("review_status") not in ("BLOCKED",):
            client_uuid = _load_client_uuid(sb, client_id)
            _materialize_dr_recommendations(
                sb, client_id, client_uuid, period_end, contract, contract_id
            )
        status = "SUCCEEDED"
        error  = None
        logger.info(
            "weekly_deep_review: %s %s → %s (review_status=%s)",
            client_id, period_end, status, contract.get("review_status"),
        )
    except Exception as exc:
        status = "FAILED"
        error  = str(exc)[:_ERROR_TRUNC]
        logger.error("weekly_deep_review: %s failed: %s", client_id, exc)

    if job_id:
        _finish_job(sb, job_id, status, error)


def run_weekly_deep_review_all_clients() -> None:
    """Scheduler entry point — Monday 08:15 UTC (after weekly_review at 08:00)."""
    from .weekly_review import _last_completed_week
    period_start, period_end = _last_completed_week()
    sb = _get_db()
    try:
        res = (
            sb.table("clients")
            .select("client_id")
            .eq("is_active", True)
            .execute()
        )
        client_ids = [c["client_id"] for c in (res.data or []) if c.get("client_id")]
    except Exception as exc:
        logger.error("weekly_deep_review: client list failed: %s", exc)
        return

    logger.info(
        "weekly_deep_review: running for %d clients — period %s→%s",
        len(client_ids), period_start, period_end,
    )
    for cid in client_ids:
        try:
            run_weekly_deep_review_for_client(cid, period_start, period_end)
        except Exception as exc:
            logger.error("weekly_deep_review: unexpected error for %s: %s", cid, exc)


def queue_weekly_deep_review_replay(
    sb,
    client_id:  str,
    period_end: date,
    replay_of:  Optional[str],
) -> str:
    """Queue a QUEUED replay job. Returns new job_id."""
    job_id  = str(uuid.uuid4())
    run_key = f"{_JOB_TYPE}:{client_id}:{period_end.isoformat()}:replay:{job_id[:8]}"
    payload: dict[str, Any] = {
        "id":         job_id,
        "job_type":   _JOB_TYPE,
        "run_key":    run_key,
        "status":     "QUEUED",
        "attempt":    1,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if replay_of is not None:
        payload["replay_of"] = replay_of
    res = sb.table("core_job_runs").insert(payload).execute()
    persisted = res.data[0]["id"] if (res and res.data) else job_id
    logger.info(
        "weekly_deep_review: queued replay %s for %s (replay_of=%s)", persisted, client_id, replay_of
    )
    return persisted


def execute_weekly_deep_review_replay(
    job_id:     str,
    client_id:  str,
    period_end: date,
) -> None:
    """Background task body — transitions QUEUED → RUNNING → SUCCEEDED|FAILED."""
    period_start = period_end - timedelta(days=6)
    run_weekly_deep_review_for_client(client_id, period_start, period_end, job_id=job_id)
