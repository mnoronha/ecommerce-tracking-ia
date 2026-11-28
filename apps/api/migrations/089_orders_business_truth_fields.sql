-- Migration 089: Business Truth columns on public.orders & idempotent refunds index (PRD §6 / R06)
--
-- Purely additive and 100% backwards-compatible:
--   1. Adds `order_source_name` (Shopify `source_name`: web, pos, shopify_draft_order, etc.),
--      `paid_at` (payment confirmation timestamp), and `cancelled_at` (cancellation timestamp)
--      to `public.orders`.
--   2. Adds supporting indexes for Business Truth period queries (paid_at, order_source_name,
--      cancelled_at) without mutating or deleting historical rows.
--   3. Adds a partial unique index on `public.refunds(client_id, order_id, platform_refund_id)`
--      so webhook retries and Shopify sync never create duplicate refund rows.

ALTER TABLE public.orders
    ADD COLUMN IF NOT EXISTS order_source_name text,
    ADD COLUMN IF NOT EXISTS paid_at           timestamptz,
    ADD COLUMN IF NOT EXISTS cancelled_at      timestamptz;

CREATE INDEX IF NOT EXISTS idx_orders_client_paid_at
    ON public.orders (client_id, paid_at)
    WHERE paid_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_orders_client_source_name
    ON public.orders (client_id, order_source_name)
    WHERE order_source_name IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_orders_client_cancelled_at
    ON public.orders (client_id, cancelled_at)
    WHERE cancelled_at IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_refunds_client_order_platform_id
    ON public.refunds (client_id, order_id, platform_refund_id)
    WHERE platform_refund_id IS NOT NULL;
