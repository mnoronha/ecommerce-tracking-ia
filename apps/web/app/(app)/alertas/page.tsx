import { redirect } from 'next/navigation'
import Link from 'next/link'
import { AlertCircle, AlertTriangle, Info, CheckCircle, Bell } from 'lucide-react'
import { createSupabaseServerClient } from '@/lib/supabase-server'
import { agencyApiFetch, AgencyApiError } from '@/lib/agency-api-client'

// ── Types ─────────────────────────────────────────────────────────────────────

interface AlertRow {
  id:               string
  client_id?:       string
  alert_type:       string
  severity:         string          // HIGH | MEDIUM | LOW
  status:           string
  title:            string
  message:          string
  occurrence_count: number
  detected_at:      string
  resolved_at?:     string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
    timeZone: 'America/Sao_Paulo',
  })
}

// Agency API severity: HIGH | MEDIUM | LOW
const SEV_BORDER: Record<string, string> = {
  HIGH:   'border-l-red-500',
  MEDIUM: 'border-l-yellow-500',
  LOW:    'border-l-indigo-500',
}

const SEV_BADGE: Record<string, string> = {
  HIGH:   'bg-red-500/20 text-red-400',
  MEDIUM: 'bg-yellow-500/20 text-yellow-400',
  LOW:    'bg-indigo-500/20 text-indigo-400',
}

const SEV_LABEL: Record<string, string> = {
  HIGH:   'Crítico',
  MEDIUM: 'Atenção',
  LOW:    'Info',
}

const SEV_ICON_CLASS: Record<string, string> = {
  HIGH:   'text-red-400',
  MEDIUM: 'text-yellow-400',
  LOW:    'text-indigo-400',
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default async function AlertasAgenciaPage() {
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) redirect('/login')

  let alerts: AlertRow[] = []
  let apiError: string | null = null

  try {
    alerts = await agencyApiFetch<AlertRow[]>('alerts', { status: 'OPEN', limit: '200' })
  } catch (err) {
    apiError = err instanceof AgencyApiError
      ? `Agency API: ${err.message}`
      : 'Não foi possível carregar alertas.'
  }

  const high   = alerts.filter(a => a.severity === 'HIGH').length
  const medium = alerts.filter(a => a.severity === 'MEDIUM').length

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3">
            <Bell size={16} className="text-indigo-400" />
            <h1 className="text-xl font-bold text-white">Alertas da Agência</h1>
            {high > 0 && (
              <span className="bg-red-500 text-white text-xs font-bold px-2 py-0.5 rounded-full">
                {high} crítico{high > 1 ? 's' : ''}
              </span>
            )}
            {medium > 0 && (
              <span className="bg-yellow-500/20 text-yellow-400 text-xs font-medium px-2 py-0.5 rounded-full">
                {medium} atenção
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-0.5">
            Alertas Core abertos em todos os clientes · fonte: Agency API
          </p>
        </div>
        <Link href="/clients" className="text-xs text-slate-500 hover:text-white transition-colors">
          ← Clientes
        </Link>
      </div>

      {apiError && (
        <div className="rounded-xl border border-yellow-500/20 bg-yellow-500/5 p-4 text-sm text-yellow-300">
          {apiError}
        </div>
      )}

      {alerts.length === 0 && !apiError ? (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-12 text-center">
          <CheckCircle size={36} className="text-emerald-500/40 mx-auto mb-3" />
          <p className="text-slate-300 font-medium">Nenhum alerta aberto</p>
          <p className="text-slate-600 text-xs mt-1">Todos os clientes estão saudáveis</p>
        </div>
      ) : (
        <div className="space-y-2">
          {alerts.map(a => {
            const sev  = a.severity
            const Icon = sev === 'HIGH' ? AlertCircle : sev === 'MEDIUM' ? AlertTriangle : Info
            return (
              <div
                key={a.id}
                className={`bg-[#1a1f2e] border border-[#2a2f3e] border-l-4 ${SEV_BORDER[sev] || SEV_BORDER.LOW} rounded-xl px-5 py-4`}
              >
                <div className="flex items-start gap-3">
                  <Icon size={15} className={`mt-0.5 shrink-0 ${SEV_ICON_CLASS[sev] || 'text-slate-400'}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-3 flex-wrap">
                      <div>
                        <p className="text-sm font-semibold text-white">{a.title}</p>
                        <p className="text-xs text-slate-500 mt-0.5">{a.alert_type}</p>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className={`text-xs px-2 py-0.5 rounded font-medium ${SEV_BADGE[sev] || SEV_BADGE.LOW}`}>
                          {SEV_LABEL[sev] || sev}
                        </span>
                        {a.occurrence_count > 1 && (
                          <span className="text-xs text-slate-500">×{a.occurrence_count}</span>
                        )}
                        {a.client_id && (
                          <Link
                            href={`/clients/${a.client_id}/alertas`}
                            className="text-xs text-indigo-400 hover:text-indigo-300 bg-indigo-500/10 px-2 py-0.5 rounded transition-colors"
                          >
                            {a.client_id}
                          </Link>
                        )}
                      </div>
                    </div>
                    <p className="text-sm text-slate-400 mt-2 leading-relaxed">{a.message}</p>
                    <p className="text-xs text-slate-600 mt-2">{fmtDate(a.detected_at)}</p>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
