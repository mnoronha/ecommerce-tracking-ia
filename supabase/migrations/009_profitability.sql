-- =============================================================================
-- Migration: 009_profitability
-- Adds COGS-per-SKU + computed gross_profit on orders for ROAS-of-margin
-- analytics. DTC ecommerce lives or dies by margin, not revenue.
-- =============================================================================

-- ── product_costs: cost_price per SKU/product_id ──────────────────────────────
CREATE TABLE IF NOT EXISTS public.product_costs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id   UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
    -- Either sku or platform_product_id is required (we match on both)
    sku                  TEXT,
    platform_product_id  TEXT,
    product_name         TEXT,
    cost_price           NUMERIC(12,2) NOT NULL CHECK (cost_price >= 0),
    currency             TEXT DEFAULT 'BRL',
    updated_at           TIMESTAMPTZ DEFAULT now(),
    -- One row per (client, sku) and one row per (client, platform_product_id).
    -- Unique partial indexes let either one or the other identifier be set.
    CHECK (sku IS NOT NULL OR platform_product_id IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_product_costs_client_sku
    ON public.product_costs (client_id, sku)
    WHERE sku IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_product_costs_client_pid
    ON public.product_costs (client_id, platform_product_id)
    WHERE platform_product_id IS NOT NULL;

ALTER TABLE public.product_costs ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_policies WHERE tablename = 'product_costs' AND policyname = 'product_costs_read'
  ) THEN
    CREATE POLICY product_costs_read ON public.product_costs
      FOR SELECT USING (client_id IN (SELECT public.get_user_client_ids()));
  END IF;
END;
$$;

-- ── orders: cached margin columns (denormalized for dashboard perf) ───────────
ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS gross_profit NUMERIC(12,2);
ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS cogs_total   NUMERIC(12,2);
ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS margin_pct   NUMERIC(5,2);

-- ── clients: monthly goals for pacing widget ──────────────────────────────────
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS monthly_revenue_goal NUMERIC(14,2);
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS monthly_ad_spend_goal NUMERIC(14,2);
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS target_roas NUMERIC(6,2);

-- ── order_items table to persist line-items (we lose them today after webhook) -
-- Used to recompute margin when COGS change.
CREATE TABLE IF NOT EXISTS public.order_items (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id             UUID NOT NULL REFERENCES public.orders(id) ON DELETE CASCADE,
    client_id            UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
    platform_product_id  TEXT,
    sku                  TEXT,
    name                 TEXT,
    quantity             INT  NOT NULL DEFAULT 1,
    unit_price           NUMERIC(12,2),
    line_total           NUMERIC(12,2),
    cost_price_snapshot  NUMERIC(12,2), -- snapshot at order time so historical margins don't drift
    created_at           TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_order_items_order  ON public.order_items (order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_client ON public.order_items (client_id);
CREATE INDEX IF NOT EXISTS idx_order_items_sku    ON public.order_items (client_id, sku) WHERE sku IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_order_items_pid    ON public.order_items (client_id, platform_product_id) WHERE platform_product_id IS NOT NULL;

ALTER TABLE public.order_items ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_policies WHERE tablename = 'order_items' AND policyname = 'order_items_read'
  ) THEN
    CREATE POLICY order_items_read ON public.order_items
      FOR SELECT USING (client_id IN (SELECT public.get_user_client_ids()));
  END IF;
END;
$$;
