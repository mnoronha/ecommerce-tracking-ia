-- Cache de métricas externas por cliente (Google Ads API, Meta Ads API).
-- Usado pelo dashboard da agência para clientes sem pedidos no banco
-- (ex: Enutri, Colab55 — não integram pedidos via webhook).
-- Atualizado diariamente pelo cron metrics_cache.

CREATE TABLE IF NOT EXISTS client_metrics_cache (
  client_id         uuid        NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  channel           text        NOT NULL,  -- 'google_ads' | 'meta_ads'
  orders            int         NOT NULL DEFAULT 0,
  revenue           numeric(14,2) NOT NULL DEFAULT 0,
  conversions       numeric(12,4) DEFAULT 0,
  conversions_value numeric(14,2) DEFAULT 0,
  refreshed_at      timestamptz NOT NULL DEFAULT NOW(),
  PRIMARY KEY (client_id, channel)
);

ALTER TABLE client_metrics_cache ENABLE ROW LEVEL SECURITY;

CREATE POLICY "agency members can read metrics cache"
  ON client_metrics_cache FOR SELECT
  USING (
    client_id IN (
      SELECT id FROM clients WHERE agency_id IN (
        SELECT agency_id FROM agency_members WHERE user_id = auth.uid()
      )
    )
  );

-- Atualiza a RPC get_agency_dashboard para usar o cache como fallback
-- quando a tabela orders não tem dados para o cliente.
CREATE OR REPLACE FUNCTION get_agency_dashboard(p_agency_id uuid, p_days int DEFAULT 30)
RETURNS TABLE (
  client_id       uuid,
  client_name     text,
  pixel_id        text,
  is_active       bool,
  revenue         numeric,
  orders_count    bigint,
  spend           numeric,
  roas            numeric,
  cpa             numeric,
  roas_goal       numeric,
  cpa_target      numeric,
  revenue_goal    numeric,
  tracking_last_at timestamptz,
  alert_critical  bigint,
  alert_warning   bigint,
  health_score    int
) LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  RETURN QUERY
  WITH
    client_list AS (
      SELECT id, name, pixel_id, is_active
      FROM clients
      WHERE agency_id = p_agency_id
    ),
    rev AS (
      SELECT client_id,
             COALESCE(SUM(total_price), 0) AS revenue,
             COUNT(*)                       AS orders_count
      FROM orders
      WHERE client_id IN (SELECT id FROM client_list)
        AND financial_status = 'paid'
        AND total_price > 0
        AND created_at >= NOW() - (p_days || ' days')::INTERVAL
      GROUP BY client_id
    ),
    -- Fallback para clientes sem pedidos no banco: usa cache de métricas externas
    cache AS (
      SELECT client_id,
             SUM(orders)  AS cached_orders,
             SUM(revenue) AS cached_revenue
      FROM client_metrics_cache
      WHERE client_id IN (SELECT id FROM client_list)
        AND refreshed_at >= NOW() - INTERVAL '48 hours'
      GROUP BY client_id
    ),
    sp AS (
      SELECT client_id,
             COALESCE(SUM(spend), 0) AS spend
      FROM ad_spend
      WHERE client_id IN (SELECT id FROM client_list)
        AND date >= CURRENT_DATE - p_days
      GROUP BY client_id
    ),
    client_goals_cte AS (
      SELECT DISTINCT ON (client_id)
             client_id,
             roas_goal,
             cpa_target,
             revenue_goal
      FROM goals
      WHERE client_id IN (SELECT id FROM client_list)
      ORDER BY client_id, month DESC
    ),
    tracking AS (
      SELECT client_id,
             MAX(created_at) AS tracking_last_at
      FROM tracking_events
      WHERE client_id IN (SELECT id FROM client_list)
        AND created_at >= NOW() - INTERVAL '7 days'
      GROUP BY client_id
    ),
    alts AS (
      SELECT client_id,
             COUNT(*) FILTER (WHERE severity = 'critical' AND NOT is_resolved) AS alert_critical,
             COUNT(*) FILTER (WHERE severity = 'warning'  AND NOT is_resolved) AS alert_warning
      FROM alerts
      WHERE client_id IN (SELECT id FROM client_list)
        AND NOT is_resolved
      GROUP BY client_id
    ),
    -- Mescla pedidos reais com cache externo
    effective AS (
      SELECT
        cl.id,
        -- Usa pedidos reais se existirem, senão usa cache
        CASE WHEN COALESCE(r.orders_count, 0) > 0
             THEN COALESCE(r.revenue, 0)
             ELSE COALESCE(c.cached_revenue, 0)
        END AS eff_revenue,
        CASE WHEN COALESCE(r.orders_count, 0) > 0
             THEN COALESCE(r.orders_count, 0)
             ELSE COALESCE(c.cached_orders, 0)
        END AS eff_orders
      FROM client_list cl
      LEFT JOIN rev   r ON r.client_id = cl.id
      LEFT JOIN cache c ON c.client_id = cl.id
    )
  SELECT
    cl.id                                                                      AS client_id,
    cl.name                                                                    AS client_name,
    cl.pixel_id,
    cl.is_active,
    e.eff_revenue                                                              AS revenue,
    e.eff_orders                                                               AS orders_count,
    COALESCE(s.spend, 0)                                                       AS spend,
    CASE WHEN COALESCE(s.spend, 0) > 0
         THEN ROUND((e.eff_revenue / s.spend)::NUMERIC, 2)
         ELSE NULL END                                                         AS roas,
    CASE WHEN COALESCE(e.eff_orders, 0) > 0 AND COALESCE(s.spend, 0) > 0
         THEN ROUND((s.spend / e.eff_orders)::NUMERIC, 2)
         ELSE NULL END                                                         AS cpa,
    g.roas_goal,
    g.cpa_target,
    g.revenue_goal,
    t.tracking_last_at,
    COALESCE(a.alert_critical, 0)                                              AS alert_critical,
    COALESCE(a.alert_warning, 0)                                               AS alert_warning,
    GREATEST(0, LEAST(100,
      CASE WHEN g.roas_goal IS NULL THEN 15
           WHEN COALESCE(s.spend, 0) = 0 THEN 10
           WHEN (e.eff_revenue / NULLIF(s.spend, 0)) >= g.roas_goal THEN 25
           ELSE ROUND(25 * (e.eff_revenue / NULLIF(s.spend, 0)) / g.roas_goal)
      END
      + CASE WHEN g.cpa_target IS NULL THEN 10
             WHEN COALESCE(e.eff_orders, 0) = 0 THEN 5
             WHEN (COALESCE(s.spend, 0) / NULLIF(e.eff_orders, 0)) <= g.cpa_target THEN 15
             ELSE GREATEST(0, ROUND(15 * (1 - ((COALESCE(s.spend, 0) / NULLIF(e.eff_orders, 0)) - g.cpa_target) / g.cpa_target)))
        END
      + CASE WHEN t.tracking_last_at IS NULL THEN 0
             WHEN t.tracking_last_at >= NOW() - INTERVAL '24 hours' THEN 20
             WHEN t.tracking_last_at >= NOW() - INTERVAL '48 hours' THEN 10
             ELSE 0
        END
      + CASE WHEN COALESCE(a.alert_critical, 0) = 0 THEN 15 ELSE 0 END
      + 25
    ))::INT                                                                    AS health_score
  FROM client_list cl
  LEFT JOIN effective         e ON e.id = cl.id
  LEFT JOIN sp                s ON s.client_id = cl.id
  LEFT JOIN client_goals_cte  g ON g.client_id = cl.id
  LEFT JOIN tracking          t ON t.client_id = cl.id
  LEFT JOIN alts              a ON a.client_id = cl.id
  ORDER BY e.eff_revenue DESC NULLS LAST;
END;
$$;
