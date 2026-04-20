-- =============================================================================
-- Migration: 003_cart_token_google_ads
-- Cart token attribution (PIX/external gateway) + Google Ads per-client creds
-- =============================================================================

-- visitors: attribution identifiers from last commit + cart_token + ga_client_id
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS fbp          TEXT;
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS fbc          TEXT;
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS cart_token   TEXT;
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS ga_client_id TEXT;

-- Index for cart_token lookup on webhook arrival (orders/create, orders/paid)
CREATE INDEX IF NOT EXISTS idx_visitors_cart_token
    ON public.visitors (client_id, cart_token)
    WHERE cart_token IS NOT NULL;

-- clients: Google Ads per-client OAuth refresh token + conversion action ID
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS google_ads_refresh_token        TEXT;
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS google_ads_conversion_action_id TEXT;

-- audience_syncs: tracks Meta Custom Audience sync status per client
CREATE TABLE IF NOT EXISTS public.audience_syncs (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id            UUID        NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
    audience_type        TEXT        NOT NULL,
    platform             TEXT        NOT NULL DEFAULT 'meta',
    platform_audience_id TEXT,
    audience_name        TEXT,
    users_count          INT         DEFAULT 0,
    last_synced_at       TIMESTAMPTZ,
    status               TEXT        DEFAULT 'never_synced',
    error_message        TEXT,
    created_at           TIMESTAMPTZ DEFAULT now(),
    UNIQUE(client_id, audience_type, platform)
);
ALTER TABLE public.audience_syncs ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_audience_syncs_client ON public.audience_syncs (client_id);
