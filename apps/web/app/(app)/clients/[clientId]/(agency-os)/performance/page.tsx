'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams } from 'next/navigation'
import { ArrowLeft, Loader2, TrendingUp, RefreshCw } from 'lucide-react'
import Link from 'next/link'
import { useDatePeriod, periodToRange, PERIOD_LABEL, periodLabelLong, type Period } from '@/lib/use-date-range'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

function toYMD(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function fmtCurrency(v: number | null): string {
  if (v == null) return '—'
  return `R$ ${v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
function fmtRoas(v: number | null): string {
  if (v == null) return '—'
  return `${v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}x`
}
function fmtNum(v: number | null): string {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR')
}

function MetricBox({ label, value }: { label: string; value: string }) {
  const isNA = value === '—'
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4">
      <span className="text-xs text-slate-500 uppercase tracking-wide block mb-1.5">{label}</span>
      {isNA ? (
        <span className="text-xs text-slate-600">Não disponível</span>
      ) : (
        <span className="text-xl font-semibold text-white">{value}</span>
      )}
    </div>
  )
}

function SectionHeader({ title, note }: { title: string; note?: string }) {
  return (
    <div className="flex items-baseline gap-2 mb-3">
      <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">{title}</h2>
      {note && <span className="text-xs text-slate-600 italic">{note}</span>}
    </div>
  )
}

interface MetricValue {
  metric_key: string
  value: number | null
}

interface AdsSummary {
  spend: number
  roas: number | null
  cpa: number | null
  purchases: number
  revenue: number
}

interface GA4Summary {
  sessions: number
  users: number
  conversions: number
  purchases: number
  revenue: number
}

const PERIOD_FILTERS: Period[] = ['1d', '7d', '30d', '90d']

export default function PerformancePage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const { period, from, to, setPreset } = useDatePeriod()

  const [metrics, setMetrics]  = useState<MetricValue[]>([])
  const [meta,    setMeta]     = useState<AdsSummary | null>(null)
  const [google,  setGoogle]   = useState<AdsSummary | null>(null)
  const [ga4,     setGa4]      = useState<GA4Summary | null>(null)
  const [loading, setLoading]  = useState(true)
  const [loaded,  setLoaded]   = useState(false)

  // Canonical date window shared by ALL sections — eliminates period mismatch between metrics and ads APIs
  const range = useMemo(() => periodToRange(period, from, to), [period, from, to])
  const startStr = useMemo(() => toYMD(range.start), [range])
  const endStr   = useMemo(() => toYMD(range.end),   [range])
  const periodForApi = `${startStr}:${endStr}`         // format for metrics API
  const qs           = `start=${startStr}&end=${endStr}` // format for ads / ga4 APIs

  const load = useCallback(async () => {
    if (!clientId) return
    setLoading(true)
    const [metricsRes, metaRes, googleRes, ga4Res] = await Promise.all([
      fetch(`/api/v1/clients/${clientId}/metrics?period=${periodForApi}`).catch(() => null),
      fetch(`${API_URL}/meta-ads/${clientId}/overview?${qs}`).catch(() => null),
      fetch(`${API_URL}/google-ads/${clientId}/overview?${qs}`).catch(() => null),
      fetch(`${API_URL}/ga4/${clientId}/report?${qs}`).catch(() => null),
    ])

    if (metricsRes?.ok) {
      const data = await metricsRes.json().catch(() => ({}))
      setMetrics(Array.isArray(data.metrics) ? data.metrics : [])
    } else {
      setMetrics([])
    }

    if (metaRes?.ok) {
      const data = await metaRes.json().catch(() => null)
      const t = data?.totals
      setMeta(t ? { spend: t.spend ?? 0, roas: t.roas ?? null, cpa: t.cpa ?? null, purchases: t.purchases ?? 0, revenue: t.revenue ?? 0 } : null)
    } else {
      setMeta(null)
    }

    if (googleRes?.ok) {
      const data = await googleRes.json().catch(() => null)
      const t = data?.totals
      setGoogle(t ? { spend: t.spend ?? 0, roas: t.roas ?? null, cpa: t.cpa ?? null, purchases: t.orders ?? t.purchases ?? 0, revenue: t.revenue ?? 0 } : null)
    } else {
      setGoogle(null)
    }

    if (ga4Res?.ok) {
      const data = await ga4Res.json().catch(() => null)
      setGa4(data?.summary ?? null)
    } else {
      setGa4(null)
    }

    setLoading(false)
    setLoaded(true)
  }, [clientId, startStr, endStr])

  useEffect(() => { load() }, [load])

  const cVal = (key: string) => metrics.find(m => m.metric_key === key)?.value ?? null

  const revenue   = cVal('revenue_business')
  const orders    = cVal('orders_count')
  const spend     = cVal('total_spend')
  const mer       = cVal('mer')
  const aov       = revenue != null && orders != null && orders > 0 ? revenue / orders : null

  return (
    <div className="p-6 max-w-5xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <Link
            href={`/clients/${clientId}/dashboard`}
            className="text-slate-500 hover:text-white transition-colors"
          >
            <ArrowLeft size={16} />
          </Link>
          <div>
            <div className="flex items-center gap-2">
              <TrendingUp size={18} className="text-indigo-400" />
              <h1 className="text-xl font-bold text-white">Performance</h1>
            </div>
            <p className="text-xs text-slate-500 mt-0.5">{periodLabelLong(period, from, to)}</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
            {PERIOD_FILTERS.map(p => (
              <button
                key={p}
                onClick={() => setPreset(p)}
                className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                  period === p
                    ? 'bg-indigo-600 text-white'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {PERIOD_LABEL[p]}
              </button>
            ))}
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="p-1.5 rounded-lg bg-[#1a1f2e] border border-[#2a2f3e] text-slate-500 hover:text-white disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
          <Link
            href={`/clients/${clientId}/canonical-reports`}
            className="text-xs text-slate-500 hover:text-white border border-[#2a2f3e] rounded-lg px-3 py-1.5 transition-colors"
          >
            Relatórios
          </Link>
        </div>
      </div>

      {loading && !loaded && (
        <div className="flex items-center gap-2 justify-center py-16 text-slate-600">
          <Loader2 size={16} className="animate-spin" />
          <span className="text-sm">Carregando…</span>
        </div>
      )}

      {(!loading || loaded) && (
        <div className="space-y-6">
          {/* NEGÓCIO */}
          <section>
            <SectionHeader title="Negócio" note="Business Truth" />
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
              <MetricBox label="Receita"      value={fmtCurrency(revenue)} />
              <MetricBox label="Pedidos"      value={fmtNum(orders)} />
              <MetricBox label="Ticket Médio" value={fmtCurrency(aov)} />
              <MetricBox label="MER"          value={fmtRoas(mer)} />
              <MetricBox label="Investimento" value={fmtCurrency(spend)} />
            </div>
          </section>

          {/* META ADS */}
          <section>
            <SectionHeader title="Meta Ads" />
            {meta ? (
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
                <MetricBox label="Investimento" value={fmtCurrency(meta.spend)} />
                <MetricBox label="Receita"      value={fmtCurrency(meta.revenue)} />
                <MetricBox label="ROAS"         value={fmtRoas(meta.roas)} />
                <MetricBox label="Compras"      value={fmtNum(meta.purchases)} />
                <MetricBox label="CPA"          value={fmtCurrency(meta.cpa)} />
              </div>
            ) : (
              <div className="bg-[#1a1f2e] rounded-xl px-4 py-3">
                <span className="text-sm text-slate-600">Não disponível</span>
              </div>
            )}
          </section>

          {/* GOOGLE ADS */}
          <section>
            <SectionHeader title="Google Ads" />
            {google ? (
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
                <MetricBox label="Investimento" value={fmtCurrency(google.spend)} />
                <MetricBox label="Receita"      value={fmtCurrency(google.revenue)} />
                <MetricBox label="ROAS"         value={fmtRoas(google.roas)} />
                <MetricBox label="Conversões"   value={fmtNum(google.purchases)} />
                <MetricBox label="CPA"          value={fmtCurrency(google.cpa)} />
              </div>
            ) : (
              <div className="bg-[#1a1f2e] rounded-xl px-4 py-3">
                <span className="text-sm text-slate-600">Não disponível</span>
              </div>
            )}
          </section>

          {/* GA4 */}
          <section>
            <SectionHeader title="GA4" />
            {ga4 ? (
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
                <MetricBox label="Sessões"     value={fmtNum(ga4.sessions)} />
                <MetricBox label="Usuários"    value={fmtNum(ga4.users)} />
                <MetricBox label="Conversões"  value={fmtNum(ga4.conversions)} />
                <MetricBox label="Compras"     value={fmtNum(ga4.purchases)} />
                <MetricBox label="Receita GA4" value={fmtCurrency(ga4.revenue)} />
              </div>
            ) : (
              <div className="bg-[#1a1f2e] rounded-xl px-4 py-3">
                <span className="text-sm text-slate-600">Não disponível</span>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  )
}
