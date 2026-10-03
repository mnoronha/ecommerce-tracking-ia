-- Immutable report history and serialized latest-version promotion.
-- Canonical contracts are stored verbatim; no KPI is calculated here.
BEGIN;

CREATE TABLE IF NOT EXISTS public.agency_report_contracts_backup_20261003
AS SELECT * FROM public.agency_report_contracts;
ALTER TABLE public.agency_report_contracts_backup_20261003 ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.agency_report_contracts_backup_20261003 FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.agency_report_contracts_backup_20261003 TO service_role;

CREATE TABLE IF NOT EXISTS public.agency_report_contract_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_slug text NOT NULL,
    report_type text NOT NULL CHECK (report_type IN ('weekly', 'monthly')),
    period_start date NOT NULL,
    period_end date NOT NULL,
    source_run_id text NOT NULL,
    generated_at timestamptz NOT NULL,
    contract jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_slug, report_type, period_start, period_end, source_run_id)
);
ALTER TABLE public.agency_report_contract_versions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.agency_report_contract_versions FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT, INSERT ON public.agency_report_contract_versions TO service_role;

INSERT INTO public.agency_report_contract_versions
    (client_slug, report_type, period_start, period_end, source_run_id, generated_at, contract)
SELECT client_slug, report_type, period_start, period_end, source_run_id, generated_at, contract
FROM public.agency_report_contracts
WHERE source_run_id IS NOT NULL AND generated_at IS NOT NULL
ON CONFLICT (client_slug, report_type, period_start, period_end, source_run_id) DO NOTHING;

CREATE OR REPLACE FUNCTION public.agency_store_report_contract(p_row jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
DECLARE
    incoming public.agency_report_contracts%ROWTYPE;
    previous public.agency_report_contracts%ROWTYPE;
    historical jsonb;
    disposition text;
BEGIN
    incoming := jsonb_populate_record(NULL::public.agency_report_contracts, p_row);
    IF incoming.client_slug NOT IN ('lk-sneakers','dipua','enutri','clinica-tarcio-caetano','spiti-auction','zipper-galeria')
       OR incoming.report_type NOT IN ('weekly','monthly')
       OR incoming.source_run_id IS NULL OR incoming.generated_at IS NULL
       OR incoming.contract IS NULL OR incoming.period_start IS NULL OR incoming.period_end IS NULL
       OR incoming.period_start > incoming.period_end
       OR incoming.client_slug IS DISTINCT FROM incoming.contract #>> '{report,client_slug}'
       OR incoming.client_slug IS DISTINCT FROM incoming.contract #>> '{client,slug}'
       OR incoming.report_type IS DISTINCT FROM incoming.contract #>> '{report,type}'
       OR incoming.source_run_id IS DISTINCT FROM incoming.contract #>> '{provenance,source_run_id}'
       OR incoming.generated_at IS DISTINCT FROM (incoming.contract #>> '{provenance,generated_at}')::timestamptz
       OR incoming.period_start IS DISTINCT FROM (incoming.contract #>> '{report,period,start}')::date
       OR incoming.period_end IS DISTINCT FROM (incoming.contract #>> '{report,period,end}')::date THEN
        RETURN jsonb_build_object('status','conflict','reason','CANONICAL_METADATA_MISMATCH');
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(concat_ws('|',incoming.client_slug,incoming.report_type,incoming.period_start,incoming.period_end),0));
    SELECT contract INTO historical FROM public.agency_report_contract_versions
    WHERE client_slug=incoming.client_slug AND report_type=incoming.report_type
      AND period_start=incoming.period_start AND period_end=incoming.period_end
      AND source_run_id=incoming.source_run_id;
    IF FOUND AND historical IS DISTINCT FROM incoming.contract THEN
        RETURN jsonb_build_object('status','conflict','reason','SOURCE_RUN_EVIDENCE_IMMUTABLE');
    END IF;
    SELECT * INTO previous FROM public.agency_report_contracts
    WHERE client_slug=incoming.client_slug AND report_type=incoming.report_type
      AND period_start=incoming.period_start AND period_end=incoming.period_end FOR UPDATE;
    IF FOUND THEN
        IF previous.source_run_id=incoming.source_run_id AND previous.contract IS DISTINCT FROM incoming.contract THEN
            RETURN jsonb_build_object('status','conflict','reason','SOURCE_RUN_EVIDENCE_IMMUTABLE');
        END IF;
        IF previous.generated_at IS NULL THEN
            RETURN jsonb_build_object('status','conflict','reason','LATEST_GENERATION_TIME_MISSING');
        END IF;
        IF previous.generated_at=incoming.generated_at AND previous.contract IS DISTINCT FROM incoming.contract THEN
            RETURN jsonb_build_object('status','conflict','reason','GENERATION_TIME_CONFLICT');
        END IF;
    END IF;

    INSERT INTO public.agency_report_contract_versions
      (client_slug,report_type,period_start,period_end,source_run_id,generated_at,contract)
    VALUES (incoming.client_slug,incoming.report_type,incoming.period_start,incoming.period_end,incoming.source_run_id,incoming.generated_at,incoming.contract)
    ON CONFLICT (client_slug,report_type,period_start,period_end,source_run_id) DO NOTHING;

    IF previous.id IS NOT NULL AND previous.generated_at > incoming.generated_at THEN
        disposition := 'ARCHIVED';
    ELSIF previous.id IS NOT NULL AND previous.contract = incoming.contract THEN
        disposition := 'REPLAY';
    ELSE
        INSERT INTO public.agency_report_contracts
          (client_slug,report_type,business_model,period_start,period_end,comparison_period_start,comparison_period_end,schema_version,source_run_id,generated_at,contract)
        VALUES (incoming.client_slug,incoming.report_type,incoming.business_model,incoming.period_start,incoming.period_end,incoming.comparison_period_start,incoming.comparison_period_end,incoming.schema_version,incoming.source_run_id,incoming.generated_at,incoming.contract)
        ON CONFLICT (client_slug,report_type,period_start,period_end) DO UPDATE SET
          business_model=EXCLUDED.business_model,comparison_period_start=EXCLUDED.comparison_period_start,
          comparison_period_end=EXCLUDED.comparison_period_end,schema_version=EXCLUDED.schema_version,
          source_run_id=EXCLUDED.source_run_id,generated_at=EXCLUDED.generated_at,contract=EXCLUDED.contract;
        disposition := 'LATEST';
    END IF;
    RETURN jsonb_build_object('status','accepted','disposition',disposition);
END;
$$;
REVOKE ALL ON FUNCTION public.agency_store_report_contract(jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.agency_store_report_contract(jsonb) TO service_role;
COMMIT;
