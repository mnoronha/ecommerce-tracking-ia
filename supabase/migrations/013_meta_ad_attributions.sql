-- =============================================================================
-- Migration: 013_meta_ad_attributions
-- Daily snapshot of Meta-reported attribution per ad. We pull this from the
-- Marketing API insights endpoint with actions=purchase and use it to:
--   1. Show "Attributed by Meta" lens in /journey alongside our server-side
--      attribution. The discrepancy itself is a key insight.
--   2. Probabilistic match: when an order has no UTM but the visitor came
--      from Meta (fbp/fbc present), we look up which ad had the most
--      clicks/spend that day and attribute with a confidence score.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.meta_ad_attributions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
    date            DATE NOT NULL,
    ad_id           TEXT NOT NULL,
    ad_name         TEXT,
    adset_id        TEXT,
    adset_name      TEXT,
    campaign_id     TEXT,
    campaign_name   TEXT,
    -- Spend / reach / clicks
    spend           NUMERIC(12,2) DEFAULT 0,
    impressions     BIGINT       DEFAULT 0,
    clicks          BIGINT       DEFAULT 0,
    -- Meta-reported conversions (post-attribution-window)
    purchases       INT          DEFAULT 0,
    purchase_value  NUMERIC(14,2) DEFAULT 0,
    -- Misc
    raw             JSONB,        -- the original /insights row, for audit
    synced_at       TIMESTAMPTZ DEFAULT now(),
    UNIQUE (client_id, date, ad_id)
);

CREATE INDEX IF NOT EXISTS idx_meta_attr_client_date
    ON public.meta_ad_attributions (client_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_meta_attr_campaign
    ON public.meta_ad_attributions (client_id, campaign_id, date DESC);

ALTER TABLE public.meta_ad_attributions ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_policies WHERE tablename = 'meta_ad_attributions' AND policyname = 'meta_ad_attributions_read'
  ) THEN
    CREATE POLICY meta_ad_attributions_read ON public.meta_ad_attributions
      FOR SELECT USING (client_id IN (SELECT public.get_user_client_ids()));
  END IF;
END;
$$;

-- Add a column to orders that tracks probabilistic match (Phase 3)
ALTER TABLE public.orders
    ADD COLUMN IF NOT EXISTS probable_meta_ad_id          TEXT,
    ADD COLUMN IF NOT EXISTS probable_meta_campaign_id    TEXT,
    ADD COLUMN IF NOT EXISTS probable_meta_campaign_name  TEXT,
    ADD COLUMN IF NOT EXISTS probable_meta_confidence     NUMERIC(4,3);  -- 0.000 to 1.000

CREATE INDEX IF NOT EXISTS idx_orders_probable_campaign
    ON public.orders (client_id, probable_meta_campaign_id)
    WHERE probable_meta_campaign_id IS NOT NULL;
