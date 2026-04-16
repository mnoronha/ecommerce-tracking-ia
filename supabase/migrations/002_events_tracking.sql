-- =============================================================================
-- Migration: 002_events_tracking
-- Tabela genérica de eventos para o pixel JS e webhooks
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Armazena todos os eventos normalizados (pixel + webhooks)
-- client_id aqui é TEXT (slug/pixel_id) para não exigir FK de UUID na hora
-- do tracking — o worker depois faz o match para a tabela clients
CREATE TABLE IF NOT EXISTS public.events (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_id    TEXT        NOT NULL UNIQUE,
    event_type  TEXT        NOT NULL,
    platform    TEXT        NOT NULL,
    client_id   TEXT        NOT NULL,
    visitor_id  TEXT,
    session_id  TEXT,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    customer    JSONB,
    order_data  JSONB,
    utm         JSONB,
    page_url    TEXT,
    referrer    TEXT,
    raw_payload JSONB,
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- RLS: service_role bypassa (API server-side); anon/authenticated sem acesso direto
ALTER TABLE public.events ENABLE ROW LEVEL SECURITY;

-- Só o service_role (API) insere — sem policy pública
-- (service_role bypassa RLS por padrão no Supabase)

CREATE INDEX IF NOT EXISTS idx_events_client_id        ON public.events (client_id);
CREATE INDEX IF NOT EXISTS idx_events_visitor_id       ON public.events (visitor_id);
CREATE INDEX IF NOT EXISTS idx_events_event_type       ON public.events (event_type);
CREATE INDEX IF NOT EXISTS idx_events_platform         ON public.events (platform);
CREATE INDEX IF NOT EXISTS idx_events_timestamp        ON public.events (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_client_timestamp ON public.events (client_id, timestamp DESC);
