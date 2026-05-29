'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import {
  ArrowLeft, Sparkles, Loader2, BarChart2, Lightbulb,
  AlertTriangle, RefreshCw, Download, FileText, Send, CheckCircle,
} from 'lucide-react'
import { useAgencyPlan } from '@/lib/use-agency-plan'
import { PlanGate } from '@/components/plan-gate'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

type InsightType = 'all' | 'weekly_report' | 'recommendation' | 'anomaly' | 'pattern'
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
  recommendation: 'Recomendação',
  anomaly:        'Anomalia',
  pattern:        'Padrão',
}

const TYPE_ICON: Record<string, React.ElementType> = {
  weekly_report:  BarChart2,
  recommendation: Lightbulb,
  anomaly:        AlertTriangle,
  pattern:        Sparkles,
}

const TYPE_COLOR: Record<string, string> = {
  weekly_report:  'bg-indigo-500/15 text-indigo-300 border-indigo-500/30',
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
  const { plan, loading: planLoading } = useAgencyPlan(clientId)

  const [insights,    setInsights]    = useState<Insight[]>([])
  const [loading,     setLoading]     = useState(true)
  const [generating,  setGenerating]  = useState(false)
  const [sending,     setSending]     = useState(false)
  const [sentTo,      setSentTo]      = useState<string | null>(null)
  const [sendError,   setSendError]   = useState<string | null>(null)
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

  async function sendReport() {
    setSending(true)
    setSentTo(null)
    setSendError(null)
    try {
      const res  = await fetch(`${API_URL}/insights/${clientId}/report`, { method: 'POST' })
      const json = await res.json()
      if (!res.ok) {
        setSendError(json.detail || `Erro ${res.status}`)
      } else {
        setSentTo(json.email)
      }
    } catch (e: unknown) {
      setSendError(e instanceof Error ? e.message : 'Erro ao enviar')
    } finally {
      setSending(false)
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

  if (!planLoading && !plan.gates['ai_insights']) {
    return <PlanGate feature="ai_insights" planId={plan.planId} fullPage>{null}</PlanGate>
  }

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
            onClick={sendReport}
            disabled={sending || generating}
            className="flex items-center gap-2 text-xs bg-[#1a1f2e] hover:bg-[#252b3b] border border-[#2a2f3e] disabled:opacity-50 text-slate-300 px-3 py-2 rounded-lg font-medium"
            title="Gera análise IA e envia por email (usa alert_email do cliente)"
          >
            {sending
              ? <><Loader2 size={12} className="animate-spin" /> Enviando…</>
              : <><Send size={12} /> Enviar relatório</>}
          </button>
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

        {/* Report send result */}
        {sentTo && (
          <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs rounded-lg px-4 py-2.5">
            <CheckCircle size={13} />
            Relatório enviado para <strong className="font-semibold">{sentTo}</strong>. Pode levar alguns minutos para chegar.
          </div>
        )}
        {sendError && (
          <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/20 text-red-400 text-xs rounded-lg px-4 py-2.5">
            <AlertTriangle size={13} />
            {sendError}
          </div>
        )}

        {/* Filtros */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
            {(['all', 'weekly_report', 'recommendation', 'anomaly', 'pattern'] as InsightType[]).map(t => (
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
