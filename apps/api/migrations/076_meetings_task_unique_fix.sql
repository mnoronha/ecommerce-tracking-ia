-- Migration 076 — Fix core_tasks unique index: allow multiple tasks per meeting
-- Applied: 2026-10-27
--
-- Migration 071 created UNIQUE (source_type, source_id) to enforce one task per
-- recommendation/action/alert source. Meetings legitimately produce multiple tasks
-- (one per commitment), so that index blocks the 2nd+ commitment inserts silently.
-- Recreate with meetings excluded, then add a meeting-specific dedup index.

DROP INDEX IF EXISTS core_tasks_source_unique;

-- Non-meeting sources: one task per source entity (recommendation, action, alert).
CREATE UNIQUE INDEX IF NOT EXISTS core_tasks_source_unique
    ON core_tasks (source_type, source_id)
    WHERE source_id IS NOT NULL
      AND source_type IS DISTINCT FROM 'meeting';

-- Meetings: one task per commitment title per meeting (prevents re-process duplicates).
CREATE UNIQUE INDEX IF NOT EXISTS core_tasks_meeting_commitment_unique
    ON core_tasks (source_type, source_id, title)
    WHERE source_type = 'meeting'
      AND source_id IS NOT NULL;
