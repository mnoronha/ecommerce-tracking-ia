-- Migration 076: Expand mutable fields in canonical_report_cas to include
-- supersession audit trail fields added by Reports.transition(..., "SUPERSEDED").
--
-- The original function allowed mutating only:
--   status, approved_at, approved_by, approval_hash, published_at
--
-- Transitioning to SUPERSEDED also writes:
--   superseded_at, superseded_by_actor, superseded_by_version
--
-- Without these in the exclusion list the immutability check fires with
-- IMMUTABLE_REPORT_VERSION even though the core identity of the report
-- (client, period, engine, version, content, artifact_hash) is unchanged.

CREATE OR REPLACE FUNCTION public.canonical_report_cas(p_key text, p_revision bigint, p_document jsonb)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE current_revision bigint; current_document jsonb;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(p_key,0));
  SELECT revision,document INTO current_revision,current_document
    FROM public.core_canonical_report_records WHERE entity_key=p_key;
  IF current_revision IS NULL THEN
    IF p_revision IS NOT NULL THEN RETURN false; END IF;
    INSERT INTO public.core_canonical_report_records(entity_key,document) VALUES(p_key,p_document);
  ELSE
    IF p_revision IS DISTINCT FROM current_revision THEN RETURN false; END IF;
    IF p_key LIKE 'report:%' AND
       (current_document - ARRAY[
         'status','approved_at','approved_by','approval_hash','published_at',
         'superseded_at','superseded_by_actor','superseded_by_version'
       ]) IS DISTINCT FROM
       (p_document - ARRAY[
         'status','approved_at','approved_by','approval_hash','published_at',
         'superseded_at','superseded_by_actor','superseded_by_version'
       ])
    THEN RAISE EXCEPTION 'IMMUTABLE_REPORT_VERSION'; END IF;
    IF p_key LIKE 'delivery:%' AND
       (current_document - ARRAY['status','attempt','sent_at','provider_message_id','error']) IS DISTINCT FROM
       (p_document - ARRAY['status','attempt','sent_at','provider_message_id','error'])
    THEN RAISE EXCEPTION 'IMMUTABLE_DELIVERY_BINDING'; END IF;
    UPDATE public.core_canonical_report_records
      SET document=p_document, revision=revision+1, updated_at=now()
      WHERE entity_key=p_key;
  END IF;
  RETURN true;
END $function$;
