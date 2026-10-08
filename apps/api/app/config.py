from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="allow")

    # ── App ──────────────────────────────────────────────────────────────
    APP_NAME: str = "Ecommerce Tracking API"
    APP_VERSION: str = "2.3.1"
    DEBUG: bool = False
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # ── Supabase ─────────────────────────────────────────────────────────
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    # JWT secret for validating Supabase Auth tokens (client_viewer auth).
    # Found in: Supabase dashboard → Settings → API → JWT Secret
    SUPABASE_JWT_SECRET: str = ""
    # Anon key (read-only, public). Required for client-side Supabase operations.
    # Scheduled for rotation in key migration plan (docs/etapa-2-key-migration-plan.md).
    SUPABASE_ANON_KEY: str = ""

    # ── Segurança / LGPD ─────────────────────────────────────────────────
    # Chave Fernet (base64) para encriptar credenciais em repouso. Gerar com:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    CREDENTIALS_KEY: str = ""
    # Janela de retenção dos eventos brutos (tracking_events), em dias.
    EVENT_RETENTION_DAYS: int = 90

    # ── Webhook secrets ───────────────────────────────────────────────────
    # Fallback secret used when no per-client secret is found in the database.
    DEFAULT_WEBHOOK_SECRET: str = ""

    # ── Agency OS / Hermes ingest ─────────────────────────────────────────────
    # Bearer token that Hermes uses to POST report contracts to this backend.
    # Server-side only — never expose this key to the client.
    AGENCY_OS_INGEST_KEY: str = ""

    # ── Agency API v1 — per-scope service tokens ─────────────────────────────
    # Configure in Railway / env. Never commit values.
    # Etapa 2 will replace these with full OAuth + Supabase role binding.
    AGENCY_API_ADMIN_KEY:    str = ""   # agency_admin  — full read/write
    AGENCY_API_HERMES_KEY:   str = ""   # hermes_service — reads truth_*/core_*; writes intel_*
    AGENCY_API_PLATFORM_KEY: str = ""   # platform_web  — agency view reads; action-events writes
    # AGENCY_API_CLIENT_KEY is DEPRECATED — client_viewer uses Supabase JWT (SUPABASE_JWT_SECRET).
    # Kept for Railway env-var compatibility only; auth module does NOT use it.
    AGENCY_API_CLIENT_KEY:   str = ""

    # ── Anthropic ────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

    # ── CORS ─────────────────────────────────────────────────────────────
    CORS_ORIGINS: str = "*"

    # ── Resend (email preferido — fallback: SMTP) ─────────────────────────
    RESEND_API_KEY: str = ""
    RESEND_FROM:    str = ""  # e.g. relatorios@noroia.com

    # ── SMTP (relatório semanal — fallback se Resend não configurado) ─────
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_FROM: str = ""  # defaults to SMTP_USER if empty

    # ── Branding ─────────────────────────────────────────────────────────
    AGENCY_NAME:     str = "Norolabs"
    AGENCY_LOGO_URL: str = ""   # ex: https://cdn.norolabs.com/logo-white.png
    AGENCY_WEBSITE:  str = "https://norolabs.com"
    DASHBOARD_URL:   str = "https://ecommerce-tracking-ia-dash.vercel.app"

    # ── Notificação da agência ────────────────────────────────────────────
    # Destino interno para relatórios retidos pelo gate de negatividade
    # (mês ruim não vai direto pro cliente — a agência revisa antes).
    AGENCY_NOTIFY_EMAIL: str = ""
    # WhatsApp da agência para alertas críticos (ex: 5511999999999)
    AGENCY_WHATSAPP: str = ""

    # ── Evolution API (WhatsApp) ─────────────────────────────────────────
    # Self-hosted Evolution API para envio de mensagens WhatsApp
    EVOLUTION_API_URL:      str = ""   # ex: https://evolution.suaempresa.com
    EVOLUTION_API_KEY:      str = ""   # global apikey configurada no Evolution
    EVOLUTION_INSTANCE:     str = ""   # nome da instância (ex: noroia-principal)
    # Mínima severidade para disparar WhatsApp (critical | warning | all)
    EVOLUTION_MIN_SEVERITY: str = "critical"

    # ── Meta CAPI ────────────────────────────────────────────────────────────
    # Código de teste do Events Manager — remover após validação
    META_TEST_EVENT_CODE: str = ""

    # ── Meta OAuth (Facebook Login for Business) ─────────────────────────────
    # Compartilhado entre todos os clientes — registrado uma vez no Meta App.
    META_APP_ID:     str = ""
    META_APP_SECRET: str = ""

    # ── Google Ads Conversion API ─────────────────────────────────────────
    # Agency-level credentials — shared across all clients
    GOOGLE_ADS_DEVELOPER_TOKEN:   str = ""
    GOOGLE_ADS_OAUTH_CLIENT_ID:   str = ""
    GOOGLE_ADS_OAUTH_CLIENT_SECRET: str = ""
    GOOGLE_ADS_REFRESH_TOKEN:     str = ""  # agency MCC refresh token
    GOOGLE_ADS_MANAGER_ID:        str = ""  # MCC account ID (optional)

    # ── 1Password Connect (SecretRef provider — Etapa 5 wiring) ─────────────
    # Deploy a 1Password Connect server, then set these two vars.
    # Used by secret_ref.resolve_secret() when provider = ONEPASSWORD.
    # Docs: https://developer.1password.com/docs/connect
    OP_CONNECT_URL:   str = ""  # e.g. https://op-connect.internal.norolabs.com
    OP_CONNECT_TOKEN: str = ""  # Connect API token (service account, server-side only)

    # ── RAG / Content Production ─────────────────────────────────────────
    VOYAGE_API_KEY: str = ""  # Voyage AI (embeddings)

    # ── Technical SEO ─────────────────────────────────────────────────────
    PAGESPEED_API_KEY: str = ""  # Google PageSpeed Insights API key (opcional)

    # ── DataForSEO ────────────────────────────────────────────────────────
    DATAFORSEO_LOGIN:    str = ""  # API login (email)
    DATAFORSEO_PASSWORD: str = ""  # API password

    # ── Telegram (agency ops channel) ─────────────────────────────────────────
    # Single agency-level bot → one ops channel for all operational alerts.
    # Set in Railway. Find chat_id via @userinfobot or getUpdates.
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID:   str = ""


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
