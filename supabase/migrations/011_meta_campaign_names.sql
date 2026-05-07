-- =============================================================================
-- Migration: 011_meta_campaign_names
-- Cache of Meta Ads campaign id → name. UTM params from Meta often arrive as
-- raw IDs (120210118442) rather than human names (e.g. "Pareto.Vendas
-- [Masculino]") because URL parameters in the ads use {{campaign.id}}. We
-- resolve them via the Meta Marketing API and persist the mapping so the
-- /journey screens can display names instead of opaque numbers.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.meta_campaign_names (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
    campaign_id     TEXT NOT NULL,
    name            TEXT NOT NULL,
    -- Level identifies whether this row is the campaign, the adset or the ad.
    -- Lets the UI show "Anúncio X (na campanha Y)" instead of "X" alone.
    level           TEXT NOT NULL DEFAULT 'campaign'
                    CHECK (level IN ('campaign','adset','ad')),
    parent_id       TEXT,           -- for adsets: campaign_id; for ads: adset_id
    parent_name     TEXT,            -- denormalized for cheap reads
    status          TEXT,             -- ACTIVE / PAUSED / DELETED
    objective       TEXT,
    updated_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (client_id, campaign_id)
);

CREATE INDEX IF NOT EXISTS idx_meta_campaign_names_client
    ON public.meta_campaign_names (client_id);

ALTER TABLE public.meta_campaign_names ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_policies WHERE tablename = 'meta_campaign_names' AND policyname = 'meta_campaign_names_read'
  ) THEN
    CREATE POLICY meta_campaign_names_read ON public.meta_campaign_names
      FOR SELECT USING (client_id IN (SELECT public.get_user_client_ids()));
  END IF;
END;
$$;
