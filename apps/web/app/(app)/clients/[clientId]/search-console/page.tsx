'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import {
  Loader2, RefreshCw, Search, TrendingUp, MousePointerClick,
  AlertTriangle, Settings, ArrowUpRight, Lightbulb, Bot,
  Sparkles, ExternalLink, CheckCircle2, XCircle, BarChart2,
} from 'lucide-react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar,
} from 'recharts'
import { useDatePeriod, periodToQuery } from '@/lib/use-date-range'
import { PeriodPicker } from '@/components/PeriodPicker'
import Link from 'next/link'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

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

interface SCSnapshot {
  date: string
  total_impressions: number
  total_clicks: number
  avg_ctr: number | null
  avg_position: number | null
  ai_overview_appearances: number
  ai_overview_clicks: number
  ai_overview_unique_urls: number
  featured_snippet_impressions: number
  impressions_change_vs_7d: number | null
  clicks_change_vs_7d: number | null
}

interface SCSnapshotResponse {
  summary: {
    total_impressions: number; total_clicks: number; avg_ctr: number | null
    ai_overview_total: number; ai_overview_clicks: number; ai_overview_urls: number
  }
  daily: SCSnapshot[]
  period: { start: string; end: string }
}

interface AIOverviewRow {
  url: string; total_impressions: number; total_clicks: number
  queries: { query: string; impressions: number; clicks: number; date: string }[]
}

interface AIOverviewData {
  summary: { total_impressions: number; total_clicks: number; unique_urls: number }
  by_url: AIOverviewRow[]
  daily: { date: string; impressions: number; clicks: number; unique_urls: number }[]
  period: { start: string; end: string }
}

interface OppQuery {
  id: string; query: string; opportunity_type: string; status: string
  avg_impressions_30d: number; avg_position_30d: number; avg_ctr_30d: number
  estimated_potential_clicks: number; related_pages: string[]
  last_seen_at: string
}

type Tab = 'overview' | 'ai-overviews' | 'opportunities' | 'snapshots'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(n: number) { return n.toLocaleString('pt-BR') }
function pct(n: number) { return `${(n * 100).toFixed(1)}%` }
function posColor(pos: number) {
  if (pos <= 3)  return 'text-emerald-400'
  if (pos <= 10) return 'text-amber-400'
  return 'text-slate-500'
}
function oppTypeBadge(t: string) {
  const map: Record<string, { label: string; cls: string }> = {
    high_impression_low_position: { label: 'Alta impressão, pos. ruim', cls: 'bg-orange-500/15 text-orange-300' },
    high_impression_low_ctr:      { label: 'Alta impressão, CTR baixo', cls: 'bg-red-500/15 text-red-300' },
    emerging:                     { label: 'Crescendo (pos. 4-10)',      cls: 'bg-indigo-500/15 text-indigo-300' },
  }
  const m = map[t] || { label: t, cls: 'bg-slate-700 text-slate-300' }
  return <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${m.cls}`}>{m.label}</span>
}

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`bg-[#0f1117] border border-[#2a2f3e] rounded-xl ${className}`}>{children}</div>
}
function CardHeader({ title, subtitle, right }: { title: string; subtitle?: string; right?: React.ReactNode }) {
  return (
    <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between">
      <div>
        <h2 className="text-sm font-semibold text-white">{title}</h2>
        {subtitle && <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>}
      </div>
      {right}
    </div>
  )
}
function Empty({ msg }: { msg: string }) {
  return <div className="p-8 text-center"><p className="text-sm text-slate-500">{msg}</p></div>
}

// ── KPI cards ─────────────────────────────────────────────────────────────────

function KPIs({ summary }: { summary: SCOverview['summary'] }) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {[
        { label: 'Cliques',      value: fmt(summary.clicks),       color: 'text-indigo-400', icon: MousePointerClick },
        { label: 'Impressões',   value: fmt(summary.impressions),   color: 'text-slate-300',  icon: Search },
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

function OverviewTab({ data, snapshots }: { data: SCOverview; snapshots: SCSnapshotResponse | null }) {
  const { summary, top_queries, top_pages, daily } = data

  return (
    <div className="space-y-6">
      <KPIs summary={summary} />

      {/* AI Overview highlight */}
      {snapshots && snapshots.summary.ai_overview_total > 0 && (
        <Card className="p-5 border-purple-500/30 bg-purple-500/5">
          <div className="flex items-center gap-3 mb-3">
            <Bot size={18} className="text-purple-400" />
            <h3 className="text-sm font-semibold text-white">AI Overviews do Google</h3>
            <span className="text-xs text-purple-300 bg-purple-500/15 px-2 py-0.5 rounded-full">
              Dados persistidos
            </span>
          </div>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <p className="text-2xl font-bold text-purple-300">{fmt(snapshots.summary.ai_overview_total)}</p>
              <p className="text-xs text-slate-500 mt-0.5">Aparições</p>
            </div>
            <div>
              <p className="text-2xl font-bold text-purple-300">{fmt(snapshots.summary.ai_overview_clicks)}</p>
              <p className="text-xs text-slate-500 mt-0.5">Cliques</p>
            </div>
            <div>
              <p className="text-2xl font-bold text-purple-300">{snapshots.summary.ai_overview_urls}</p>
              <p className="text-xs text-slate-500 mt-0.5">URLs únicas</p>
            </div>
          </div>
        </Card>
      )}

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

// ── AI Overviews tab ──────────────────────────────────────────────────────────

function AIOverviewsTab({ clientId, qs }: { clientId: string; qs: string }) {
  const [data, setData] = useState<AIOverviewData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const r = await fetch(`${API}/search-console/${clientId}/ai-overviews?${qs}&days=28`)
      if (!r.ok) { setError(`Erro ${r.status}`); return }
      setData(await r.json())
    } catch { setError('Falha de rede') }
    finally { setLoading(false) }
  }, [clientId, qs])

  const runSync = async () => {
    setSyncing(true)
    try {
      await fetch(`${API}/search-console/${clientId}/sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ days: 7 }),
      })
      setTimeout(() => { load(); setSyncing(false) }, 3000)
    } catch { setSyncing(false) }
  }

  useEffect(() => { load() }, [load])

  if (loading) return <div className="flex items-center justify-center h-64"><Loader2 size={24} className="animate-spin text-indigo-400" /></div>
  if (error)   return <div className="flex items-center justify-center h-64 gap-2"><AlertTriangle size={18} className="text-red-400" /><p className="text-sm text-red-400">{error}</p></div>

  const noData = !data || data.summary.total_impressions === 0

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-white flex items-center gap-2">
            <Bot size={16} className="text-purple-400" /> AI Overviews do Google
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Dados persistidos no banco — requer sync para atualizar
          </p>
        </div>
        <button
          onClick={runSync} disabled={syncing}
          className="flex items-center gap-1.5 h-8 px-3 bg-purple-600 hover:bg-purple-500 disabled:opacity-50 rounded text-xs text-white transition-colors"
        >
          {syncing ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          {syncing ? 'Sincronizando...' : 'Sincronizar 7 dias'}
        </button>
      </div>

      {noData ? (
        <Card className="p-10 text-center space-y-3">
          <Bot size={32} className="mx-auto text-slate-600" />
          <p className="text-sm font-medium text-slate-300">Nenhuma aparição em AI Overview registrada</p>
          <p className="text-xs text-slate-500 max-w-sm mx-auto">
            Clique em "Sincronizar 7 dias" para coletar dados do Search Console e persistir no banco.
            Após o primeiro backfill, dados aparecem aqui automaticamente.
          </p>
        </Card>
      ) : (
        <>
          {/* KPIs */}
          <div className="grid grid-cols-3 gap-4">
            {[
              { label: 'Aparições', value: fmt(data!.summary.total_impressions), color: 'text-purple-300' },
              { label: 'Cliques', value: fmt(data!.summary.total_clicks), color: 'text-purple-300' },
              { label: 'URLs únicas', value: data!.summary.unique_urls.toString(), color: 'text-purple-300' },
            ].map(({ label, value, color }) => (
              <Card key={label} className="p-4 border-purple-500/20">
                <p className={`text-2xl font-bold ${color}`}>{value}</p>
                <p className="text-xs text-slate-500 mt-1">{label}</p>
              </Card>
            ))}
          </div>

          {/* Daily chart */}
          {data!.daily.length > 0 && (
            <Card className="p-5">
              <h3 className="text-sm font-semibold text-white mb-4">Aparições em AI Overview por dia</h3>
              <ResponsiveContainer width="100%" height={180}>
                <BarChart data={data!.daily} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e2435" />
                  <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 11 }} tickFormatter={d => d.slice(5)} />
                  <YAxis tick={{ fill: '#64748b', fontSize: 11 }} width={35} />
                  <Tooltip
                    contentStyle={{ background: '#1a1f2e', border: '1px solid #2a2f3e', borderRadius: 8 }}
                    labelStyle={{ color: '#94a3b8' }}
                  />
                  <Bar dataKey="impressions" fill="#a855f7" name="Aparições" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="clicks" fill="#7c3aed" name="Cliques" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </Card>
          )}

          {/* By URL */}
          <Card className="overflow-hidden">
            <CardHeader title="URLs que apareceram em AI Overview" subtitle="Ordenadas por total de aparições" />
            <div className="divide-y divide-[#1a1f2e]">
              {data!.by_url.map((row, i) => {
                const path = row.url.replace(/^https?:\/\/[^/]+/, '') || '/'
                return (
                  <div key={i} className="px-5 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <Sparkles size={11} className="text-purple-400 shrink-0" />
                          <a href={row.url} target="_blank" rel="noreferrer"
                            className="text-sm text-slate-300 truncate hover:text-indigo-400 transition-colors">
                            {path}
                          </a>
                          <ExternalLink size={10} className="text-slate-600 shrink-0" />
                        </div>
                        {row.queries.length > 0 && (
                          <p className="text-xs text-slate-500 mt-1 ml-4 truncate">
                            queries: {row.queries.slice(0, 3).map(q => q.query).join(', ')}
                          </p>
                        )}
                      </div>
                      <div className="text-right shrink-0">
                        <p className="text-sm font-semibold text-purple-300">{fmt(row.total_impressions)}</p>
                        <p className="text-xs text-slate-500">{fmt(row.total_clicks)} cliques</p>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </Card>
        </>
      )}
    </div>
  )
}

// ── Opportunities DB tab ───────────────────────────────────────────────────────

function OpportunitiesDBTab({ clientId }: { clientId: string }) {
  const [items, setItems]   = useState<OppQuery[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<string>('all')
  const [updating, setUpdating] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${API}/search-console/${clientId}/opportunities-db?limit=80`)
      if (!r.ok) return
      const d = await r.json()
      setItems(d.items || [])
    } finally { setLoading(false) }
  }, [clientId])

  useEffect(() => { load() }, [load])

  const updateStatus = async (id: string, status: string) => {
    setUpdating(id)
    try {
      await fetch(`${API}/search-console/${clientId}/opportunities-db/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      })
      setItems(prev => prev.map(i => i.id === id ? { ...i, status } : i))
    } finally { setUpdating(null) }
  }

  const types = ['all', 'high_impression_low_position', 'high_impression_low_ctr', 'emerging']
  const filtered = filter === 'all' ? items : items.filter(i => i.opportunity_type === filter)

  if (loading) return <div className="flex items-center justify-center h-64"><Loader2 size={24} className="animate-spin text-indigo-400" /></div>

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        {types.map(t => (
          <button key={t} onClick={() => setFilter(t)}
            className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
              filter === t ? 'border-indigo-500 bg-indigo-500/15 text-indigo-300' : 'border-[#2a2f3e] text-slate-500 hover:text-slate-300'
            }`}>
            {t === 'all' ? `Todas (${items.length})`
              : t === 'high_impression_low_position' ? 'Pos. ruim'
              : t === 'high_impression_low_ctr' ? 'CTR baixo'
              : 'Crescendo'}
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <Card className="p-10 text-center">
          <Lightbulb size={28} className="mx-auto text-slate-600 mb-3" />
          <p className="text-sm text-slate-400">
            {items.length === 0
              ? 'Nenhuma oportunidade identificada ainda. Execute um sync para analisar o histórico.'
              : 'Nenhuma oportunidade neste filtro.'}
          </p>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <CardHeader
            title={`${filtered.length} oportunidades identificadas`}
            subtitle="Ordenadas por volume de impressões (30d)"
          />
          <div className="divide-y divide-[#1a1f2e]">
            {filtered.map(row => (
              <div key={row.id} className="px-5 py-4 hover:bg-[#1a1f2e]/50 transition-colors">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                      <p className="text-sm font-medium text-white truncate">{row.query}</p>
                      {oppTypeBadge(row.opportunity_type)}
                    </div>
                    <div className="flex gap-4 mt-1 flex-wrap">
                      <span className="text-xs text-slate-500">{fmt(row.avg_impressions_30d)} imp/mês</span>
                      <span className={`text-xs ${posColor(row.avg_position_30d)}`}>pos. {row.avg_position_30d?.toFixed(1)}</span>
                      <span className="text-xs text-slate-500">{pct(row.avg_ctr_30d)} CTR</span>
                      {row.estimated_potential_clicks > 0 && (
                        <span className="text-xs text-emerald-400">+{fmt(row.estimated_potential_clicks)} cliques potenciais</span>
                      )}
                    </div>
                    {row.related_pages?.length > 0 && (
                      <p className="text-xs text-slate-600 mt-1 truncate">
                        {row.related_pages[0].replace(/^https?:\/\/[^/]+/, '') || '/'}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    {row.status === 'identified' && (
                      <>
                        <button
                          onClick={() => updateStatus(row.id, 'in_pauta')}
                          disabled={updating === row.id}
                          className="text-xs px-2 py-1 border border-indigo-500/50 text-indigo-400 rounded hover:bg-indigo-500/10 transition-colors disabled:opacity-50"
                        >
                          Em pauta
                        </button>
                        <button
                          onClick={() => updateStatus(row.id, 'dismissed')}
                          disabled={updating === row.id}
                          className="p-1.5 text-slate-600 hover:text-slate-400 transition-colors"
                        >
                          <XCircle size={14} />
                        </button>
                      </>
                    )}
                    {row.status === 'in_pauta' && (
                      <div className="flex items-center gap-1">
                        <span className="text-xs text-indigo-400 bg-indigo-500/10 px-2 py-0.5 rounded-full">Em pauta</span>
                        <button onClick={() => updateStatus(row.id, 'addressed')} disabled={updating === row.id}
                          className="p-1 text-slate-600 hover:text-emerald-400 transition-colors">
                          <CheckCircle2 size={14} />
                        </button>
                      </div>
                    )}
                    {row.status === 'addressed' && (
                      <span className="text-xs text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded-full">Resolvida</span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}

// ── Opportunities live tab (original) ─────────────────────────────────────────

function OpportunitiesLiveTab({ data }: { data: SCOpportunities }) {
  return (
    <div className="space-y-6">
      <Card className="overflow-hidden">
        <CardHeader title="Queries com CTR baixo" subtitle="100+ impressões mas menos de 5% CTR — título/description pode melhorar" />
        {data.low_ctr_queries.length === 0 ? <Empty msg="Nenhuma query identificada com este critério." /> : (
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
                    <td className="px-4 py-3 text-slate-300 max-w-xs"><p className="truncate">{row.query}</p></td>
                    <td className="px-4 py-3 text-slate-400">{fmt(row.impressions)}</td>
                    <td className="px-4 py-3 text-slate-400">{fmt(row.clicks)}</td>
                    <td className="px-4 py-3"><span className="text-xs text-red-400 font-medium">{row.ctr}%</span></td>
                    <td className="px-4 py-3"><span className={`text-xs font-medium ${posColor(row.position)}`}>{row.position}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card className="overflow-hidden">
        <CardHeader title="Candidatos a 1ª posição" subtitle="Páginas em posição 4-10 — otimização pode trazer para o top 3" />
        {data.upgrade_candidates.length === 0 ? <Empty msg="Nenhuma página em posição 4-10 com tráfego suficiente." /> : (
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
                      <td className="px-4 py-3 text-slate-300 max-w-[200px]"><p className="text-xs truncate">{path}</p></td>
                      <td className="px-4 py-3 text-slate-400 max-w-[200px]"><p className="text-xs truncate">{row.query}</p></td>
                      <td className="px-4 py-3">
                        <span className="text-xs font-bold text-amber-400 bg-amber-400/10 px-2 py-0.5 rounded">{row.position}</span>
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
  const [overview,  setOverview]  = useState<SCOverview | null>(null)
  const [snapshots, setSnapshots] = useState<SCSnapshotResponse | null>(null)
  const [opps,      setOpps]      = useState<SCOpportunities | null>(null)
  const [loading,   setLoading]   = useState(true)
  const [oppsLoading, setOppsLoading] = useState(false)
  const [error,     setError]     = useState<string | null>(null)
  const [oppsError, setOppsError] = useState<string | null>(null)
  const [backfilling, setBackfilling] = useState(false)

  const qs = periodToQuery(period, from, to)

  const loadOverview = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [ovRes, snapRes] = await Promise.all([
        fetch(`${API}/search-console/${clientId}/overview?${qs}`),
        fetch(`${API}/search-console/${clientId}/snapshots?${qs}`),
      ])
      if (ovRes.status === 403) { setError('scope_missing'); return }
      if (ovRes.status === 400) { setError('not_configured'); return }
      if (!ovRes.ok) { const b = await ovRes.json().catch(() => ({})); setError(b.detail || `Erro ${ovRes.status}`); return }
      setOverview(await ovRes.json())
      if (snapRes.ok) {
        const sd = await snapRes.json()
        if (!sd.message) setSnapshots(sd)
      }
    } catch { setError('Falha de rede') }
    finally { setLoading(false) }
  }, [clientId, qs])

  const loadOpps = useCallback(async () => {
    setOppsLoading(true); setOppsError(null)
    const oppQs = qs.includes('days=') ? qs.replace(/days=\d+/, 'days=90') : `${qs}&days=90`
    try {
      const r = await fetch(`${API}/search-console/${clientId}/opportunities?${oppQs}`)
      if (!r.ok) { const b = await r.json().catch(() => ({})); setOppsError(b.detail || `Erro ${r.status}`); return }
      setOpps(await r.json())
    } catch { setOppsError('Falha de rede') }
    finally { setOppsLoading(false) }
  }, [clientId, qs])

  const runBackfill = async () => {
    setBackfilling(true)
    try {
      await fetch(`${API}/search-console/${clientId}/backfill`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ months_back: 16 }),
      })
    } finally {
      setTimeout(() => setBackfilling(false), 2000)
    }
  }

  useEffect(() => { loadOverview() }, [loadOverview])
  useEffect(() => { setOpps(null) }, [qs])

  const handleTabChange = (tab: Tab) => {
    setActiveTab(tab)
    if (tab === 'opportunities' && !opps) loadOpps()
  }

  // ── Error states ────────────────────────────────────────────────────────────

  if (!loading && error === 'scope_missing') return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 text-center px-4">
      <AlertTriangle size={40} className="text-amber-500" />
      <h2 className="text-lg font-semibold text-white">Permissão Search Console necessária</h2>
      <p className="text-sm text-slate-400 max-w-md">
        Reconecte o Google OAuth nas Settings incluindo a permissão{' '}
        <code className="text-amber-400">webmasters.readonly</code>.
      </p>
      <Link href={`/clients/${clientId}/settings`}
        className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg">
        <Settings size={14} /> Ir para Settings
      </Link>
    </div>
  )

  if (!loading && error === 'not_configured') return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 text-center px-4">
      <Search size={40} className="text-slate-600" />
      <h2 className="text-lg font-semibold text-white">Search Console não configurado</h2>
      <p className="text-sm text-slate-400 max-w-md">
        Preencha o campo <strong>URL do Search Console</strong> nas Settings.
        Ex: <code className="text-emerald-400">sc-domain:lksneakers.com.br</code>
      </p>
      <Link href={`/clients/${clientId}/settings`}
        className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg">
        <Settings size={14} /> Ir para Settings
      </Link>
    </div>
  )

  if (!loading && error) return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-3">
      <AlertTriangle size={32} className="text-red-400" />
      <p className="text-sm text-red-400">{error}</p>
      <button onClick={loadOverview} className="text-xs text-slate-400 hover:text-white underline">Tentar novamente</button>
    </div>
  )

  if (loading) return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <Loader2 size={28} className="animate-spin text-indigo-400" />
    </div>
  )

  if (!overview) return null

  const TABS = [
    { id: 'overview' as Tab,      label: 'Visão Geral',    icon: Search },
    { id: 'ai-overviews' as Tab,  label: 'AI Overviews',   icon: Bot },
    { id: 'opportunities' as Tab, label: 'Oportunidades',  icon: Lightbulb },
    { id: 'snapshots' as Tab,     label: 'Oportunidades DB', icon: BarChart2 },
  ]

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-bold text-white">Google Search Console</h1>
          <p className="text-xs text-slate-500 mt-0.5">{overview.period.start} → {overview.period.end} · dados com delay de 2-3 dias</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={runBackfill} disabled={backfilling}
            title="Importa 16 meses de histórico do Search Console para o banco"
            className="h-8 px-3 text-xs border border-[#2a2f3e] text-slate-400 hover:text-white hover:border-slate-500 rounded-lg flex items-center gap-1.5 disabled:opacity-50 transition-colors"
          >
            {backfilling ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
            {backfilling ? 'Backfill...' : 'Backfill 16m'}
          </button>
          <PeriodPicker period={period} from={from} to={to} onPreset={setPreset} onCustom={setCustom} />
          <button onClick={() => { loadOverview(); if (activeTab === 'opportunities') loadOpps() }}
            className="p-2 text-slate-400 hover:text-white border border-[#2a2f3e] rounded-lg hover:border-slate-500 transition-colors">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-[#2a2f3e] overflow-x-auto">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button key={id} onClick={() => handleTabChange(id)}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 whitespace-nowrap transition-colors ${
              activeTab === id ? 'border-indigo-500 text-indigo-400' : 'border-transparent text-slate-500 hover:text-slate-300'
            }`}>
            <Icon size={13} />{label}
          </button>
        ))}
      </div>

      {/* Content */}
      {activeTab === 'overview' && <OverviewTab data={overview} snapshots={snapshots} />}
      {activeTab === 'ai-overviews' && <AIOverviewsTab clientId={clientId} qs={qs} />}
      {activeTab === 'snapshots' && <OpportunitiesDBTab clientId={clientId} />}
      {activeTab === 'opportunities' && (
        oppsLoading ? <div className="flex items-center justify-center h-48"><Loader2 size={24} className="animate-spin text-indigo-400" /></div>
        : oppsError  ? <div className="flex flex-col items-center justify-center h-48 gap-3"><AlertTriangle size={24} className="text-red-400" /><p className="text-sm text-red-400">{oppsError}</p><button onClick={loadOpps} className="text-xs text-slate-400 hover:text-white underline">Tentar novamente</button></div>
        : opps       ? <OpportunitiesLiveTab data={opps} />
        : <div className="flex items-center justify-center h-48"><Loader2 size={24} className="animate-spin text-indigo-400" /></div>
      )}
    </div>
  )
}
