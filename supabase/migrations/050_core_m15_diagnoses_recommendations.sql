-- ─────────────────────────────────────────────────────────────────────────────
-- M15 — Core Diagnoses and Recommendations
--
-- Authority: Core detects → Hermes interprets → Human decides.
-- Core never modifies diagnosis/recommendation rows.
--
-- Replaces the v0 schema (facts/hypotheses/action_proposal) that existed
-- in the old tables. Tables were dropped and recreated to match the M15 spec.
-- ─────────────────────────────────────────────────────────────────────────────

-- Note: applied via execute_sql (drop+recreate) because tables existed with v0 schema.
-- This file documents the final state for migration history.

CREATE TABLE IF NOT EXISTS public.core_diagnoses (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id              UUID        NOT NULL,
    client_id             UUID,
    status                TEXT        NOT NULL DEFAULT 'DRAFT'
                          CHECK (status IN ('DRAFT', 'FINAL')),
    summary               TEXT        NOT NULL,
    root_cause_hypotheses JSONB       NOT NULL DEFAULT '[]',
    evidence              JSONB       NOT NULL DEFAULT '{}',
    confidence            TEXT        CHECK (confidence IN ('HIGH', 'MEDIUM', 'LOW', 'UNKNOWN')),
    limitations           TEXT,
    fingerprint           TEXT        UNIQUE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by            TEXT        NOT NULL DEFAULT 'hermes',
    schema_version        TEXT        NOT NULL DEFAULT '1.1'
);

CREATE INDEX IF NOT EXISTS core_diagnoses_alert_id_idx
    ON public.core_diagnoses (alert_id);

CREATE TABLE IF NOT EXISTS public.core_recommendations (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    diagnosis_id            UUID        NOT NULL REFERENCES public.core_diagnoses(id),
    alert_id                UUID        NOT NULL,
    client_id               UUID,
    title                   TEXT        NOT NULL,
    action                  TEXT        NOT NULL,
    rationale               TEXT        NOT NULL,
    priority                TEXT        NOT NULL DEFAULT 'MEDIUM'
                            CHECK (priority IN ('HIGH', 'MEDIUM', 'LOW')),
    risk                    TEXT,
    expected_impact         TEXT,
    requires_human_approval BOOLEAN     NOT NULL DEFAULT TRUE,
    status                  TEXT        NOT NULL DEFAULT 'PROPOSED'
                            CHECK (status IN ('PROPOSED', 'APPROVED', 'REJECTED', 'EXECUTED')),
    fingerprint             TEXT        UNIQUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by              TEXT        NOT NULL DEFAULT 'hermes',
    schema_version          TEXT        NOT NULL DEFAULT '1.1'
);

CREATE INDEX IF NOT EXISTS core_recommendations_diagnosis_id_idx
    ON public.core_recommendations (diagnosis_id);

CREATE INDEX IF NOT EXISTS core_recommendations_alert_id_idx
    ON public.core_recommendations (alert_id);

COMMENT ON TABLE public.core_diagnoses IS
    'Hermes-produced diagnoses for Core operational alerts. Hermes authority — Core never modifies.';

COMMENT ON TABLE public.core_recommendations IS
    'Hermes-proposed actions. requires_human_approval=true (MVP). PROPOSED→APPROVED|REJECTED→EXECUTED.';

COMMENT ON COLUMN public.core_diagnoses.fingerprint IS
    'Idempotency key format: diag:{alert_id}:{idempotency_key}';

COMMENT ON COLUMN public.core_recommendations.fingerprint IS
    'Idempotency key format: rec:{diagnosis_id}:{idempotency_key}';
