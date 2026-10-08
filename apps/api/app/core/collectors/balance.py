"""
Balance collector for prepaid ad accounts.

Supported platforms:
  google — GAQL billing_setup (payment_setting) + account_budget remaining micros
  meta   — Graph API balance field

Primary alert rule: estimated_days_remaining (balance / avg_daily_spend).
Absolute threshold is a fallback only when no reliable spend data exists.

NEVER treats campaign_budget, daily_budget, or spend_cap as balance.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from ...config import settings
from ...services.spend_sync import _get_google_token

logger = logging.getLogger(__name__)

_GOOGLE_ADS_API = "https://googleads.googleapis.com/v23"
_META_API_BASE  = "https://graph.facebook.com/v19.0"

# Days-remaining thresholds (primary rule)
_DAYS_LOW       = 3   # <= 3 days → LOW
_DAYS_CRITICAL  = 1   # <= 1 day  → CRITICAL
# Fraction of threshold_low for absolute fallback
_CRITICAL_FRACTION = 0.5


class BalanceSnapshot(dict):
    """
    Keys (all platforms):
      billing_model (str|None), collection_status (str),
      balance (float|None), balance_status (str),
      avg_daily_spend (float|None), spend_avg_window_days (int|None),
      spend_data_days (int|None), estimated_days_remaining (float|None),
      threshold_low (float|None), threshold_critical (float|None),
      currency (str|None), raw (dict|None), error (str|None)
    """


# ── Classification ─────────────────────────────────────────────────────────────

def _classify(
    balance: Optional[float],
    estimated_days: Optional[float],
    threshold_low: Optional[float],
) -> str:
    """
    Primary rule: estimated_days_remaining.
    Fallback (when no spend data): absolute threshold.
    """
    if balance is None:
        return "NO_DATA"
    if balance <= 0:
        return "EXHAUSTED"

    if estimated_days is not None:
        if estimated_days <= _DAYS_CRITICAL:
            return "CRITICAL"
        if estimated_days <= _DAYS_LOW:
            return "LOW"
        return "OK"

    # Fallback: absolute threshold
    if threshold_low is not None:
        threshold_critical = threshold_low * _CRITICAL_FRACTION
        if balance <= threshold_critical:
            return "CRITICAL"
        if balance <= threshold_low:
            return "LOW"

    return "OK"


def _compute_days(balance: Optional[float], avg_daily_spend: Optional[float]) -> Optional[float]:
    if balance is None or avg_daily_spend is None or avg_daily_spend <= 0:
        return None
    return round(balance / avg_daily_spend, 1)


# ── Google Ads ─────────────────────────────────────────────────────────────────

_BILLING_SETUP_QUERY = (
    "SELECT billing_setup.id, billing_setup.status, billing_setup.payment_setting "
    "FROM billing_setup "
    "WHERE billing_setup.status = 'APPROVED' "
    "ORDER BY billing_setup.id DESC LIMIT 1"
)

_ACCOUNT_BUDGET_QUERY = (
    "SELECT "
    "  account_budget.id, "
    "  account_budget.status, "
    "  account_budget.adjusted_spending_limit_micros, "
    "  account_budget.amount_served_micros, "
    "  account_budget.approved_spending_limit_micros, "
    "  account_budget.total_adjustments_micros "
    "FROM account_budget "
    "WHERE account_budget.status = 'APPROVED' "
    "ORDER BY account_budget.id DESC LIMIT 1"
)

_PAYMENT_SETTING_LABEL: dict[str, str] = {
    "PAY_MANUALLY":      "prepaid",
    "AUTOMATIC_BILLING": "postpaid",
    "MANUAL_CPC_BIDDING": "manual",
}


def _google_headers(token: str, manager_id: Optional[str] = None) -> dict:
    h = {
        "Authorization":   f"Bearer {token}",
        "developer-token": settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        "Content-Type":    "application/json",
    }
    if manager_id:
        h["login-customer-id"] = manager_id.replace("-", "").replace(" ", "")
    return h


def _blocked(reason: str, threshold_low: Optional[float], currency: Optional[str], raw: Optional[dict] = None) -> BalanceSnapshot:
    return BalanceSnapshot(
        billing_model=None, collection_status="BLOCKED",
        balance=None, balance_status="MISSING",
        avg_daily_spend=None, spend_avg_window_days=None,
        spend_data_days=None, estimated_days_remaining=None,
        threshold_low=threshold_low,
        threshold_critical=threshold_low * _CRITICAL_FRACTION if threshold_low else None,
        currency=currency, raw=raw, error=reason,
    )


def _denied(reason: str, threshold_low: Optional[float], currency: Optional[str], raw: Optional[dict] = None) -> BalanceSnapshot:
    return BalanceSnapshot(
        billing_model=None, collection_status="PERMISSION_DENIED",
        balance=None, balance_status="PERMISSION_DENIED",
        avg_daily_spend=None, spend_avg_window_days=None,
        spend_data_days=None, estimated_days_remaining=None,
        threshold_low=threshold_low,
        threshold_critical=threshold_low * _CRITICAL_FRACTION if threshold_low else None,
        currency=currency, raw=raw, error=reason,
    )


def collect_google_balance(
    customer_id: str,
    refresh_token: str,
    threshold_low: Optional[float],
    currency: Optional[str] = None,
    manager_id: Optional[str] = None,
    avg_daily_spend: Optional[float] = None,
    spend_avg_window_days: Optional[int] = None,
    spend_data_days: Optional[int] = None,
) -> BalanceSnapshot:
    """
    Queries Google Ads GAQL for:
      1. billing_setup.payment_setting (confirms prepaid / billing model)
      2. account_budget.adjusted_spending_limit_micros - amount_served_micros (remaining)

    Official signal: adjusted_spending_limit_micros - amount_served_micros on the
    APPROVED account_budget. This is the closest official proxy to remaining balance
    in Google Ads; Google does not expose a single "account_balance" field in the API.

    NOT used: campaign_budget, daily_budget.
    """
    if not all([
        settings.GOOGLE_ADS_DEVELOPER_TOKEN,
        settings.GOOGLE_ADS_OAUTH_CLIENT_ID,
        settings.GOOGLE_ADS_OAUTH_CLIENT_SECRET,
    ]):
        return _blocked("Google Ads OAuth credentials not configured", threshold_low, currency)

    token = _get_google_token(refresh_token)
    if not token:
        return _blocked("Failed to obtain Google OAuth access token", threshold_low, currency)

    clean_cid = customer_id.replace("-", "").replace(" ", "")
    url     = f"{_GOOGLE_ADS_API}/customers/{clean_cid}/googleAds:search"
    headers = _google_headers(token, manager_id)

    # Step 1: billing_setup → billing_model
    try:
        resp = httpx.post(url, json={"query": _BILLING_SETUP_QUERY}, headers=headers, timeout=15.0)
    except Exception as exc:
        return _blocked(f"billing_setup network error: {exc}", threshold_low, currency)

    if resp.status_code in (401, 403):
        return _denied(resp.text[:300], threshold_low, currency, {"http_status": resp.status_code})
    if resp.status_code != 200:
        return _blocked(f"billing_setup HTTP {resp.status_code}: {resp.text[:200]}", threshold_low, currency,
                        {"http_status": resp.status_code})

    billing_rows = resp.json().get("results") or []
    if not billing_rows:
        return BalanceSnapshot(
            billing_model=None, collection_status="PASS",
            balance=None, balance_status="NO_DATA",
            avg_daily_spend=avg_daily_spend, spend_avg_window_days=spend_avg_window_days,
            spend_data_days=spend_data_days, estimated_days_remaining=None,
            threshold_low=threshold_low,
            threshold_critical=threshold_low * _CRITICAL_FRACTION if threshold_low else None,
            currency=currency, raw={"billing_setup_rows": 0},
            error="No approved billing_setup found; account may be inactive",
        )

    billing_row     = billing_rows[0].get("billingSetup", {})
    payment_setting = billing_row.get("paymentSetting", "")
    billing_model   = _PAYMENT_SETTING_LABEL.get(payment_setting, f"unknown:{payment_setting}")

    if payment_setting != "PAY_MANUALLY":
        return BalanceSnapshot(
            billing_model=billing_model, collection_status="PASS",
            balance=None, balance_status="NOT_SUPPORTED",
            avg_daily_spend=None, spend_avg_window_days=None,
            spend_data_days=None, estimated_days_remaining=None,
            threshold_low=threshold_low,
            threshold_critical=threshold_low * _CRITICAL_FRACTION if threshold_low else None,
            currency=currency,
            raw={"payment_setting": payment_setting, "billing_setup_status": billing_row.get("status")},
            error=f"Account billing_model={billing_model}; balance field only available for prepaid (PAY_MANUALLY)",
        )

    # Step 2: account_budget → remaining
    try:
        resp2 = httpx.post(url, json={"query": _ACCOUNT_BUDGET_QUERY}, headers=headers, timeout=15.0)
    except Exception as exc:
        return _blocked(f"account_budget network error: {exc}", threshold_low, currency)

    if resp2.status_code != 200:
        return _blocked(f"account_budget HTTP {resp2.status_code}: {resp2.text[:200]}", threshold_low, currency,
                        {"http_status": resp2.status_code})

    budget_rows = resp2.json().get("results") or []
    if not budget_rows:
        return BalanceSnapshot(
            billing_model=billing_model, collection_status="PASS",
            balance=None, balance_status="NO_DATA",
            avg_daily_spend=avg_daily_spend, spend_avg_window_days=spend_avg_window_days,
            spend_data_days=spend_data_days, estimated_days_remaining=None,
            threshold_low=threshold_low,
            threshold_critical=threshold_low * _CRITICAL_FRACTION if threshold_low else None,
            currency=currency,
            raw={"billing_setup": billing_row, "account_budget_rows": 0},
            error="billing_model=prepaid confirmed; no approved account_budget row found",
        )

    budget          = budget_rows[0].get("accountBudget", {})
    adjusted_micros = int(budget.get("adjustedSpendingLimitMicros") or 0)
    served_micros   = int(budget.get("amountServedMicros") or 0)
    remaining_micros = adjusted_micros - served_micros
    balance          = round(remaining_micros / 1_000_000, 2)

    estimated_days = _compute_days(balance, avg_daily_spend)
    status         = _classify(balance, estimated_days, threshold_low)

    raw_out: dict[str, Any] = {
        "billing_model":                   billing_model,
        "payment_setting":                 payment_setting,
        "account_budget_id":               budget.get("id"),
        "account_budget_status":           budget.get("status"),
        "adjusted_spending_limit_micros":  adjusted_micros,
        "approved_spending_limit_micros":  int(budget.get("approvedSpendingLimitMicros") or 0),
        "total_adjustments_micros":        int(budget.get("totalAdjustmentsMicros") or 0),
        "amount_served_micros":            served_micros,
        "remaining_micros":                remaining_micros,
        # Signal description
        "balance_signal": (
            "account_budget.adjusted_spending_limit_micros - amount_served_micros. "
            "Official closest proxy; Google Ads API has no single 'account_balance' field."
        ),
    }

    return BalanceSnapshot(
        billing_model=billing_model, collection_status="PASS",
        balance=balance, balance_status=status,
        avg_daily_spend=avg_daily_spend,
        spend_avg_window_days=spend_avg_window_days,
        spend_data_days=spend_data_days,
        estimated_days_remaining=estimated_days,
        threshold_low=threshold_low,
        threshold_critical=threshold_low * _CRITICAL_FRACTION if threshold_low else None,
        currency=currency,
        raw=raw_out,
        error=None,
    )


# ── Meta Ads ───────────────────────────────────────────────────────────────────

def collect_meta_balance(
    ad_account_id: str,
    access_token: str,
    threshold_low: Optional[float],
    currency: Optional[str] = None,
    avg_daily_spend: Optional[float] = None,
    spend_avg_window_days: Optional[int] = None,
    spend_data_days: Optional[int] = None,
) -> BalanceSnapshot:
    """
    Queries Meta Graph API for the ad account balance field.

    Official signal: `balance` field on the AdAccount object.
    Value is in the account's minor currency unit (centavos for BRL, cents for USD).
    Divide by 100 to get major unit.

    NOT used: spend_cap (is a limit, not a balance), amount_spent.
    """
    clean_id = ad_account_id.removeprefix("act_")
    url = f"{_META_API_BASE}/act_{clean_id}"

    try:
        resp = httpx.get(
            url,
            params={
                "fields":       "balance,currency,account_status",
                "access_token": access_token,
            },
            timeout=15.0,
        )
    except Exception as exc:
        return _blocked(f"Meta API network error: {exc}", threshold_low, currency)

    if resp.status_code in (401, 403):
        return _denied(resp.text[:300], threshold_low, currency, {"http_status": resp.status_code})
    if resp.status_code != 200:
        return _blocked(f"Meta API HTTP {resp.status_code}: {resp.text[:200]}", threshold_low, currency,
                        {"http_status": resp.status_code})

    data = resp.json()
    if "error" in data:
        err  = data["error"]
        code = err.get("code", 0)
        if code in (100, 200, 190, 10):
            return _denied(err.get("message", str(err)), threshold_low, currency, {"error": err})
        return _blocked(err.get("message", str(err)), threshold_low, currency, {"error": err})

    raw_balance = data.get("balance")
    account_currency = data.get("currency", currency)
    account_status   = data.get("account_status")

    if raw_balance is None:
        return BalanceSnapshot(
            billing_model="prepaid", collection_status="PASS",
            balance=None, balance_status="NO_DATA",
            avg_daily_spend=avg_daily_spend, spend_avg_window_days=spend_avg_window_days,
            spend_data_days=spend_data_days, estimated_days_remaining=None,
            threshold_low=threshold_low,
            threshold_critical=threshold_low * _CRITICAL_FRACTION if threshold_low else None,
            currency=account_currency,
            raw={"account_status": account_status, "balance_field_present": False},
            error="Meta `balance` field absent — account may not be prepaid or token lacks permissions",
        )

    # Meta balance is in minor currency unit (centavos for BRL, cents for USD)
    balance        = round(int(raw_balance) / 100, 2)
    estimated_days = _compute_days(balance, avg_daily_spend)
    status         = _classify(balance, estimated_days, threshold_low)

    raw_out = {
        "billing_model":        "prepaid",
        "balance_minor_units":  raw_balance,
        "account_status":       account_status,
        "currency":             account_currency,
        "balance_signal": (
            "AdAccount.balance (minor currency units ÷ 100). "
            "Official field: direct prepaid account balance."
        ),
    }

    return BalanceSnapshot(
        billing_model="prepaid", collection_status="PASS",
        balance=balance, balance_status=status,
        avg_daily_spend=avg_daily_spend,
        spend_avg_window_days=spend_avg_window_days,
        spend_data_days=spend_data_days,
        estimated_days_remaining=estimated_days,
        threshold_low=threshold_low,
        threshold_critical=threshold_low * _CRITICAL_FRACTION if threshold_low else None,
        currency=account_currency,
        raw=raw_out,
        error=None,
    )
