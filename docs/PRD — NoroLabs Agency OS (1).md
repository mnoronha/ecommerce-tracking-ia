# PRD — NoroLabs Agency OS

Oct 3, 2026 · @Norolabs

## 1. Visão geral

O Agency OS transforma dados de mídia e de negócio em decisões rastreáveis: Truth → Diagnosis → Decision → Action → Outcome → Learning. Relatórios passam a ser apenas uma das saídas desse ciclo.

Este PRD é a fonte de orientação para duas implementações paralelas: **Claude Code** constrói o Núcleo Determinístico e a Noro Platform; **Codex** constrói o Hermes (camada de inteligência e Telegram). Os dois só se comunicam pelo Supabase e pela Agency API definidos aqui.

**Versão 1.1 (03/10/2026).** Incorpora a revisão técnica do PRD: estados semânticos ampliados (NOT\_CONTRACTED, PERMISSION\_DENIED, NO\_DATA e outros), report contract do núcleo separado da narrativa do Hermes, estratégia de migração do Agency OS atual, IDs canônicos em conversion map e change log, timezone e moeda por cliente, regras de propagação por métrica, infraestrutura de jobs (locks, retries, replay), ambientes isolados, frontend mantido no Vercel e schema\_version em todos os objetos.

### Problema

- Platform e Hermes leem as fontes separadamente, criando risco de duas verdades para a mesma métrica.
- Não há registro do que mudou nas contas, então diagnósticos não sabem se a queda veio de uma alteração própria.
- Métricas são consultadas ao vivo, sem histórico do que se sabia no momento de cada decisão.
- Recomendações ficam enterradas em relatórios, sem ciclo de aprovação, execução e resultado.
- O sistema trata todo cliente como e-commerce, mas a carteira inclui clínica, lead-gen e leilão.

### Objetivos (MVP)

1. Uma única verdade numérica, calculada por código determinístico e lida por Platform e Hermes.
2. Data Health visível por cliente e fonte, com estados explícitos (READY, PARTIAL, STALE, MISSING).
3. Change Log alimentado manualmente (Telegram) e automaticamente (Google Ads change history).
4. Ciclo Alerta → Diagnóstico → Recomendação entregue no Telegram, com botões de ação.
5. Noro Platform com Agency View e Client View separadas por RLS.

### Não-objetivos (MVP)

- Execução automática de mudanças em campanhas, orçamentos ou anúncios.
- Approval Center complexo com múltiplos níveis de aprovação.
- Edição de Client/Target Truth pela Platform (o Notion continua sendo a entrada).
- Experiment Engine, Creative Intelligence avançada e reativação do Noro Track (Fase 4).
- Transformar a Platform em SaaS independente.

### Contexto operacional

Operação enxuta: Maicon (operação, mídia, produto) e Letícia (financeiro/admin). Carteira atual inclui e-commerce (LK Sneakers, Dipua, Enutri), clínica (Clínica Dr. Tárcio Caetano), lead-gen (Zipper Galeria) e leilão (SPITI.AUCTION). Todo desenho deve ser proporcional a essa escala.

## 2. Arquitetura alvo

O Agency OS é o sistema inteiro, não o Hermes. Um Núcleo Determinístico é a única autoridade numérica; Platform e Hermes leem a mesma verdade pela Agency API.

&#91;embedded content: arquitetura alvo · fontes até aprendizado\]

Os números nascem em código no núcleo (Railway); o Hermes só interpreta e escreve nas próprias tabelas; decisões passam pelo Telegram e voltam ao Supabase como eventos rastreáveis.

### Papel de cada componente

| Componente | Papel | Analogia |
| --- | --- | --- |
| Núcleo Determinístico | Verdade operacional: coleta, métricas, snapshots, health, change log, alertas | Contabilidade |
| Supabase | Memória operacional versionada | Arquivo |
| Agency API | Única porta de leitura e escrita | Balcão |
| Noro Platform | Interface: contexto, histórico, Agency View e Client View (Next.js no Vercel) | Cockpit |
| Hermes | Inteligência: diagnóstico, hipótese, recomendação, narrativa | Analista |
| Telegram | Canal operacional rápido: alertas, aprovações, change log | Rádio |
| Notion | Entrada humana de Client Truth e Target Truth (só ida) | Formulário |
| Obsidian | Conhecimento validado: SOPs, playbooks, aprendizados aprovados | Biblioteca |
| 1Password | Segredos; o banco guarda apenas referências | Cofre |
| Noro Track | Captura de Business Truth para lead-gen (Fase 4) | Sensor |
| GEO / AI Presence | Domínio do núcleo e módulo da Platform conforme contrato | Módulo |

## 3. Princípios invioláveis

Estas regras valem para os dois implementadores. Qualquer código que as viole é bug, mesmo que funcione.

### Regras de dados

1. **Ausência nunca é zero.** UNKNOWN, MISSING, NO\_DATA, NOT\_CONTRACTED, PERMISSION\_DENIED e NOT\_APPLICABLE são estados distintos e nunca são gravados ou exibidos como 0. Todo valor carrega `value_status` e, quando não for OK, `reason_code`.
2. **Estado da fonte ≠ estado do valor.** "Meta não contratado", "Meta contratado sem permissão", "Meta com acesso mas sem dados no período" e "Meta com erro técnico" são situações diferentes e têm estados diferentes (Seção 5).
3. **Nunca inventar** meta, margem, ticket médio ou qualquer número de negócio. Se não está no Client/Target Truth, é UNKNOWN.
4. **Nome não é identidade.** Conversões, contas, campanhas e anúncios são identificados por ID da plataforma. Nome é atributo histórico e só serve de fallback.
5. **Nunca somar aliases de conversão.** Conversões só entram na métrica canônica via `truth_conversion_map` aprovado, por `source_conversion_id`.
6. **Business Truth > atribuição de plataforma.** Receita e vendas reais vêm de e-commerce/CRM; Meta e Google são Performance Truth.
7. **Propagação explícita.** Cada métrica derivada declara quais insumos exige e o que acontece quando faltam. Não existe ranking genérico de "pior status".
8. **Nenhum número em prompt, JSON solto ou arquivo.** Números persistem apenas em tabelas versionadas, com `collected_at`, `certification_status` e `schema_version`.
9. **API é origem; snapshot é registro.** O que foi observado é gravado em snapshot imutável. Histórico, outcomes e relatórios apontam para snapshots, nunca copiam números.
10. **Tempo no fuso do cliente.** "Hoje", "ontem", fechamento mensal, ritmo de orçamento e janelas de coleta usam o `timezone` do cliente.

### Regras de autoridade

1. **Código calcula, Hermes interpreta.** Toda métrica e todo report contract são produzidos pelo Núcleo Determinístico. O Hermes nunca recalcula, corrige ou altera números.
2. **Hermes não escreve na verdade.** O Hermes lê pela Agency API e escreve apenas nos objetos de inteligência.
3. **Platform não calcula métricas canônicas.** O frontend apenas exibe projeções da Agency API.
4. **Notion → Agency OS é unidirecional.** Nenhum componente escreve no Notion nem edita Truth fora dele.
5. **Obsidian só recebe conhecimento aprovado.** Agentes geram `learning_candidates`; só humanos promovem ao vault.
6. **Nenhuma execução sem aprovação.** No MVP não há execução automática. Futuramente, só via MCP separado com approval gate.
7. **Nenhum ajuste automático de regra.** Feedback de alertas gera sugestão de recalibração; só aprovação humana cria nova versão de regra.
8. **Outcome não é causalidade.** Resultado após uma ação é rotulado "observado após", nunca "causado por".

### Regras de migração

1. **Sem big bang.** O núcleo novo roda em paralelo ao Agency OS atual (Performance Truth, Report Contract v1, runners weekly/monthly, ingest no Railway).
2. **Nenhum pipeline funcional é removido** antes que o novo passe pelos mesmos testes e produza resultado equivalente para pelo menos um cliente.
3. **Evolução, não reescrita.** O Report Contract v1 e as semânticas existentes são migrados, não descartados.
4. **Clientes não percebem a migração.** O acesso atual ao dashboard e o módulo GEO continuam funcionando durante toda a transição.

### Regras de visibilidade, segurança e ambiente

1. Todo objeto de inteligência tem `visibility_scope`: `AGENCY_ONLY` (padrão) ou `CLIENT_VISIBLE`.
2. Cliente nunca lê tabela operacional diretamente. Lê projeções da Agency API; o RLS é a segunda camada de defesa.
3. Hipóteses, diagnósticos, alertas, notas internas e change log nunca são CLIENT\_VISIBLE por padrão.
4. Segredos ficam no 1Password. O banco guarda apenas `secret_ref`, estado e `last_verified_at`.
5. Chaves legadas anon/service\_role do Supabase são substituídas antes do fim de 2026.
6. **Produção e staging são projetos Supabase separados.** Dado de staging ou sintético nunca existe no banco de produção. Migrações rodam primeiro em staging.
7. Todo payload persistido ou trafegado carrega `schema_version`.

## 4. Divisão de responsabilidades

O Claude Code fica com o Núcleo Determinístico, a Agency API e o frontend da Noro Platform. O Codex fica com tudo o que exige interpretação, linguagem natural e interação no Telegram. A hospedagem atual é preservada: frontend no Vercel, API e workers no Railway.

| Componente | Responsável | Onde roda | Lê | Escreve |
| --- | --- | --- | --- | --- |
| Núcleo Determinístico (coleta, normalização, métricas, propagação, snapshots, certificação, data health, monitores, sync Notion, change history, report contracts, outcomes) | Claude Code | Railway (workers) | APIs Meta, Google Ads, GA4, Shopify/VNDA, CRM, Notion | Tabelas `truth_*`, `core_*`, `alerts`, `change_log` (AUTO), `core_report_contracts`, `action_outcomes` |
| Agency API `/agency/v1` | Claude Code | Railway (FastAPI) | Supabase | Projeções de leitura e endpoints de escrita controlados |
| Noro Platform (Agency View e Client View) | Claude Code | Vercel (Next.js) | Agency API | Apenas transições e eventos via Agency API |
| Hermes (diagnóstico, recomendação, narrativa, learning candidates) | Codex | Ambiente próprio atual (/data/workspace) | Agency API | Objetos `intel_*` via Agency API |
| Bot Telegram (alertas, ações, change log manual, aprovações) | Codex | Ambiente do Hermes | Agency API | `action_events`, `alert_feedback`, `change_log` (HUMAN), transições de narrativa via Agency API |
| Promoção ao Obsidian | Codex (export) + Maicon (aprova) | Ambiente do Hermes | `intel_learning_candidates` aprovados | Pasta de aprendizados aprovados do vault |

### Fronteiras (contrato entre os dois)

- O **único ponto de integração** é a Agency API. O Hermes não conecta no Postgres e não produz números canônicos.
- O Hermes **pode** consultar APIs de mídia em modo leitura para detalhes exploratórios (ex.: texto de um criativo), mas todo número citado referencia um `snapshot_id` via `metric_refs`.
- O **report contract é do núcleo**; a **narrativa é do Hermes**. O Hermes nunca altera um report contract.
- O schema do Supabase é de **propriedade do Claude Code**. Mudanças pedidas pelo Codex entram por migração no repositório da Platform.
- Os contratos da Seção 6 (OpenAPI + Pydantic) são a especificação compartilhada. Quebra de contrato exige nova versão de rota e de `schema_version`.
- O OpenClaw permanece apenas como origem histórica/backup. Nenhum código novo depende dele.

### Como usar este PRD

- **Claude Code:** Seções 3 a 9, depois 11 (migração e roadmap) e 12. A Seção 9 traz os prompts por etapa.
- **Codex:** Seções 3, 4, 6, 7, 8 e 10, depois 11 e 12. A Seção 5 é referência do que existe no banco.
- Os dois leem a Seção 3 antes de qualquer código.

## 5. Modelo de dados (Supabase)

Todas as tabelas têm `client_id` (exceto catálogos globais e jobs de sistema), RLS ativo desde a criação, timestamps em UTC e `schema_version`. Prefixos indicam quem escreve: `truth_*` e `core_*` são do núcleo; `intel_*` são do Hermes via API; `action_*` e `change_log` são compartilhados com regras de origem. As tabelas atuais do Agency OS são mantidas até a migração (Seção 11).

### Mapa de tabelas

| Tabela | Escrita por | Conteúdo | Campos-chave |
| --- | --- | --- | --- |
| `clients` | Núcleo (sync Notion) | Cadastro de clientes | `client_id` (slug, ex. lk-sneakers), `name`, `business_model`, `status`, `timezone`, `currency`, `country`, `unit_aliases[]` |
| `truth_client_versions` | Núcleo (sync Notion) | Client Truth versionado | `version`, `payload jsonb`, `source_hash`, `valid_from`, `validation_status` |
| `truth_target_versions` | Núcleo (sync Notion) | Metas versionadas | `period`, `metric_key`, `target_value`, `version`, `validation_status` |
| `truth_conversion_map` | Núcleo (aprovação humana) | Mapeamento de conversões canônicas | `platform`, `account_id`, `source_conversion_id` (obrigatório quando a plataforma fornece), `source_conversion_name`, `canonical_event`, `valid_from`, `valid_to`, `status`, `approved_by`, `approved_at`, `evidence_ref` |
| `core_data_sources` | Núcleo | Fontes por cliente | `source_type`, `external_account_id`, `external_account_name`, `connection_id`, `contract_status`, `expected_source`, `source_state`, `state_reason`, `secret_ref`, `collector_version`, `timezone`, `currency`, `last_success_at`, `last_error` |
| `core_certification_policies` | Núcleo (aprovação humana) | Janelas de certificação | `source_type` (default), `account_id` e `metric_key` (overrides opcionais), `lag_days`, `version` |
| `core_jobs` / `core_job_runs` | Núcleo | Agendamento e execução | `job_type`, `run_key` (único), `status` (QUEUED, RUNNING, SUCCEEDED, FAILED, DEAD), `attempt`, `next_retry_at`, `started_at`, `finished_at`, `error`, `replay_of` |
| `core_metric_snapshots` | Núcleo | Valores observados, imutáveis | `metric_key`, `grain`, `entity_type`, `entity_id`, `period_start`, `period_end`, `value`, `unit`, `currency`, `value_status`, `reason_code`, `certification_status`, `collected_at`, `job_run_id`, `data_source_id` |
| `core_data_health` | Núcleo | Estado por domínio e cliente | `domain` (business, google\_ads, meta\_ads, ga4, conversion\_map, notion\_truth), `source_state`, `reason`, `checked_at` |
| `core_creatives` | Núcleo | Registro de criativos | `creative_id` (CR-LK-00001), `angle`, `hook`, `format`, `concept`, `platform_ad_ids[]` |
| `change_log` | Núcleo (AUTO) e Hermes (HUMAN) via API | Toda mudança em contas | `occurred_at`, `channel`, `platform_account_id`, `entity_type`, `campaign_id`, `adset_or_adgroup_id`, `ad_id`, `entity_name_at_time`, `change_type`, `before`, `after`, `source`, `external_change_id`, `reported_by`, `confidence`, `reason`, `match_status`, `matched_change_id`, `linked_action_id` |
| `core_alert_rules` | Núcleo (aprovação humana) | Regras versionadas por cliente | `rule_key`, `rule_version`, `params jsonb`, `status` |
| `core_alert_rule_suggestions` | Núcleo | Sugestões de recalibração | `rule_key`, `current_version`, `proposed_params`, `evidence` (feedbacks), `status` (PENDING, APPROVED, REJECTED) |
| `alerts` | Núcleo | Desvios detectados | `rule_key`, `rule_version`, `metric_key`, `entity`, `observed_snapshot_refs[]`, `baseline_snapshot_refs[]`, `delta_pct`, `min_volume_met`, `severity`, `status` |
| `core_report_contracts` | Núcleo | Contrato imutável de relatório | `report_contract_id`, `report_type` (WEEKLY, MONTHLY), `period`, `contract jsonb`, `truth_versions`, `period_closed_at`, `certification_coverage`, `generated_at`, `supersedes_id` |
| `intel_diagnoses` | Hermes via API | Diagnóstico estruturado | `alert_id`, `facts jsonb` (com `metric_refs`), `localization`, `hypotheses jsonb`, `confidence`, `do_not_conclude[]`, `data_limitations[]`, `visibility_scope` |
| `intel_recommendations` | Hermes via API | Recomendação formal | `diagnosis_id`, `recommendation`, `action_proposal jsonb` (com IDs de entidade), `priority`, `confidence`, `risk`, `reversible`, `expected_effect`, `review_window_days`, `status`, `visibility_scope` |
| `intel_report_narratives` | Hermes via API | Narrativa sobre um contract | `report_contract_id`, `blocks jsonb` (texto + `metric_refs`), `status` (DRAFT, READY\_FOR\_REVIEW, APPROVED, PUBLISHED, SUPERSEDED), `visibility_scope`, `approved_by`, `approved_at`, `published_at` |
| `intel_learning_candidates` | Hermes via API | Aprendizados propostos | `statement`, `scope` (CLIENT, BUSINESS\_MODEL, AGENCY), `evidence_level`, `evidence_refs[]`, `context jsonb`, `status` |
| `action_events` | Hermes (Telegram) e Platform via API | Eventos de decisão | `recommendation_id`, `event_type` (APPROVED, IGNORED, EXECUTED\_CONFIRMED, RESOLVED, EXPIRED), `actor`, `occurred_at`, `note`, `change_id` |
| `action_outcomes` | Núcleo | Resultado observado após ação | `action_event_id`, `metric_key`, `baseline_window`, `evaluation_window`, `before_snapshot_refs[]`, `after_snapshot_refs[]`, `observed_delta`, `label` (fixo: OBSERVED\_AFTER) |
| `alert_feedback` | Hermes (Telegram) via API | Útil vs ruído | `alert_id`, `feedback` (USEFUL, NOISE), `actor` |
| `audit_log` | Todos via API | Toda escrita relevante | `actor`, `action`, `object_type`, `object_id`, `payload_hash`, `at` |

### Enumerações obrigatórias

```sql
-- source_state (estado da fonte, em core_data_sources e core_data_health)
'READY' | 'PARTIAL' | 'STALE' | 'NO_DATA' | 'MISSING' | 'ACCESS_MISSING'
| 'PERMISSION_DENIED' | 'NOT_CONTRACTED' | 'NOT_APPLICABLE' | 'ERROR'
-- value_status (estado do valor, em snapshots e contratos) + reason_code livre
'OK' | 'PARTIAL' | 'STALE' | 'NO_DATA' | 'MISSING' | 'UNKNOWN' | 'NOT_APPLICABLE'
-- contract_status
'CONTRACTED' | 'NOT_CONTRACTED' | 'PENDING'
-- certification_status
'PROVISIONAL' | 'CERTIFIED' | 'SUPERSEDED'
-- business_model
'ecommerce' | 'lead_generation' | 'local_lead_generation' | 'auction'
-- visibility_scope
'AGENCY_ONLY' | 'CLIENT_VISIBLE'
-- change_log.source / match_status
'AUTO_GOOGLE_ADS' | 'AUTO_META' | 'HUMAN'  /  'MATCHED' | 'AUTO_PENDING_CONTEXT' | 'HUMAN_UNCONFIRMED'
-- narrative status
'DRAFT' | 'READY_FOR_REVIEW' | 'APPROVED' | 'PUBLISHED' | 'SUPERSEDED'
-- confidence / evidence_level
'LOW' | 'MEDIUM' | 'HIGH'  /  'OBSERVATION' | 'WEAK' | 'MODERATE' | 'STRONG'
```

### Semântica dos estados de fonte

| Estado | Significa | Exemplo |
| --- | --- | --- |
| NOT\_CONTRACTED | Canal fora do escopo do cliente | Cliente sem Meta no contrato |
| ACCESS\_MISSING | Contratado, mas nenhuma conexão configurada | Conta ainda não vinculada |
| PERMISSION\_DENIED | Conectado, mas a plataforma recusa acesso | Token sem permissão na conta Meta |
| NO\_DATA | Acesso ok, API retornou vazio no período | Nenhuma campanha ativa |
| STALE | Última coleta bem-sucedida além do limite | GA4 sem coleta há 49h |
| ERROR | Falha técnica na coleta | Timeout, erro 500 |
| NOT\_APPLICABLE | Domínio não existe para o modelo | Funil de checkout em lead-gen |

NO\_DATA nunca é convertido em 0 automaticamente. Se uma métrica pode tratar NO\_DATA como zero (ex.: investimento de uma fonte saudável e contratada sem campanhas), isso é declarado na regra de propagação daquela métrica (Seção 7).

### Certificação

- Snapshot nasce `PROVISIONAL` e vira `CERTIFIED` quando a janela da política aplicável fecha. Política = default por fonte, com override opcional por conta ou métrica. Valores iniciais: Meta 7 dias, Google Ads 14 dias, Business 3 dias.
- Recoleta após certificação gera nova linha; a anterior vira `SUPERSEDED`. Nunca há UPDATE de valor (bloqueado no banco).
- "Período fechado" é propriedade do report contract (`period_closed_at`), não do snapshot: o mensal congela o que se sabia no fechamento e registra a cobertura de certificação.

### Acesso e RLS

- `agency_admin`: acesso total via API.
- `hermes_service`: usado só pela Agency API em nome do Hermes; lê `truth_*`, `core_*`, `alerts`, `change_log`; escreve `intel_*`, `action_events`, `alert_feedback` e `change_log` com `source = 'HUMAN'`.
- `client_viewer`: **sem acesso direto a tabelas**. Recebe projeções da API filtradas por `client_id` e `CLIENT_VISIBLE`. O RLS replica a regra como segunda defesa.
- `readonly_analyst`: leitura geral, sem escrita.

### Ambientes

Produção e staging são projetos Supabase separados, com deploys separados da API (Railway) e do frontend (Vercel preview). Não existe coluna `environment`: o isolamento é físico. Dados sintéticos e testes de alerta existem só em staging.

## 6. Agency API

A Agency API é o conjunto de rotas `/agency/v1` no FastAPI do Railway. É o único caminho de leitura da Platform e o único caminho de escrita do Hermes. Autenticação por token de serviço por consumidor (`hermes_service`, `platform_web`) e sessão de usuário para clientes, com escopos mapeados aos papéis da Seção 5.

### Endpoints de leitura

| Método e rota | Consumidor | Retorna |
| --- | --- | --- |
| `GET /clients` | Platform, Hermes | Clientes ativos com `business_model`, `timezone`, `currency` |
| `GET /clients/{id}/truth` | Platform, Hermes | Client Truth e Target Truth vigentes, com `version` |
| `GET /clients/{id}/health` | Platform, Hermes | Data Health por domínio com `source_state` |
| `GET /clients/{id}/pipeline-health` | Platform, Hermes | Última coleta, certificação, avaliação de alertas, sync Notion e report por cliente |
| `GET /clients/{id}/metrics?period=&grain=&view=live\|certified` | Platform, Hermes | Metric contract (Seção 7) |
| `GET /clients/{id}/changes?since=` | Platform, Hermes | Change log |
| `GET /alerts?status=open` | Hermes, Platform | Alertas abertos |
| `GET /alerts/{id}/context` | Hermes | Pacote de diagnóstico: alerta, métricas segmentadas, mudanças em ±14 dias, criativos ativos, truth vigente, health |
| `GET /clients/{id}/recommendations` | Platform | Recomendações e status |
| `GET /clients/{id}/report-contracts` | Platform, Hermes | Report contracts gerados pelo núcleo |
| `GET /clients/{id}/reports` | Platform | Narrativas com renderização; cliente só vê PUBLISHED |
| `GET /alert-rule-suggestions` | Platform, Hermes | Sugestões de recalibração pendentes |
| `GET /system/health` | Platform, Hermes | API, banco, workers, filas e jobs atrasados |
| `GET /jobs`, `GET /jobs/{id}` | Platform (admin) | Execuções, tentativas, erros |

### Endpoints de escrita

| Método e rota | Quem | Efeito |
| --- | --- | --- |
| `POST /diagnoses` | Hermes | Cria diagnóstico ligado a um alerta |
| `POST /recommendations` | Hermes | Cria recomendação ligada a um diagnóstico |
| `POST /changes` | Hermes | Registra mudança HUMAN |
| `POST /action-events` | Hermes, Platform | Aprovação, ignorar, execução confirmada, resolução |
| `POST /alert-feedback` | Hermes | Útil/ruído |
| `POST /report-narratives` | Hermes | Cria narrativa (DRAFT) sobre um `report_contract_id` |
| `POST /report-narratives/{id}/transitions` | Hermes, Platform | Muda estado: READY\_FOR\_REVIEW, APPROVED, PUBLISHED; só `agency_admin` aprova e publica |
| `POST /alert-rule-suggestions/{id}/decision` | Platform, Hermes (em nome do Maicon) | Aprova ou rejeita; aprovação cria nova `rule_version` |
| `POST /learning-candidates` | Hermes | Cria candidato a aprendizado |
| `POST /jobs/{id}/replay` | Platform (admin) | Reexecuta um job falho |

Toda escrita valida schema e `schema_version`, rejeita `metric_refs`/`evidence_refs` inexistentes, é idempotente via `Idempotency-Key` e grava `audit_log`.

### Números por referência (metric\_refs)

Em objetos críticos (fatos de diagnóstico, recomendações, blocos de narrativa) o Hermes não digita números: usa placeholders resolvidos pela API. A API devolve o texto renderizado para Platform e Telegram, garantindo que os dois mostram o mesmo número. Texto livre fora dos placeholders ainda passa por validação textual como segunda defesa.

```json
{
  "statement": "CPA do Meta subiu {{m1}} em 3 dias, de {{m2}} para {{m3}}",
  "metric_refs": {
    "m1": {"snapshot_ids": ["snap_124"], "baseline_snapshot_ids": ["snap_098"], "presentation": "delta_pct"},
    "m2": {"snapshot_ids": ["snap_098"], "presentation": "currency"},
    "m3": {"snapshot_ids": ["snap_124"], "presentation": "currency"}
  }
}
```

### Contrato: diagnóstico

```json
{
  "schema_version": "1.1",
  "alert_id": "alt_01J...",
  "facts": [
    {"statement": "Compras Meta caíram {{m1}} vs. baseline de 14 dias", "metric_refs": {"m1": {"snapshot_ids": ["snap_124"], "baseline_snapshot_ids": ["snap_098"], "presentation": "delta_pct"}}}
  ],
  "localization": "Deterioração concentrada entre carrinho e checkout",
  "related_changes": ["chg_88"],
  "hypotheses": [
    {"statement": "Mudança de frete", "supporting_refs": [], "how_to_verify": "Confirmar com cliente"}
  ],
  "confidence": "MEDIUM",
  "do_not_conclude": ["Que o problema é o checkout sem dado da plataforma de e-commerce"],
  "data_limitations": ["GA4 STALE há 2 dias"],
  "visibility_scope": "AGENCY_ONLY"
}
```

### Contrato: recomendação

```json
{
  "schema_version": "1.1",
  "diagnosis_id": "dia_01J...",
  "recommendation": "Reduzir budget do ADV+ Geral em 15% e reavaliar em 72h",
  "action_proposal": {
    "platform": "meta_ads", "platform_account_id": "act_123", "entity_type": "campaign", "entity_id": "120210000000",
    "entity_name_at_time": "ADV+ Geral", "change_type": "BUDGET", "before": 500, "after": 425, "unit": "BRL/dia"
  },
  "priority": "HIGH",
  "confidence": "MEDIUM",
  "risk": "LOW",
  "reversible": true,
  "expected_effect": "Conter CPA enquanto criativos são renovados",
  "review_window_days": 3,
  "requires_approval": true,
  "visibility_scope": "AGENCY_ONLY"
}
```

### Contrato: mudança (change log)

```json
{
  "schema_version": "1.1",
  "client_id": "lk-sneakers",
  "occurred_at": "2026-10-03T14:30:00-03:00",
  "channel": "google_ads",
  "platform_account_id": "123-456-7890",
  "entity_type": "campaign",
  "campaign_id": "20123456789",
  "adset_or_adgroup_id": null,
  "ad_id": null,
  "entity_name_at_time": "PMax Geral",
  "change_type": "TARGET_ROAS",
  "before": 400,
  "after": 500,
  "reason": "Campanha gastando acima do ritmo",
  "source": "HUMAN",
  "reported_by": "maicon",
  "confidence": "CONFIRMED",
  "linked_action_id": null
}
```

Quando o Maicon informa a mudança só pelo nome, o Hermes resolve o ID consultando as entidades do cliente na API e pede confirmação se houver ambiguidade. `change_type` permitido: BUDGET, TARGET\_ROAS, TARGET\_CPA, BID\_STRATEGY, CREATIVE\_LAUNCH, CREATIVE\_PAUSE, CAMPAIGN\_LAUNCH, CAMPAIGN\_PAUSE, AUDIENCE, LANDING\_PAGE, OFFER, TRACKING, OTHER.

### Contrato: narrativa de relatório

```json
{
  "schema_version": "1.1",
  "report_contract_id": "rpc_2026_09_lk",
  "blocks": [
    {"section": "resultado", "statement": "Receita do mês fechou em {{m1}}, {{m2}} da meta", "metric_refs": {"m1": {"contract_path": "metrics.revenue_business", "presentation": "currency"}, "m2": {"contract_path": "metrics.revenue_business.target_attainment", "presentation": "pct"}}}
  ],
  "visibility_scope": "AGENCY_ONLY"
}
```

Narrativas referenciam o report contract por `contract_path`; nunca trazem números próprios.

## 7. Business models e metric contracts

O `business_model` de cada cliente comanda o sistema inteiro: quais métricas são válidas, qual funil é analisado, quais regras de alerta valem e qual template de relatório é usado. Não existe tela ou alerta universal baseado em ROAS.

```
Client Truth → business_model → metric contract → propagation rules → funnel contract → alert rules → report template
```

### Métricas por modelo

| Modelo | Clientes atuais | Métricas principais | Funil | Business Truth |
| --- | --- | --- | --- | --- |
| `ecommerce` | LK Sneakers, Dipua, Enutri | Receita, pedidos, AOV, MER, ROAS por canal, CAC, novos clientes | Sessão → ATC → Begin Checkout → Compra | Shopify, VNDA |
| `local_lead_generation` | Clínica Dr. Tárcio Caetano | Leads, leads qualificados, agendamentos, comparecimentos, vendas, CPL, CPQL, custo por agendamento | Lead → contato → qualificado → agendado → compareceu → venda | WhatsApp/CRM (hoje ausente; Noro Track na Fase 4) |
| `lead_generation` | Zipper Galeria | Leads, CPL e a regra própria de reporte do cliente | Lead → qualificado | CRM do cliente |
| `auction` | SPITI.AUCTION | Cadastros, cadastros qualificados, lances, compradores, receita | Cadastro → qualificado → lance → compra | Plataforma de leilão |

Clientes sem modelo, timezone ou moeda definidos ficam fora do monitoramento até o Client Truth ser completado; o Data Health acusa `notion_truth` como PARTIAL.

### Metric contract (resposta de `GET /metrics`)

```json
{
  "schema_version": "1.1",
  "client_id": "lk-sneakers",
  "business_model": "ecommerce",
  "timezone": "America/Sao_Paulo",
  "currency": "BRL",
  "period": {"start": "2026-09-26", "end": "2026-10-02"},
  "view": "live",
  "truth_versions": {"client": 7, "target": 3, "conversion_map": 2},
  "metrics": [
    {"metric_key": "revenue_business", "value": 84210.50, "unit": "BRL", "value_status": "OK", "certification_status": "PROVISIONAL", "snapshot_ids": ["snap_501"], "target": 90000, "target_status": "OK"},
    {"metric_key": "mer", "value": 5.4, "unit": "x", "value_status": "OK", "certification_status": "PROVISIONAL", "snapshot_ids": ["snap_502"]},
    {"metric_key": "roas_meta", "value": null, "value_status": "UNKNOWN", "reason_code": "SOURCE_PERMISSION_DENIED"},
    {"metric_key": "ga4_sessions", "value": null, "value_status": "STALE", "reason_code": "LAST_SUCCESS_49H"}
  ],
  "health": {"business": "READY", "google_ads": "READY", "meta_ads": "PERMISSION_DENIED", "ga4": "STALE", "conversion_map": "PARTIAL"}
}
```

### Regras de propagação

Cada métrica derivada tem uma regra declarada em código (`core/metrics/rules/`), com testes para cada estado de insumo. Não existe herança genérica de "pior status".

| Métrica | Exige | Se faltar | Resultado |
| --- | --- | --- | --- |
| MER | `revenue_business` OK ou PARTIAL; investimento de todas as fontes contratadas OK | Receita indisponível por qualquer motivo | UNKNOWN, `reason_code: BUSINESS_TRUTH_UNAVAILABLE` |
| MER | idem | Fonte de mídia contratada STALE/ERROR/PERMISSION\_DENIED | UNKNOWN, `reason_code: SPEND_INCOMPLETE` |
| MER | idem | Fonte NOT\_CONTRACTED | Ignorada no denominador |
| MER | idem | Fonte contratada e saudável com NO\_DATA | Investimento dessa fonte conta como 0 (única exceção declarada) |
| ROAS por plataforma | Conversões mapeadas no `truth_conversion_map` + investimento OK | Sem mapeamento aprovado | PARTIAL, `reason_code: CONVERSION_MAP_INCOMPLETE` |
| CPA / CPL / CPQL | Conversões canônicas + investimento OK | Conversões = 0 com fonte READY | NOT\_APPLICABLE, `reason_code: ZERO_CONVERSIONS` (nunca divisão por zero) |
| Atingimento de meta | Valor OK + meta no Target Truth | Meta ausente | `target_status: UNKNOWN` |

Novas métricas só entram no contrato com sua regra e seus testes.

### Regras de alerta (versão inicial)

| Regra | Condição | Volume mínimo |
| --- | --- | --- |
| Custo por resultado | CPA/CPL/CPQL +30% vs. baseline de 14 dias, janela de 3 dias | ≥ 15 conversões no baseline e ≥ 5 na janela |
| Volume | Conversões −30% com investimento estável (±10%) | ≥ 15 conversões no baseline |
| Ritmo de gasto | Gasto acumulado do mês > 110% do ritmo do orçamento aprovado | Orçamento definido no Target Truth |
| Data Health | Fonte contratada passa a STALE, ACCESS\_MISSING, PERMISSION\_DENIED ou ERROR | Sempre |
| Meta do mês | Projeção linear < 80% da meta a partir do dia 10 (fuso do cliente) | Meta definida |

Nenhum alerta de performance dispara se a fonte envolvida não estiver READY; nesse caso dispara só o alerta de Data Health. Fontes NOT\_CONTRACTED nunca geram alerta. As regras são versionadas em `core_alert_rules`. O feedback em `alert_feedback` gera **sugestões** em `core_alert_rule_suggestions` (ex.: três RUÍDO na mesma regra e cliente em 30 dias); só a aprovação do Maicon cria nova `rule_version`.

## 8. Fluxos ponta a ponta

Cada fluxo indica quem executa cada passo: **\[CC\]** Claude Code (núcleo/API/Platform), **\[CX\]** Codex (Hermes/Telegram), **\[M\]** Maicon.

### 8.1 Jobs, coleta e certificação

1. &#91;CC\] Todo trabalho agendado vira um `core_job_run` com `run_key` determinístico (ex.: `collect:lk-sneakers:meta_ads:2026-10-03T12`). A unicidade do `run_key` no banco impede execução duplicada; um lock (advisory lock do Postgres) impede concorrência após reinício de worker.
2. &#91;CC\] Coleta a cada 6h para mídia e 1x/dia para GA4 e Business, no fuso do cliente.
3. &#91;CC\] Falha gera retry com backoff exponencial (3 tentativas); esgotadas, o run vai para DEAD e aparece em `GET /system/health`. Replay manual via `POST /jobs/{id}/replay`.
4. &#91;CC\] Coleta normaliza por IDs da plataforma, grava snapshots PROVISIONAL e atualiza `core_data_sources.source_state` e `core_data_health`.
5. &#91;CC\] Job diário certifica snapshots conforme a política aplicável; recoleta divergente gera nova linha e marca a anterior como SUPERSEDED.

### 8.2 Alerta → diagnóstico → Telegram

1. &#91;CC\] Após cada coleta, monitores avaliam a versão vigente de `core_alert_rules`. Atendidas condição e volume mínimo com fonte READY, cria `alerts` (OPEN) com referências a snapshots.
2. &#91;CX\] Hermes consulta `GET /alerts?status=open` (polling de 5 min).
3. &#91;CX\] Busca `GET /alerts/{id}/context` e gera diagnóstico pelo contrato da Seção 6, com números via `metric_refs`.
4. &#91;CX\] Se houver ação plausível, cria recomendação com `action_proposal` identificado por IDs.
5. &#91;CX\] Envia ao Telegram o texto renderizado pela API com botões: **Ver evidências · Aprovar · Ignorar · Útil · Ruído**.
6. &#91;M\] Escolhe; \[CX\] registra em `action_events` ou `alert_feedback`.

```
🔴 LK Sneakers · Meta Ads
CPA +31% em 3 dias (R$ 48 → R$ 63) · volume ok
Localização: ADV+ Geral concentra 80% da piora
Mudança relacionada: criativo pausado em 30/09
Sugestão: reduzir budget 15% e reavaliar em 72h
Confiança: média · Risco: baixo
[Ver evidências] [Aprovar] [Ignorar] [Útil] [Ruído]
```

### 8.3 Change log

1. &#91;M\] Escreve no Telegram: "LK: alterei tROAS da PMax Geral de 400 para 500 porque estava gastando acima".
2. &#91;CX\] Hermes resolve a entidade para IDs (conta e campanha) via API, estrutura o contrato e pede confirmação; havendo mais de uma entidade possível, pergunta qual.
3. &#91;M\] Confirma; \[CX\] envia `POST /changes` com `source = HUMAN`.
4. &#91;CC\] Job de change history do Google Ads (depois Meta) grava mudanças AUTO com `external_change_id`.
5. &#91;CC\] Matching por `platform_account_id` + ID da entidade + `change_type` + janela de ±2h; nome só como fallback. Casamento → MATCHED e herda o `reason`. AUTO sem par → AUTO\_PENDING\_CONTEXT. HUMAN sem par em 24h → HUMAN\_UNCONFIRMED.

### 8.4 Ação → outcome

1. &#91;M\] Aprova recomendação → `action_events` APPROVED. Aprovar não significa executado.
2. &#91;M\] Executa a mudança manualmente.
3. &#91;CC\] Change history detecta mudança compatível com o `action_proposal` (mesmos IDs e `change_type`); \[CX\] pergunta "Essa mudança executa a recomendação X?" e, confirmada, grava EXECUTED\_CONFIRMED com `change_id`.
4. &#91;CC\] A review window conta da execução confirmada. No fim, grava `action_outcomes` com referências aos snapshots de baseline e avaliação, delta observado e rótulo OBSERVED\_AFTER.
5. &#91;CX\] Envia o outcome ao Telegram e pode propor learning candidate.

Aprovada sem execução confirmada: lembrete em 7 dias, EXPIRED em 14.

### 8.5 Learning

1. &#91;CX\] Gera `intel_learning_candidates` com `scope`, `evidence_level`, `evidence_refs` e `context` (mudanças no período, volume, sazonalidade).
2. &#91;M\] Revisa: aprova, rejeita ou pede mais evidência.
3. &#91;CX\] Aprovados são exportados como nota markdown para a pasta de aprendizados aprovados do vault.
4. Escopo AGENCY exige repetição em pelo menos 2 clientes e aprovação explícita.

### 8.6 Sync Notion

1. &#91;CC\] Job a cada 30 min lê Client Truth e Target Truth.
2. &#91;CC\] Calcula `source_hash`; se mudou, valida o schema (incluindo `business_model`, `timezone`, `currency`).
3. &#91;CC\] Válido → nova versão vigente. Inválido → mantém a última válida e gera alerta de Data Health com o campo problemático.

### 8.7 Relatórios

1. &#91;CC\] Gera `core_report_contracts` imutável: métricas, metas, health, mudanças, ações e outcomes do período. O mensal registra `period_closed_at` e a cobertura de certificação. Correção gera novo contract que substitui o anterior.
2. &#91;CX\] Hermes cria `intel_report_narratives` (DRAFT) referenciando o contract por `contract_path`, sem números próprios, e move para READY\_FOR\_REVIEW.
3. &#91;M\] Aprova (APPROVED). Publicação é uma ação separada (PUBLISHED); só então o relatório aparece na Client View. Nova versão publicada marca a anterior SUPERSEDED.

Weekly responde: o que mudou, o que precisa de atenção, o que fizemos, o que faremos. Monthly responde: resultado, meta, evolução, aprendizados, plano do próximo mês. O Report Contract v1 atual é a base do `core_report_contracts` (Seção 11).

### 8.8 Recalibração de alertas

1. &#91;CC\] Job semanal agrega `alert_feedback`; padrão de ruído gera `core_alert_rule_suggestions` (PENDING) com a evidência.
2. &#91;CX\] Hermes apresenta a sugestão no Telegram com botões Aprovar/Rejeitar.
3. &#91;M\] Aprova → \[CC\] cria nova `rule_version`; alertas futuros registram a versão usada.

### 8.9 Falha de pipeline

1. &#91;CC\] Job DEAD, worker sem heartbeat ou atraso de coleta acima do limite viram alerta de sistema.
2. &#91;CX\] Hermes envia ao Telegram com link para `GET /jobs/{id}` na Agency View.
3. &#91;M\] Corrige a causa e dispara replay.

## 9. Guia de implementação — Claude Code

Escopo: Núcleo Determinístico e Agency API (FastAPI e workers no Railway), schema do Supabase e frontend da Noro Platform (Next.js no Vercel). A implementação é feita em etapas, uma sessão por etapa, sempre em branch e validada em staging antes de produção.

### Estrutura de código sugerida (backend)

```
api/
  agency_api/v1/        # rotas, schemas Pydantic, auth por escopo, renderização de metric_refs
  core/
    jobs/               # core_jobs, run_key, locks, retries, replay, heartbeat
    collectors/         # google_ads, meta_ads, ga4, shopify, vnda, notion
    normalize/          # IDs de plataforma → metric_key + entidade
    metrics/            # funções puras por metric_key
    metrics/rules/      # regras de propagação por métrica
    certification/      # políticas, SUPERSEDED
    health/             # source_state, data health, pipeline health, system health
    alerts/             # monitores, regras versionadas, sugestões de recalibração
    changes/            # change history + matching por ID
    reports/            # core_report_contracts (evolução do Report Contract v1)
    outcomes/           # action_outcomes por snapshot refs
    truth/              # sync Notion, validação, versões, conversion map
    domains/geo/        # GEO/AI Presence existente
  migrations/           # SQL versionado (dono único do schema)
  tests/
```

O frontend Next.js continua no seu repositório e deploy atuais no Vercel, consumindo apenas a Agency API.

### Requisitos dos workers

- Todo job tem `run_key` determinístico com unicidade no banco e advisory lock do Postgres. Nada de Redis ou infraestrutura nova sem necessidade.
- Retry com backoff exponencial (3 tentativas), estado DEAD, replay manual e heartbeat por worker.
- Reinício do Railway não pode gerar execução duplicada nem snapshot duplicado.

### Regras de implementação

- Frontend nunca calcula métrica. Número novo nasce em `core/metrics` com regra de propagação e testes.
- Valores não OK aparecem com estado e motivo ("Meta sem permissão de acesso", "Desatualizado há 49h", "Fora do contrato"), nunca como 0 ou traço sem explicação.
- Snapshots são append-only; UPDATE de `value` bloqueado no banco.
- Toda identificação de conversão, conta e entidade usa ID da plataforma.
- Cliente nunca lê tabela diretamente; só projeções da API.
- Nenhum pipeline atual é removido sem teste de equivalência aprovado (Seção 11).
- Antes de qualquer operação de escrita na Google Ads API (futuro Executor), revisar o caso de uso declarado na solicitação de Basic Access.

### CLAUDE.md (colar na raiz do repositório)

```markdown
# Noro Platform — instruções para Claude Code
Você implementa o Núcleo Determinístico, a Agency API e a Noro Platform do NoroLabs Agency OS.
O PRD completo está em docs/PRD.md (v1.1). Leia a Seção 3 antes de qualquer mudança e a seção indicada em cada tarefa.

Regras invioláveis:
- Ausência nunca é zero. Use source_state e value_status + reason_code da Seção 5. NO_DATA só vira 0 se a regra de propagação da métrica declarar.
- Toda métrica é calculada em core/metrics com regra de propagação e testes. Frontend nunca calcula.
- Nome não é identidade: conversões, contas e entidades por ID da plataforma.
- Snapshots são append-only. Certificação gera nova linha.
- Report contract é do núcleo e imutável; narrativa é do Hermes.
- Você é dono do schema. Toda mudança passa por migrations/ e roda primeiro em staging.
- Cliente só lê projeções da API. RLS em toda tabela como segunda defesa.
- Todo job tem run_key único, lock, retry com backoff e replay.
- schema_version em todo payload. Quebra de contrato = nova versão.
- Nunca escreva no Notion. Nunca guarde segredo no banco, só secret_ref.
- Nenhuma operação de escrita em plataformas de mídia.

Regras de migração:
- Clientes já usam o dashboard e o módulo GEO está ativo: nada pode quebrá-los.
- O núcleo novo roda em paralelo ao Agency OS atual (Performance Truth, Report Contract v1, runners, ingest).
- Nenhum pipeline atual é removido sem teste de equivalência aprovado por mim.
- Trabalhe em branch. Nunca rode migração contra produção sem minha aprovação explícita.
```

### Como conduzir cada etapa

1. Sessão nova (`/clear`) e modo de plano (Shift+Tab) antes de colar o prompt.
2. Revise o plano; só então autorize a implementação.
3. Ele roda os testes; você revisa o diff, valida em staging e faz merge.
4. Cada etapa termina com um arquivo `docs/etapa-N.md` resumindo o que foi feito, decisões e pendências.

### Prompts por etapa

**Etapa 0 — Reconhecimento e inventário de migração (sem código)**

```
Leia docs/PRD.md inteiro (v1.1), com atenção às Seções 3 e 11. Não escreva código.

Produza docs/etapa-0-reconhecimento.md com:
1. Inventário do Agency OS atual: Performance Truth, Report Contract v1, runners weekly/monthly, ingest no Railway, onboarding registry, capabilities, governance, módulo GEO, frontend no Vercel. Para cada item: onde está, o que faz, quem consome.
2. Schema atual do Supabase, RLS, papéis e uso das chaves legadas anon/service_role.
3. Como os clientes acessam o dashboard hoje.
4. Semânticas de estado já usadas no código (ex.: permission_denied, not_contracted, no_data) e como mapeiam para os enums da Seção 5.
5. Mapa de migração: para cada componente atual, se será mantido, evoluído ou substituído, e em qual fase (A a F da Seção 11).
6. Conflitos entre o código atual e o PRD, riscos e perguntas que preciso responder antes de começar.
```

**Etapa 1 — Contratos v1.1**

```
Leia as Seções 3, 5, 6 e 7 de docs/PRD.md.

Crie, sem lógica de negócio:
- Schemas Pydantic de todos os contratos (diagnóstico, recomendação, mudança, action event, alert feedback, narrativa, learning candidate, metric contract, report contract), com todos os enums da Seção 5, schema_version e metric_refs.
- Rotas /agency/v1 da Seção 6 como stubs que retornam exemplos válidos, incluindo system health e jobs.
- docs/agency-api-v1.openapi.json gerado.
- Testes validando os exemplos do PRD contra os schemas.
- Onde o Report Contract v1 atual já define campos, preserve nomes e semântica e documente a correspondência.

O OpenAPI será usado pelo Codex para implementar o Hermes contra um mock. Mostre o plano primeiro.
```

**Etapa 2 — Ambientes, auth e segredos**

```
Leia as Seções 3 e 5 (Acesso e RLS, Ambientes) de docs/PRD.md e docs/etapa-0-reconhecimento.md.

- Configure o projeto Supabase de staging separado de produção e o deploy de staging da API no Railway e da Platform em preview no Vercel.
- Crie os papéis agency_admin, hermes_service, client_viewer e readonly_analyst.
- Implemente auth da Agency API por token de serviço com escopos e sessão de usuário para clientes.
- Planeje a troca das chaves legadas anon/service_role sem derrubar o acesso dos clientes. Mostre a ordem antes de executar.
- Integre secret_ref com o 1Password. Nenhum segredo no banco.

Critério: clientes atuais continuam acessando o dashboard normalmente.
```

**Etapa 3 — Schema e RLS**

```
Implemente via migrations/ as tabelas, enums e políticas da Seção 5 de docs/PRD.md, sem remover tabelas atuais.

- RLS na criação de toda tabela com client_id; client_viewer sem acesso direto.
- UPDATE de value em core_metric_snapshots bloqueado no banco.
- run_key único em core_job_runs.
- clients com timezone, currency e country obrigatórios para clientes monitorados.

Testes: falha se existir tabela com client_id sem RLS; testes por papel; UPDATE em snapshot falha; run_key duplicado falha. Rode primeiro em staging.
```

**Etapa 4 — Infraestrutura de jobs e system health**

```
Leia as Seções 8.1, 8.9 e 9 (Requisitos dos workers) de docs/PRD.md.

Implemente core/jobs: agendamento, run_key, advisory lock, retry com backoff (3 tentativas), estado DEAD, replay e heartbeat.
Implemente GET /system/health, GET /jobs, GET /jobs/{id}, POST /jobs/{id}/replay e GET /clients/{id}/pipeline-health.

Critério: reiniciar o worker no meio de um job não gera execução duplicada; job que falha 3 vezes aparece como DEAD no system health.
```

**Etapa 5 — Truth sync Notion**

```
Leia as Seções 5, 7 e 8.6 de docs/PRD.md.

Implemente o job (30 min) Notion → clients, truth_client_versions e truth_target_versions:
- source_hash; nova versão só quando mudou.
- Validação incluindo business_model, timezone e currency; inválido mantém a última versão válida e gera alerta de Data Health.
- Estrutura de truth_conversion_map com source_conversion_id e aprovação humana.
- Fluxo unidirecional: nenhuma escrita no Notion.

Antes de codar, mostre como vai mapear as databases do Notion; eu passo IDs e estrutura.
```

**Etapa 6 — Fontes e coletores da LK (em paralelo ao atual)**

```
Leia as Seções 3, 5 e 8.1 de docs/PRD.md e o mapa de migração da Etapa 0.

Para lk-sneakers apenas:
- Cadastre core_data_sources com external_account_id, contract_status, source_state, timezone e currency.
- Implemente coletores Google Ads, Meta Ads, GA4 e Shopify como jobs, reaproveitando a coleta atual.
- Normalize por IDs de plataforma e grave snapshots PROVISIONAL com value_status e reason_code.
- Distinga NOT_CONTRACTED, ACCESS_MISSING, PERMISSION_DENIED, NO_DATA, STALE e ERROR. Falha nunca grava zero.
- O pipeline atual continua rodando sem alteração.
```

**Etapa 7 — Métricas, propagação, certificação e equivalência (Fase B)**

```
Leia as Seções 3, 5 (Certificação) e 7 de docs/PRD.md.

- core/metrics do modelo ecommerce e core/metrics/rules com a tabela de propagação da Seção 7, testada para cada estado de insumo.
- ROAS por plataforma só com truth_conversion_map aprovado por source_conversion_id.
- core_certification_policies (default por fonte + overrides) e job de certificação com SUPERSEDED.
- core_data_health por domínio.
- GET /clients/{id}/metrics (live e certified) e GET /clients/{id}/health reais.
- Relatório de equivalência em docs/etapa-7-equivalencia.md comparando o metric contract novo com o Performance Truth atual para a LK nas últimas 4 semanas, explicando cada diferença.

Critério: desligar a credencial do GA4 em staging deixa ga4 STALE/ERROR sem nenhum 0 falso.
```

**Etapa 8 — Change log**

```
Leia as Seções 6 (contrato de mudança) e 8.3 de docs/PRD.md.

- Change history do Google Ads (somente leitura) como job, gravando AUTO_GOOGLE_ADS com external_change_id e IDs de entidade.
- POST /changes para HUMAN com validação e Idempotency-Key.
- Endpoint de busca de entidades do cliente por nome para o Hermes resolver IDs.
- Matching por account_id + entity_id + change_type + janela de ±2h, nome só como fallback; estados MATCHED, AUTO_PENDING_CONTEXT e HUMAN_UNCONFIRMED.
- GET /clients/{id}/changes.

Nenhuma escrita na API do Google Ads.
```

**Etapa 9 — Alertas e API completa**

```
Leia as Seções 6, 7 (regras de alerta), 8.2 e 8.8 de docs/PRD.md.

- core_alert_rules versionadas com as regras iniciais e volume mínimo; sem alerta de performance com fonte não READY; NOT_CONTRACTED nunca alerta.
- Monitores após cada coleta, com snapshot refs no alerta.
- GET /alerts e GET /alerts/{id}/context completos.
- Endpoints de escrita da Seção 6 com validação de metric_refs, renderização de placeholders, Idempotency-Key e audit_log.
- Sugestões de recalibração (sem ajuste automático) e endpoint de decisão.
- Substitua os stubs da Etapa 1 mantendo o contrato idêntico ao OpenAPI.

Critério: teste de contrato da Etapa 1 passando contra a API real.
```

Ao fim da Etapa 9 roda o teste ponta a ponta da Seção 12 com o Codex (Gate 2).

**Etapa 10 — Report contracts**

```
Leia as Seções 5, 6 e 8.7 de docs/PRD.md e o inventário do Report Contract v1 da Etapa 0.

- Evolua o Report Contract v1 para core_report_contracts imutável, preservando campos e semântica existentes; documente o mapeamento.
- Geração weekly e monthly no núcleo, com period_closed_at e cobertura de certificação no mensal.
- intel_report_narratives com estados DRAFT → READY_FOR_REVIEW → APPROVED → PUBLISHED → SUPERSEDED e endpoint de transição.
- Os runners atuais continuam gerando relatórios; compare as duas saídas para a LK em docs/etapa-10-equivalencia.md.
```

**Etapa 11 — Agency View (Fase C)**

```
Leia as Seções 3 e 9 de docs/PRD.md. Trabalhe no frontend Next.js atual (Vercel).

Implemente a Agency View consumindo só a /agency/v1:
- Agency Dashboard: o que preciso fazer hoje (clientes em atenção, alertas, aprovadas não executadas, outcomes, sugestões de recalibração, falhas de pipeline).
- Client Overview com faixa de Data Health mostrando source_state e motivo.
- Toggle Live/Certified com selo PROVISIONAL.
- Alerts, Recommendations, Change History, Reports (com estados) e System Health.

Nada calcula no frontend. Estados não OK aparecem com texto explicativo. Comece pelo layout para eu aprovar.
```

**Etapa 12 — Client View**

```
Leia as Seções 3 e 5 (Acesso e RLS) de docs/PRD.md.

Migre o acesso atual dos clientes para a Client View: Performance, Metas, Relatórios PUBLISHED e insights CLIENT_VISIBLE, tudo via projeções da API.
- Usuários existentes continuam funcionando durante a migração.
- Teste provando que nada AGENCY_ONLY, nenhum alerta, change log ou diagnóstico chega ao cliente.
- Preserve o módulo GEO para quem o contratou.
```

**Etapa 13 — Outcomes, expansão e compatibility mode (Fase E)**

```
Leia as Seções 7, 8.4 e 11 de docs/PRD.md.

- action_outcomes com baseline/evaluation windows e snapshot refs, rótulo OBSERVED_AFTER.
- Metric contracts e regras de propagação de local_lead_generation, lead_generation e auction; Business Truth ausente aparece como MISSING ou NOT_CONTRACTED conforme o caso.
- Change history do Meta.
- Coloque os runners antigos em compatibility mode (rodam, mas não são fonte primária) e liste em docs/etapa-13-depreciacao.md o que pode ser removido, com a evidência de equivalência de cada item. Não remova nada.
```

**Etapa 14 — Depreciação (Fase F, só com aprovação explícita)**

```
Com base em docs/etapa-13-depreciacao.md e nos itens que eu aprovei, remova os pipelines antigos um por vez, cada um em PR separado, com rollback documentado.
```

## 10. Guia de implementação — Codex

Escopo: Hermes como camada de inteligência e o bot Telegram, no ambiente próprio atual (`/data/workspace`). O Hermes deixa de ser autoridade numérica: números críticos entram por `metric_refs` resolvidos pela Agency API.

### Mudança em relação ao Hermes atual

| Hoje | Depois |
| --- | --- |
| Consulta Meta, GA4 e Google Ads direto para análises | Lê métricas e contexto pela Agency API; APIs de mídia só para detalhes exploratórios |
| Calcula métricas no raciocínio | Nunca calcula; cita números por `metric_refs` |
| Gera Report Contract e narrativa | Lê `core_report_contracts` do núcleo e escreve só a narrativa |
| Lê Notion e Obsidian como arquivos soltos | Lê Truth versionada pela API; lê Obsidian aprovado como conhecimento |
| Pode escrever nas pastas do vault | Gera learning candidates; exporta só após aprovação |
| Análises em varredura | Diagnóstico disparado por alerta |

A migração do Hermes para a Agency API é a Fase D da Seção 11: até a API real passar no Gate 2, o Hermes atual continua operando como hoje e o novo código roda contra o mock.

### Estrutura de código sugerida

```
hermes/
  agency_client/       # cliente HTTP tipado da /agency/v1 (gerado do OpenAPI)
  diagnosis/           # prompt + validação do contrato
  recommendation/      # geração e validação
  changes/             # linguagem natural → contrato de mudança com IDs
  reports/             # narrativa sobre report contract
  learning/            # candidates + export Obsidian
  telegram/            # bot, mensagens, botões, callbacks
  knowledge/           # índice do Obsidian aprovado
  schedulers/          # polling de alertas, lembretes
  tests/
```

### Ordem de entrega

1. **Agency client** gerado do OpenAPI, com retry, idempotência, `schema_version` e token `hermes_service`. Desenvolvimento contra mock até a Etapa 9 do Claude Code.
2. **Telegram base:** allowlist de user\_id, comandos `/clientes`, `/saude <cliente>`, `/alertas`, `/sistema`.
3. **Change log manual:** parser → resolução de IDs via API → confirmação → `POST /changes`.
4. **Polling de alertas** + diagnóstico com `metric_refs` + validação local.
5. **Recomendação** com `action_proposal` por IDs + mensagem com botões → `action_events` / `alert_feedback`.
6. **Confirmação de execução:** mudança AUTO compatível → pergunta → EXECUTED\_CONFIRMED; lembrete em 7 dias, expiração em 14.
7. **Outcome** ao fim da review window, com rótulo "observado após".
8. **Narrativas** sobre `core_report_contracts` via `contract_path`, transição para READY\_FOR\_REVIEW e aprovação/publicação pelo Telegram.
9. **Sugestões de recalibração** e **alertas de sistema** no Telegram.
10. **Learning candidates** + export aprovado para o Obsidian.

### Regras do diagnóstico

- Ordem: observação → mudança → localização → evidências → hipóteses → confiança.
- Todo item em `facts` usa `metric_refs` existentes no contexto recebido. Fato sem referência vira hipótese ou é descartado.
- Sempre checar `related_changes` antes de propor causa externa; mudança própria na janela é a primeira hipótese.
- Fonte com `source_state` diferente de READY vai para `data_limitations` e reduz a confiança. NOT\_CONTRACTED não é limitação; é fora de escopo.
- Preencher `do_not_conclude` com conclusões tentadoras sem evidência.
- Clientes sem Business Truth: confiança máxima MEDIUM.
- Default AGENCY\_ONLY; nunca marcar CLIENT\_VISIBLE automaticamente.

### Validação antes de enviar

Primeira defesa: números críticos só via placeholders `{{mN}}` com `metric_refs`; o Hermes não digita esses números. Segunda defesa: números em texto livre (hipóteses, narrativa) são conferidos contra o contexto com tolerância de arredondamento; divergência bloqueia o envio e gera log. A API repete a validação de schema e de referências.

### Telegram

- Só usuários na allowlist acionam botões; toda ação registra `actor`.
- Mensagens usam o texto renderizado pela API, para Telegram e Platform mostrarem o mesmo número.
- "Ver evidências" abre a tela do alerta na Agency View.
- Alertas do mesmo cliente em menos de 1h viram uma mensagem.
- Aprovar e publicar relatório são botões diferentes.

### AGENTS.md (colar na raiz do repositório)

```markdown
# Hermes — instruções para Codex
Você implementa a camada de inteligência e o bot Telegram do NoroLabs Agency OS.
PRD em docs/PRD.md (v1.1). Leia as Seções 3, 4, 6, 7, 8 e 10 antes de qualquer mudança.
Regras invioláveis:
- Você não é autoridade numérica. Números críticos só via metric_refs resolvidos pela Agency API (/agency/v1).
- Nunca calcule ROAS, MER, CPA, CPL ou receita. Ausência de dado nunca é zero; respeite source_state e value_status.
- Report contract é do núcleo. Você escreve só a narrativa, referenciando contract_path.
- Nome não é identidade: mudanças e propostas de ação usam IDs de conta e entidade.
- Você só escreve via API em: diagnoses, recommendations, changes (HUMAN), action-events, alert-feedback, report-narratives, learning-candidates e decisões aprovadas pelo Maicon.
- Nunca escreva no Notion. Nunca escreva no Obsidian sem learning candidate aprovado.
- Nenhuma ação em plataformas de mídia. Aprovar não executa nada.
- Separe fato, diagnóstico e hipótese. Default AGENCY_ONLY. Outcome é "observado após".
- schema_version em todo payload. O schema do banco pertence ao repositório da Platform.
- O Hermes atual continua operando até a Fase D da migração; não desligue nada existente.
```

## 11. Migração e roadmap

O Agency OS novo substitui o atual por evolução, nunca por big bang: o núcleo novo roda em paralelo, prova equivalência na LK Sneakers e só então assume. Nenhum pipeline funcional é removido antes de o substituto passar nos mesmos testes e produzir resultado equivalente para pelo menos um cliente.

### Fases de migração

| Fase | O que acontece | Etapas Claude Code | Critério para avançar |
| --- | --- | --- | --- |
| A — Paralelo | Núcleo novo coleta e calcula ao lado do Performance Truth atual; nada antigo muda | 0 a 6 | Snapshots da LK sendo gerados com estados corretos |
| B — Equivalência | Metric contract novo comparado ao atual para a LK | 7 e 10 | Diferenças explicadas e aceitas por Maicon |
| C — Platform na API | Agency View e Client View passam a ler só a Agency API | 11 e 12 | Clientes acessando sem regressão |
| D — Hermes na API | Codex troca leitura local pela Agency API | Ordem de entrega do Codex | Gate 2 aprovado |
| E — Compatibility mode | Runners weekly/monthly e ingest antigos rodam, mas não são fonte primária | 13 | Duas semanas sem divergência relevante |
| F — Depreciação | Remoção item a item, com rollback documentado | 14 | Aprovação explícita por item |

O Report Contract v1 é evoluído para `core_report_contracts`, preservando campos e semântica existentes. Semânticas de estado já usadas no código (ex.: permission\_denied, not\_contracted) são mapeadas para os enums da Seção 5, não descartadas.

### Roadmap

O MVP são as Fases 1 e 2 mais a Agency View da Fase 3, entregues primeiro como fatia vertical na LK Sneakers e só depois replicadas para os outros clientes.

&#91;embedded content: roadmap · 4 fases e 3 gates\]

Nenhum item da Fase 4 começa antes do Gate 3. O change log entra antes de qualquer diagnóstico real, porque muda a qualidade da interpretação.

### Como trabalhar em paralelo

- **Primeiro artefato do Claude Code:** OpenAPI da `/agency/v1` e schemas Pydantic v1.1 (Etapa 1).
- **Codex começa contra mock** gerado do OpenAPI: cliente, parser de change log e Telegram, sem desligar o Hermes atual.
- **Ponto de encontro:** o teste ponta a ponta da Seção 12, ao fim da Etapa 9, é o Gate 2 e libera a Fase D.
- **Mudança de contrato:** quem precisar alterar um contrato propõe por comentário neste documento antes de mexer no código e incrementa `schema_version`.

## 12. Critérios de aceite e testes

O MVP está pronto quando o slice LK Sneakers roda por 2 semanas seguidas sem intervenção manual no núcleo, com equivalência ao pipeline atual aceita e todos os critérios abaixo atendidos.

### Claude Code

- [ ] Nenhuma tabela com `client_id` sem RLS; teste automatizado falha se houver.
- [ ] Cliente não acessa tabela diretamente e não recebe nada AGENCY\_ONLY, alerta, change log ou diagnóstico pela API (teste de integração).
- [ ] Cada estado de fonte (NOT\_CONTRACTED, ACCESS\_MISSING, PERMISSION\_DENIED, NO\_DATA, STALE, ERROR) é reproduzido em staging e aparece corretamente no Data Health, sem nenhum 0 exibido.
- [ ] Cada regra de propagação da Seção 7 tem teste para cada estado de insumo; MER fica UNKNOWN com Business Truth indisponível.
- [ ] Conversões de mesmo nome em contas diferentes não são somadas (teste com `source_conversion_id`).
- [ ] Recoleta após certificação gera nova linha e SUPERSEDED; UPDATE de valor falha no banco.
- [ ] Reiniciar worker no meio de um job não duplica execução nem snapshot; job com 3 falhas vira DEAD e aparece no system health.
- [ ] Meta inválida no Notion mantém a versão anterior e gera alerta de Data Health.
- [ ] Mudança no Google Ads aparece como AUTO em até 6h e casa com o registro HUMAN pelo ID mesmo após renomear a campanha.
- [ ] Alerta não dispara abaixo do volume mínimo, com fonte não READY ou para canal NOT\_CONTRACTED.
- [ ] Feedback de ruído gera sugestão, nunca mudança automática de regra.
- [ ] Report contract é imutável; narrativa só aparece ao cliente em PUBLISHED.
- [ ] Relatórios de equivalência das Etapas 7 e 10 aceitos.
- [ ] Staging e produção em projetos Supabase separados; chaves legadas fora de uso.

### Codex

- [ ] Fatos e recomendações usam `metric_refs`; diagnóstico com referência inexistente é rejeitado.
- [ ] Número em texto livre que não existe no contexto bloqueia o envio (teste com contexto adulterado).
- [ ] Change log em linguagem natural vira contrato com IDs corretos em pelo menos 9 de 10 casos de teste e sempre pede confirmação; ambiguidade gera pergunta.
- [ ] Botões gravam eventos com `actor` e são idempotentes.
- [ ] Usuário fora da allowlist não aciona nada.
- [ ] Recomendação aprovada sem execução gera lembrete em 7 dias e expira em 14.
- [ ] Diagnóstico considera `related_changes` quando existem e trata NOT\_CONTRACTED como fora de escopo.
- [ ] Narrativa não contém números próprios; aprovar e publicar são ações separadas.
- [ ] Learning candidate só chega ao Obsidian após aprovação.
- [ ] O Hermes atual continua funcionando até a Fase D.

### Teste ponta a ponta (Gate 2)

1. Alterar o tROAS de uma campanha LK no Google Ads e registrar via Telegram pelo nome.
2. Verificar resolução de ID e casamento AUTO + HUMAN no change log.
3. Forçar um alerta em staging (regra com limiar baixo).
4. Receber diagnóstico no Telegram com números renderizados pela API, citando a mudança; aprovar; confirmar execução.
5. Ver o ciclo completo na Agency View e confirmar que nada aparece na Client View.
6. Gerar report contract semanal, narrativa, aprovar e publicar; confirmar que só a versão publicada aparece ao cliente.

## 13. Riscos, decisões e glossário

### Riscos

| Risco | Efeito | Mitigação |
| --- | --- | --- |
| Migração quebrar o que funciona | Clientes perdem dashboard ou relatório | Fases A–F, equivalência antes de trocar, nada removido sem aprovação |
| Fadiga de alertas | Canal Telegram ignorado | Volume mínimo, agrupamento, feedback com sugestão de recalibração |
| Recalibração esconder problema real | Limiar sobe até não detectar nada | Nenhum ajuste automático; aprovação humana por versão |
| Change log manual incompleto | Causa atribuída errada | Change history AUTO desde o MVP; AUTO sem contexto vira pendência |
| Matching por nome | Mudanças erradas casadas | Matching por IDs; nome só fallback |
| Estados colapsados | "Sem acesso" tratado como "sem resultado" | Enums ampliados e testes por estado |
| Clínicas e lead-gen sem Business Truth até a Fase 4 | Decisões só com atribuição | Data Health mostra a ausência; confiança máxima MEDIUM |
| Hermes alucinar números | Decisão sobre dado falso | metric\_refs, validação local e na API |
| Vazamento de hipótese ao cliente | Dano comercial | Cliente só lê projeções; AGENCY\_ONLY padrão; publicação separada da aprovação |
| Jobs duplicados ou silenciosamente falhos | Snapshots duplicados ou dados velhos | run\_key, lock, DEAD, system health |
| Divergência de contrato entre Claude Code e Codex | Integração quebra | OpenAPI único, schema\_version, teste ponta a ponta |
| Overengineering | Meses sem valor visível | Slice LK primeiro; Fase 4 só após Gate 3 |
| Depreciação das chaves do Supabase | Serviços param no fim de 2026 | Etapa 2, com prazo fixo |

### Decisões registradas na v1.1

| Decisão | Motivo |
| --- | --- |
| Frontend permanece no Vercel; API e workers no Railway | Preservar o que funciona; mover sem benefício é risco gratuito |
| Staging como projeto Supabase separado, sem coluna `environment` | Isolamento físico evita vazamento por filtro esquecido |
| "Período fechado" pertence ao report contract, não ao snapshot | O snapshot registra o observado; o fechamento é uma decisão de relatório |
| Política de certificação: default por fonte + overrides opcionais | Evitar configurar uma matriz que hoje ninguém precisa |
| Placeholders obrigatórios só em objetos críticos; texto livre com validação textual | Equilíbrio entre segurança e escrita natural |
| Locks via Postgres (run\_key + advisory lock) | Sem infraestrutura nova |

### Decisões em aberto

- [ ] Onde o Hermes roda a longo prazo: manter o ambiente atual ou migrar para o Railway.
- [ ] Janelas de certificação definitivas por fonte (valores da Seção 5 são iniciais).
- [ ] Regra de reporte da Zipper Galeria traduzida em metric contract.
- [ ] Fonte de Business Truth da SPITI.AUCTION.
- [ ] Completar Client Truth de Enutri, Colab55 e Multiteiner (modelo, timezone, moeda, metas, status).
- [ ] Versão mínima de status de lead para a Clínica Dr. Tárcio Caetano antes do Noro Track.
- [ ] Convenção de ID de criativo (`[CLIENTE][CR-XXX-00000][FUNIL]`) para novos anúncios.

### Glossário

| Termo | Significado |
| --- | --- |
| Client Truth | Quem é o cliente: modelo de negócio, fuso, moeda, escopo, oferta, regras |
| Target Truth | Metas e orçamento aprovados por período |
| Business Truth | O que aconteceu no negócio: receita, pedidos, vendas reais |
| Performance Truth | O que as plataformas de mídia reportam |
| Journey Truth | Comportamento digital (GA4) |
| Knowledge Truth | Metodologia e aprendizados aprovados (Obsidian) |
| source\_state | Estado da fonte: contratada, acessível, saudável |
| value\_status | Estado de um valor específico, com reason\_code |
| Snapshot | Registro imutável de um valor observado num momento |
| PROVISIONAL / CERTIFIED / SUPERSEDED | Antes da janela, depois da janela, substituído por recoleta |
| Regra de propagação | O que uma métrica derivada exige dos insumos e o que faz quando faltam |
| Metric contract | Métricas válidas para um business model, com estados |
| Report contract | Pacote imutável de números de um relatório, gerado pelo núcleo |
| Narrativa | Texto do Hermes sobre um report contract, sem números próprios |
| metric\_refs | Placeholders que apontam para snapshots ou caminhos do contract |
| run\_key | Identidade determinística de uma execução de job |
| Action event | Decisão registrada: aprovado, ignorado, executado, resolvido, expirado |
| Outcome | Métricas observadas após ação executada, sem afirmar causalidade |
| Learning candidate | Aprendizado proposto, pendente de revisão humana |
