# Etapa 1 — Mapping: Report Contract v1 → Agency API v1.1

**Data:** 2026-10-03  
**Propósito:** documentar a correspondência entre os campos do Report Contract v1 atual
(`agency_report_contracts`) e os contratos do núcleo novo (Agency API v1.1 + `core_report_contracts`).

---

## 1. MetricStatus legado → v1.1 enums separados

O tipo `MetricStatus` em `apps/web/lib/agency-os.ts` misturava `source_state` (estado da fonte)
com `value_status` (estado do valor). A v1.1 os separa em dois enums distintos.

| Código legado (`MetricStatus`) | Novo `value_status` | Novo `source_state` (quando aplicável) | `reason_code` sugerido |
|---|---|---|---|
| `available` | `OK` | — | — |
| `no_data` | `NO_DATA` | `NO_DATA` | — |
| `unknown` | `UNKNOWN` | — | — |
| `access_missing` | `UNKNOWN` | `ACCESS_MISSING` | `SOURCE_ACCESS_MISSING` |
| `permission_denied` | `UNKNOWN` | `PERMISSION_DENIED` | `SOURCE_PERMISSION_DENIED` |
| `not_contracted` | `NOT_APPLICABLE` | `NOT_CONTRACTED` | `SOURCE_NOT_CONTRACTED` |
| — | `PARTIAL` | `PARTIAL` | *(novo — não existia)* |
| — | `STALE` | `STALE` | `LAST_SUCCESS_Xh` *(novo)* |
| — | `MISSING` | `MISSING` | *(novo)* |

**Regra:** quando `source_state ≠ READY`, o `value_status` da métrica **nunca** é `OK`.
A propagação exata é declarada em `core/metrics/rules/` (Etapa 7).

---

## 2. `integrations_health` legado → `SourceState` v1.1

Colunas `*_health` na tabela `clients` (texto livre) → enum `SourceState` em `core_data_sources`.

| Código legado (texto em `*_health`) | Novo `SourceState` | Observações |
|---|---|---|
| `healthy` | `READY` | Mapeamento direto |
| `expiring_soon` | `READY` | Subcaso de READY; preservar em `state_reason` |
| `expired` | `ACCESS_MISSING` | Token expirado = acesso ausente (não é erro técnico) |
| `invalid` | `PERMISSION_DENIED` ou `ACCESS_MISSING` | `PERMISSION_DENIED` se conta banida; `ACCESS_MISSING` se credencial errada/escopo faltando |
| `unknown` | `ACCESS_MISSING` | Nunca configurado/probed |
| *(ausente)* | `NOT_CONTRACTED` | Canal fora do contrato do cliente |
| *(ausente)* | `STALE` | Última coleta > limite de lag (novo conceito) |
| *(ausente)* | `NO_DATA` | Fonte saudável mas sem campanhas ativas |
| *(ausente)* | `ERROR` | Falha técnica (timeout, 500) |
| *(ausente)* | `NOT_APPLICABLE` | Domínio não existe para o modelo de negócio |

---

## 3. `agency_report_contracts` → `core_report_contracts`

As duas tabelas **coexistem** durante as Fases A–E (decisão C1). Mapeamento de campos:

| Campo em `agency_report_contracts` | Campo em `core_report_contracts` | Status |
|---|---|---|
| `client_slug` | `client_id` | **Renomeado** — semântica igual; passa a ser o slug canônico |
| `report_type` | `report_type` (enum `WEEKLY`/`MONTHLY`) | **Mesmo** |
| `business_model` | *(dentro de `contract`)* | **Movido** para o contrato e para `clients.business_model` |
| `period_start` | `period.start` | **Mesmo** (agora em sub-objeto `Period`) |
| `period_end` | `period.end` | **Mesmo** |
| `schema_version` | `schema_version` | **Mesmo** — valor muda para `"1.1"` |
| `source_run_id` | *(em `provenance`)* | **Movido** para dentro do contrato JSONB |
| `generated_at` | `generated_at` | **Mesmo** |
| `contract` (JSONB) | `contract` (JSONB) | **Mesmo** — armazenado verbatim |
| `comparison_period_start/end` | *(em `contract.report.comparison_period`)* | **Movido** para dentro do contrato JSONB |
| `updated_at` | — | **Removido** — `core_report_contracts` é imutável (append-only) |
| — | `report_contract_id` | **Novo** — ID canônico (ex: `rpc_2026_w40_lk`) |
| — | `truth_versions` | **Novo** — versões de Client/Target/ConversionMap no momento da geração |
| — | `period_closed_at` | **Novo** — timestamp de fechamento (mensal); null = aberto |
| — | `certification_coverage` | **Novo** — % de snapshots certificados no momento do fechamento |
| — | `supersedes_id` | **Novo** — apontador para versão anterior (correção) |

**Invariante:** UPDATE de valor em `core_report_contracts` é **bloqueado no banco** (trigger SQL,
Etapa 3). Correção = novo registro com `supersedes_id`.

---

## 4. `alert_rules` → `core_alert_rules`

| Campo em `alert_rules` | Campo em `core_alert_rules` | Status |
|---|---|---|
| `rule_key` | `rule_key` | **Mesmo** |
| `config jsonb` | `params jsonb` | **Renomeado** |
| `enabled` | `status` (`ACTIVE`/`INACTIVE`) | **Evoluído** |
| `severity` | *(dentro de `params`)* | **Movido** |
| `throttle_minutes` | *(dentro de `params`)* | **Movido** |
| — | `rule_version` | **Novo** — obrigatório; alertas registram a versão usada |
| — | `client_id` | **Mesmo** — já existe implicitamente |

---

## 5. Novas tabelas sem equivalência (criadas do zero na Etapa 3)

| Tabela nova | Criado em | Para que serve |
|---|---|---|
| `core_metric_snapshots` | Etapa 3 | Valores imutáveis por coletor; append-only |
| `core_data_sources` | Etapa 3 | Fontes por cliente com `source_state` e `secret_ref` |
| `core_data_health` | Etapa 3 | Estado por domínio e cliente |
| `core_certification_policies` | Etapa 3 | Janelas por fonte (default + overrides) |
| `core_jobs` / `core_job_runs` | Etapa 4 | Agendamento com `run_key` e advisory lock |
| `truth_client_versions` | Etapa 5 | Client Truth versionado (sync Notion) |
| `truth_target_versions` | Etapa 5 | Target Truth versionado |
| `truth_conversion_map` | Etapa 5 | Mapeamento de conversões por `source_conversion_id` |
| `change_log` | Etapa 8 | Mudanças AUTO + HUMAN com matching |
| `intel_diagnoses` | Etapa 9 | Hermes via Agency API |
| `intel_recommendations` | Etapa 9 | Hermes via Agency API |
| `intel_report_narratives` | Etapa 9/10 | Narrativas sobre `core_report_contracts` |
| `intel_learning_candidates` | Etapa 9 | Aprendizados propostos |
| `action_events` | Etapa 9 | Decisões: APPROVED, IGNORED, EXECUTED_CONFIRMED |
| `action_outcomes` | Etapa 13 | Resultados observados após ação |
| `alert_feedback` | Etapa 9 | USEFUL / NOISE por alerta |
| `core_alert_rules` | Etapa 9 | Regras versionadas (substitui `alert_rules`) |
| `core_alert_rule_suggestions` | Etapa 9 | Sugestões de recalibração |
| `audit_log` | Etapa 9 | Toda escrita via Agency API |

---

## 6. Itens para resolver antes da Etapa 2

| # | Item | Razão |
|---|---|---|
| I1 | Criar projeto Supabase de **staging** | Nenhuma migration nova pode rodar em produção sem staging |
| I2 | Configurar `AGENCY_API_HERMES_KEY` + `AGENCY_API_PLATFORM_KEY` + `AGENCY_API_ADMIN_KEY` no Railway | Stubs retornam 503 sem as chaves |
| I3 | Configurar `AGENCY_OS_INGEST_KEY` no Railway | Endpoint `/agency/ingest/report-contract` retorna 503 |
| I4 | Decidir `client_id` canônico dos outros clientes (além de `lk-sneakers`) | Etapa 6 precisa cadastrar `core_data_sources` para cada cliente |
| I5 | Levantar `business_model`, `timezone`, `currency` de cada cliente | Obrigatórios para qualquer cliente entrar no monitoramento |
| I6 | Levantar IDs de conversão aprovados por cliente | `truth_conversion_map` (Etapa 5) — sem ele ROAS canônico é `PARTIAL` |
