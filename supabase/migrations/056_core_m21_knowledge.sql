-- M21: Agency Knowledge + Knowledge Promotions
--
-- core_agency_knowledge: canonical store for promoted learning candidates.
--   Hermes reads this table as approved intelligence.
--   PROPOSED/PENDING candidates are never surfaced here.
--
-- core_knowledge_promotions: immutable audit trail for each promote action.
--   Idempotency: one promotion per (learning_candidate_id, status=COMPLETED).
--
-- Rollback:
--   DROP TABLE IF EXISTS public.core_knowledge_promotions CASCADE;
--   DROP TABLE IF EXISTS public.core_agency_knowledge CASCADE;

BEGIN;

-- ── 1. core_agency_knowledge ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.core_agency_knowledge (
    id                         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Source lineage (all optional — set at promotion time)
    learning_candidate_id      UUID        UNIQUE  -- one knowledge doc per candidate
                                           REFERENCES public.core_learning_candidates(id)
                                           ON DELETE SET NULL,
    source_alert_id            TEXT,
    source_diagnosis_id        TEXT,
    source_recommendation_id   TEXT,
    source_action_id           TEXT,
    source_outcome_id          TEXT,

    -- Document content
    title                      TEXT        NOT NULL,
    statement                  TEXT        NOT NULL,
    pattern                    TEXT,
    context                    JSONB       NOT NULL DEFAULT '{}',
    observed_outcome           JSONB       NOT NULL DEFAULT '{}',
    evidence_refs              JSONB       NOT NULL DEFAULT '[]',
    evidence_level             TEXT        NOT NULL DEFAULT 'OBSERVATION'
                                           CHECK (evidence_level IN ('OBSERVATION','WEAK','MODERATE','STRONG')),
    confidence                 TEXT
                                           CHECK (confidence IS NULL OR confidence IN ('LOW','MEDIUM','HIGH')),
    scope                      TEXT        NOT NULL DEFAULT 'CLIENT_ONLY'
                                           CHECK (scope IN (
                                             'CLIENT_ONLY','CLIENT','BUSINESS_MODEL',
                                             'PLATFORM','AGENCY_WIDE_CANDIDATE','AGENCY'
                                           )),
    client_id                  UUID,
    business_model             TEXT,
    platform                   TEXT,

    -- When to apply / not apply
    when_to_apply              TEXT,
    when_not_to_apply          TEXT,
    limitations                TEXT,

    -- Approval audit
    approved_by                TEXT        NOT NULL,
    approved_at                TIMESTAMPTZ NOT NULL,

    -- Promotion audit
    promoted_by                TEXT        NOT NULL,
    promoted_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Obsidian / document metadata
    document_path              TEXT,       -- e.g. knowledge/ads/ecommerce/balance-recharge/slug.md
    document_slug              TEXT,
    document_version           INT         NOT NULL DEFAULT 1,
    obsidian_status            TEXT        NOT NULL DEFAULT 'PENDING'
                                           CHECK (obsidian_status IN (
                                             'PENDING','WRITTEN','SKIPPED','FAILED'
                                           )),
    obsidian_url               TEXT,       -- GitHub permalink if written
    markdown_content           TEXT,       -- full markdown stored for re-sync

    -- Duplicate detection
    supersedes_id              UUID        REFERENCES public.core_agency_knowledge(id) ON DELETE SET NULL,

    schema_version             TEXT        NOT NULL DEFAULT '1.1',
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Pattern index for duplicate detection
CREATE INDEX IF NOT EXISTS core_agency_knowledge_pattern_idx
    ON public.core_agency_knowledge (pattern)
    WHERE pattern IS NOT NULL;

CREATE INDEX IF NOT EXISTS core_agency_knowledge_scope_idx
    ON public.core_agency_knowledge (scope);

CREATE INDEX IF NOT EXISTS core_agency_knowledge_client_idx
    ON public.core_agency_knowledge (client_id)
    WHERE client_id IS NOT NULL;

ALTER TABLE public.core_agency_knowledge ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_agency_knowledge FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.core_agency_knowledge TO service_role;

COMMENT ON TABLE public.core_agency_knowledge IS
    'M21: Promoted learning candidates as Agency Knowledge. '
    'Only APPROVED learning candidates can be promoted (human gate). '
    'Hermes reads this table as trusted intelligence. '
    'PROPOSED/PENDING candidates are never surfaced here.';

-- ── 2. core_knowledge_promotions ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.core_knowledge_promotions (
    id                         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    learning_candidate_id      UUID        NOT NULL
                                           REFERENCES public.core_learning_candidates(id)
                                           ON DELETE RESTRICT,
    knowledge_id               UUID
                                           REFERENCES public.core_agency_knowledge(id)
                                           ON DELETE SET NULL,
    actor                      TEXT        NOT NULL,
    reason                     TEXT,
    status                     TEXT        NOT NULL DEFAULT 'PENDING'
                                           CHECK (status IN (
                                             'PENDING','COMPLETED','FAILED',
                                             'EXISTING_KNOWLEDGE','NEW_VERSION'
                                           )),
    destination                TEXT        NOT NULL DEFAULT 'agency_knowledge',
    document_path              TEXT,
    document_version           INT,
    existing_knowledge_id      UUID
                                           REFERENCES public.core_agency_knowledge(id)
                                           ON DELETE SET NULL,
    error                      TEXT,
    idempotency_key            TEXT        UNIQUE,
    promoted_at                TIMESTAMPTZ,
    schema_version             TEXT        NOT NULL DEFAULT '1.1',
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS core_knowledge_promotions_candidate_idx
    ON public.core_knowledge_promotions (learning_candidate_id);

CREATE INDEX IF NOT EXISTS core_knowledge_promotions_status_idx
    ON public.core_knowledge_promotions (status);

ALTER TABLE public.core_knowledge_promotions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_knowledge_promotions FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.core_knowledge_promotions TO service_role;

COMMENT ON TABLE public.core_knowledge_promotions IS
    'M21: Immutable audit trail for each knowledge promotion attempt. '
    'Idempotency via idempotency_key UNIQUE constraint. '
    'EXISTING_KNOWLEDGE = similar pattern already in knowledge base. '
    'NEW_VERSION = new version created for existing pattern.';

COMMIT;
