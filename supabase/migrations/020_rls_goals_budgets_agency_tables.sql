-- Migration 020: Enable RLS + policies for tables created in 018 (goals, budgets,
-- alert_rules, campaign_notes, sync_runs). These tables were created without RLS
-- policies, causing "violates row-level security" errors for authenticated users.

-- ── goals ─────────────────────────────────────────────────────────────────────
ALTER TABLE public.goals ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_policies WHERE tablename = 'goals' AND policyname = 'goals_iso'
  ) THEN
    CREATE POLICY goals_iso ON public.goals FOR ALL
      USING (agency_id IN (SELECT public.get_user_agency_ids()))
      WITH CHECK (agency_id IN (SELECT public.get_user_agency_ids()));
  END IF;
END $$;

-- ── budgets ───────────────────────────────────────────────────────────────────
ALTER TABLE public.budgets ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_policies WHERE tablename = 'budgets' AND policyname = 'budgets_iso'
  ) THEN
    CREATE POLICY budgets_iso ON public.budgets FOR ALL
      USING (agency_id IN (SELECT public.get_user_agency_ids()))
      WITH CHECK (agency_id IN (SELECT public.get_user_agency_ids()));
  END IF;
END $$;

-- ── alert_rules ───────────────────────────────────────────────────────────────
ALTER TABLE public.alert_rules ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_policies WHERE tablename = 'alert_rules' AND policyname = 'alert_rules_iso'
  ) THEN
    CREATE POLICY alert_rules_iso ON public.alert_rules FOR ALL
      USING (agency_id IN (SELECT public.get_user_agency_ids()))
      WITH CHECK (agency_id IN (SELECT public.get_user_agency_ids()));
  END IF;
END $$;

-- ── campaign_notes ────────────────────────────────────────────────────────────
ALTER TABLE public.campaign_notes ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_policies WHERE tablename = 'campaign_notes' AND policyname = 'campaign_notes_iso'
  ) THEN
    CREATE POLICY campaign_notes_iso ON public.campaign_notes FOR ALL
      USING (agency_id IN (SELECT public.get_user_agency_ids()))
      WITH CHECK (agency_id IN (SELECT public.get_user_agency_ids()));
  END IF;
END $$;

-- ── sync_runs ─────────────────────────────────────────────────────────────────
ALTER TABLE public.sync_runs ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_policies WHERE tablename = 'sync_runs' AND policyname = 'sync_runs_iso'
  ) THEN
    CREATE POLICY sync_runs_iso ON public.sync_runs FOR ALL
      USING (agency_id IN (SELECT public.get_user_agency_ids()))
      WITH CHECK (agency_id IN (SELECT public.get_user_agency_ids()));
  END IF;
END $$;
