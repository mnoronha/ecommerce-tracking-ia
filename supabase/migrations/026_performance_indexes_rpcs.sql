-- Performance: índices faltando + RPCs para agregações que eram feitas em Python

-- ── Índices ───────────────────────────────────────────────────────────────────

-- Lookup por email em orders (is_first_purchase, cohort retention, retroactive lookup)
CREATE INDEX IF NOT EXISTS idx_orders_email
  ON public.orders(client_id, email)
  WHERE email IS NOT NULL;

-- Google Ads pending (retry cron + health monitor)
CREATE INDEX IF NOT EXISTS idx_orders_google_pending
  ON public.orders(client_id, google_sent)
  WHERE google_sent = false;

-- Filtro por UTM source no dashboard de atribuição
CREATE INDEX IF NOT EXISTS idx_orders_utm_source
  ON public.orders(client_id, utm_source, created_at DESC)
  WHERE utm_source IS NOT NULL;


-- ── RPC: live today stats ──────────────────────────────────────────────────────
-- Substitui o endpoint live_stats que carregava TODOS os pedidos do dia em Python
-- para somar receita. Agora uma única query retorna os 4 aggregados necessários.
CREATE OR REPLACE FUNCTION public.live_today_stats(
  p_client_id  UUID,
  p_day_start  TIMESTAMPTZ,
  p_last_hour  TIMESTAMPTZ
) RETURNS TABLE(
  today_revenue      NUMERIC,
  today_orders       BIGINT,
  last_hour_revenue  NUMERIC,
  last_hour_orders   BIGINT
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
AS $$
  SELECT
    COALESCE(SUM(total_price), 0)                                         AS today_revenue,
    COUNT(*)                                                               AS today_orders,
    COALESCE(SUM(total_price) FILTER (WHERE created_at >= p_last_hour), 0) AS last_hour_revenue,
    COUNT(*)            FILTER (WHERE created_at >= p_last_hour)           AS last_hour_orders
  FROM public.orders
  WHERE client_id    = p_client_id
    AND financial_status = 'paid'
    AND total_price  > 0
    AND created_at   >= p_day_start;
$$;


-- ── RPC: orders revenue sum ────────────────────────────────────────────────────
-- Usado pela página de Pedidos para exibir receita total do filtro atual sem
-- carregar todas as linhas em memória no cliente.
CREATE OR REPLACE FUNCTION public.orders_revenue_sum(
  p_client_id    UUID,
  p_gte          TIMESTAMPTZ DEFAULT NULL,
  p_lte          TIMESTAMPTZ DEFAULT NULL,
  p_status       TEXT        DEFAULT NULL,
  p_email_search TEXT        DEFAULT NULL
) RETURNS NUMERIC
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
AS $$
  SELECT COALESCE(SUM(total_price), 0)
  FROM public.orders
  WHERE client_id = p_client_id
    AND total_price > 0
    AND (p_gte    IS NULL OR created_at >= p_gte)
    AND (p_lte    IS NULL OR created_at <= p_lte)
    AND (p_status IS NULL OR financial_status = p_status)
    AND (p_email_search IS NULL OR email ILIKE '%' || p_email_search || '%');
$$;
