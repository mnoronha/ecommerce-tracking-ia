-- Migration 084 — Fireflies smart client mapping rules
-- Applied: pending

-- 1. Rule-based client resolution (title_pattern + participant_domain)
--    Priority chain: explicit meeting-id (077) → these rules → UNRESOLVED
CREATE TABLE IF NOT EXISTS core_fireflies_mapping_rules (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id   TEXT        NOT NULL,
    match_type  TEXT        NOT NULL
                            CHECK (match_type IN ('title_pattern', 'participant_domain')),
    match_value TEXT        NOT NULL,
    priority    INT         NOT NULL DEFAULT 10,
    enabled     BOOLEAN     NOT NULL DEFAULT TRUE,
    notes       TEXT,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT  core_fireflies_mapping_rules_uniq UNIQUE (client_id, match_type, match_value)
);

CREATE INDEX IF NOT EXISTS core_fireflies_mapping_rules_active
    ON core_fireflies_mapping_rules (enabled, priority);
CREATE INDEX IF NOT EXISTS core_fireflies_mapping_rules_client
    ON core_fireflies_mapping_rules (client_id);

-- 2. Enrich event log with Fireflies-side metadata (organizer, meeting URL)
ALTER TABLE core_fireflies_events
    ADD COLUMN IF NOT EXISTS organizer_email TEXT,
    ADD COLUMN IF NOT EXISTS meeting_link    TEXT;
