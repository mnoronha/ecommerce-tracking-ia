-- M20: core_actions — structured action records linked to accepted recommendations
-- Also: alter core_outcomes to support action_id FK + richer outcome status
--       alter core_learning_candidates to add provenance + pattern columns
-- Rollback:
--   DROP TABLE IF EXISTS public.core_actions CASCADE;
--   ALTER TABLE public.core_outcomes
--     DROP COLUMN IF EXISTS action_id,
--     DROP COLUMN IF EXISTS evaluation_window_days,
--     DROP COLUMN IF EXISTS confidence,
--     DROP COLUMN IF EXISTS data_health;
--   ALTER TABLE public.core_learning_candidates
--     DROP COLUMN IF EXISTS source_action_id,
--     DROP COLUMN IF EXISTS source_recommendation_id,
--     DROP COLUMN IF EXISTS pattern,
--     DROP COLUMN IF EXISTS observed_outcome;
BEGIN;

-- ── 1. core_actions ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.core_actions (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id               UUID        NOT NULL,
    recommendation_id       UUID        NOT NULL
                              REFERENCES public.core_recommendations(id)
                              ON DELETE RESTRICT,
    diagnosis_id            UUID
                              REFERENCES public.core_diagnoses(id)
                              ON DELETE SET NULL,
    alert_id                TEXT,
    action_type             TEXT        NOT NULL
                              CHECK (action_type IN (
                                'MANUAL_EXTERNAL_ACTION',
                                'INTERNAL_ACTION',
                                'NO_ACTION_REQUIRED'
                              )),
    platform                TEXT,
    entity_type             TEXT,
    entity_id               TEXT,
    status                  TEXT        NOT NULL DEFAULT 'PLANNED'
                              CHECK (status IN ('PLANNED','EXECUTED','FAILED','CANCELLED')),
    executed_by             TEXT,
    executed_at             TIMESTAMPTZ,
    evaluation_window_days  INT         NOT NULL DEFAULT 7,
    before_state            JSONB       NOT NULL DEFAULT '{}',
    after_state             JSONB       NOT NULL DEFAULT '{}',
    reason                  TEXT,
    provenance              JSONB       NOT NULL DEFAULT '{}',
    requires_external_write BOOLEAN     NOT NULL DEFAULT false,
    change_log_id           UUID,
    schema_version          TEXT        NOT NULL DEFAULT '1.1',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS core_actions_recommendation_idx
    ON public.core_actions (recommendation_id);
CREATE INDEX IF NOT EXISTS core_actions_client_idx
    ON public.core_actions (client_id);
CREATE INDEX IF NOT EXISTS core_actions_status_idx
    ON public.core_actions (status)
    WHERE status IN ('PLANNED','EXECUTED');

ALTER TABLE public.core_actions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_actions FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.core_actions TO service_role;

COMMENT ON TABLE public.core_actions IS
    'Structured action records created when a recommendation is accepted. '
    'PLANNED → EXECUTED|FAILED|CANCELLED. Outcome evaluator reads EXECUTED rows. '
    'Append-only for PLANNED rows; UPDATE allowed for status transitions.';

-- ── 2. Extend core_outcomes ────────────────────────────────────────────────────

-- Make action_event_id nullable (new outcomes may link to core_actions instead)
ALTER TABLE public.core_outcomes
    ALTER COLUMN action_event_id DROP NOT NULL;

-- Add FK to core_actions
ALTER TABLE public.core_outcomes
    ADD COLUMN IF NOT EXISTS action_id UUID REFERENCES public.core_actions(id) ON DELETE SET NULL;

-- Add evaluation metadata columns
ALTER TABLE public.core_outcomes
    ADD COLUMN IF NOT EXISTS evaluation_window_days INT;
ALTER TABLE public.core_outcomes
    ADD COLUMN IF NOT EXISTS confidence TEXT
        CHECK (confidence IS NULL OR confidence IN ('LOW','MEDIUM','HIGH'));
ALTER TABLE public.core_outcomes
    ADD COLUMN IF NOT EXISTS data_health JSONB DEFAULT '{}';

-- Extend status enum to include outcome quality values
ALTER TABLE public.core_outcomes
    DROP CONSTRAINT IF EXISTS core_outcomes_status_check;
ALTER TABLE public.core_outcomes
    ADD CONSTRAINT core_outcomes_status_check CHECK (
        status IN (
            'PENDING', 'MEASURING', 'CONFIRMED', 'INCONCLUSIVE',
            'POSITIVE', 'NEGATIVE', 'NEUTRAL', 'BLOCKED_DATA_QUALITY'
        )
    );

CREATE INDEX IF NOT EXISTS core_outcomes_action_id_idx
    ON public.core_outcomes (action_id)
    WHERE action_id IS NOT NULL;

-- ── 3. Extend core_learning_candidates ────────────────────────────────────────

ALTER TABLE public.core_learning_candidates
    ADD COLUMN IF NOT EXISTS source_action_id UUID REFERENCES public.core_actions(id) ON DELETE SET NULL;
ALTER TABLE public.core_learning_candidates
    ADD COLUMN IF NOT EXISTS source_recommendation_id UUID REFERENCES public.core_recommendations(id) ON DELETE SET NULL;
ALTER TABLE public.core_learning_candidates
    ADD COLUMN IF NOT EXISTS pattern TEXT;
ALTER TABLE public.core_learning_candidates
    ADD COLUMN IF NOT EXISTS observed_outcome JSONB DEFAULT '{}';

-- Add SUPERSEDED to status
ALTER TABLE public.core_learning_candidates
    DROP CONSTRAINT IF EXISTS core_learning_candidates_status_check;
ALTER TABLE public.core_learning_candidates
    ADD CONSTRAINT core_learning_candidates_status_check
        CHECK (status IN ('PENDING','APPROVED','REJECTED','SUPERSEDED'));

COMMENT ON TABLE public.core_learning_candidates IS
    'Proposed learnings extracted from CONFIRMED/POSITIVE/NEGATIVE outcomes. '
    'Always PENDING until human APPROVED. Never auto-promoted to Agency Knowledge.';

COMMIT;
