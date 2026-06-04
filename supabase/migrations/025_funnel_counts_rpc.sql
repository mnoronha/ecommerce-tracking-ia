-- Funil de conversão para o relatório mensal: agrega tracking_events por etapa
-- em UMA query server-side (count="exact" por evento estourava timeout em 30d).
CREATE OR REPLACE FUNCTION funnel_counts(p_client uuid, p_start timestamptz, p_end timestamptz)
RETURNS TABLE(event_type text, n bigint)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT event_type, count(*)::bigint
  FROM tracking_events
  WHERE client_id = p_client
    AND created_at >= p_start AND created_at < p_end
    AND event_type IN ('pageview','view_product','add_to_cart','begin_checkout')
  GROUP BY event_type
$$;
