-- M06: core_recommendations — recomendações geradas pelo Hermes
-- FK para core_diagnoses.id (depende de M05).
-- RecommendationStatus: PENDING_REVIEW,APPROVED,IGNORED,EXECUTED_CONFIRMED,RESOLVED,EXPIRED
-- VisibilityScope: AGENCY_ONLY,CLIENT_VISIBLE
-- Rollback: DROP TABLE IF EXISTS public.core_recommendations;
BEGIN;

CREATE TABLE IF NOT EXISTS public.core_recommendations (
  id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  diagnosis_id       uuid        NOT NULL
                       REFERENCES public.core_diagnoses(id)
                       ON DELETE RESTRICT,
  recommendation     text        NOT NULL,
  action_proposal    jsonb,
  priority           text        NOT NULL
                       CHECK (priority IN ('LOW','MEDIUM','HIGH')),
  confidence         text        NOT NULL
                       CHECK (confidence IN ('LOW','MEDIUM','HIGH')),
  risk               text        NOT NULL
                       CHECK (risk IN ('LOW','MEDIUM','HIGH')),
  reversible         boolean     NOT NULL DEFAULT true,
  expected_effect    text,
  review_window_days int         NOT NULL DEFAULT 3,
  requires_approval  boolean     NOT NULL DEFAULT true,
  visibility_scope   text        NOT NULL DEFAULT 'AGENCY_ONLY'
                       CHECK (visibility_scope IN ('AGENCY_ONLY','CLIENT_VISIBLE')),
  status             text        NOT NULL DEFAULT 'PENDING_REVIEW'
                       CHECK (status IN (
                         'PENDING_REVIEW','APPROVED','IGNORED',
                         'EXECUTED_CONFIRMED','RESOLVED','EXPIRED'
                       )),
  created_at         timestamptz NOT NULL DEFAULT now()
);

-- RLS_BOOTSTRAP
ALTER TABLE public.core_recommendations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_recommendations FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.core_recommendations TO service_role;

CREATE INDEX IF NOT EXISTS core_recommendations_diagnosis_idx
  ON public.core_recommendations (diagnosis_id);

CREATE INDEX IF NOT EXISTS core_recommendations_status_idx
  ON public.core_recommendations (status);

COMMENT ON TABLE public.core_recommendations IS
  'Agency API RecommendationOut. Status: enums.py RecommendationStatus. RLS_BOOTSTRAP.';

COMMIT;
