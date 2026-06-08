-- Shopify API sync: campos para controlar clientes que usam polling
-- ao invés de webhooks para importar pedidos.

ALTER TABLE public.clients
  ADD COLUMN IF NOT EXISTS shopify_sync_enabled  BOOLEAN      DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS shopify_last_sync_at  TIMESTAMPTZ  DEFAULT NULL;

COMMENT ON COLUMN public.clients.shopify_sync_enabled IS
  'Quando true, pedidos são importados via Shopify Admin API (polling) em vez de webhooks. '
  'Adequado para clientes que não instalam o tracking mas querem dados de receita no dashboard.';

COMMENT ON COLUMN public.clients.shopify_last_sync_at IS
  'Timestamp da última sincronização bem-sucedida via API. '
  'O próximo sync busca apenas pedidos updated_at > este valor.';
