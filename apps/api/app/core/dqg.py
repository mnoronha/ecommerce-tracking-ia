"""
Data Quality Gate (DQG) — Etapa 4 Bloco 1.

10 structured checks per collection result:
  1.  COLLECTION_OK       — collector ran without exception
  2.  SCHEMA_OK           — expected keys present in aggregates
  3.  ACCOUNT_ID_OK       — account_id non-empty
  4.  PERIOD_OK           — period_start <= period_end, both set
  5.  CURRENCY_OK         — client_currency matches expected (BRL)
  6.  TIMEZONE_OK         — client_timezone non-empty
  7.  FRESHNESS_OK        — data age within source-specific threshold
  8.  DUPLICATE_CHECK_OK  — no duplicate keys in raw rows
  9.  ROW_COUNT_SANITY_OK — row count < MAX_ROWS
  10. RECONCILIATION_OK   — populated post-write by the reconciler

source_state and value_status are distinct concepts:
  source_state describes the SOURCE health (used in core_data_sources).
  value_status describes an individual METRIC VALUE (used in core_metric_snapshots).

"Ausência" (absence) ≠ zero:
  - COLLECTION_OK fails → value_status = MISSING, value = None
  - COLLECTION_OK passes + 0 rows → source_state = NO_DATA, value_status = NO_DATA
  - COLLECTION_OK passes + rows > 0 → value_status = OK (or PARTIAL/STALE per source_state)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional


# ── Data transfer objects ─────────────────────────────────────────────────────

@dataclass
class CollectionResult:
    source_system: str      # "meta_ads" | "google_ads" | "ga4" | "shopify"
    semantic_domain: str    # "ADS" | "JOURNEY" | "BUSINESS"
    account_id: str         # platform account identifier
    client_currency: str    # from client truth (e.g. "BRL")
    client_timezone: str    # from client truth (e.g. "America/Sao_Paulo")
    period_start: date
    period_end: date
    rows: list[dict]        # raw data rows from source API / DB
    aggregates: dict[str, float]  # metric_key → computed aggregate
    collected_at: datetime
    error: Optional[str] = None  # set if collector raised an exception


@dataclass
class DQCheck:
    name: str
    passed: bool
    detail: Optional[str] = None


@dataclass
class DQGReport:
    source_system: str
    semantic_domain: str
    account_id: str
    period_start: date
    period_end: date
    checks: list[DQCheck] = field(default_factory=list)

    # ── Accessors ──────────────────────────────────────────────────────────

    def get_check(self, name: str) -> Optional[DQCheck]:
        return next((c for c in self.checks if c.name == name), None)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def has_data(self) -> bool:
        coll = self.get_check("COLLECTION_OK")
        if coll and not coll.passed:
            return False
        rc = self.get_check("ROW_COUNT_SANITY_OK")
        if rc and rc.detail and "0 rows" in rc.detail:
            return False
        return True

    @property
    def source_state(self) -> str:
        """
        Derives SourceState from check results.
        Priority: hard failures first, soft degradations last.
        """
        passed = {c.name: c.passed for c in self.checks}

        if not passed.get("COLLECTION_OK", False):
            err_check = self.get_check("COLLECTION_OK")
            detail = (err_check.detail or "").lower()
            if "permission" in detail or "token" in detail or "oauth" in detail:
                return "PERMISSION_DENIED"
            if "unauthorized" in detail or "403" in detail:
                return "ACCESS_MISSING"
            return "ERROR"

        if not passed.get("SCHEMA_OK", False):
            return "ERROR"
        if not passed.get("ACCOUNT_ID_OK", False):
            return "ERROR"

        if not self.has_data:
            return "NO_DATA"

        if not passed.get("FRESHNESS_OK", True):
            return "STALE"

        soft_fails = [
            "PERIOD_OK", "CURRENCY_OK", "TIMEZONE_OK",
            "DUPLICATE_CHECK_OK", "RECONCILIATION_OK",
        ]
        if any(not passed.get(c, True) for c in soft_fails):
            return "PARTIAL"

        if not passed.get("ROW_COUNT_SANITY_OK", True):
            return "PARTIAL"

        return "READY"

    @property
    def value_status(self) -> str:
        """Derives ValueStatus from source_state."""
        ss = self.source_state
        mapping = {
            "READY":            "OK",
            "PARTIAL":          "PARTIAL",
            "STALE":            "STALE",
            "NO_DATA":          "NO_DATA",
            "MISSING":          "MISSING",
            "ACCESS_MISSING":   "MISSING",
            "PERMISSION_DENIED": "MISSING",
            "NOT_CONTRACTED":   "NOT_APPLICABLE",
            "NOT_APPLICABLE":   "NOT_APPLICABLE",
            "ERROR":            "MISSING",
        }
        return mapping.get(ss, "UNKNOWN")

    def add_reconciliation_result(self, passed: bool, detail: Optional[str] = None) -> None:
        self.checks = [c for c in self.checks if c.name != "RECONCILIATION_OK"]
        self.checks.append(DQCheck("RECONCILIATION_OK", passed, detail))

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "source_system":   self.source_system,
            "semantic_domain": self.semantic_domain,
            "account_id":      self.account_id,
            "source_state":    self.source_state,
            "value_status":    self.value_status,
            "all_passed":      self.all_passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
        }


# ── Expected aggregates per source_system ─────────────────────────────────────

_EXPECTED_AGGREGATE_KEYS: dict[str, set[str]] = {
    "meta_ads":   {"meta_spend", "meta_conversions", "meta_conversion_value"},
    "google_ads": {"google_spend", "google_conversions", "google_conversion_value"},
    "ga4":        {"ga4_sessions", "ga4_purchases", "ga4_revenue"},
    "shopify":    {"revenue_business", "orders_count"},
}

# Hours before collected data is considered stale per source
_FRESHNESS_HOURS: dict[str, int] = {
    "shopify":    24,
    "meta_ads":   48,
    "google_ads": 48,
    "ga4":        72,   # GA4 has typical processing lag
}

# Dedup key builders per source_system (returns a hashable key per row)
_DEDUP_KEY: dict[str, Any] = {
    "meta_ads":   lambda r: r.get("campaign_id"),
    # campaign_id: per-campaign rows (new). date: summary_row fallback (old, no campaign_id).
    "google_ads": lambda r: r.get("campaign_id") or r.get("date"),
    "ga4":        lambda r: r.get("channel"),
    "shopify":    lambda r: r.get("id") or r.get("order_id"),  # orders table PK is "id"
}

_MAX_ROWS = 100_000


# ── Main DQG runner ───────────────────────────────────────────────────────────

def run_dqg(result: CollectionResult) -> DQGReport:
    """
    Run checks 1–9 on a CollectionResult.
    Check 10 (RECONCILIATION_OK) is added later via
    report.add_reconciliation_result().
    """
    report = DQGReport(
        source_system=result.source_system,
        semantic_domain=result.semantic_domain,
        account_id=result.account_id,
        period_start=result.period_start,
        period_end=result.period_end,
    )

    # 1. COLLECTION_OK ─────────────────────────────────────────────────────────
    collection_ok = result.error is None
    report.checks.append(DQCheck("COLLECTION_OK", collection_ok, result.error))

    if not collection_ok:
        _add_skipped(report, [
            "SCHEMA_OK", "ACCOUNT_ID_OK", "PERIOD_OK", "CURRENCY_OK",
            "TIMEZONE_OK", "FRESHNESS_OK", "DUPLICATE_CHECK_OK",
            "ROW_COUNT_SANITY_OK", "RECONCILIATION_OK",
        ])
        return report

    # 2. SCHEMA_OK ─────────────────────────────────────────────────────────────
    expected = _EXPECTED_AGGREGATE_KEYS.get(result.source_system, set())
    missing  = expected - set(result.aggregates.keys())
    schema_ok = len(missing) == 0
    report.checks.append(DQCheck(
        "SCHEMA_OK", schema_ok,
        f"missing aggregate keys: {missing}" if not schema_ok else None,
    ))

    # 3. ACCOUNT_ID_OK ─────────────────────────────────────────────────────────
    acct_ok = bool(result.account_id and result.account_id.strip())
    report.checks.append(DQCheck(
        "ACCOUNT_ID_OK", acct_ok,
        "account_id is empty" if not acct_ok else None,
    ))

    # 4. PERIOD_OK ─────────────────────────────────────────────────────────────
    period_ok = (
        result.period_start is not None
        and result.period_end is not None
        and result.period_start <= result.period_end
    )
    report.checks.append(DQCheck(
        "PERIOD_OK", period_ok,
        f"invalid period {result.period_start}→{result.period_end}" if not period_ok else None,
    ))

    # 5. CURRENCY_OK ───────────────────────────────────────────────────────────
    currency_ok = bool(result.client_currency)
    report.checks.append(DQCheck(
        "CURRENCY_OK", currency_ok,
        "client_currency is empty" if not currency_ok else None,
    ))

    # 6. TIMEZONE_OK ───────────────────────────────────────────────────────────
    tz_ok = bool(result.client_timezone)
    report.checks.append(DQCheck(
        "TIMEZONE_OK", tz_ok,
        "client_timezone is empty" if not tz_ok else None,
    ))

    # 7. FRESHNESS_OK ──────────────────────────────────────────────────────────
    threshold = _FRESHNESS_HOURS.get(result.source_system, 48)
    now = datetime.now(timezone.utc)
    collected_at = result.collected_at
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=timezone.utc)
    age_hours = (now - collected_at).total_seconds() / 3600
    fresh_ok = age_hours <= threshold
    report.checks.append(DQCheck(
        "FRESHNESS_OK", fresh_ok,
        f"age {age_hours:.1f}h exceeds {threshold}h threshold" if not fresh_ok else None,
    ))

    # 8. DUPLICATE_CHECK_OK ────────────────────────────────────────────────────
    dedup_fn = _DEDUP_KEY.get(result.source_system)
    if result.rows and dedup_fn:
        keys     = [dedup_fn(r) for r in result.rows]
        has_dupe = len(keys) != len(set(keys))
        report.checks.append(DQCheck(
            "DUPLICATE_CHECK_OK", not has_dupe,
            f"duplicate keys in {len(result.rows)} rows" if has_dupe else None,
        ))
    else:
        report.checks.append(DQCheck("DUPLICATE_CHECK_OK", True, "no rows to dedup"))

    # 9. ROW_COUNT_SANITY_OK ───────────────────────────────────────────────────
    n = len(result.rows)
    count_ok = n < _MAX_ROWS
    detail: Optional[str] = None
    if n == 0:
        detail = f"0 rows for {result.source_system} in period"
    elif not count_ok:
        detail = f"{n} rows exceeds sanity limit {_MAX_ROWS}"
    report.checks.append(DQCheck("ROW_COUNT_SANITY_OK", count_ok, detail))

    # 10. RECONCILIATION_OK — placeholder; filled by reconciler post-write
    report.checks.append(DQCheck("RECONCILIATION_OK", True, "pending reconciliation"))

    return report


def _add_skipped(report: DQGReport, names: list[str]) -> None:
    for name in names:
        report.checks.append(DQCheck(name, False, "skipped: COLLECTION_OK failed"))
