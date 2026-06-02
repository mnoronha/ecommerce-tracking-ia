'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import {
  Loader2, RefreshCw, ChevronDown, ChevronRight,
  TrendingUp, TrendingDown, Minus, ShoppingBag, DollarSign,
  MousePointerClick, Eye, Sparkles,
} from 'lucide-react'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { useDatePeriod, periodToQuery } from '@/lib/use-date-range'
import { PeriodPicker } from '@/components/PeriodPicker'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Totals {
  spend: number; revenue: number; purchases: number
  impressions: number; clicks: number
  roas: number | null; cpa: number | null; ctr: number | null
  cpc: number | null; avg_ticket: number | null
}

interface AdRow {
  ad_id: string; ad_name: string; image_url: string | null; status: string | null
  spend: number; revenue: number; purchases: number; impressions: number; clicks: number
  roas: number | null; cpa: number | null; roas_delta: number | null; spend_delta: number | null
}

interface AdsetRow {
  adset_id: string; adset_name: string
  spend: number; revenue: number; purchases: number; impressions: number; clicks: number
  roas: number | null; cpa: number | null; roas_delta: number | null; spend_delta: number | null
  ads: AdRow[]
}

interface CampaignRow {
  campaign_id: string; campaign_name: string
  spend: number; revenue: number; purchases: number; impressions: number; clicks: number
  roas: number | null; cpa: number | null; ctr: number | null; cpc: number | null
  roas_delta: number | null; spend_delta: number | null
  server_orders: number; server_revenue: number
  adsets: AdsetRow[]
}

interface DayRow {
  date: string; spend: number; revenue: number; purchases: number
  impressions: number; clicks: number; roas: number | null; cpc: number | null
}

interface OverviewData {
  days: number; start: string; end: string; prev_start: string; prev_end: string
  has_data: boolean
  totals: Totals; prev_totals: Totals; deltas: Record<string, number | null>
  campaigns: CampaignRow[]
  daily: DayRow[]
  funnel: Record<string, number>
  funnel_prev: Record<string, number>
}


// ── Helpers ───────────────────────────────────────────────────────────────────

const fmt = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(n)
const fmtDec = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 2 }).format(n)
const fmtN  = (n: number) => new Intl.NumberFormat('pt-BR').format(n)
const fmtD  = (s: string) => s.slice(8, 10) + '/' + s.slice(5, 7)

function Delta({ v }: { v: number | null }) {
  if (v === null || v === undefined) return <span className="text-slate-600 text-xs">—</span>
  const pos = v >= 0
  const Icon = pos ? TrendingUp : TrendingDown
  return (
    <span className={`inline-flex items-center gap-0.5 text-xs font-medium ${pos ? 'text-emerald-400' : 'text-red-400'}`}>
      <Icon size={10} />
      {pos ? '+' : ''}{v.toFixed(1)}%
    </span>
  )
}

function KpiCard({ label, value, delta, sub, accent }: {
  label: string; value: string; delta?: number | null; sub?: string; accent?: 'emerald' | 'teal' | 'indigo' | 'orange'
}) {
  const c = accent === 'emerald' ? 'text-emerald-400' : accent === 'teal' ? 'text-teal-400' : accent === 'orange' ? 'text-orange-400' : accent === 'indigo' ? 'text-indigo-400' : 'text-white'
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

function FunnelBar({ label, count, pct, prev, prevPct }: {
  label: string; count: number; pct: number; prev?: number; prevPct?: number
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
        <div className="h-full bg-indigo-500/70 rounded transition-all duration-700"
          style={{ width: `${Math.max(pct, count > 0 ? 2 : 0)}%` }} />
      </div>
      <p className="text-xs text-slate-600 mt-0.5">{pct.toFixed(1)}% do topo</p>
    </div>
  )
}

// ── Row components ─────────────────────────────────────────────────────────────

function AdRowComp({ ad }: { ad: AdRow }) {
  return (
    <tr className="border-t border-[#1a1f2e] hover:bg-[#1a1f2e]/40">
      <td className="pl-16 pr-4 py-2">
        <div className="flex items-center gap-2">
          {ad.image_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={ad.image_url} alt="" className="w-8 h-8 rounded object-cover shrink-0" />
          ) : (
            <div className="w-8 h-8 rounded bg-[#252a3a] shrink-0" />
          )}
          <span className="text-xs text-slate-300 line-clamp-1">{ad.ad_name}</span>
        </div>
      </td>
      <td className="px-3 py-2 text-right text-xs text-slate-400 tabular-nums">{fmtN(ad.impressions)}</td>
      <td className="px-3 py-2 text-right text-xs text-slate-400 tabular-nums">{fmtN(ad.clicks)}</td>
      <td className="px-3 py-2 text-right text-xs text-slate-300 tabular-nums">{fmt(ad.spend)}</td>
      <td className="px-3 py-2 text-right text-xs text-emerald-400 tabular-nums">{ad.purchases}</td>
      <td className="px-3 py-2 text-right text-xs text-emerald-400 tabular-nums">{fmt(ad.revenue)}</td>
      <td className="px-3 py-2 text-right text-xs tabular-nums">
        {ad.roas != null
          ? <span className={ad.roas >= 3 ? 'text-emerald-400 font-bold' : ad.roas >= 1.5 ? 'text-yellow-400' : 'text-red-400'}>{ad.roas.toFixed(2)}x</span>
          : <span className="text-slate-600">—</span>}
      </td>
      <td className="px-3 py-2 text-right text-xs text-slate-500 tabular-nums">
        {ad.cpa != null ? fmtDec(ad.cpa) : <span className="text-slate-600">—</span>}
      </td>
      <td className="px-3 py-2 text-right text-xs"><Delta v={ad.roas_delta ?? null} /></td>
    </tr>
  )
}

function AdsetRowComp({ adset }: { adset: AdsetRow }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <tr className="border-t border-[#1a1f2e] hover:bg-[#1a1f2e]/60 cursor-pointer"
          onClick={() => setOpen(v => !v)}>
        <td className="pl-10 pr-4 py-2.5">
          <div className="flex items-center gap-2">
            {open ? <ChevronDown size={11} className="text-slate-600" /> : <ChevronRight size={11} className="text-slate-600" />}
            <span className="text-xs text-slate-400 line-clamp-1">{adset.adset_name}</span>
            <span className="text-slate-600 text-xs">({adset.ads.length} anúncios)</span>
          </div>
        </td>
        <td className="px-3 py-2.5 text-right text-xs text-slate-500 tabular-nums">{fmtN(adset.impressions)}</td>
        <td className="px-3 py-2.5 text-right text-xs text-slate-500 tabular-nums">{fmtN(adset.clicks)}</td>
        <td className="px-3 py-2.5 text-right text-xs text-slate-300 tabular-nums">{fmt(adset.spend)}</td>
        <td className="px-3 py-2.5 text-right text-xs text-slate-400 tabular-nums">{adset.purchases}</td>
        <td className="px-3 py-2.5 text-right text-xs text-emerald-400/80 tabular-nums">{fmt(adset.revenue)}</td>
        <td className="px-3 py-2.5 text-right text-xs tabular-nums">
          {adset.roas != null ? <span className="text-slate-300">{adset.roas.toFixed(2)}x</span> : <span className="text-slate-600">—</span>}
        </td>
        <td className="px-3 py-2.5 text-right text-xs text-slate-500 tabular-nums">
          {adset.cpa != null ? fmtDec(adset.cpa) : <span className="text-slate-600">—</span>}
        </td>
        <td className="px-3 py-2.5 text-right text-xs"><Delta v={adset.roas_delta ?? null} /></td>
      </tr>
      {open && adset.ads.map(ad => <AdRowComp key={ad.ad_id} ad={ad} />)}
    </>
  )
}

function CampaignRowComp({ row }: { row: CampaignRow }) {
  const [open, setOpen] = useState(false)
  const serverDiff = row.server_orders - row.purchases
  return (
    <>
      <tr className="border-t border-[#2a2f3e] hover:bg-[#252a3a] cursor-pointer" onClick={() => setOpen(v => !v)}>
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            {open ? <ChevronDown size={13} className="text-slate-500" /> : <ChevronRight size={13} className="text-slate-500" />}
            <div className="min-w-0">
              <p className="text-sm text-white font-medium line-clamp-1">{row.campaign_name}</p>
              <p className="text-xs text-slate-600">{row.adsets.length} conjuntos</p>
            </div>
          </div>
        </td>
        <td className="px-3 py-3 text-right text-sm text-slate-400 tabular-nums">{fmtN(row.impressions)}</td>
        <td className="px-3 py-3 text-right text-sm text-slate-400 tabular-nums">{fmtN(row.clicks)}</td>
        <td className="px-3 py-3 text-right text-sm text-slate-300 tabular-nums font-medium">{fmt(row.spend)}</td>
        <td className="px-3 py-3 text-right text-sm tabular-nums">
          <span className="text-emerald-400 font-semibold">{row.purchases}</span>
          {serverDiff !== 0 && (
            <span className="text-xs text-slate-600 ml-1" title="Server-side">
              ({row.server_orders})
            </span>
          )}
        </td>
        <td className="px-3 py-3 text-right text-sm tabular-nums">
          <span className="text-emerald-400 font-semibold">{fmt(row.revenue)}</span>
          {row.server_revenue > 0 && (
            <p className={`text-xs mt-0.5 ${Math.abs(row.server_revenue - row.revenue) > 50 ? 'text-teal-400' : 'text-slate-500'}`}>
              {fmt(row.server_revenue)} real
            </p>
          )}
        </td>
        <td className="px-3 py-3 text-right tabular-nums">
          {row.roas != null ? (
            <span className={`text-sm font-bold ${row.roas >= 3 ? 'text-emerald-400' : row.roas >= 1.5 ? 'text-yellow-400' : 'text-red-400'}`}>
              {row.roas.toFixed(2)}x
            </span>
          ) : <span className="text-slate-600">—</span>}
        </td>
        <td className="px-3 py-3 text-right text-sm text-slate-400 tabular-nums">
          {row.cpa != null ? fmtDec(row.cpa) : <span className="text-slate-600">—</span>}
        </td>
        <td className="px-3 py-3 text-right"><Delta v={row.roas_delta ?? null} /></td>
      </tr>
      {open && row.adsets.map(as => <AdsetRowComp key={as.adset_id} adset={as} />)}
    </>
  )
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function MetaAdsPage() {
  const params  = useParams()
  const pixelId = params.clientId as string

  const { period, from, to, setPreset, setCustom } = useDatePeriod()
  const [data,     setData]     = useState<OverviewData | null>(null)
  const [loading,  setLoading]  = useState(true)
  const [syncing,  setSyncing]  = useState(false)
  const [syncMsg,  setSyncMsg]  = useState<string | null>(null)
  const [adData,   setAdData]   = useState<any[]>([])
  const [adExpanded, setAdExpanded] = useState<Set<string>>(new Set())

  const load = useCallback(async (q: string) => {
    setLoading(true)
    try {
      const res  = await fetch(`${API_URL}/meta-ads/${pixelId}/overview?${q}`)
      if (res.ok) setData(await res.json())
    } catch (_) {}
    setLoading(false)
  }, [pixelId])

  const loadAdProducts = useCallback(async (q: string) => {
    try {
      const res  = await fetch(`${API_URL}/journey/${pixelId}/by-ad?${q}&top_products=5`)
      if (res.ok) setAdData((await res.json()).ads || [])
    } catch (_) {}
  }, [pixelId])

  useEffect(() => {
    if (period === 'custom' && (!from || !to)) return
    const q = periodToQuery(period, from, to)
    load(q)
    loadAdProducts(q)
  }, [period, from, to, load, loadAdProducts])

  async function handleSync() {
    setSyncing(true); setSyncMsg(null)
    try {
      const res = await fetch(`${API_URL}/journey/${pixelId}/sync-meta-attribution?days=7`, { method: 'POST' })
      const d = await res.json()
      setSyncMsg(res.ok ? `${d.synced || 0} registros sincronizados` : 'Erro ao sincronizar')
      if (res.ok) { const q = periodToQuery(period, from, to); load(q); loadAdProducts(q) }
    } catch (_) { setSyncMsg('Erro ao sincronizar') }
    setSyncing(false)
  }

  const t = data?.totals
  const pt = data?.prev_totals
  const dlt = data?.deltas || {}

  const chartData = (data?.daily || []).map(d => ({
    date: fmtD(d.date),
    Investimento: d.spend,
    Receita: d.revenue,
    ROAS: d.roas,
  }))

  const funnel = data?.funnel || {}
  const funnelPrev = data?.funnel_prev || {}
  const fTop = funnel.pageview || 1

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-lg font-bold text-white">Meta Ads</h1>
          {data && (
            <p className="text-xs text-slate-500 mt-0.5">
              {fmtD(data.start)} → {fmtD(data.end)} · vs {fmtD(data.prev_start)} → {fmtD(data.prev_end)}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <PeriodPicker period={period} from={from} to={to} onPreset={setPreset} onCustom={setCustom} />
          <button onClick={handleSync} disabled={syncing}
            className="flex items-center gap-1.5 text-xs bg-[#1a1f2e] border border-[#2a2f3e] text-slate-300 px-3 py-2 rounded-lg hover:bg-[#252a3a] transition-colors disabled:opacity-50">
            {syncing ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            Sincronizar
          </button>
          <button onClick={() => load(periodToQuery(period, from, to))} className="text-slate-500 hover:text-white transition-colors">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>
      {syncMsg && (
        <div className="px-6 py-2 bg-indigo-500/10 text-xs text-indigo-300">{syncMsg}</div>
      )}

      {loading && !data ? (
        <div className="flex items-center gap-2 justify-center py-24 text-slate-500">
          <Loader2 size={18} className="animate-spin" /> Carregando…
        </div>
      ) : !data?.has_data ? (
        <div className="p-8 text-center text-slate-500">
          <p className="font-medium">Sem dados no período</p>
          <p className="text-xs mt-1">Clique em "Sincronizar" para buscar os dados do Meta Ads.</p>
        </div>
      ) : (
        <div className="p-6 space-y-6 max-w-[1400px]">

          {/* KPI Strip */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-10 gap-3">
            <KpiCard label="Investimento" value={t ? fmt(t.spend) : '—'}     delta={dlt.spend}       accent="indigo" />
            <KpiCard label="Receita Meta" value={t ? fmt(t.revenue) : '—'}   delta={dlt.revenue}     accent="emerald" />
            <KpiCard label="ROAS"         value={t?.roas != null ? `${t.roas.toFixed(2)}x` : '—'} delta={dlt.roas} accent="teal" />
            <KpiCard label="Compras"      value={t ? String(t.purchases) : '—'} delta={dlt.purchases} />
            <KpiCard label="CPA"          value={t?.cpa != null ? fmtDec(t.cpa) : '—'} delta={dlt.cpa != null ? -(dlt.cpa) : null} />
            <KpiCard label="Impressões"   value={t ? fmtN(t.impressions) : '—'} delta={dlt.impressions} />
            <KpiCard label="Clicks"       value={t ? fmtN(t.clicks) : '—'}   delta={dlt.clicks} />
            <KpiCard label="CTR"          value={t?.ctr != null ? `${t.ctr.toFixed(2)}%` : '—'} delta={dlt.ctr} />
            <KpiCard label="CPC"          value={t?.cpc != null ? fmtDec(t.cpc) : '—'} delta={dlt.cpc != null ? -(dlt.cpc) : null} />
            <KpiCard label="Ticket Médio" value={t?.avg_ticket != null ? fmt(t.avg_ticket) : '—'} delta={dlt.avg_ticket} accent="orange" />
          </div>

          {/* Charts + Funnel */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* Main chart: Invest + Revenue / ROAS */}
            <div className="lg:col-span-2 bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
              <h2 className="text-sm font-semibold text-slate-300 mb-4">Investimento × Receita × ROAS</h2>
              <ResponsiveContainer width="100%" height={200}>
                <ComposedChart data={chartData} margin={{ top: 0, right: 30, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2f3e" />
                  <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 10 }} />
                  <YAxis yAxisId="left"  tick={{ fill: '#64748b', fontSize: 10 }} tickFormatter={v => `R$${(v/1000).toFixed(0)}k`} />
                  <YAxis yAxisId="right" orientation="right" tick={{ fill: '#64748b', fontSize: 10 }} tickFormatter={v => `${v}x`} />
                  <Tooltip
                    contentStyle={{ background: '#1a1f2e', border: '1px solid #2a2f3e', borderRadius: 8, fontSize: 11 }}
                    formatter={(v: any, name: any) => name === 'ROAS' ? [`${Number(v).toFixed(2)}x`, name] : [fmt(Number(v)), name]}
                  />
                  <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
                  <Bar yAxisId="left" dataKey="Investimento" fill="#6366f1" opacity={0.7} radius={[2,2,0,0]} />
                  <Bar yAxisId="left" dataKey="Receita"      fill="#10b981" opacity={0.7} radius={[2,2,0,0]} />
                  <Line yAxisId="right" dataKey="ROAS" stroke="#f59e0b" strokeWidth={2} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>

            {/* Funnel */}
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
              <h2 className="text-sm font-semibold text-slate-300 mb-4">Funil de Conversão</h2>
              <div className="space-y-3">
                <FunnelBar label="Pageviews"     count={funnel.pageview || 0}        pct={100}
                  prev={funnelPrev.pageview} />
                <FunnelBar label="Add ao Carrinho" count={funnel.add_to_cart || 0}   pct={fTop > 0 ? (funnel.add_to_cart || 0)/fTop*100 : 0}
                  prev={funnelPrev.add_to_cart} />
                <FunnelBar label="Checkout Iniciado" count={funnel.begin_checkout || 0} pct={fTop > 0 ? (funnel.begin_checkout || 0)/fTop*100 : 0}
                  prev={funnelPrev.begin_checkout} />
                <FunnelBar label="Compras Meta"  count={funnel.purchases || 0}       pct={fTop > 0 ? (funnel.purchases || 0)/fTop*100 : 0}
                  prev={funnelPrev.purchases} />
              </div>
              {t && (
                <div className="mt-4 pt-4 border-t border-[#2a2f3e] grid grid-cols-2 gap-3 text-center">
                  <div>
                    <p className="text-lg font-bold text-emerald-400">{t.ctr?.toFixed(2) ?? '—'}%</p>
                    <p className="text-xs text-slate-500">CTR</p>
                  </div>
                  <div>
                    <p className="text-lg font-bold text-teal-400">{t.cpa != null ? fmtDec(t.cpa) : '—'}</p>
                    <p className="text-xs text-slate-500">CPA</p>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Campaign Table */}
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-slate-300">Campanhas → Conjuntos → Anúncios</h2>
                <p className="text-xs text-slate-500 mt-0.5">
                  Expanda para ver conjuntos e anúncios · Receita "real" = nosso server-side quando diferente do Meta
                </p>
              </div>
              {loading && <Loader2 size={14} className="animate-spin text-slate-500" />}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#2a2f3e] bg-[#0f1117]">
                    {['Campanha / Conjunto / Anúncio', 'Impressões', 'Clicks', 'Investimento', 'Compras', 'Receita', 'ROAS', 'CPA', '% Δ ROAS'].map(h => (
                      <th key={h} className={`px-3 py-2.5 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap ${h === 'Campanha / Conjunto / Anúncio' ? 'text-left' : 'text-right'}`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(data?.campaigns || []).length === 0 ? (
                    <tr><td colSpan={9} className="py-10 text-center text-slate-500 text-sm">Nenhuma campanha com dados no período</td></tr>
                  ) : (
                    (data?.campaigns || []).map(row => <CampaignRowComp key={row.campaign_id} row={row} />)
                  )}
                </tbody>
                {t && (
                  <tfoot className="border-t-2 border-[#2a2f3e] bg-[#0f1117]">
                    <tr>
                      <td className="px-4 py-3 text-xs font-semibold text-slate-400">Total</td>
                      <td className="px-3 py-3 text-right text-xs text-slate-400 tabular-nums font-medium">{fmtN(t.impressions)}</td>
                      <td className="px-3 py-3 text-right text-xs text-slate-400 tabular-nums font-medium">{fmtN(t.clicks)}</td>
                      <td className="px-3 py-3 text-right text-sm font-bold text-slate-200 tabular-nums">{fmt(t.spend)}</td>
                      <td className="px-3 py-3 text-right text-sm font-bold text-emerald-400 tabular-nums">{t.purchases}</td>
                      <td className="px-3 py-3 text-right text-sm font-bold text-emerald-400 tabular-nums">{fmt(t.revenue)}</td>
                      <td className="px-3 py-3 text-right text-sm font-bold tabular-nums">
                        {t.roas != null ? <span className="text-teal-400">{t.roas.toFixed(2)}x</span> : '—'}
                      </td>
                      <td className="px-3 py-3 text-right text-sm text-slate-300 tabular-nums">
                        {t.cpa != null ? fmtDec(t.cpa) : '—'}
                      </td>
                      <td className="px-3 py-3 text-right"><Delta v={dlt.roas ?? null} /></td>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          </div>

          {/* O que cada anúncio vendeu */}
          {adData.length > 0 && (
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
              <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center gap-2">
                <Sparkles size={14} className="text-indigo-400" />
                <div>
                  <h2 className="text-sm font-semibold text-slate-300">O que cada anúncio vendeu</h2>
                  <p className="text-xs text-slate-500 mt-0.5">Produtos por anúncio — clique para expandir</p>
                </div>
              </div>
              <div className="divide-y divide-[#2a2f3e]">
                {adData.filter(a => a.ad_id !== '—' && a.orders > 0).slice(0, 20).map((a: any) => {
                  const open = adExpanded.has(a.ad_id)
                  return (
                    <div key={a.ad_id}>
                      <button
                        onClick={() => setAdExpanded(prev => { const n = new Set(prev); open ? n.delete(a.ad_id) : n.add(a.ad_id); return n })}
                        className="w-full flex items-center gap-3 px-5 py-3.5 hover:bg-[#252a3a] transition-colors text-left"
                      >
                        {open ? <ChevronDown size={13} className="text-slate-500 shrink-0" /> : <ChevronRight size={13} className="text-slate-500 shrink-0" />}
                        {a.image_url ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={a.image_url} alt="" className="w-10 h-10 rounded object-cover shrink-0" />
                        ) : <div className="w-10 h-10 rounded bg-[#252a3a] shrink-0" />}
                        <div className="flex-1 min-w-0">
                          <p className="text-sm text-white font-medium truncate">{a.ad_name}</p>
                          <p className="text-xs text-slate-500">{a.platform} · {a.source}</p>
                        </div>
                        <div className="text-right shrink-0">
                          <p className="text-sm font-bold text-emerald-400">{fmt(a.revenue)}</p>
                          <p className="text-xs text-slate-500">{a.orders} pedidos · {a.units} un.</p>
                        </div>
                      </button>
                      {open && a.top_products.length > 0 && (
                        <div className="border-t border-[#2a2f3e] bg-[#0f1117]">
                          <table className="w-full text-xs">
                            <tbody>
                              {a.top_products.map((p: any) => (
                                <tr key={p.product_id} className="border-t border-[#2a2f3e]/50 last:border-0">
                                  <td className="px-5 py-2 text-slate-300 max-w-xs truncate">{p.name}</td>
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

          {/* Diferencial vs Looker */}
          <div className="bg-indigo-500/5 border border-indigo-500/20 rounded-xl p-5 text-xs text-slate-400 leading-relaxed">
            <p className="text-indigo-400 font-semibold text-sm mb-2">✦ Além do Looker</p>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div>
                <p className="text-slate-300 font-medium">Receita real vs Meta</p>
                <p>A coluna "Receita" mostra o que o Meta reportou. Quando nosso servidor registrou um valor diferente (ex: compra por PIX que o Meta não viu), aparece em teal logo abaixo — a diferença que o Looker não captura.</p>
              </div>
              <div>
                <p className="text-slate-300 font-medium">Compras reconciliadas</p>
                <p>O número entre parênteses nas compras é o que nosso servidor viu. Diferença positiva = Meta atribuiu vendas que não temos via UTM (janela de view). Negativa = capturamos por PIX/webhook.</p>
              </div>
              <div>
                <p className="text-slate-300 font-medium">Produtos por anúncio</p>
                <p>A seção "O que cada anúncio vendeu" mostra exatamente quais SKUs cada criativo converteu — granularidade que o Looker não tem acesso.</p>
              </div>
            </div>
          </div>

        </div>
      )}
    </div>
  )
}
