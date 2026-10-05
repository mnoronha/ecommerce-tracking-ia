-- M07: core_report_narratives — narrativas associadas a report contracts
-- FK para agency_report_contracts.id (uuid PK, 12 rows, não modificada).
-- NarrativeStatus: DRAFT,READY_FOR_REVIEW,APPROVED,PUBLISHED,SUPERSEDED
-- VisibilityScope: AGENCY_ONLY,CLIENT_VISIBLE
-- Rollback: DROP TABLE IF EXISTS public.core_report_narratives;
BEGIN;

CREATE TABLE IF NOT EXISTS public.core_report_narratives (
  id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  report_contract_id  uuid        NOT NULL
                        REFERENCES public.agency_report_contracts(id)
                        ON DELETE RESTRICT,
  blocks              jsonb       NOT NULL DEFAULT '[]',
  visibility_scope    text        NOT NULL DEFAULT 'AGENCY_ONLY'
                        CHECK (visibility_scope IN ('AGENCY_ONLY','CLIENT_VISIBLE')),
  status              text        NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN (
                          'DRAFT','READY_FOR_REVIEW','APPROVED',
                          'PUBLISHED','SUPERSEDED'
                        )),
  approved_by         text,
  approved_at         timestamptz,
  published_at        timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now()
);

-- RLS_BOOTSTRAP
ALTER TABLE public.core_report_narratives ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_report_narratives FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.core_report_narratives TO service_role;

CREATE INDEX IF NOT EXISTS core_report_narratives_contract_idx
  ON public.core_report_narratives (report_contract_id);

CREATE INDEX IF NOT EXISTS core_report_narratives_status_idx
  ON public.core_report_narratives (status);

COMMENT ON TABLE public.core_report_narratives IS
  'Agency API ReportNarrativeOut. FK para agency_report_contracts. RLS_BOOTSTRAP.';

COMMIT;
