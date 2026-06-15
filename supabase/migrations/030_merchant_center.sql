-- Migration 030: Google Merchant Center — schema completo
-- Coleta diária via Content API for Shopping v2.1
-- Scope OAuth: https://www.googleapis.com/auth/content
-- Run via Supabase SQL Editor

-- Novas colunas no clients (mesmo padrão do Google Ads)
ALTER TABLE clients
  ADD COLUMN IF NOT EXISTS merchant_center_id          TEXT,
  ADD COLUMN IF NOT EXISTS merchant_center_refresh_token TEXT;

-- 1. Snapshot diário do catálogo
CREATE TABLE IF NOT EXISTS merchant_products (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id             UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  snapshot_date         DATE NOT NULL,
  product_id            TEXT NOT NULL,   -- ID do produto no Google (online:pt-BR:BR:{offer_id})
  offer_id              TEXT,
  channel               TEXT DEFAULT 'online',
  language              TEXT DEFAULT 'pt-BR',
  feed_label            TEXT,
  title                 TEXT,
  description           TEXT,
  link                  TEXT,
  image_link            TEXT,
  brand                 TEXT,
  gtin                  TEXT,
  mpn                   TEXT,
  product_type          TEXT,
  google_product_category TEXT,
  price                 DECIMAL,
  sale_price            DECIMAL,
  currency              TEXT,
  availability          TEXT,            -- 'in_stock' | 'out_of_stock' | 'preorder' | 'backorder'
  custom_label_0        TEXT,
  custom_label_1        TEXT,
  custom_label_2        TEXT,
  custom_label_3        TEXT,
  custom_label_4        TEXT,
  shipping_country      TEXT,
  raw_data              JSONB,
  collected_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(client_id, product_id, snapshot_date)
);

-- 2. Status de aprovação por produto × destino
CREATE TABLE IF NOT EXISTS merchant_product_statuses (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id             UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  product_id            TEXT NOT NULL,
  snapshot_date         DATE NOT NULL,
  destination           TEXT NOT NULL,   -- 'shopping_ads' | 'free_listings' | 'shopping_actions'
  approval_status       TEXT,            -- 'approved' | 'pending' | 'disapproved' | 'undeclared'
  approved_countries    TEXT[],
  disapproved_countries TEXT[],
  pending_countries     TEXT[],
  servability           TEXT,            -- 'eligible' | 'not_eligible'
  collected_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(client_id, product_id, destination, snapshot_date)
);

-- 3. Issues por produto (warnings e errors)
CREATE TABLE IF NOT EXISTS merchant_product_issues (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id         UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  product_id        TEXT NOT NULL,
  snapshot_date     DATE NOT NULL,
  code              TEXT NOT NULL,       -- ex: 'missing_value', 'image_link_broken'
  severity          TEXT NOT NULL,       -- 'error' | 'warning' | 'info'
  description       TEXT,
  attribute_name    TEXT,
  destination       TEXT,
  documentation_url TEXT,
  affected_countries TEXT[],
  resolution        TEXT,
  first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at       TIMESTAMPTZ,
  is_resolved       BOOLEAN NOT NULL DEFAULT false,
  collected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(client_id, product_id, code, destination, snapshot_date)
);

-- 4. Price competitiveness (benchmark vs mercado)
CREATE TABLE IF NOT EXISTS merchant_price_benchmarks (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id            UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  product_id           TEXT NOT NULL,
  snapshot_date        DATE NOT NULL,
  product_price        DECIMAL NOT NULL,
  benchmark_price      DECIMAL,
  price_difference_pct DECIMAL,          -- % acima/abaixo da média (positivo = mais caro)
  competitive_status   TEXT,             -- 'competitive' | 'above_market' | 'below_market'
  country              TEXT DEFAULT 'BR',
  collected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(client_id, product_id, country, snapshot_date)
);

-- 5. Best Sellers do Google Shopping
CREATE TABLE IF NOT EXISTS merchant_best_sellers (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id        UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  report_date      DATE NOT NULL,
  report_period    TEXT DEFAULT 'weekly',  -- 'weekly' | 'monthly'
  category_id      TEXT,
  category_name    TEXT,
  country          TEXT DEFAULT 'BR',
  product_id       TEXT,
  product_title    TEXT,
  brand            TEXT,
  rank             INTEGER NOT NULL,
  previous_rank    INTEGER,
  rank_change      INTEGER,
  is_own_product   BOOLEAN NOT NULL DEFAULT false,
  popularity       DECIMAL,
  price_range_min  DECIMAL,
  price_range_max  DECIMAL,
  collected_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 6. Performance de produtos (Shopping ads + free listings)
CREATE TABLE IF NOT EXISTS merchant_product_performance (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id        UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  product_id       TEXT NOT NULL,
  date             DATE NOT NULL,
  destination      TEXT NOT NULL,         -- 'shopping_ads' | 'free_listings'
  impressions      INTEGER NOT NULL DEFAULT 0,
  clicks           INTEGER NOT NULL DEFAULT 0,
  ctr              DECIMAL,
  click_share      DECIMAL,
  conversions      DECIMAL NOT NULL DEFAULT 0,
  conversion_value DECIMAL NOT NULL DEFAULT 0,
  cost             DECIMAL NOT NULL DEFAULT 0,
  collected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(client_id, product_id, destination, date)
);

-- 7. Snapshot diário da saúde do feed (agregado)
CREATE TABLE IF NOT EXISTS merchant_feed_health_snapshots (
  id                           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id                    UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  snapshot_date                DATE NOT NULL,
  total_products               INTEGER,
  approved_products            INTEGER,
  pending_products             INTEGER,
  disapproved_products         INTEGER,
  out_of_stock_products        INTEGER,
  products_with_warnings       INTEGER,
  products_with_errors         INTEGER,
  total_warnings               INTEGER,
  total_errors                 INTEGER,
  unique_issue_codes           INTEGER,
  top_issue_codes              JSONB,     -- [{code, count}, ...]
  products_above_market_price  INTEGER,
  products_below_market_price  INTEGER,
  avg_price_difference_pct     DECIMAL,
  feed_health_score            INTEGER,   -- 0-100
  collected_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(client_id, snapshot_date)
);

-- 8. Sugestões de otimização geradas pela IA
CREATE TABLE IF NOT EXISTS merchant_optimization_suggestions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id       UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  product_id      TEXT,
  type            TEXT NOT NULL,  -- 'title_optimization' | 'description_optimization' | 'image_quality' | 'price_adjustment' | 'attribute_completion'
  severity        TEXT,           -- 'high_impact' | 'medium_impact' | 'low_impact'
  current_value   TEXT,
  suggested_value TEXT,
  reasoning       TEXT,
  estimated_impact TEXT,
  status          TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'applied' | 'dismissed' | 'expired'
  applied_at      TIMESTAMPTZ,
  generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Índices
CREATE INDEX IF NOT EXISTS idx_mp_client_date        ON merchant_products(client_id, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_mp_client_product      ON merchant_products(client_id, product_id);
CREATE INDEX IF NOT EXISTS idx_mps_client_date        ON merchant_product_statuses(client_id, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_mps_status             ON merchant_product_statuses(client_id, approval_status, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_mpi_client_date        ON merchant_product_issues(client_id, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_mpi_severity           ON merchant_product_issues(client_id, severity, is_resolved);
CREATE INDEX IF NOT EXISTS idx_mpb_client_product     ON merchant_price_benchmarks(client_id, product_id, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_mbs_client_date        ON merchant_best_sellers(client_id, report_date DESC, category_id);
CREATE INDEX IF NOT EXISTS idx_mpp_client_date        ON merchant_product_performance(client_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_mfh_client_date        ON merchant_feed_health_snapshots(client_id, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_mos_client_status      ON merchant_optimization_suggestions(client_id, status);

-- RLS
ALTER TABLE merchant_products                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE merchant_product_statuses           ENABLE ROW LEVEL SECURITY;
ALTER TABLE merchant_product_issues             ENABLE ROW LEVEL SECURITY;
ALTER TABLE merchant_price_benchmarks           ENABLE ROW LEVEL SECURITY;
ALTER TABLE merchant_best_sellers               ENABLE ROW LEVEL SECURITY;
ALTER TABLE merchant_product_performance        ENABLE ROW LEVEL SECURITY;
ALTER TABLE merchant_feed_health_snapshots      ENABLE ROW LEVEL SECURITY;
ALTER TABLE merchant_optimization_suggestions   ENABLE ROW LEVEL SECURITY;

CREATE POLICY agency_access ON merchant_products                 FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_access ON merchant_product_statuses         FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_access ON merchant_product_issues           FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_access ON merchant_price_benchmarks         FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_access ON merchant_best_sellers             FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_access ON merchant_product_performance      FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_access ON merchant_feed_health_snapshots    FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_access ON merchant_optimization_suggestions FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
