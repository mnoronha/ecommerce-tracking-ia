"""
Daily tracking health monitor — verifica a saúde do sistema para todos os clientes.

Checks (cada dia, sobre o dia anterior):
  1. Snippet — volume de eventos vs. média 7d (queda > 50% → crítico, > 30% → aviso)
  2. Identificadores — cobertura de fbp (< 90% → aviso, < 70% → crítico)
  3. Dispatch Meta CAPI — pedidos online pagos sem capi_sent → erro
  4. Dispatch Google — pedidos online pagos sem google_sent → erro
  5. Filtro offline — verifica que POS/in_store não foram enviados
  6. Enriquecimento EMQ — % de pedidos com browser_ip + browser_ua

Produz:
  • Email HTML consolidado para AGENCY_NOTIFY_EMAIL
  • Alertas no banco (tabela `alerts`) para qualquer problema encontrado

Cron: diário às 12:30 UTC (09:30 BRT) via APScheduler.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..config import settings
from ..database import get_supabase
from . import resend as email_service
from . import notify as _notify

logger = logging.getLogger(__name__)

# ── Tunables ─────────────────────────────────────────────────────────────────
_SNIPPET_DROP_CRITICAL = 50   # % queda vs média 7d → crítico
_SNIPPET_DROP_WARN     = 30   # % queda → aviso
_FBP_COVER_CRITICAL    = 70   # fbp < 70% → crítico
_FBP_COVER_WARN        = 90   # fbp < 90% → aviso
_MIN_EVENTS_BASELINE   = 100  # só emite alerta de queda se a baseline for alta o suficiente

_OFFLINE_SOURCES = {"pos", "in_store", "offline"}
_EVENT_TYPES     = ["pageview", "view_product", "add_to_cart", "begin_checkout"]


# ── helpers ──────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_brl(n: float) -> str:
    return f"R$ {n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(num: int, den: int) -> float:
    return round(num / den * 100, 1) if den > 0 else 0.0


def _status_badge(ok: bool) -> str:
    return (
        '<span style="color:#16a34a;font-weight:700">✓ OK</span>'
        if ok
        else '<span style="color:#dc2626;font-weight:700">✗ FALHA</span>'
    )


def _severity_color(sev: str) -> str:
    return {"critical": "#dc2626", "warning": "#f59e0b", "info": "#6366f1"}.get(sev, "#6b7280")


# ── data collection (PostgREST — consistent with reports.py) ─────────────────

def _collect_snippet_health(sb, client_id: str, day_start: str, day_end: str) -> dict:
    # Count per event_type (4 queries, each cheap with index on client_id+created_at)
    counts: dict[str, int] = {}
    for et in _EVENT_TYPES:
        r = (
            sb.table("tracking_events")
            .select("id", count="exact", head=True)
            .eq("client_id", client_id)
            .eq("event_type", et)
            .gte("created_at", day_start)
            .lt("created_at", day_end)
            .execute()
        )
        counts[et] = r.count or 0

    total = sum(counts.values())

    # 7-day baseline (full days before yesterday)
    window_start = (datetime.fromisoformat(day_start) - timedelta(days=7)).isoformat()
    b = (
        sb.table("tracking_events")
        .select("id", count="exact", head=True)
        .eq("client_id", client_id)
        .gte("created_at", window_start)
        .lt("created_at", day_start)
        .execute()
    )
    baseline_total = b.count or 0
    daily_avg = round(baseline_total / 7, 0)

    drop_pct: Optional[float] = None
    if daily_avg >= _MIN_EVENTS_BASELINE:
        drop_pct = round((daily_avg - total) / daily_avg * 100, 1)

    return {
        "total":     total,
        "counts":    counts,
        "pageviews": counts.get("pageview", 0),
        "daily_avg": daily_avg,
        "drop_pct":  drop_pct,
    }


def _collect_visitor_coverage(sb, client_id: str, day_start: str, day_end: str) -> dict:
    # Fetch visitors active yesterday — select only the id + identifier columns
    r = (
        sb.table("visitors")
        .select("id, fbp, fbc, gclid, ga_client_id")
        .eq("client_id", client_id)
        .gte("last_seen_at", day_start)
        .lt("last_seen_at", day_end)
        .execute()
    )
    rows  = r.data or []
    total = len(rows)
    return {
        "total":     total,
        "fbp":       sum(1 for v in rows if v.get("fbp")),
        "fbc":       sum(1 for v in rows if v.get("fbc")),
        "gclid":     sum(1 for v in rows if v.get("gclid")),
        "ga":        sum(1 for v in rows if v.get("ga_client_id")),
        "pct_fbp":   _pct(sum(1 for v in rows if v.get("fbp")), total),
        "pct_gclid": _pct(sum(1 for v in rows if v.get("gclid")), total),
    }


def _collect_orders_health(sb, client_id: str, day_start: str, day_end: str) -> dict:
    r = (
        sb.table("orders")
        .select(
            "platform_order_number, financial_status, utm_source, utm_medium, "
            "total_price, capi_sent, capi_last_error, google_sent, google_last_error, "
            "google_match_type, tiktok_sent, tiktok_last_error, browser_ip, browser_ua"
        )
        .eq("client_id", client_id)
        .gte("created_at", day_start)
        .lt("created_at", day_end)
        .not_.is_("platform_order_number", "null")
        .neq("platform_order_number", "")
        .execute()
    )
    rows = r.data or []

    paid_pos_count = 0
    voided_count   = 0
    paid_online:    list[dict] = []
    capi_errors:    list[dict] = []
    google_errors:  list[dict] = []
    meta_dispatched   = 0
    google_dispatched = 0
    enriched          = 0
    pos_correctly_skipped = 0
    gm_counts: dict[str, int] = {}

    for o in rows:
        fs     = (o.get("financial_status") or "").lower()
        src    = (o.get("utm_source")       or "").lower()
        medium = (o.get("utm_medium")       or "").lower()
        price  = float(o.get("total_price") or 0)
        num    = o.get("platform_order_number", "?")

        is_offline = src in _OFFLINE_SOURCES or medium in _OFFLINE_SOURCES
        is_paid    = fs == "paid"
        is_voided  = fs in ("voided", "refunded")

        if is_voided:
            voided_count += 1
        elif is_paid and is_offline:
            paid_pos_count += 1
            err = (o.get("capi_last_error") or "").lower()
            if "offline" in err or ("skipped" in err and not o.get("capi_sent")):
                pos_correctly_skipped += 1
        elif is_paid and price > 0:
            paid_online.append(o)
            gmt = o.get("google_match_type")
            if gmt:
                gm_counts[gmt] = gm_counts.get(gmt, 0) + 1

            if o.get("capi_sent"):
                meta_dispatched += 1
            else:
                err = o.get("capi_last_error") or ""
                if "skipped" not in err.lower():
                    capi_errors.append({"order": num, "error": err or "not sent"})

            if o.get("google_sent"):
                google_dispatched += 1
            else:
                capi_err = (o.get("capi_last_error") or "").lower()
                if "skipped" in capi_err:
                    pass  # already counted as offline
                else:
                    g_err = o.get("google_last_error") or ""
                    google_errors.append({"order": num, "error": g_err or "not sent"})

            if o.get("browser_ip") and o.get("browser_ua"):
                enriched += 1

    n_online = len(paid_online)
    return {
        "paid_online":           n_online,
        "paid_pos":              paid_pos_count,
        "voided":                voided_count,
        "meta_dispatched":       meta_dispatched,
        "google_dispatched":     google_dispatched,
        "meta_missing":          n_online - meta_dispatched,
        "google_missing":        n_online - google_dispatched,
        "pos_correctly_skipped": pos_correctly_skipped,
        "capi_errors":           capi_errors,
        "google_errors":         google_errors,
        "enriched":              enriched,
        "pct_enriched":          _pct(enriched, n_online),
        "gm_counts":             gm_counts,
        "orders":                rows,
    }


# ── evaluation ───────────────────────────────────────────────────────────────

def _evaluate_client(client: dict, day_start: str, day_end: str) -> dict:
    sb        = get_supabase()
    client_id = client["id"]
    pixel_id  = client.get("pixel_id") or client_id

    tracking_enabled = client.get("tracking_enabled", True)

    if tracking_enabled is False:
        snippet  = {"total": 0, "counts": {}, "pageviews": 0, "daily_avg": 0, "drop_pct": None}
        visitors = {"total": 0, "fbp": 0, "fbc": 0, "gclid": 0, "ga": 0, "pct_fbp": 0, "pct_gclid": 0}
    else:
        try:
            snippet = _collect_snippet_health(sb, client_id, day_start, day_end)
        except Exception as exc:
            logger.warning("health_monitor: snippet query failed for %s: %s", pixel_id, exc)
            snippet = {"total": 0, "counts": {}, "pageviews": 0, "daily_avg": 0, "drop_pct": None}

        try:
            visitors = _collect_visitor_coverage(sb, client_id, day_start, day_end)
        except Exception as exc:
            logger.warning("health_monitor: visitor query failed for %s: %s", pixel_id, exc)
            visitors = {"total": 0, "fbp": 0, "fbc": 0, "gclid": 0, "ga": 0, "pct_fbp": 0, "pct_gclid": 0}

    try:
        orders = _collect_orders_health(sb, client_id, day_start, day_end)
    except Exception as exc:
        logger.warning("health_monitor: orders query failed for %s: %s", pixel_id, exc)
        orders = {
            "paid_online": 0, "paid_pos": 0, "voided": 0,
            "meta_dispatched": 0, "google_dispatched": 0,
            "meta_missing": 0, "google_missing": 0,
            "pos_correctly_skipped": 0, "capi_errors": [], "google_errors": [],
            "enriched": 0, "pct_enriched": 0, "gm_counts": {}, "orders": [],
        }

    # ── build findings ───────────────────────────────────────────────────
    findings: list[dict] = []

    # 1) Snippet volume — skip entirely for clients using native Shopify tracking
    drop = snippet.get("drop_pct")
    if tracking_enabled is False:
        pass  # no pixel deployed — snippet metrics not applicable
    elif snippet["total"] < 50 and snippet["daily_avg"] >= _MIN_EVENTS_BASELINE:
        findings.append({
            "severity": "critical", "check": "snippet_volume",
            "message": f"Apenas {snippet['total']} eventos ontem (média 7d: {snippet['daily_avg']:.0f}). Snippet pode estar fora.",
        })
    elif drop is not None and drop >= _SNIPPET_DROP_CRITICAL:
        findings.append({
            "severity": "critical", "check": "snippet_volume",
            "message": f"Eventos caíram {drop:.0f}% vs. média 7d ({snippet['total']:,} vs. {snippet['daily_avg']:.0f}/dia).",
        })
    elif drop is not None and drop >= _SNIPPET_DROP_WARN:
        findings.append({
            "severity": "warning", "check": "snippet_volume",
            "message": f"Eventos caíram {drop:.0f}% vs. média 7d ({snippet['total']:,} vs. {snippet['daily_avg']:.0f}/dia).",
        })

    # 2) fbp coverage
    pct_fbp = visitors.get("pct_fbp", 0)
    if visitors["total"] > 50:
        if pct_fbp < _FBP_COVER_CRITICAL:
            findings.append({
                "severity": "critical", "check": "fbp_coverage",
                "message": f"fbp apenas {pct_fbp:.1f}% (crítico < 70%). First-party cookie pode estar quebrado.",
            })
        elif pct_fbp < _FBP_COVER_WARN:
            findings.append({
                "severity": "warning", "check": "fbp_coverage",
                "message": f"fbp em {pct_fbp:.1f}% (ideal ≥ 90%). Verificar snippet e domínio CNAME.",
            })

    # 3) Meta CAPI dispatch
    if orders["meta_missing"] > 0:
        nums = [e["order"] for e in orders["capi_errors"]]
        findings.append({
            "severity": "critical", "check": "meta_dispatch",
            "message": f"{orders['meta_missing']} pedido(s) online NÃO enviado(s) ao Meta CAPI: {nums}",
        })

    # 4) Google dispatch
    if orders["google_missing"] > 0:
        nums = [e["order"] for e in orders["google_errors"]]
        findings.append({
            "severity": "critical", "check": "google_dispatch",
            "message": f"{orders['google_missing']} pedido(s) online NÃO enviado(s) ao Google: {nums}",
        })

    # 5) POS filter sanity
    if orders["paid_pos"] > 0 and orders["pos_correctly_skipped"] < orders["paid_pos"]:
        leaked = orders["paid_pos"] - orders["pos_correctly_skipped"]
        findings.append({
            "severity": "critical", "check": "pos_filter",
            "message": f"{leaked} pedido(s) POS passaram pelo filtro offline — verificar capi_last_error.",
        })

    is_healthy = not any(f["severity"] == "critical" for f in findings)
    return {
        "client_id": client_id,
        "pixel_id":  pixel_id,
        "name":      client.get("name") or pixel_id,
        "snippet":   snippet,
        "visitors":  visitors,
        "orders":    orders,
        "findings":  findings,
        "healthy":   is_healthy,
    }


# ── alert persistence ─────────────────────────────────────────────────────────

def _persist_finding(sb, result: dict, finding: dict) -> None:
    fp_src      = f"{result['client_id']}:{finding['check']}"
    fingerprint = hashlib.md5(fp_src.encode()).hexdigest()[:16]
    existing = (
        sb.table("alerts")
        .select("id")
        .eq("client_id", result["client_id"])
        .eq("fingerprint", fingerprint)
        .is_("resolved_at", "null")
        .limit(1)
        .execute()
    )
    if existing.data:
        return
    try:
        sb.table("alerts").insert({
            "client_id":   result["client_id"],
            "severity":    finding["severity"],
            "fingerprint": fingerprint,
            "title":       f"[Monitor] {finding['check'].replace('_', ' ').title()}",
            "message":     finding["message"],
            "data":        json.dumps({"check": finding["check"]}),
        }).execute()
    except Exception as exc:
        logger.warning("health_monitor: alert insert failed for %s/%s: %s",
                       result["pixel_id"], finding["check"], exc)


# ── email rendering ───────────────────────────────────────────────────────────

def _render_client_block(r: dict) -> str:
    sn = r["snippet"]
    vi = r["visitors"]
    od = r["orders"]

    header_color = "#16a34a" if r["healthy"] else "#dc2626"
    status_text  = "Saudável" if r["healthy"] else "ATENÇÃO"

    findings_html = ""
    if r["findings"]:
        items = "".join(
            f'<li style="margin:4px 0;font-size:12px;color:{_severity_color(f["severity"])}">'
            f'{"🔴" if f["severity"]=="critical" else "🟡"} {f["message"]}</li>'
            for f in r["findings"]
        )
        findings_html = f'<ul style="margin:8px 0 0;padding-left:18px">{items}</ul>'

    counts = sn.get("counts", {})
    funnel = " → ".join(
        f'{k}: {counts.get(k,0):,}'
        for k in _EVENT_TYPES if counts.get(k, 0) > 0
    ) or "—"

    drop_txt = ""
    if sn.get("drop_pct") is not None:
        d   = sn["drop_pct"]
        col = "#dc2626" if d >= _SNIPPET_DROP_CRITICAL else "#f59e0b" if d >= _SNIPPET_DROP_WARN else "#16a34a"
        drop_txt = f'<span style="color:{col};font-size:11px"> {"▼" if d>0 else "▲"}{abs(d):.0f}% vs. 7d</span>'

    gm_txt = ", ".join(f"{k}: {v}" for k, v in sorted(od.get("gm_counts", {}).items())) or "—"

    return f"""
    <div style="border:1px solid #e5e7eb;border-radius:8px;padding:18px;margin-bottom:16px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <h3 style="margin:0;font-size:15px;color:#111827">{r['name']}</h3>
        <span style="color:{header_color};font-size:13px;font-weight:700">{'✓' if r['healthy'] else '✗'} {status_text}</span>
      </div>
      {findings_html}
      <table style="width:100%;border-collapse:collapse;margin-top:12px;font-size:12px">
        <tr style="border-bottom:1px solid #f3f4f6">
          <td style="padding:5px 0;color:#6b7280">Eventos ontem</td>
          <td style="padding:5px 0;text-align:right">{sn['total']:,}{drop_txt}</td>
        </tr>
        <tr style="border-bottom:1px solid #f3f4f6">
          <td style="padding:5px 0;color:#6b7280">Funil</td>
          <td style="padding:5px 0;text-align:right;font-size:11px">{funnel}</td>
        </tr>
        <tr style="border-bottom:1px solid #f3f4f6">
          <td style="padding:5px 0;color:#6b7280">Visitantes / fbp</td>
          <td style="padding:5px 0;text-align:right">{vi['total']:,} / {vi['pct_fbp']:.1f}%</td>
        </tr>
        <tr style="border-bottom:1px solid #f3f4f6">
          <td style="padding:5px 0;color:#6b7280">gclid</td>
          <td style="padding:5px 0;text-align:right">{vi['pct_gclid']:.1f}%</td>
        </tr>
        <tr style="border-bottom:1px solid #f3f4f6">
          <td style="padding:5px 0;color:#6b7280">Pedidos online pagos</td>
          <td style="padding:5px 0;text-align:right">{od['paid_online']}</td>
        </tr>
        <tr style="border-bottom:1px solid #f3f4f6">
          <td style="padding:5px 0;color:#6b7280">Meta CAPI</td>
          <td style="padding:5px 0;text-align:right">{_status_badge(od['meta_missing']==0)} {od['meta_dispatched']}/{od['paid_online']}</td>
        </tr>
        <tr style="border-bottom:1px solid #f3f4f6">
          <td style="padding:5px 0;color:#6b7280">Google Ads</td>
          <td style="padding:5px 0;text-align:right">{_status_badge(od['google_missing']==0)} {od['google_dispatched']}/{od['paid_online']}</td>
        </tr>
        <tr style="border-bottom:1px solid #f3f4f6">
          <td style="padding:5px 0;color:#6b7280">Google match types</td>
          <td style="padding:5px 0;text-align:right;font-size:11px">{gm_txt}</td>
        </tr>
        <tr style="border-bottom:1px solid #f3f4f6">
          <td style="padding:5px 0;color:#6b7280">POS bloqueados</td>
          <td style="padding:5px 0;text-align:right">{od['paid_pos']} ({od['pos_correctly_skipped']} corretos)</td>
        </tr>
        <tr>
          <td style="padding:5px 0;color:#6b7280">Enriquecimento EMQ</td>
          <td style="padding:5px 0;text-align:right">{od['pct_enriched']:.0f}% (IP+UA)</td>
        </tr>
      </table>
    </div>"""


def _render_email(results: list[dict], day_label: str) -> str:
    all_healthy   = all(r["healthy"] for r in results)
    n_issues      = sum(len(r["findings"]) for r in results)
    summary_color = "#16a34a" if all_healthy else "#dc2626"
    summary_text  = "Todos os sistemas operando normalmente" if all_healthy else f"{n_issues} problema(s) detectado(s)"
    blocks        = "".join(_render_client_block(r) for r in results)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f9fafb;margin:0;padding:0">
<div style="max-width:640px;margin:0 auto;padding:24px 16px">

  <div style="background:#1e1b4b;border-radius:8px 8px 0 0;padding:20px 24px">
    <p style="margin:0;color:#a5b4fc;font-size:11px;letter-spacing:1px;text-transform:uppercase">Monitor Diário de Tracking</p>
    <h1 style="margin:4px 0 0;color:#fff;font-size:20px">Saúde do Sistema</h1>
    <p style="margin:4px 0 0;color:#8b9cf4;font-size:12px">{day_label}</p>
  </div>

  <div style="background:#fff;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;padding:24px">

    <div style="background:{'#f0fdf4' if all_healthy else '#fef2f2'};border:1px solid {'#bbf7d0' if all_healthy else '#fecaca'};border-radius:6px;padding:14px 18px;margin-bottom:20px">
      <p style="margin:0;font-weight:700;color:{summary_color};font-size:14px">
        {'✅' if all_healthy else '🚨'} {summary_text}
      </p>
    </div>

    {blocks}

    <p style="color:#9ca3af;font-size:11px;margin-top:8px">
      Verificação automática diária • Ecommerce Tracking IA
    </p>
  </div>
</div>
</body></html>"""


# ── main entry points ─────────────────────────────────────────────────────────

def run_daily_health_check(target_date: Optional[str] = None) -> list[dict]:
    """
    Evaluate all active clients for the given date (default: yesterday UTC).
    Sends a summary email to AGENCY_NOTIFY_EMAIL and persists critical/warning alerts.
    Returns the results list.
    """
    notify_email = getattr(settings, "AGENCY_NOTIFY_EMAIL", "") or ""
    if not notify_email:
        logger.warning("health_monitor: AGENCY_NOTIFY_EMAIL not set — email will be skipped")

    now = _now()
    if target_date:
        day = datetime.fromisoformat(target_date).replace(tzinfo=timezone.utc)
    else:
        day = (now - timedelta(days=1))

    day_start = day.replace(hour=0,  minute=0,  second=0,      microsecond=0).isoformat()
    day_end   = day.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
    day_label = day.strftime("%d/%m/%Y")

    sb = get_supabase()
    try:
        clients = (
            sb.table("clients")
            .select("id, pixel_id, name, is_active, tracking_enabled")
            .eq("is_active", True)
            .execute()
        )
    except Exception as exc:
        logger.error("health_monitor: failed to load clients: %s", exc)
        return []

    results: list[dict] = []
    for c in (clients.data or []):
        try:
            result = _evaluate_client(c, day_start, day_end)
            results.append(result)
            for finding in result["findings"]:
                if finding["severity"] in ("critical", "warning"):
                    _persist_finding(sb, result, finding)
        except Exception as exc:
            logger.error("health_monitor: evaluation failed for %s: %s", c.get("pixel_id"), exc)

    if not results:
        logger.info("health_monitor: no results for %s", day_label)
        return results

    all_healthy = all(r["healthy"] for r in results)
    n_issues    = sum(len(r["findings"]) for r in results)
    subject = (
        f"✅ Tracking OK — {day_label}"
        if all_healthy
        else f"🚨 {n_issues} problema(s) — {day_label}"
    )

    # Email (sempre para AGENCY_NOTIFY_EMAIL)
    if notify_email:
        try:
            email_service.send_email(to=notify_email, subject=subject, html_body=_render_email(results, day_label))
            logger.info("health_monitor: email sent to %s (%d clients, %d issues)",
                        notify_email, len(results), n_issues)
        except Exception as exc:
            logger.error("health_monitor: email failed: %s", exc)

    # WhatsApp — só se houver problemas críticos/warning
    if not all_healthy:
        try:
            _notify.notify_health_issues(results)
        except Exception as exc:
            logger.error("health_monitor: whatsapp notify failed: %s", exc)

    return results


def run_daily_health_check_safe() -> None:
    """Entry point for APScheduler — wraps run_daily_health_check, never raises."""
    try:
        results  = run_daily_health_check()
        n_issues = sum(len(r["findings"]) for r in results)
        if n_issues:
            logger.warning("health_monitor: %d issue(s) found across %d client(s)", n_issues, len(results))
        else:
            logger.info("health_monitor: all %d client(s) healthy", len(results))
    except Exception as exc:
        logger.error("health_monitor: unexpected error: %s", exc)
