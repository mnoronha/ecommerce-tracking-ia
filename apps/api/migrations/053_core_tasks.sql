-- Migration 053 — core_tasks (Phase 2 + Phase 3 Notion sync prep)
-- Applied: 2026-10-14 via Supabase MCP
CREATE TABLE IF NOT EXISTS core_tasks (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       TEXT        NOT NULL,
    title           TEXT        NOT NULL,
    description     TEXT,
    status          TEXT        NOT NULL DEFAULT 'OPEN'
                                CHECK (status IN ('OPEN','IN_PROGRESS','DONE','ARCHIVED','BLOCKED')),
    priority        TEXT        NOT NULL DEFAULT 'MEDIUM'
                                CHECK (priority IN ('LOW','MEDIUM','HIGH','URGENT')),
    owner           TEXT,
    due_date        DATE,
    source_type     TEXT        CHECK (source_type IN ('recommendation','action','alert','meeting','manual')),
    source_id       UUID,
    visibility_scope TEXT       NOT NULL DEFAULT 'AGENCY_ONLY'
                                CHECK (visibility_scope IN ('AGENCY_ONLY','CLIENT_VISIBLE')),
    external_task_id TEXT,
    external_system  TEXT       CHECK (external_system IN ('notion') OR external_system IS NULL),
    sync_state       TEXT       NOT NULL DEFAULT 'none'
                                CHECK (sync_state IN ('none','pending','synced','error')),
    last_synced_at   TIMESTAMPTZ,
    sync_error       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS core_tasks_client_status  ON core_tasks (client_id, status);
CREATE INDEX IF NOT EXISTS core_tasks_source         ON core_tasks (source_type, source_id);
CREATE INDEX IF NOT EXISTS core_tasks_external       ON core_tasks (external_task_id) WHERE external_task_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS core_tasks_due_date       ON core_tasks (client_id, due_date) WHERE due_date IS NOT NULL;
