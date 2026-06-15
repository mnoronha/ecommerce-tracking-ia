-- Migration 029: AI Search Visibility — schema completo
-- Armazena dados de visibilidade da marca nas IAs (ChatGPT, Gemini, Perplexity, Claude)
-- Fonte v1: import manual de CSV exportado do Ubersuggest AI Search Visibility
-- Run via Supabase SQL Editor

-- 1. Marcas monitoradas (própria + competidores)
CREATE TABLE IF NOT EXISTS ai_visibility_brands (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id            UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  brand_name           TEXT NOT NULL,
  brand_aliases        TEXT[],
  website_url          TEXT,
  is_own_brand         BOOLEAN NOT NULL DEFAULT false,
  competitor_priority  INTEGER,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(client_id, brand_name)
);

-- 2. Prompts monitorados
CREATE TABLE IF NOT EXISTS ai_visibility_prompts (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id           UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  prompt_text         TEXT NOT NULL,
  category            TEXT,   -- 'recommendation' | 'comparison' | 'problem_solution' | 'alternative' | 'review'
  intent              TEXT,   -- 'high_intent' | 'mid_intent' | 'low_intent'
  is_active           BOOLEAN NOT NULL DEFAULT true,
  external_prompt_id  TEXT,   -- ID do prompt no Ubersuggest (reconciliação)
  notes               TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. Registro de cada import
CREATE TABLE IF NOT EXISTS ai_visibility_imports (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id        UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  imported_by      UUID,
  source           TEXT NOT NULL DEFAULT 'ubersuggest',
  source_type      TEXT NOT NULL DEFAULT 'csv_upload',   -- 'csv_upload' | 'api' | 'manual'
  period_start     DATE NOT NULL,
  period_end       DATE NOT NULL,
  file_name        TEXT,
  file_size_bytes  INTEGER,
  rows_processed   INTEGER NOT NULL DEFAULT 0,
  rows_skipped     INTEGER NOT NULL DEFAULT 0,
  errors           JSONB NOT NULL DEFAULT '[]'::jsonb,
  status           TEXT NOT NULL DEFAULT 'pending',      -- 'pending' | 'imported' | 'failed' | 'reverted'
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  imported_at      TIMESTAMPTZ
);

-- 4. Métricas por prompt × data × plataforma (granularidade central)
CREATE TABLE IF NOT EXISTS ai_visibility_metrics (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id                UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  prompt_id                UUID REFERENCES ai_visibility_prompts(id),
  date                     DATE NOT NULL,
  platform                 TEXT NOT NULL,   -- 'chatgpt' | 'gemini' | 'perplexity' | 'claude'
  own_brand_mentioned      BOOLEAN NOT NULL DEFAULT false,
  own_brand_position       INTEGER,
  own_brand_sentiment      TEXT,            -- 'positive' | 'neutral' | 'negative'
  own_brand_context        TEXT,
  total_brands_mentioned   INTEGER,
  response_includes_links  BOOLEAN,
  response_text            TEXT,
  import_id                UUID REFERENCES ai_visibility_imports(id),
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(client_id, prompt_id, date, platform)
);

-- 5. Menções de competidores por entrada de métricas
CREATE TABLE IF NOT EXISTS ai_visibility_competitor_mentions (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  metric_id  UUID NOT NULL REFERENCES ai_visibility_metrics(id) ON DELETE CASCADE,
  client_id  UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  brand_id   UUID REFERENCES ai_visibility_brands(id),
  brand_name TEXT NOT NULL,
  position   INTEGER,
  sentiment  TEXT,
  date       DATE NOT NULL,
  platform   TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 6. Resumos mensais recalculados após cada import
CREATE TABLE IF NOT EXISTS ai_visibility_monthly_summary (
  id                                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id                          UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  month                              DATE NOT NULL,    -- primeiro dia do mês
  platform                           TEXT,             -- NULL = consolidado todas plataformas
  total_prompts_run                  INTEGER,
  total_responses_analyzed           INTEGER,
  own_brand_mention_rate             DECIMAL,          -- 0 a 1
  own_brand_avg_position             DECIMAL,
  own_brand_positive_sentiment_rate  DECIMAL,          -- 0 a 1
  share_of_voice                     DECIMAL,          -- 0 a 1
  top_competitor_1                   TEXT,
  top_competitor_1_share             DECIMAL,
  top_competitor_2                   TEXT,
  top_competitor_2_share             DECIMAL,
  top_competitor_3                   TEXT,
  top_competitor_3_share             DECIMAL,
  visibility_index                   INTEGER,          -- 0-100
  trend_vs_previous_month            TEXT,             -- 'up' | 'stable' | 'down'
  trend_change_pct                   DECIMAL,
  created_at                         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(client_id, month, platform)
);

-- Índices de acesso frequente
CREATE INDEX IF NOT EXISTS idx_aiv_metrics_client_date    ON ai_visibility_metrics(client_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_aiv_metrics_prompt_date    ON ai_visibility_metrics(client_id, prompt_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_aiv_metrics_platform       ON ai_visibility_metrics(platform, date DESC);
CREATE INDEX IF NOT EXISTS idx_aiv_comp_client_date       ON ai_visibility_competitor_mentions(client_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_aiv_brands_client          ON ai_visibility_brands(client_id);
CREATE INDEX IF NOT EXISTS idx_aiv_imports_client         ON ai_visibility_imports(client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_aiv_summary_client_month   ON ai_visibility_monthly_summary(client_id, month DESC);

-- RLS — apenas usuários autenticados da agência
ALTER TABLE ai_visibility_brands               ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_visibility_prompts              ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_visibility_imports              ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_visibility_metrics              ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_visibility_competitor_mentions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_visibility_monthly_summary      ENABLE ROW LEVEL SECURITY;

CREATE POLICY agency_access ON ai_visibility_brands               FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_access ON ai_visibility_prompts              FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_access ON ai_visibility_imports              FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_access ON ai_visibility_metrics              FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_access ON ai_visibility_competitor_mentions  FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_access ON ai_visibility_monthly_summary      FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
