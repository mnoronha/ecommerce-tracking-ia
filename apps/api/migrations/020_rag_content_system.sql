-- Migration 020: RAG + Content Production System
-- Aplica via Supabase SQL Editor

-- ── pgvector ──────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS vector;

-- ── RAG: Knowledge Base ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rag_knowledge_bases (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE UNIQUE,
  brand_voice TEXT,
  brand_dos TEXT[],
  brand_donts TEXT[],
  forbidden_terms TEXT[],
  preferred_terms JSONB,
  preferred_generation_model TEXT DEFAULT 'claude-sonnet-4-6',
  preferred_factcheck_model TEXT DEFAULT 'gpt-4o',
  temperature DECIMAL DEFAULT 0.7,
  total_documents INTEGER DEFAULT 0,
  total_chunks INTEGER DEFAULT 0,
  last_reindexed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  knowledge_base_id UUID REFERENCES rag_knowledge_bases(id) ON DELETE CASCADE,
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  description TEXT,
  category TEXT,
  source_type TEXT DEFAULT 'upload',
  source_url TEXT,
  file_path TEXT,
  file_size_bytes INTEGER,
  file_mime_type TEXT,
  raw_text TEXT,
  word_count INTEGER,
  is_active BOOLEAN DEFAULT true,
  priority INTEGER DEFAULT 5,
  tags TEXT[],
  processing_status TEXT DEFAULT 'pending',
  processing_error TEXT,
  processed_at TIMESTAMPTZ,
  version INTEGER DEFAULT 1,
  superseded_by UUID REFERENCES rag_documents(id),
  uploaded_by UUID REFERENCES agency_members(id),
  uploaded_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID REFERENCES rag_documents(id) ON DELETE CASCADE,
  knowledge_base_id UUID REFERENCES rag_knowledge_bases(id) ON DELETE CASCADE,
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
  chunk_text TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  total_chunks_in_doc INTEGER,
  section_title TEXT,
  page_number INTEGER,
  embedding vector(1024),
  embedding_model TEXT DEFAULT 'voyage-3-large',
  retrieval_count INTEGER DEFAULT 0,
  last_retrieved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS rag_chunks_embedding_idx ON rag_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_client ON rag_chunks(client_id);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_kb ON rag_chunks(knowledge_base_id);
CREATE INDEX IF NOT EXISTS idx_rag_documents_client ON rag_documents(client_id, is_active);
CREATE INDEX IF NOT EXISTS idx_rag_documents_category ON rag_documents(client_id, category, is_active);

-- ── Content: Pautas ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS content_pautas (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
  month DATE NOT NULL,
  status TEXT DEFAULT 'draft',
  total_pieces_planned INTEGER,
  total_pieces_completed INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  created_by UUID REFERENCES agency_members(id),
  approved_at TIMESTAMPTZ,
  approved_by UUID REFERENCES agency_members(id)
);

-- ── Content: Briefings ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS content_briefings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
  pauta_id UUID REFERENCES content_pautas(id) ON DELETE SET NULL,
  working_title TEXT NOT NULL,
  content_type TEXT NOT NULL,
  target_query TEXT,
  target_keywords TEXT[],
  target_audience TEXT,
  products_to_mention TEXT[],
  competitors_to_cite TEXT[],
  required_length TEXT DEFAULT 'medium',
  required_structure TEXT,
  tone_override TEXT,
  additional_instructions TEXT,
  source TEXT DEFAULT 'manual',
  source_data JSONB,
  priority TEXT DEFAULT 'medium',
  due_date DATE,
  status TEXT DEFAULT 'briefed',
  created_by UUID REFERENCES agency_members(id),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- ── Content: Pieces + Versions ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS content_pieces (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  briefing_id UUID REFERENCES content_briefings(id) ON DELETE CASCADE,
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
  final_title TEXT,
  meta_description TEXT,
  slug TEXT,
  url_published TEXT,
  current_version INTEGER DEFAULT 1,
  schema_type TEXT,
  schema_data JSONB,
  published_at TIMESTAMPTZ,
  published_by UUID REFERENCES agency_members(id),
  status TEXT DEFAULT 'draft',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS content_piece_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  piece_id UUID REFERENCES content_pieces(id) ON DELETE CASCADE,
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
  version_number INTEGER NOT NULL,
  version_type TEXT NOT NULL,
  title TEXT,
  body_markdown TEXT NOT NULL,
  body_html TEXT,
  word_count INTEGER,
  generation_model TEXT,
  generation_prompt TEXT,
  generation_temperature DECIMAL,
  rag_chunks_used UUID[],
  tokens_input INTEGER,
  tokens_output INTEGER,
  generation_cost_usd DECIMAL,
  generation_duration_ms INTEGER,
  edited_by UUID REFERENCES agency_members(id),
  edit_notes TEXT,
  diff_from_previous TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(piece_id, version_number)
);

-- ── Content: Fact-checks ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS content_factchecks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  piece_id UUID REFERENCES content_pieces(id) ON DELETE CASCADE,
  version_id UUID REFERENCES content_piece_versions(id),
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
  factcheck_model TEXT,
  overall_confidence TEXT,
  facts_to_verify JSONB,
  issues_found JSONB,
  recommendation TEXT,
  tokens_used INTEGER,
  cost_usd DECIMAL,
  reviewed_by_human BOOLEAN DEFAULT false,
  reviewed_at TIMESTAMPTZ,
  reviewed_by UUID REFERENCES agency_members(id),
  human_resolution_notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- ── Content: Performance + Approvals ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS content_piece_performance (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  piece_id UUID REFERENCES content_pieces(id) ON DELETE CASCADE,
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
  snapshot_date DATE NOT NULL,
  impressions INTEGER DEFAULT 0,
  clicks INTEGER DEFAULT 0,
  avg_position DECIMAL,
  sessions INTEGER DEFAULT 0,
  pageviews INTEGER DEFAULT 0,
  avg_time_on_page INTEGER,
  bounce_rate DECIMAL,
  ai_referred_sessions INTEGER DEFAULT 0,
  conversions_attributed INTEGER DEFAULT 0,
  revenue_attributed DECIMAL DEFAULT 0,
  appeared_in_ai_responses INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(piece_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS content_approvals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  piece_id UUID REFERENCES content_pieces(id) ON DELETE CASCADE,
  version_id UUID REFERENCES content_piece_versions(id),
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
  sent_to_email TEXT,
  sent_at TIMESTAMPTZ,
  sent_by UUID REFERENCES agency_members(id),
  approval_link_token TEXT UNIQUE,
  deadline TIMESTAMPTZ,
  auto_approve_on_deadline BOOLEAN DEFAULT true,
  status TEXT DEFAULT 'pending',
  responded_at TIMESTAMPTZ,
  feedback TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- ── AI Config + Usage Log ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_model_configs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope TEXT NOT NULL DEFAULT 'global',
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
  content_type TEXT,
  task TEXT NOT NULL,
  provider TEXT NOT NULL,
  model_id TEXT NOT NULL,
  temperature DECIMAL,
  max_tokens INTEGER,
  is_active BOOLEAN DEFAULT true,
  fallback_config_id UUID REFERENCES ai_model_configs(id),
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ai_usage_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID REFERENCES clients(id),
  task TEXT NOT NULL,
  related_entity_type TEXT,
  related_entity_id UUID,
  provider TEXT NOT NULL,
  model_id TEXT NOT NULL,
  tokens_input INTEGER,
  tokens_output INTEGER,
  cost_usd DECIMAL,
  duration_ms INTEGER,
  was_successful BOOLEAN DEFAULT true,
  error_message TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_content_briefings_client ON content_briefings(client_id, status);
CREATE INDEX IF NOT EXISTS idx_content_pieces_client ON content_pieces(client_id, status);
CREATE INDEX IF NOT EXISTS idx_content_versions_piece ON content_piece_versions(piece_id, version_number);
CREATE INDEX IF NOT EXISTS idx_content_performance_piece ON content_piece_performance(piece_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_content_approvals_token ON content_approvals(approval_link_token);
CREATE INDEX IF NOT EXISTS idx_ai_usage_client_date ON ai_usage_log(client_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_usage_task ON ai_usage_log(task, created_at);

-- ── pgvector similarity search function ───────────────────────────────────────
CREATE OR REPLACE FUNCTION match_rag_chunks(
  query_embedding vector(1024),
  p_client_id uuid,
  match_threshold float DEFAULT 0.4,
  match_count int DEFAULT 15,
  p_categories text[] DEFAULT NULL
)
RETURNS TABLE (
  id uuid,
  chunk_text text,
  section_title text,
  document_id uuid,
  document_title text,
  document_category text,
  similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    c.id,
    c.chunk_text,
    c.section_title,
    c.document_id,
    d.title AS document_title,
    d.category AS document_category,
    1 - (c.embedding <=> query_embedding) AS similarity
  FROM rag_chunks c
  INNER JOIN rag_documents d ON c.document_id = d.id
  WHERE c.client_id = p_client_id
    AND d.is_active = true
    AND (p_categories IS NULL OR d.category = ANY(p_categories))
    AND 1 - (c.embedding <=> query_embedding) > match_threshold
  ORDER BY c.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- ── RLS ───────────────────────────────────────────────────────────────────────
ALTER TABLE rag_knowledge_bases ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_pautas ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_briefings ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_pieces ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_piece_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_factchecks ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_piece_performance ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_model_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_usage_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY agency_rag_kb ON rag_knowledge_bases FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_rag_docs ON rag_documents FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_rag_chunks ON rag_chunks FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_content_pautas ON content_pautas FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_content_briefings ON content_briefings FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_content_pieces ON content_pieces FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_content_versions ON content_piece_versions FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_content_factchecks ON content_factchecks FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_content_performance ON content_piece_performance FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_content_approvals ON content_approvals FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_ai_configs ON ai_model_configs FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
CREATE POLICY agency_ai_usage ON ai_usage_log FOR ALL USING (auth.jwt() ->> 'role' = 'agency');
-- Approval pública: acesso por token sem autenticação
CREATE POLICY public_approval_read ON content_approvals FOR SELECT USING (approval_link_token IS NOT NULL);
