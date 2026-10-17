-- Migration 070 — LGPD step 2: purge raw customer email/phone (DESTRUCTIVE)
--
-- Apply ONLY after:
--   1. migration 069 is applied and the hash-based code is deployed;
--   2. one week of normal operation (dashboards, audiences, first-purchase
--      flags and LGPD requests working on hashes);
--   3. a database backup exists.
--
-- After this, raw emails/phones no longer exist in the platform; customers are
-- identified only by email_hash / phone_hash and shown as email_masked.

UPDATE public.orders
SET email = NULL, phone = NULL
WHERE email IS NOT NULL OR phone IS NOT NULL;

UPDATE public.visitors
SET email = NULL, phone = NULL
WHERE email IS NOT NULL OR phone IS NOT NULL;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'attribution_cookies' AND column_name = 'email') THEN
    EXECUTE 'UPDATE public.attribution_cookies SET email = NULL WHERE email IS NOT NULL';
  END IF;
END $$;
