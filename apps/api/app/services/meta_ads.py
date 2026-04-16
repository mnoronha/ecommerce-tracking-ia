"""
Meta Ads Insights API — busca gasto por campanha para cálculo de ROAS.

Docs: https://developers.facebook.com/docs/marketing-api/insights
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_ADS_URL = "https://graph.facebook.com/v19.0/act_{account_id}/insights"


def fetch_campaign_insights(
    account_id: str,
    access_token: str,
    days: int = 30,
) -> list[dict]:
    """
    Busca métricas de campanhas no período (spend, impressions, clicks, reach).
    account_id: sem o prefixo "act_" — adicionado internamente.
    Retorna lista de dicts ou [] em caso de erro.
    """
    # Remove prefixo "act_" se o usuário já incluiu
    clean_id = account_id.removeprefix("act_")

    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    until = datetime.utcnow().strftime("%Y-%m-%d")

    try:
        resp = httpx.get(
            _ADS_URL.format(account_id=clean_id),
            params={
                "fields":     "campaign_name,campaign_id,spend,impressions,clicks,reach,cpc,cpm,frequency",
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
            result.append({
                "campaign_id":   row.get("campaign_id", ""),
                "campaign_name": row.get("campaign_name", ""),
                "spend":         spend,
                "impressions":   int(row.get("impressions") or 0),
                "clicks":        int(row.get("clicks") or 0),
                "reach":         int(row.get("reach") or 0),
                "cpc":           float(row["cpc"])   if row.get("cpc")   else None,
                "cpm":           float(row["cpm"])   if row.get("cpm")   else None,
                "frequency":     float(row["frequency"]) if row.get("frequency") else None,
            })
        logger.info("meta_ads: %d campaigns fetched for act_%s", len(result), clean_id)
        return result

    except httpx.TimeoutException:
        logger.warning("meta_ads insights timeout for act_%s", clean_id)
        return []
    except Exception as exc:
        logger.error("meta_ads insights error: %s", exc)
        return []
