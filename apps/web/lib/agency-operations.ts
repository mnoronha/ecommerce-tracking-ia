/** Server-side reads of canonical Hermes operational evidence, governed by session RLS. */
import { createSupabaseServerClient } from '@/lib/supabase-server'
import { canonicalClient, ReportError } from '@/lib/agency-os'

export interface OperationalAlert {
  alert: {
    alert_id: string; client_slug: string; type: string; severity: string; state: string
    source?: string; created_at: string; last_seen_at?: string
    details?: Record<string, unknown>
    resolution?: { reason?: string; evidence?: string; resolved_by?: string }
  }
  diagnosis: { diagnosis_id: string; diagnosis: string; confidence: string; facts: unknown[] }
  recommendation: { recommendation_id: string; proposed_action: string; risk: string; rollback: string; reevaluation_window: string }
}

export interface OperationsContract {
  schema_version: 'norolabs-operations-contract-v1'
  client_slug: string; source_run_id: string; generated_at: string; current_period: string
  capabilities: Record<string, boolean>
  truth_gates: Record<string, string>
  target_truth: { state: string; period: string | null; status: string }
  source_status: Record<string, string>
  alerts: OperationalAlert[]
  human_dependencies: Array<{ dependency_id: string; type: string; status: string; blocking_level: string; requested_action: string; reason?: string; source?: string; created_at: string }>
  workflows: {
    creative: { state: string; service_status: string; blockers: string[]; asset_ids: string[] }
    campaign: { state: string; blockers: string[]; create_paused: boolean; activate_after_creation: boolean }
  }
  governance: { human_review_required: boolean; decision_automation: boolean; execution: boolean; performance_alerts_enabled: boolean }
}

export async function getLatestOperations(route: string): Promise<OperationsContract> {
  const client = canonicalClient(route)
  const supabase = await createSupabaseServerClient()
  const { data, error } = await supabase.from('agency_operations_contracts')
    .select('contract').eq('client_slug', client)
    .order('generated_at', { ascending: false }).order('received_at', { ascending: false })
    .limit(1).maybeSingle()
  if (error) throw new ReportError('unavailable', 'Não foi possível consultar o estado operacional.')
  if (!data) throw new ReportError('not_found', 'Estado operacional ainda não recebido do Hermes.')
  const contract = data.contract as OperationsContract
  if (contract.schema_version !== 'norolabs-operations-contract-v1' || contract.client_slug !== client)
    throw new ReportError('unavailable', 'O registro operacional não corresponde a este cliente.')
  return contract
}

export const OPS_LABELS: Record<string, string> = {
  google_ads: 'Google Ads', meta_ads: 'Meta Ads', ga4: 'GA4', business_truth: 'Resultados de negócio',
  OPEN: 'Aberto', ACKNOWLEDGED: 'Em revisão', RESOLVED: 'Resolvido', SILENCED: 'Silenciado',
  PASS: 'Disponível', available: 'Disponível', access_missing: 'Acesso ausente', permission_denied: 'Permissão negada',
  unknown: 'Não confirmado', UNKNOWN: 'Não confirmado', missing: 'Ausente', stale: 'Desatualizado',
  not_contracted: 'Não contratado', NOT_CONTRACTED: 'Não contratado', no_data: 'Sem dados',
  ACTIVE: 'Ativo', BLOCKED: 'Bloqueado', IN_PROGRESS: 'Em andamento', BLOCKING: 'Bloqueia a etapa', NON_BLOCKING: 'Não bloqueia a etapa',
  COLLECTION_FAILED: 'Falha na coleta', SOURCE_STALE: 'Dados desatualizados', PERMISSION_DENIED: 'Permissão negada', ACCESS_MISSING: 'Acesso ausente',
  ROUTING_CONFLICT: 'Conflito de conta', EXECUTION_BLOCKED: 'Execução bloqueada', UNRESOLVED_MAPPING: 'Conversões não reconciliadas', BUSINESS_TRUTH_MISSING: 'Fonte de negócio ausente',
  CREATIVE_SERVICE_NOT_CONFIRMED: 'Confirmar escopo de produção criativa', CREATIVE_NOT_CONTRACTED: 'Produção criativa não contratada',
  APPROVED_BRIEF_MISSING: 'Fornecer briefing aprovado', APPROVED_SEED_ASSET_MISSING: 'Fornecer imagens ou vídeos aprovados', IDENTITY_REGISTRY_MISSING: 'Cadastrar identidade da marca',
  CONCRETE_CREATIVE_RELEASE_REVIEW_REQUIRED: 'Validar uma produção concreta antes de liberá-la',
  CURRENT_TARGET_TRUTH_MISSING: 'Aprovar metas e orçamento do período atual', CONVERSION_SEMANTICS_UNRESOLVED: 'Reconciliar e aprovar conversões oficiais',
  BUSINESS_TRUTH_UNAVAILABLE_FOR_BUSINESS_OPTIMIZATION: 'Integrar vendas ou CRM para otimizar por resultado de negócio',
  CONCRETE_CAMPAIGN_SPEC_AND_HUMAN_APPROVAL_REQUIRED: 'Preparar e revisar uma proposta concreta de campanha',
  INTEGRATION_ACCESS: 'Acesso à integração', BUSINESS_DATA: 'Fonte de vendas ou CRM', CONVERSION_MAPPING: 'Definição de conversões', CREATIVE_ASSET: 'Material criativo', CLAIM_APPROVAL: 'Aprovação de afirmações', CLIENT_CONFIRMATION: 'Confirmação do cliente', SECURITY_ACTION: 'Acesso e segurança',
}

export const operationLabel = (value: string) => OPS_LABELS[value] ?? value
