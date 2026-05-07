-- =============================================================================
-- Migration: 012_meta_campaign_levels
-- Extend meta_campaign_names cache to also store adsets and ads, so we can
-- resolve any Meta ID format that customers might send via UTM params
-- ({{campaign.id}}, {{adset.id}} or {{ad.id}}).
-- =============================================================================

ALTER TABLE public.meta_campaign_names
    ADD COLUMN IF NOT EXISTS level       TEXT DEFAULT 'campaign',
    ADD COLUMN IF NOT EXISTS parent_id   TEXT,
    ADD COLUMN IF NOT EXISTS parent_name TEXT;

-- Add the CHECK constraint only if it isn't already there
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'meta_campaign_names_level_check'
  ) THEN
    ALTER TABLE public.meta_campaign_names
      ADD CONSTRAINT meta_campaign_names_level_check
      CHECK (level IN ('campaign','adset','ad'));
  END IF;
END;
$$;
