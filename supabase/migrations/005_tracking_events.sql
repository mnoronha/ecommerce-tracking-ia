-- =============================================================================
-- Migration: 005_tracking_events
-- Tabela de eventos do pixel JS — lida pelo dashboard e pelo AI analyst
-- ATENÇÃO: Se a tabela já existe no Supabase (criada manualmente), aplique
-- apenas as políticas RLS abaixo para liberar leitura ao dashboard.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.tracking_events (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id         UUID        REFERENCES public.clients(id) ON DELETE CASCADE,
    visitor_id        UUID        REFERENCES public.visitors(id) ON DELETE SET NULL,
    visitor_cookie_id TEXT,
    session_id        TEXT,
    event_type        TEXT        NOT NULL,
    url               TEXT,
    referrer          TEXT,
    utm_source        TEXT,
    utm_medium        TEXT,
    utm_campaign      TEXT,
    utm_content       TEXT,
    utm_term          TEXT,
    product_id        TEXT,
    product_name      TEXT,
    product_sku       TEXT,
    product_price     NUMERIC,
    product_quantity  INTEGER,
    product_category  TEXT,
    properties        JSONB       DEFAULT '{}',
    processed         BOOLEAN     DEFAULT FALSE,
    created_at        TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE public.tracking_events ENABLE ROW LEVEL SECURITY;

-- Leitura para usuários autenticados (dashboard) — filtrado por cliente do usuário
-- (service_role bypassa RLS por padrão, então o API backend escreve sem policy)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename  = 'tracking_events'
      AND policyname = 'tracking_events_read'
  ) THEN
    CREATE POLICY tracking_events_read ON public.tracking_events
      FOR SELECT USING (client_id IN (SELECT public.get_user_client_ids()));
  END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_tracking_events_client
    ON public.tracking_events (client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tracking_events_visitor
    ON public.tracking_events (visitor_id);
CREATE INDEX IF NOT EXISTS idx_tracking_events_type
    ON public.tracking_events (client_id, event_type);
CREATE INDEX IF NOT EXISTS idx_tracking_events_product
    ON public.tracking_events (client_id, product_name)
    WHERE product_name IS NOT NULL;
