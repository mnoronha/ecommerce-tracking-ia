-- Migration 077 — Fireflies.ai integration
-- Applied: 2026-10-27

-- 1. Add 'fireflies' to core_meetings provider enum
ALTER TABLE core_meetings
    DROP CONSTRAINT IF EXISTS core_meetings_provider_check;
ALTER TABLE core_meetings
    ADD CONSTRAINT core_meetings_provider_check
    CHECK (provider IN ('zoom','google_meet','teams','manual','other','fireflies'));

-- 2. Webhook event log — idempotency + audit
CREATE TABLE IF NOT EXISTS core_fireflies_events (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    fireflies_meeting_id TEXT        NOT NULL,
    event_type           TEXT        NOT NULL,
    raw_payload          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    client_id            TEXT,
    resolution_method    TEXT,
    meeting_id           UUID        REFERENCES core_meetings(id),
    status               TEXT        NOT NULL DEFAULT 'received'
                                     CHECK (status IN ('received','processed','failed','unresolved')),
    error                TEXT,
    received_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at         TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS core_fireflies_events_idempotency
    ON core_fireflies_events (fireflies_meeting_id, event_type);
CREATE INDEX IF NOT EXISTS core_fireflies_events_status
    ON core_fireflies_events (status, received_at DESC);

-- 3. Explicit client mapping
CREATE TABLE IF NOT EXISTS core_fireflies_client_mapping (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    fireflies_meeting_id TEXT        NOT NULL UNIQUE,
    client_id            TEXT        NOT NULL,
    created_by           TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS core_fireflies_mapping_client
    ON core_fireflies_client_mapping (client_id);
