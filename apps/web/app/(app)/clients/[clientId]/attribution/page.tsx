'use client'

import { useEffect, useState, useCallback } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ArrowLeft, RefreshCw, Loader2, TrendingUp, Info } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

const MODELS = [
  { value: 'last_click',     label: 'Último clique',  desc: '100% para a última fonte (padrão Meta)' },
  { value: 'first_click',    label: 'Primeiro clique', desc: '100% para a aquisição inicial' },
  { value: 'linear',         label: 'Linear',         desc: 'Crédito igual entre todos os toques' },
  { value: 'time_decay',     label: 'Time decay',      desc: 'Decay exponencial, half-life 7 dias' },
  { value: 'position_based', label: 'Posição (U)',     desc: '40% primeiro + 40% último + 20% meio' },
] as const

type Model = typeof MODELS[number]['value']

const DAY_RANGES = [
  { value: 7,  label: '7d'  },
  { value: 30, label: '30d' },
  { value: 90, label: '90d' },
] as const

interface PlatformRow {
  platform:    string
  revenue:     number
  conversions: number
  orders:      number
  share_pct:   number
}

interface SourceRow {
  source:      string
  medium:      string | null
  campaign:    string | null
  revenue:     number
  conversions: number
}

interface Summary {
  model:         Model
  days:          number
  total_revenue: number
  by_platform:   PlatformRow[]
  by_source:     SourceRow[]
}

const PLATFORM_COLORS: Record<string, string> = {
  meta:       'bg-blue-500/20 text-blue-300 border-blue-500/30',
  google:     'bg-yellow-500/20 text-yellow-300 border-yellow-500/30',
  tiktok:     'bg-pink-500/20 text-pink-300 border-pink-500/30',
  pinterest:  'bg-red-500/20 text-red-300 border-red-500/30',
  organic:    'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
  direct:     'bg-slate-500/20 text-slate-300 border-slate-500/30',
  email:      'bg-purple-500/20 text-purple-300 border-purple-500/30',
  shopify:    'bg-green-500/20 text-green-300 border-green-500/30',
  other:      'bg-slate-500/20 text-slate-400 border-slate-500/30',
}

const fmtBRL = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n)

export default function AttributionPage() {
  const params = useParams()
  const clientId = params.clientId as string

  const [model,   setModel]   = useState<Model>('last_click')
  const [days,    setDays]    = useState<number>(30)
  const [data,    setData]    = useState<Summary | null>(null)
  const [loading, setLoading] = useState(true)
  const [recomputing, setRecomputing] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API_URL}/attribution/${clientId}/summary?model=${model}&days=${days}`)
      if (res.ok) setData(await res.json())
    } finally {
      setLoading(false)
    }
  }, [clientId, model, days])

  useEffect(() => { load() }, [load])

  async function recompute() {
    setRecomputing(true)
    try {
      await fetch(`${API_URL}/attribution/${clientId}/recompute?days=${days}`, { method: 'POST' })
      await new Promise(r => setTimeout(r, 3000)) // give backend a moment
      await load()
    } finally {
      setRecomputing(false)
    }
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
              <TrendingUp size={14} className="text-indigo-400" />
              <h1 className="text-lg font-bold text-white">Atribuição unificada</h1>
            </div>
            <p className="text-xs text-slate-500 mt-0.5">
              Resolve overlap entre Meta, Google e tráfego direto — modelo configurável
            </p>
          </div>
        </div>
        <button
          onClick={recompute}
          disabled={recomputing}
          className="flex items-center gap-2 text-xs bg-[#1a1f2e] hover:bg-[#252b3b] border border-[#2a2f3e] text-slate-300 px-3 py-2 rounded-lg disabled:opacity-50"
        >
          {recomputing ? <><Loader2 size={12} className="animate-spin" /> Recalculando…</> : <><RefreshCw size={12} /> Recalcular</>}
        </button>
      </div>

      <div className="p-6 space-y-5 max-w-6xl">
        {/* Controls */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
            {MODELS.map(m => (
              <button
                key={m.value}
                onClick={() => setModel(m.value)}
                title={m.desc}
                className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                  model === m.value
                    ? 'bg-indigo-600 text-white'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
          <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
            {DAY_RANGES.map(d => (
              <button
                key={d.value}
                onClick={() => setDays(d.value)}
                className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                  days === d.value
                    ? 'bg-indigo-600 text-white'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {d.label}
              </button>
            ))}
          </div>
          <p className="text-xs text-slate-500 ml-auto flex items-center gap-1.5">
            <Info size={12} />
            {MODELS.find(m => m.value === model)?.desc}
          </p>
        </div>

        {loading && !data ? (
          <div className="flex items-center gap-2 text-slate-500 text-sm py-12">
            <Loader2 size={16} className="animate-spin" /> Carregando…
          </div>
        ) : !data || data.total_revenue === 0 ? (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-8 text-center">
            <p className="text-slate-400">Sem dados de atribuição para os últimos {days} dias.</p>
            <p className="text-xs text-slate-500 mt-1">Clique em &quot;Recalcular&quot; para processar pedidos existentes.</p>
          </div>
        ) : (
          <>
            {/* Total */}
            <div className="bg-gradient-to-br from-indigo-600/10 to-purple-600/10 border border-indigo-500/20 rounded-xl p-5">
              <p className="text-xs text-slate-400 uppercase tracking-wider">Receita atribuída — modelo {MODELS.find(m => m.value === model)?.label}</p>
              <p className="text-3xl font-bold text-white mt-1">{fmtBRL(data.total_revenue)}</p>
              <p className="text-xs text-slate-500 mt-1">Últimos {days} dias</p>
            </div>

            {/* By platform */}
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
              <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-4">
                Por plataforma
              </h3>
              <div className="space-y-3">
                {data.by_platform.map(p => (
                  <div key={p.platform}>
                    <div className="flex items-center justify-between text-sm mb-1.5">
                      <div className="flex items-center gap-2">
                        <span className={`text-xs px-2 py-0.5 rounded border ${PLATFORM_COLORS[p.platform] || PLATFORM_COLORS.other}`}>
                          {p.platform}
                        </span>
                        <span className="text-slate-500 text-xs">
                          {p.conversions.toFixed(1)} conversões · {p.orders} pedidos
                        </span>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className="text-emerald-400 font-semibold">{fmtBRL(p.revenue)}</span>
                        <span className="text-xs text-slate-500 w-12 text-right">{p.share_pct.toFixed(1)}%</span>
                      </div>
                    </div>
                    <div className="h-1.5 bg-[#0f1117] rounded-full overflow-hidden">
                      <div
                        className="h-full bg-gradient-to-r from-indigo-500 to-purple-500"
                        style={{ width: `${Math.min(p.share_pct, 100)}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* By source/campaign */}
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-[#2a2f3e]">
                <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
                  Por fonte / campanha
                </h3>
              </div>
              <table className="w-full text-sm">
                <thead className="bg-[#0f1117] text-xs text-slate-500 uppercase tracking-wider">
                  <tr>
                    <th className="text-left px-5 py-2.5">Fonte / Médio</th>
                    <th className="text-left px-5 py-2.5">Campanha</th>
                    <th className="text-right px-5 py-2.5">Conversões</th>
                    <th className="text-right px-5 py-2.5">Receita atribuída</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_source.slice(0, 20).map((s, i) => (
                    <tr key={i} className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a]">
                      <td className="px-5 py-2.5">
                        <span className="text-slate-200">{s.source}</span>
                        {s.medium && <span className="text-slate-500 text-xs ml-2">/ {s.medium}</span>}
                      </td>
                      <td className="px-5 py-2.5 text-slate-400 text-xs">{s.campaign || '—'}</td>
                      <td className="px-5 py-2.5 text-right text-slate-300">{s.conversions.toFixed(2)}</td>
                      <td className="px-5 py-2.5 text-right text-emerald-400 font-medium">{fmtBRL(s.revenue)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="text-xs text-slate-500 leading-relaxed bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-4">
              <strong className="text-slate-400">Como ler:</strong> Cada plataforma se atribui crédito sozinha (Meta diz X, Google diz Y).
              A atribuição unificada lê o histórico multi-toque do visitor (capturado pelo nosso pixel) e distribui
              crédito proporcional segundo o modelo escolhido. Diferença entre o relatório nativo de cada plataforma
              e este painel revela o overclaim por overlap entre canais.
            </div>
          </>
        )}
      </div>
    </div>
  )
}
