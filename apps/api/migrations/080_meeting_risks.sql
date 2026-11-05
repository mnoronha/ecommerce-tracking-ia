-- Migration 080: Add risks column and analysis metadata to core_meeting_summaries

ALTER TABLE core_meeting_summaries
  ADD COLUMN IF NOT EXISTS risks jsonb DEFAULT '[]'::jsonb;

COMMENT ON COLUMN core_meeting_summaries.risks IS
  'Risks extracted from meeting transcript. Each entry: {risk, severity, mitigation_suggested}.';
