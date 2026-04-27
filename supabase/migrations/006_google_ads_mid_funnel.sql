-- =============================================================================
-- Migration: 006_google_ads_mid_funnel
-- Google Ads mid-funnel conversions (AddToCart, Checkout) + AW-ID for gtag
-- =============================================================================

-- Conversion Action IDs for mid-funnel events (server-side upload via Conversion API)
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS google_ads_add_to_cart_action_id TEXT;
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS google_ads_checkout_action_id     TEXT;

-- AW-XXXXXXXXXX tag ID used in the Shopify gtag.js remarketing snippet
-- Different from google_ads_customer_id (10-digit account ID used in API calls)
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS google_ads_aw_id TEXT;
