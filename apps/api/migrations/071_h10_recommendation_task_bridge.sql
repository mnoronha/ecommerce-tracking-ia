-- Migration 071 — H10 Core Bridge: recommendation→task idempotency + approval audit
-- Applied: 2026-10-20

-- Idempotency constraint: one task per (source_type, source_id).
-- Prevents duplicate tasks when approve is called more than once for the same
-- recommendation (e.g., Hermes retry after timeout, UI double-click).
CREATE UNIQUE INDEX IF NOT EXISTS core_tasks_source_unique
    ON core_tasks (source_type, source_id)
    WHERE source_id IS NOT NULL;

-- Approval audit table: preserves the full chain analysis→recommendation→approval→task.
-- Non-FK task_id intentionally (tasks can be soft-deleted; audit must survive).
CREATE TABLE IF NOT EXISTS core_recommendation_approvals (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id UUID        NOT NULL,
    task_id           UUID        NOT NULL,
    actor             TEXT        NOT NULL,
    reason            TEXT,
    approved_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS cra_recommendation ON core_recommendation_approvals (recommendation_id);
CREATE INDEX IF NOT EXISTS cra_task           ON core_recommendation_approvals (task_id);
