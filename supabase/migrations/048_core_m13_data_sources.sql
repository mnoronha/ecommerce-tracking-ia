-- M13: core_data_sources — estado de cada fonte de dados por cliente
-- Additive only. RLS_BOOTSTRAP: service_role apenas.
-- Rollback: DROP TABLE IF EXISTS public.core_data_sources;
BEGIN;

CREATE TABLE IF NOT EXISTS public.core_data_sources (
  id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id            text        NOT NULL
                         REFERENCES public.clients(client_id)
                         ON DELETE RESTRICT,
  source_key           text        NOT NULL,
  source_system        text        NOT NULL,
  semantic_domain      text        NOT NULL,
  source_state         text        NOT NULL DEFAULT 'MISSING'
                         CHECK (source_state IN (
                           'READY','PARTIAL','STALE','NO_DATA','MISSING',
                           'ACCESS_MISSING','PERMISSION_DENIED',
                           'NOT_CONTRACTED','NOT_APPLICABLE','ERROR'
                         )),
  last_attempt_at      timestamptz,
  last_data_at         timestamptz,
  last_validated_at    timestamptz,
  last_reconciled_at   timestamptz,
  reconciliation_state text        CHECK (reconciliation_state IN (
                           'OK','RECONCILIATION_MISMATCH'
                         )),
  last_error           text,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT core_data_sources_client_key_uq
    UNIQUE (client_id, source_key)
);

ALTER TABLE public.core_data_sources ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_data_sources FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.core_data_sources TO service_role;

CREATE INDEX IF NOT EXISTS core_data_sources_client_idx
  ON public.core_data_sources (client_id);

COMMENT ON TABLE public.core_data_sources IS
  'Estado de cada fonte de dados por cliente. '
  'source_system: "meta_ads"|"google_ads"|"ga4"|"shopify". '
  'semantic_domain: "ADS"|"JOURNEY"|"BUSINESS"|"CONVERSION". '
  'source_state: SourceState enum (READY/PARTIAL/STALE/NO_DATA/MISSING/…). '
  'RLS_BOOTSTRAP: service_role apenas.';

COMMIT;
