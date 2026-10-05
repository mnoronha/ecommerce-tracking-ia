-- M05: core_diagnoses — diagnósticos gerados pelo Hermes
-- FK para alerts.id (legado estável, 1203 rows).
-- Rollback: DROP TABLE IF EXISTS public.core_diagnoses;
BEGIN;

CREATE TABLE IF NOT EXISTS public.core_diagnoses (
  id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  alert_id         uuid        NOT NULL
                     REFERENCES public.alerts(id)
                     ON DELETE RESTRICT,
  facts            jsonb       NOT NULL DEFAULT '[]',
  localization     text,
  related_changes  jsonb       NOT NULL DEFAULT '[]',
  hypotheses       jsonb       NOT NULL DEFAULT '[]',
  confidence       text        NOT NULL
                     CHECK (confidence IN ('LOW','MEDIUM','HIGH')),
  do_not_conclude  jsonb       NOT NULL DEFAULT '[]',
  data_limitations jsonb       NOT NULL DEFAULT '[]',
  visibility_scope text        NOT NULL DEFAULT 'AGENCY_ONLY'
                     CHECK (visibility_scope IN ('AGENCY_ONLY','CLIENT_VISIBLE')),
  created_at       timestamptz NOT NULL DEFAULT now()
);

-- RLS_BOOTSTRAP
ALTER TABLE public.core_diagnoses ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_diagnoses FROM anon, authenticated;
GRANT SELECT, INSERT ON public.core_diagnoses TO service_role;

CREATE INDEX IF NOT EXISTS core_diagnoses_alert_idx
  ON public.core_diagnoses (alert_id);

COMMENT ON TABLE public.core_diagnoses IS
  'Agency API DiagnosisOut. FK para alerts legado. RLS_BOOTSTRAP.';

COMMIT;
