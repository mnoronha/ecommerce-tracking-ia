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


_BREAKDOWN_FIELDS = {
    "age":       "age",
    "gender":    "gender",
    "device":    "impression_device",
    "placement": "publisher_platform",
}


def fetch_campaign_breakdowns(
    account_id: str,
    access_token: str,
    since: str,
    until: str,
    breakdown: str = "age",
) -> list[dict]:
    """
    Busca insights de campanhas segmentados por demographic/device.
    breakdown: "age" | "gender" | "device" | "placement"
    """
    bd_field = _BREAKDOWN_FIELDS.get(breakdown, "age")
    clean_id  = account_id.removeprefix("act_")
    try:
        resp = httpx.get(
            _ADS_URL.format(account_id=clean_id),
            params={
                "fields":       "campaign_name,campaign_id,spend,impressions,clicks,reach,actions,action_values",
                "time_range":   f'{{"since":"{since}","until":"{until}"}}',
                "level":        "campaign",
                "breakdowns":   bd_field,
                "access_token": access_token,
            },
            timeout=20.0,
        )
        if resp.status_code != 200:
            logger.warning("meta_ads breakdowns HTTP %s: %s", resp.status_code, resp.text[:400])
            return []

        result = []
        for row in resp.json().get("data", []):
            spend = float(row.get("spend") or 0)
            impr  = int(row.get("impressions") or 0)
            clks  = int(row.get("clicks") or 0)
            purch = _pick_action(row.get("actions") or [], (
                "purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase",
            ))
            rev   = _pick_action(row.get("action_values") or [], (
                "purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase",
            ))
            result.append({
                "campaign_id":    row.get("campaign_id", ""),
                "campaign_name":  row.get("campaign_name", ""),
                "breakdown_type": breakdown,
                "breakdown_val":  row.get(bd_field, "unknown"),
                "spend":          round(spend, 2),
                "impressions":    impr,
                "clicks":         clks,
                "reach":          int(row.get("reach") or 0),
                "purchases":      int(purch or 0),
                "revenue":        round(float(rev or 0), 2),
                "ctr":            round(clks / impr * 100, 2) if impr > 0 else None,
                "cpa":            round(spend / float(purch), 2) if purch and purch > 0 and spend > 0 else None,
                "roas":           round(float(rev or 0) / spend, 2) if spend > 0 and rev else None,
            })
        logger.info("meta_ads breakdowns: %d rows for act_%s breakdown=%s", len(result), clean_id, breakdown)
        return result
    except httpx.TimeoutException:
        logger.warning("meta_ads breakdowns timeout for act_%s", clean_id)
        return []
    except Exception as exc:
        logger.error("meta_ads breakdowns error: %s", exc)
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
