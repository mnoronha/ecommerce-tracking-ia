-- M16: core_outcomes — measured outcomes after Hermes action events
-- FK para core_action_events.id (depende de M08).
-- OutcomeStatus: PENDING, MEASURING, CONFIRMED, INCONCLUSIVE
-- Rollback: DROP TABLE IF EXISTS public.core_outcomes;
BEGIN;

CREATE TABLE IF NOT EXISTS public.core_outcomes (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    action_event_id     UUID        NOT NULL
                          REFERENCES public.core_action_events(id)
                          ON DELETE RESTRICT,
    recommendation_id   UUID
                          REFERENCES public.core_recommendations(id)
                          ON DELETE SET NULL,
    client_id           UUID,
    measurement_period  JSONB       NOT NULL DEFAULT '{}',
    metric_refs         JSONB       NOT NULL DEFAULT '[]',
    before_state        JSONB,
    after_state         JSONB,
    delta               JSONB,
    status              TEXT        NOT NULL DEFAULT 'PENDING'
                          CHECK (status IN ('PENDING','MEASURING','CONFIRMED','INCONCLUSIVE')),
    measured_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by          TEXT        NOT NULL DEFAULT 'human',
    schema_version      TEXT        NOT NULL DEFAULT '1.1'
);

ALTER TABLE public.core_outcomes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_outcomes FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.core_outcomes TO service_role;

CREATE INDEX IF NOT EXISTS core_outcomes_action_event_idx
    ON public.core_outcomes (action_event_id);

CREATE INDEX IF NOT EXISTS core_outcomes_recommendation_idx
    ON public.core_outcomes (recommendation_id)
    WHERE recommendation_id IS NOT NULL;

COMMENT ON TABLE public.core_outcomes IS
    'Measured outcomes after action events. PENDING→MEASURING→CONFIRMED|INCONCLUSIVE.';

COMMIT;
