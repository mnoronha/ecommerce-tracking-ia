-- Migration 054 — core_meetings (Phase 5)
-- Applied: 2026-10-14 via Supabase MCP
CREATE TABLE IF NOT EXISTS core_meetings (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id           TEXT        NOT NULL,
    provider            TEXT        NOT NULL DEFAULT 'manual'
                                    CHECK (provider IN ('zoom','google_meet','teams','manual','other')),
    external_id         TEXT,
    title               TEXT        NOT NULL,
    started_at          TIMESTAMPTZ,
    ended_at            TIMESTAMPTZ,
    participants        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    recording_url       TEXT,
    transcript_status   TEXT        NOT NULL DEFAULT 'none'
                                    CHECK (transcript_status IN ('none','pending','processing','ready','failed')),
    processing_status   TEXT        NOT NULL DEFAULT 'pending'
                                    CHECK (processing_status IN ('pending','processing','done','failed')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS core_meetings_client   ON core_meetings (client_id, started_at DESC);
CREATE TABLE IF NOT EXISTS core_meeting_transcripts (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_id  UUID        NOT NULL REFERENCES core_meetings(id) ON DELETE CASCADE,
    raw_text    TEXT        NOT NULL,
    language    TEXT        NOT NULL DEFAULT 'pt',
    word_count  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS core_meeting_summaries (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_id      UUID        NOT NULL REFERENCES core_meetings(id) ON DELETE CASCADE,
    summary         TEXT,
    decisions       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    open_questions  JSONB       NOT NULL DEFAULT '[]'::jsonb,
    commitments     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    follow_ups      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    tasks_created   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    processor       TEXT        NOT NULL DEFAULT 'manual'
                                CHECK (processor IN ('manual','claude','gpt','other')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
