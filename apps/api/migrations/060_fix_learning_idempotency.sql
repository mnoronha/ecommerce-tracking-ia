-- Migration 060 — H4: fix learning candidate idempotency
--
-- Problem: migration 059 keyed uniqueness on (source_action_id, scope) only.
-- This collapses distinct outcomes from the same action into one candidate —
-- e.g., ROAS outcome (window=7d) and CPA outcome (window=30d) from the same
-- action would share a candidate, which is wrong.
--
-- Fix: add outcome_id to the key.  Two cases per source type:
--   a) outcome_id IS NOT NULL → canonical evidence key = (source, outcome_id, scope)
--   b) outcome_id IS NULL     → pre-measurement key = (source, scope) [unchanged]
--
-- Old indexes dropped; new indexes replace them.

-- Drop over-broad indexes from migration 059
DROP INDEX IF EXISTS core_lc_source_action_scope_uniq;
DROP INDEX IF EXISTS core_lc_source_rec_scope_uniq;

-- ── Action-sourced candidates ──────────────────────────────────────────────
-- One candidate per (action, outcome, scope) — distinct outcomes remain distinct
CREATE UNIQUE INDEX IF NOT EXISTS core_lc_action_outcome_scope_uniq
  ON core_learning_candidates(source_action_id, outcome_id, scope)
  WHERE source_action_id IS NOT NULL
    AND outcome_id       IS NOT NULL;

-- One pre-measurement candidate per (action, scope) when outcome not yet measured
CREATE UNIQUE INDEX IF NOT EXISTS core_lc_action_scope_no_outcome_uniq
  ON core_learning_candidates(source_action_id, scope)
  WHERE source_action_id IS NOT NULL
    AND outcome_id       IS NULL;

-- ── Recommendation-sourced candidates (no action link) ────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS core_lc_rec_outcome_scope_uniq
  ON core_learning_candidates(source_recommendation_id, outcome_id, scope)
  WHERE source_action_id        IS NULL
    AND source_recommendation_id IS NOT NULL
    AND outcome_id               IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS core_lc_rec_scope_no_outcome_uniq
  ON core_learning_candidates(source_recommendation_id, scope)
  WHERE source_action_id        IS NULL
    AND source_recommendation_id IS NOT NULL
    AND outcome_id               IS NULL;
