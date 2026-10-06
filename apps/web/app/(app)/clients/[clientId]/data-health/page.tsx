import Link from 'next/link'
import { redirect } from 'next/navigation'
import { ArrowLeft, CheckCircle2, AlertTriangle, XCircle, Clock, HelpCircle, RefreshCw } from 'lucide-react'
import { createSupabaseServerClient } from '@/lib/supabase-server'
import { agencyApiFetch, AgencyApiError } from '@/lib/agency-api-client'

// ── Types ─────────────────────────────────────────────────────────────────────

interface DataHealthEntry {
  domain:                 string
  source_key?:            string
  source_system:          string
  semantic_domain?:       string
  source_state:           string
  collection_status:      string
  freshness_status:       string
  reconciliation_status:  string
  last_attempt_at?:       string
  last_data_at?:          string
  last_validated_at?:     string
  last_reconciled_at?:    string
  reconciliation_state?:  string
  reason?:                string
  checked_at:             string
}

interface DataHealthOut {
  client_id:  string
  health:     DataHealthEntry[]
  checked_at: string
}

interface PipelineHealthOut {
  client_id:       string
  last_collection: Record<string, string | null>
  certification:   Record<string, string | null>
  last_report_at?: string
  checked_at:      string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const STATUS_ICON: Record<string, React.ElementType> = {
  OK:         CheckCircle2,
  FRESH:      CheckCircle2,
  STALE:      Clock,
  NO_DATA:    HelpCircle,
  NOT_APPLICABLE: XCircle,
  ERROR:      AlertTriangle,
  UNKNOWN:    HelpCircle,
  DEGRADED:   AlertTriangle,
}

const STATUS_CLASS: Record<string, string> = {
  OK:             'text-emerald-400',
  FRESH:          'text-emerald-400',
  STALE:          'text-yellow-400',
  NO_DATA:        'text-slate-500',
  NOT_APPLICABLE: 'text-slate-600',
  ERROR:          'text-red-400',
  UNKNOWN:        'text-slate-500',
  DEGRADED:       'text-yellow-400',
}

function StatusPill({ status }: { status: string }) {
  const Icon  = STATUS_ICON[status] || HelpCircle
  const cls   = STATUS_CLASS[status] || 'text-slate-400'
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${cls}`}>
      <Icon size={11} />
      {status}
    </span>
  )
}

function fmtRelative(iso?: string | null): string {
  if (!iso) return '—'
  const d   = new Date(iso)
  const now = Date.now()
  const ms  = now - d.getTime()
  const min = Math.floor(ms / 60000)
  if (min < 60) return `${min}min atrás`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h}h atrás`
  return `${Math.floor(h / 24)}d atrás`
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default async function DataHealthPage({
  params,
}: {
  params: Promise<{ clientId: string }>
}) {
  const { clientId } = await params

  // Auth
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) redirect('/login')

  // Fetch from Agency API directly (server component)
  let health: DataHealthOut | null = null
  let pipeline: PipelineHealthOut | null = null
  let errorMsg: string | null = null

  try {
    ;[health, pipeline] = await Promise.all([
      agencyApiFetch<DataHealthOut>(`clients/${clientId}/health`),
      agencyApiFetch<PipelineHealthOut>(`clients/${clientId}/pipeline-health`),
    ])
  } catch (err) {
    if (err instanceof AgencyApiError && err.status === 404) {
      errorMsg = 'Nenhum dado de saúde encontrado para este cliente.'
    } else {
      errorMsg = 'Não foi possível carregar os dados de saúde. Tente novamente.'
    }
  }

  const sources = health?.health || []
  const domains = [...new Set(sources.map(s => s.domain))].sort()

  return (
    <div className="p-8 max-w-5xl">
      {/* Header */}
      <div className="mb-8">
        <Link href={`/clients/${clientId}/dashboard`} className="inline-flex items-center gap-1.5 text-slate-500 hover:text-white text-xs mb-4 transition-colors">
          <ArrowLeft size={12} /> Voltar
        </Link>
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-xl font-bold text-white mb-1">Saúde dos Dados</h1>
            <p className="text-xs text-slate-500">
              Fontes canônicas via Agency API · fonte: Core
            </p>
          </div>
          {health?.checked_at && (
            <p className="text-xs text-slate-600 flex items-center gap-1">
              <RefreshCw size={10} />
              verificado {fmtRelative(health.checked_at)}
            </p>
          )}
        </div>
      </div>

      {errorMsg && (
        <div className="rounded-xl border border-yellow-500/20 bg-yellow-500/5 p-5 text-sm text-yellow-300 mb-6">
          {errorMsg}
        </div>
      )}

      {/* Pipeline summary */}
      {pipeline && (
        <section className="mb-8">
          <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Última Coleta por Domínio</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {Object.entries(pipeline.last_collection).map(([domain, at]) => (
              <div key={domain} className="bg-[#1a1f2e] rounded-xl p-4">
                <p className="text-xs text-slate-500 uppercase tracking-wide mb-1">{domain}</p>
                <p className="text-sm font-medium text-white">{fmtRelative(at)}</p>
                {pipeline.certification[domain] && (
                  <p className="text-xs text-emerald-400 mt-1">{pipeline.certification[domain]}</p>
                )}
              </div>
            ))}
          </div>
          {pipeline.last_report_at && (
            <p className="text-xs text-slate-500 mt-3">
              Último snapshot de métricas: {fmtRelative(pipeline.last_report_at)}
            </p>
          )}
        </section>
      )}

      {/* Sources by domain */}
      {domains.length > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Fontes por Domínio</h2>
          {domains.map(domain => {
            const domainSources = sources.filter(s => s.domain === domain)
            return (
              <div key={domain} className="mb-6">
                <h3 className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-2">{domain}</h3>
                <div className="bg-[#1a1f2e] rounded-xl overflow-hidden">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-[#2a2f3e]">
                        <th className="text-left px-4 py-2.5 text-xs text-slate-500 font-medium">Fonte</th>
                        <th className="text-left px-4 py-2.5 text-xs text-slate-500 font-medium">Estado</th>
                        <th className="text-left px-4 py-2.5 text-xs text-slate-500 font-medium">Coleta</th>
                        <th className="text-left px-4 py-2.5 text-xs text-slate-500 font-medium">Frescor</th>
                        <th className="text-left px-4 py-2.5 text-xs text-slate-500 font-medium">Reconciliação</th>
                        <th className="text-left px-4 py-2.5 text-xs text-slate-500 font-medium">Último dado</th>
                      </tr>
                    </thead>
                    <tbody>
                      {domainSources.map(s => (
                        <tr key={s.source_key || s.source_system} className="border-b border-[#2a2f3e] last:border-0">
                          <td className="px-4 py-3">
                            <p className="text-xs font-medium text-slate-300">{s.source_system}</p>
                            {s.semantic_domain && (
                              <p className="text-[10px] text-slate-600">{s.semantic_domain}</p>
                            )}
                          </td>
                          <td className="px-4 py-3">
                            <StatusPill status={s.source_state} />
                            {s.reason && (
                              <p className="text-[10px] text-slate-600 mt-1 max-w-[160px] truncate" title={s.reason}>{s.reason}</p>
                            )}
                          </td>
                          <td className="px-4 py-3"><StatusPill status={s.collection_status} /></td>
                          <td className="px-4 py-3"><StatusPill status={s.freshness_status} /></td>
                          <td className="px-4 py-3"><StatusPill status={s.reconciliation_status} /></td>
                          <td className="px-4 py-3 text-xs text-slate-500">{fmtRelative(s.last_data_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )
          })}
        </section>
      )}

      {!errorMsg && sources.length === 0 && (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-12 text-center">
          <HelpCircle size={32} className="text-slate-600 mx-auto mb-3" />
          <p className="text-slate-400">Nenhuma fonte de dados registrada ainda.</p>
        </div>
      )}
    </div>
  )
}
