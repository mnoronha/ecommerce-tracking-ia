-- =============================================================================
-- Migration: 008_unified_attribution
-- Cross-platform unified attribution — flagship differentiator vs Triple Whale.
--
-- Hoje cada plataforma se atribui crédito (Meta diz 10 conversões, Google diz 8,
-- Shopify diz 12 totais). Soma > realidade por overlap. Esta tabela armazena
-- crédito atribuído por touchpoint para cada order, calculado em múltiplos
-- modelos (last_click, first_click, linear, time_decay, position_based).
--
-- O motor lê visitors.utm_history (multi-touch capturado pelo pixel) e calcula
-- crédito proporcional. Dashboard mostra: "Meta diz X, Google diz Y, modelo
-- unificado diz Z" com overlap visível.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.order_attributions (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id          UUID        NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
    order_id           UUID        NOT NULL REFERENCES public.orders(id)  ON DELETE CASCADE,

    -- Touchpoint position in journey (0 = first, n-1 = last)
    touchpoint_index   INT         NOT NULL,
    total_touchpoints  INT         NOT NULL,

    -- Touchpoint identity
    source             TEXT,
    medium             TEXT,
    campaign           TEXT,
    platform           TEXT,        -- inferred: meta|google|tiktok|organic|direct
    touchpoint_at      TIMESTAMPTZ,

    -- Attribution model + credit
    model              TEXT        NOT NULL
        CHECK (model IN ('last_click','first_click','linear','time_decay','position_based')),
    credit             NUMERIC(6,5) NOT NULL CHECK (credit BETWEEN 0 AND 1),
    attributed_revenue NUMERIC(12,2), -- credit * order.total_price (denormalized for perf)

    computed_at        TIMESTAMPTZ DEFAULT now(),
    UNIQUE(order_id, touchpoint_index, model)
);

ALTER TABLE public.order_attributions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename  = 'order_attributions'
      AND policyname = 'order_attributions_read'
  ) THEN
    CREATE POLICY order_attributions_read ON public.order_attributions
      FOR SELECT USING (client_id IN (SELECT public.get_user_client_ids()));
  END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_order_attr_client_model
    ON public.order_attributions (client_id, model, computed_at DESC);
CREATE INDEX IF NOT EXISTS idx_order_attr_order
    ON public.order_attributions (order_id);
CREATE INDEX IF NOT EXISTS idx_order_attr_platform
    ON public.order_attributions (client_id, model, platform);
