'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { BrainCircuit, RefreshCw, Loader2, TrendingUp, TrendingDown, Minus, UploadCloud, AlertCircle } from 'lucide-react'
import { useDatePeriod } from '@/lib/use-date-range'
import { PeriodPicker } from '@/components/PeriodPicker'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Summary {
  has_data:               boolean
  prompts_run:            number
  responses_analyzed:     number
  mention_rate:           number | null
  avg_position:           number | null
  positive_sentiment_rate: number | null
  share_of_voice:         number | null
  last_import_at:         string | null
}

interface TrendPoint {
  date:         string
  platform:     string
  mention_rate: number
  mentioned:    number
  total:        number
}

interface PromptRow {
  prompt_id:    string
  prompt_text:  string
  category:     string | null
  intent:       string | null
  total_runs:   number
  mention_rate: number | null
  avg_position: number | null
  positive_rate: number | null
}

interface CompetitorRow {
  brand_name: string
  mentions:   number
  share:      number
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmtPct = (n: number | null) =>
  n === null ? '—' : `${(n * 100).toFixed(1)}%`

const fmtPos = (n: number | null) =>
  n === null ? '—' : `#${n.toFixed(1)}`

const PLATFORM_COLORS: Record<string, string> = {
  chatgpt:    'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
  gemini:     'bg-blue-500/20 text-blue-300 border-blue-500/30',
  perplexity: 'bg-orange-500/20 text-orange-300 border-orange-500/30',
  claude:     'bg-purple-500/20 text-purple-300 border-purple-500/30',
}

const CATEGORY_LABELS: Record<string, string> = {
  recommendation:   'Recomendação',
  comparison:       'Comparação',
  problem_solution: 'Problema/Solução',
  alternative:      'Alternativa',
  review:           'Avaliação',
}

function KpiCard({
  label, value, sub, delta, help,
}: {
  label: string; value: string; sub?: string; delta?: number; help?: string
}) {
  const DeltaIcon = delta === undefined ? null
    : delta > 0.02 ? TrendingUp : delta < -0.02 ? TrendingDown : Minus
  const deltaColor = delta === undefined ? '' : delta > 0.02 ? 'text-emerald-400' : delta < -0.02 ? 'text-red-400' : 'text-slate-400'

  return (
    <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl p-5">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className="text-2xl font-bold text-white tabular-nums">{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
      {DeltaIcon && delta !== undefined && (
        <div className={`flex items-center gap-1 mt-2 text-xs ${deltaColor}`}>
          <DeltaIcon size={12} />
          <span>{delta > 0 ? '+' : ''}{(delta * 100).toFixed(1)}% vs mês ant.</span>
        </div>
      )}
    </div>
  )
}

function MentionBar({ rate, max = 1 }: { rate: number; max?: number }) {
  const pct = Math.min((rate / max) * 100, 100)
  const color = rate >= 0.5 ? 'bg-emerald-500' : rate >= 0.3 ? 'bg-yellow-500' : 'bg-red-500'
  return (
    <div className="flex items-center gap-2 w-full">
      <div className="flex-1 bg-[#0f1117] rounded-full h-1.5">
        <div className={`h-1.5 rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-slate-300 w-12 text-right">{fmtPct(rate)}</span>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AIVisibilityPage() {
  const params   = useParams()
  const router   = useRouter()
  const clientId = params.clientId as string
  const { from, to } = useDatePeriod()

  const [summary,     setSummary]     = useState<Summary | null>(null)
  const [trend,       setTrend]       = useState<TrendPoint[]>([])
  const [prompts,     setPrompts]     = useState<PromptRow[]>([])
  const [competitors, setCompetitors] = useState<CompetitorRow[]>([])
  const [platform,    setPlatform]    = useState<string>('')
  const [loading,     setLoading]     = useState(true)
  const [error,       setError]       = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const qs = `start=${from}&end=${to}${platform ? `&platform=${platform}` : ''}`
      const [s, t, p, c] = await Promise.all([
        fetch(`${API_URL}/ai-visibility/${clientId}/summary?${qs}`).then(r => r.json()),
        fetch(`${API_URL}/ai-visibility/${clientId}/trend?${qs}`).then(r => r.json()),
        fetch(`${API_URL}/ai-visibility/${clientId}/prompts?${qs}`).then(r => r.json()),
        fetch(`${API_URL}/ai-visibility/${clientId}/competitors?${qs}`).then(r => r.json()),
      ])
      setSummary(s)
      setTrend(Array.isArray(t) ? t : [])
      setPrompts(Array.isArray(p) ? p : [])
      setCompetitors(Array.isArray(c) ? c : [])
    } catch (e) {
      setError('Erro ao carregar dados de AI Visibility')
    } finally {
      setLoading(false)
    }
  }, [clientId, from, to, platform])

  useEffect(() => { load() }, [load])

  // Detectar plataformas com dados
  const platforms = [...new Set(trend.map(t => t.platform))].sort()

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <BrainCircuit className="text-indigo-400 shrink-0" size={22} />
          <div>
            <h1 className="text-lg font-bold text-white">AI Visibility</h1>
            <p className="text-xs text-slate-500 mt-0.5">
              Presença da marca no ChatGPT, Gemini, Perplexity e Claude
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          <PeriodPicker />
          <select
            value={platform}
            onChange={e => setPlatform(e.target.value)}
            className="h-8 px-2 bg-[#1a1f2e] border border-[#2a2f3e] rounded text-xs text-slate-300 focus:outline-none"
          >
            <option value="">Todas as plataformas</option>
            {platforms.map(p => (
              <option key={p} value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>
            ))}
          </select>
          <button
            onClick={() => router.push('/ai-visibility/import')}
            className="h-8 px-3 bg-indigo-600 hover:bg-indigo-500 rounded text-xs text-white flex items-center gap-1.5 transition-colors"
          >
            <UploadCloud size={13} />
            Importar dados
          </button>
          <button
            onClick={load}
            className="h-8 w-8 flex items-center justify-center bg-[#1a1f2e] border border-[#2a2f3e] rounded hover:bg-[#252a3a] transition-colors"
          >
            {loading ? <Loader2 size={13} className="animate-spin text-slate-400" /> : <RefreshCw size={13} className="text-slate-400" />}
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
          <AlertCircle size={15} />
          {error}
        </div>
      )}

      {/* Empty state */}
      {!loading && summary && !summary.has_data && (
        <div className="flex flex-col items-center justify-center py-16 gap-4 text-center">
          <BrainCircuit size={40} className="text-slate-600" />
          <p className="text-slate-400 text-sm font-medium">Nenhum dado de AI Visibility</p>
          <p className="text-slate-600 text-xs max-w-xs">
            Exporte o CSV do Ubersuggest AI Search Visibility e importe aqui para começar a monitorar.
          </p>
          <button
            onClick={() => router.push('/ai-visibility/import')}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 rounded-lg text-sm text-white flex items-center gap-2 transition-colors"
          >
            <UploadCloud size={14} />
            Importar primeiro CSV
          </button>
        </div>
      )}

      {summary?.has_data && (
        <>
          {/* KPI cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <KpiCard
              label="Taxa de menção"
              value={fmtPct(summary.mention_rate)}
              sub={`${summary.prompts_run} prompts analisados`}
            />
            <KpiCard
              label="Posição média"
              value={fmtPos(summary.avg_position)}
              sub="quando citada (1 = primeira)"
            />
            <KpiCard
              label="Share of voice"
              value={fmtPct(summary.share_of_voice)}
              sub="das menções totais"
            />
            <KpiCard
              label="Sentimento positivo"
              value={fmtPct(summary.positive_sentiment_rate)}
              sub="das menções da marca"
            />
          </div>

          {/* Last update */}
          {summary.last_import_at && (
            <p className="text-xs text-slate-600">
              Último import: {new Date(summary.last_import_at).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}
            </p>
          )}

          {/* Trend by platform */}
          {trend.length > 0 && (
            <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl p-5">
              <h2 className="text-sm font-semibold text-white mb-4">Taxa de menção por plataforma</h2>
              <div className="space-y-3">
                {platforms.map(plat => {
                  const platPoints = trend.filter(t => t.platform === plat)
                  const avgRate = platPoints.reduce((s, p) => s + p.mention_rate, 0) / platPoints.length
                  return (
                    <div key={plat} className="flex items-center gap-3">
                      <span className={`text-xs px-2 py-0.5 rounded border ${PLATFORM_COLORS[plat] || 'bg-slate-500/20 text-slate-300 border-slate-500/30'} w-24 text-center shrink-0`}>
                        {plat}
                      </span>
                      <MentionBar rate={avgRate} />
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Competitors */}
          {competitors.length > 0 && (
            <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl p-5">
              <h2 className="text-sm font-semibold text-white mb-4">Share of voice — competidores</h2>
              <div className="space-y-2">
                {competitors.slice(0, 8).map((c, i) => (
                  <div key={c.brand_name} className="flex items-center gap-3">
                    <span className="text-xs text-slate-500 w-5 shrink-0 tabular-nums">{i + 1}</span>
                    <span className="text-xs text-slate-300 w-32 shrink-0 truncate">{c.brand_name}</span>
                    <div className="flex-1 bg-[#0f1117] rounded-full h-1.5">
                      <div
                        className="h-1.5 rounded-full bg-indigo-500"
                        style={{ width: `${Math.min(c.share * 100 * 3, 100)}%` }}
                      />
                    </div>
                    <span className="text-xs text-slate-400 tabular-nums w-12 text-right">{fmtPct(c.share)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Prompts table */}
          {prompts.length > 0 && (
            <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl overflow-hidden">
              <div className="px-5 py-4 border-b border-[#2a2f3e]">
                <h2 className="text-sm font-semibold text-white">Performance por prompt</h2>
                <p className="text-xs text-slate-500 mt-0.5">{prompts.length} prompts monitorados</p>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-[#2a2f3e]">
                      <th className="text-left px-5 py-3 text-slate-500 font-medium">Prompt</th>
                      <th className="text-left px-3 py-3 text-slate-500 font-medium">Categoria</th>
                      <th className="text-right px-3 py-3 text-slate-500 font-medium">Menção</th>
                      <th className="text-right px-3 py-3 text-slate-500 font-medium">Posição</th>
                      <th className="text-right px-5 py-3 text-slate-500 font-medium">Positivo</th>
                    </tr>
                  </thead>
                  <tbody>
                    {prompts.map(p => (
                      <tr key={p.prompt_id} className="border-b border-[#1a1f2e] hover:bg-[#1a1f2e]/50 transition-colors">
                        <td className="px-5 py-3 text-slate-300 max-w-[280px]">
                          <span className="line-clamp-2 leading-relaxed">{p.prompt_text}</span>
                          {p.intent && (
                            <span className={`mt-1 inline-block text-[10px] px-1.5 py-0.5 rounded ${
                              p.intent === 'high_intent' ? 'bg-emerald-500/20 text-emerald-400' :
                              p.intent === 'mid_intent'  ? 'bg-yellow-500/20 text-yellow-400' :
                              'bg-slate-500/20 text-slate-400'
                            }`}>
                              {p.intent === 'high_intent' ? 'alta intenção' : p.intent === 'mid_intent' ? 'média intenção' : 'baixa intenção'}
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-3 text-slate-500">
                          {p.category ? CATEGORY_LABELS[p.category] || p.category : '—'}
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={`font-medium tabular-nums ${
                            (p.mention_rate || 0) >= 0.5 ? 'text-emerald-400' :
                            (p.mention_rate || 0) >= 0.3 ? 'text-yellow-400' :
                            (p.mention_rate || 0) > 0    ? 'text-red-400' :
                            'text-slate-600'
                          }`}>
                            {fmtPct(p.mention_rate)}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right text-slate-400 tabular-nums">{fmtPos(p.avg_position)}</td>
                        <td className="px-5 py-3 text-right text-slate-400 tabular-nums">{fmtPct(p.positive_rate)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
