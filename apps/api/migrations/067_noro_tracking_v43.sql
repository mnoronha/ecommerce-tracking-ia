-- Migration 067 — Noro Tracking v4.3 (storefront v4.3.x + Custom Pixel v2.x)
--
-- 1. tracking_events: first-class columns for fields the v4.3 tracker and the
--    Shopify Custom Pixel already send (previously dropped or buried in properties).
-- 2. tracking_identities: Google click id, GA4 session, consent and the last
--    server-observed IP / User-Agent (needed later for Meta CAPI).
-- 3. tracking_order_matches: one row per Shopify order linking the order
--    (Business Truth) to the browsing evidence, with how it was linked
--    (match_source) and the shadow payloads that WOULD be sent to GA4 / Google Ads.
--
-- Invariants (unchanged from Phase 7):
--   - No raw email / phone stored by the tracking layer (email_hash only).
--   - Rows never cross client boundaries.
--   - Shadow payloads are NOT sent anywhere by this migration or its code.
--
-- Safe to re-run (IF NOT EXISTS everywhere).

-- ── 1. tracking_events ───────────────────────────────────────────────────────
ALTER TABLE tracking_events
    ADD COLUMN IF NOT EXISTS checkout_token       TEXT,
    ADD COLUMN IF NOT EXISTS variant_id           TEXT,
    ADD COLUMN IF NOT EXISTS sku                  TEXT,
    ADD COLUMN IF NOT EXISTS gclid                TEXT,
    ADD COLUMN IF NOT EXISTS ga_session_id        TEXT,
    ADD COLUMN IF NOT EXISTS consent_analytics    BOOLEAN,
    ADD COLUMN IF NOT EXISTS consent_advertising  BOOLEAN,
    ADD COLUMN IF NOT EXISTS event_source         TEXT;   -- storefront | shopify_web_pixel | server

CREATE INDEX IF NOT EXISTS tracking_events_checkout_token_idx
    ON tracking_events (client_id, checkout_token)
    WHERE checkout_token IS NOT NULL;

-- ── 2. tracking_identities ───────────────────────────────────────────────────
ALTER TABLE tracking_identities
    ADD COLUMN IF NOT EXISTS ga_session_id        TEXT,
    ADD COLUMN IF NOT EXISTS consent_advertising  BOOLEAN,
    ADD COLUMN IF NOT EXISTS consent_updated_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_ip_address      TEXT,
    ADD COLUMN IF NOT EXISTS last_user_agent      TEXT;

-- ── 3. tracking_order_matches ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tracking_order_matches (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id            UUID        NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
    order_id             TEXT        NOT NULL,          -- platform order id (Shopify numeric id)
    order_uuid           UUID,                          -- public.orders.id when known
    order_number         TEXT,
    financial_status     TEXT,                          -- paid | pending | refunded | voided | ...
    total_price          NUMERIC(12,2),
    currency             TEXT,
    order_created_at     TIMESTAMPTZ,
    paid_at              TIMESTAMPTZ,
    source_name          TEXT,                          -- web | pos | shopify_draft_order | ...
    excluded_reason      TEXT,                          -- offline | zero_value | test | NULL
    is_first_purchase    BOOLEAN,
    email_hash           TEXT,                          -- sha256(lower(trim(email))) — never raw

    -- How the order was linked to browsing evidence
    match_source         TEXT        NOT NULL DEFAULT 'none'
        CHECK (match_source IN ('cart_attributes', 'pixel', 'customer_history', 'shopify_fallback', 'none')),
    visitor_cookie_id    TEXT,
    session_id           TEXT,
    checkout_token       TEXT,

    -- Attribution evidence (click ids only when advertising consent allowed it)
    utm_source           TEXT,
    utm_medium           TEXT,
    utm_campaign         TEXT,
    utm_content          TEXT,
    utm_term             TEXT,
    click_source         TEXT,                          -- gclid | gbraid | wbraid | fbclid (type only)
    gclid                TEXT,
    gbraid               TEXT,
    wbraid               TEXT,
    fbclid               TEXT,
    fbc                  TEXT,
    fbp                  TEXT,
    ga_client_id         TEXT,
    ga_session_id        TEXT,
    consent_advertising  BOOLEAN,                       -- NULL = unknown (legacy tracker)

    -- What WOULD be sent once destinations are enabled (never sent from here)
    shadow_ga4           JSONB,
    shadow_google_ads    JSONB,

    tracker_version      TEXT,
    matched_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tracking_order_matches_order_uniq UNIQUE (client_id, order_id)
);

CREATE INDEX IF NOT EXISTS tracking_order_matches_client_created_idx
    ON tracking_order_matches (client_id, order_created_at DESC);
CREATE INDEX IF NOT EXISTS tracking_order_matches_email_hash_idx
    ON tracking_order_matches (client_id, email_hash)
    WHERE email_hash IS NOT NULL;

-- Service role bypasses RLS; no anon / authenticated access by default.
ALTER TABLE tracking_order_matches ENABLE ROW LEVEL SECURITY;
