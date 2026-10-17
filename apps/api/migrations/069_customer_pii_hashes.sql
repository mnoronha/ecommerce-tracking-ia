-- Migration 069 — LGPD: identify customers by hash (step 1 of 2, non-destructive)
--
-- Adds email_hash / phone_hash / email_masked to orders and visitors (and
-- email_hash to attribution_cookies), backfills them from the raw columns and
-- indexes them. The application (this PR) reads/writes ONLY the hash columns
-- and stops storing raw email/phone (STORE_RAW_CUSTOMER_PII unset).
--
-- Normalization MUST match app/core/pii.py:
--   email_hash  = sha256(lower(trim(email)))       (only when it contains '@')
--   phone_hash  = sha256(digits only)
--   email_masked = first 2 chars of local part || '***@' || domain
--
-- APPLY THIS BEFORE DEPLOYING THE CODE. Safe to re-run.
-- Step 2 (purge raw values) is migrations/pending/070_purge_raw_customer_pii.sql.

ALTER TABLE public.orders
    ADD COLUMN IF NOT EXISTS email_hash   TEXT,
    ADD COLUMN IF NOT EXISTS phone_hash   TEXT,
    ADD COLUMN IF NOT EXISTS email_masked TEXT;

ALTER TABLE public.visitors
    ADD COLUMN IF NOT EXISTS email_hash   TEXT,
    ADD COLUMN IF NOT EXISTS phone_hash   TEXT,
    ADD COLUMN IF NOT EXISTS email_masked TEXT;

ALTER TABLE IF EXISTS public.attribution_cookies
    ADD COLUMN IF NOT EXISTS email_hash TEXT;

-- ── Backfill ────────────────────────────────────────────────────────────────
UPDATE public.orders
SET email_hash   = encode(sha256(convert_to(lower(trim(email)), 'UTF8')), 'hex'),
    email_masked = left(split_part(lower(trim(email)), '@', 1), 2) || '***@' || split_part(lower(trim(email)), '@', 2)
WHERE email IS NOT NULL AND position('@' in email) > 0 AND email_hash IS NULL;

UPDATE public.orders
SET phone_hash = encode(sha256(convert_to(regexp_replace(phone, '\D', '', 'g'), 'UTF8')), 'hex')
WHERE phone IS NOT NULL AND regexp_replace(phone, '\D', '', 'g') <> '' AND phone_hash IS NULL;

UPDATE public.visitors
SET email_hash   = encode(sha256(convert_to(lower(trim(email)), 'UTF8')), 'hex'),
    email_masked = left(split_part(lower(trim(email)), '@', 1), 2) || '***@' || split_part(lower(trim(email)), '@', 2)
WHERE email IS NOT NULL AND position('@' in email) > 0 AND email_hash IS NULL;

UPDATE public.visitors
SET phone_hash = encode(sha256(convert_to(regexp_replace(phone, '\D', '', 'g'), 'UTF8')), 'hex')
WHERE phone IS NOT NULL AND regexp_replace(phone, '\D', '', 'g') <> '' AND phone_hash IS NULL;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'attribution_cookies' AND column_name = 'email') THEN
    EXECUTE $q$
      UPDATE public.attribution_cookies
      SET email_hash = encode(sha256(convert_to(lower(trim(email)), 'UTF8')), 'hex')
      WHERE email IS NOT NULL AND position('@' in email) > 0 AND email_hash IS NULL
    $q$;
  END IF;
END $$;

-- ── Indexes for lookups that used raw email ─────────────────────────────────
CREATE INDEX IF NOT EXISTS orders_client_email_hash_idx
    ON public.orders (client_id, email_hash) WHERE email_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS visitors_client_email_hash_idx
    ON public.visitors (client_id, email_hash) WHERE email_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS attribution_cookies_client_email_hash_idx
    ON public.attribution_cookies (client_id, email_hash) WHERE email_hash IS NOT NULL;
