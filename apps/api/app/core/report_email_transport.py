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
import uuid
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Protocol

from .canonical_reports import Reports, ReportError, digest as canonical_digest

logger = logging.getLogger(__name__)

# ── Internal Test Email Constants (PRD §16.3 / R02) ───────────────────────────
# Hardcoded backend allowlist — NEVER overridden by request body or client settings.
INTERNAL_TEST_RECIPIENT = "contato@norolabs.com.br"
INTERNAL_TEST_SUBJECT_PREFIX = "[TESTE INTERNO]"
TEST_DELIVERY_ALLOWED_STATUSES = frozenset({
    "DRAFT",
    "READY_FOR_REVIEW",
    "APPROVED",
    "PUBLISHED",
})
_NON_HUMAN_ACTORS = frozenset({
    "",
    "hermes",
    "hermes_service",
    "platform_web",
    "agency_admin",
    "system",
    "scheduler",
    "canonical_analysis_callback",
})
_ARTIFACT_HASH_EXCLUDE = frozenset({
    "artifact_hash",
    "status",
    "approved_by",
    "approved_at",
    "published_by",
    "published_at",
    "superseded_by_version",
    "superseded_at",
})
TEST_DELIVERY_COOLDOWN_SECONDS = 30
TEST_DELIVERY_MAX_PER_HOUR = 10


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


# ── Shared Canonical Rendering & Subject Helpers ──────────────────────────────


def build_canonical_subject(report: dict, *, test_mode: bool = False) -> str:
    period_s = report.get("period_start", "")
    period_e = report.get("period_end", "")
    rtype = (report.get("report_type") or "report").title()
    base = f"Relatório Noro — {rtype} | {period_s} a {period_e}"
    return f"{INTERNAL_TEST_SUBJECT_PREFIX} {base}" if test_mode else base


def render_canonical_report_email(
    report: dict,
    *,
    cta_authorized: bool,
) -> tuple[str, dict]:
    """
    Render canonical report HTML via v5 canonical_email_renderer.

    Any renderer failure raises ReportError("RENDER_FAILED") BEFORE any
    transport/Resend call is made (zero external provider calls on failure).
    """
    try:
        from .canonical_email_renderer import render_weekly_email, render_monthly_email

        artifact = {
            "report_id":      report.get("report_id"),
            "client_id":      report.get("client_id"),
            "period_start":   (report.get("period_start") or "")[:10],
            "period_end":     (report.get("period_end") or "")[:10],
            "report_type":    report.get("report_type"),
            "version":        report.get("version"),
            "status":         report.get("status"),
            "content":        report.get("content") or {},
            "cta_authorized": bool(cta_authorized),
        }
        if report.get("report_type") == "weekly":
            return render_weekly_email(artifact)
        return render_monthly_email(artifact)
    except Exception:
        logger.exception("v5 renderer failed for %s — blocking delivery", report.get("report_id"))
        raise ReportError("RENDER_FAILED")


# ── Commercial Transport ──────────────────────────────────────────────────────

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

    subject = build_canonical_subject(report, test_mode=False)
    html, _ = render_canonical_report_email(report, cta_authorized=True)

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


# ── Internal Test Delivery Audit Store & Transport (PRD §16.3 / R02) ──────────


class TestDeliveryAuditStore(Protocol):
    def get_by_idempotency_key(
        self, report_id: str, report_version: int, idempotency_key: str
    ) -> dict[str, Any] | None: ...

    def count_recent_for_report(
        self, report_id: str, report_version: int, since_iso: str
    ) -> int: ...

    def count_recent_for_actor(self, actor: str, since_iso: str) -> int: ...

    def create(self, row: dict[str, Any]) -> tuple[dict[str, Any], bool]: ...

    def update(self, record_id: str, patch: dict[str, Any]) -> dict[str, Any]: ...

    def list_for_report(self, report_id: str, limit: int = 20) -> list[dict[str, Any]]: ...


class InMemoryTestDeliveryAuditStore:
    """Isolated in-memory audit store for unit tests (zero DB dependency)."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def get_by_idempotency_key(
        self, report_id: str, report_version: int, idempotency_key: str
    ) -> dict[str, Any] | None:
        for r in self.rows:
            if (
                r["report_id"] == report_id
                and r["report_version"] == report_version
                and r["idempotency_key"] == idempotency_key
            ):
                return dict(r)
        return None

    def count_recent_for_report(
        self, report_id: str, report_version: int, since_iso: str
    ) -> int:
        return sum(
            1
            for r in self.rows
            if r["report_id"] == report_id
            and r["report_version"] == report_version
            and r["created_at"] >= since_iso
        )

    def count_recent_for_actor(self, actor: str, since_iso: str) -> int:
        return sum(
            1
            for r in self.rows
            if r["actor"] == actor and r["created_at"] >= since_iso
        )

    def create(self, row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        existing = self.get_by_idempotency_key(
            row["report_id"], row["report_version"], row["idempotency_key"]
        )
        if existing is not None:
            return existing, False
        rec = dict(row)
        rec.setdefault("id", str(uuid.uuid4()))
        rec.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        self.rows.append(rec)
        return dict(rec), True

    def update(self, record_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        for r in self.rows:
            if r["id"] == record_id:
                r.update(patch)
                return dict(r)
        raise KeyError(record_id)

    def list_for_report(self, report_id: str, limit: int = 20) -> list[dict[str, Any]]:
        matched = [dict(r) for r in self.rows if r["report_id"] == report_id]
        matched.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return matched[:limit]


class SupabaseTestDeliveryAuditStore:
    """Supabase-backed audit store for `core_canonical_report_test_deliveries`."""

    TABLE = "core_canonical_report_test_deliveries"

    def __init__(self, sb) -> None:
        self._sb = sb

    def get_by_idempotency_key(
        self, report_id: str, report_version: int, idempotency_key: str
    ) -> dict[str, Any] | None:
        res = (
            self._sb.table(self.TABLE)
            .select("*")
            .eq("report_id", report_id)
            .eq("report_version", report_version)
            .eq("idempotency_key", idempotency_key)
            .limit(1)
            .execute()
        )
        return dict(res.data[0]) if (res and res.data) else None

    def count_recent_for_report(
        self, report_id: str, report_version: int, since_iso: str
    ) -> int:
        res = (
            self._sb.table(self.TABLE)
            .select("id", count="exact", head=True)
            .eq("report_id", report_id)
            .eq("report_version", report_version)
            .gte("created_at", since_iso)
            .execute()
        )
        return int(res.count or 0) if res else 0

    def count_recent_for_actor(self, actor: str, since_iso: str) -> int:
        res = (
            self._sb.table(self.TABLE)
            .select("id", count="exact", head=True)
            .eq("actor", actor)
            .gte("created_at", since_iso)
            .execute()
        )
        return int(res.count or 0) if res else 0

    def create(self, row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        try:
            res = self._sb.table(self.TABLE).insert(row).execute()
            if res and res.data:
                return dict(res.data[0]), True
        except Exception as exc:
            err = str(exc).lower()
            if any(k in err for k in ("23505", "unique", "duplicate")):
                existing = self.get_by_idempotency_key(
                    row["report_id"], row["report_version"], row["idempotency_key"]
                )
                if existing is not None:
                    return existing, False
            raise
        existing = self.get_by_idempotency_key(
            row["report_id"], row["report_version"], row["idempotency_key"]
        )
        if existing is not None:
            return existing, False
        raise RuntimeError("Failed to create test delivery audit row")

    def update(self, record_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        res = self._sb.table(self.TABLE).update(patch).eq("id", record_id).execute()
        if res and res.data:
            return dict(res.data[0])
        return {"id": record_id, **patch}

    def list_for_report(self, report_id: str, limit: int = 20) -> list[dict[str, Any]]:
        res = (
            self._sb.table(self.TABLE)
            .select("*")
            .eq("report_id", report_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return [dict(r) for r in (res.data or [])]


def dispatch_test_email(
    *,
    report_id: str,
    report_version: int,
    client_id: str,
    actor: str,
    idempotency_key: str,
    reports: Reports,
    audit_store: TestDeliveryAuditStore,
    requested_recipient: str | None = None,
    cooldown_seconds: int = TEST_DELIVERY_COOLDOWN_SECONDS,
    max_per_hour: int = TEST_DELIVERY_MAX_PER_HOUR,
) -> dict[str, Any]:
    """
    Send an isolated internal test email for an exact canonical report version (PRD §16.3 / R02).

    Invariants enforced server-side:
      - Exclusive hardcoded destination: `contato@norolabs.com.br` (any external
        requested_recipient raises ReportError("INTERNAL_TEST_RECIPIENT_ONLY");
        client delivery settings are NEVER consulted).
      - Subject always prefixed with `[TESTE INTERNO]`.
      - `cta_authorized=False` hardcoded (blocked CTA notice only).
      - Allowed statuses: DRAFT, READY_FOR_REVIEW, APPROVED, PUBLISHED (SUPERSEDED blocked).
      - Report version document, status, artifact_hash, and commercial `delivery:*`
        receipts are NEVER mutated.
      - RENDER_FAILED records audit status `RENDER_FAILED` and makes ZERO Resend calls.
      - Idempotent on `(report_id, report_version, idempotency_key)` + sliding-window rate limit.
    """
    from ..services import resend as _email

    # 1. Hard allowlist enforcement — reject any explicit external recipient attempt
    if requested_recipient is not None:
        if requested_recipient.strip().lower() != INTERNAL_TEST_RECIPIENT:
            raise ReportError("INTERNAL_TEST_RECIPIENT_ONLY")

    # 2. Human operator check
    clean_actor = (actor or "").strip()
    if not clean_actor or clean_actor.lower() in _NON_HUMAN_ACTORS:
        raise ReportError("HUMAN_ACTOR_REQUIRED")

    clean_key = (idempotency_key or "").strip()
    if len(clean_key) < 8:
        raise ReportError("IDEMPOTENCY_KEY_REQUIRED")

    # 3. Load exact report version & validate cross-client scope
    report = reports.get(report_id, report_version)
    if client_id and report.get("client_id") != client_id:
        raise ReportError("CLIENT_SCOPE_MISMATCH")

    # 4. Lifecycle status guard (SUPERSEDED forbidden)
    status_at_send = str(report.get("status") or "")
    if status_at_send not in TEST_DELIVERY_ALLOWED_STATUSES:
        raise ReportError("TEST_DELIVERY_STATUS_NOT_ALLOWED")

    # 5. Artifact hash integrity check (if artifact_hash present)
    stored_hash = report.get("artifact_hash") or ""
    if stored_hash:
        check_doc = {k: v for k, v in report.items() if k not in _ARTIFACT_HASH_EXCLUDE}
        if canonical_digest(check_doc) != stored_hash:
            raise ReportError("ARTIFACT_INTEGRITY_ERROR")

    # 6. Idempotency replay check (before rate limiting)
    existing = audit_store.get_by_idempotency_key(report_id, report_version, clean_key)
    if existing is not None:
        return {**existing, "recipient": INTERNAL_TEST_RECIPIENT, "idempotent_replay": True}

    # 7. Sliding-window rate limit
    now = datetime.now(timezone.utc)
    if cooldown_seconds > 0:
        since_cooldown = (now - timedelta(seconds=cooldown_seconds)).isoformat()
        if audit_store.count_recent_for_report(report_id, report_version, since_cooldown) > 0:
            raise ReportError("TEST_DELIVERY_RATE_LIMITED")
    if max_per_hour > 0:
        since_hour = (now - timedelta(hours=1)).isoformat()
        if audit_store.count_recent_for_actor(clean_actor, since_hour) >= max_per_hour:
            raise ReportError("TEST_DELIVERY_RATE_LIMITED")

    subject = build_canonical_subject(report, test_mode=True)
    recipient_hash = sha256(INTERNAL_TEST_RECIPIENT.encode("utf-8")).hexdigest()

    base_audit_row: dict[str, Any] = {
        "event_type":                "TEST_DELIVERY",
        "idempotency_key":           clean_key,
        "report_id":                 report_id,
        "report_version":            report_version,
        "artifact_hash":             stored_hash,
        "report_status_at_send":     status_at_send,
        "client_id":                 str(report.get("client_id") or client_id),
        "report_type":               str(report.get("report_type") or "weekly"),
        "channel":                   "EMAIL",
        "recipient_allowlist_email": INTERNAL_TEST_RECIPIENT,
        "recipient_hash":            recipient_hash,
        "subject":                   subject,
        "cta_authorized":            False,
        "actor":                     clean_actor,
        "status":                    "PENDING",
        "provider_message_id":       None,
        "error_code":                None,
        "created_at":                now.isoformat(),
        "sent_at":                   None,
    }

    # 8. Render HTML BEFORE touching Resend (cta_authorized=False hardcoded)
    try:
        html, _ = render_canonical_report_email(report, cta_authorized=False)
    except ReportError as exc:
        if str(exc) == "RENDER_FAILED":
            failed_row = {**base_audit_row, "status": "RENDER_FAILED", "error_code": "RENDER_FAILED"}
            try:
                audit_store.create(failed_row)
            except Exception:
                logger.warning("test_delivery: failed to persist RENDER_FAILED audit row for %s", report_id)
        raise

    # 9. Atomically reserve PENDING audit row (protects against concurrent double-click)
    reserved, created = audit_store.create(base_audit_row)
    if not created:
        return {**reserved, "recipient": INTERNAL_TEST_RECIPIENT, "idempotent_replay": True}

    record_id = str(reserved["id"])

    # 10. Send exclusively to INTERNAL_TEST_RECIPIENT (no CC/BCC, no client recipients)
    ok, msg_id, err = _email.send_email_tracked(
        INTERNAL_TEST_RECIPIENT,
        subject,
        html,
        from_name="Noro Reports",
    )

    if ok:
        sent_at = datetime.now(timezone.utc).isoformat()
        final = audit_store.update(
            record_id,
            {
                "status":              "SENT",
                "provider_message_id": msg_id or f"test-sent:{record_id}",
                "sent_at":             sent_at,
                "error_code":          None,
            },
        )
        return {**final, "recipient": INTERNAL_TEST_RECIPIENT, "idempotent_replay": False}

    error_code = _classify_provider_error(err)
    audit_store.update(
        record_id,
        {
            "status":     "FAILED",
            "error_code": error_code,
        },
    )
    raise ReportError(f"TEST_DELIVERY_FAILED:{error_code}")
