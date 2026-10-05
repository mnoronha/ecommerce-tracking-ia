# Etapa 0 — Reconhecimento e inventário de migração

**Data:** 2026-10-03  
**Propósito:** inventário do estado atual do sistema antes de qualquer código da v1.1. Este documento é a referência para as decisões de migração das Etapas 1–14.

---

## 1. Inventário do Agency OS atual

### 1.1 Performance Truth — coleta e leitura ao vivo

| Componente | Arquivo | O que faz | Quem consome |
|---|---|---|---|
| Meta Ads ROAS/overview | `apps/api/app/routers/meta_ads.py` | ROAS por campanha (Meta API + pedidos), overview com `meta_ad_attributions` | Frontend: `/clients/[id]/meta-ads` |
| Google Ads dashboard | `apps/api/app/routers/google_ads_dashboard.py` | KPIs Google Ads via GAQL, conversões, ROAS | Frontend: `/clients/[id]/google-ads` |
| Pacing | `apps/api/app/routers/pacing.py` | Ritmo de gasto vs. meta mensal | Frontend: `/clients/[id]/dashboard` |
| GA4 | `apps/api/app/routers/journey.py` + `services/ga4_reporting.py` | Funil de sessões, GA4 reporting | Frontend: `/clients/[id]/ga4`, `/journey` |
| Spend sync | `apps/api/app/services/spend_sync.py` | Cron 06:00 UTC — sincroniza gastos de todas as fontes para tabela `clients.monthly_ad_spend` | Relatórios, dashboards |
| Meta attribution sync | `apps/api/app/services/meta_attribution_sync.py` | Cron 09 e 14 UTC — preenche `meta_ad_attributions` | ROAS/overview endpoint |
| Metrics cache | `apps/api/app/services/metrics_cache.py` | Cron 06:30 UTC — materializa métricas por cliente no banco | Performance page |

**Característica crítica:** não há snapshots imutáveis. Todos os valores são calculados ao vivo na consulta ou em cache de curto prazo. Sem histórico de "o que se sabia em X".

---

### 1.2 Report Contract v1 — runners semanal e mensal

| Componente | Arquivo | O que faz | Quem consome |
|---|---|---|---|
| Runner semanal | `services/reports.py:send_weekly_reports` | Cron segunda 11 UTC — gera HTML, envia email para clientes | Email (Resend/SMTP) |
| Runner mensal | `services/reports.py:send_monthly_reports` | Cron dia 1 11 UTC — relatório completo + gate de negatividade | Email (Resend/SMTP) |
| Report builder | `services/report_builder.py` | Monta o dict de dados (revenue, KPIs por canal, top products) | runners acima |
| Report renderer | `services/report_renderer.py` | Converte dict → HTML Handlebars (`semanal_uau.html`) | runners acima |

**Observação:** os runners atuais enviam o relatório por email. Não há endpoint de leitura — o "contrato" vive apenas como email enviado, não há armazenamento estruturado para consulta posterior. A tabela `agency_report_contracts` (criada na sessão anterior) é o primeiro passo para mudar isso.

---

### 1.3 Ingest no Railway (Agency OS endpoint)

| Componente | Arquivo | O que faz | Quem consome |
|---|---|---|---|
| Endpoint ingest | `apps/api/app/routers/agency_os.py` | `POST /agency/ingest/report-contract` — valida `schema_version`, extrai de `report.client_slug/business_model/period`, upsert em `agency_report_contracts` | Hermes / Agency OS |
| Tabela de contratos | Supabase `agency_report_contracts` | Armazena contrato JSONB verbatim + metadados (`client_slug`, `report_type`, `period_start/end`, `schema_version`, `source_run_id`, `generated_at`) | Frontend (performance page), API |
| Auth | `AGENCY_OS_INGEST_KEY` no Railway | Bearer token server-side only | Hermes |

**Estado:** endpoint está em produção (verificado via OpenAPI). A tabela existe. O Hermes **ainda não está enviando** — o endpoint aguarda configuração do `AGENCY_OS_INGEST_KEY` no Railway e integração do lado Hermes.

---

### 1.4 Alert Engine e Alertas

| Componente | Arquivo | O que faz | Quem consome |
|---|---|---|---|
| Alert engine | `services/alert_engine.py` | Cron a cada 30 min — avalia `alert_rules`, upsert em `alerts`, auto-resolve | Dashboard `/clients/[id]/alertas` |
| Conversion check | `services/alerts.py` | Cron a cada 6h — queda de CR vs. baseline 7d → Slack webhook | Slack (se `slack_webhook_url` configurado) |
| Anomaly check | `services/anomalies.py` | Cron diário 11 UTC — detecção de anomalias em pedidos | Email agência |
| Alert rules | Tabela `alert_rules` | Regras por `agency_id`/`client_id`, `rule_key`, `severity`, `throttle_minutes`, `config jsonb`, `enabled` | alert_engine |
| Alerts | Tabela `alerts` | Fingerprint, título, mensagem, `data jsonb`, `resolved_at` | Frontend, email |

**Gap em relação ao PRD:** regras sem `rule_version`; sem referências a `snapshot_ids`; sem `alert_feedback`; sem sugestões de recalibração; sem integração Telegram.

---

### 1.5 Health Monitor e Integrações

| Componente | Arquivo | O que faz | Quem consome |
|---|---|---|---|
| Daily health monitor | `services/health_monitor.py` | Cron 12:30 UTC — verifica snippet volume, cobertura fbp, dispatch CAPI/Google, filtro offline | Email agência |
| Integrations health | `services/integrations_health.py` | Cron horário — probes ao vivo de Meta, Google Ads, GA4, TikTok, Pinterest, Shopify; grava `<platform>_health` em `clients` | Dashboard health card |
| Token health | `services/meta_token_health.py` | Cron a cada 6h — verifica vencimento de tokens Meta | Email agência |
| Endpoint de status | `routers/integrations.py` | `GET /integrations/{pixel}/status` (cached), `POST` (live probe) | Wizard onboarding, health card |

**Estados atuais:** `healthy | expiring_soon | expired | invalid | unknown` — armazenados como texto em colunas `meta_token_health`, `google_ads_token_health`, `ga4_health`, `shopify_health`, `tiktok_token_health`, `pinterest_token_health` na tabela `clients`.

---

### 1.6 Onboarding Registry e Capabilities

| Componente | Arquivo | O que faz |
|---|---|---|
| Setup/credentials | `routers/setup.py` | `POST /setup/shopify/{id}/webhooks`, `POST /setup/shopify/{id}/install`, `PUT /{id}/credentials` |
| Frontend wizard | `app/(app)/clients/[clientId]/onboarding/page.tsx` | Wizard de configuração de integrações |
| Capabilities | Tabela `clients` | Credenciais por plataforma, flags `is_active`, pixel IDs, `monthly_ad_spend`, `monthly_revenue`, `monthly_roas` (campos de cache) |

---

### 1.7 Módulo GEO / AI Visibility

| Componente | Arquivo | O que faz | Quem consome |
|---|---|---|---|
| Router GEO | `routers/ai_visibility.py` | Import CSV (legacy), DataForSEO auto, summary/trend/prompts/competitors | Frontend `/clients/[id]/ai-visibility` |
| Collector | `services/ai_visibility_collector.py` | Cron segunda 03 UTC — coleta DataForSEO semanal; cron dia 1 00 UTC — zera budget | — |
| Analyst | `services/ai_visibility_analyst.py` | Análise IA de visibilidade; cron dia 1 11 UTC — reminder de import | — |
| Parser | `services/ai_visibility_parser.py` | Parse CSV Ubersuggest (import manual) | — |
| AI presence pipeline | Frontend `clients/[id]/ai-presence/pipeline/page.tsx` | Visualização do pipeline de AI Presence | — |

**Regra de migração:** GEO/AI Visibility não muda durante a migração inteira (Fases A–F). Só na Fase C (Etapa 12) o acesso é reorganizado dentro da Client View, sem alterar a lógica.

---

### 1.8 Frontend — Noro Platform (Vercel)

- **Stack:** Next.js 16.2.4 (App Router), React 19, Supabase `@supabase/ssr`
- **~50 telas** em `apps/web/app/(app)/clients/[clientId]/`
- **Autenticação:** Supabase Auth com cookie de sessão; `createSupabaseServerClient()` para leitura server-side
- **Performance page** (`/clients/[id]/performance`) — única tela que já consome `agency_report_contracts`; lê via `lib/agency-os.ts` com Supabase direto
- **Todas as outras telas** consomem os endpoints FastAPI legados (`/meta-ads`, `/google-ads`, `/integrations`, etc.) via `fetch` nas Server Components ou Client Components

**Acesso de clientes:** hoje não há Client View. Clientes recebem relatório por email. O dashboard é **agência apenas**.

---

## 2. Schema atual do Supabase, RLS e chaves

### 2.1 Tabelas principais observadas no código

| Tabela | Escrita por | Campos principais | RLS |
|---|---|---|---|
| `clients` | Frontend (setup), API | `id uuid`, `pixel_id`, `name`, `is_active`, credenciais encriptadas (`meta_access_token`, `google_ads_refresh_token`, `ga4_*`, `shopify_*`, ...), campos de health por plataforma, `monthly_ad_spend`, `monthly_revenue`, `monthly_roas` | Desconhecido — acesso via service_role no backend |
| `orders` | Shopify webhook, Shopify sync | `client_id`, `platform_order_id`, `total_price`, `currency`, `financial_status`, `utm_*`, `capi_sent`, `google_sent`, `google_match_type`, `visitor_id`, `email`, `phone` | Desconhecido |
| `visitors` | Pixel JS, Shopify webhook | `client_id`, `gclid`, `fbp`, `fbc`, `ga_client_id`, `last_seen_at` | Desconhecido |
| `tracking_events` | Pixel JS | `client_id`, `session_id`, tipo de evento, properties | Retenção 90d (LGPD) |
| `sessions` | `sessionization.py` (desativado) | Agregado de tracking_events por session_id | — |
| `alerts` | `alert_engine.py` | `agency_id`, `client_id`, `alert_rule_id`, `severity`, `fingerprint`, `title`, `message`, `data jsonb`, `resolved_at` | — |
| `alert_rules` | Manual/admin | `agency_id`, `client_id`, `rule_key`, `severity`, `throttle_minutes`, `config jsonb`, `enabled` | — |
| `agency_report_contracts` | `agency_os.py` ingest | `client_slug`, `report_type`, `business_model`, `period_start`, `period_end`, `schema_version`, `source_run_id`, `generated_at`, `contract jsonb`, `comparison_period_start/end`, `updated_at` | Desconhecido |
| Tabelas RAG | RAG service | migration 020: content, embeddings, etc. | — |
| Tabelas AI visibility | ai_visibility_* | Visibilidade por prompt, série temporal, competitors | — |

**Observação importante:** não há pasta `apps/api/migrations/` com SQL versionado (exceto `020_rag_content_system.sql`). As migrations anteriores foram aplicadas manualmente via Supabase SQL Editor — não há controle de versão das migrações 001–019.

### 2.2 RLS atual

Não há evidência de RLS configurado sistematicamente. O backend usa sempre `SUPABASE_SERVICE_KEY` (service_role), que contorna RLS. O frontend usa `createSupabaseServerClient()` com sessão do usuário — RLS existiria como segunda defesa, mas provavelmente não está configurado de forma rigorosa.

O PRD exige: RLS em toda tabela com `client_id`, `client_viewer` sem acesso direto, papéis `agency_admin/hermes_service/client_viewer/readonly_analyst`.

### 2.3 Chaves e papéis

| Chave | Onde está | Para que serve |
|---|---|---|
| `SUPABASE_SERVICE_KEY` | Railway (FastAPI) | Acesso total service_role (contorna RLS) |
| `SUPABASE_SERVICE_ROLE_KEY` | Vercel (algumas rotas) | Idem, para rotas Next.js que precisam de escrita privilegiada |
| Supabase anon key (implícita via `@supabase/ssr`) | Vercel (frontend) | Autenticação de usuários e leitura com RLS |
| `CREDENTIALS_KEY` (Fernet) | Railway | Encripta credenciais de clientes em repouso |
| `AGENCY_OS_INGEST_KEY` | Railway (pendente de configurar) | Bearer token para ingest do Hermes |

**Risco (PRD §3):** "Chaves legadas anon/service_role do Supabase são substituídas antes do fim de 2026." As chaves atuais (`SUPABASE_SERVICE_KEY` / `SUPABASE_SERVICE_ROLE_KEY`) são as chaves service_role brutas que a Supabase planeja descontinuar. Etapa 2 do PRD endereça isso.

---

## 3. Como clientes acessam o dashboard hoje

**Hoje:** clientes **não têm acesso ao dashboard**. O acesso é exclusivo da agência (Maicon). Os clientes recebem:

1. **Relatório semanal por email** — gerado toda segunda, enviado via Resend/SMTP para o email do cliente cadastrado em `clients`
2. **Relatório mensal por email** — gerado no dia 1, com gate de negatividade (mês ruim vai para agência primeiro)

O dashboard em `ecommerce-tracking-ia-dash.vercel.app` é acesso interno. Não há URL de Client View nem usuários `client_viewer` cadastrados no Supabase Auth.

**Implicação para migração:** a Etapa 12 (Client View) é nova funcionalidade, não migração de acesso existente. O risco é baixo no sentido de "não quebrar clientes" porque clientes não acessam hoje.

---

## 4. Semânticas de estado já no código e mapeamento para os enums da Seção 5

### 4.1 `MetricStatus` em `lib/agency-os.ts`

Usado em `Metric.status` na performance page (novo).

| Código atual | Mapeamento PRD (`value_status`) | Observações |
|---|---|---|
| `available` | `OK` | Valor presente e confiável |
| `access_missing` | — | É `source_state`, não `value_status`. Na performance page mistura os dois níveis |
| `permission_denied` | — | Idem — deveria ser `source_state` |
| `not_contracted` | `NOT_APPLICABLE` | Mais precisamente é `source_state: NOT_CONTRACTED` propagado para `value_status: NOT_APPLICABLE` |
| `no_data` | `NO_DATA` | Correspondência direta |
| `unknown` | `UNKNOWN` | Correspondência direta |
| — | `PARTIAL` | **Ausente** no código atual |
| — | `STALE` | **Ausente** no código atual |
| — | `MISSING` | **Ausente** no código atual |

**Problema:** a `MetricStatus` atual mistura dois níveis semânticos do PRD: estado da fonte (`source_state`) e estado do valor (`value_status`). A Etapa 1 precisa separar os dois, preservando os rótulos de exibição em PT-BR que já existem na performance page.

### 4.2 Estados em `integrations_health.py` (coluna `*_health` em `clients`)

Usado pelo health card e pelo wizard.

| Código atual | Mapeamento PRD (`source_state`) | Observações |
|---|---|---|
| `healthy` | `READY` | |
| `expiring_soon` | `READY` (com nota) | Subcaso de READY — token funciona mas expira logo. O PRD não tem esse estado; seria `READY` com `state_reason` |
| `expired` | `ERROR` ou `STALE` | Depende do motivo: token expirado é `ACCESS_MISSING` ou `PERMISSION_DENIED` |
| `invalid` | `PERMISSION_DENIED` ou `ACCESS_MISSING` | Credenciais erradas/scopes faltando → `ACCESS_MISSING`; conta banida → `PERMISSION_DENIED` |
| `unknown` | `ACCESS_MISSING` (nunca probed) | `unknown` = nunca configurado/testado |
| — | `NOT_CONTRACTED` | **Ausente** — hoje se Meta não está configurado, fica `unknown`, não `NOT_CONTRACTED` |
| — | `STALE` | **Ausente** — sem tracking de "última coleta bem-sucedida" |
| — | `NO_DATA` | **Ausente** |
| — | `NOT_APPLICABLE` | **Ausente** |
| — | `ERROR` (técnico) | Parcialmente coberto por `invalid` |

### 4.3 Estados em `alert_engine.py` (tabela `alert_rules`)

O campo `severity` tem valores implícitos (`critical`, `warning`, `info`). Sem `rule_version`, sem referências a snapshots.

### 4.4 Semântica de `agency_report_contracts`

A tabela armazena `schema_version = 'norolabs-report-contract-v1'` e o contrato JSONB. A `MetricStatus` dentro do contrato usa o enum de 6 valores descrito em 4.1 acima.

---

## 5. Mapa de migração

Para cada componente: destino, fase de migração (A–F da Seção 11), ação.

### Componentes a evoluir

| Componente atual | Destino PRD | Fase | Ação |
|---|---|---|---|
| Coleta Meta Ads (`meta_ads.py` + `meta_attribution_sync`) | `core/collectors/meta_ads` + `core_metric_snapshots` | A (Etapa 6) | Reaproveitamento: nova camada de normalização por ID grava snapshots; endpoint legado permanece |
| Coleta Google Ads (`google_ads_dashboard.py` + `spend_sync`) | `core/collectors/google_ads` + `core_metric_snapshots` | A (Etapa 6) | Idem |
| Coleta GA4 (`ga4_reporting.py`) | `core/collectors/ga4` + `core_metric_snapshots` | A (Etapa 6) | Idem |
| Coleta Shopify (`shopify_sync.py`) | `core/collectors/shopify` + Business Truth snapshots | A (Etapa 6) | Idem |
| `reports.py` runners weekly/monthly | `core/reports/` + `core_report_contracts` | A–E (Etapa 10) | Paralelo: runners atuais continuam; novo núcleo gera `core_report_contracts`; equivalência comparada em `docs/etapa-10-equivalencia.md` |
| `agency_report_contracts` (tabela atual) | `core_report_contracts` | B–E | Ponteiro de migração: `agency_report_contracts` recebe ingest do Hermes atual; `core_report_contracts` é gerado pelo núcleo novo; frontend migra na Fase C |
| `alert_engine.py` + `alert_rules` | `core_alert_rules` + monitores do núcleo | B–E (Etapa 9) | Paralelo: engine atual continua; novo núcleo adiciona `rule_version`, snapshot refs, `alert_feedback` |
| `integrations_health.py` (colunas `*_health` em `clients`) | `core_data_sources` + `core_data_health` | A–B (Etapas 3, 6, 7) | Evolução: colunas legadas continuam existindo até Fase F; novas tabelas alimentadas em paralelo |
| `agency-os.ts` `MetricStatus` | `value_status` + `source_state` separados | B–C (Etapa 1/7/11) | Mapeamento em Etapa 1; implementação em Etapa 7 |
| Tabela `clients` | `clients` com `timezone`, `currency`, `business_model` obrigatórios | Etapa 3 | Migration additive — adiciona colunas, preenche dados existentes |

### Componentes a manter sem alteração

| Componente | Fase de preservação | Condição para deprecar |
|---|---|---|
| GEO / AI Visibility (`ai_visibility_*`) | A–F (toda migração) | Só reorganizado na Client View (Etapa 12); nada removido |
| Onboarding wizard (`setup.py`, `onboarding/page.tsx`) | A–F | Permanece até Fase F |
| Serviços de email (Resend/SMTP, templates HTML) | A–E | Permanecem como canal de entrega; podem coexistir com narrativas via API |
| Serviços desativados (sessionization, capi_retry, cart_abandonment) | — | Já desativados; remover na Etapa 14 após confirmar que não há dependência |

### Componentes novos (não existem hoje)

| Componente | Etapa | Impacto na migração |
|---|---|---|
| `core_metric_snapshots` (imutável, append-only) | 3/6 | Novo; nada atual depende |
| `core_jobs` / `core_job_runs` (run_key, locks, retry) | 4 | Substitui APScheduler simples; APScheduler continua até Fase E |
| `truth_*` tables (sync Notion) | 3/5 | Novo; alimenta metrics na Etapa 6 |
| `truth_conversion_map` | 3/5 | Novo; bloqueia ROAS canônico até aprovação |
| `change_log` | 3/8 | Novo; não interfere com nada existente |
| `intel_*` tables | 3 | Do Hermes via Agency API; novo |
| `core_alert_rule_suggestions` | 9 | Novo |
| Projeto Supabase de staging | Etapa 2 | **Não existe hoje** — precisa ser criado |
| Agency API `/agency/v1` | 1–9 | Parcialmente iniciado (`agency_os.py`); precisa expandir |

---

## 6. Conflitos com o PRD, riscos e perguntas em aberto

### 6.1 Conflitos técnicos

**C1 — `agency_report_contracts` vs `core_report_contracts`**  
A tabela `agency_report_contracts` já existe em produção e o frontend já a lê (`lib/agency-os.ts`). O PRD define `core_report_contracts` com campos diferentes (`report_contract_id`, `truth_versions`, `period_closed_at`, `certification_coverage`, `supersedes_id`). **Decisão necessária antes da Etapa 3:** renomear/evoluir `agency_report_contracts` para `core_report_contracts` com adição de colunas, ou manter as duas como aliases temporários?

**C2 — `MetricStatus` mistura `source_state` e `value_status`**  
A performance page renderiza `Metric.status` com códigos que pertencem a dois níveis semânticos distintos (`access_missing`, `permission_denied` são `source_state`; `no_data`, `unknown` são `value_status`). A Etapa 1 (contratos Pydantic) precisa separar os tipos e o frontend precisa de refactor correspondente na Etapa 11.

**C3 — `schema_version` ausente em todos os payloads legados**  
Nenhum endpoint atual (`/meta-ads`, `/integrations`, etc.) carrega `schema_version`. O PRD exige `schema_version` em todo payload e objeto persistido. O risco é baixo enquanto o Hermes não consome esses endpoints, mas a Fase D exige que a Agency API os exponha com `schema_version`.

**C4 — Migrations sem controle de versão**  
Apenas `020_rag_content_system.sql` existe em `apps/api/migrations/`. As migrations 001–019 foram aplicadas manualmente. A Etapa 3 cria o padrão versionado em `migrations/` e todas as novas migrations passam por lá. As antigas não precisam ser retroativamente versionadas, mas precisam ser documentadas antes de qualquer migration de Etapa 3 rodar em staging.

**C5 — Sem staging Supabase**  
O PRD exige produção e staging como projetos físicos separados. Hoje existe apenas um projeto de produção. A Etapa 2 precisa criar o projeto de staging antes de qualquer migration de Etapa 3.

**C6 — `business_model`, `timezone`, `currency` ausentes em `clients`**  
A tabela `clients` não tem esses campos. O PRD os exige como obrigatórios para qualquer cliente monitorado. A Etapa 3 adiciona as colunas; a Etapa 5 (sync Notion) é quem vai preencher os valores. Enquanto não preenchidos, o Data Health mostra `notion_truth: PARTIAL` (comportamento correto).

**C7 — `run_key`/advisory lock ausentes no APScheduler atual**  
APScheduler não tem deduplicação por job se o processo reiniciar no meio de uma execução. Isso cria risco de snapshots duplicados quando a Etapa 6 introduzir coletores. A Etapa 4 (infraestrutura de jobs) resolve isso antes dos coletores reais.

**C8 — IDs de conversão não mapeados**  
O sistema atual usa `google_ads_conversion_action_id` por cliente (um ID por cliente). O PRD exige `truth_conversion_map` com `source_conversion_id` por conta e ação, com aprovação humana. O ROAS canônico do núcleo só pode ser calculado após esse mapeamento ser criado e aprovado (Etapa 5).

**C9 — Alert rules sem versão**  
A tabela `alert_rules` atual não tem `rule_version`. O PRD exige que todo alerta registre a versão da regra usada. Conflito na Etapa 9 — a migration de `core_alert_rules` precisará coexistir com `alert_rules` até a Fase E.

### 6.2 Riscos operacionais

**R1 — Clientes não percebem a migração (requisito PRD §3)**  
Os clientes hoje não acessam o dashboard (só recebem email). O risco real é a agência perder acesso às telas existentes durante a migração. Mitigação: Fases A–B não tocam no frontend existente.

**R2 — GEO ativo em produção**  
O módulo GEO tem crons semanais e mensais ativos. Qualquer migration que toque na tabela `clients` (adicionando `timezone`/`currency`/`business_model`) pode quebrar queries do GEO que fazem `SELECT *`. Mitigação: colunas additive-only, nunca `DROP COLUMN` nem `ALTER COLUMN` antes da Fase F.

**R3 — Hermes sem Agency API ainda**  
O Hermes atual lê diretamente Meta/Google/GA4. A Fase D só começa após o Gate 2 (Etapa 9). Enquanto isso, `agency_report_contracts` é alimentado pelo Hermes via o endpoint de ingest existente. Não há colisão de escrita.

**R4 — Chave `AGENCY_OS_INGEST_KEY` não configurada**  
O endpoint `/agency/ingest/report-contract` retorna 503 enquanto `AGENCY_OS_INGEST_KEY` não for configurado no Railway. O Hermes não consegue enviar contratos. Ação imediata: configurar a variável de ambiente.

**R5 — Snapshots append-only sem UPDATE**  
O PRD exige bloquear UPDATE em `value` via trigger SQL. Isso precisa ser testado em staging antes de produção, pois uma trigger mal escrita pode travar operações legítimas de upsert em outras tabelas.

### 6.3 Perguntas que precisam ser respondidas antes de começar a codificar

| # | Pergunta | Impacto | Etapa que desbloqueia |
|---|---|---|---|
| P1 | O projeto Supabase de staging existe? Se não, qual o plano para criá-lo? | Toda Etapa 2/3 depende disso | Etapa 2 |
| P2 | O `client_id` canônico da LK Sneakers para o Agency OS é o UUID do Supabase, o `pixel_id`, ou um novo slug `lk-sneakers`? | Chave de toda a tabela `core_data_sources` e slugs do Hermes | Etapa 3 |
| P3 | O Hermes está operacional hoje e pode começar a enviar contratos via `/agency/ingest/report-contract`? | Alimenta `agency_report_contracts` com dados reais para comparação | Agora |
| P4 | `agency_report_contracts` deve evoluir para `core_report_contracts` in-place (migration additive) ou as duas devem coexistir temporariamente? | Evita quebrar a performance page durante a migração | Etapa 3 |
| P5 | Qual o `timezone` e `currency` de cada cliente? (necessário para Etapa 5/6) | Coleta de dados no fuso correto; fechamento mensal | Etapa 3/5 |
| P6 | O `business_model` de cada cliente está documentado no Notion? Se não, onde confirmar? | Metric contract correto por cliente; alertas corretos | Etapa 5 |
| P7 | Quais conversões de cada cliente entram no `truth_conversion_map`? (Google Ads: action IDs; Meta: event names canônicos) | ROAS canônico só disponível após aprovação do mapa | Etapa 5 |
| P8 | O Hermes atual pode ser apontado contra uma mock Agency API durante as Etapas 1–8, sem desligar o Hermes de produção? | Implementação paralela sem interrupção | Etapa 1 |
| P9 | Há clientes além de LK Sneakers para os quais o núcleo novo deve gerar dados na Fase A? | Escopo inicial dos coletores | Etapa 6 |

---

## Resumo executivo

O Agency OS atual é um conjunto de pipelines funcionais mas independentes: coleta ao vivo sem histórico, relatórios por email sem armazenamento estruturado, health checks com estados colapsados e alertas sem versão. O PRD v1.1 introduz o conceito de snapshot imutável, enums semânticos precisos, jobs com deduplicação e uma única fonte de verdade numérica para Platform e Hermes.

A migração não quebra nada existente porque:
1. Todas as Fases A–B são aditivas (novos pipelines ao lado dos antigos)
2. O frontend só migra na Fase C, com equivalência validada antes
3. GEO e os runners de email não são tocados até a Fase E

O único item de ação imediata (fora do código) é configurar `AGENCY_OS_INGEST_KEY` no Railway para que o Hermes possa começar a enviar contratos à `agency_report_contracts`.
