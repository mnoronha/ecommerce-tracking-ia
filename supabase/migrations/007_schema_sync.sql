-- =============================================================================
-- Migration: 007_schema_sync
-- Sincroniza schema com colunas/tabelas referenciadas no código mas que foram
-- criadas manualmente no Supabase ao longo do desenvolvimento e nunca foram
-- versionadas. Sem isso, qualquer ambiente novo (staging, novo cliente,
-- recriação) vai quebrar em silêncio.
--
-- Totalmente idempotente: pode ser rodada múltiplas vezes e não falha se
-- tabelas/colunas já existirem (criadas manualmente no painel do Supabase).
-- =============================================================================

-- ── clients: GA4 credentials per-cliente ──────────────────────────────────────
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS ga4_measurement_id TEXT;
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS ga4_api_secret     TEXT;

-- ── visitors: scoring + multi-touch attribution ───────────────────────────────
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS retargeting_score INT DEFAULT 0;
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS last_cart_at      TIMESTAMPTZ;
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS last_purchase_at  TIMESTAMPTZ;
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS utm_history       JSONB DEFAULT '[]'::jsonb;
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS last_seen_at      TIMESTAMPTZ DEFAULT now();

-- Adiciona constraint apenas se ainda não existir (idempotente)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM information_schema.constraint_column_usage
    WHERE table_schema = 'public'
      AND table_name   = 'visitors'
      AND column_name  = 'retargeting_score'
      AND constraint_name LIKE '%retargeting_score%'
  ) THEN
    BEGIN
      ALTER TABLE public.visitors
        ADD CONSTRAINT visitors_retargeting_score_check
        CHECK (retargeting_score BETWEEN 0 AND 100);
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
  END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_visitors_retargeting
    ON public.visitors (client_id, retargeting_score)
    WHERE retargeting_score > 0;
CREATE INDEX IF NOT EXISTS idx_visitors_last_purchase
    ON public.visitors (client_id, last_purchase_at DESC)
    WHERE last_purchase_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_visitors_last_seen
    ON public.visitors (client_id, last_seen_at DESC);

-- ── webhook_deliveries: log de auditoria de webhooks recebidos ───────────────
-- Cria a tabela se não existir; senão garante que TODAS as colunas existam
-- (a tabela pode ter sido criada manualmente no Supabase com um schema parcial).
CREATE TABLE IF NOT EXISTS public.webhook_deliveries (
    id  UUID PRIMARY KEY DEFAULT gen_random_uuid()
);

-- Garantir todas as colunas (defensivo — funciona se a tabela já existia)
ALTER TABLE public.webhook_deliveries ADD COLUMN IF NOT EXISTS client_id          UUID REFERENCES public.clients(id) ON DELETE CASCADE;
ALTER TABLE public.webhook_deliveries ADD COLUMN IF NOT EXISTS platform           TEXT;
ALTER TABLE public.webhook_deliveries ADD COLUMN IF NOT EXISTS platform_event_id  TEXT;
ALTER TABLE public.webhook_deliveries ADD COLUMN IF NOT EXISTS event_topic        TEXT;
ALTER TABLE public.webhook_deliveries ADD COLUMN IF NOT EXISTS payload            JSONB;
ALTER TABLE public.webhook_deliveries ADD COLUMN IF NOT EXISTS headers            JSONB;
ALTER TABLE public.webhook_deliveries ADD COLUMN IF NOT EXISTS signature_valid    BOOLEAN DEFAULT TRUE;
ALTER TABLE public.webhook_deliveries ADD COLUMN IF NOT EXISTS status             TEXT    DEFAULT 'processed';
ALTER TABLE public.webhook_deliveries ADD COLUMN IF NOT EXISTS error_message      TEXT;
ALTER TABLE public.webhook_deliveries ADD COLUMN IF NOT EXISTS response_code      INT;
ALTER TABLE public.webhook_deliveries ADD COLUMN IF NOT EXISTS result_order_id    UUID REFERENCES public.orders(id)   ON DELETE SET NULL;
ALTER TABLE public.webhook_deliveries ADD COLUMN IF NOT EXISTS result_visitor_id  UUID REFERENCES public.visitors(id) ON DELETE SET NULL;
ALTER TABLE public.webhook_deliveries ADD COLUMN IF NOT EXISTS created_at         TIMESTAMPTZ DEFAULT now();

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

-- ── clients: Meta token expiry tracking ──────────────────────────────────────
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS meta_token_expires_at TIMESTAMPTZ;
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS meta_token_health     TEXT DEFAULT 'unknown';

-- Adiciona CHECK constraint apenas se ainda não existir
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM information_schema.constraint_column_usage
    WHERE table_schema = 'public'
      AND table_name   = 'clients'
      AND column_name  = 'meta_token_health'
      AND constraint_name LIKE '%meta_token_health%'
  ) THEN
    BEGIN
      ALTER TABLE public.clients
        ADD CONSTRAINT clients_meta_token_health_check
        CHECK (meta_token_health IN ('healthy','expiring_soon','expired','invalid','unknown'));
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
  END IF;
END;
$$;

-- ── orders: contador de retentativas CAPI ────────────────────────────────────
ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS capi_retry_count INT DEFAULT 0;
ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS capi_last_error  TEXT;

-- ── Limpeza: remover coluna órfã de 001_initial.sql ──────────────────────────
-- google_ads_conversion_action (sem _id) foi substituída por
-- google_ads_conversion_action_id em 003. Nenhum código usa a versão antiga.
ALTER TABLE public.clients DROP COLUMN IF EXISTS google_ads_conversion_action;
