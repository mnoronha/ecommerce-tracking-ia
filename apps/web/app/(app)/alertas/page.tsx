import { redirect } from 'next/navigation'
import Link from 'next/link'
import { createSupabaseServerClient } from '@/lib/supabase-server'
import { AlertCircle, AlertTriangle, Info, CheckCircle, Bell } from 'lucide-react'

interface AlertRow {
  id: string
  client_id: string
  severity: string
  title: string
  message: string
  created_at: string
  resolved_at: string | null
  data: Record<string, unknown>
}

interface ClientName {
  id: string
  name: string
  pixel_id: string
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
    timeZone: 'America/Sao_Paulo',
  })
}

const SEV_BORDER: Record<string, string> = {
  critical: 'border-l-red-500',
  warning:  'border-l-yellow-500',
  info:     'border-l-indigo-500',
}

const SEV_BADGE: Record<string, string> = {
  critical: 'bg-red-500/20 text-red-400',
  warning:  'bg-yellow-500/20 text-yellow-400',
  info:     'bg-indigo-500/20 text-indigo-400',
}

const SEV_LABEL: Record<string, string> = {
  critical: 'Crítico',
  warning:  'Atenção',
  info:     'Info',
}

export default async function AlertasAgenciaPage() {
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) redirect('/login')

  // Get agency for this user
  const { data: membership } = await supabase
    .from('agency_members')
    .select('agency:agencies(id, slug, name)')
    .eq('user_id', user.id)
    .limit(1)
    .single()

  const agency = membership?.agency
    ? (Array.isArray(membership.agency) ? membership.agency[0] : membership.agency) as { id: string; slug: string; name: string }
    : null

  // Fetch open alerts for this agency
  let alerts: AlertRow[] = []
  let clients: ClientName[] = []

  if (agency) {
    const [alertRes, clientRes] = await Promise.all([
      supabase
        .from('alerts')
        .select('id, client_id, severity, title, message, created_at, resolved_at, data')
        .eq('agency_id', agency.id)
        .is('resolved_at', null)
        .order('created_at', { ascending: false })
        .limit(200),
      supabase
        .from('clients')
        .select('id, name, pixel_id')
        .eq('agency_id', agency.id),
    ])
    alerts  = (alertRes.data  || []) as AlertRow[]
    clients = (clientRes.data || []) as ClientName[]
  }

  const clientMap = Object.fromEntries(clients.map(c => [c.id, c]))
  const critical  = alerts.filter(a => a.severity === 'critical').length
  const warning   = alerts.filter(a => a.severity === 'warning').length

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold text-white">Alertas da Agência</h1>
            {critical > 0 && (
              <span className="bg-red-500 text-white text-xs font-bold px-2 py-0.5 rounded-full">
                {critical} crítico{critical > 1 ? 's' : ''}
              </span>
            )}
            {warning > 0 && (
              <span className="bg-yellow-500/20 text-yellow-400 text-xs font-medium px-2 py-0.5 rounded-full">
                {warning} atenção
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-0.5">
            {agency?.name || 'Agência'} · alertas abertos em todos os clientes
          </p>
        </div>
        <Link
          href="/clients"
          className="text-xs text-slate-500 hover:text-white transition-colors"
        >
          ← Clientes
        </Link>
      </div>

      {alerts.length === 0 ? (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-12 text-center">
          <CheckCircle size={36} className="text-emerald-500/40 mx-auto mb-3" />
          <p className="text-slate-300 font-medium">Nenhum alerta aberto</p>
          <p className="text-slate-600 text-xs mt-1">Todos os clientes estão saudáveis</p>
        </div>
      ) : (
        <div className="space-y-2">
          {alerts.map(a => {
            const client = clientMap[a.client_id]
            const Icon = a.severity === 'critical' ? AlertCircle
              : a.severity === 'warning' ? AlertTriangle : Info
            return (
              <div
                key={a.id}
                className={`bg-[#1a1f2e] border border-[#2a2f3e] border-l-4 ${SEV_BORDER[a.severity] || SEV_BORDER.info} rounded-xl px-5 py-4`}
              >
                <div className="flex items-start gap-3">
                  <Icon
                    size={15}
                    className={`mt-0.5 shrink-0 ${
                      a.severity === 'critical' ? 'text-red-400'
                      : a.severity === 'warning' ? 'text-yellow-400'
                      : 'text-indigo-400'
                    }`}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-3 flex-wrap">
                      <p className="text-sm font-semibold text-white">{a.title}</p>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className={`text-xs px-2 py-0.5 rounded font-medium ${SEV_BADGE[a.severity] || SEV_BADGE.info}`}>
                          {SEV_LABEL[a.severity] || a.severity}
                        </span>
                        {client && (
                          <Link
                            href={`/clients/${client.pixel_id}/alertas`}
                            className="text-xs text-indigo-400 hover:text-indigo-300 bg-indigo-500/10 px-2 py-0.5 rounded transition-colors"
                          >
                            {client.name}
                          </Link>
                        )}
                      </div>
                    </div>
                    <p className="text-xs text-slate-400 mt-1 leading-relaxed">{a.message}</p>
                    <p className="text-xs text-slate-600 mt-2">{fmtDate(a.created_at)}</p>
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
