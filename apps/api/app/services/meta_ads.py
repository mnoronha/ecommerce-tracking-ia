"""
Meta Ads Insights API — busca gasto por campanha para cálculo de ROAS.

Docs: https://developers.facebook.com/docs/marketing-api/insights
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_ADS_URL     = "https://graph.facebook.com/v19.0/act_{account_id}/insights"
_ACCOUNT_URL = "https://graph.facebook.com/v19.0/act_{account_id}"


def _pick_action(actions: list[dict], action_types: tuple[str, ...]) -> Optional[float]:
    """
    Meta returns metrics like purchases / revenue / cpa as arrays of
    {action_type, value}. Pick the first matching action_type. We try
    several variants because Meta uses different names depending on the
    pixel/event setup (e.g. server-side "purchase" vs browser-side
    "offsite_conversion.fb_pixel_purchase").
    """
    if not actions:
        return None
    for action_type in action_types:
        for entry in actions:
            if entry.get("action_type") == action_type:
                try:
                    return float(entry.get("value") or 0)
                except (TypeError, ValueError):
                    return None
    return None


def fetch_campaign_insights(
    account_id: str,
    access_token: str,
    since: str,
    until: str,
) -> list[dict]:
    """
    Busca métricas de campanhas no período (spend, impressions, clicks, reach).
    account_id: sem o prefixo "act_" — adicionado internamente.
    since/until: datas no formato YYYY-MM-DD (ambas inclusivas).
    Retorna lista de dicts ou [] em caso de erro.
    """
    # Remove prefixo "act_" se o usuário já incluiu
    clean_id = account_id.removeprefix("act_")

    try:
        resp = httpx.get(
            _ADS_URL.format(account_id=clean_id),
            params={
                # actions + cost_per_action_type lets us extract Meta-reported
                # purchases & CPA. We compare those against our server-side
                # numbers in the dashboard ("Meta diz X, na verdade é Y").
                "fields": (
                    "campaign_name,campaign_id,spend,impressions,clicks,reach,"
                    "cpc,cpm,frequency,actions,cost_per_action_type,"
                    "action_values"
                ),
                "time_range": f'{{"since":"{since}","until":"{until}"}}',
                "level":      "campaign",
                "access_token": access_token,
            },
            timeout=15.0,
        )
        if resp.status_code != 200:
            logger.warning("meta_ads insights HTTP %s: %s", resp.status_code, resp.text[:400])
            return []

        rows = resp.json().get("data", [])
        result = []
        for row in rows:
            spend = float(row.get("spend") or 0)

            # Extract purchase count + revenue + CPA from the actions arrays
            # Meta returns several action_type variants — try them in order
            meta_purchases = _pick_action(row.get("actions") or [], (
                "purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase",
            ))
            meta_revenue = _pick_action(row.get("action_values") or [], (
                "purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase",
            ))
            meta_cpa = _pick_action(row.get("cost_per_action_type") or [], (
                "purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase",
            ))

            result.append({
                "campaign_id":     row.get("campaign_id", ""),
                "campaign_name":   row.get("campaign_name", ""),
                "spend":           spend,
                "impressions":     int(row.get("impressions") or 0),
                "clicks":          int(row.get("clicks") or 0),
                "reach":           int(row.get("reach") or 0),
                "cpc":             float(row["cpc"])   if row.get("cpc")   else None,
                "cpm":             float(row["cpm"])   if row.get("cpm")   else None,
                "frequency":       float(row["frequency"]) if row.get("frequency") else None,
                "meta_purchases":  int(meta_purchases or 0),
                "meta_revenue":    float(meta_revenue or 0),
                "meta_cpa":        float(meta_cpa) if meta_cpa else None,
            })
        logger.info("meta_ads: %d campaigns fetched for act_%s", len(result), clean_id)
        return result

    except httpx.TimeoutException:
        logger.warning("meta_ads insights timeout for act_%s", clean_id)
        return []
    except Exception as exc:
        logger.error("meta_ads insights error: %s", exc)
        return []


def fetch_account_balance(account_id: str, access_token: str) -> dict:
    """
    Busca saldo da conta Meta Ads (relevante para contas pre-pagas).
    Retorna dict com: balance (float BRL), currency, is_prepaid, raw.
    """
    clean_id = account_id.removeprefix("act_")
    try:
        resp = httpx.get(
            _ACCOUNT_URL.format(account_id=clean_id),
            params={
                "fields": "balance,currency,funding_source_details,spend_cap,amount_spent",
                "access_token": access_token,
            },
            timeout=10.0,
        )
        if resp.status_code != 200:
            logger.warning("meta_ads balance HTTP %s: %s", resp.status_code, resp.text[:200])
            return {"error": f"HTTP {resp.status_code}"}
        data = resp.json()
        balance_raw   = float(data.get("balance") or 0) / 100.0  # Meta retorna em centavos
        currency      = data.get("currency", "BRL")
        funding       = data.get("funding_source_details") or {}
        is_prepaid    = funding.get("type") in ("PREPAY_ACCOUNT", 1, "1")
        return {
            "balance":    balance_raw,
            "currency":   currency,
            "is_prepaid": is_prepaid,
            "raw":        data,
        }
    except httpx.TimeoutException:
        return {"error": "timeout"}
    except Exception as exc:
        logger.error("meta_ads balance error: %s", exc)
        return {"error": str(exc)}
