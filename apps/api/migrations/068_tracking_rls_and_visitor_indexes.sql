-- Migration 068 — RLS on Noro Tracking tables + indexes for the Visitors screen
--
-- 055/056 created these tables without RLS. On Supabase, public tables without
-- RLS can be readable with the public anon key. The web app never reads them
-- directly (all access goes through the API with the service role, which
-- bypasses RLS), so enabling RLS with no policies only closes that exposure.
--
-- Safe to re-run.

ALTER TABLE IF EXISTS public.tracking_sessions            ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.tracking_identities          ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.tracking_attribution_inputs  ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.tracking_ingest_tokens       ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.tracking_order_matches       ENABLE ROW LEVEL SECURITY;

-- Visitors screen: list by recency, journey by visitor
CREATE INDEX IF NOT EXISTS tracking_identities_client_last_seen_idx
    ON public.tracking_identities (client_id, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS tracking_sessions_client_visitor_idx
    ON public.tracking_sessions (client_id, visitor_cookie_id, started_at);
CREATE INDEX IF NOT EXISTS tracking_events_client_visitor_cookie_idx
    ON public.tracking_events (client_id, visitor_cookie_id, created_at)
    WHERE visitor_cookie_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS tracking_order_matches_client_visitor_idx
    ON public.tracking_order_matches (client_id, visitor_cookie_id)
    WHERE visitor_cookie_id IS NOT NULL;
