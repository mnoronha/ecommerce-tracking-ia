'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import {
  Loader2, RefreshCw, ChevronDown, ChevronRight,
  TrendingUp, TrendingDown, Sparkles,
} from 'lucide-react'
import { detectOutlier, type OutlierResult } from '@/lib/outlier-detection'
import { OutlierBadge, outlierRowLeftBorder } from '@/components/outlier-badge'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { useDatePeriod, periodToQuery } from '@/lib/use-date-range'
import { PeriodPicker } from '@/components/PeriodPicker'
import { ColHeader } from '@/components/ui/metric-tooltip'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Totals {
  orders: number; revenue: number
  spend: number; has_spend: boolean; roas: number | null
  impressions: number; clicks: number
  total_sent: number; sent_coverage_pct: number | null
  gclid: number; gbraid: number; enhanced_only: number; not_sent: number
  gclid_pct: number | null; cpa: number | null; avg_ticket: number | null
  data_source?: string
}

interface CampaignRow {
  campaign: string; orders: number; revenue: number
  gclid: number; enhanced: number; cpa: number | null; revenue_delta: number | null
  top_products: Array<{ name: string; sku: string | null; units: number; revenue: number }>
}

interface AdGroupRow {
  adgroup_id: string; adgroup_name: string; status: string
  spend: number; impressions: number; clicks: number
  ctr: number | null; conversions: number | null; conversions_value: number | null
  roas: number | null; cpa: number | null
}

interface PlatformCampaign {
  campaign_id: string | null; campaign_name: string; status: string
  spend: number; impressions: number; clicks: number
  ctr: number | null; cpc: number | null
  conversions: number | null; conversions_value: number | null
  roas: number | null; cpa: number | null
  ad_groups: AdGroupRow[]
  server_orders: number; server_revenue: number
  server_gclid: number; server_enhanced: number
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
  platform_campaigns: PlatformCampaign[]
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

// ── Status badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status, size = 'sm' }: { status: string; size?: 'xs' | 'sm' }) {
  const active = status === 'ENABLED'
  const label  = active ? 'Ativa' : status === 'PAUSED' ? 'Pausada' : (status || '—')
  const cls    = active
    ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/25'
    : 'bg-slate-500/15 text-slate-400 border-slate-500/25'
  return (
    <span className={`${size === 'xs' ? 'text-xs px-1 py-0.5' : 'text-xs px-1.5 py-0.5'} rounded border ${cls}`}>
      {label}
    </span>
  )
}

// ── Ad Group Row ──────────────────────────────────────────────────────────────

function AdGroupRowComp({ ag }: { ag: AdGroupRow }) {
  return (
    <tr className="border-t border-[#1a1f2e] hover:bg-[#1a1f2e]/60">
      <td className="pl-10 pr-4 py-2.5">
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400 line-clamp-1">{ag.adgroup_name}</span>
          <StatusBadge status={ag.status} size="xs" />
        </div>
      </td>
      <td />
      <td className="px-3 py-2.5 text-right text-xs text-slate-500 tabular-nums">{fmtN(ag.impressions)}</td>
      <td className="px-3 py-2.5 text-right text-xs text-slate-500 tabular-nums">{fmtN(ag.clicks)}</td>
      <td className="px-3 py-2.5 text-right text-xs text-slate-500 tabular-nums">
        {ag.ctr != null ? `${ag.ctr.toFixed(2)}%` : '—'}
      </td>
      <td className="px-3 py-2.5 text-right text-xs text-slate-300 tabular-nums">{fmt(ag.spend)}</td>
      <td className="px-3 py-2.5 text-right text-xs text-emerald-400/80 tabular-nums">
        {ag.conversions != null ? fmtN(Math.round(ag.conversions)) : '—'}
      </td>
      <td className="px-3 py-2.5 text-right text-xs text-emerald-400/80 tabular-nums">
        {ag.conversions_value != null ? fmt(ag.conversions_value) : '—'}
      </td>
      <td className="px-3 py-2.5 text-right tabular-nums">
        {ag.roas != null
          ? <span className="text-xs text-slate-300">{ag.roas.toFixed(2)}x</span>
          : <span className="text-slate-600 text-xs">—</span>}
      </td>
      <td className="px-3 py-2.5 text-right text-xs text-slate-500 tabular-nums">
        {ag.cpa != null ? fmtD2(ag.cpa) : '—'}
      </td>
      <td />
    </tr>
  )
}

// ── Platform Campaign Row (expandable → ad groups) ────────────────────────────

function PlatformCampaignRowComp({ row, roasOutlier }: { row: PlatformCampaign; roasOutlier?: OutlierResult }) {
  const [open, setOpen] = useState(false)
  const hasAdGroups = row.ad_groups.length > 0
  const leftBorder = outlierRowLeftBorder(roasOutlier)
  const roasTooltip = roasOutlier?.isOutlier
    ? roasOutlier.direction === 'positive'
      ? `ROAS ${row.roas?.toFixed(2)}x — campanha acima da média.`
      : `ROAS ${row.roas?.toFixed(2) ?? '—'} — campanha abaixo da média.`
    : undefined
  return (
    <>
      <tr
        className={`border-t border-[#2a2f3e] hover:bg-[#252a3a] ${hasAdGroups ? 'cursor-pointer' : ''}`}
        onClick={() => hasAdGroups && setOpen(v => !v)}
      >
        <td className="px-4 py-3" style={{ borderLeft: leftBorder }}>
          <div className="flex items-center gap-2">
            {hasAdGroups
              ? (open ? <ChevronDown size={13} className="text-slate-500 shrink-0" /> : <ChevronRight size={13} className="text-slate-500 shrink-0" />)
              : <span className="w-[13px]" />}
            <div className="min-w-0">
              <p className="text-sm text-white font-medium line-clamp-1">{row.campaign_name}</p>
              <div className="flex items-center gap-1.5 mt-0.5">
                {hasAdGroups && (
                  <p className="text-xs text-slate-600">{row.ad_groups.length} grupos de anúncios</p>
                )}
                {roasOutlier?.isOutlier && (
                  <OutlierBadge outlier={roasOutlier} tooltip={roasTooltip} />
                )}
              </div>
            </div>
          </div>
        </td>
        <td className="px-3 py-3 text-right">
          <StatusBadge status={row.status} />
        </td>
        <td className="px-3 py-3 text-right text-sm text-slate-400 tabular-nums">{fmtN(row.impressions)}</td>
        <td className="px-3 py-3 text-right text-sm text-slate-400 tabular-nums">{fmtN(row.clicks)}</td>
        <td className="px-3 py-3 text-right text-sm text-slate-400 tabular-nums">
          {row.ctr != null ? `${row.ctr.toFixed(2)}%` : '—'}
        </td>
        <td className="px-3 py-3 text-right text-sm text-slate-300 tabular-nums font-medium">{fmt(row.spend)}</td>
        <td className="px-3 py-3 text-right text-sm tabular-nums">
          <span className="text-emerald-400 font-semibold">
            {row.conversions != null ? fmtN(Math.round(row.conversions)) : '—'}
          </span>
          {row.server_orders > 0 && (
            <span className="text-xs text-slate-600 ml-1" title="Server-side">({row.server_orders})</span>
          )}
        </td>
        <td className="px-3 py-3 text-right text-sm tabular-nums">
          <span className="text-emerald-400 font-semibold">
            {row.conversions_value != null ? fmt(row.conversions_value) : '—'}
          </span>
          {row.server_revenue > 0 && (
            <p className={`text-xs mt-0.5 ${Math.abs(row.server_revenue - (row.conversions_value || 0)) > 50 ? 'text-teal-400' : 'text-slate-500'}`}>
              {fmt(row.server_revenue)} real
            </p>
          )}
        </td>
        <td className="px-3 py-3 text-right tabular-nums">
          {row.roas != null
            ? <span className={`text-sm font-bold ${row.roas >= 3 ? 'text-emerald-400' : row.roas >= 1.5 ? 'text-yellow-400' : 'text-red-400'}`}>{row.roas.toFixed(2)}x</span>
            : <span className="text-slate-600">—</span>}
        </td>
        <td className="px-3 py-3 text-right text-sm text-slate-400 tabular-nums">
          {row.cpa != null ? fmtD2(row.cpa) : '—'}
        </td>
        <td className="px-3 py-3 text-right text-sm text-slate-500 tabular-nums">
          {row.cpc != null ? fmtD2(row.cpc) : '—'}
        </td>
      </tr>
      {open && row.ad_groups.map(ag => <AdGroupRowComp key={ag.adgroup_id} ag={ag} />)}
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
  const [campExpanded, setCampExpanded] = useState<Set<string>>(new Set())

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
  const platformRoasValues = (data?.platform_campaigns || []).map(c => c.roas ?? 0)
  const funnel = data?.funnel || {}
  const fTop   = funnel.pageview || 1

  const chartData = (data?.daily || []).map(d => ({
    date:        fmtDt(d.date),
    Receita:     d.revenue,
    Investimento: d.spend,
    Compras:     d.orders,
    gclid:       d.gclid,
  }))

  const campaignsWithProducts = (data?.campaigns || []).filter(c => c.orders > 0 && c.top_products.length > 0)

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
          {t?.data_source === 'google_api' && (
            <div className="flex items-center gap-2 px-3 py-2 bg-blue-500/10 border border-blue-500/20 rounded-lg text-xs text-blue-300">
              <Sparkles size={12} />
              Compras e receita via Google Ads API — cliente sem integração de pedidos
            </div>
          )}
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

          {/* Platform Campaign Table — hierárquica Campanha → Grupo de anúncios */}
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-slate-300">Campanhas → Grupos de anúncios</h2>
                <p className="text-xs text-slate-500 mt-0.5">
                  Dados ao vivo da API do Google Ads · Expanda para ver grupos · Compras/Receita: Google (server-side)
                </p>
              </div>
              {loading && <Loader2 size={14} className="animate-spin text-slate-500" />}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#2a2f3e] bg-[#0f1117]">
                    {([
                      { h: 'Campanha / Grupo', tip: undefined, left: true },
                      { h: 'Status',       tip: 'Status da campanha reportado pelo Google Ads.' },
                      { h: 'Impressões',   tip: 'Impressões reportadas pelo Google Ads.' },
                      { h: 'Cliques',      tip: 'Cliques reportados pelo Google Ads.' },
                      { h: 'CTR',          tip: 'Taxa de clique: Cliques ÷ Impressões. Fonte: Google Ads API.' },
                      { h: 'Investimento', tip: 'Custo total da campanha no período. Fonte: Google Ads API.' },
                      { h: 'Conv. Google', tip: 'Conversões de Purchase reportadas pelo Google Ads. Inclui enhanced conversions (email hashed). Pode diferir de Pedidos Shopify.' },
                      { h: 'Receita Google', tip: 'Receita reportada pelo Google Ads. Usa janela de atribuição do Google (padrão: 30 dias por clique). Para receita real, veja Dashboard.' },
                      { h: 'ROAS',         tip: 'Receita Google ÷ Investimento. Usa a receita reportada pelo Google Ads.' },
                      { h: 'CPA',          tip: 'Investimento ÷ Conv. Google.' },
                      { h: 'CPC',          tip: 'Custo por clique médio.' },
                    ] as { h: string; tip?: string; left?: boolean }[]).map(({ h, tip, left }) => (
                      <th key={h} className={`px-3 py-2.5 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap ${left ? 'text-left px-4' : 'text-right'}`}>
                        <ColHeader label={h} tooltip={tip} right={!left} />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(data?.platform_campaigns || []).length === 0 ? (
                    <tr><td colSpan={11} className="py-10 text-center text-slate-500 text-sm">
                      {data?.has_creds ? 'Nenhuma campanha com investimento no período' : 'Google Ads não conectado para este cliente'}
                    </td></tr>
                  ) : (
                    (data?.platform_campaigns || []).map(row => (
                      <PlatformCampaignRowComp
                        key={row.campaign_id || row.campaign_name}
                        row={row}
                        roasOutlier={detectOutlier(row.roas ?? 0, platformRoasValues)}
                      />
                    ))
                  )}
                </tbody>
              </table>
            </div>
            <p className="px-5 py-3 text-xs text-slate-600 border-t border-[#2a2f3e]">
              Conv. e Receita são os valores reportados pelo Google Ads. O número entre parênteses e "real" são nossos pedidos server-side com utm_source=google.
            </p>
          </div>

          {/* O que cada campanha vendeu */}
          {campaignsWithProducts.length > 0 && (
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
              <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center gap-2">
                <Sparkles size={14} className="text-blue-400" />
                <div>
                  <h2 className="text-sm font-semibold text-slate-300">O que cada campanha vendeu</h2>
                  <p className="text-xs text-slate-500 mt-0.5">Produtos por campanha · atribuição server-side · clique para expandir</p>
                </div>
              </div>
              <div className="divide-y divide-[#2a2f3e]">
                {campaignsWithProducts.map((c) => {
                  const open = campExpanded.has(c.campaign)
                  return (
                    <div key={c.campaign}>
                      <button
                        onClick={() => setCampExpanded(prev => {
                          const n = new Set(prev)
                          open ? n.delete(c.campaign) : n.add(c.campaign)
                          return n
                        })}
                        className="w-full flex items-center gap-3 px-5 py-3.5 hover:bg-[#252a3a] transition-colors text-left"
                      >
                        {open ? <ChevronDown size={13} className="text-slate-500 shrink-0" /> : <ChevronRight size={13} className="text-slate-500 shrink-0" />}
                        <div className="flex-1 min-w-0">
                          <p className="text-sm text-white font-medium truncate">{c.campaign}</p>
                          <div className="flex items-center gap-2 mt-0.5">
                            {c.gclid > 0 && (
                              <span className="text-xs bg-blue-500/15 text-blue-300 px-1.5 py-0.5 rounded border border-blue-500/25">{c.gclid} gclid</span>
                            )}
                            {c.enhanced > 0 && (
                              <span className="text-xs bg-indigo-500/15 text-indigo-300 px-1.5 py-0.5 rounded border border-indigo-500/25">{c.enhanced} enhanced</span>
                            )}
                          </div>
                        </div>
                        <div className="text-right shrink-0">
                          <p className="text-sm font-bold text-emerald-400">{fmt(c.revenue)}</p>
                          <p className="text-xs text-slate-500">{c.orders} pedidos · CPA {c.cpa != null ? fmtD2(c.cpa) : '—'}</p>
                        </div>
                      </button>
                      {open && (
                        <div className="border-t border-[#2a2f3e] bg-[#0f1117]">
                          <table className="w-full text-xs">
                            <tbody>
                              {c.top_products.map((p, i) => (
                                <tr key={i} className="border-t border-[#2a2f3e]/50 last:border-0">
                                  <td className="px-5 py-2 text-slate-300 max-w-xs">
                                    <p className="truncate">{p.name}</p>
                                    {p.sku && <p className="text-slate-600 font-mono">{p.sku}</p>}
                                  </td>
                                  <td className="px-5 py-2 text-right text-slate-500">{p.units} un.</td>
                                  <td className="px-5 py-2 text-right text-emerald-400 font-semibold">{fmt(p.revenue)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Explanation */}
          <div className="bg-blue-500/5 border border-blue-500/20 rounded-xl p-5 space-y-3">
            <p className="text-sm font-semibold text-blue-400">Como interpretar os dados</p>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs text-slate-400 leading-relaxed">
              <div>
                <p className="text-slate-300 font-medium mb-1">Conv. Google vs pedidos server-side</p>
                <p>A coluna "Conv." mostra o que o Google Ads reportou. O número entre parênteses e o valor "real" em teal são nossos pedidos com utm_source=google — a diferença revela janelas de view-through ou compras por PIX que o Google não viu.</p>
              </div>
              <div>
                <p className="text-slate-300 font-medium mb-1">gclid vs Enhanced</p>
                <p>gclid = clique direto no anúncio → atribuição 100% certa. Enhanced = sem clique Google mas enviamos email/telefone hasheado para o Google cruzar. Quanto mais gclid, melhor o Smart Bidding aprende.</p>
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
