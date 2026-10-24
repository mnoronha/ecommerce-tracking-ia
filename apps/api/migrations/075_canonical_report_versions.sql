-- Canonical report versions and delivery receipts. No sends, no legacy table changes.
BEGIN;
CREATE TABLE public.core_canonical_report_records (
 entity_key text PRIMARY KEY,
 revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
 document jsonb NOT NULL CHECK (jsonb_typeof(document) = 'object'),
 updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.core_canonical_report_records ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_canonical_report_records FROM PUBLIC,anon,authenticated;
GRANT SELECT ON public.core_canonical_report_records TO service_role;
CREATE FUNCTION public.canonical_report_cas(p_key text,p_revision bigint,p_document jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE current_revision bigint; current_document jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended(p_key,0));
 SELECT revision,document INTO current_revision,current_document FROM public.core_canonical_report_records WHERE entity_key=p_key;
 IF current_revision IS NULL THEN
  IF p_revision IS NOT NULL THEN RETURN false; END IF;
  INSERT INTO public.core_canonical_report_records(entity_key,document) VALUES(p_key,p_document);
 ELSE
  IF p_revision IS DISTINCT FROM current_revision THEN RETURN false; END IF;
  IF p_key LIKE 'report:%' AND (current_document - ARRAY['status','approved_at','approved_by','approval_hash','published_at']) IS DISTINCT FROM (p_document - ARRAY['status','approved_at','approved_by','approval_hash','published_at']) THEN RAISE EXCEPTION 'IMMUTABLE_REPORT_VERSION'; END IF;
  IF p_key LIKE 'delivery:%' AND (current_document - ARRAY['status','attempt','sent_at','provider_message_id','error']) IS DISTINCT FROM (p_document - ARRAY['status','attempt','sent_at','provider_message_id','error']) THEN RAISE EXCEPTION 'IMMUTABLE_DELIVERY_BINDING'; END IF;
  UPDATE public.core_canonical_report_records SET document=p_document,revision=revision+1,updated_at=now() WHERE entity_key=p_key;
 END IF;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.canonical_report_cas(text,bigint,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.canonical_report_cas(text,bigint,jsonb) TO service_role;
COMMIT;
