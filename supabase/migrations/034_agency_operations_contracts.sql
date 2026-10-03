-- Operational evidence is produced by Hermes and stored verbatim. Each run is
-- retained; late arrivals cannot replace a newer snapshot or erase its history.
BEGIN;
CREATE TABLE IF NOT EXISTS public.agency_operations_contracts (
  client_slug text NOT NULL CHECK (client_slug IN ('lk-sneakers','dipua','enutri','clinica-tarcio-caetano','spiti-auction','zipper-galeria')),
  source_run_id text NOT NULL,
  client_id uuid NOT NULL REFERENCES public.clients(id),
  generated_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  schema_version text NOT NULL CHECK (schema_version = 'norolabs-operations-contract-v1'),
  contract_hash text NOT NULL CHECK (contract_hash ~ '^[a-f0-9]{64}$'),
  contract jsonb NOT NULL,
  PRIMARY KEY (client_slug, source_run_id),
  CHECK (contract->>'client_slug' = client_slug),
  CHECK (contract->>'source_run_id' = source_run_id)
);
CREATE INDEX IF NOT EXISTS agency_operations_latest_idx ON public.agency_operations_contracts (client_slug, generated_at DESC, received_at DESC);
ALTER TABLE public.agency_operations_contracts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.agency_operations_contracts FROM anon, authenticated;
GRANT SELECT ON public.agency_operations_contracts TO authenticated;
GRANT SELECT, INSERT ON public.agency_operations_contracts TO service_role;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='agency_operations_contracts' AND policyname='agency_operations_read') THEN
    CREATE POLICY agency_operations_read ON public.agency_operations_contracts FOR SELECT TO authenticated
      USING (client_id IN (SELECT public.get_user_client_ids()));
  END IF;
END $$;
COMMIT;
