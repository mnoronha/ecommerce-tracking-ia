-- ─────────────────────────────────────────────────────────────────────────────
-- M14 — Extend alerts table for Core operational alerts
--
-- Adds status + occurrence_count to the existing alerts table.
-- The legacy alert_engine continues to use is_resolved (boolean) unchanged.
-- Core operational alerts use status='OPEN'|'ACKNOWLEDGED'|'RESOLVED'.
--
-- Also drops the restrictive type CHECK constraint so Core types
-- (JOB_FAILED, SOURCE_STALE, etc.) can be inserted alongside legacy types.
-- ─────────────────────────────────────────────────────────────────────────────

-- Drop old type CHECK (legacy constraint named alerts_type_check or similar).
-- Use DO block to suppress error if constraint name differs between environments.
DO $$
BEGIN
  ALTER TABLE public.alerts DROP CONSTRAINT IF EXISTS alerts_type_check;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;

-- Re-check: drop by iterating pg_constraint in case of auto-generated name
DO $$
DECLARE
  c_name text;
BEGIN
  SELECT conname INTO c_name
  FROM pg_constraint
  WHERE conrelid = 'public.alerts'::regclass
    AND contype = 'c'
    AND conname ILIKE '%type%'
  LIMIT 1;
  IF c_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE public.alerts DROP CONSTRAINT %I', c_name);
  END IF;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- Drop old severity CHECK added in M018 ('info','warning','critical').
-- Core operational alerts use 'HIGH','MEDIUM','LOW' — incompatible.
DO $$
DECLARE
  c_name text;
BEGIN
  SELECT conname INTO c_name
  FROM pg_constraint
  WHERE conrelid = 'public.alerts'::regclass
    AND contype = 'c'
    AND conname ILIKE '%severity%'
  LIMIT 1;
  IF c_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE public.alerts DROP CONSTRAINT %I', c_name);
  END IF;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

ALTER TABLE public.alerts
  ADD COLUMN IF NOT EXISTS status           TEXT DEFAULT 'OPEN'
      CHECK (status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'active', 'inactive')),
  ADD COLUMN IF NOT EXISTS occurrence_count INT  DEFAULT 1;

-- Migrate legacy is_resolved rows: resolved → RESOLVED, active → OPEN
UPDATE public.alerts
SET status = CASE WHEN is_resolved THEN 'RESOLVED' ELSE 'OPEN' END
WHERE status IS NULL OR status = 'OPEN';

-- Index for efficient OPEN Core alert queries per client
CREATE INDEX IF NOT EXISTS alerts_client_type_open_idx
  ON public.alerts (client_id, type, status)
  WHERE status = 'OPEN';

-- Index for system-wide PIPELINE_NOT_RUN fingerprint lookup
CREATE INDEX IF NOT EXISTS alerts_fingerprint_status_idx
  ON public.alerts (fingerprint, status)
  WHERE fingerprint IS NOT NULL;

COMMENT ON COLUMN public.alerts.status IS
  'OPEN | ACKNOWLEDGED | RESOLVED — used by Core operational alerts. Legacy rows: active → OPEN.';

COMMENT ON COLUMN public.alerts.occurrence_count IS
  'How many times this fingerprint has been seen without resolving.';
