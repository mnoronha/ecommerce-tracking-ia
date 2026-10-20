-- Migration 072 — Shopify app (Dev Dashboard, custom distribution) per client.
-- Since 2026-01-01 Shopify no longer lets stores create custom apps in the admin.
-- Each client store gets a Noro app in the Dev Dashboard; the merchant installs it
-- via the custom-distribution link and the authorization code grant stores an
-- offline (non-expiring) Admin API token in clients.shopify_access_token.
-- Additive only.
ALTER TABLE public.clients
  ADD COLUMN IF NOT EXISTS shopify_app_client_id     text,
  ADD COLUMN IF NOT EXISTS shopify_app_client_secret text,          -- Fernet-encrypted (crypto.SECRET_FIELDS)
  ADD COLUMN IF NOT EXISTS shopify_app_scopes        text,
  ADD COLUMN IF NOT EXISTS shopify_app_installed_at  timestamptz;
