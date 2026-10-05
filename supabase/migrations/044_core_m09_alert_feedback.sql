-- M09: core_alert_feedback — feedback humano sobre alertas, append-only
-- FK para alerts.id (legado estável). feedback: USEFUL | NOISE
-- Rollback: DROP TABLE IF EXISTS public.core_alert_feedback;
BEGIN;

CREATE TABLE IF NOT EXISTS public.core_alert_feedback (
  id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  alert_id   uuid        NOT NULL
               REFERENCES public.alerts(id)
               ON DELETE RESTRICT,
  feedback   text        NOT NULL
               CHECK (feedback IN ('USEFUL','NOISE')),
  actor      text        NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- RLS_BOOTSTRAP
ALTER TABLE public.core_alert_feedback ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_alert_feedback FROM anon, authenticated;
GRANT SELECT, INSERT ON public.core_alert_feedback TO service_role;

CREATE INDEX IF NOT EXISTS core_alert_feedback_alert_idx
  ON public.core_alert_feedback (alert_id);

COMMENT ON TABLE public.core_alert_feedback IS
  'Agency API AlertFeedbackCreate. FK para alerts legado. RLS_BOOTSTRAP.';

COMMIT;
