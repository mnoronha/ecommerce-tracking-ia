'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import {
  Loader2, RefreshCw, Search, TrendingUp, MousePointerClick,
  AlertTriangle, Settings, ArrowUpRight, Lightbulb,
} from 'lucide-react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { useDatePeriod, periodToQuery } from '@/lib/use-date-range'
import { PeriodPicker } from '@/components/PeriodPicker'
import Link from 'next/link'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ─────────────────────────────────────────────────────────────────────

interface SCOverview {
  summary: { clicks: number; impressions: number; avg_ctr: number | null; avg_position: number | null }
  top_queries: { query: string; clicks: number; impressions: number; ctr: number; position: number }[]
  top_pages: { page: string; clicks: number; impressions: number; ctr: number; position: number }[]
  daily: { date: string; clicks: number; impressions: number; ctr: number; position: number }[]
  period: { start: string; end: string }
}

interface SCOpportunities {
  low_ctr_queries: { query: string; impressions: number; clicks: number; ctr: number; position: number }[]
  upgrade_candidates: { page: string; query: string; position: number; clicks: number; impressions: number; ctr: number }[]
  period: { start: string; end: string }
}

type Tab = 'overview' | 'opportunities'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(n: number) { return n.toLocaleString('pt-BR') }
function posColor(pos: number) {
  if (pos <= 3) return 'text-emerald-400'
  if (pos <= 10) return 'text-amber-400'
  return 'text-slate-500'
}

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`bg-[#0f1117] border border-[#2a2f3e] rounded-xl ${className}`}>{children}</div>
}
function CardHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="px-5 py-4 border-b border-[#2a2f3e]">
      <h2 className="text-sm font-semibold text-white">{title}</h2>
      {subtitle && <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>}
    </div>
  )
}

// ── KPI cards ─────────────────────────────────────────────────────────────────

function KPIs({ summary }: { summary: SCOverview['summary'] }) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {[
        { label: 'Cliques',      value: fmt(summary.clicks),                               color: 'text-indigo-400', icon: MousePointerClick },
        { label: 'Impressões',   value: fmt(summary.impressions),                           color: 'text-slate-300', icon: Search },
        { label: 'CTR médio',    value: summary.avg_ctr !== null ? `${summary.avg_ctr}%` : '—', color: 'text-amber-400', icon: TrendingUp },
        { label: 'Posição média',value: summary.avg_position !== null ? `${summary.avg_position}` : '—', color: 'text-emerald-400', icon: ArrowUpRight },
      ].map(({ label, value, color, icon: Icon }) => (
        <Card key={label} className="p-4">
          <div className="flex items-center gap-2 mb-2">
            <Icon size={13} className={color} />
            <span className="text-xs text-slate-500">{label}</span>
          </div>
          <p className={`text-2xl font-bold ${color}`}>{value}</p>
        </Card>
      ))}
    </div>
  )
}

// ── Overview tab ──────────────────────────────────────────────────────────────

function OverviewTab({ data }: { data: SCOverview }) {
  const { summary, top_queries, top_pages, daily } = data

  return (
    <div className="space-y-6">
      <KPIs summary={summary} />

      {/* Daily chart */}
      {daily.length > 0 && (
        <Card className="p-5">
          <h2 className="text-sm font-semibold text-white mb-4">Cliques e Impressões por dia</h2>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={daily} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="scclicks" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#6366f1" stopOpacity={0.4} />
                  <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="scimpr" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#64748b" stopOpacity={0.2} />
                  <stop offset="95%" stopColor="#64748b" stopOpacity={0} />
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
              <Area type="monotone" dataKey="impressions" stroke="#64748b" fill="url(#scimpr)" strokeWidth={1} dot={false} name="Impressões" />
              <Area type="monotone" dataKey="clicks" stroke="#6366f1" fill="url(#scclicks)" strokeWidth={2} dot={false} name="Cliques" />
            </AreaChart>
          </ResponsiveContainer>
        </Card>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Top queries */}
        {top_queries.length > 0 && (
          <Card className="overflow-hidden">
            <CardHeader title="Top queries" subtitle="Termos que mais trazem cliques" />
            <div className="divide-y divide-[#1a1f2e]">
              {top_queries.slice(0, 15).map((row, i) => (
                <div key={i} className="px-5 py-3 flex items-center gap-3 hover:bg-[#1a1f2e] transition-colors">
                  <span className="text-xs text-slate-600 w-5 text-right">{i + 1}</span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-slate-300 truncate">{row.query}</p>
                    <div className="flex gap-3 mt-0.5">
                      <span className="text-xs text-slate-600">{fmt(row.impressions)} imp.</span>
                      <span className="text-xs text-slate-600">{row.ctr}% CTR</span>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-sm font-medium text-white">{fmt(row.clicks)}</p>
                    <p className={`text-xs ${posColor(row.position)}`}>pos. {row.position}</p>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        )}

        {/* Top pages */}
        {top_pages.length > 0 && (
          <Card className="overflow-hidden">
            <CardHeader title="Top páginas" subtitle="Páginas que mais recebem tráfego orgânico" />
            <div className="divide-y divide-[#1a1f2e]">
              {top_pages.slice(0, 15).map((row, i) => {
                const path = row.page.replace(/^https?:\/\/[^/]+/, '') || '/'
                return (
                  <div key={i} className="px-5 py-3 flex items-center gap-3 hover:bg-[#1a1f2e] transition-colors">
                    <span className="text-xs text-slate-600 w-5 text-right">{i + 1}</span>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-slate-300 truncate">{path}</p>
                      <div className="flex gap-3 mt-0.5">
                        <span className="text-xs text-slate-600">{fmt(row.impressions)} imp.</span>
                        <span className="text-xs text-slate-600">{row.ctr}% CTR</span>
                      </div>
                    </div>
                    <div className="text-right">
                      <p className="text-sm font-medium text-white">{fmt(row.clicks)}</p>
                      <p className={`text-xs ${posColor(row.position)}`}>pos. {row.position}</p>
                    </div>
                  </div>
                )
              })}
            </div>
          </Card>
        )}
      </div>
    </div>
  )
}

// ── Opportunities tab ─────────────────────────────────────────────────────────

function OpportunitiesTab({ data }: { data: SCOpportunities }) {
  return (
    <div className="space-y-6">

      {/* Low CTR queries */}
      <Card className="overflow-hidden">
        <CardHeader
          title="Queries com CTR baixo"
          subtitle="100+ impressões mas menos de 5% de CTR — título/description pode melhorar"
        />
        {data.low_ctr_queries.length === 0 ? (
          <div className="p-8 text-center">
            <p className="text-sm text-slate-500">Nenhuma query identificada com este critério no período.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#2a2f3e]">
                  {['Query', 'Impressões', 'Cliques', 'CTR', 'Posição'].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs text-slate-500 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.low_ctr_queries.map((row, i) => (
                  <tr key={i} className="border-b border-[#1a1f2e] hover:bg-[#1a1f2e] transition-colors">
                    <td className="px-4 py-3 text-slate-300 max-w-xs">
                      <p className="truncate">{row.query}</p>
                    </td>
                    <td className="px-4 py-3 text-slate-400">{fmt(row.impressions)}</td>
                    <td className="px-4 py-3 text-slate-400">{fmt(row.clicks)}</td>
                    <td className="px-4 py-3">
                      <span className="text-xs text-red-400 font-medium">{row.ctr}%</span>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`text-xs font-medium ${posColor(row.position)}`}>{row.position}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Upgrade candidates */}
      <Card className="overflow-hidden">
        <CardHeader
          title="Candidatos a subir para 1ª posição"
          subtitle="Páginas em posição 4-10 — otimização pode trazer para o top 3"
        />
        {data.upgrade_candidates.length === 0 ? (
          <div className="p-8 text-center">
            <p className="text-sm text-slate-500">Nenhuma página identificada em posição 4-10 com tráfego suficiente.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#2a2f3e]">
                  {['Página', 'Query', 'Posição', 'Impressões', 'Cliques', 'CTR'].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs text-slate-500 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.upgrade_candidates.map((row, i) => {
                  const path = row.page.replace(/^https?:\/\/[^/]+/, '') || '/'
                  return (
                    <tr key={i} className="border-b border-[#1a1f2e] hover:bg-[#1a1f2e] transition-colors">
                      <td className="px-4 py-3 text-slate-300 max-w-[200px]">
                        <p className="text-xs truncate">{path}</p>
                      </td>
                      <td className="px-4 py-3 text-slate-400 max-w-[200px]">
                        <p className="text-xs truncate">{row.query}</p>
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-xs font-bold text-amber-400 bg-amber-400/10 px-2 py-0.5 rounded">
                          {row.position}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-slate-400">{fmt(row.impressions)}</td>
                      <td className="px-4 py-3 text-slate-400">{fmt(row.clicks)}</td>
                      <td className="px-4 py-3 text-xs text-slate-500">{row.ctr}%</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SearchConsolePage() {
  const { clientId } = useParams<{ clientId: string }>()
  const { period, from, to, setPreset, setCustom } = useDatePeriod()

  const [activeTab, setActiveTab] = useState<Tab>('overview')
  const [overview, setOverview]   = useState<SCOverview | null>(null)
  const [opps, setOpps]           = useState<SCOpportunities | null>(null)
  const [loading, setLoading]     = useState(true)
  const [tabLoading, setTabLoading] = useState(false)
  const [error, setError]         = useState<string | null>(null)
  const [tabError, setTabError]   = useState<string | null>(null)

  const qs = periodToQuery(period, from, to)

  const loadOverview = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_URL}/search-console/${clientId}/overview?${qs}`)
      if (res.status === 403) { setError('scope_missing'); return }
      if (res.status === 400) { setError('not_configured'); return }
      if (!res.ok) { const b = await res.json().catch(() => ({})); setError(b.detail || `Erro ${res.status}`); return }
      setOverview(await res.json())
    } catch { setError('Falha de rede') }
    finally { setLoading(false) }
  }, [clientId, qs])

  const loadOpps = useCallback(async () => {
    setTabLoading(true)
    setTabError(null)
    // Opportunities benefit from a longer window — use 90 days
    const oppQs = qs.includes('days=') ? qs.replace(/days=\d+/, 'days=90') : `${qs}&days=90`
    try {
      const res = await fetch(`${API_URL}/search-console/${clientId}/opportunities?${oppQs}`)
      if (!res.ok) { const b = await res.json().catch(() => ({})); setTabError(b.detail || `Erro ${res.status}`); return }
      setOpps(await res.json())
    } catch { setTabError('Falha de rede') }
    finally { setTabLoading(false) }
  }, [clientId, qs])

  useEffect(() => { loadOverview() }, [loadOverview])
  useEffect(() => { setOpps(null) }, [qs])

  const handleTabChange = (tab: Tab) => {
    setActiveTab(tab)
    if (tab === 'opportunities' && !opps) loadOpps()
  }

  // ── Error states ───────────────────────────────────────────────────────────

  if (!loading && error === 'scope_missing') {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 text-center px-4">
        <AlertTriangle size={40} className="text-amber-500" />
        <h2 className="text-lg font-semibold text-white">Permissão Search Console necessária</h2>
        <p className="text-sm text-slate-400 max-w-md">
          O token do Google OAuth não tem a permissão <code className="text-amber-400">webmasters.readonly</code>.
          Reconecte o Google OAuth nas configurações deste cliente incluindo a permissão Search Console.
        </p>
        <Link href={`/clients/${clientId}/settings`}
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors">
          <Settings size={14} /> Ir para Settings
        </Link>
      </div>
    )
  }

  if (!loading && error === 'not_configured') {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 text-center px-4">
        <Search size={40} className="text-slate-600" />
        <h2 className="text-lg font-semibold text-white">Search Console não configurado</h2>
        <p className="text-sm text-slate-400 max-w-md">
          Preencha o campo <strong className="text-white">URL do Search Console</strong> nas configurações deste cliente.
          Exemplo: <code className="text-emerald-400">sc-domain:lksneakers.com.br</code>
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
          <h1 className="text-xl font-bold text-white">Google Search Console</h1>
          <p className="text-xs text-slate-500 mt-0.5">{overview.period.start} → {overview.period.end}</p>
        </div>
        <div className="flex items-center gap-3">
          <PeriodPicker period={period} from={from} to={to} onPreset={setPreset} onCustom={setCustom} />
          <button
            onClick={() => { loadOverview(); if (activeTab === 'opportunities') loadOpps() }}
            className="p-2 text-slate-400 hover:text-white border border-[#2a2f3e] rounded-lg hover:border-slate-500 transition-colors"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-[#2a2f3e]">
        {([
          { id: 'overview' as Tab, label: 'Visão Geral', icon: Search },
          { id: 'opportunities' as Tab, label: 'Oportunidades', icon: Lightbulb },
        ]).map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => handleTabChange(id)}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              activeTab === id ? 'border-indigo-500 text-indigo-400' : 'border-transparent text-slate-500 hover:text-slate-300'
            }`}
          >
            <Icon size={13} />
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      {activeTab === 'overview' && <OverviewTab data={overview} />}
      {activeTab === 'opportunities' && (
        tabLoading ? (
          <div className="flex items-center justify-center h-48"><Loader2 size={24} className="animate-spin text-indigo-400" /></div>
        ) : tabError ? (
          <div className="flex flex-col items-center justify-center h-48 gap-3">
            <AlertTriangle size={24} className="text-red-400" />
            <p className="text-sm text-red-400">{tabError}</p>
            <button onClick={loadOpps} className="text-xs text-slate-400 hover:text-white underline">Tentar novamente</button>
          </div>
        ) : opps ? (
          <OpportunitiesTab data={opps} />
        ) : (
          <div className="flex items-center justify-center h-48"><Loader2 size={24} className="animate-spin text-indigo-400" /></div>
        )
      )}

    </div>
  )
}
