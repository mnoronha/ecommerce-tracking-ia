-- M11: core_learning_candidates — candidatos de aprendizado
-- client_id nullable: NULL = aprendizado global (scope BUSINESS_MODEL ou AGENCY)
-- LearningCandidateStatus: PENDING,APPROVED,REJECTED (sem APPLIED)
-- VisibilityScope: AGENCY_ONLY,CLIENT_VISIBLE
-- LearningScope: CLIENT,BUSINESS_MODEL,AGENCY
-- EvidenceLevel: OBSERVATION,WEAK,MODERATE,STRONG
-- Rollback: DROP TABLE IF EXISTS public.core_learning_candidates;
BEGIN;

CREATE TABLE IF NOT EXISTS public.core_learning_candidates (
  id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  statement        text        NOT NULL,
  scope            text        NOT NULL
                     CHECK (scope IN ('CLIENT','BUSINESS_MODEL','AGENCY')),
  evidence_level   text        NOT NULL
                     CHECK (evidence_level IN
                       ('OBSERVATION','WEAK','MODERATE','STRONG')),
  evidence_refs    jsonb       NOT NULL DEFAULT '[]',
  context          jsonb       NOT NULL DEFAULT '{}',
  client_id        text
                     REFERENCES public.clients(client_id)
                     ON DELETE RESTRICT,
  visibility_scope text        NOT NULL DEFAULT 'AGENCY_ONLY'
                     CHECK (visibility_scope IN ('AGENCY_ONLY','CLIENT_VISIBLE')),
  status           text        NOT NULL DEFAULT 'PENDING'
                     CHECK (status IN ('PENDING','APPROVED','REJECTED')),
  created_at       timestamptz NOT NULL DEFAULT now()
);

-- RLS_BOOTSTRAP
ALTER TABLE public.core_learning_candidates ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_learning_candidates FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.core_learning_candidates TO service_role;

CREATE INDEX IF NOT EXISTS core_learning_candidates_status_idx
  ON public.core_learning_candidates (status);

CREATE INDEX IF NOT EXISTS core_learning_candidates_client_idx
  ON public.core_learning_candidates (client_id)
  WHERE client_id IS NOT NULL;

COMMENT ON TABLE public.core_learning_candidates IS
  'Agency API LearningCandidateOut. client_id nullable (global). RLS_BOOTSTRAP.';

COMMIT;
