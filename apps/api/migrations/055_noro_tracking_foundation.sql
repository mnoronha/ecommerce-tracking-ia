-- Migration 055 — Noro Tracking Foundation (Phase 6)
-- Applied: 2026-10-14 via Supabase MCP
CREATE TABLE IF NOT EXISTS tracking_sessions (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id           UUID        NOT NULL,
    session_id          TEXT        NOT NULL,
    visitor_cookie_id   TEXT        NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at            TIMESTAMPTZ,
    duration_seconds    INTEGER,
    page_count          INTEGER     NOT NULL DEFAULT 0,
    event_count         INTEGER     NOT NULL DEFAULT 0,
    utm_source          TEXT,
    utm_medium          TEXT,
    utm_campaign        TEXT,
    landing_url         TEXT,
    exit_url            TEXT,
    converted           BOOLEAN     NOT NULL DEFAULT FALSE,
    conversion_value    NUMERIC,
    order_id            TEXT,
    CONSTRAINT tracking_sessions_session_uniq UNIQUE (client_id, session_id)
);
CREATE TABLE IF NOT EXISTS tracking_identities (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id           UUID        NOT NULL,
    visitor_cookie_id   TEXT        NOT NULL,
    email_hash          TEXT,
    phone_hash          TEXT,
    external_id         TEXT,
    fbp                 TEXT,
    fbc                 TEXT,
    gclid               TEXT,
    gbraid              TEXT,
    wbraid              TEXT,
    ga_client_id        TEXT,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tracking_identities_visitor_uniq UNIQUE (client_id, visitor_cookie_id)
);
CREATE TABLE IF NOT EXISTS tracking_attribution_inputs (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id           UUID        NOT NULL,
    order_id            TEXT        NOT NULL,
    session_id          TEXT,
    visitor_cookie_id   TEXT,
    utm_source          TEXT,
    utm_medium          TEXT,
    utm_campaign        TEXT,
    fbp                 TEXT,
    fbc                 TEXT,
    fbclid              TEXT,
    gclid               TEXT,
    gbraid              TEXT,
    wbraid              TEXT,
    ga_client_id        TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tracking_attribution_inputs_order_uniq UNIQUE (client_id, order_id)
);
ALTER TABLE tracking_events
    ADD COLUMN IF NOT EXISTS event_id   TEXT,
    ADD COLUMN IF NOT EXISTS fbp        TEXT,
    ADD COLUMN IF NOT EXISTS fbc        TEXT,
    ADD COLUMN IF NOT EXISTS gbraid     TEXT,
    ADD COLUMN IF NOT EXISTS wbraid     TEXT,
    ADD COLUMN IF NOT EXISTS order_id   TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS tracking_events_event_id_dedup
    ON tracking_events (client_id, event_id) WHERE event_id IS NOT NULL;
