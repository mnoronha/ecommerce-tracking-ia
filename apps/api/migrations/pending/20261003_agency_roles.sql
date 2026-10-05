-- ==============================================================
-- ETAPA 2 — Agency roles + client_id slug column
-- STATUS : PENDING — apply in Etapa 3 AFTER staging validation
-- TARGET : staging branch first, then production
--
-- DO NOT apply directly to production.
-- Validation checklist before applying to production:
--   [ ] Tested on staging branch (Supabase branch "staging")
--   [ ] All 42 existing tests still pass
--   [ ] Agency dashboard loads normally (smoke test 5 pages)
--   [ ] clients.client_id slugs manually populated for all 8 clients
--   [ ] client_users.client_id backfill verified (3 rows)
-- ==============================================================

BEGIN;

-- ── 1. Postgres application roles ────────────────────────────────────────────
-- These roles are NOT Supabase Auth roles — they are Postgres roles used
-- for schema-level permission grants.

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agency_admin') THEN
    CREATE ROLE agency_admin;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hermes_service') THEN
    CREATE ROLE hermes_service;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'client_viewer') THEN
    CREATE ROLE client_viewer;
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'readonly_analyst') THEN
    CREATE ROLE readonly_analyst;
  END IF;
END $$;


-- ── 2. Permission grants ──────────────────────────────────────────────────────

-- agency_admin: full access on public schema
GRANT USAGE ON SCHEMA public TO agency_admin;
GRANT ALL ON ALL TABLES IN SCHEMA public TO agency_admin;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO agency_admin;

-- hermes_service: global read; write permissions on intel_* tables added in Etapa 9
-- when those tables exist.
GRANT USAGE ON SCHEMA public TO hermes_service;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO hermes_service;

-- client_viewer: NO direct table access — Agency API projects all data.
-- RLS on client_users (portal_user_read_own) is the second-line defense.
GRANT USAGE ON SCHEMA public TO client_viewer;
GRANT SELECT ON client_users TO client_viewer;

-- readonly_analyst: read-only across the whole schema
GRANT USAGE ON SCHEMA public TO readonly_analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_analyst;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO readonly_analyst;


-- ── 3. Canonical client_id slug column on clients ────────────────────────────
-- client_id is the Agency API v1.1 canonical identifier (e.g. 'lk-sneakers').
-- It is NOT auto-derived from pixel_id — populate manually after applying this migration.

ALTER TABLE clients ADD COLUMN IF NOT EXISTS client_id text;
ALTER TABLE clients ADD CONSTRAINT clients_client_id_unique UNIQUE (client_id)
  NOT VALID;  -- NOT VALID so existing NULL rows don't block constraint creation
COMMENT ON COLUMN clients.client_id IS
  'Canonical slug for Agency API v1.1 (e.g. lk-sneakers). Populate manually.';

-- Manual population template (run separately after applying migration):
-- UPDATE clients SET client_id = 'lk-sneakers'           WHERE pixel_id = '<lk-pixel-id>';
-- UPDATE clients SET client_id = 'dipua'                 WHERE pixel_id = 'dipua-qe5p';
-- UPDATE clients SET client_id = 'enutri'                WHERE pixel_id = '<enutri-pixel-id>';
-- UPDATE clients SET client_id = 'clinica-tarcio-caetano' WHERE pixel_id = '<clinica-pixel-id>';
-- UPDATE clients SET client_id = 'spiti-auction'         WHERE pixel_id = '<spiti-pixel-id>';
-- UPDATE clients SET client_id = 'zipper-galeria'        WHERE pixel_id = '<zipper-pixel-id>';


-- ── 4. client_id column on client_users ─────────────────────────────────────
-- References clients.client_id. Populated via the backfill UPDATE below.

ALTER TABLE client_users ADD COLUMN IF NOT EXISTS client_id text;
COMMENT ON COLUMN client_users.client_id IS
  'Canonical slug. Backfill after populating clients.client_id.';

-- Backfill (run after clients.client_id is populated):
-- UPDATE client_users cu
-- SET client_id = c.client_id
-- FROM clients c
-- WHERE c.pixel_id = cu.pixel_id
--   AND c.client_id IS NOT NULL;


COMMIT;
