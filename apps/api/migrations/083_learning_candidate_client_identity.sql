BEGIN;

-- Resolve existing slug FKs through the registry before replacing the FK.
CREATE FUNCTION pg_temp.learning_client_uuid(legacy_slug text)
RETURNS uuid LANGUAGE plpgsql STABLE STRICT AS $$
DECLARE
    canonical_id uuid;
BEGIN
    SELECT id INTO STRICT canonical_id
    FROM public.clients WHERE client_id = legacy_slug;
    RETURN canonical_id;
END;
$$;

ALTER TABLE public.core_learning_candidates
    DROP CONSTRAINT core_learning_candidates_client_id_fkey;

ALTER TABLE public.core_learning_candidates
    ALTER COLUMN client_id TYPE uuid USING pg_temp.learning_client_uuid(client_id);

ALTER TABLE public.core_learning_candidates
    ADD CONSTRAINT core_learning_candidates_client_id_fkey
        FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE RESTRICT;

COMMIT;
