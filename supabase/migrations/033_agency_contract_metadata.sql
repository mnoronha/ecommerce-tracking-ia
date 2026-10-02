-- Preserve the canonical JSON; repair metadata used by report queries.
-- Do not delete colliding rows. A collision requires human inspection.
UPDATE public.agency_report_contracts AS report
SET report_type = report.contract #>> '{report,type}',
    source_run_id = report.contract #>> '{provenance,source_run_id}',
    generated_at = (report.contract #>> '{provenance,generated_at}')::timestamptz
WHERE report.contract->>'schema_version' = 'norolabs-report-contract-v1'
  AND report.contract #>> '{report,type}' IN ('weekly', 'monthly')
  AND report.report_type IS DISTINCT FROM report.contract #>> '{report,type}'
  AND NOT EXISTS (
      SELECT 1 FROM public.agency_report_contracts AS other
      WHERE other.client_slug = report.client_slug
        AND other.report_type = report.contract #>> '{report,type}'
        AND other.period_start = report.period_start
        AND other.period_end = report.period_end
        AND other.id <> report.id
  );
