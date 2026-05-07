-- =============================================================================
-- Migration: 010_device_country
-- Adds shipping_country on orders and device_type on tracking_events so the
-- dashboard can filter by either dimension. Both are denormalized for query
-- speed (vs reading from JSONB on every dashboard hit).
-- =============================================================================

ALTER TABLE public.orders
    ADD COLUMN IF NOT EXISTS shipping_country  TEXT,
    ADD COLUMN IF NOT EXISTS shipping_state    TEXT,
    ADD COLUMN IF NOT EXISTS shipping_city     TEXT;

CREATE INDEX IF NOT EXISTS idx_orders_country
    ON public.orders (client_id, shipping_country)
    WHERE shipping_country IS NOT NULL;

ALTER TABLE public.tracking_events
    ADD COLUMN IF NOT EXISTS device_type TEXT;  -- desktop | mobile | tablet | bot | unknown

CREATE INDEX IF NOT EXISTS idx_tracking_events_device
    ON public.tracking_events (client_id, device_type)
    WHERE device_type IS NOT NULL;
