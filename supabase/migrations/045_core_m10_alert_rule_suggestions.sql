-- M10: core_alert_rule_suggestions — sugestões de recalibração de regras
-- client_id TEXT FK. AlertRuleSuggestionStatus: PENDING,APPROVED,REJECTED (sem APPLIED)
-- decision_actor = humano; enforcement forte na Etapa 4
-- Rollback: DROP TABLE IF EXISTS public.core_alert_rule_suggestions;
BEGIN;

CREATE TABLE IF NOT EXISTS public.core_alert_rule_suggestions (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id       text        NOT NULL
                    REFERENCES public.clients(client_id)
                    ON DELETE RESTRICT,
  rule_key        text        NOT NULL,
  current_version text        NOT NULL,
  proposed_params jsonb       NOT NULL DEFAULT '{}',
  evidence        jsonb       NOT NULL DEFAULT '[]',
  status          text        NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','APPROVED','REJECTED')),
  decision_at     timestamptz,
  decision_actor  text,
  decision_scope  text,
  decision_note   text,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- RLS_BOOTSTRAP
ALTER TABLE public.core_alert_rule_suggestions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_alert_rule_suggestions FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.core_alert_rule_suggestions TO service_role;

CREATE INDEX IF NOT EXISTS core_alert_rule_suggestions_client_status_idx
  ON public.core_alert_rule_suggestions (client_id, status);

CREATE INDEX IF NOT EXISTS core_alert_rule_suggestions_rule_idx
  ON public.core_alert_rule_suggestions (rule_key, status);

COMMENT ON TABLE public.core_alert_rule_suggestions IS
  'Agency API AlertRuleSuggestionOut. decision_actor = humano (não serviço). '
  'Enforcement forte: Etapa 4. RLS_BOOTSTRAP.';

COMMIT;
