-- Migration 016: Add billing / plan fields to agencies table
-- Tracks which plan the agency is on, trial window, and usage caps
-- Run via Supabase SQL Editor

ALTER TABLE agencies
  ADD COLUMN IF NOT EXISTS plan            TEXT    DEFAULT 'rastreador',
  ADD COLUMN IF NOT EXISTS plan_started_at TIMESTAMPTZ DEFAULT now(),
  ADD COLUMN IF NOT EXISTS trial_ends_at   TIMESTAMPTZ DEFAULT (now() + INTERVAL '14 days'),
  ADD COLUMN IF NOT EXISTS billing_email   TEXT,
  ADD COLUMN IF NOT EXISTS client_limit    INTEGER DEFAULT 1,
  ADD COLUMN IF NOT EXISTS orders_limit    INTEGER DEFAULT 2000;

COMMENT ON COLUMN agencies.plan            IS 'Active plan slug: rastreador | growth | scale';
COMMENT ON COLUMN agencies.plan_started_at IS 'When the current plan started';
COMMENT ON COLUMN agencies.trial_ends_at   IS 'Trial expiry date (14d from signup by default)';
COMMENT ON COLUMN agencies.billing_email   IS 'Email for billing notifications (defaults to owner email)';
COMMENT ON COLUMN agencies.client_limit    IS 'Max active clients allowed on this plan';
COMMENT ON COLUMN agencies.orders_limit    IS 'Max orders/month allowed on this plan';
