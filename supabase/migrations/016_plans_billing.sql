-- Migration 016: SaaS plans and billing on agencies table
-- Run via Supabase SQL Editor

ALTER TABLE agencies
  ADD COLUMN IF NOT EXISTS plan           TEXT DEFAULT 'rastreador'
    CHECK (plan IN ('rastreador', 'inteligencia', 'predicao')),
  ADD COLUMN IF NOT EXISTS plan_started_at  TIMESTAMPTZ DEFAULT now(),
  ADD COLUMN IF NOT EXISTS trial_ends_at    TIMESTAMPTZ DEFAULT (now() + interval '14 days'),
  ADD COLUMN IF NOT EXISTS billing_email    TEXT,
  ADD COLUMN IF NOT EXISTS client_limit     INTEGER DEFAULT 1,
  ADD COLUMN IF NOT EXISTS orders_limit     INTEGER DEFAULT 2000;

-- Set limits for existing agencies (upgrade to predicao for Pareto Plus internal)
UPDATE agencies SET
  plan          = 'predicao',
  client_limit  = 9999,
  orders_limit  = 9999999,
  trial_ends_at = now() + interval '10 years'
WHERE slug = 'pareto-plus';

COMMENT ON COLUMN agencies.plan          IS 'SaaS plan: rastreador | inteligencia | predicao';
COMMENT ON COLUMN agencies.trial_ends_at IS 'When the 14-day trial expires (NULL = paid)';
COMMENT ON COLUMN agencies.client_limit  IS 'Max number of clients allowed on this plan';
COMMENT ON COLUMN agencies.orders_limit  IS 'Monthly order soft cap (NULL = unlimited)';
