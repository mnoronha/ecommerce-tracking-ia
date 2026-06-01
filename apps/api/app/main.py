import logging
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from .config import settings
from .limiter import limiter
from .routers import alerts as alerts_router, attribution, audiences, cname, cogs, creatives, diagnostics, ecommerce_webhooks, google_ads_dashboard, insights, integrations, journey, klaviyo_webhook, live, meta_ads, pacing, pixel, setup
from .services import ai_analyst, alert_engine, alerts, anomalies, capi_retry, cart_abandonment, creative_intelligence, creative_sync, health_monitor, integrations_health, ltv_predictor, meta_attribution_sync, meta_audiences, meta_token_health, reports, sessionization, spend_sync

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    debug=settings.DEBUG,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_scheduler = BackgroundScheduler()
_scheduler.add_job(alerts.run_conversion_check, "interval", hours=6, id="conversion_alerts")
_scheduler.add_job(
    reports.send_weekly_reports,
    "cron",
    day_of_week="mon",
    hour=11,  # 11 UTC = 08:00 BRT
    minute=0,
    id="weekly_reports",
)
_scheduler.add_job(
    reports.send_monthly_reports,
    "cron",
    day=1,
    hour=11,  # 11 UTC = 08:00 BRT — 1º dia do mês, sobre o mês anterior fechado
    minute=0,
    id="monthly_reports",
)
_scheduler.add_job(
    meta_audiences.run_audience_sync_all_clients,
    "interval",
    hours=6,
    id="audience_sync",
)
_scheduler.add_job(
    meta_token_health.run_token_health_check,
    "interval",
    hours=6,
    id="meta_token_health",
)
_scheduler.add_job(
    capi_retry.retry_failed_capi,
    "interval",
    minutes=30,
    id="capi_retry",
)
_scheduler.add_job(
    anomalies.run_daily_anomaly_check,
    "cron",
    hour=11,  # 11 UTC = 08:00 BRT
    minute=0,
    id="anomalies_daily",
)
_scheduler.add_job(
    anomalies.run_capi_health_check_all_clients,
    "interval",
    hours=1,
    id="capi_health_hourly",
)
_scheduler.add_job(
    integrations_health.run_hourly_for_all_clients,
    "interval",
    hours=1,
    id="integrations_health_hourly",
)
_scheduler.add_job(
    meta_attribution_sync.run_daily_sync_all_clients,
    "cron",
    hour=9,   # 09 UTC = 06:00 BRT — early so dashboards are fresh by morning
    minute=0,
    id="meta_attribution_sync",
)
_scheduler.add_job(
    cart_abandonment.run_hourly,
    "interval",
    hours=1,
    id="cart_abandonment",
)
_scheduler.add_job(
    sessionization.run_daily,
    "cron",
    hour=4,   # 04 UTC = 01:00 BRT — well after the previous UTC day closed
    minute=0,
    id="sessionization",
)
_scheduler.add_job(
    ltv_predictor.run_daily_for_all_clients,
    "cron",
    hour=5,   # 05 UTC = 02:00 BRT — after sessionization, before attribution sync
    minute=0,
    id="ltv_stats_refresh",
)
_scheduler.add_job(
    creative_sync.run_daily_for_all_clients,
    "cron",
    hour=10,  # 10 UTC = 07:00 BRT — after meta_attribution_sync (09 UTC)
    minute=0,
    id="creative_sync",
)
_scheduler.add_job(
    creative_intelligence.run_weekly_for_all_clients,
    "cron",
    day_of_week="tue",
    hour=11,  # 11 UTC Tue = 08:00 BRT — fresh report for Tuesday standups
    minute=0,
    id="creative_intelligence",
)
_scheduler.add_job(
    alert_engine.run_alert_engine,
    "interval",
    minutes=30,
    id="alert_engine",
)
_scheduler.add_job(
    capi_retry.retry_failed_tiktok,
    "interval",
    minutes=30,
    id="tiktok_retry",
)
_scheduler.add_job(
    spend_sync.run_daily_spend_sync,
    "cron",
    hour=6,
    minute=0,
    id="spend_sync_daily",
)
_scheduler.add_job(
    ai_analyst.run_daily_insights_all_clients,
    "cron",
    hour=7,
    minute=30,  # 07:30 UTC = 04:30 BRT — antes do início do dia comercial
    id="daily_ai_insights",
)
_scheduler.add_job(
    health_monitor.run_daily_health_check_safe,
    "cron",
    hour=12,
    minute=30,  # 12:30 UTC = 09:30 BRT — verifica o dia anterior completo
    id="daily_health_monitor",
)


@app.on_event("startup")
def _start_scheduler() -> None:
    _scheduler.start()
    logger.info("APScheduler started — conversion alerts every 6h")


@app.on_event("shutdown")
def _stop_scheduler() -> None:
    _scheduler.shutdown(wait=False)

# ── CORS ──────────────────────────────────────────────────────────────────────
# First-party cookie persistence needs credentialed cross-origin requests
# (store page www.cliente.com → tracker track.cliente.com). With credentials,
# the spec forbids "*" — the middleware must reflect the specific Origin. We
# reflect any https origin via regex (the pixel endpoints are public and carry
# no cookie-based auth, so reflecting origins is low-risk). Explicit origins
# from CORS_ORIGINS are still honored.
if settings.CORS_ORIGINS != "*":
    explicit_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=explicit_origins,
        allow_origin_regex=r"https://[^/]+",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://[^/]+",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ── Static files (pixel tracker.js) ──────────────────────────────────────────
_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(ecommerce_webhooks.router)
app.include_router(pixel.router)
app.include_router(insights.router)
app.include_router(meta_ads.router)
app.include_router(audiences.router)
app.include_router(setup.router)
app.include_router(cname.router)
app.include_router(attribution.router)
app.include_router(live.router)
app.include_router(cogs.router)
app.include_router(pacing.router)
app.include_router(journey.router)
app.include_router(creatives.router)
app.include_router(integrations.router)
app.include_router(alerts_router.router)
app.include_router(diagnostics.router)
app.include_router(google_ads_dashboard.router)
app.include_router(klaviyo_webhook.router)


# ── CNAME verify echo (root-level — called via customer's CNAME) ─────────────
@app.get("/_verify/{secret}", include_in_schema=False)
async def cname_verify_echo(secret: str):
    """
    Echoes the secret in plain text. Used to verify that a customer's
    CNAME (track.cliente.com) is correctly routing to our infrastructure.
    """
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(secret)


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["meta"])
async def health_check():
    from .database import get_supabase
    db_status = "ok"
    db_error = None
    try:
        sb = get_supabase()
        sb.table("clients").select("id").limit(1).execute()
    except Exception as e:
        db_status = "error"
        db_error = str(e)[:200]
    return {
        "status": "ok",
        "version": settings.APP_VERSION,
        "db": db_status,
        "db_error": db_error,
        "supabase_url": settings.SUPABASE_URL[:40] + "..." if settings.SUPABASE_URL else "NOT SET",
        "service_key_set": bool(settings.SUPABASE_SERVICE_KEY),
    }
