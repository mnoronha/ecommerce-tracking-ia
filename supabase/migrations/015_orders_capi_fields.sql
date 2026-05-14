-- Migration 015: Store buyer PII and browser identifiers on orders
-- Needed for rich CAPI retry path (capi_retry rebuilds events from orders table)
-- Run via Supabase SQL Editor

ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS first_name   TEXT,
  ADD COLUMN IF NOT EXISTS last_name    TEXT,
  ADD COLUMN IF NOT EXISTS zip_code     TEXT,
  ADD COLUMN IF NOT EXISTS browser_ip   TEXT,
  ADD COLUMN IF NOT EXISTS browser_ua   TEXT;

COMMENT ON COLUMN orders.first_name  IS 'Buyer first name (hashed before Meta CAPI send)';
COMMENT ON COLUMN orders.last_name   IS 'Buyer last name (hashed before Meta CAPI send)';
COMMENT ON COLUMN orders.zip_code    IS 'Shipping zip code for Meta CAPI EMQ';
COMMENT ON COLUMN orders.browser_ip  IS 'Customer browser IP from Shopify order webhook (browser_ip field)';
COMMENT ON COLUMN orders.browser_ua  IS 'Customer user agent from Shopify client_details.user_agent';
