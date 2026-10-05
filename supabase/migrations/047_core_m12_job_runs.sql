-- M12: core_job_runs — execuções de jobs com idempotência por run_key global
-- Tabela física para rota /agency/v1/jobs (nome da rota é contrato público).
-- run_key UNIQUE global: nunca reutilizado, mesmo após SUCCEEDED/FAILED/DEAD.
-- Replay: nova linha + run_key novo (<original>:replay:<n>) + replay_of → job original.
-- Advisory lock em pg_advisory_xact_lock(hashtext(run_key)) na camada Python.
-- JobStatus: QUEUED,RUNNING,SUCCEEDED,FAILED,DEAD (não SUCCESS)
-- Rollback: DROP TABLE IF EXISTS public.core_job_runs;
BEGIN;

CREATE TABLE IF NOT EXISTS public.core_job_runs (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  job_type      text        NOT NULL,
  run_key       text        NOT NULL UNIQUE,
  status        text        NOT NULL DEFAULT 'QUEUED'
                  CHECK (status IN
                    ('QUEUED','RUNNING','SUCCEEDED','FAILED','DEAD')),
  attempt       int         NOT NULL DEFAULT 0,
  next_retry_at timestamptz,
  started_at    timestamptz,
  finished_at   timestamptz,
  error         text,
  replay_of     uuid
                  REFERENCES public.core_job_runs(id)
                  ON DELETE RESTRICT,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- RLS_BOOTSTRAP
ALTER TABLE public.core_job_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_job_runs FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.core_job_runs TO service_role;

CREATE INDEX IF NOT EXISTS core_job_runs_status_created_idx
  ON public.core_job_runs (status, created_at DESC);

CREATE INDEX IF NOT EXISTS core_job_runs_replay_idx
  ON public.core_job_runs (replay_of)
  WHERE replay_of IS NOT NULL;

COMMENT ON TABLE public.core_job_runs IS
  'Agency API JobOut. Tabela física para rota /jobs. '
  'run_key UNIQUE global. Replay = nova linha + replay_of. RLS_BOOTSTRAP.';

COMMIT;
