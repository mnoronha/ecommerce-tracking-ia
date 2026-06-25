-- Migration 031: AI Visibility cross-data analysis
-- Extends ai_insights type enum to include 'ai_visibility'
-- Removes the old CHECK and re-adds with the new value

ALTER TABLE public.ai_insights
  DROP CONSTRAINT IF EXISTS ai_insights_type_check;

ALTER TABLE public.ai_insights
  ADD CONSTRAINT ai_insights_type_check
    CHECK (type IN (
      'weekly_report',
      'anomaly',
      'recommendation',
      'pattern',
      'creative_analysis',
      'ai_visibility'
    ));

-- Index for fast retrieval of visibility insights per client
CREATE INDEX IF NOT EXISTS idx_ai_insights_client_type
  ON public.ai_insights(client_id, type, created_at DESC);
