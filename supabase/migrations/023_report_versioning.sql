-- Versionamento de relatórios: guarda a versão da IA junto do que foi enviado.
-- html_content (já existente) = versão final enviada;
-- ai_summary = análise crua da IA no momento do envio;
-- ai_insight_id = referência ao insight usado.
ALTER TABLE reports
  ADD COLUMN IF NOT EXISTS ai_summary    text,
  ADD COLUMN IF NOT EXISTS ai_insight_id uuid;
