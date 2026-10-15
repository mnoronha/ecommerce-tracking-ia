-- Migration 058 — H4 Core Closure: action traceability + outcome idempotency
--
-- 1. core_actions gains action_event_id (the approving decision event = "decision_id")
--    and task_id (optional link back to the task that spawned this action).
-- 2. core_outcomes gains a partial unique index so the same
--    (action_event_id, evaluation_window_days) pair can never produce two rows.
-- 3. evaluation_window_days on core_outcomes is guarded with IF NOT EXISTS
--    in case an earlier migration already added it.

-- Traceability columns
ALTER TABLE core_actions
  ADD COLUMN IF NOT EXISTS action_event_id UUID,
  ADD COLUMN IF NOT EXISTS task_id        UUID;

-- Lookup indexes (partial — no index bloat for NULL-only rows)
CREATE INDEX IF NOT EXISTS core_actions_action_event_id_idx
  ON core_actions(action_event_id)
  WHERE action_event_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS core_actions_task_id_idx
  ON core_actions(task_id)
  WHERE task_id IS NOT NULL;

-- evaluation_window_days on core_outcomes (may already exist)
ALTER TABLE core_outcomes
  ADD COLUMN IF NOT EXISTS evaluation_window_days INT;

-- Outcome idempotency: one evaluation per (event × window).
-- NULLs stay unrestricted — legacy rows are not affected.
CREATE UNIQUE INDEX IF NOT EXISTS core_outcomes_action_event_window_uniq
  ON core_outcomes(action_event_id, evaluation_window_days)
  WHERE action_event_id IS NOT NULL;
