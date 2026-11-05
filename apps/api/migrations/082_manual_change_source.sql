-- Migration 082 — Manual Change Source
--
-- Adds 'MANUAL' to the core_change_log.source CHECK constraint.
-- Existing rows with source='HUMAN' remain valid.
-- Additive only: no rows modified.

BEGIN;

ALTER TABLE public.core_change_log
  DROP CONSTRAINT IF EXISTS core_change_log_source_check;

ALTER TABLE public.core_change_log
  ADD CONSTRAINT core_change_log_source_check CHECK (source IN (
    'AUTO_GOOGLE_ADS',
    'AUTO_META',
    'HUMAN',
    'HERMES_RECOMMENDATION',
    'AUTOMATION',
    'PLATFORM_DETECTED',
    'MANUAL'
  ));

CREATE INDEX IF NOT EXISTS core_change_log_manual_idx
  ON public.core_change_log (client_id, occurred_at DESC)
  WHERE source = 'MANUAL';

COMMENT ON COLUMN public.core_change_log.source IS
  'Origin of change: MANUAL = human-registered via platform UI; '
  'PLATFORM_DETECTED = auto-ingested from Google/Meta APIs; '
  'HUMAN = legacy human-created entry.';

COMMIT;
