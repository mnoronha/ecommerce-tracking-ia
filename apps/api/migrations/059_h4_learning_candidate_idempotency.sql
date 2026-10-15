-- Migration 059 — H4 Core Closure: learning candidate idempotency + outcome link
--
-- 1. core_learning_candidates gains outcome_id — the outcome whose observation
--    motivated this candidate (traceability: outcome → learning).
-- 2. Two partial unique indexes enforce that Hermes cannot submit duplicate
--    learning candidates for the same source evidence:
--    a) source_action_id + scope  (preferred key when an action is the source)
--    b) source_recommendation_id + scope  (fallback when no action is involved)
--    NULLs in the WHERE clause ensure legacy rows remain untouched.

-- Outcome traceability link
ALTER TABLE core_learning_candidates
  ADD COLUMN IF NOT EXISTS outcome_id UUID;

CREATE INDEX IF NOT EXISTS core_lc_outcome_id_idx
  ON core_learning_candidates(outcome_id)
  WHERE outcome_id IS NOT NULL;

-- Idempotency: one candidate per (action × scope)
CREATE UNIQUE INDEX IF NOT EXISTS core_lc_source_action_scope_uniq
  ON core_learning_candidates(source_action_id, scope)
  WHERE source_action_id IS NOT NULL;

-- Idempotency: one candidate per (recommendation × scope) when no action link
CREATE UNIQUE INDEX IF NOT EXISTS core_lc_source_rec_scope_uniq
  ON core_learning_candidates(source_recommendation_id, scope)
  WHERE source_action_id IS NULL
    AND source_recommendation_id IS NOT NULL;
