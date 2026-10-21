-- Migration 074 — GA4 server-side purchase state (Noro Tracking → GA4 Measurement Protocol).
-- Additive. ga4_sent_at is the idempotency guard: a row with it set is never sent again.
ALTER TABLE public.tracking_order_matches
  ADD COLUMN IF NOT EXISTS ga4_sent_at     timestamptz,
  ADD COLUMN IF NOT EXISTS ga4_send_result text;

CREATE INDEX IF NOT EXISTS tracking_order_matches_ga4_pending
  ON public.tracking_order_matches (updated_at)
  WHERE financial_status = 'paid' AND ga4_sent_at IS NULL;
