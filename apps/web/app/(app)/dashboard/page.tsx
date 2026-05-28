import { redirect } from 'next/navigation'
import Link from 'next/link'
import { createSupabaseServerClient } from '@/lib/supabase-server'
import {
  TrendingUp, ShoppingBag, DollarSign, Bell,
  AlertTriangle, CheckCircle, AlertCircle, BarChart2, Settings,
} from 'lucide-react'

interface ClientRow {
  client_id: string
  client_name: string
  pixel_id: string
  is_active: boolean
  revenue: number
  orders_count: number
  spend: number
  roas: number
  cpa: number
  roas_goal: number
  cpa_target: number
  revenue_goal: number
  tracking_last_at: string | null
  alert_critical: number
  alert_warning: number
  health_score: number
}

interface Alert {
  id: string
  client_id: string
  severity: 'critical' | 'warning' | 'info'
  message: string
  created_at: string
  clients: { name: string; pixel_id: string } | null
}

async function getAgencyId(userId: string): Promise<string | null> {
  const supabase = await createSupabaseServerClient()
  const { data } = await supabase
    .from('agency_members')
    .select('agency_id')
    .eq('user_id', userId)
    .limit(1)
    .single()
  return data?.agency_id ?? null
}

async function getDashboardData(agencyId: string): Promise<ClientRow[]> {
  const supabase = await createSupabaseServerClient()
  const { data, error } = await supabase.rpc('get_agency_dashboard', {
    p_agency_id: agencyId,
    p_days: 30,
  })
  if (error) {
    console.error('[getDashboardData]', error)
    return []
  }
  return (data ?? []) as ClientRow[]
}

async function getRecentAlerts(agencyId: string): Promise<Alert[]> {
  const supabase = await createSupabaseServerClient()
  const { data } = await supabase
    .from('alerts')
    .select('id, client_id, severity, message, created_at, clients!inner(name, pixel_id, agency_id)')
    .eq('clients.agency_id', agencyId)
    .is('resolved_at', null)
    .order('created_at', { ascending: false })
    .limit(8)
  return ((data ?? []) as unknown as Alert[])
}

function fmt(n: number, style: 'currency' | 'decimal' = 'decimal', decimals = 0): string {
  if (style === 'currency') {
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(n)
  }
  return new Intl.NumberFormat('pt-BR', { maximumFractionDigits: decimals }).format(n)
}

function HealthDot({ score }: { score: number }) {
  const cls = score >= 80 ? 'bg-emerald-400' : score >= 50 ? 'bg-yellow-400' : 'bg-red-400'
  return <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${cls}`} />
}

function HealthBar({ score }: { score: number }) {
  const color = score >= 80 ? 'bg-emerald-500' : score >= 50 ? 'bg-yellow-500' : 'bg-red-500'
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 bg-[#2a2f3e] rounded-full h-1.5 overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${score}%` }} />
      </div>
      <span className="text-xs text-slate-400 tabular-nums w-8">{score}</span>
    </div>
  )
}

function TrackingStatus({ ts }: { ts: string | null }) {
  if (!ts) return <span className="text-xs text-slate-600">—</span>
  const diffH = (Date.now() - new Date(ts).getTime()) / 3_600_000
  if (diffH <= 2)  return <span className="text-xs text-emerald-400">Ao vivo</span>
  if (diffH <= 24) return <span className="text-xs text-emerald-300">&lt; 24h</span>
  if (diffH <= 48) return <span className="text-xs text-yellow-400">&lt; 48h</span>
  return <span className="text-xs text-red-400">Parado</span>
}

export default async function AgencyDashboardPage() {
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) redirect('/login')

  const agencyId = await getAgencyId(user.id)
  if (!agencyId) redirect('/clients')

  const [rows, alerts] = await Promise.all([
    getDashboardData(agencyId),
    getRecentAlerts(agencyId),
  ])

  const totalRevenue  = rows.reduce((s, r) => s + Number(r.revenue), 0)
  const totalSpend    = rows.reduce((s, r) => s + Number(r.spend), 0)
  const weightedRoas  = totalSpend > 0 ? totalRevenue / totalSpend : 0
  const activeAlerts  = alerts.length
  const criticalCount = alerts.filter(a => a.severity === 'critical').length

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-bold text-white">Visão Geral</h1>
        <p className="text-sm text-slate-500 mt-0.5">Últimos 30 dias · {rows.length} cliente{rows.length !== 1 ? 's' : ''}</p>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard
          label="Receita total"
          value={fmt(totalRevenue, 'currency')}
          icon={<DollarSign size={16} className="text-emerald-400" />}
          colorClass="bg-emerald-500/10 border-emerald-500/20"
        />
        <KpiCard
          label="Investimento total"
          value={fmt(totalSpend, 'currency')}
          icon={<TrendingUp size={16} className="text-indigo-400" />}
          colorClass="bg-indigo-500/10 border-indigo-500/20"
        />
        <KpiCard
          label="ROAS médio"
          value={`${weightedRoas.toFixed(2)}x`}
          icon={<BarChart2 size={16} className="text-blue-400" />}
          colorClass="bg-blue-500/10 border-blue-500/20"
        />
        <KpiCard
          label="Alertas ativos"
          value={String(activeAlerts)}
          badge={criticalCount > 0 ? `${criticalCount} crítico${criticalCount > 1 ? 's' : ''}` : undefined}
          icon={<Bell size={16} className={criticalCount > 0 ? 'text-red-400' : 'text-slate-400'} />}
          colorClass={criticalCount > 0 ? 'bg-red-500/10 border-red-500/20' : 'bg-slate-500/10 border-slate-500/20'}
        />
      </div>

      {/* Client table */}
      <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between">
          <h2 className="text-sm font-semibold text-white">Clientes</h2>
          <Link href="/clients/new" className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors">
            + Novo
          </Link>
        </div>
        {rows.length === 0 ? (
          <div className="py-16 text-center text-slate-500 text-sm">
            Nenhum cliente cadastrado.{' '}
            <Link href="/clients/new" className="text-indigo-400 hover:underline">Adicionar →</Link>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-[#2a2f3e]">
                  <th className="text-left px-5 py-3 font-medium">Cliente</th>
                  <th className="text-right px-4 py-3 font-medium">Receita</th>
                  <th className="text-right px-4 py-3 font-medium">Invest.</th>
                  <th className="text-right px-4 py-3 font-medium">ROAS</th>
                  <th className="text-right px-4 py-3 font-medium">CPA</th>
                  <th className="text-left px-4 py-3 font-medium">Tracking</th>
                  <th className="text-left px-4 py-3 font-medium">Score</th>
                  <th className="text-center px-4 py-3 font-medium">Alertas</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody>
                {rows.map(row => (
                  <ClientTableRow key={row.client_id} row={row} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Recent alerts */}
      {alerts.length > 0 && (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between">
            <h2 className="text-sm font-semibold text-white">Alertas recentes</h2>
            <Link href="/alertas" className="text-xs text-slate-500 hover:text-white transition-colors">
              Ver todos →
            </Link>
          </div>
          <div className="divide-y divide-[#2a2f3e]">
            {alerts.map(a => <AlertRow key={a.id} alert={a} />)}
          </div>
        </div>
      )}
    </div>
  )
}

function KpiCard({
  label, value, badge, icon, colorClass,
}: {
  label: string
  value: string
  badge?: string
  icon: React.ReactNode
  colorClass: string
}) {
  return (
    <div className={`rounded-xl border p-4 ${colorClass}`}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs text-slate-400">{label}</span>
        {icon}
      </div>
      <div className="flex items-end gap-2">
        <span className="text-xl font-bold text-white">{value}</span>
        {badge && <span className="text-xs text-red-400 font-medium pb-0.5">{badge}</span>}
      </div>
    </div>
  )
}

function ClientTableRow({ row }: { row: ClientRow }) {
  const roasVsGoal  = row.roas_goal > 0 ? row.roas / row.roas_goal : null
  const roasColor   = roasVsGoal == null ? 'text-slate-300' : roasVsGoal >= 1 ? 'text-emerald-400' : roasVsGoal >= 0.8 ? 'text-yellow-400' : 'text-red-400'
  const cpaVsTarget = row.cpa_target > 0 ? row.cpa / row.cpa_target : null
  const cpaColor    = cpaVsTarget == null ? 'text-slate-300' : cpaVsTarget <= 1 ? 'text-emerald-400' : cpaVsTarget <= 1.2 ? 'text-yellow-400' : 'text-red-400'
  const totalAlerts = row.alert_critical + row.alert_warning

  return (
    <tr className="hover:bg-white/[0.02] transition-colors border-b border-[#2a2f3e] last:border-0">
      <td className="px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <HealthDot score={row.health_score} />
          <div>
            <Link
              href={`/clients/${row.pixel_id}/dashboard`}
              className="text-sm font-medium text-white hover:text-indigo-300 transition-colors"
            >
              {row.client_name}
            </Link>
            {!row.is_active && <span className="ml-2 text-xs text-slate-600">inativo</span>}
          </div>
        </div>
      </td>

      <td className="px-4 py-3.5 text-right tabular-nums text-sm text-slate-200">
        {Number(row.revenue) > 0 ? fmt(Number(row.revenue), 'currency') : <span className="text-slate-600">—</span>}
      </td>

      <td className="px-4 py-3.5 text-right tabular-nums text-sm text-slate-400">
        {Number(row.spend) > 0 ? fmt(Number(row.spend), 'currency') : <span className="text-slate-600">—</span>}
      </td>

      <td className={`px-4 py-3.5 text-right tabular-nums text-sm font-medium ${roasColor}`}>
        {Number(row.roas) > 0 ? `${Number(row.roas).toFixed(2)}x` : <span className="text-slate-600">—</span>}
        {row.roas_goal > 0 && (
          <span className="block text-xs text-slate-600 font-normal">meta {Number(row.roas_goal).toFixed(1)}x</span>
        )}
      </td>

      <td className={`px-4 py-3.5 text-right tabular-nums text-sm font-medium ${cpaColor}`}>
        {Number(row.cpa) > 0 ? fmt(Number(row.cpa), 'currency') : <span className="text-slate-600">—</span>}
        {row.cpa_target > 0 && (
          <span className="block text-xs text-slate-600 font-normal">alvo {fmt(Number(row.cpa_target), 'currency')}</span>
        )}
      </td>

      <td className="px-4 py-3.5">
        <TrackingStatus ts={row.tracking_last_at} />
      </td>

      <td className="px-4 py-3.5">
        <HealthBar score={row.health_score} />
      </td>

      <td className="px-4 py-3.5 text-center">
        {totalAlerts > 0 ? (
          <Link
            href={`/clients/${row.pixel_id}/alertas`}
            className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full ${
              row.alert_critical > 0
                ? 'bg-red-500/15 text-red-400 hover:bg-red-500/25'
                : 'bg-yellow-500/15 text-yellow-400 hover:bg-yellow-500/25'
            }`}
          >
            {row.alert_critical > 0 ? <AlertTriangle size={10} /> : <AlertCircle size={10} />}
            {totalAlerts}
          </Link>
        ) : (
          <CheckCircle size={14} className="text-emerald-500/40 mx-auto" />
        )}
      </td>

      <td className="px-4 py-3.5">
        <div className="flex items-center gap-2 justify-end">
          <Link
            href={`/clients/${row.pixel_id}/dashboard`}
            className="text-slate-500 hover:text-white transition-colors"
            title="Dashboard"
          >
            <BarChart2 size={14} />
          </Link>
          <Link
            href={`/clients/${row.pixel_id}/settings`}
            className="text-slate-500 hover:text-white transition-colors"
            title="Configurações"
          >
            <Settings size={14} />
          </Link>
        </div>
      </td>
    </tr>
  )
}

function AlertRow({ alert }: { alert: Alert }) {
  const ago = (() => {
    const diffH = (Date.now() - new Date(alert.created_at).getTime()) / 3_600_000
    if (diffH < 1)  return `${Math.round(diffH * 60)}min atrás`
    if (diffH < 24) return `${Math.floor(diffH)}h atrás`
    return `${Math.floor(diffH / 24)}d atrás`
  })()

  const icon =
    alert.severity === 'critical' ? <AlertTriangle size={13} className="text-red-400 shrink-0 mt-0.5" />
    : alert.severity === 'warning' ? <AlertCircle   size={13} className="text-yellow-400 shrink-0 mt-0.5" />
    : <Bell size={13} className="text-blue-400 shrink-0 mt-0.5" />

  return (
    <div className="flex items-start gap-3 px-5 py-3 hover:bg-white/[0.02] transition-colors">
      {icon}
      <div className="flex-1 min-w-0">
        <p className="text-sm text-slate-300 truncate">{alert.message}</p>
        <p className="text-xs text-slate-600 mt-0.5">
          {alert.clients && (
            <Link href={`/clients/${alert.clients.pixel_id}/alertas`} className="hover:text-slate-400 transition-colors">
              {alert.clients.name}
            </Link>
          )}
          {alert.clients && ' · '}{ago}
        </p>
      </div>
    </div>
  )
}
