'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { Loader2, RefreshCw, Sparkles, TrendingUp, TrendingDown, AlertCircle } from 'lucide-react'
import { useAgencyPlan } from '@/lib/use-agency-plan'
import { PlanGate } from '@/components/plan-gate'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

interface CreativeRow {
  ad_id:            string
  ad_name:          string | null
  campaign_id:      string | null
  image_url:        string | null
  headline:         string | null
  body:             string | null
  call_to_action:   string | null
  effective_status: string | null
  spend:            number
  clicks:           number
  impressions:      number
  purchases:        number
  revenue:          number
  roas:             number | null
  cpa:              number | null
  ctr:              number | null
}

interface Analysis {
  id:         string
  title:     string
  content:   string
  created_at: string
  data: {
    winning_patterns?: string[]
    losing_patterns?:  string[]
    next_brief?:       string
    top_ads?:          Array<{ ad_id: string; ad_name: string; roas: number; image_url: string }>
    bottom_ads?:       Array<{ ad_id: string; ad_name: string; roas: number; image_url: string }>
    ads_analyzed?:     number
    lookback_days?:    number
  }
}

const fmt = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n)

export default function CreativesPage() {
  const params  = useParams()
  const pixelId = params.clientId as string
  const { plan } = useAgencyPlan(pixelId)

  const [days, setDays] = useState<7 | 30 | 90>(30)
  const [creatives, setCreatives] = useState<CreativeRow[]>([])
  const [totalSpend, setTotalSpend] = useState(0)
  const [totalRevenue, setTotalRevenue] = useState(0)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [sort, setSort] = useState<'spend' | 'roas' | 'ctr'>('spend')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [galRes, anaRes] = await Promise.all([
        fetch(`${API_URL}/creatives/${pixelId}?days=${days}`),
        fetch(`${API_URL}/creatives/${pixelId}/latest-analysis`),
      ])
      if (galRes.ok) {
        const data = await galRes.json()
        setCreatives(data.creatives || [])
        setTotalSpend(data.total_spend || 0)
        setTotalRevenue(data.total_revenue || 0)
      }
      if (anaRes.ok) {
        const data = await anaRes.json()
        setAnalysis(data.analysis || null)
      }
    } finally {
      setLoading(false)
    }
  }, [pixelId, days])

  useEffect(() => { load() }, [load])

  async function handleSync() {
    setSyncing(true); setMsg(null)
    try {
      const res = await fetch(`${API_URL}/creatives/${pixelId}/sync`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || data.error || 'falha ao sincronizar')
      setMsg({ ok: true, text: `${data.upserted} criativos sincronizados` })
      await load()
    } catch (e) {
      setMsg({ ok: false, text: 'erro: ' + (e as Error).message })
    } finally {
      setSyncing(false)
    }
  }

  async function handleAnalyze() {
    setAnalyzing(true); setMsg(null)
    try {
      const res = await fetch(`${API_URL}/creatives/${pixelId}/analyze`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || data.error || 'falha ao analisar')
      setMsg({ ok: true, text: 'análise gerada' })
      await load()
    } catch (e) {
      setMsg({ ok: false, text: 'erro: ' + (e as Error).message })
    } finally {
      setAnalyzing(false)
    }
  }

  const sorted = [...creatives].sort((a, b) => {
    if (sort === 'roas') return (b.roas || 0) - (a.roas || 0)
    if (sort === 'ctr')  return (b.ctr  || 0) - (a.ctr  || 0)
    return b.spend - a.spend
  })

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white">Criativos · Análise visual com IA</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Performance por anúncio + padrões que separam vencedores de perdedores
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
            {[7, 30, 90].map(r => (
              <button key={r} onClick={() => setDays(r as 7 | 30 | 90)}
                className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                  days === r ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
                }`}>
                {r}d
              </button>
            ))}
          </div>
          <button
            onClick={handleSync}
            disabled={syncing}
            className="flex items-center gap-2 text-xs bg-[#1a1f2e] hover:bg-[#252a3a] border border-[#2a2f3e] text-slate-300 px-3 py-2 rounded-lg"
          >
            {syncing
              ? <><Loader2 size={12} className="animate-spin" />Sincronizando…</>
              : <><RefreshCw size={12} />Sincronizar criativos</>}
          </button>
          <button
            onClick={handleAnalyze}
            disabled={analyzing}
            className="flex items-center gap-2 text-xs bg-indigo-600/20 hover:bg-indigo-600/30 border border-indigo-500/30 text-indigo-200 px-3 py-2 rounded-lg"
          >
            {analyzing
              ? <><Loader2 size={12} className="animate-spin" />Analisando…</>
              : <><Sparkles size={12} />Analisar com IA</>}
          </button>
        </div>
      </div>

      {msg && (
        <div className={`mx-6 mt-4 px-3 py-2 rounded-lg text-xs flex items-center gap-2 ${
          msg.ok
            ? 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-300'
            : 'bg-rose-500/10 border border-rose-500/30 text-rose-300'
        }`}>
          {msg.ok ? null : <AlertCircle size={12} />}{msg.text}
        </div>
      )}

      {/* Analysis card — Creative Intelligence (Predição plan) */}
      <PlanGate feature="creative_intelligence" planId={plan.planId}>
      {analysis && (
        <div className="mx-6 mt-6 bg-gradient-to-br from-indigo-500/10 to-violet-500/5 border border-indigo-500/30 rounded-2xl p-5">
          <div className="flex items-start gap-3 mb-3">
            <div className="bg-indigo-500/20 p-2 rounded-lg">
              <Sparkles size={16} className="text-indigo-300" />
            </div>
            <div className="flex-1 min-w-0">
              <h2 className="text-sm font-bold text-white">{analysis.title}</h2>
              <p className="text-xs text-slate-500 mt-0.5">
                {analysis.data.ads_analyzed} criativos · janela {analysis.data.lookback_days}d · gerado em {new Date(analysis.created_at).toLocaleDateString('pt-BR')}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
            <div className="bg-[#0f1117]/60 border border-emerald-500/20 rounded-xl p-4">
              <div className="flex items-center gap-2 mb-2">
                <TrendingUp size={13} className="text-emerald-400" />
                <p className="text-xs font-semibold uppercase tracking-wider text-emerald-400">Padrões dos vencedores</p>
              </div>
              <ul className="space-y-1.5 text-xs text-slate-300">
                {(analysis.data.winning_patterns || []).map((p, i) => (
                  <li key={i} className="flex gap-2"><span className="text-emerald-400">•</span><span>{p}</span></li>
                ))}
              </ul>
            </div>
            <div className="bg-[#0f1117]/60 border border-rose-500/20 rounded-xl p-4">
              <div className="flex items-center gap-2 mb-2">
                <TrendingDown size={13} className="text-rose-400" />
                <p className="text-xs font-semibold uppercase tracking-wider text-rose-400">Padrões dos perdedores</p>
              </div>
              <ul className="space-y-1.5 text-xs text-slate-300">
                {(analysis.data.losing_patterns || []).map((p, i) => (
                  <li key={i} className="flex gap-2"><span className="text-rose-400">•</span><span>{p}</span></li>
                ))}
              </ul>
            </div>
          </div>
          {analysis.data.next_brief && (
            <div className="mt-4 bg-[#0f1117]/60 border border-violet-500/20 rounded-xl p-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-violet-400 mb-1">Briefing do próximo criativo</p>
              <p className="text-sm text-slate-200">{analysis.data.next_brief}</p>
            </div>
          )}
        </div>
      )}
      </PlanGate>

      {/* KPIs */}
      <div className="px-6 pt-6 grid grid-cols-2 md:grid-cols-4 gap-3">
        <Kpi label="Total de criativos" value={creatives.length.toString()} />
        <Kpi label="Spend" value={fmt(totalSpend)} />
        <Kpi label="Receita Meta" value={fmt(totalRevenue)} accent="emerald" />
        <Kpi label="ROAS médio" value={totalSpend > 0 ? (totalRevenue / totalSpend).toFixed(2) + 'x' : '—'} accent="teal" />
      </div>

      {/* Gallery */}
      <div className="p-6">
        <div className="flex items-center justify-between mb-3">
          <p className="text-xs uppercase tracking-wider text-slate-500 font-medium">
            Galeria de criativos
          </p>
          <div className="flex gap-1 text-xs">
            {(['spend', 'roas', 'ctr'] as const).map(s => (
              <button key={s} onClick={() => setSort(s)}
                className={`px-2 py-1 rounded ${
                  sort === s ? 'bg-indigo-600/30 text-indigo-200 border border-indigo-500/30' : 'text-slate-500 hover:text-white'
                }`}>
                {s.toUpperCase()}
              </button>
            ))}
          </div>
        </div>

        {loading ? (
          <div className="flex items-center gap-2 text-slate-500 text-sm py-12 justify-center">
            <Loader2 size={16} className="animate-spin" /> Carregando…
          </div>
        ) : sorted.length === 0 ? (
          <p className="text-slate-500 text-sm text-center py-12">
            Sem criativos sincronizados. Clique <span className="text-slate-300">"Sincronizar criativos"</span>.
          </p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
            {sorted.map(c => (
              <div key={c.ad_id} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
                {c.image_url ? (
                  <div className="aspect-square bg-[#0f1117] flex items-center justify-center">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={c.image_url} alt={c.ad_name || ''} className="max-w-full max-h-full object-contain" />
                  </div>
                ) : (
                  <div className="aspect-square bg-[#0f1117] flex items-center justify-center text-slate-700 text-xs">sem imagem</div>
                )}
                <div className="p-3">
                  <p className="text-xs font-medium text-white truncate" title={c.ad_name || ''}>
                    {c.ad_name || '—'}
                  </p>
                  {c.headline && (
                    <p className="text-xs text-slate-400 mt-1 line-clamp-2">{c.headline}</p>
                  )}
                  <div className="grid grid-cols-3 gap-2 mt-3 pt-3 border-t border-[#2a2f3e]">
                    <Stat label="Spend" value={fmt(c.spend)} />
                    <Stat label="ROAS"  value={c.roas != null ? c.roas.toFixed(2) + 'x' : '—'} accent={c.roas && c.roas >= 2 ? 'emerald' : c.roas && c.roas < 1 ? 'rose' : undefined} />
                    <Stat label="CTR"   value={c.ctr  != null ? c.ctr.toFixed(2)  + '%' : '—'} />
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function Kpi({ label, value, accent }: { label: string; value: string; accent?: 'emerald' | 'teal' }) {
  return (
    <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl px-4 py-3">
      <p className="text-xs text-slate-500">{label}</p>
      <p className={`text-base font-bold mt-1 ${
        accent === 'emerald' ? 'text-emerald-400' :
        accent === 'teal'    ? 'text-teal-400'    : 'text-white'
      }`}>{value}</p>
    </div>
  )
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: 'emerald' | 'rose' }) {
  return (
    <div>
      <p className="text-[10px] text-slate-600 uppercase tracking-wider">{label}</p>
      <p className={`text-xs font-semibold mt-0.5 ${
        accent === 'emerald' ? 'text-emerald-400' :
        accent === 'rose'    ? 'text-rose-400'    : 'text-slate-200'
      }`}>{value}</p>
    </div>
  )
}
