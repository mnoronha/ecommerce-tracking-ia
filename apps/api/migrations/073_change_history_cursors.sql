-- Migration 073 — Change History Ingest infrastructure
--
-- 1. core_change_history_cursors: per-(client, platform) polling state
-- 2. Unique partial index on core_change_log for dedup by external_change_id
--
-- Additive-only. Safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS public.core_change_history_cursors (
  id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id      text        NOT NULL,
  platform       text        NOT NULL
                   CHECK (platform IN ('google', 'meta')),
  last_polled_at timestamptz,
  last_status    text,
  error_message  text,
  changes_count  int         DEFAULT 0,
  updated_at     timestamptz DEFAULT now(),
  UNIQUE (client_id, platform)
);

COMMENT ON TABLE public.core_change_history_cursors IS
  'Polling cursor for incremental change history ingest. One row per (client, platform).';
COMMENT ON COLUMN public.core_change_history_cursors.last_polled_at IS
  'Upper bound of the last successful poll window. Next poll starts here minus OVERLAP_MINUTES.';
COMMENT ON COLUMN public.core_change_history_cursors.last_status IS
  'READY | PERMISSION_DENIED | ERROR. NO_DATA is not used — zero changes = READY with changes_count=0.';

-- Dedup guard: prevents duplicate external_change_id per (client, channel).
-- Partial (WHERE NOT NULL) so NULLs don't participate in uniqueness.
CREATE UNIQUE INDEX IF NOT EXISTS core_change_log_ext_id_dedup
  ON public.core_change_log (client_id, channel, external_change_id)
  WHERE external_change_id IS NOT NULL;

COMMIT;
