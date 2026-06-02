'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import {
  Loader2, RefreshCw, ChevronDown, ChevronRight,
  TrendingUp, TrendingDown, CheckCircle, AlertCircle, Zap,
} from 'lucide-react'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { useDatePeriod, periodToQuery } from '@/lib/use-date-range'
import { PeriodPicker } from '@/components/PeriodPicker'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Totals {
  orders: number; revenue: number
  spend: number; has_spend: boolean; roas: number | null
  impressions: number; clicks: number
  total_sent: number; sent_coverage_pct: number | null
  gclid: number; gbraid: number; enhanced_only: number; not_sent: number
  gclid_pct: number | null; cpa: number | null; avg_ticket: number | null
}

interface CampaignRow {
  campaign: string; orders: number; revenue: number
  gclid: number; enhanced: number; cpa: number | null; revenue_delta: number | null
  top_products: Array<{ name: string; sku: string | null; units: number; revenue: number }>
}

interface DayRow {
  date: string; orders: number; revenue: number; gclid: number; enhanced: number
  spend: number; roas: number | null
}

interface OverviewData {
  days: number; start: string; end: string; prev_start: string; prev_end: string
  has_creds: boolean; customer_id: string | null
  totals: Totals; prev_totals: { orders: number; revenue: number; gclid: number }
  deltas: Record<string, number | null>
  campaigns: CampaignRow[]
  daily: DayRow[]
  funnel: Record<string, number | null>
  funnel_available: boolean
}


// ── Helpers ───────────────────────────────────────────────────────────────────

const fmt   = (n: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(n)
const fmtD2 = (n: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 2 }).format(n)
const fmtN  = (n: number) => new Intl.NumberFormat('pt-BR').format(n)
const fmtDt = (s: string) => s.slice(8, 10) + '/' + s.slice(5, 7)

function Delta({ v, invert }: { v: number | null; invert?: boolean }) {
  if (v === null || v === undefined) return <span className="text-slate-600 text-xs">—</span>
  const good = invert ? v <= 0 : v >= 0
  const Icon = v >= 0 ? TrendingUp : TrendingDown
  return (
    <span className={`inline-flex items-center gap-0.5 text-xs font-medium ${good ? 'text-emerald-400' : 'text-red-400'}`}>
      <Icon size={10} />
      {v >= 0 ? '+' : ''}{v.toFixed(1)}%
    </span>
  )
}

function KpiCard({ label, value, delta, sub, accent, invertDelta }: {
  label: string; value: string; delta?: number | null; sub?: string
  accent?: 'emerald' | 'teal' | 'blue' | 'yellow' | 'orange' | 'rose'; invertDelta?: boolean
}) {
  const colorMap: Record<string, string> = { emerald: 'text-emerald-400', teal: 'text-teal-400', blue: 'text-blue-400', yellow: 'text-yellow-400', orange: 'text-orange-400', rose: 'text-rose-400' }
  const c = (accent ? colorMap[accent] : null) ?? 'text-white'
  return (
    <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl px-4 py-3">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className={`text-lg font-bold ${c}`}>{value}</p>
      <div className="flex items-center gap-2 mt-0.5">
        {delta !== undefined && <Delta v={delta ?? null} invert={invertDelta} />}
        {sub && <span className="text-xs text-slate-600">{sub}</span>}
      </div>
    </div>
  )
}

function MatchBar({ label, count, total, color }: { label: string; count: number; total: number; color: string }) {
  const pct = total > 0 ? (count / total * 100) : 0
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-slate-400">{label}</span>
        <span className="text-slate-300 tabular-nums">{fmtN(count)} <span className="text-slate-600">({pct.toFixed(0)}%)</span></span>
      </div>
      <div className="h-2 bg-[#0f1117] rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
    </div>
  )
}

// ── Campaign Row ──────────────────────────────────────────────────────────────

function CampaignRowComp({ row }: { row: CampaignRow }) {
  const [open, setOpen] = useState(false)
  const total = row.gclid + row.enhanced
  return (
    <>
      <tr className="border-t border-[#2a2f3e] hover:bg-[#252a3a] cursor-pointer" onClick={() => setOpen(v => !v)}>
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            {open ? <ChevronDown size={13} className="text-slate-500" /> : <ChevronRight size={13} className="text-slate-500" />}
            <p className="text-sm text-white font-medium line-clamp-1">{row.campaign}</p>
          </div>
        </td>
        <td className="px-3 py-3 text-right text-sm text-emerald-400 font-semibold tabular-nums">{row.orders}</td>
        <td className="px-3 py-3 text-right text-sm text-emerald-400 font-semibold tabular-nums">{fmt(row.revenue)}</td>
        <td className="px-3 py-3 text-right text-sm text-slate-400 tabular-nums">
          {row.cpa != null ? fmtD2(row.cpa) : <span className="text-slate-600">—</span>}
        </td>
        <td className="px-3 py-3">
          {total > 0 && (
            <div className="flex items-center gap-2 text-xs">
              {row.gclid > 0 && (
                <span className="bg-blue-500/15 text-blue-300 px-1.5 py-0.5 rounded border border-blue-500/25">
                  {row.gclid} gclid
                </span>
              )}
              {row.enhanced > 0 && (
                <span className="bg-indigo-500/15 text-indigo-300 px-1.5 py-0.5 rounded border border-indigo-500/25">
                  {row.enhanced} enhanced
                </span>
              )}
            </div>
          )}
        </td>
        <td className="px-3 py-3 text-right"><Delta v={row.revenue_delta ?? null} /></td>
      </tr>
      {open && row.top_products.length > 0 && (
        <tr className="border-t border-[#1a1f2e]">
          <td colSpan={6} className="px-0 py-0">
            <div className="bg-[#0f1117] border-b border-[#2a2f3e]">
              <p className="px-5 pt-2.5 pb-1 text-xs uppercase tracking-wider text-slate-600 font-medium">
                Produtos vendidos por esta campanha
              </p>
              <table className="w-full text-xs">
                <tbody>
                  {row.top_products.map((p, i) => (
                    <tr key={i} className="border-t border-[#1a1f2e]">
                      <td className="pl-5 pr-4 py-2 text-slate-300 max-w-xs">
                        <p className="truncate">{p.name}</p>
                        {p.sku && <p className="text-slate-600 font-mono">{p.sku}</p>}
                      </td>
                      <td className="px-4 py-2 text-right text-slate-500 tabular-nums">{p.units} un.</td>
                      <td className="px-5 py-2 text-right text-emerald-400 font-semibold tabular-nums">{fmt(p.revenue)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function GoogleAdsPage() {
  const params  = useParams()
  const pixelId = params.clientId as string

  const { period, from, to, setPreset, setCustom } = useDatePeriod()
  const [data,    setData]    = useState<OverviewData | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async (q: string) => {
    setLoading(true)
    try {
      const res  = await fetch(`${API_URL}/google-ads/${pixelId}/overview?${q}`)
      if (res.ok) setData(await res.json())
    } catch (_) {}
    setLoading(false)
  }, [pixelId])

  useEffect(() => {
    if (period === 'custom' && (!from || !to)) return
    load(periodToQuery(period, from, to))
  }, [period, from, to, load])

  const t   = data?.totals
  const dlt = data?.deltas || {}
  const funnel = data?.funnel || {}
  const fTop   = funnel.pageview || 1

  const chartData = (data?.daily || []).map(d => ({
    date:        fmtDt(d.date),
    Receita:     d.revenue,
    Investimento: d.spend,
    Compras:     d.orders,
    gclid:       d.gclid,
  }))

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-lg font-bold text-white">Google Ads</h1>
          {data && (
            <p className="text-xs text-slate-500 mt-0.5">
              {fmtDt(data.start)} → {fmtDt(data.end)} · vs {fmtDt(data.prev_start)} → {fmtDt(data.prev_end)}
              {data.customer_id && <span className="ml-2 text-slate-600">ID: {data.customer_id}</span>}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
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

          {/* KPI Strip */}
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-5 gap-3">
            <KpiCard label="Investimento"     value={t?.has_spend ? fmt(t.spend) : '—'} delta={dlt.spend} invertDelta accent="rose"
              sub={t && !t.has_spend ? 'sem sync de spend' : undefined} />
            <KpiCard label="ROAS"             value={t?.roas != null ? `${t.roas.toFixed(2)}x` : '—'} delta={dlt.roas} accent="emerald" />
            <KpiCard label="Compras Google"   value={t ? String(t.orders) : '—'}       delta={dlt.orders}   accent="emerald" />
            <KpiCard label="Receita Google"   value={t ? fmt(t.revenue) : '—'}          delta={dlt.revenue}  accent="emerald" />
            <KpiCard label="CPA"              value={t?.cpa != null ? fmtD2(t.cpa) : '—'} invertDelta />
            <KpiCard label="Ticket Médio"     value={t?.avg_ticket != null ? fmt(t.avg_ticket) : '—'} accent="orange" />
            <KpiCard label="Conversões env."  value={t ? String(t.total_sent) : '—'}    delta={dlt.total_sent} accent="blue"
              sub={t?.sent_coverage_pct != null ? `${t.sent_coverage_pct}% dos pedidos` : undefined} />
            <KpiCard label="gclid (clique)"   value={t ? String(t.gclid) : '—'}        delta={dlt.gclid}    accent="yellow"
              sub={t?.gclid_pct != null ? `${t.gclid_pct}% dos enviados` : undefined} />
            <KpiCard label="Enhanced"         value={t ? String(t.enhanced_only) : '—'}  accent="teal" />
            <KpiCard label="Não enviados"      value={t ? String(t.not_sent) : '—'}      invertDelta />
          </div>

          {/* Charts + Match Types */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

            {/* Daily Chart */}
            <div className="lg:col-span-2 bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
              <h2 className="text-sm font-semibold text-slate-300 mb-4">Receita × Investimento × Compras por dia</h2>
              <ResponsiveContainer width="100%" height={200}>
                <ComposedChart data={chartData} margin={{ top: 0, right: 30, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2f3e" />
                  <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 10 }} />
                  <YAxis yAxisId="left"  tick={{ fill: '#64748b', fontSize: 10 }} tickFormatter={v => `R$${(v/1000).toFixed(0)}k`} />
                  <YAxis yAxisId="right" orientation="right" tick={{ fill: '#64748b', fontSize: 10 }} />
                  <Tooltip contentStyle={{ background: '#1a1f2e', border: '1px solid #2a2f3e', borderRadius: 8, fontSize: 11 }}
                    formatter={(v: any, name: any) => name === 'Compras' ? [v, name] : [fmt(Number(v)), name]} />
                  <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
                  <Bar yAxisId="left"  dataKey="Receita" fill="#34d399" opacity={0.7} radius={[2,2,0,0]} />
                  <Bar yAxisId="left"  dataKey="Investimento" fill="#fb7185" opacity={0.7} radius={[2,2,0,0]} />
                  <Line yAxisId="right" dataKey="Compras" stroke="#6366f1" strokeWidth={2} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>

            {/* Match Type Breakdown */}
            <div className="space-y-4">
              {/* Coverage */}
              <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
                <h2 className="text-sm font-semibold text-slate-300 mb-4">Cobertura de conversão</h2>
                {t && (
                  <div className="space-y-3">
                    <MatchBar label="gclid (clique direto)" count={t.gclid} total={t.total_sent} color="bg-yellow-500" />
                    <MatchBar label="gbraid (app/redirect)"  count={t.gbraid} total={t.total_sent} color="bg-orange-500" />
                    <MatchBar label="Enhanced (email/phone)" count={t.enhanced_only} total={t.total_sent} color="bg-indigo-500" />
                    <div className="pt-2 border-t border-[#2a2f3e]">
                      <div className="flex justify-between text-xs">
                        <span className="text-slate-400">Total enviados</span>
                        <span className="text-white font-semibold tabular-nums">{fmtN(t.total_sent)}</span>
                      </div>
                      {t.sent_coverage_pct != null && (
                        <p className="text-xs text-slate-500 mt-1">
                          {t.sent_coverage_pct}% de todos os pedidos enviados ao Google
                        </p>
                      )}
                    </div>
                  </div>
                )}
              </div>

              {/* Funnel */}
              <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
                <h2 className="text-sm font-semibold text-slate-300 mb-3">Funil (site)</h2>
                {data && !data.funnel_available ? (
                  <div className="space-y-2 text-xs">
                    <p className="text-slate-500">
                      Pageviews · carrinho · checkout indisponíveis em períodos longos. Selecione <span className="text-slate-300 font-medium">Ontem</span> ou <span className="text-slate-300 font-medium">7d</span> para ver o funil do site.
                    </p>
                    <div className="flex justify-between pt-1 border-t border-[#2a2f3e]">
                      <span className="text-slate-400">Compras Google</span>
                      <span className="text-slate-300 tabular-nums">{fmtN(Number(funnel.purchases || 0))}</span>
                    </div>
                  </div>
                ) : (
                <div className="space-y-2 text-xs">
                  {[
                    { label: 'Pageviews',   key: 'pageview' },
                    { label: 'Carrinho',    key: 'add_to_cart' },
                    { label: 'Checkout',    key: 'begin_checkout' },
                    { label: 'Compras Google', key: 'purchases' },
                  ].map(({ label, key }) => {
                    const count = Number(funnel[key] || 0)
                    const pct   = fTop > 0 ? (count / fTop * 100) : 0
                    return (
                      <div key={key}>
                        <div className="flex justify-between mb-0.5">
                          <span className="text-slate-400">{label}</span>
                          <span className="text-slate-300 tabular-nums">{fmtN(count)}</span>
                        </div>
                        <div className="h-1.5 bg-[#0f1117] rounded-full overflow-hidden">
                          <div className="h-full bg-emerald-500/60 rounded-full" style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    )
                  })}
                </div>
                )}
              </div>
            </div>
          </div>

          {/* Campaign Table */}
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-slate-300">Campanhas Google</h2>
                <p className="text-xs text-slate-500 mt-0.5">
                  Pedidos com utm_source=google · Expanda para ver produtos vendidos
                </p>
              </div>
              {loading && <Loader2 size={14} className="animate-spin text-slate-500" />}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#2a2f3e] bg-[#0f1117]">
                    {['Campanha', 'Compras', 'Receita', 'CPA', 'Match type', '% Δ Receita'].map(h => (
                      <th key={h} className={`px-3 py-2.5 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap ${h === 'Campanha' ? 'text-left px-4' : 'text-right'}`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(data?.campaigns || []).length === 0 ? (
                    <tr><td colSpan={6} className="py-10 text-center text-slate-500 text-sm">Nenhuma campanha Google no período</td></tr>
                  ) : (
                    (data?.campaigns || []).map(row => <CampaignRowComp key={row.campaign} row={row} />)
                  )}
                </tbody>
                {t && t.orders > 0 && (
                  <tfoot className="border-t-2 border-[#2a2f3e] bg-[#0f1117]">
                    <tr>
                      <td className="px-4 py-3 text-xs font-semibold text-slate-400">Total Google</td>
                      <td className="px-3 py-3 text-right text-sm font-bold text-emerald-400 tabular-nums">{t.orders}</td>
                      <td className="px-3 py-3 text-right text-sm font-bold text-emerald-400 tabular-nums">{fmt(t.revenue)}</td>
                      <td className="px-3 py-3 text-right text-sm text-slate-300 tabular-nums">{t.cpa != null ? fmtD2(t.cpa) : '—'}</td>
                      <td className="px-3 py-3" />
                      <td className="px-3 py-3 text-right"><Delta v={dlt.revenue ?? null} /></td>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          </div>

          {/* Explanation */}
          <div className="bg-blue-500/5 border border-blue-500/20 rounded-xl p-5 space-y-3">
            <p className="text-sm font-semibold text-blue-400">Como interpretar os dados</p>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs text-slate-400 leading-relaxed">
              <div>
                <p className="text-slate-300 font-medium mb-1">Compras Google vs Conversões enviadas</p>
                <p>"Compras Google" são pedidos com utm_source=google (tráfego pago clicou no anúncio e comprou). "Conversões enviadas" é o total de pedidos que mandamos ao Google Ads para treinar o Smart Bidding — inclui vendas de outros canais para melhorar o modelo.</p>
              </div>
              <div>
                <p className="text-slate-300 font-medium mb-1">gclid vs Enhanced</p>
                <p>gclid = clique direto no anúncio Google → atribuição 100% certa. Enhanced = sem clique Google mas enviamos email/telefone hasheado para o Google cruzar. Quanto mais gclid, melhor o Smart Bidding aprende.</p>
              </div>
              <div>
                <p className="text-slate-300 font-medium mb-1">PIX e compras sem utm</p>
                <p>Compras por PIX (cliente paga no app do banco e não volta) são capturadas pelo nosso webhook e enviadas como Enhanced Conversion. O Google nativo perderia essas conversões — esse é o valor do nosso rastreamento.</p>
              </div>
            </div>
          </div>

        </div>
      )}
    </div>
  )
}
