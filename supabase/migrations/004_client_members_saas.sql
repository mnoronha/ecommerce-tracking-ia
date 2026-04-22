-- =============================================================================
-- Migration: 004_client_members_saas
-- SaaS multi-tenant: tabela client_members para acesso por cliente
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.client_members (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id   UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
  user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role        TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN ('admin', 'viewer')),
  invited_by  UUID REFERENCES auth.users(id),
  created_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE(client_id, user_id)
);

ALTER TABLE public.client_members ENABLE ROW LEVEL SECURITY;

-- Agência vê e gerencia membros dos seus clientes
CREATE POLICY IF NOT EXISTS client_members_agency ON public.client_members FOR ALL
  USING (client_id IN (
    SELECT id FROM public.clients WHERE agency_id IN (SELECT public.get_user_agency_ids())
  ));

-- Membro vê apenas sua própria linha
CREATE POLICY IF NOT EXISTS client_members_self ON public.client_members FOR SELECT
  USING (user_id = auth.uid());

-- Atualiza get_user_client_ids para incluir client_members (acesso direto a cliente)
CREATE OR REPLACE FUNCTION public.get_user_client_ids()
RETURNS SETOF UUID AS $$
  SELECT id FROM public.clients WHERE agency_id IN (SELECT public.get_user_agency_ids())
  UNION
  SELECT client_id FROM public.client_members WHERE user_id = auth.uid()
$$ LANGUAGE sql SECURITY DEFINER STABLE;

CREATE INDEX IF NOT EXISTS idx_client_members_user   ON public.client_members(user_id);
CREATE INDEX IF NOT EXISTS idx_client_members_client ON public.client_members(client_id);

-- Agência Pareto Plus
INSERT INTO public.agencies (name, slug)
VALUES ('Pareto Plus', 'pareto-plus')
ON CONFLICT (slug) DO NOTHING;
