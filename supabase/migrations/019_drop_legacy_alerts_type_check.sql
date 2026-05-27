-- Migration 019: drop legacy alerts.type CHECK constraint.
-- New alerts identify their kind via alert_rule_id → alert_rules.rule_key,
-- so the hardcoded list (cpa_spike, roas_drop, etc) is no longer authoritative.
-- Keeping the `type` column nullable as a backward-compat label only.
-- Run via Supabase SQL Editor.

ALTER TABLE public.alerts
  DROP CONSTRAINT IF EXISTS alerts_type_check;
