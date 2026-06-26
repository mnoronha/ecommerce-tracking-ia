-- Migration 032: DataForSEO integration for AI Visibility automatic collection
-- Adds config, usage log, raw response tables + extends existing tables

-- 1. DataForSEO config per client (one row per client, upsert on activation)
CREATE TABLE IF NOT EXISTS dataforseo_configs (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id                UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  is_enabled               BOOLEAN NOT NULL DEFAULT false,
  llms_to_monitor          TEXT[] NOT NULL DEFAULT ARRAY['chatgpt','gemini','perplexity'],
  collection_frequency     TEXT NOT NULL DEFAULT 'weekly',  -- 'weekly' | 'biweekly' | 'monthly'
  location_code            INTEGER NOT NULL DEFAULT 2076,   -- 2076 = Brazil
  language_code            TEXT NOT NULL DEFAULT 'pt',
  budget_monthly_usd       DECIMAL(10,2) NOT NULL DEFAULT 50.00,
  budget_used_this_month   DECIMAL(10,4) NOT NULL DEFAULT 0,
  budget_reset_at          TIMESTAMPTZ,
  last_collection_at       TIMESTAMPTZ,
  last_collection_status   TEXT,  -- 'ok' | 'error' | 'budget_exceeded'
  notes                    TEXT,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(client_id)
);

-- 2. Per-request usage log for cost tracking and auditing
CREATE TABLE IF NOT EXISTS dataforseo_usage_log (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id         UUID REFERENCES clients(id) ON DELETE SET NULL,
  collection_run_id UUID REFERENCES ai_visibility_imports(id) ON DELETE SET NULL,
  endpoint          TEXT NOT NULL,  -- 'llm_mentions' | 'ai_keyword_data' | 'balance'
  request_units     INTEGER NOT NULL DEFAULT 1,
  cost_usd          DECIMAL(10,6),
  api_status_code   INTEGER,
  api_task_id       TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. Raw LLM responses for audit trail and future reprocessing
CREATE TABLE IF NOT EXISTS llm_responses_raw (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id         UUID REFERENCES clients(id) ON DELETE SET NULL,
  collection_run_id UUID REFERENCES ai_visibility_imports(id) ON DELETE SET NULL,
  metric_id         UUID REFERENCES ai_visibility_metrics(id) ON DELETE SET NULL,
  llm_platform      TEXT NOT NULL,
  prompt_text       TEXT NOT NULL,
  response_text     TEXT,
  response_date     DATE,
  api_source        TEXT NOT NULL DEFAULT 'dataforseo',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. Extend ai_visibility_imports with DataForSEO-specific fields
ALTER TABLE ai_visibility_imports
  ADD COLUMN IF NOT EXISTS collection_cost_usd DECIMAL(10,4),
  ADD COLUMN IF NOT EXISTS llms_queried        TEXT[],
  ADD COLUMN IF NOT EXISTS api_task_ids        JSONB;

-- 5. Extend ai_visibility_metrics with richer data from DataForSEO
ALTER TABLE ai_visibility_metrics
  ADD COLUMN IF NOT EXISTS context_snippets    TEXT[],
  ADD COLUMN IF NOT EXISTS cited_sources       JSONB,
  ADD COLUMN IF NOT EXISTS response_word_count INTEGER;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_dfs_configs_client   ON dataforseo_configs(client_id);
CREATE INDEX IF NOT EXISTS idx_dfs_usage_client     ON dataforseo_usage_log(client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dfs_usage_run        ON dataforseo_usage_log(collection_run_id);
CREATE INDEX IF NOT EXISTS idx_llm_raw_client_date  ON llm_responses_raw(client_id, response_date DESC);
CREATE INDEX IF NOT EXISTS idx_llm_raw_run          ON llm_responses_raw(collection_run_id);

-- RLS
ALTER TABLE dataforseo_configs   ENABLE ROW LEVEL SECURITY;
ALTER TABLE dataforseo_usage_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm_responses_raw    ENABLE ROW LEVEL SECURITY;

CREATE POLICY agency_access ON dataforseo_configs   FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_access ON dataforseo_usage_log FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_access ON llm_responses_raw    FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
