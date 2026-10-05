-- M03: core_metric_snapshots — snapshots de MetricContract, append-only
-- Rollback: DROP TABLE IF EXISTS public.core_metric_snapshots;
BEGIN;

CREATE TABLE IF NOT EXISTS public.core_metric_snapshots (
  id                     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id              text        NOT NULL
                           REFERENCES public.clients(client_id)
                           ON DELETE RESTRICT,
  period_start           date        NOT NULL,
  period_end             date        NOT NULL,
  view                   text        NOT NULL
                           CHECK (view IN ('live','certified')),
  client_truth_version   int         NOT NULL DEFAULT 1,
  target_truth_version   int         NOT NULL DEFAULT 1,
  conversion_map_version int         NOT NULL DEFAULT 1,
  metrics                jsonb       NOT NULL DEFAULT '[]',
  health                 jsonb       NOT NULL DEFAULT '{}',
  computed_at            timestamptz NOT NULL DEFAULT now(),
  source_run_id          text,

  CONSTRAINT core_metric_snapshots_period_valid
    CHECK (period_start <= period_end)
);

-- RLS_BOOTSTRAP
ALTER TABLE public.core_metric_snapshots ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_metric_snapshots FROM anon, authenticated;
GRANT SELECT, INSERT ON public.core_metric_snapshots TO service_role;

CREATE INDEX IF NOT EXISTS core_metric_snapshots_client_period_idx
  ON public.core_metric_snapshots (client_id, period_start DESC, computed_at DESC);

CREATE INDEX IF NOT EXISTS core_metric_snapshots_source_run_idx
  ON public.core_metric_snapshots (source_run_id)
  WHERE source_run_id IS NOT NULL;

COMMENT ON TABLE public.core_metric_snapshots IS
  'Agency API MetricContract. Append-only. RLS_BOOTSTRAP: service_role apenas.';

COMMIT;
