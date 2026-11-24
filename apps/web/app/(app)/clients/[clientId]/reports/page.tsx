'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import {
  ArrowLeft, Sparkles, Loader2, BarChart2, Lightbulb,
  AlertTriangle, RefreshCw, Download, FileText, Settings,
} from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Core Narratives (Agency API) ──────────────────────────────────────────────

interface ActionOutcome {
  status:       string
  status_label: string
  status_css:   string
  delta_label?: string
  confidence?:  string
}

interface ExecutedAction {
  id:          string
  executed_at?: string
  platform?:   string
  action_type?: string
  description: string
  executed_by?: string
  outcome?:    ActionOutcome | null
}

interface OpenRecommendation {
  id:               string
  title?:           string
  action?:          string
  rationale?:       string
  priority?:        string
  expected_impact?: string
}

interface CoreNarrative {
  id:                   string
  report_contract_id:   string
  status:               string
  visibility_scope:     string
  approved_by?:         string
  published_at?:        string
  created_at:           string
  client_id?:           string
  report_type?:         string
  period_start?:        string
  period_end?:          string
  blocks:               Array<{ section: string; statement: string }>
  executed_actions:     ExecutedAction[]
  open_recommendations: OpenRecommendation[]
}

const NARRATIVE_STATUS_BADGE: Record<string, string> = {
  DRAFT:            'bg-slate-500/15 text-slate-400',
  READY_FOR_REVIEW: 'bg-yellow-500/15 text-yellow-400',
  APPROVED:         'bg-emerald-500/15 text-emerald-400',
  PUBLISHED:        'bg-indigo-500/15 text-indigo-400',
  SUPERSEDED:       'bg-slate-600/15 text-slate-600',
}

const NEXT_STATUS: Record<string, { target: string; label: string; cls: string }> = {
  DRAFT:            { target: 'READY_FOR_REVIEW', label: 'Marcar pronto',    cls: 'bg-yellow-600/20 hover:bg-yellow-600/30 text-yellow-300 border-yellow-600/40' },
  READY_FOR_REVIEW: { target: 'APPROVED',          label: 'Aprovar',         cls: 'bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border-emerald-600/40' },
  APPROVED:         { target: 'PUBLISHED',          label: 'Publicar + enviar email', cls: 'bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border-indigo-600/40' },
}

function CoreNarrativesSection({ clientId }: { clientId: string }) {
  const [items,      setItems]      = useState<CoreNarrative[]>([])
  const [loading,    setLoading]    = useState(true)
  const [open,       setOpen]       = useState(false)
  const [expanded,   setExpanded]   = useState<string | null>(null)
  const [transitioning, setTransitioning] = useState<string | null>(null)
  const [transError, setTransError] = useState<Record<string, string>>({})
  const [transOk,    setTransOk]    = useState<Record<string, string>>({})
  // Per-narrative recipient override (empty = use full client list)
  const [recipientOverride, setRecipientOverride] = useState<Record<string, string>>({})

  const reload = () => {
    setLoading(true)
    fetch(`/api/v1/clients/${clientId}/reports`)
      .then(r => r.ok ? r.json() : [])
      .then(setItems)
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => { reload() }, [clientId]) // eslint-disable-line

  async function transition(n: CoreNarrative, target: string) {
    setTransitioning(n.id)
    setTransError(prev => ({ ...prev, [n.id]: '' }))
    setTransOk(prev => ({ ...prev, [n.id]: '' }))
    const override = recipientOverride[n.id]?.trim() || undefined
    try {
      const res = await fetch(`/api/v1/report-narratives/${n.id}/transitions`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ target_status: target, ...(override ? { recipient_override: override } : {}) }),
      })
      const data = await res.json().catch(() => null) as { status?: string; detail?: string } | null
      if (!res.ok) {
        setTransError(prev => ({ ...prev, [n.id]: data?.detail || `Erro ${res.status}` }))
      } else {
        setTransOk(prev => ({ ...prev, [n.id]: `→ ${data?.status || target}` }))
        reload()
      }
    } catch {
      setTransError(prev => ({ ...prev, [n.id]: 'Falha de comunicação' }))
    } finally {
      setTransitioning(null)
    }
  }

  if (loading) return null
  if (items.length === 0) return null

  const pendingCount = items.filter(n => ['DRAFT','READY_FOR_REVIEW','APPROVED'].includes(n.status)).length

  return (
    <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-[#252b3b] transition-colors"
      >
        <div className="flex items-center gap-2">
          <FileText size={14} className="text-indigo-400" />
          <span className="text-sm font-semibold text-white">Relatórios Core ({items.length})</span>
          <span className="text-xs text-slate-500">· Agency API</span>
          {pendingCount > 0 && (
            <span className="text-xs px-1.5 py-0.5 rounded-full bg-yellow-500/20 text-yellow-400 font-medium">
              {pendingCount} pendente{pendingCount > 1 ? 's' : ''}
            </span>
          )}
        </div>
        <span className="text-slate-600 text-xs">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="border-t border-[#2a2f3e] space-y-0">
          {items.map(n => {
            const next = NEXT_STATUS[n.status]
            const isExpanded = expanded === n.id
            const hasBlocks = (n.blocks?.length > 0) || (n.executed_actions?.length > 0) || (n.open_recommendations?.length > 0)
            return (
              <div key={n.id} className="border-b border-[#2a2f3e] last:border-0">
                <div className="flex items-center gap-3 px-4 py-3">
                  <div className="flex-1 min-w-0 grid grid-cols-4 gap-2 items-center">
                    <span className="text-xs text-slate-400">{n.report_type || '—'}</span>
                    <span className="text-xs text-slate-400 col-span-1">
                      {n.period_start && n.period_end ? `${n.period_start} → ${n.period_end}` : '—'}
                    </span>
                    <span className={`text-xs px-2 py-0.5 rounded font-medium w-fit ${NARRATIVE_STATUS_BADGE[n.status] || 'bg-slate-500/15 text-slate-400'}`}>
                      {n.status}
                    </span>
                    <span className="text-xs text-slate-500">
                      {n.published_at ? new Date(n.published_at).toLocaleDateString('pt-BR') : n.approved_by ? `aprov: ${n.approved_by.split('@')[0]}` : '—'}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {hasBlocks && (
                      <button
                        onClick={() => setExpanded(expanded === n.id ? null : n.id)}
                        className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
                      >
                        {isExpanded ? '▲' : '▼'}
                      </button>
                    )}
                    {next?.target === 'PUBLISHED' && (
                      <input
                        type="email"
                        placeholder="override email (opcional)"
                        value={recipientOverride[n.id] || ''}
                        onChange={e => setRecipientOverride(prev => ({ ...prev, [n.id]: e.target.value }))}
                        className="text-xs px-2 py-1 rounded border border-[#3a3f50] bg-[#0f1117] text-slate-300 placeholder-slate-600 w-44 focus:outline-none focus:border-indigo-500/50"
                      />
                    )}
                    {next && (
                      <button
                        onClick={() => transition(n, next.target)}
                        disabled={transitioning === n.id}
                        className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded border transition-colors disabled:opacity-50 ${next.cls}`}
                      >
                        {transitioning === n.id
                          ? <><Loader2 size={10} className="animate-spin" /> …</>
                          : next.label}
                      </button>
                    )}
                  </div>
                </div>
                {transError[n.id] && (
                  <p className="px-4 pb-2 text-xs text-red-400">{transError[n.id]}</p>
                )}
                {transOk[n.id] && (
                  <p className="px-4 pb-2 text-xs text-emerald-400">{transOk[n.id]}</p>
                )}
                {isExpanded && (
                  <div className="border-t border-[#2a2f3e] bg-[#0f1117]/40">
                    {/* Narrative blocks */}
                    {n.blocks?.map((b, i) => (
                      <div key={i} className="px-4 pb-3 pt-3 border-b border-[#2a2f3e]/50 last:border-0">
                        <p className="text-xs text-slate-500 font-semibold mb-1">{b.section}</p>
                        <p className="text-xs text-slate-400 leading-relaxed whitespace-pre-wrap">{b.statement}</p>
                      </div>
                    ))}

                    {/* Executed actions */}
                    {n.executed_actions?.length > 0 && (
                      <div className="px-4 py-3 border-t border-[#2a2f3e]/50">
                        <p className="text-xs text-slate-500 font-semibold mb-2">OTIMIZAÇÕES REALIZADAS NO PERÍODO</p>
                        <div className="space-y-2">
                          {n.executed_actions.map(a => (
                            <div key={a.id} className="border-l-2 border-indigo-500/60 pl-3">
                              <div className="flex flex-wrap gap-1.5 mb-1">
                                {a.executed_at && (
                                  <span className="text-xs text-slate-500">
                                    {new Date(a.executed_at).toLocaleDateString('pt-BR')}
                                  </span>
                                )}
                                {a.platform && (
                                  <span className="text-xs font-semibold text-indigo-400 bg-indigo-500/10 rounded px-1.5 py-0.5">
                                    {a.platform}
                                  </span>
                                )}
                                {a.action_type && (
                                  <span className="text-xs text-slate-500 bg-slate-700/40 rounded px-1.5 py-0.5">
                                    {a.action_type}
                                  </span>
                                )}
                              </div>
                              <p className="text-xs text-slate-300 font-medium leading-snug">{a.description}</p>
                              {a.outcome && (
                                <div className="flex flex-wrap gap-2 mt-1">
                                  {a.outcome.delta_label && (
                                    <span className="text-xs font-semibold text-emerald-400">{a.outcome.delta_label}</span>
                                  )}
                                  <span className={`text-xs px-1.5 py-0.5 rounded ${
                                    a.outcome.status_css === 'ok'      ? 'bg-emerald-500/15 text-emerald-400' :
                                    a.outcome.status_css === 'blocked' ? 'bg-red-500/15 text-red-400' :
                                    'bg-yellow-500/15 text-yellow-400'
                                  }`}>{a.outcome.status_label}</span>
                                  {a.outcome.confidence && (
                                    <span className="text-xs text-slate-500">confiança: {a.outcome.confidence}</span>
                                  )}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Open recommendations */}
                    {n.open_recommendations?.length > 0 && (
                      <div className="px-4 py-3 border-t border-[#2a2f3e]/50">
                        <p className="text-xs text-slate-500 font-semibold mb-2">PRÓXIMAS AÇÕES RECOMENDADAS</p>
                        <div className="space-y-1.5">
                          {n.open_recommendations.map(r => (
                            <div key={r.id} className="flex items-start gap-2">
                              <span className="text-xs font-bold text-indigo-400 mt-0.5">→</span>
                              <div>
                                <p className="text-xs text-slate-300 font-medium">{r.title || r.action}</p>
                                {r.expected_impact && (
                                  <p className="text-xs text-emerald-400/80 mt-0.5">{r.expected_impact}</p>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

type InsightType = 'all' | 'weekly_report' | 'monthly_report' | 'recommendation' | 'anomaly' | 'pattern'
type Severity    = 'all' | 'info' | 'warning' | 'critical'

interface Insight {
  id:         string
  type:       string
  severity:   string
  title:      string
  content:    string
  data:       { recommendation?: string; metrics?: Record<string, unknown> }
  is_read:    boolean
  created_at: string
}

const TYPE_LABELS: Record<string, string> = {
  weekly_report:  'Relatório Semanal',
  monthly_report: 'Relatório Mensal',
  recommendation: 'Recomendação',
  anomaly:        'Anomalia',
  pattern:        'Padrão',
}

const TYPE_ICON: Record<string, React.ElementType> = {
  weekly_report:  BarChart2,
  monthly_report: BarChart2,
  recommendation: Lightbulb,
  anomaly:        AlertTriangle,
  pattern:        Sparkles,
}

const TYPE_COLOR: Record<string, string> = {
  weekly_report:  'bg-indigo-500/15 text-indigo-300 border-indigo-500/30',
  monthly_report: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  recommendation: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  anomaly:        'bg-yellow-500/15 text-yellow-300 border-yellow-500/30',
  pattern:        'bg-purple-500/15 text-purple-300 border-purple-500/30',
}

const SEV_STYLE: Record<string, string> = {
  info:     'border-l-indigo-500',
  warning:  'border-l-yellow-500',
  critical: 'border-l-red-500',
}

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })

function downloadMarkdown(insights: Insight[], clientId: string) {
  const lines: string[] = [`# Relatório IA — ${clientId}\n`]
  for (const ins of insights) {
    lines.push(`## ${ins.title}`)
    lines.push(`_${TYPE_LABELS[ins.type] || ins.type} · ${ins.severity} · ${fmtDate(ins.created_at)}_\n`)
    lines.push(ins.content)
    if (ins.data?.recommendation) {
      lines.push(`\n**Ação recomendada:** ${ins.data.recommendation}`)
    }
    lines.push('\n---\n')
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = `relatorio-ia-${clientId}-${new Date().toISOString().slice(0, 10)}.md`
  a.click()
  URL.revokeObjectURL(url)
}

export default function ReportsPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [insights,    setInsights]    = useState<Insight[]>([])
  const [loading,     setLoading]     = useState(true)
  const [generating,  setGenerating]  = useState(false)
  const [typeFilter,  setTypeFilter]  = useState<InsightType>('all')
  const [sevFilter,   setSevFilter]   = useState<Severity>('all')
  const [expanded,    setExpanded]    = useState<Set<string>>(new Set())
  const [page,        setPage]        = useState(0)
  const [hasMore,     setHasMore]     = useState(false)

  const PAGE = 20

  const load = useCallback(async (reset = false) => {
    setLoading(true)
    const off = reset ? 0 : page * PAGE
    const params = new URLSearchParams({ limit: String(PAGE), offset: String(off) })
    if (typeFilter !== 'all') params.set('type', typeFilter)
    if (sevFilter  !== 'all') params.set('severity', sevFilter)

    try {
      const res  = await fetch(`${API_URL}/insights/${clientId}?${params}`)
      const json = await res.json()
      const rows: Insight[] = json.insights || []
      setInsights(prev => reset ? rows : [...prev, ...rows])
      setHasMore(rows.length === PAGE)
      if (reset) setPage(0)
    } finally {
      setLoading(false)
    }
  }, [clientId, typeFilter, sevFilter, page])

  useEffect(() => { load(true) }, [clientId, typeFilter, sevFilter])

  async function generate() {
    setGenerating(true)
    try {
      await fetch(`${API_URL}/insights/${clientId}/generate`, { method: 'POST' })
      await new Promise(r => setTimeout(r, 9000))
      await load(true)
    } finally {
      setGenerating(false)
    }
  }

  function toggle(id: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  async function markRead(id: string) {
    setInsights(prev => prev.map(i => i.id === id ? { ...i, is_read: true } : i))
    await fetch(`${API_URL}/insights/${clientId}/${id}/read`, { method: 'PATCH' })
  }

  const unread = insights.filter(i => !i.is_read).length


  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link href={`/clients/${clientId}/dashboard`} className="text-slate-500 hover:text-white">
            <ArrowLeft size={18} />
          </Link>
          <div>
            <div className="flex items-center gap-2">
              <FileText size={14} className="text-indigo-400" />
              <h1 className="text-lg font-bold text-white">Relatórios & Insights IA</h1>
              {unread > 0 && (
                <span className="bg-indigo-600 text-white text-xs px-1.5 py-0.5 rounded-full font-medium">
                  {unread} novo{unread > 1 ? 's' : ''}
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500 mt-0.5">
              Análises geradas pelo Claude — histórico completo
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {insights.length > 0 && (
            <button
              onClick={() => downloadMarkdown(insights, clientId)}
              className="flex items-center gap-1.5 text-xs bg-[#1a1f2e] hover:bg-[#252b3b] border border-[#2a2f3e] text-slate-300 px-3 py-2 rounded-lg"
            >
              <Download size={12} /> Exportar .md
            </button>
          )}
          <button
            onClick={generate}
            disabled={generating}
            className="flex items-center gap-2 text-xs bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-3 py-2 rounded-lg font-medium"
          >
            {generating
              ? <><Loader2 size={12} className="animate-spin" /> Analisando…</>
              : <><Sparkles size={12} /> Gerar análise</>}
          </button>
        </div>
      </div>

      <div className="p-6 space-y-5 max-w-4xl">

        {/* Core narratives (Agency API) — canonical source */}
        <CoreNarrativesSection clientId={clientId} />

        {/* Delivery settings link */}
        <div className="flex items-center justify-between bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl px-5 py-4">
          <div>
            <p className="text-sm font-semibold text-white">Entrega de relatórios</p>
            <p className="text-xs text-slate-500 mt-0.5">
              Configure destinatários, agenda semanal/mensal e aprovação em Configurações do cliente.
            </p>
          </div>
          <Link
            href={`/clients/${clientId}/settings`}
            className="flex items-center gap-1.5 text-xs text-indigo-400 hover:text-indigo-300 font-medium shrink-0 ml-4"
          >
            <Settings size={12} /> Configurar
          </Link>
        </div>

        {/* ── Insights IA ─────────────────────────────────────────────── */}
        <div className="flex items-center gap-3">
          <div className="h-px flex-1 bg-[#2a2f3e]" />
          <span className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider">Insights IA</span>
          <div className="h-px flex-1 bg-[#2a2f3e]" />
        </div>

        {/* Filtros */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
            {(['all', 'weekly_report', 'monthly_report', 'recommendation', 'anomaly', 'pattern'] as InsightType[]).map(t => (
              <button
                key={t}
                onClick={() => setTypeFilter(t)}
                className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                  typeFilter === t ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
                }`}
              >
                {t === 'all' ? 'Todos' : TYPE_LABELS[t]}
              </button>
            ))}
          </div>
          <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
            {(['all', 'info', 'warning', 'critical'] as Severity[]).map(s => (
              <button
                key={s}
                onClick={() => setSevFilter(s)}
                className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                  sevFilter === s ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
                }`}
              >
                {s === 'all' ? 'Severidade' : s.charAt(0).toUpperCase() + s.slice(1)}
              </button>
            ))}
          </div>
          {!loading && (
            <button onClick={() => load(true)} className="text-slate-500 hover:text-white ml-auto">
              <RefreshCw size={14} />
            </button>
          )}
        </div>

        {/* Insights list */}
        {loading && insights.length === 0 ? (
          <div className="flex items-center gap-2 text-slate-500 text-sm py-12">
            <Loader2 size={16} className="animate-spin" /> Carregando…
          </div>
        ) : insights.length === 0 ? (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-12 text-center">
            <Sparkles size={36} className="text-slate-600 mx-auto mb-3" />
            <p className="text-slate-400 font-medium">Nenhum insight encontrado</p>
            <p className="text-slate-600 text-xs mt-1">
              {typeFilter !== 'all' || sevFilter !== 'all'
                ? 'Tente remover os filtros.'
                : 'Clique em "Gerar análise" para o Claude analisar seus dados.'}
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {insights.map(ins => {
              const Icon   = TYPE_ICON[ins.type] || Lightbulb
              const isOpen = expanded.has(ins.id)
              return (
                <div
                  key={ins.id}
                  className={`bg-[#1a1f2e] border border-[#2a2f3e] border-l-4 ${SEV_STYLE[ins.severity] || 'border-l-slate-600'} rounded-xl overflow-hidden transition-opacity ${ins.is_read ? 'opacity-60' : ''}`}
                >
                  <button
                    onClick={() => { toggle(ins.id); if (!ins.is_read) markRead(ins.id) }}
                    className="w-full text-left px-5 py-4 flex items-start gap-3 hover:bg-[#252b3b] transition-colors"
                  >
                    <Icon size={15} className="shrink-0 mt-0.5 text-slate-400" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap mb-1">
                        <span className={`text-xs px-2 py-0.5 rounded border ${TYPE_COLOR[ins.type] || 'bg-slate-500/15 text-slate-300 border-slate-500/30'}`}>
                          {TYPE_LABELS[ins.type] || ins.type}
                        </span>
                        {!ins.is_read && (
                          <span className="w-2 h-2 rounded-full bg-indigo-400 shrink-0" />
                        )}
                      </div>
                      <p className="text-sm font-semibold text-white">{ins.title}</p>
                      <p className="text-xs text-slate-500 mt-0.5">{fmtDate(ins.created_at)}</p>
                    </div>
                    <span className="text-slate-600 text-xs shrink-0">
                      {isOpen ? '▲' : '▼'}
                    </span>
                  </button>

                  {isOpen && (
                    <div className="px-5 pb-5 border-t border-[#2a2f3e]">
                      <p className="text-sm text-slate-300 mt-4 leading-relaxed whitespace-pre-wrap">
                        {ins.content}
                      </p>
                      {ins.data?.recommendation && (
                        <div className="mt-4 bg-[#0f1117] rounded-lg p-4 border border-[#2a2f3e]">
                          <p className="text-xs font-semibold text-emerald-400 mb-1.5">Ação recomendada</p>
                          <p className="text-sm text-slate-300">{ins.data.recommendation}</p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}

            {hasMore && (
              <button
                onClick={() => { setPage(p => p + 1); load() }}
                disabled={loading}
                className="w-full py-3 text-sm text-slate-400 hover:text-white border border-[#2a2f3e] rounded-xl bg-[#1a1f2e] hover:bg-[#252b3b] disabled:opacity-50"
              >
                {loading ? <Loader2 size={14} className="animate-spin mx-auto" /> : 'Carregar mais'}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
