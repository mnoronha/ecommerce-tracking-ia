'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import {
  Loader2, RefreshCw, TrendingUp, TrendingDown,
  CheckCircle2, AlertCircle, ChevronDown, ChevronRight,
} from 'lucide-react'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { useDatePeriod, periodToQuery } from '@/lib/use-date-range'
import { PeriodPicker } from '@/components/PeriodPicker'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Totals  { orders: number; revenue: number; avg_ticket: number; spend: number; roas: number | null; cpa: number | null }
interface DayRow  { date: string; orders: number; revenue: number }
interface CampRow { campaign: string; orders: number; revenue: number; avg_ticket: number }
interface Capi    { total: number; sent: number; failed: number; sent_pct: number }

interface OverviewData {
  has_data: boolean; has_spend: boolean
  start: string; end: string; prev_start: string; prev_end: string
  tag_id: string
  totals: Totals; prev_totals: Totals; deltas: Record<string, number | null>
  daily: DayRow[]; campaigns: CampRow[]
  capi: Capi
  funnel: Record<string, number>
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmt    = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(n)
const fmtDec = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 2 }).format(n)
const fmtN   = (n: number) => new Intl.NumberFormat('pt-BR').format(n)
const fmtD   = (s: string) => s.slice(8, 10) + '/' + s.slice(5, 7)

function Delta({ v }: { v: number | null }) {
  if (v === null || v === undefined) return <span className="text-slate-600 text-xs">—</span>
  const pos  = v >= 0
  const Icon = pos ? TrendingUp : TrendingDown
  return (
    <span className={`inline-flex items-center gap-0.5 text-xs font-medium ${pos ? 'text-emerald-400' : 'text-red-400'}`}>
      <Icon size={10} />
      {pos ? '+' : ''}{v.toFixed(1)}%
    </span>
  )
}

function KpiCard({ label, value, delta, sub, accent }: {
  label: string; value: string; delta?: number | null; sub?: string
  accent?: 'rose' | 'red' | 'pink' | 'emerald' | 'teal' | 'orange'
}) {
  const c = {
    rose: 'text-rose-400', red: 'text-red-400', pink: 'text-pink-400',
    emerald: 'text-emerald-400', teal: 'text-teal-400', orange: 'text-orange-400',
  }[accent ?? ''] ?? 'text-white'
  return (
    <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl px-4 py-3">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className={`text-lg font-bold ${c}`}>{value}</p>
      <div className="flex items-center gap-2 mt-0.5">
        {delta !== undefined && <Delta v={delta ?? null} />}
        {sub && <span className="text-xs text-slate-600">{sub}</span>}
      </div>
    </div>
  )
}

function FunnelBar({ label, count, pct, prev }: {
  label: string; count: number; pct: number; prev?: number
}) {
  const delta = prev && prev > 0 ? ((count - prev) / prev * 100) : null
  return (
    <div>
      <div className="flex items-center justify-between text-xs mb-1">
        <span className="text-slate-300">{label}</span>
        <div className="flex items-center gap-2">
          <span className="text-slate-400 tabular-nums">{fmtN(count)}</span>
          <Delta v={delta} />
        </div>
      </div>
      <div className="h-5 bg-[#0f1117] rounded overflow-hidden">
        <div className="h-full bg-rose-500/70 rounded transition-all duration-700"
          style={{ width: `${Math.max(pct, count > 0 ? 2 : 0)}%` }} />
      </div>
      <p className="text-xs text-slate-600 mt-0.5">{pct.toFixed(1)}% do topo</p>
    </div>
  )
}

function CampRowComp({ row }: { row: CampRow }) {
  return (
    <tr className="border-t border-[#2a2f3e] hover:bg-[#252a3a]">
      <td className="px-4 py-3">
        <span className="text-sm text-white font-medium line-clamp-1">{row.campaign}</span>
      </td>
      <td className="px-3 py-3 text-right text-sm text-emerald-400 font-semibold tabular-nums">{row.orders}</td>
      <td className="px-3 py-3 text-right text-sm text-emerald-400 font-semibold tabular-nums">{fmt(row.revenue)}</td>
      <td className="px-3 py-3 text-right text-sm text-slate-400 tabular-nums">{fmtDec(row.avg_ticket)}</td>
    </tr>
  )
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function PinterestAdsPage() {
  const params  = useParams()
  const pixelId = params.clientId as string

  const { period, from, to, setPreset, setCustom } = useDatePeriod()
  const [data,    setData]    = useState<OverviewData | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async (q: string) => {
    setLoading(true)
    try {
      const res = await fetch(`${API_URL}/pinterest-ads/${pixelId}/overview?${q}`)
      if (res.ok) setData(await res.json())
    } catch (_) {}
    setLoading(false)
  }, [pixelId])

  useEffect(() => {
    if (period === 'custom' && (!from || !to)) return
    load(periodToQuery(period, from, to))
  }, [period, from, to, load])

  const t    = data?.totals
  const dlt  = data?.deltas || {}
  const capi = data?.capi

  const chartData = (data?.daily || []).map(d => ({
    date: fmtD(d.date),
    Pedidos: d.orders,
    Receita: d.revenue,
  }))

  const funnel = data?.funnel || {}

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between flex-wrap gap-3">
        <div>
          <div className="flex items-center gap-2">
            <div className="w-5 h-5 rounded bg-[#e60023] flex items-center justify-center shrink-0">
              <span className="text-white text-[10px] font-black">P</span>
            </div>
            <h1 className="text-lg font-bold text-white">Pinterest Ads</h1>
          </div>
          {data && (
            <p className="text-xs text-slate-500 mt-0.5">
              {fmtD(data.start)} → {fmtD(data.end)} · vs {fmtD(data.prev_start)} → {fmtD(data.prev_end)}
              {data.tag_id && <span className="ml-2 text-slate-600">· tag {data.tag_id}</span>}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <PeriodPicker period={period} from={from} to={to} onPreset={setPreset} onCustom={setCustom} />
          <button onClick={() => load(periodToQuery(period, from, to))} className="text-slate-500 hover:text-white transition-colors">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {loading && !data ? (
        <div className="flex items-center gap-2 justify-center py-24 text-slate-500">
          <Loader2 size={18} className="animate-spin" /> Carregando…
        </div>
      ) : (
        <div className="p-6 space-y-6 max-w-[1400px]">

          {/* No UTM data notice */}
          {data && !data.has_data && (
            <div className="bg-rose-500/5 border border-rose-500/20 rounded-xl p-4 text-sm text-slate-400">
              <p className="text-rose-400 font-semibold mb-1">Sem pedidos atribuídos ao Pinterest no período</p>
              <p>Pedidos aparecem aqui quando <code className="text-slate-300 bg-[#0f1117] px-1 rounded">utm_source=pinterest</code> está presente nos links dos anúncios. Verifique os parâmetros UTM nas campanhas.</p>
            </div>
          )}

          {/* KPI Strip */}
          <div className={`grid gap-3 ${data?.has_spend ? 'grid-cols-2 sm:grid-cols-4 lg:grid-cols-7' : 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-5'}`}>
            <KpiCard label="Pedidos Pinterest" value={t ? String(t.orders) : '—'}        delta={dlt.orders}     accent="rose" />
            <KpiCard label="Receita"           value={t ? fmt(t.revenue) : '—'}           delta={dlt.revenue}    accent="emerald" />
            <KpiCard label="Ticket Médio"      value={t ? fmtDec(t.avg_ticket) : '—'}     delta={dlt.avg_ticket} accent="teal" />
            {data?.has_spend && <>
              <KpiCard label="Investimento"    value={t ? fmt(t.spend) : '—'}             delta={dlt.spend}      accent="red" />
              <KpiCard label="ROAS"            value={t?.roas != null ? `${t.roas.toFixed(2)}x` : '—'} delta={dlt.roas} accent={t?.roas != null && t.roas >= 3 ? 'emerald' : t?.roas != null && t.roas >= 1.5 ? 'teal' : 'rose'} />
              <KpiCard label="CPA"             value={t?.cpa != null ? fmtDec(t.cpa) : '—'} delta={dlt.cpa != null ? -(dlt.cpa) : null} />
            </>}
            <KpiCard
              label="Taxa CAPI"
              value={capi ? `${capi.sent_pct.toFixed(1)}%` : '—'}
              sub={capi?.failed ? `${capi.failed} erro${capi.failed > 1 ? 's' : ''}` : undefined}
              accent={capi && capi.sent_pct >= 95 ? 'emerald' : capi && capi.sent_pct >= 80 ? 'orange' : 'rose'}
            />
          </div>

          {/* Chart + Funnel */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className="lg:col-span-2 bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
              <h2 className="text-sm font-semibold text-slate-300 mb-4">Pedidos × Receita (atribuição UTM)</h2>
              {chartData.length === 0 ? (
                <div className="flex items-center justify-center h-48 text-slate-600 text-sm">Sem dados no período</div>
              ) : (
                <ResponsiveContainer width="100%" height={200}>
                  <ComposedChart data={chartData} margin={{ top: 0, right: 30, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#2a2f3e" />
                    <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 10 }} />
                    <YAxis yAxisId="left"  tick={{ fill: '#64748b', fontSize: 10 }} tickFormatter={v => `R$${(v/1000).toFixed(0)}k`} />
                    <YAxis yAxisId="right" orientation="right" tick={{ fill: '#64748b', fontSize: 10 }} />
                    <Tooltip
                      contentStyle={{ background: '#1a1f2e', border: '1px solid #2a2f3e', borderRadius: 8, fontSize: 11 }}
                      formatter={(v: any, name: any) => name === 'Pedidos' ? [v, name] : [fmt(Number(v)), name]}
                    />
                    <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
                    <Bar yAxisId="left"  dataKey="Receita" fill="#e11d48" opacity={0.7} radius={[2,2,0,0]} />
                    <Line yAxisId="right" dataKey="Pedidos" stroke="#10b981" strokeWidth={2} dot={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              )}
            </div>

            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
              <h2 className="text-sm font-semibold text-slate-300 mb-4">Funil do Site</h2>
              <div className="space-y-3">
                {(() => {
                  const base = Math.max(funnel.pageview || 0, 1)
                  return (
                    <>
                      <FunnelBar label="Pageviews"         count={funnel.pageview || 0}       pct={100} />
                      <FunnelBar label="Produto Visto"     count={funnel.view_product || 0}   pct={(funnel.view_product || 0) / base * 100} />
                      <FunnelBar label="Add ao Carrinho"   count={funnel.add_to_cart || 0}    pct={(funnel.add_to_cart || 0) / base * 100} />
                      <FunnelBar label="Checkout Iniciado" count={funnel.begin_checkout || 0} pct={(funnel.begin_checkout || 0) / base * 100} />
                      <FunnelBar label="Compras"           count={funnel.purchase || 0}       pct={(funnel.purchase || 0) / base * 100} />
                    </>
                  )
                })()}
              </div>
              <p className="mt-4 text-xs text-slate-600">Todos os visitantes do site, independente da origem.</p>
            </div>
          </div>

          {/* CAPI Health */}
          {capi && (
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
              <h2 className="text-sm font-semibold text-slate-300 mb-4">Saúde do Pinterest CAPI</h2>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <div className="text-center">
                  <p className="text-2xl font-bold text-white">{fmtN(capi.total)}</p>
                  <p className="text-xs text-slate-500 mt-0.5">Pedidos online no período</p>
                </div>
                <div className="text-center">
                  <p className="text-2xl font-bold text-emerald-400">{fmtN(capi.sent)}</p>
                  <p className="text-xs text-slate-500 mt-0.5">Enviados ao CAPI</p>
                </div>
                <div className="text-center">
                  <p className={`text-2xl font-bold ${capi.failed > 0 ? 'text-red-400' : 'text-slate-600'}`}>{capi.failed}</p>
                  <p className="text-xs text-slate-500 mt-0.5">Com erro</p>
                </div>
                <div className="text-center">
                  <p className={`text-2xl font-bold ${capi.sent_pct >= 95 ? 'text-emerald-400' : capi.sent_pct >= 80 ? 'text-yellow-400' : 'text-red-400'}`}>
                    {capi.sent_pct.toFixed(1)}%
                  </p>
                  <p className="text-xs text-slate-500 mt-0.5">Cobertura CAPI</p>
                </div>
              </div>
              {capi.total > 0 && (
                <div className="mt-4 h-3 bg-[#0f1117] rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-700 ${capi.sent_pct >= 95 ? 'bg-emerald-500' : capi.sent_pct >= 80 ? 'bg-yellow-500' : 'bg-red-500'}`}
                    style={{ width: `${capi.sent_pct}%` }}
                  />
                </div>
              )}
              <div className="mt-3 flex items-center gap-1.5 text-xs text-slate-500">
                {capi.sent_pct >= 95
                  ? <><CheckCircle2 size={12} className="text-emerald-400" /> CAPI saudável — otimização de lances ativa</>
                  : <><AlertCircle  size={12} className="text-yellow-400" /> Cobertura abaixo do ideal — verifique erros no Diagnóstico</>}
              </div>
            </div>
          )}

          {/* Campaign Table */}
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-[#2a2f3e]">
              <h2 className="text-sm font-semibold text-slate-300">Campanhas (utm_campaign)</h2>
              <p className="text-xs text-slate-500 mt-0.5">Pedidos atribuídos via parâmetros UTM — independente do que o Pinterest reporta</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#2a2f3e] bg-[#0f1117]">
                    {['Campanha', 'Pedidos', 'Receita', 'Ticket Médio'].map(h => (
                      <th key={h} className={`px-3 py-2.5 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap ${h === 'Campanha' ? 'text-left px-4' : 'text-right'}`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(data?.campaigns || []).length === 0 ? (
                    <tr><td colSpan={4} className="py-10 text-center text-slate-500 text-sm">
                      {data?.has_data === false ? 'Sem pedidos com utm_source=pinterest no período' : 'Carregando…'}
                    </td></tr>
                  ) : (
                    (data?.campaigns || []).map(row => <CampRowComp key={row.campaign} row={row} />)
                  )}
                </tbody>
                {t && t.orders > 0 && (
                  <tfoot className="border-t-2 border-[#2a2f3e] bg-[#0f1117]">
                    <tr>
                      <td className="px-4 py-3 text-xs font-semibold text-slate-400">Total</td>
                      <td className="px-3 py-3 text-right text-sm font-bold text-emerald-400 tabular-nums">{t.orders}</td>
                      <td className="px-3 py-3 text-right text-sm font-bold text-emerald-400 tabular-nums">{fmt(t.revenue)}</td>
                      <td className="px-3 py-3 text-right text-sm text-slate-300 tabular-nums">{fmtDec(t.avg_ticket)}</td>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          </div>

          {/* Spend note (only when no spend data) */}
          {!data?.has_spend && (
            <div className="bg-rose-500/5 border border-rose-500/20 rounded-xl p-5 text-xs text-slate-400 leading-relaxed">
              <p className="text-rose-400 font-semibold text-sm mb-2">Sem dados de investimento</p>
              <p>
                ROAS e CPA não estão disponíveis ainda. O spend sync do Pinterest Ads está ativo — ele sincronizará
                automaticamente quando a conta de anúncios estiver configurada (campos <strong className="text-slate-300">Pinterest Ad Account ID</strong> e
                token OAuth nas Configurações do cliente).
              </p>
            </div>
          )}

        </div>
      )}
    </div>
  )
}
