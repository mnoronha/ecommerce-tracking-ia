-- M02: core_client_truth — truth versions por cliente, append-only
-- Rollback: DROP TABLE IF EXISTS public.core_client_truth;
BEGIN;

CREATE TABLE IF NOT EXISTS public.core_client_truth (
  id                     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id              text        NOT NULL
                           REFERENCES public.clients(client_id)
                           ON DELETE RESTRICT,
  client_version         int         NOT NULL DEFAULT 1,
  client_truth           jsonb       NOT NULL DEFAULT '{}',
  target_version         int         NOT NULL DEFAULT 1,
  target_truth           jsonb       NOT NULL DEFAULT '{}',
  conversion_map_version int         NOT NULL DEFAULT 1,
  valid_from             timestamptz NOT NULL DEFAULT now(),
  created_at             timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT core_client_truth_versions_positive
    CHECK (client_version > 0
       AND target_version > 0
       AND conversion_map_version > 0)
);

-- RLS_BOOTSTRAP
ALTER TABLE public.core_client_truth ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_client_truth FROM anon, authenticated;
GRANT SELECT, INSERT ON public.core_client_truth TO service_role;

CREATE INDEX IF NOT EXISTS core_client_truth_client_valid_idx
  ON public.core_client_truth (client_id, valid_from DESC);

COMMENT ON TABLE public.core_client_truth IS
  'Agency API TruthOut. Append-only. RLS_BOOTSTRAP: service_role apenas.';

COMMIT;
