-- M01: clients — identidade canônica + metadados Agency API v1.1
-- Additive only. Colunas nullable. RLS legado herdado automaticamente.
-- Rollback: ALTER TABLE public.clients DROP COLUMN IF EXISTS
--   client_id, timezone, currency, country, business_model;
BEGIN;

ALTER TABLE public.clients
  ADD COLUMN IF NOT EXISTS client_id      text UNIQUE,
  ADD COLUMN IF NOT EXISTS timezone       text,
  ADD COLUMN IF NOT EXISTS currency       text,
  ADD COLUMN IF NOT EXISTS country        text,
  ADD COLUMN IF NOT EXISTS business_model text
    CHECK (business_model IN
      ('ecommerce','lead_generation','local_lead_generation','auction'));

COMMENT ON COLUMN public.clients.client_id IS
  'Slug canônico Agency API v1.1. Imutável após definido. '
  'NÃO deriva automaticamente de pixel_id — valores podem diferir. '
  'Ex: lk-sneakers, dipua, enutri, clinica-tarcio-caetano.';
COMMENT ON COLUMN public.clients.timezone IS
  'IANA timezone. Ex: America/Sao_Paulo. Definido no onboarding Core.';
COMMENT ON COLUMN public.clients.currency IS
  'ISO 4217. Ex: BRL. Definido no onboarding Core.';
COMMENT ON COLUMN public.clients.country IS
  'ISO 3166-1 alpha-2. Ex: BR. Definido no onboarding Core.';
COMMENT ON COLUMN public.clients.business_model IS
  'BusinessModel enum: ecommerce | lead_generation | local_lead_generation | auction.';

COMMIT;
