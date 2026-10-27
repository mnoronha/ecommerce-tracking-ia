"""
Controlled email transport for canonical report delivery receipts.

Rules:
- Caller provides raw recipient address; it is verified against the stored hash, never persisted.
- Only DELIVERY_PENDING receipts are sent. DELIVERED → idempotent return. DELIVERY_FAILED → explicit retry via retry_delivery first.
- Provider error → DELIVERY_FAILED + sanitised error_code. No silent fallback, no auto-retry.
- Zero client sends. Zero WhatsApp sends.
"""
from __future__ import annotations

import logging
from hashlib import sha256

from .canonical_reports import Reports, ReportError

logger = logging.getLogger(__name__)


# ── Email body ────────────────────────────────────────────────────────────────

def build_report_email_html(report: dict) -> str:
    """Build plain but branded HTML email from a canonical report document."""
    client_id   = report.get("client_id", "")
    period_s    = report.get("period_start", "")
    period_e    = report.get("period_end", "")
    rtype       = report.get("report_type", "report")
    version     = report.get("version", 1)
    engine_ver  = report.get("engine_version", "")
    published   = report.get("published_at", "")
    ah          = report.get("artifact_hash", "")
    engine      = report.get("canonical_engine", "")

    type_label  = "Semanal" if rtype == "weekly" else "Mensal" if rtype == "monthly" else rtype.title()

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body{{font-family:Arial,sans-serif;background:#f5f5f5;margin:0;padding:24px}}
.wrap{{max-width:600px;margin:0 auto;background:#fff;border-radius:8px;padding:32px;border:1px solid #e8e8e8}}
h1{{color:#111;font-size:20px;margin:0 0 4px}}
.sub{{color:#777;font-size:13px;margin:0 0 24px}}
.field{{margin-bottom:14px}}
.label{{color:#999;font-size:11px;text-transform:uppercase;letter-spacing:.5px}}
.value{{color:#222;font-size:14px;margin-top:2px}}
.hash{{font-family:monospace;font-size:11px;color:#666;word-break:break-all}}
.footer{{margin-top:28px;padding-top:16px;border-top:1px solid #eee;color:#aaa;font-size:11px}}
</style>
</head>
<body>
<div class="wrap">
  <h1>Relatório Noro — {type_label}</h1>
  <p class="sub">Piloto interno · Não compartilhe externamente</p>

  <div class="field">
    <div class="label">Cliente</div>
    <div class="value">{client_id}</div>
  </div>
  <div class="field">
    <div class="label">Período</div>
    <div class="value">{period_s} a {period_e}</div>
  </div>
  <div class="field">
    <div class="label">Tipo</div>
    <div class="value">{type_label} · Engine {engine} v{engine_ver[:8]}</div>
  </div>
  <div class="field">
    <div class="label">Versão</div>
    <div class="value">v{version}</div>
  </div>
  <div class="field">
    <div class="label">Publicado em</div>
    <div class="value">{published}</div>
  </div>
  <div class="field">
    <div class="label">Hash do artefato</div>
    <div class="value hash">{ah}</div>
  </div>

  <div class="footer">Noro Platform · Relatório canônico · uso interno</div>
</div>
</body>
</html>"""


# ── Error classification ───────────────────────────────────────────────────────

def _classify_provider_error(err: str | None) -> str:
    if not err:
        return "TRANSPORT_UNAVAILABLE"
    e = err.lower()
    if "timeout" in e or "timed out" in e or "read timeout" in e:
        return "TIMEOUT_REQUIRES_RECONCILIATION"
    if "http" in e:
        return "PROVIDER_REJECTED"
    return "TRANSPORT_UNAVAILABLE"


# ── Transport ─────────────────────────────────────────────────────────────────

def dispatch_delivery(delivery_id: str, recipient: str, reports: Reports) -> dict:
    """
    Execute email transport for an existing delivery receipt.

    - Verifies recipient hash against stored value.
    - DELIVERED receipts are returned as-is (idempotent).
    - DELIVERY_FAILED receipts must be reset via retry_delivery before calling here
      (dispatch_delivery itself calls retry_delivery when status is DELIVERY_FAILED,
       so callers may call this unconditionally for explicit retries).
    - Provider error → DELIVERY_FAILED; no silent fallback.
    """
    from ..services import resend as _email

    receipt = reports.get_delivery(delivery_id)

    # Verify recipient without logging the raw address
    expected_hash = sha256(recipient.encode("utf-8")).hexdigest()
    if receipt["recipient"] != expected_hash:
        raise ReportError("DELIVERY_HASH_MISMATCH")

    # Idempotency: already delivered
    if receipt["status"] == "DELIVERED":
        return receipt

    # Explicit retry from DELIVERY_FAILED
    if receipt["status"] == "DELIVERY_FAILED":
        receipt = reports.retry_delivery(delivery_id)

    if receipt["status"] != "DELIVERY_PENDING":
        raise ReportError("DELIVERY_RESULT_CONFLICT")

    # Fetch bound report artifact
    report = reports.get(receipt["report_id"], receipt["report_version"])

    # Build and send
    period_s = report.get("period_start", "")
    period_e = report.get("period_end", "")
    rtype    = (report.get("report_type") or "report").title()
    subject  = f"Relatório Noro — {rtype} | {period_s} a {period_e}"
    html     = build_report_email_html(report)

    ok, msg_id, err = _email.send_email_tracked(recipient, subject, html, from_name="Noro Reports")

    if ok:
        return reports.record_delivery_result(
            delivery_id,
            artifact_hash=receipt["artifact_hash"],
            provider_message_id=msg_id or f"sent:{delivery_id}",
        )

    error_code = _classify_provider_error(err)
    return reports.record_delivery_result(
        delivery_id,
        artifact_hash=receipt["artifact_hash"],
        error_code=error_code,
    )
