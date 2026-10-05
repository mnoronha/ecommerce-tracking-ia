# Etapa 2 — Plano de migração de chaves Supabase

**Data:** 2026-10-03  
**Prazo limite (PRD §3):** fim de 2026  
**Critério de sucesso:** nenhum cliente perde acesso ao dashboard durante a migração.

---

## Contexto: chaves legadas em uso

O projeto atual usa dois tokens Supabase de escopo amplo:

| Token | Localização | Permissão no banco |
|---|---|---|
| `anon` key | Frontend Next.js (`NEXT_PUBLIC_SUPABASE_ANON_KEY`) | SELECT em tabelas com RLS ALLOW para `anon` |
| `service_role` key | Backend Railway (`SUPABASE_SERVICE_KEY`) | Bypassa RLS — acesso irrestrito ao banco |

**Risco da `service_role`:** qualquer código que rode no backend tem acesso irrestrito ao banco. Um bug, injeção ou vazamento vira brecha total.  
**Risco da `anon` key:** exposta no JS bundle do cliente; se uma tabela tiver policy permissiva demais, dados vazam sem autenticação.

---

## Objetivos da migração

1. Substituir `anon` key por um token de escopo limitado (Supabase `readonly_analyst` role).
2. Substituir `service_role` pelo token de escopo mínimo necessário para cada consumidor (`hermes_service`, `platform_web`, `agency_admin`).
3. Girar (rotate) ambas as chaves antigas após a substituição.
4. Resultado: nenhuma chave ativa tem permissão além do que precisa.

---

## Pré-requisitos (Etapa 3)

- [ ] Migration `20261003_agency_roles.sql` aplicada em staging e validada
- [ ] Roles Postgres criadas: `agency_admin`, `hermes_service`, `client_viewer`, `readonly_analyst`
- [ ] RLS policies revisadas para não depender de `anon` com permissão implícita
- [ ] `clients.client_id` slug preenchido para todos os 8 clientes
- [ ] Supabase staging branch criado e testado

---

## Fases de migração

### Fase 1 — Preparar sem quebrar (Etapa 3, seguro de fazer em paralelo com produção)

1. Criar as 4 roles via migration (ver `migrations/pending/20261003_agency_roles.sql`)
2. Para cada role, definir grants explícitos (sem herdar de `postgres`)
3. Criar **novos tokens** para cada role via Supabase dashboard → Settings → API → Service role keys
   - `AGENCY_API_ADMIN_KEY_V2` — mapeia ao role `agency_admin`
   - `AGENCY_API_HERMES_KEY_V2` — mapeia ao role `hermes_service`
   - `AGENCY_API_PLATFORM_KEY_V2` — mapeia ao role `platform_web`
   - `AGENCY_READ_ONLY_KEY` — mapeia ao role `readonly_analyst` (substitui anon para leitura interna)
4. Configurar os tokens novos em Railway staging
5. Testar que o dashboard staging funciona 100% com os novos tokens

**Nada muda em produção nesta fase.**

### Fase 2 — Substituir em produção (Etapa 3+, após validação staging)

Ordem obrigatória (nunca inverter):

```
a. Adicionar novo token em Railway (lado a lado com antigo)
b. Verificar que sistema funciona com o novo token
c. Remover o token antigo do código
d. Revogar o token antigo no Supabase
```

Para cada consumidor:

| Consumidor | Variável atual | Variável nova | Quando |
|---|---|---|---|
| Backend Railway — Hermes ingest | `SUPABASE_SERVICE_KEY` | `AGENCY_API_HERMES_KEY` + role `hermes_service` | Etapa 4 |
| Backend Railway — Collectors | `SUPABASE_SERVICE_KEY` | role-specific tokens | Etapa 4 |
| Frontend Next.js | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | `NEXT_PUBLIC_SUPABASE_ANON_KEY` (rotacionado) ou `readonly_analyst` | Etapa 6 |

### Fase 3 — Girar a service_role key (NUNCA revogar antes de terminar Fase 2)

Somente após **todos** os consumidores estarem usando tokens de escopo limitado:

1. Confirmar que `SUPABASE_SERVICE_KEY` só está em Railway (sem uso no frontend)
2. Gerar nova service_role key no Supabase dashboard
3. Atualizar Railway com a nova chave
4. Verificar sistema funcionando
5. Revogar a chave antiga no Supabase
6. Aguardar 24h monitorando alertas antes de confirmar que é seguro manter revogada

### Fase 4 — Girar a anon key

1. Confirmar que nenhuma tabela tem policy `FOR ALL TO anon` com dados sensíveis
2. Gerar nova anon key no Supabase dashboard
3. Atualizar Vercel env vars (`NEXT_PUBLIC_SUPABASE_ANON_KEY`)
4. Deploy e verificar que dashboard clients carrega normalmente
5. Revogar a anon key antiga
6. Aguardar 24h monitorando

---

## Rollback

Se qualquer fase quebrar o sistema:

1. **Restaurar o token antigo** no Railway/Vercel imediatamente (a chave antiga ainda está válida durante a fase de transição — nunca revogue antes de confirmar sucesso)
2. Abrir issue descrevendo o que falhou
3. Corrigir antes de tentar novamente

A sequência "adicionar novo → verificar → remover antigo → revogar antigo" garante que sempre existe um caminho de rollback disponível.

---

## Inventário de chaves ativas (estado atual, 2026-10-03)

| Chave | Uso | Exposição | Ação |
|---|---|---|---|
| Supabase `service_role` | Railway backend — todas as operações DB | Server-side apenas | Migrar para scoped tokens (Fase 2) |
| Supabase `anon` | Vercel frontend — `NEXT_PUBLIC_SUPABASE_ANON_KEY` | JS bundle público | Girar após Fase 3 (Fase 4) |
| `AGENCY_OS_INGEST_KEY` | Hermes → Railway POST /agency/ingest | Server-side apenas | Manter; girar anualmente |
| `AGENCY_API_ADMIN_KEY` | Desenvolvimento/admin | Server-side apenas | Configurar no Railway staging agora |
| `AGENCY_API_HERMES_KEY` | Hermes → Agency API | Server-side apenas | Configurar no Railway staging agora |
| `AGENCY_API_PLATFORM_KEY` | Platform → Agency API | Server-side apenas | Configurar no Railway staging agora |
| `CREDENTIALS_KEY` (Fernet) | Encriptar tokens de clientes em repouso | Railway env var | Não girar sem migração de dados |

---

## Checklist de progresso

- [ ] Staging branch Supabase criado (manual — requer Supabase dashboard)
- [ ] Railway staging service configurado com `.env.staging`
- [ ] Migration `20261003_agency_roles.sql` testada em staging
- [x] `SUPABASE_JWT_SECRET` adicionado ao config.py (Etapa 2)
- [x] auth.py não usa `AGENCY_API_CLIENT_KEY` (Etapa 2)
- [ ] Tokens de escoped roles criados e testados (Etapa 3)
- [ ] `service_role` rotacionada (Fase 3)
- [ ] `anon` key rotacionada (Fase 4)
