'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { RefreshCw, Loader2, TrendingUp, Info } from 'lucide-react'
import { useDatePeriod } from '@/lib/use-date-range'
import { PeriodPicker } from '@/components/PeriodPicker'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ──────────────────────────────────────────────────────────────────────

const MODELS = [
  { value: 'last_click',     label: 'Último clique',  desc: '100% para a última fonte antes da compra' },
  { value: 'first_click',    label: 'Primeiro clique', desc: '100% para a fonte que trouxe o cliente' },
  { value: 'linear',         label: 'Linear',          desc: 'Crédito igual entre todos os toques' },
  { value: 'time_decay',     label: 'Time decay',      desc: 'Peso maior para toques mais recentes (half-life 7d)' },
  { value: 'position_based', label: 'Posição (U)',     desc: '40% primeiro + 40% último + 20% restante' },
] as const

type Model    = typeof MODELS[number]['value']
type DatePreset = '1d' | '7d' | '30d' | '90d' | 'custom'

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
  start:         string | null
  end:           string | null
  total_revenue: number
  by_platform:   PlatformRow[]
  by_source:     SourceRow[]
  total_orders?:      number
  multitouch_orders?: number
  multitouch_pct?:    number
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmtBRL = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n)

const PLATFORM_COLORS: Record<string, string> = {
  meta:      'bg-blue-500/20 text-blue-300 border-blue-500/30',
  google:    'bg-yellow-500/20 text-yellow-300 border-yellow-500/30',
  tiktok:    'bg-pink-500/20 text-pink-300 border-pink-500/30',
  pinterest: 'bg-red-500/20 text-red-300 border-red-500/30',
  organic:   'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
  direct:    'bg-slate-500/20 text-slate-300 border-slate-500/30',
  email:     'bg-purple-500/20 text-purple-300 border-purple-500/30',
  shopify:   'bg-green-500/20 text-green-300 border-green-500/30',
  pos:       'bg-orange-500/20 text-orange-300 border-orange-500/30',
  other:     'bg-slate-500/20 text-slate-400 border-slate-500/30',
}

const PLATFORM_LABEL: Record<string, string> = {
  pos: 'Loja Física',
}

const SOURCE_BADGE: Record<string, string> = {
  facebook:  'bg-blue-500/10 text-blue-400',
  instagram: 'bg-pink-500/10 text-pink-400',
  meta:      'bg-blue-500/10 text-blue-400',
  google:    'bg-yellow-500/10 text-yellow-300',
  tiktok:    'bg-pink-500/10 text-pink-400',
  email:     'bg-purple-500/10 text-purple-400',
  organic:   'bg-emerald-500/10 text-emerald-400',
  pos:       'bg-orange-500/10 text-orange-400',
}

function isNumericId(s: string | null): boolean {
  return !!s && /^\d{8,}$/.test(s.trim())
}

function periodLabel(preset: DatePreset, fromDate: string, toDate: string): string {
  if (preset === '1d') return 'Ontem'
  if (preset === 'custom' && fromDate && toDate) return `${fromDate} → ${toDate}`
  return `Últimos ${preset === '7d' ? 7 : preset === '30d' ? 30 : 90} dias`
}

function buildQuery(preset: DatePreset, days: number, fromDate: string, toDate: string): string {
  if (preset === '1d') {
    const d = new Date(); d.setDate(d.getDate() - 1)
    const s = d.toISOString().split('T')[0]
    return `start=${s}&end=${s}`
  }
  if (preset === 'custom' && fromDate && toDate) {
    return `start=${fromDate}&end=${toDate}`
  }
  return `days=${days}`
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function AttributionPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [model,      setModel]      = useState<Model>('last_click')
  const { period, from, to, setPreset, setCustom } = useDatePeriod()
  const [data,       setData]       = useState<Summary | null>(null)
  const [loading,    setLoading]    = useState(true)
  const [recomputing, setRecomputing] = useState(false)

  const days = period === '7d' ? 7 : period === '90d' ? 90 : period === '1d' ? 1 : 30

  const load = useCallback(async (p: DatePreset, m: Model, fd: string, td: string) => {
    if (p === 'custom' && (!fd || !td)) return
    setLoading(true)
    try {
      const q  = buildQuery(p, p === '7d' ? 7 : p === '30d' ? 30 : p === '90d' ? 90 : 1, fd, td)
      const res = await fetch(`${API_URL}/attribution/${clientId}/summary?model=${m}&${q}`)
      if (res.ok) setData(await res.json())
    } finally {
      setLoading(false)
    }
  }, [clientId])

  useEffect(() => { load(period, model, from, to) }, [period, from, to, model, load])

  async function recompute() {
    setRecomputing(true)
    try {
      await fetch(`${API_URL}/attribution/${clientId}/recompute?days=${days}`, { method: 'POST' })
      await new Promise(r => setTimeout(r, 3000))
      await load(period, model, from, to)
    } finally {
      setRecomputing(false)
    }
  }

  const activeModel = MODELS.find(m => m.value === model)

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <TrendingUp size={16} className="text-indigo-400" />
          <div>
            <h1 className="text-lg font-bold text-white">Atribuição unificada</h1>
            <p className="text-xs text-slate-500 mt-0.5">
              Resolve overlap entre canais — crédito distribuído pelo historial multi-toque do visitor
            </p>
          </div>
        </div>
        <button
          onClick={recompute}
          disabled={recomputing}
          className="flex items-center gap-2 text-xs bg-[#1a1f2e] hover:bg-[#252b3b] border border-[#2a2f3e] text-slate-300 px-3 py-2 rounded-lg disabled:opacity-50"
        >
          {recomputing
            ? <><Loader2 size={12} className="animate-spin" /> Recalculando…</>
            : <><RefreshCw size={12} /> Recalcular</>}
        </button>
      </div>

      <div className="p-6 space-y-5 max-w-6xl">
        {/* Controls */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Model selector */}
          <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
            {MODELS.map(m => (
              <button
                key={m.value}
                onClick={() => setModel(m.value)}
                title={m.desc}
                className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                  model === m.value ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>

          {/* Date presets */}
          <PeriodPicker period={period} from={from} to={to} onPreset={setPreset} onCustom={setCustom} />

          <p className="text-xs text-slate-500 ml-auto flex items-center gap-1.5">
            <Info size={12} />
            {activeModel?.desc}
          </p>
        </div>

        {loading && !data ? (
          <div className="flex items-center gap-2 text-slate-500 text-sm py-12">
            <Loader2 size={16} className="animate-spin" /> Carregando…
          </div>
        ) : !data || data.total_revenue === 0 ? (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-8 text-center">
            <p className="text-slate-400">Sem dados de atribuição para o período.</p>
            <p className="text-xs text-slate-500 mt-1">Clique em &quot;Recalcular&quot; para processar pedidos existentes.</p>
          </div>
        ) : (
          <>
            {/* Total banner */}
            <div className="bg-gradient-to-br from-indigo-600/10 to-purple-600/10 border border-indigo-500/20 rounded-xl p-5 flex items-center justify-between">
              <div>
                <p className="text-xs text-slate-400 uppercase tracking-wider">
                  Receita atribuída — {activeModel?.label}
                </p>
                <p className="text-3xl font-bold text-white mt-1">{fmtBRL(data.total_revenue)}</p>
                <p className="text-xs text-slate-500 mt-1">{periodLabel(period, from, to)}</p>
              </div>
              <div className="text-right">
                <p className="text-xs text-slate-500">Canais ativos</p>
                <p className="text-2xl font-bold text-indigo-300">{data.by_platform.length}</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  {data.by_platform.reduce((s, p) => s + p.orders, 0)} pedidos
                </p>
              </div>
            </div>

            {/* Aviso: modelos coincidem quando a jornada tem um só toque */}
            {data.multitouch_pct != null && data.multitouch_pct === 0 && (data.total_orders ?? 0) > 0 && (
              <div className="flex items-start gap-2 text-xs bg-amber-500/5 border border-amber-500/20 rounded-lg px-4 py-3">
                <Info size={14} className="shrink-0 text-amber-400 mt-0.5" />
                <span className="text-amber-200/80">
                  <span className="font-medium text-amber-300">Os 5 modelos mostram o mesmo resultado neste período.</span>{' '}
                  Todos os {data.total_orders} pedidos têm jornada de <span className="font-medium">toque único</span> (uma só origem capturada),
                  então 100% do crédito vai para esse toque em qualquer modelo — não é um erro. Os modelos passam a divergir conforme
                  os clientes forem rastreados em múltiplas visitas com origens diferentes (multi-toque).
                </span>
              </div>
            )}
            {data.multitouch_pct != null && data.multitouch_pct > 0 && (
              <p className="text-xs text-slate-500">
                {data.multitouch_pct}% dos pedidos têm jornada multi-toque — onde os modelos divergem.
              </p>
            )}

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
                          {PLATFORM_LABEL[p.platform] ?? p.platform}
                        </span>
                        <span className="text-slate-500 text-xs">
                          {p.orders} pedido{p.orders !== 1 ? 's' : ''} · {p.conversions.toFixed(1)} créditos
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
                    <th className="text-left px-5 py-2.5">Fonte</th>
                    <th className="text-left px-5 py-2.5">Campanha</th>
                    <th className="text-right px-5 py-2.5">Créditos</th>
                    <th className="text-right px-5 py-2.5">Receita atribuída</th>
                    <th className="text-right px-5 py-2.5">% total</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_source.slice(0, 25).map((s, i) => (
                    <tr key={i} className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a]">
                      <td className="px-5 py-2.5">
                        <span className={`text-xs px-2 py-0.5 rounded font-medium ${SOURCE_BADGE[s.source.toLowerCase()] || 'bg-slate-500/10 text-slate-400'}`}>
                          {s.source === 'pos' ? 'Loja Física' : s.source}
                        </span>
                        {s.medium && s.medium !== 'in_store' && <span className="text-slate-500 text-xs ml-2">/ {s.medium}</span>}
                      </td>
                      <td className="px-5 py-2.5 text-xs max-w-[220px]">
                        {isNumericId(s.campaign) ? (
                          <p className="truncate font-mono text-amber-400/70" title="ID numérico — clique em Recalcular para resolver o nome">
                            {s.campaign}
                          </p>
                        ) : (
                          <p className="truncate text-slate-400">{s.campaign || <span className="text-slate-600">—</span>}</p>
                        )}
                      </td>
                      <td className="px-5 py-2.5 text-right text-slate-300 tabular-nums">{s.conversions.toFixed(2)}</td>
                      <td className="px-5 py-2.5 text-right text-emerald-400 font-medium tabular-nums">{fmtBRL(s.revenue)}</td>
                      <td className="px-5 py-2.5 text-right text-slate-500 text-xs tabular-nums">
                        {data.total_revenue > 0 ? ((s.revenue / data.total_revenue) * 100).toFixed(1) : '0'}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Explanation card */}
            <div className="text-xs text-slate-500 leading-relaxed bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-4">
              <strong className="text-slate-400">Como ler:</strong>{' '}
              Cada plataforma se atribui 100% do crédito sozinha — Meta diz X, Google diz Y, gerando
              overlap. Esta tabela lê o histórico multi-toque do visitor capturado pelo nosso pixel e
              distribui crédito fracionado pelo modelo <strong className="text-slate-400">{activeModel?.label}</strong>.
              A diferença entre o painel nativo de cada plataforma e esta receita atribuída revela o
              overclaim por sobreposição de canais.
            </div>
          </>
        )}
      </div>
    </div>
  )
}
