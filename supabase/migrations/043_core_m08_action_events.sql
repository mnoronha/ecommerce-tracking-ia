-- M08: core_action_events — eventos de ação humana sobre recomendações
-- FK para core_recommendations.id (depende de M06). Append-only.
-- ActionEventType: APPROVED,IGNORED,EXECUTED_CONFIRMED,RESOLVED,EXPIRED
-- Rollback: DROP TABLE IF EXISTS public.core_action_events;
BEGIN;

CREATE TABLE IF NOT EXISTS public.core_action_events (
  id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  recommendation_id uuid        NOT NULL
                      REFERENCES public.core_recommendations(id)
                      ON DELETE RESTRICT,
  event_type        text        NOT NULL
                      CHECK (event_type IN (
                        'APPROVED','IGNORED','EXECUTED_CONFIRMED',
                        'RESOLVED','EXPIRED'
                      )),
  actor             text        NOT NULL,
  occurred_at       timestamptz NOT NULL DEFAULT now(),
  note              text,
  change_id         uuid,
  created_at        timestamptz NOT NULL DEFAULT now()
);

-- RLS_BOOTSTRAP
ALTER TABLE public.core_action_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_action_events FROM anon, authenticated;
GRANT SELECT, INSERT ON public.core_action_events TO service_role;

CREATE INDEX IF NOT EXISTS core_action_events_recommendation_idx
  ON public.core_action_events (recommendation_id);

CREATE INDEX IF NOT EXISTS core_action_events_actor_idx
  ON public.core_action_events (actor);

COMMENT ON TABLE public.core_action_events IS
  'Agency API ActionEventOut. Append-only. RLS_BOOTSTRAP.';

COMMIT;
