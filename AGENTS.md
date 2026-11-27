# Noro Platform — Regras Inegociáveis para Agentes

Monorepo da NoroLabs: `apps/api` (FastAPI / Railway), `apps/web` (Next.js / Vercel), `pixel/noro-v4` (Shopify Tracker) e `apps/api/migrations` (Supabase). Veja [README.md](README.md).

Este arquivo consolida as regras obrigatórias das seções **0, 3.1, 21 e 27** do **PRD Noro Platform v3.0 (27/11/2026)** e vale para todos os agentes (Antigravity, Claude Code via `CLAUDE.md`, Codex/Hermes).

---

## 1. Disciplina de Git, Branches e PRs (Seções 0 e 27)

- **NUNCA faça push direto na `main`.** A `main` é protegida e dispara deploy automático no Railway e na Vercel. Toda alteração deve ir em branch dedicada com **PR focado por assunto**, revisado e validado antes do merge.
- **Fluxo obrigatório de engenharia:** `audit → state/backup → implement → tests → smoke`. Sempre verifique o código real antes de alterar — o PRD não substitui inspeção no repositório.
- **Sem comandos destrutivos nem sujeira no commit:** nunca use `git add .` indiscriminado nem `git reset` destrutivo; nunca commite scripts temporários (`_probe_*`, etc.), previews HTML reais, dumps ou dados financeiros/operacionais de clientes.
- **Critério de `CLOSED` / `PRODUÇÃO`:** exige PR mergeado na `main`, migração aplicada no Supabase (quando aplicável) e evidência de smoke/teste real. Nunca reporte status não verificado.
- **Sem regressão funcional (`no functional regression`):** reutilize foundations existentes antes de criar paralelas e não altere módulos já homologados para satisfazer ajustes cosméticos.

---

## 2. Credenciais, Segurança e LGPD (Seções 0, 21 e 27)

- **NUNCA exponha, logue, reproduza ou hardcode credenciais, tokens ou chaves** em código, documentos, PRDs, testes, commits ou saídas (ex.: `AGENCY_API_PLATFORM_KEY`, Supabase service role, `OBSIDIAN_GITHUB_TOKEN`, `.env*`).
- **Nenhuma rota nova sem autenticação e autorização server-side:** valide identidade do cliente (`clients.id` UUID canônico), escopo e isolamento multi-tenant no backend/BFF. Mantenha CORS em allowlist estreita, rotas administrativas restritas a admin e endpoints públicos de health mínimos. Não presuma dívidas históricas de segurança (S1–S7) corrigidas sem verificação.
- **LGPD e PII:** nunca grave e-mail ou telefone de cliente em texto claro — use `apps/api/app/core/pii.py`. Não registre PII em logs nem colete PII no tracker. Mantenha dados clínicos (AI Sales) estritamente segregados de marketing e anúncios.
- **Menor privilégio do Hermes:** nunca amplie o Hermes para `agency_admin`, `platform_web`, SQL direto ou escrita em plataformas de mídia.

---

## 3. Fronteiras de Arquitetura e Autoridade (Seção 3.1)

- **Core (`apps/api` + Supabase)** é a autoridade única de valores, estado, histórico e contratos.
- **Hermes** interpreta evidências via API sem recalcular nem alterar facts canônicos.
- **Platform (`apps/web`)** não deve duplicar regras de negócio; o BFF autentica, valida escopo/tenant e encaminha ao Core.
- **Projeções não são fonte de verdade:** Notion não é banco de histórico; Obsidian não é fonte de métricas; Telegram não aprova alterações de mídia implicitamente; Noro Tracking e GA4 não substituem Business Truth (Shopify/VNDA/CRM).
- **AI Sales** é domínio independente — sem acoplamento de banco nem compartilhamento de credenciais com o Core.

---

## 4. Proteção de Sistemas Externos e Campanhas (Seções 21 e 27)

- **NUNCA altere campanhas de mídia** (Meta Ads, Google Ads ou qualquer escrita em contas de anúncio — media writes estão fora do escopo e bloqueados).
- **NUNCA altere lojas, temas ou pixels externos** (Shopify, VNDA, Custom Pixel/tracker) sem autorização explícita do owner.
- **NUNCA chame `send_*` de plataformas de anúncio sem `send_guard.sends_blocked(...)`** e respeito à flag `CONVERSION_SENDS_DISABLED`.

---

## 5. Integridade de Dados e Gates de Relatórios (Seções 21 e 27)

- **NUNCA bypasse gates de publicação ou entrega de relatórios** (`DATA_VALIDATION`, `EXACT_PUBLISHED_VERSION_REQUIRED`, guarda de `RENDER_FAILED`, allowlist de teste interno, aprovação humana explícita e política de CTA).
- **NUNCA fabrique métricas, converta `null`/ausência em zero, apague histórico ou sobrescreva decisões humanas.** Preserve estados explícitos de fonte/valor (`MISSING`, `NO_DATA`, `STALE`, `PARTIAL`, `PROVISIONAL`, etc.).

---

## 6. Runtime, Concorrência, Migrações e Stack (Seções 21 e 27)

- **NÃO faça I/O bloqueante em rotas ou funções `async`** no FastAPI (`apps/api`) — use clientes assíncronos ou offload explícito (`asyncio.to_thread` / threadpool) para evitar travamento do event loop.
- **Scheduler e concorrência:** não escale múltiplos processos `uvicorn` com APScheduler duplicado sem separação de worker ou advisory lock. Preserve idempotência, locks/CAS e registro de execução em jobs.
- **Migrações SQL:** novas migrações vão em `apps/api/migrations/` com numeração sequencial, incrementais e retrocompatíveis. Aplique no Supabase antes do deploy quando o código depender delas (nunca execute operações destrutivas/purge sem backup e autorização).
- **Frontend Next.js (`apps/web`):** usa versão do Next.js com mudanças incompatíveis — antes de escrever código no front, leia o guia relevante em `apps/web/node_modules/next/dist/docs/` e respeite avisos de depreciação.

---

## 7. Testes Isolados Obrigatórios Antes de Qualquer PR (Seções 0, 24 e 27)

- **NUNCA rode testes de escrita contra produção.** Toda suíte deve ser **isolada** (fixtures/mocks sem mutar dados reais); smokes em produção devem ser somente leitura ou operações explicitamente autorizadas.
- **Nunca corrija um teste apenas para "ficar verde"** mascarando regressões ou contratos quebrados. Registre resultados reais e eventuais falhas conhecidas no PR.
- **Checks obrigatórios antes de abrir qualquer PR:**
  - Backend import: `cd apps/api && python -c "import app.main"`
  - Backend testes isolados: `cd apps/api && pytest tests -q` (incluindo testes negativos de auth/cross-tenant, idempotência e invariantes quando aplicável)
  - Frontend build / TypeScript: `cd apps/web && npm run build`
