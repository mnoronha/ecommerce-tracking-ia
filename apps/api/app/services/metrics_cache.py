"""
Cache de métricas externas por cliente.

Popula `client_metrics_cache` com dados de conversão para clientes que não
integram pedidos via webhook (ex: Enutri, Colab55).

Hierarquia de fontes:
  1. GA4 (ga4_reporting_enabled=true + ga4_property_id) — todos os canais
  2. Google Ads API (fallback quando GA4 não está disponível) — só Google Ads

Roda diariamente via APScheduler (06:30 UTC, após spend_sync).
"""

import logging
from datetime import date, datetime, timedelta, timezone

from ..database import get_supabase
from ..services import crypto

logger = logging.getLogger(__name__)


def refresh_ga4(client: dict) -> bool:
    """Busca last-30d transactions/revenue do GA4 e salva no cache (todos os canais)."""
    from ..services import ga4_reporting

    creds = crypto.decrypt_client_secrets(client)
    if not creds.get("ga4_reporting_enabled") or not creds.get("ga4_property_id"):
        return False
    if not creds.get("google_ads_refresh_token"):
        return False

    today = date.today()
    start = today - timedelta(days=29)

    result = ga4_reporting.fetch_overview(
        property_id   = creds["ga4_property_id"],
        refresh_token = creds["google_ads_refresh_token"],
        start_date    = start,
        end_date      = today,
    )

    if "error" in result:
        logger.warning("metrics_cache: ga4 failed for %s: %s",
                       creds.get("name", creds.get("id")), result["error"])
        return False

    summary    = result.get("summary", {})
    total_conv = float(summary.get("conversions", 0))
    total_rev  = float(summary.get("revenue", 0))

    if total_conv == 0 and total_rev == 0:
        return False

    sb = get_supabase()
    sb.table("client_metrics_cache").upsert(
        {
            "client_id":         creds["id"],
            "channel":           "ga4",
            "orders":            round(total_conv),
            "revenue":           round(total_rev, 2),
            "conversions":       total_conv,
            "conversions_value": total_rev,
            "refreshed_at":      datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="client_id,channel",
    ).execute()

    logger.info("metrics_cache: ga4 updated — %s: %d conv / R$%.2f",
                creds.get("name", creds.get("id")), round(total_conv), total_rev)
    return True


def refresh_google_ads(client: dict) -> bool:
    """Fallback: busca last-30d conversions da Google Ads API (só Google Ads)."""
    from ..services import google_ads

    creds = crypto.decrypt_client_secrets(client)
    if not (creds.get("google_ads_customer_id") and creds.get("google_ads_refresh_token")):
        return False

    today = date.today()
    start = today - timedelta(days=29)

    try:
        campaigns = google_ads.fetch_campaign_insights(
            customer_id   = creds["google_ads_customer_id"],
            refresh_token = creds["google_ads_refresh_token"],
            start_date    = str(start),
            end_date      = str(today),
            manager_id    = creds.get("google_ads_login_customer_id"),
            limit         = 500,
        )
    except Exception as exc:
        logger.warning("metrics_cache: google_ads failed for %s: %s",
                       creds.get("name", creds.get("id")), exc)
        return False

    if not campaigns:
        return False

    total_conv = sum(float(c.get("conversions") or 0) for c in campaigns)
    total_rev  = sum(float(c.get("conversions_value") or 0) for c in campaigns)

    if total_conv == 0 and total_rev == 0:
        return False

    sb = get_supabase()
    sb.table("client_metrics_cache").upsert(
        {
            "client_id":         creds["id"],
            "channel":           "google_ads",
            "orders":            round(total_conv),
            "revenue":           round(total_rev, 2),
            "conversions":       total_conv,
            "conversions_value": total_rev,
            "refreshed_at":      datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="client_id,channel",
    ).execute()

    logger.info("metrics_cache: google_ads updated — %s: %d conv / R$%.2f",
                creds.get("name", creds.get("id")), round(total_conv), total_rev)
    return True


def run_daily_metrics_cache() -> None:
    """
    Atualiza cache de métricas externas para clientes elegíveis:
      - ga4_reporting_enabled=true  → usa GA4 (todos os canais)
      - sem GA4 mas com Google Ads  → usa Google Ads API (fallback)

    O gatilho principal é o toggle ga4_reporting_enabled nas configurações
    do cliente. Clientes com integração completa (pedidos via webhook) não
    precisam do cache, mas a RPC do dashboard usa orders reais quando existem,
    então popular o cache para eles é inofensivo.
    """
    sb = get_supabase()

    # Elegíveis: GA4 ativado OU sem GA4 mas com Google Ads (para Colab55 etc.)
    all_clients = (
        sb.table("clients")
        .select(
            "id, name, ga4_property_id, ga4_reporting_enabled, "
            "google_ads_customer_id, google_ads_refresh_token, "
            "google_ads_login_customer_id"
        )
        .eq("is_active", True)
        .execute()
    ).data or []

    ok = fail = skip = 0
    for client in all_clients:
        ga4_ok  = client.get("ga4_reporting_enabled") and client.get("ga4_property_id")
        gads_ok = bool(client.get("google_ads_customer_id"))

        if not ga4_ok and not gads_ok:
            skip += 1
            continue

        # GA4 como fonte primária (todos os canais)
        if ga4_ok:
            if refresh_ga4(client):
                ok += 1
                continue

        # Google Ads como fallback (clientes sem GA4)
        if gads_ok:
            if refresh_google_ads(client):
                ok += 1
                continue
            fail += 1
        else:
            skip += 1

    logger.info("metrics_cache: done — %d ok / %d fail / %d skip", ok, fail, skip)
