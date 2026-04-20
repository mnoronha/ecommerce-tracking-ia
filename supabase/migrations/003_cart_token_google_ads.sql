-- =============================================================================
-- Migration: 003_cart_token_google_ads
-- Cart token attribution (PIX/external gateway) + Google Ads per-client creds
-- =============================================================================

-- visitors: attribution identifiers from last commit + cart_token
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS fbp       TEXT;
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS fbc       TEXT;
ALTER TABLE public.visitors ADD COLUMN IF NOT EXISTS cart_token TEXT;

-- Index for cart_token lookup on webhook arrival (orders/create, orders/paid)
CREATE INDEX IF NOT EXISTS idx_visitors_cart_token
    ON public.visitors (client_id, cart_token)
    WHERE cart_token IS NOT NULL;

-- clients: Google Ads per-client OAuth refresh token + conversion action ID
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS google_ads_refresh_token        TEXT;
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS google_ads_conversion_action_id TEXT;
