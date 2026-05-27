-- Migration 018: Agency dashboard — goals, budgets, alert_rules engine, campaign notes
-- Bringing the Noro-Dash features into the tracking project as a single product.
-- Run via Supabase SQL Editor.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. goals — monthly targets per client (leads / conversions / revenue / ROAS)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.goals (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agency_id        UUID NOT NULL REFERENCES public.agencies(id) ON DELETE CASCADE,
  client_id        UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
  month            DATE NOT NULL,  -- first day of the month, e.g. 2026-05-01
  leads_goal       INTEGER,
  conversions_goal INTEGER,
  revenue_goal     NUMERIC(14, 2),
  roas_goal        NUMERIC(10, 4),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (client_id, month)
);

CREATE INDEX IF NOT EXISTS goals_client_month_idx ON public.goals (client_id, month DESC);

COMMENT ON COLUMN public.goals.month IS 'First day of the goal month (YYYY-MM-01).';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. budgets — monthly spend cap per client per channel
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.budgets (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agency_id   UUID NOT NULL REFERENCES public.agencies(id) ON DELETE CASCADE,
  client_id   UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
  month       DATE NOT NULL,
  channel     TEXT NOT NULL CHECK (channel IN ('meta_ads', 'google_ads', 'tiktok_ads', 'pinterest_ads', 'total')),
  amount      NUMERIC(14, 2) NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (client_id, month, channel)
);

CREATE INDEX IF NOT EXISTS budgets_client_month_idx ON public.budgets (client_id, month DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. alert_rules — config separated from instances
-- An alert rule can be agency-wide (client_id NULL) or client-specific.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.alert_rules (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agency_id         UUID NOT NULL REFERENCES public.agencies(id) ON DELETE CASCADE,
  client_id         UUID REFERENCES public.clients(id) ON DELETE CASCADE,
  name              TEXT NOT NULL,
  rule_key          TEXT NOT NULL,  -- e.g. 'roas_below_goal', 'budget_overspent', 'token_expiring'
  severity          TEXT NOT NULL DEFAULT 'warning' CHECK (severity IN ('info', 'warning', 'critical')),
  enabled           BOOLEAN NOT NULL DEFAULT true,
  channels          TEXT[] NOT NULL DEFAULT ARRAY['in_app']::TEXT[],
  throttle_minutes  INTEGER NOT NULL DEFAULT 1440,
  config            JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS alert_rules_agency_enabled_idx ON public.alert_rules (agency_id, enabled);

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. alerts — extend existing table with fingerprint + severity + rule link
-- Keep legacy columns (type, sent_via, is_resolved, data) for backwards compat
-- so existing inserts keep working until they're migrated.
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE public.alerts
  ADD COLUMN IF NOT EXISTS agency_id      UUID REFERENCES public.agencies(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS alert_rule_id  UUID REFERENCES public.alert_rules(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS severity       TEXT CHECK (severity IN ('info', 'warning', 'critical')),
  ADD COLUMN IF NOT EXISTS fingerprint    TEXT,
  ADD COLUMN IF NOT EXISTS resolved_at    TIMESTAMPTZ;

-- Backfill agency_id from clients table for existing alerts
UPDATE public.alerts a
SET agency_id = c.agency_id
FROM public.clients c
WHERE a.client_id = c.id
  AND a.agency_id IS NULL;

-- Unique partial index: an unresolved alert with a given fingerprint can only
-- exist once per client. New alerts with the same fingerprint should update
-- the existing row (via upsert on conflict).
CREATE UNIQUE INDEX IF NOT EXISTS alerts_fingerprint_open_unique
  ON public.alerts (client_id, fingerprint)
  WHERE fingerprint IS NOT NULL AND resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS alerts_client_open_idx
  ON public.alerts (client_id, created_at DESC)
  WHERE resolved_at IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. campaign_notes — timeline events ("subi budget 20%", "criativo novo")
-- Manual notes + automated entries (sync, budget changes, alerts resolved).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.campaign_notes (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agency_id   UUID NOT NULL REFERENCES public.agencies(id) ON DELETE CASCADE,
  client_id   UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
  event_date  DATE NOT NULL,
  title       TEXT NOT NULL,
  body        TEXT,
  category    TEXT,  -- 'budget' | 'creative' | 'audience' | 'observation' | 'system'
  color       TEXT,
  created_by  UUID,  -- references auth.users(id), nullable for system entries
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS campaign_notes_client_date_idx
  ON public.campaign_notes (client_id, event_date DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. sync_runs — unified log of every sync job (Meta/Google/GA4/Shopify webhook)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.sync_runs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agency_id       UUID NOT NULL REFERENCES public.agencies(id) ON DELETE CASCADE,
  client_id       UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
  provider        TEXT NOT NULL CHECK (provider IN ('meta_ads', 'google_ads', 'ga4', 'shopify', 'nuvemshop', 'woocommerce', 'tiktok_ads', 'pinterest_ads')),
  job_type        TEXT NOT NULL,  -- 'insights_daily', 'attribution_sync', 'capi_dispatch', etc
  status          TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'success', 'partial', 'failed')),
  date_from       DATE,
  date_to         DATE,
  rows_read       INTEGER NOT NULL DEFAULT 0,
  rows_written    INTEGER NOT NULL DEFAULT 0,
  error_message   TEXT,
  started_at      TIMESTAMPTZ,
  finished_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS sync_runs_client_created_idx
  ON public.sync_runs (client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS sync_runs_provider_status_idx
  ON public.sync_runs (provider, status);

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. updated_at triggers (reuse existing function if present, else create)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS goals_set_updated_at ON public.goals;
CREATE TRIGGER goals_set_updated_at BEFORE UPDATE ON public.goals
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS budgets_set_updated_at ON public.budgets;
CREATE TRIGGER budgets_set_updated_at BEFORE UPDATE ON public.budgets
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS alert_rules_set_updated_at ON public.alert_rules;
CREATE TRIGGER alert_rules_set_updated_at BEFORE UPDATE ON public.alert_rules
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. Seed default alert rules for Pareto Plus (LK + future clients)
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO public.alert_rules (agency_id, name, rule_key, severity, throttle_minutes, config)
SELECT
  id, 'Meta token prestes a expirar', 'meta_token_expiring',  'warning',  720,
  '{"threshold_days": 7}'::JSONB
FROM public.agencies WHERE slug = 'pareto-plus'
ON CONFLICT DO NOTHING;

INSERT INTO public.alert_rules (agency_id, name, rule_key, severity, throttle_minutes, config)
SELECT
  id, 'ROAS abaixo da meta', 'roas_below_goal', 'warning', 1440,
  '{"tolerance_pct": 10}'::JSONB
FROM public.agencies WHERE slug = 'pareto-plus'
ON CONFLICT DO NOTHING;

INSERT INTO public.alert_rules (agency_id, name, rule_key, severity, throttle_minutes, config)
SELECT
  id, 'Orçamento estourado', 'budget_overspent', 'critical', 1440,
  '{"threshold_pct": 5}'::JSONB
FROM public.agencies WHERE slug = 'pareto-plus'
ON CONFLICT DO NOTHING;

INSERT INTO public.alert_rules (agency_id, name, rule_key, severity, throttle_minutes, config)
SELECT
  id, 'Integração com falha', 'integration_unhealthy', 'critical', 360,
  '{}'::JSONB
FROM public.agencies WHERE slug = 'pareto-plus'
ON CONFLICT DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- Final comments
-- ─────────────────────────────────────────────────────────────────────────────
COMMENT ON TABLE public.goals          IS 'Monthly performance targets per client. UI: /clients/[id]/goals';
COMMENT ON TABLE public.budgets        IS 'Monthly spend cap per channel. Drives budget_overspent alerts.';
COMMENT ON TABLE public.alert_rules    IS 'Reusable alert config. Instances live in alerts (deduped by fingerprint).';
COMMENT ON TABLE public.campaign_notes IS 'Timeline of decisions + automated events per client. UI: /clients/[id]/timeline';
COMMENT ON TABLE public.sync_runs      IS 'Unified log of every sync job. Powers "last sync" view + sync health.';
