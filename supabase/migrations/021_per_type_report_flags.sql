-- Flags por tipo de relatório (semanal/mensal) por cliente.
-- Antes havia só `reports_enabled` (um flag para os dois). Agora a agência
-- liga/desliga semanal e mensal de forma independente por cliente, pela aba
-- Relatórios. Backfill preserva o comportamento atual.

ALTER TABLE clients
  ADD COLUMN IF NOT EXISTS weekly_report_enabled  boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS monthly_report_enabled boolean NOT NULL DEFAULT false;

UPDATE clients
SET weekly_report_enabled  = COALESCE(reports_enabled, false),
    monthly_report_enabled = COALESCE(reports_enabled, false);
