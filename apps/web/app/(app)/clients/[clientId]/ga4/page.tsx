'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import {
  Loader2, RefreshCw, BarChart2, Users, MousePointerClick,
  ShoppingCart, AlertTriangle, Settings, Bot, FileText, TrendingUp,
} from 'lucide-react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Legend,
} from 'recharts'
import { useDatePeriod, periodToQuery } from '@/lib/use-date-range'
import { PeriodPicker } from '@/components/PeriodPicker'
import Link from 'next/link'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ─────────────────────────────────────────────────────────────────────

interface GA4Report {
  summary: { sessions: number; users: number; conversions: number; revenue: number }
  by_channel: { channel: string; sessions: number; users: number; conversions: number; revenue: number }[]
  daily_series: { date: string; sessions: number; users: number; conversions: number }[]
  period: { start: string; end: string }
}

interface FunnelReport {
  summary: {
    sessions: number; add_to_cart: number; begin_checkout: number; purchases: number
    atc_rate: number | null; checkout_rate: number | null; purchase_rate: number | null
  }
  by_channel: {
    channel: string; sessions: number; add_to_cart: number; begin_checkout: number; purchases: number
    atc_rate: number | null; checkout_rate: number | null; purchase_rate: number | null
  }[]
  period: { start: string; end: string }
}

interface AIReport {
  summary: { sessions: number; users: number; conversions: number; revenue: number; share_of_total: number | null }
  by_source: { source: string; medium: string; sessions: number; users: number; conversions: number; revenue: number }[]
  ai_domains_monitored: string[]
  period: { start: string; end: string }
}

interface PagesReport {
  pages: {
    path: string; title: string; sessions: number; pageviews: number
    conversions: number; revenue: number; bounce_rate: number; conv_rate: number | null
  }[]
  period: { start: string; end: string }
}

interface AudienceReport {
  cohorts: {
    cohort: string; users: number; sessions: number; conversions: number
    revenue: number; engagement_rate: number; conv_rate: number | null
    avg_ticket: number | null; revenue_per_user: number | null; user_share: number | null
  }[]
  daily_series: { date: string; new: number; returning: number; new_conv: number; returning_conv: number }[]
  period: { start: string; end: string }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(n: number) { return n.toLocaleString('pt-BR') }
function fmtR(n: number) {
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 })
}
function pct(n: number | null) { return n === null ? '—' : `${n.toFixed(1)}%` }

const CHANNEL_COLORS: Record<string, string> = {
  'Organic Search': '#10b981', 'Paid Search': '#6366f1', 'Organic Social': '#f59e0b',
  'Paid Social': '#ec4899', 'Direct': '#64748b', 'Email': '#0ea5e9',
  'Referral': '#8b5cf6', 'Display': '#f97316',
}
function chColor(ch: string) { return CHANNEL_COLORS[ch] || '#94a3b8' }

type Tab = 'overview' | 'funnel' | 'ai' | 'pages' | 'audience'

const TABS: { id: Tab; label: string; icon: React.ElementType }[] = [
  { id: 'overview', label: 'Visão Geral',   icon: BarChart2 },
  { id: 'funnel',   label: 'Funil',          icon: TrendingUp },
  { id: 'ai',       label: 'Tráfego de IA',  icon: Bot },
  { id: 'pages',    label: 'Páginas',         icon: FileText },
  { id: 'audience', label: 'Audiência',       icon: Users },
]

// ── Shared wrappers ───────────────────────────────────────────────────────────

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`bg-[#0f1117] border border-[#2a2f3e] rounded-xl ${className}`}>{children}</div>
}

function CardHeader({ title }: { title: string }) {
  return (
    <div className="px-5 py-4 border-b border-[#2a2f3e]">
      <h2 className="text-sm font-semibold text-white">{title}</h2>
    </div>
  )
}

function SectionLoading() {
  return (
    <div className="flex items-center justify-center h-48">
      <Loader2 size={24} className="animate-spin text-indigo-400" />
    </div>
  )
}

function SectionError({ msg, retry }: { msg: string; retry: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center h-48 gap-3">
      <AlertTriangle size={24} className="text-red-400" />
      <p className="text-sm text-red-400">{msg}</p>
      <button onClick={retry} className="text-xs text-slate-400 hover:text-white underline">Tentar novamente</button>
    </div>
  )
}

// ── Overview tab ──────────────────────────────────────────────────────────────

function OverviewTab({ data }: { data: GA4Report }) {
  const { summary, by_channel, daily_series } = data
  const totalSessions = summary.sessions || 1

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { icon: BarChart2, label: 'Sessões', value: fmt(summary.sessions), color: 'text-indigo-400' },
          { icon: Users, label: 'Usuários', value: fmt(summary.users), color: 'text-emerald-400' },
          { icon: MousePointerClick, label: 'Conversões', value: fmt(summary.conversions), color: 'text-amber-400' },
          { icon: ShoppingCart, label: 'Receita GA4', value: fmtR(summary.revenue), color: 'text-pink-400' },
        ].map(({ icon: Icon, label, value, color }) => (
          <Card key={label} className="p-4">
            <div className="flex items-center gap-2 mb-2">
              <Icon size={14} className={color} />
              <span className="text-xs text-slate-500">{label}</span>
            </div>
            <p className="text-2xl font-bold text-white">{value}</p>
          </Card>
        ))}
      </div>

      {daily_series.length > 0 && (
        <Card className="p-5">
          <h2 className="text-sm font-semibold text-white mb-4">Sessões por dia</h2>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={daily_series} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="ga4sessions" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2435" />
              <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 11 }} tickFormatter={d => d.slice(5)} />
              <YAxis tick={{ fill: '#64748b', fontSize: 11 }} width={40} />
              <Tooltip
                contentStyle={{ background: '#1a1f2e', border: '1px solid #2a2f3e', borderRadius: 8 }}
                labelStyle={{ color: '#94a3b8' }}
                itemStyle={{ color: '#e2e8f0' }}
              />
              <Area type="monotone" dataKey="sessions" stroke="#6366f1" fill="url(#ga4sessions)"
                strokeWidth={2} dot={false} name="Sessões" />
            </AreaChart>
          </ResponsiveContainer>
        </Card>
      )}

      {by_channel.length > 0 && (
        <Card className="overflow-hidden">
          <CardHeader title="Por canal" />
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#2a2f3e]">
                  {['Canal', 'Sessões', '% Sessões', 'Usuários', 'Conversões', 'Receita'].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs text-slate-500 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {by_channel.map((row, i) => (
                  <tr key={i} className="border-b border-[#1a1f2e] hover:bg-[#1a1f2e] transition-colors">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: chColor(row.channel) }} />
                        <span className="text-slate-300 font-medium">{row.channel}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-slate-300">{fmt(row.sessions)}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="w-16 h-1.5 bg-[#2a2f3e] rounded-full overflow-hidden">
                          <div className="h-full rounded-full"
                            style={{ width: `${Math.round((row.sessions / totalSessions) * 100)}%`, backgroundColor: chColor(row.channel) }} />
                        </div>
                        <span className="text-slate-400 text-xs">{Math.round((row.sessions / totalSessions) * 100)}%</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-slate-300">{fmt(row.users)}</td>
                    <td className="px-4 py-3 text-slate-300">{fmt(row.conversions)}</td>
                    <td className="px-4 py-3 text-slate-300">{row.revenue > 0 ? fmtR(row.revenue) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}

// ── Funnel tab ────────────────────────────────────────────────────────────────

function FunnelTab({ data }: { data: FunnelReport }) {
  const { summary, by_channel } = data
  const steps = [
    { key: 'sessions', label: 'Sessões', value: summary.sessions, rate: null },
    { key: 'add_to_cart', label: 'Add-to-cart', value: summary.add_to_cart, rate: summary.atc_rate },
    { key: 'begin_checkout', label: 'Checkout', value: summary.begin_checkout, rate: summary.checkout_rate },
    { key: 'purchases', label: 'Compras', value: summary.purchases, rate: summary.purchase_rate },
  ]
  const max = summary.sessions || 1

  return (
    <div className="space-y-6">
      {/* Summary funnel bars */}
      <Card className="p-5">
        <h2 className="text-sm font-semibold text-white mb-6">Funil geral</h2>
        <div className="space-y-3">
          {steps.map(({ label, value, rate }, i) => {
            const colors = ['bg-indigo-500', 'bg-amber-500', 'bg-orange-500', 'bg-emerald-500']
            const w = Math.round((value / max) * 100)
            return (
              <div key={i} className="flex items-center gap-4">
                <span className="text-xs text-slate-400 w-28 text-right">{label}</span>
                <div className="flex-1 bg-[#1a1f2e] rounded-full h-7 overflow-hidden">
                  <div className={`h-full ${colors[i]} rounded-full flex items-center px-3 transition-all`} style={{ width: `${Math.max(w, 4)}%` }}>
                    <span className="text-white text-xs font-medium">{fmt(value)}</span>
                  </div>
                </div>
                <span className="text-xs text-slate-500 w-16 text-right">
                  {rate !== null ? `${rate}%` : '100%'}
                </span>
              </div>
            )
          })}
        </div>
      </Card>

      {/* By channel */}
      {by_channel.length > 0 && (
        <Card className="overflow-hidden">
          <CardHeader title="Funil por canal" />
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#2a2f3e]">
                  {['Canal', 'Sessões', 'ATC', 'Taxa ATC', 'Checkout', 'Compras', 'Taxa compra'].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs text-slate-500 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {by_channel.map((row, i) => (
                  <tr key={i} className="border-b border-[#1a1f2e] hover:bg-[#1a1f2e] transition-colors">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="w-2 h-2 rounded-full" style={{ backgroundColor: chColor(row.channel) }} />
                        <span className="text-slate-300 font-medium">{row.channel}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-slate-300">{fmt(row.sessions)}</td>
                    <td className="px-4 py-3 text-slate-300">{fmt(row.add_to_cart)}</td>
                    <td className="px-4 py-3">
                      <span className={`text-xs font-medium ${(row.atc_rate ?? 0) >= 5 ? 'text-emerald-400' : (row.atc_rate ?? 0) >= 2 ? 'text-amber-400' : 'text-slate-400'}`}>
                        {pct(row.atc_rate)}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-300">{fmt(row.begin_checkout)}</td>
                    <td className="px-4 py-3 text-slate-300">{fmt(row.purchases)}</td>
                    <td className="px-4 py-3">
                      <span className={`text-xs font-medium ${(row.purchase_rate ?? 0) >= 2 ? 'text-emerald-400' : (row.purchase_rate ?? 0) >= 0.5 ? 'text-amber-400' : 'text-slate-400'}`}>
                        {pct(row.purchase_rate)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}

// ── AI Traffic tab ────────────────────────────────────────────────────────────

function AITab({ data }: { data: AIReport }) {
  const { summary, by_source } = data
  const hasData = summary.sessions > 0

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: 'Sessões via IA', value: fmt(summary.sessions), color: 'text-violet-400' },
          { label: 'Usuários', value: fmt(summary.users), color: 'text-indigo-400' },
          { label: 'Conversões', value: fmt(summary.conversions), color: 'text-amber-400' },
          { label: '% do total', value: summary.share_of_total !== null ? `${summary.share_of_total}%` : '—', color: 'text-emerald-400' },
        ].map(({ label, value, color }) => (
          <Card key={label} className="p-4">
            <p className="text-xs text-slate-500 mb-1">{label}</p>
            <p className={`text-2xl font-bold ${color}`}>{value}</p>
          </Card>
        ))}
      </div>

      {!hasData && (
        <Card className="p-8 text-center">
          <Bot size={32} className="text-slate-600 mx-auto mb-3" />
          <p className="text-sm text-slate-400">Nenhum tráfego detectado de ferramentas de IA no período.</p>
          <p className="text-xs text-slate-600 mt-1">Monitorando: {data.ai_domains_monitored.join(', ')}</p>
        </Card>
      )}

      {by_source.length > 0 && (
        <Card className="overflow-hidden">
          <CardHeader title="Por ferramenta de IA" />
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#2a2f3e]">
                  {['Ferramenta', 'Medium', 'Sessões', 'Usuários', 'Conversões', 'Receita'].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs text-slate-500 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {by_source.map((row, i) => (
                  <tr key={i} className="border-b border-[#1a1f2e] hover:bg-[#1a1f2e] transition-colors">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <Bot size={12} className="text-violet-400" />
                        <span className="text-slate-300 font-medium">{row.source}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-slate-500 text-xs">{row.medium}</td>
                    <td className="px-4 py-3 text-slate-300">{fmt(row.sessions)}</td>
                    <td className="px-4 py-3 text-slate-300">{fmt(row.users)}</td>
                    <td className="px-4 py-3 text-slate-300">{fmt(row.conversions)}</td>
                    <td className="px-4 py-3 text-slate-300">{row.revenue > 0 ? fmtR(row.revenue) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="px-5 py-3 border-t border-[#2a2f3e]">
            <p className="text-xs text-slate-600">Monitorando: {data.ai_domains_monitored.join(' · ')}</p>
          </div>
        </Card>
      )}
    </div>
  )
}

// ── Pages tab ─────────────────────────────────────────────────────────────────

function PagesTab({ data }: { data: PagesReport }) {
  return (
    <div className="space-y-6">
      {data.pages.length === 0 ? (
        <Card className="p-8 text-center">
          <FileText size={32} className="text-slate-600 mx-auto mb-3" />
          <p className="text-sm text-slate-400">Nenhuma página de produto encontrada no período.</p>
          <p className="text-xs text-slate-600 mt-1">Buscando paths com /products/, /produto/ ou /item/</p>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <CardHeader title={`Top ${data.pages.length} páginas de produto`} />
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#2a2f3e]">
                  {['Página', 'Sessões', 'Pageviews', 'Conv.', 'Taxa conv.', 'Receita', 'Bounce'].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs text-slate-500 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.pages.map((row, i) => (
                  <tr key={i} className="border-b border-[#1a1f2e] hover:bg-[#1a1f2e] transition-colors">
                    <td className="px-4 py-3 max-w-xs">
                      <p className="text-slate-300 font-medium text-xs truncate">{row.title || row.path}</p>
                      <p className="text-slate-600 text-xs truncate">{row.path}</p>
                    </td>
                    <td className="px-4 py-3 text-slate-300">{fmt(row.sessions)}</td>
                    <td className="px-4 py-3 text-slate-400">{fmt(row.pageviews)}</td>
                    <td className="px-4 py-3 text-slate-300">{fmt(row.conversions)}</td>
                    <td className="px-4 py-3">
                      <span className={`text-xs font-medium ${(row.conv_rate ?? 0) >= 3 ? 'text-emerald-400' : (row.conv_rate ?? 0) >= 1 ? 'text-amber-400' : 'text-slate-500'}`}>
                        {pct(row.conv_rate)}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-300">{row.revenue > 0 ? fmtR(row.revenue) : '—'}</td>
                    <td className="px-4 py-3">
                      <span className={`text-xs ${row.bounce_rate > 60 ? 'text-red-400' : row.bounce_rate > 40 ? 'text-amber-400' : 'text-emerald-400'}`}>
                        {row.bounce_rate.toFixed(0)}%
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}

// ── Audience tab ──────────────────────────────────────────────────────────────

function AudienceTab({ data }: { data: AudienceReport }) {
  const { cohorts, daily_series } = data

  return (
    <div className="space-y-6">
      {/* Cohort cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {cohorts.map((c) => {
          const isNew = c.cohort === 'new'
          const color = isNew ? 'text-indigo-400' : 'text-emerald-400'
          const border = isNew ? 'border-indigo-500/20' : 'border-emerald-500/20'
          return (
            <Card key={c.cohort} className={`p-5 border ${border}`}>
              <div className="flex items-center gap-2 mb-4">
                <Users size={14} className={color} />
                <h3 className={`text-sm font-semibold ${color}`}>
                  {isNew ? 'Novos visitantes' : 'Recorrentes'}
                </h3>
                {c.user_share !== null && (
                  <span className="ml-auto text-xs text-slate-500">{c.user_share}% dos usuários</span>
                )}
              </div>
              <div className="grid grid-cols-2 gap-3">
                {[
                  { label: 'Usuários',      value: fmt(c.users) },
                  { label: 'Conversões',    value: fmt(c.conversions) },
                  { label: 'Taxa conv.',    value: pct(c.conv_rate) },
                  { label: 'Ticket médio',  value: c.avg_ticket !== null ? fmtR(c.avg_ticket) : '—' },
                  { label: 'Receita/user',  value: c.revenue_per_user !== null ? fmtR(c.revenue_per_user) : '—' },
                  { label: 'Engajamento',   value: `${c.engagement_rate}%` },
                ].map(({ label, value }) => (
                  <div key={label}>
                    <p className="text-xs text-slate-500">{label}</p>
                    <p className="text-sm font-semibold text-white">{value}</p>
                  </div>
                ))}
              </div>
            </Card>
          )
        })}
      </div>

      {/* Daily series */}
      {daily_series.length > 0 && (
        <Card className="p-5">
          <h2 className="text-sm font-semibold text-white mb-4">Novos vs Recorrentes por dia</h2>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={daily_series} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2435" />
              <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 11 }} tickFormatter={d => d.slice(5)} />
              <YAxis tick={{ fill: '#64748b', fontSize: 11 }} width={40} />
              <Tooltip
                contentStyle={{ background: '#1a1f2e', border: '1px solid #2a2f3e', borderRadius: 8 }}
                labelStyle={{ color: '#94a3b8' }}
                itemStyle={{ color: '#e2e8f0' }}
              />
              <Legend wrapperStyle={{ fontSize: 12, color: '#94a3b8' }} />
              <Bar dataKey="new" name="Novos" fill="#6366f1" stackId="a" />
              <Bar dataKey="returning" name="Recorrentes" fill="#10b981" stackId="a" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function GA4Page() {
  const { clientId } = useParams<{ clientId: string }>()
  const { period, from, to, setPreset, setCustom } = useDatePeriod()

  const [activeTab, setActiveTab] = useState<Tab>('overview')

  const [overview,  setOverview]  = useState<GA4Report | null>(null)
  const [funnel,    setFunnel]    = useState<FunnelReport | null>(null)
  const [aiData,    setAiData]    = useState<AIReport | null>(null)
  const [pages,     setPages]     = useState<PagesReport | null>(null)
  const [audience,  setAudience]  = useState<AudienceReport | null>(null)

  const [loading,   setLoading]   = useState(true)
  const [tabLoading, setTabLoading] = useState(false)
  const [error,     setError]     = useState<string | null>(null)
  const [tabError,  setTabError]  = useState<string | null>(null)

  const qs = periodToQuery(period, from, to)

  const loadOverview = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_URL}/ga4/${clientId}/report?${qs}`)
      if (res.status === 403) { setError('disabled'); return }
      if (res.status === 400) { setError('no_property'); return }
      if (!res.ok) { const b = await res.json().catch(() => ({})); setError(b.detail || `Erro ${res.status}`); return }
      setOverview(await res.json())
    } catch { setError('Falha de rede') }
    finally { setLoading(false) }
  }, [clientId, qs])

  const loadTab = useCallback(async (tab: Tab) => {
    if (tab === 'overview') return
    const endpoints: Record<string, string> = {
      funnel:   `/ga4/${clientId}/funnel?${qs}`,
      ai:       `/ga4/${clientId}/ai-traffic?${qs}`,
      pages:    `/ga4/${clientId}/top-pages?${qs}`,
      audience: `/ga4/${clientId}/audience?${qs}`,
    }
    const setters: Record<string, (d: unknown) => void> = {
      funnel:   (d) => setFunnel(d as FunnelReport),
      ai:       (d) => setAiData(d as AIReport),
      pages:    (d) => setPages(d as PagesReport),
      audience: (d) => setAudience(d as AudienceReport),
    }
    setTabLoading(true)
    setTabError(null)
    try {
      const res = await fetch(`${API_URL}${endpoints[tab]}`)
      if (!res.ok) { const b = await res.json().catch(() => ({})); setTabError(b.detail || `Erro ${res.status}`); return }
      setters[tab](await res.json())
    } catch { setTabError('Falha de rede') }
    finally { setTabLoading(false) }
  }, [clientId, qs])

  useEffect(() => { loadOverview() }, [loadOverview])

  useEffect(() => {
    // Reset derived tabs when period changes
    setFunnel(null); setAiData(null); setPages(null); setAudience(null)
  }, [qs])

  const handleTabChange = (tab: Tab) => {
    setActiveTab(tab)
    if (tab !== 'overview') {
      const current = { funnel, ai: aiData, pages, audience }[tab]
      if (!current) loadTab(tab)
    }
  }

  // ── Error states ───────────────────────────────────────────────────────────

  if (!loading && error === 'disabled') {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 text-center px-4">
        <BarChart2 size={40} className="text-slate-600" />
        <h2 className="text-lg font-semibold text-white">Relatórios GA4 desativados</h2>
        <p className="text-sm text-slate-400 max-w-sm">
          Ative a opção "Relatórios GA4 no dashboard" nas configurações deste cliente e preencha o Property ID.
        </p>
        <Link href={`/clients/${clientId}/settings`}
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors">
          <Settings size={14} /> Ir para Settings
        </Link>
      </div>
    )
  }

  if (!loading && error === 'no_property') {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 text-center px-4">
        <AlertTriangle size={40} className="text-amber-500" />
        <h2 className="text-lg font-semibold text-white">Property ID não configurado</h2>
        <p className="text-sm text-slate-400 max-w-sm">
          Preencha o campo "Property ID" nas settings com o número da propriedade GA4.
        </p>
        <Link href={`/clients/${clientId}/settings`}
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors">
          <Settings size={14} /> Ir para Settings
        </Link>
      </div>
    )
  }

  if (!loading && error) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-3">
        <AlertTriangle size={32} className="text-red-400" />
        <p className="text-sm text-red-400">{error}</p>
        <button onClick={loadOverview} className="text-xs text-slate-400 hover:text-white underline">Tentar novamente</button>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <Loader2 size={28} className="animate-spin text-indigo-400" />
      </div>
    )
  }

  if (!overview) return null

  return (
    <div className="space-y-6 p-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Google Analytics 4</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            {overview.period.start} → {overview.period.end}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <PeriodPicker period={period} from={from} to={to} onPreset={setPreset} onCustom={setCustom} />
          <button
            onClick={() => { loadOverview(); if (activeTab !== 'overview') loadTab(activeTab) }}
            className="p-2 text-slate-400 hover:text-white border border-[#2a2f3e] rounded-lg hover:border-slate-500 transition-colors"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-[#2a2f3e] overflow-x-auto">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => handleTabChange(id)}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
              activeTab === id
                ? 'border-indigo-500 text-indigo-400'
                : 'border-transparent text-slate-500 hover:text-slate-300'
            }`}
          >
            <Icon size={13} />
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'overview' && <OverviewTab data={overview} />}

      {activeTab !== 'overview' && (
        tabLoading ? <SectionLoading /> :
        tabError   ? <SectionError msg={tabError} retry={() => loadTab(activeTab)} /> :
        activeTab === 'funnel'   && funnel   ? <FunnelTab   data={funnel} /> :
        activeTab === 'ai'       && aiData   ? <AITab       data={aiData} /> :
        activeTab === 'pages'    && pages    ? <PagesTab    data={pages} /> :
        activeTab === 'audience' && audience ? <AudienceTab data={audience} /> :
        <SectionLoading />
      )}

    </div>
  )
}
