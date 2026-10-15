-- Migration 065 — Sync core_learning_candidates.scope CHECK with Python LearningScope enum
--
-- DB had: ('CLIENT', 'BUSINESS_MODEL', 'AGENCY')
-- Enum has: CLIENT_ONLY, CLIENT (legacy), BUSINESS_MODEL, AGENCY_WIDE_CANDIDATE, AGENCY (legacy)
-- Table was empty so constraint replacement is safe.

ALTER TABLE core_learning_candidates
  DROP CONSTRAINT core_learning_candidates_scope_check;

ALTER TABLE core_learning_candidates
  ADD CONSTRAINT core_learning_candidates_scope_check
  CHECK (scope = ANY(ARRAY[
    'CLIENT_ONLY',
    'CLIENT',
    'BUSINESS_MODEL',
    'AGENCY_WIDE_CANDIDATE',
    'AGENCY'
  ]));
