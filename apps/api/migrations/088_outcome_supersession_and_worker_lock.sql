-- Migration 088: Outcome versioned supersession (PRD §14.1 / R04) &
-- Worker advisory lock / lease helpers (PRD §21.4).
--
-- Purely additive and backwards-compatible:
--   1. Allows `SUPERSEDED` status on `core_outcomes` so hollow or outdated
--      outcomes can be superseded without deleting history.
--   2. Adds `supersedes_outcome_id`, `superseded_at`, `superseded_reason`
--      and a partial unique index enforcing at most ONE active outcome per
--      `(action_id, evaluation_window_days)`.
--   3. Adds Postgres advisory lock helper functions and a fallback
--      `core_worker_leases` table for single-worker coordination.

-- ── 1. core_outcomes: versioned supersession & active uniqueness ──────────────

ALTER TABLE public.core_outcomes
    ADD COLUMN IF NOT EXISTS supersedes_outcome_id uuid REFERENCES public.core_outcomes(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS superseded_at         timestamptz,
    ADD COLUMN IF NOT EXISTS superseded_reason     text;

ALTER TABLE public.core_outcomes
    DROP CONSTRAINT IF EXISTS core_outcomes_status_check;

ALTER TABLE public.core_outcomes
    ADD CONSTRAINT core_outcomes_status_check CHECK (
        status = ANY (ARRAY[
            'PENDING'::text,
            'MEASURING'::text,
            'CONFIRMED'::text,
            'INCONCLUSIVE'::text,
            'POSITIVE'::text,
            'NEGATIVE'::text,
            'NEUTRAL'::text,
            'BLOCKED_DATA_QUALITY'::text,
            'SUPERSEDED'::text
        ])
    );

CREATE UNIQUE INDEX IF NOT EXISTS uq_core_outcomes_active_action_window
    ON public.core_outcomes (action_id, evaluation_window_days)
    WHERE action_id IS NOT NULL AND status <> 'SUPERSEDED';

CREATE INDEX IF NOT EXISTS idx_core_outcomes_supersedes
    ON public.core_outcomes (supersedes_outcome_id)
    WHERE supersedes_outcome_id IS NOT NULL;

-- ── 2. Postgres advisory lock wrappers & fallback worker lease table ──────────

CREATE OR REPLACE FUNCTION public.noro_try_advisory_lock(p_key bigint)
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT pg_try_advisory_lock(p_key);
$$;

CREATE OR REPLACE FUNCTION public.noro_advisory_unlock(p_key bigint)
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT pg_advisory_unlock(p_key);
$$;

REVOKE ALL ON FUNCTION public.noro_try_advisory_lock(bigint) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.noro_try_advisory_lock(bigint) TO service_role;
REVOKE ALL ON FUNCTION public.noro_advisory_unlock(bigint) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.noro_advisory_unlock(bigint) TO service_role;

CREATE TABLE IF NOT EXISTS public.core_worker_leases (
    lock_name    text        PRIMARY KEY,
    holder_id    text        NOT NULL,
    acquired_at  timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    heartbeat_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.core_worker_leases ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_worker_leases FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.core_worker_leases TO service_role;
