-- =============================================================================
-- Migration: 007_schema_sync
-- Sincroniza schema com colunas/tabelas referenciadas no código mas que foram
-- criadas manualmente no Supabase ao longo do desenvolvimento e nunca foram
-- versionadas. Sem isso, qualquer ambiente novo (staging, novo cliente,
-- recriação) vai quebrar em silêncio.
-- =============================================================================

-- ── clients: GA4 credentials per-cliente ──────────────────────────────────────
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS ga4_measurement_id TEXT;
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS ga4_api_secret     TEXT;

-- ── visitors: scoring + multi-touch attribution ───────────────────────────────
-- retargeting_score: pontuação de intenção de compra (drop ao converter)
-- last_cart_at: timestamp do último add_to_cart/cart_updated
-- last_purchase_at: timestamp da última conversão (usado em audience inactive)
-- utm_history: JSONB com últimos 20 touchpoints para atribuição multi-toque
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS retargeting_score INT
    DEFAULT 0 CHECK (retargeting_score BETWEEN 0 AND 100);
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS last_cart_at      TIMESTAMPTZ;
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS last_purchase_at  TIMESTAMPTZ;
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS utm_history       JSONB DEFAULT '[]'::jsonb;

-- Índices para queries frequentes (audience sync e dashboard)
CREATE INDEX IF NOT EXISTS idx_visitors_retargeting
    ON public.visitors (client_id, retargeting_score)
    WHERE retargeting_score > 0;
CREATE INDEX IF NOT EXISTS idx_visitors_last_purchase
    ON public.visitors (client_id, last_purchase_at DESC)
    WHERE last_purchase_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_visitors_last_seen
    ON public.visitors (client_id, last_seen_at DESC);

-- ── visitors: last_seen_at (referenciada como first_seen_at/last_seen_at) ────
-- Migration 001 já cria last_seen_at — apenas garantir que existe.
-- (idempotente: ADD COLUMN IF NOT EXISTS)
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ DEFAULT now();

-- ── webhook_deliveries: log de auditoria de webhooks recebidos ───────────────
-- Referenciada em writer.py:writer.write_webhook_delivery() mas nunca criada.
CREATE TABLE IF NOT EXISTS public.webhook_deliveries (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id           UUID        REFERENCES public.clients(id) ON DELETE CASCADE,
    platform            TEXT        NOT NULL,
    platform_event_id   TEXT,
    event_topic         TEXT,
    payload             JSONB,
    headers             JSONB,
    signature_valid     BOOLEAN     DEFAULT TRUE,
    status              TEXT        DEFAULT 'processed'
        CHECK (status IN ('processed','error','skipped','pending')),
    error_message       TEXT,
    response_code       INT,
    result_order_id     UUID        REFERENCES public.orders(id) ON DELETE SET NULL,
    result_visitor_id   UUID        REFERENCES public.visitors(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE public.webhook_deliveries ENABLE ROW LEVEL SECURITY;

-- Leitura pelo dashboard (filtrado pelo cliente do usuário)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename  = 'webhook_deliveries'
      AND policyname = 'webhook_deliveries_read'
  ) THEN
    CREATE POLICY webhook_deliveries_read ON public.webhook_deliveries
      FOR SELECT USING (client_id IN (SELECT public.get_user_client_ids()));
  END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_client
    ON public.webhook_deliveries (client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_status
    ON public.webhook_deliveries (client_id, status)
    WHERE status != 'processed';
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_event
    ON public.webhook_deliveries (platform_event_id);

-- ── clients: Meta token expiry tracking (preparação para item #4) ────────────
-- Long-lived Meta tokens duram ~60 dias. Salvar quando expira para alertar
-- antes da renovação e evitar quebras silenciosas em produção.
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS meta_token_expires_at TIMESTAMPTZ;
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS meta_token_health     TEXT
    DEFAULT 'unknown'
    CHECK (meta_token_health IN ('healthy','expiring_soon','expired','invalid','unknown'));

-- ── orders: contador de retentativas CAPI ────────────────────────────────────
-- Quando Meta CAPI falha 3 vezes seguidas no fluxo síncrono do webhook, o
-- pedido fica com capi_sent=false. O job retry_failed_capi pega esses pedidos
-- e re-tenta. Para evitar loops infinitos em pedidos com problema permanente
-- (ex: token revogado), limitamos a 5 retries.
ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS capi_retry_count INT DEFAULT 0;
ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS capi_last_error  TEXT;

-- ── Limpeza: remover coluna órfã de 001_initial.sql ──────────────────────────
-- google_ads_conversion_action (sem _id) foi substituída por
-- google_ads_conversion_action_id em 003. Nenhum código usa a versão antiga.
ALTER TABLE public.clients DROP COLUMN IF EXISTS google_ads_conversion_action;
