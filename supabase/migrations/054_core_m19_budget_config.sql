-- M19: core_budget_config — orçamento mensal canônico por cliente/plataforma.
-- Append-only via UPSERT. Nunca deletar; usar monitoring_enabled=false para desativar.
-- Rollback: DROP TABLE IF EXISTS public.core_budget_config;
BEGIN;

CREATE TABLE IF NOT EXISTS public.core_budget_config (
  id                 uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id          text          NOT NULL
                       REFERENCES public.clients(client_id) ON DELETE CASCADE,
  platform           text          NOT NULL
                       CHECK (platform IN ('google', 'meta')),
  period_type        text          NOT NULL DEFAULT 'monthly'
                       CHECK (period_type = 'monthly'),
  period_label       text          NOT NULL,   -- 'YYYY-MM'
  monthly_budget     numeric(14,2) NOT NULL CHECK (monthly_budget >= 0),
  currency           text          NOT NULL DEFAULT 'BRL',
  monitoring_enabled boolean       NOT NULL DEFAULT true,
  write_meta         jsonb,                    -- {actor, provenance, reason, written_at}
  created_at         timestamptz   NOT NULL DEFAULT now(),
  updated_at         timestamptz   NOT NULL DEFAULT now(),
  UNIQUE (client_id, platform, period_label)
);

ALTER TABLE public.core_budget_config ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.core_budget_config FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.core_budget_config TO service_role;

CREATE INDEX IF NOT EXISTS core_budget_config_client_period_idx
  ON public.core_budget_config (client_id, period_label DESC);

CREATE INDEX IF NOT EXISTS core_budget_config_active_idx
  ON public.core_budget_config (client_id, platform)
  WHERE monitoring_enabled = true;

COMMENT ON TABLE public.core_budget_config IS
  'M19: Orçamento mensal canônico por cliente/plataforma. '
  'UPSERT-safe via UNIQUE(client_id, platform, period_label). '
  'RLS_BOOTSTRAP: service_role apenas.';

COMMIT;
