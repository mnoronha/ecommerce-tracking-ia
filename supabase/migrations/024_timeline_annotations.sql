-- Anotações na timeline: o gestor marca eventos no gráfico de linha do dashboard
-- ("Black Friday", "mudamos o checkout") para explicar variações nos dados.
CREATE TABLE IF NOT EXISTS timeline_annotations (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id  uuid NOT NULL,
  date       date NOT NULL,
  label      text NOT NULL,
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_annotations_client_date ON timeline_annotations (client_id, date);
ALTER TABLE timeline_annotations ENABLE ROW LEVEL SECURITY;
