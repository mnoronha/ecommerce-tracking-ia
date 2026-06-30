'use client'

import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { supabase } from '@/lib/supabase'
import { ShoppingBag, Users, TrendingUp, Activity, RefreshCw, Percent, CheckCircle, Sparkles, AlertTriangle, Lightbulb, BarChart2, Loader2, Target, Globe, UserX, DollarSign } from 'lucide-react'
import IntegrationsHealth from '@/components/IntegrationsHealth'
import { useDatePeriod, periodLabelLong, periodToQuery } from '@/lib/use-date-range'
import { PeriodPicker } from '@/components/PeriodPicker'
import { EmptyState } from '@/components/ui/empty-state'
import { ColHeader, SourceBadge } from '@/components/ui/metric-tooltip'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Line,
} from 'recharts'

// ── Types ──────────────────────────────────────────────────────────────────────

type Tab = 'overview' | 'traffic' | 'campaigns' | 'clients'


interface KPIs {
  totalRevenue: number
  totalOrders: number
  totalVisitors: number
  avgOrderValue: number
  revenueChange:       number
  ordersChange:        number
  visitorsChange:      number
  avgOrderValueChange: number
  conversionRateChange: number
  conversionRate: number
  totalProfit:   number | null
  marginPct:     number | null
}

interface RevenuePoint { date: string; revenue: number; orders: number; prevRevenue?: number }

interface Ga4Channel { channel: string; sessions: number; users: number; conversions: number; revenue: number }
interface Ga4Funnel { sessions: number; add_to_cart: number; begin_checkout: number; purchases: number; atc_rate: number; checkout_rate: number; purchase_rate: number }
interface Ga4TopPage { path: string; title: string; sessions: number; conversions: number; revenue: number; conv_rate: number; bounce_rate: number }
interface AdsTotals { spend: number; roas: number | null; cpa: number | null; purchases: number; revenue: number }
interface LtvCustomer { email: string; total: number; orders: number }
interface LtvStats { avgLtv: number; topCustomers: LtvCustomer[]; atRisk: number; totalCustomers: number }

interface Order {
  id: string
  email: string | null
  total_price: number
  gross_profit?: number | null
  margin_pct?: number | null
  financial_status: string | null
  platform_source: string | null
  utm_source: string | null
  utm_medium: string | null
  utm_campaign: string | null
  is_first_purchase: boolean | null
  shipping_country?: string | null
  created_at: string
}

interface RetentionData {
  newOrders: number
  returningOrders: number
  total: number
}

interface FunnelStep { label: string; count: number; pct: number }

interface CampaignRow {
  source: string
  medium: string
  campaign: string
  orders: number
  revenue: number
  pctRevenue: number
  avgTicket: number
}

interface ProductRow {
  name: string
  views: number
  cartAdds: number
  purchases: number
}

interface Attribution {
  ordersWithUtm: number
  ordersWithEmail: number
  total: number
}

interface Insight {
  id: string
  type: string
  severity: string
  title: string
  content: string
  data: { recommendation?: string }
  is_read: boolean
  created_at: string
}

interface CohortMonth {
  label:     string   // e.g. "Abr 2025"
  newBuyers: number
  returned:  number
  retPct:    number
}

interface RoasCampaign {
  campaign_name: string
  utm_source:   string | null
  spend:        number
  revenue:      number
  gross_profit: number | null
  margin_pct:   number | null
  margin_roas:  number | null
  orders:       number
  roas:         number | null
  cpa:          number | null
  impressions:  number
  clicks:       number
  ctr:          number | null
  cpm:          number | null
}

interface RoasCampaignWithMeta extends RoasCampaign {
  meta_purchases: number
  meta_revenue:   number
  meta_cpa:       number | null
  meta_roas:      number | null
  cpa_diff_pct:   number | null
  purchases_diff: number
}

interface RoasData {
  has_ads_credentials: boolean
  has_cogs:   boolean
  days:       number
  campaigns:  RoasCampaignWithMeta[]
  totals: {
    spend:           number
    revenue:         number
    gross_profit:    number | null
    margin_pct:      number | null
    margin_roas:     number | null
    orders:          number
    roas:            number | null
    total_cpa:       number | null
    meta_purchases:  number
    meta_revenue:    number
    meta_cpa:        number | null
    meta_roas:       number | null
    cpa_diff_pct:    number | null
  }
  paid_only?: {
    revenue:      number
    orders:       number
    spend:        number
    roas:         number | null
    cpa:          number | null
    gross_profit: number | null
    margin_roas:  number | null
    campaigns:    number
  }
}

type DrilldownKPI = 'revenue' | 'orders' | 'visitors' | 'avgOrderValue' | 'conversionRate' | 'profit'

interface ChannelRevenue {
  channel: string
  revenue: number
  orders: number
  pct: number
  colorBar: string
  colorBadge: string
}

interface CampaignProductItem { name: string; qty: number; revenue: number }
interface CampaignProductRow {
  campaign: string
  source: string | null
  medium: string | null
  orders: number
  revenue: number
  products: CampaignProductItem[]
  data_source: 'order_items' | 'tracking_events'
}

interface PacingData {
  mtd_revenue:              number
  mtd_orders:               number
  mtd_profit:               number | null
  today_revenue:            number
  today_orders:             number
  projected_revenue:        number
  monthly_revenue_goal:     number | null
  pct_done:                 number | null
  pct_target:               number
  on_track:                 boolean | null
  needed_per_day_remaining: number | null
  day_of_month:             number
  days_in_month:            number
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmt = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n)

// Locale-independent YYYY-MM-DD key in local timezone
function toDateKey(iso: string): string {
  const d = new Date(iso)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${dd}`
}
// Display formatter for the chart X-axis: YYYY-MM-DD → "DD/MM"
const fmtDateAxis = (key: string) => key.slice(8, 10) + '/' + key.slice(5, 7)

const fmtDate = toDateKey

const pct = (n: number, total: number) =>
  total > 0 ? ((n / total) * 100).toFixed(0) + '%' : '—'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Channel classification ────────────────────────────────────────────────────

function classifyChannel(source: string | null, medium: string | null): string {
  const s = (source || '').toLowerCase()
  const m = (medium || '').toLowerCase()
  if (!s) return 'Direto'
  if (['facebook', 'instagram', 'meta', 'fb'].includes(s) || s.includes('facebook') || s.includes('instagram')) return 'Meta Ads'
  if (s === 'google' && m === 'organic') return 'Google Orgânico'
  if (['google', 'cpc', 'adwords', 'paid_search', 'ppc'].includes(s)) return 'Google Ads'
  if (s.includes('tiktok') || s === 'tt') return 'TikTok Ads'
  if (['email', 'klaviyo', 'newsletter', 'crm'].includes(s) || ['email', 'crm', 'newsletter'].includes(m)) return 'Email / CRM'
  if (m === 'organic' || s === 'organic') return 'Orgânico'
  return 'Outros'
}

const CHANNEL_COLORS: Record<string, { bar: string; badge: string }> = {
  'Meta Ads':        { bar: 'bg-blue-500',    badge: 'bg-blue-500/15 text-blue-300 border border-blue-500/25' },
  'Google Ads':      { bar: 'bg-red-400',     badge: 'bg-red-500/15 text-red-300 border border-red-500/25' },
  'Google Orgânico': { bar: 'bg-orange-400',  badge: 'bg-orange-500/15 text-orange-300 border border-orange-500/25' },
  'TikTok Ads':      { bar: 'bg-pink-500',    badge: 'bg-pink-500/15 text-pink-300 border border-pink-500/25' },
  'Email / CRM':     { bar: 'bg-yellow-400',  badge: 'bg-yellow-500/15 text-yellow-300 border border-yellow-500/25' },
  'Orgânico':        { bar: 'bg-emerald-500', badge: 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/25' },
  'Direto':          { bar: 'bg-slate-500',   badge: 'bg-slate-500/15 text-slate-400 border border-slate-500/25' },
  'Outros':          { bar: 'bg-indigo-400',  badge: 'bg-indigo-500/15 text-indigo-300 border border-indigo-500/25' },
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SparkleLine({ data, positive }: { data: number[]; positive: boolean }) {
  if (data.length < 2) return null
  const max = Math.max(...data, 0.01)
  const w = 100, h = 28
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w
    const y = h - 4 - (v / max) * (h - 8)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const color = positive ? '#34d399' : '#f87171'
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-7 mt-1" preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5"
        strokeLinecap="round" strokeLinejoin="round" opacity="0.7" />
    </svg>
  )
}

function KPICard({ title, value, icon: Icon, change, color, hint, spark, onClick }: {
  title: string; value: string; icon: React.ElementType
  change?: number; color: string; hint?: string
  spark?: number[]
  onClick?: () => void
}) {
  const Tag: any = onClick ? 'button' : 'div'
  const positive  = (change ?? 0) >= 0
  return (
    <Tag
      onClick={onClick}
      className={`bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e] text-left w-full ${
        onClick ? 'hover:border-indigo-500/50 cursor-pointer transition-colors' : ''
      }`}
    >
      <div className="flex items-start justify-between mb-3">
        <span className="text-sm text-slate-400">{title}</span>
        <div className={`p-2 rounded-lg ${color}`}><Icon size={16} /></div>
      </div>
      <div className="text-2xl font-bold text-white mb-1">{value}</div>
      {change !== undefined && (
        <div className={`flex items-center gap-1 text-xs font-medium ${positive ? 'text-emerald-400' : 'text-red-400'}`}>
          <span>{positive ? '▲' : '▼'}</span>
          <span>{Math.abs(change).toFixed(1)}% vs anterior</span>
        </div>
      )}
      {hint && <div className="text-xs text-slate-500 mt-0.5">{hint}</div>}
      {spark && spark.length >= 2 && (
        <SparkleLine data={spark} positive={positive} />
      )}
    </Tag>
  )
}

function FunnelBar({ steps }: { steps: FunnelStep[] }) {
  const colors = ['#6366f1', '#8b5cf6', '#a855f7', '#ec4899', '#10b981']
  // Compute drop% between adjacent steps so the merchant sees where the leak is
  const drops = steps.map((step, i) => {
    if (i === 0 || steps[i - 1].count === 0) return null
    return 1 - step.count / steps[i - 1].count
  })
  return (
    <div className="space-y-3">
      {steps.map((step, i) => {
        const drop = drops[i]
        const isWorstDrop = drop !== null && drop === Math.max(...drops.filter((d): d is number => d !== null))
        return (
          <div key={step.label}>
            <div className="flex justify-between text-xs mb-1">
              <span className="text-slate-300">{step.label}</span>
              <span className="text-slate-400">
                {step.count.toLocaleString('pt-BR')}
                <span className="text-slate-500 ml-1">({step.pct.toFixed(1)}%)</span>
              </span>
            </div>
            <div className="h-5 bg-[#0f1117] rounded overflow-hidden">
              <div
                className="h-full rounded transition-all duration-700"
                style={{ width: `${Math.max(step.pct, step.count > 0 ? 2 : 0)}%`, backgroundColor: colors[i] }}
              />
            </div>
            {drop !== null && drop > 0.05 && (
              <p className={`text-xs mt-1 ${isWorstDrop ? 'text-red-400 font-medium' : 'text-slate-600'}`}>
                ↓ {(drop * 100).toFixed(0)}% perdidos
                {isWorstDrop && ' · maior queda'}
              </p>
            )}
          </div>
        )
      })}
    </div>
  )
}

function KPIDrilldownModal({
  kpi,
  orders,
  onClose,
}: {
  kpi: DrilldownKPI
  orders: any[]
  onClose: () => void
}) {
  const TITLES: Record<DrilldownKPI, string> = {
    revenue:        'Receita',
    orders:         'Pedidos',
    visitors:       'Visitantes',
    avgOrderValue:  'Ticket Médio',
    conversionRate: 'Taxa de Conversão',
    profit:         'Margem Bruta',
  }

  // Daily series + breakdown by source/country/device based on KPI semantics
  const byDay: Record<string, { value: number; count: number }> = {}
  orders.forEach(o => {
    const day = fmtDate(o.created_at)
    if (!byDay[day]) byDay[day] = { value: 0, count: 0 }
    if (kpi === 'revenue')    byDay[day].value += o.total_price || 0
    else if (kpi === 'profit') byDay[day].value += o.gross_profit || 0
    byDay[day].count += 1
  })
  const series = Object.entries(byDay).map(([date, v]) => ({
    date,
    value: kpi === 'orders' ? v.count : kpi === 'avgOrderValue' ? (v.count ? v.value / v.count : 0) : v.value,
  }))

  // Breakdown by source
  const bySource: Record<string, { revenue: number; orders: number }> = {}
  orders.forEach(o => {
    const src = o.utm_source || 'direto'
    if (!bySource[src]) bySource[src] = { revenue: 0, orders: 0 }
    bySource[src].revenue += o.total_price || 0
    bySource[src].orders  += 1
  })
  const sourceRows = Object.entries(bySource)
    .map(([source, v]) => ({ source, ...v }))
    .sort((a, b) => b.revenue - a.revenue)
    .slice(0, 8)

  // Breakdown by country
  const byCountry: Record<string, { revenue: number; orders: number }> = {}
  orders.forEach(o => {
    const c = o.shipping_country || 'desconhecido'
    if (!byCountry[c]) byCountry[c] = { revenue: 0, orders: 0 }
    byCountry[c].revenue += o.total_price || 0
    byCountry[c].orders  += 1
  })
  const countryRows = Object.entries(byCountry)
    .map(([country, v]) => ({ country, ...v }))
    .sort((a, b) => b.revenue - a.revenue)
    .slice(0, 6)

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-end" onClick={onClose}>
      <div
        className="w-full max-w-2xl h-full bg-[#0f1117] border-l border-[#2a2f3e] overflow-auto"
        onClick={e => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-[#0f1117] border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between z-10">
          <div>
            <h2 className="text-lg font-bold text-white">Detalhes — {TITLES[kpi]}</h2>
            <p className="text-xs text-slate-500 mt-0.5">{orders.length} pedidos no período</p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white text-2xl leading-none">×</button>
        </div>

        <div className="p-6 space-y-6">
          {/* Daily series */}
          <div className="bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
            <h3 className="text-sm font-semibold text-slate-300 mb-3">Diário</h3>
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={series}>
                <defs>
                  <linearGradient id="kpiGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2f3e" />
                <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 10 }} tickFormatter={fmtDateAxis} />
                <YAxis tick={{ fill: '#64748b', fontSize: 10 }} />
                <Tooltip
                  contentStyle={{ background: '#1a1f2e', border: '1px solid #2a2f3e', borderRadius: 8 }}
                  formatter={(v) => [
                    kpi === 'revenue' || kpi === 'profit' || kpi === 'avgOrderValue'
                      ? fmt(Number(v)) : Math.round(Number(v)).toString(),
                    TITLES[kpi],
                  ]}
                />
                <Area type="monotone" dataKey="value" stroke="#6366f1" fill="url(#kpiGrad)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {/* By source */}
          <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-hidden">
            <div className="px-5 py-3 border-b border-[#2a2f3e]">
              <h3 className="text-sm font-semibold text-slate-300">Top fontes</h3>
            </div>
            <table className="w-full text-sm">
              <tbody>
                {sourceRows.map(s => (
                  <tr key={s.source} className="border-b border-[#2a2f3e] last:border-0">
                    <td className="px-5 py-2.5 text-slate-300 text-xs">{s.source}</td>
                    <td className="px-5 py-2.5 text-right text-slate-400 text-xs">{s.orders} pedidos</td>
                    <td className="px-5 py-2.5 text-right text-emerald-400 font-semibold whitespace-nowrap">{fmt(s.revenue)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* By country */}
          {countryRows.length > 1 && (
            <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-hidden">
              <div className="px-5 py-3 border-b border-[#2a2f3e]">
                <h3 className="text-sm font-semibold text-slate-300">Top países</h3>
              </div>
              <table className="w-full text-sm">
                <tbody>
                  {countryRows.map(c => (
                    <tr key={c.country} className="border-b border-[#2a2f3e] last:border-0">
                      <td className="px-5 py-2.5 text-slate-300 text-xs">{c.country}</td>
                      <td className="px-5 py-2.5 text-right text-slate-400 text-xs">{c.orders} pedidos</td>
                      <td className="px-5 py-2.5 text-right text-emerald-400 font-semibold whitespace-nowrap">{fmt(c.revenue)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function PacingWidget({ pacing }: { pacing: PacingData }) {
  const goal     = pacing.monthly_revenue_goal!
  const pctDone  = pacing.pct_done ?? 0
  const onTrack  = pacing.on_track
  const projDiff = pacing.projected_revenue - goal
  const projPct  = goal ? (pacing.projected_revenue / goal) * 100 : 0
  return (
    <div className="bg-gradient-to-br from-[#1a1f2e] to-[#1a1f2e]/50 rounded-xl border border-[#2a2f3e] p-5">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h2 className="text-sm font-semibold text-white">Pacing — Meta do Mês</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Dia {pacing.day_of_month} de {pacing.days_in_month}
            {' · '}{pacing.pct_target.toFixed(0)}% do mês decorrido
          </p>
        </div>
        <span className={`text-xs px-2 py-1 rounded font-medium ${
          onTrack === true  ? 'bg-emerald-500/15 text-emerald-300' :
          onTrack === false ? 'bg-red-500/15 text-red-300' :
          'bg-slate-500/15 text-slate-400'
        }`}>
          {onTrack === true ? 'No ritmo' : onTrack === false ? 'Abaixo da meta' : '—'}
        </span>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-3">
        <div>
          <p className="text-xs text-slate-500">Realizado</p>
          <p className="text-lg font-bold text-white">{fmt(pacing.mtd_revenue)}</p>
          <p className="text-xs text-slate-400">{pctDone.toFixed(1)}% da meta</p>
        </div>
        <div>
          <p className="text-xs text-slate-500">Meta</p>
          <p className="text-lg font-bold text-slate-300">{fmt(goal)}</p>
        </div>
        <div>
          <p className="text-xs text-slate-500">Projeção</p>
          <p className={`text-lg font-bold ${projDiff >= 0 ? 'text-emerald-400' : 'text-yellow-400'}`}>
            {fmt(pacing.projected_revenue)}
          </p>
          <p className="text-xs text-slate-400">
            {projDiff >= 0 ? '+' : ''}{(projPct - 100).toFixed(0)}% vs meta
          </p>
        </div>
        <div>
          <p className="text-xs text-slate-500">Faltam/dia</p>
          <p className="text-lg font-bold text-indigo-300">
            {pacing.needed_per_day_remaining ? fmt(pacing.needed_per_day_remaining) : '—'}
          </p>
        </div>
        <div>
          <p className="text-xs text-slate-500">Hoje</p>
          <p className="text-lg font-bold text-emerald-400">{fmt(pacing.today_revenue)}</p>
          <p className="text-xs text-slate-400">{pacing.today_orders} pedidos</p>
        </div>
      </div>

      {/* Progress bar with target marker */}
      <div className="relative h-2 bg-[#0f1117] rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-700 ${
            onTrack === true ? 'bg-emerald-500' :
            onTrack === false ? 'bg-yellow-500' : 'bg-indigo-500'
          }`}
          style={{ width: `${Math.min(pctDone, 100)}%` }}
        />
        <div
          className="absolute top-0 h-full w-px bg-slate-400/60"
          style={{ left: `${pacing.pct_target}%` }}
          title={`Posição esperada: ${pacing.pct_target.toFixed(0)}%`}
        />
      </div>
    </div>
  )
}

const INSIGHT_ICON: Record<string, React.ElementType> = {
  weekly_report:    BarChart2,
  recommendation:   Lightbulb,
  anomaly:          AlertTriangle,
  pattern:          Sparkles,
}

const SEVERITY_STYLE: Record<string, string> = {
  info:     'border-indigo-500/30 bg-indigo-500/5',
  warning:  'border-yellow-500/30 bg-yellow-500/5',
  critical: 'border-red-500/30 bg-red-500/5',
}

const SEVERITY_ICON_COLOR: Record<string, string> = {
  info:     'text-indigo-400',
  warning:  'text-yellow-400',
  critical: 'text-red-400',
}

function InsightCard({
  insight, onDismiss, autoExpand = false,
}: {
  insight: Insight
  onDismiss: (id: string) => void
  autoExpand?: boolean
}) {
  const [expanded, setExpanded] = useState(autoExpand)
  const Icon = INSIGHT_ICON[insight.type] || Lightbulb

  return (
    <div className={`rounded-xl border p-4 transition-all ${SEVERITY_STYLE[insight.severity] || SEVERITY_STYLE.info}`}>
      <div className="flex items-start gap-3">
        <div className={`mt-0.5 shrink-0 ${SEVERITY_ICON_COLOR[insight.severity]}`}>
          <Icon size={16} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <p className="text-sm font-semibold text-white">{insight.title}</p>
            <button
              onClick={() => onDismiss(insight.id)}
              className="shrink-0 text-slate-600 hover:text-slate-400 transition-colors text-xs leading-none mt-0.5"
              title="Dispensar"
            >
              ✕
            </button>
          </div>

          {expanded ? (
            <>
              <p className="text-xs text-slate-400 mt-2 leading-relaxed whitespace-pre-wrap">
                {insight.content}
              </p>
              {insight.data?.recommendation && (
                <div className="mt-3 bg-[#0f1117] rounded-lg p-3 border border-[#2a2f3e]">
                  <p className="text-xs font-medium text-emerald-400 mb-1">Ação recomendada</p>
                  <p className="text-xs text-slate-300">{insight.data.recommendation}</p>
                </div>
              )}
              <button
                onClick={() => setExpanded(false)}
                className="text-xs text-slate-500 hover:text-slate-300 mt-3"
              >
                Recolher
              </button>
            </>
          ) : (
            <button
              onClick={() => setExpanded(true)}
              className="text-xs text-slate-500 hover:text-slate-300 mt-1"
            >
              Ver análise →
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Sales Heatmap ─────────────────────────────────────────────────────────────

const _DAYS   = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']
const _BLOCKS = ['00h', '03h', '06h', '09h', '12h', '15h', '18h', '21h']

function SalesHeatmap({ grid }: { grid: number[][] }) {
  const maxVal = Math.max(...grid.flat(), 1)
  if (grid.every(row => row.every(v => v === 0))) {
    return (
      <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] p-5">
        <h2 className="text-sm font-semibold text-slate-300 mb-1">Horários de Maior Venda</h2>
        <p className="text-slate-500 text-sm mt-4">Sem dados de pedidos no período</p>
      </div>
    )
  }
  return (
    <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-hidden">
      <div className="px-5 py-4 border-b border-[#2a2f3e]">
        <h2 className="text-sm font-semibold text-slate-300">Horários de Maior Venda</h2>
        <p className="text-xs text-slate-500 mt-0.5">Pedidos por dia da semana × bloco de 3 horas — útil para programar anúncios</p>
      </div>
      <div className="p-5 overflow-x-auto">
        <div
          className="inline-grid gap-1.5"
          style={{ gridTemplateColumns: `48px repeat(8, minmax(40px, 1fr))` }}
        >
          {/* Header */}
          <div />
          {_BLOCKS.map(b => (
            <div key={b} className="text-center text-xs text-slate-500 pb-1">{b}</div>
          ))}
          {/* Rows */}
          {_DAYS.map((day, di) => (
            <React.Fragment key={day}>
              <div className="text-xs text-slate-400 flex items-center justify-end pr-2">{day}</div>
              {Array.from({ length: 8 }, (_, bi) => {
                const val       = grid[di]?.[bi] || 0
                const intensity = val / maxVal
                return (
                  <div
                    key={bi}
                    title={`${day} ${_BLOCKS[bi]}: ${val} pedido${val !== 1 ? 's' : ''}`}
                    className="rounded flex items-center justify-center text-xs font-medium cursor-default transition-colors"
                    style={{
                      height: 36,
                      backgroundColor: intensity > 0
                        ? `rgba(99,102,241,${0.12 + intensity * 0.83})`
                        : '#0f1117',
                      color: intensity > 0.55 ? '#fff' : intensity > 0.1 ? '#a5b4fc' : 'transparent',
                    }}
                  >
                    {val > 0 ? val : ''}
                  </div>
                )
              })}
            </React.Fragment>
          ))}
        </div>
        <div className="flex items-center gap-2 mt-4">
          <span className="text-xs text-slate-500">Menos vendas</span>
          {[0.12, 0.3, 0.5, 0.7, 0.95].map(i => (
            <div key={i} className="w-6 h-3 rounded" style={{ backgroundColor: `rgba(99,102,241,${i})` }} />
          ))}
          <span className="text-xs text-slate-500">Mais vendas</span>
        </div>
      </div>
    </div>
  )
}

// ── Main Dashboard ────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const [kpis, setKpis]               = useState<KPIs | null>(null)
  const [revenueData, setRevenueData] = useState<RevenuePoint[]>([])
  const [recentOrders, setRecentOrders] = useState<Order[]>([])
  const [funnelSteps, setFunnelSteps] = useState<FunnelStep[]>([])
  const [campaigns, setCampaigns]     = useState<CampaignRow[]>([])
  const [products, setProducts]       = useState<ProductRow[]>([])
  const [attribution, setAttribution]   = useState<Attribution | null>(null)
  const [insights, setInsights]         = useState<Insight[]>([])
  const [insightsLoading, setInsLoading] = useState(false)
  const [generating, setGenerating]     = useState(false)
  const [retention, setRetention]       = useState<RetentionData | null>(null)
  // heatmap is derived from allOrdersRaw — no separate state needed

  const [cohortData, setCohortData]     = useState<CohortMonth[]>([])
  const [channelRevenue, setChannelRevenue] = useState<ChannelRevenue[]>([])
  const [pacing, setPacing]             = useState<PacingData | null>(null)
  const [loading, setLoading]           = useState(true)
  const [lastUpdate, setLastUpdate]     = useState<Date>(new Date())
  const [clientName, setClientName]     = useState<string>('')
  const { period, from, to, setPreset, setCustom } = useDatePeriod()
  const [device,   setDevice]           = useState<string>('all')
  const [annotations, setAnnotations]   = useState<Array<{ id: string; date: string; label: string }>>([])
  const [annoOpen,  setAnnoOpen]        = useState(false)
  const [annoDate,  setAnnoDate]        = useState('')
  const [annoLabel, setAnnoLabel]       = useState('')
  const [country,  setCountry]          = useState<string>('all')
  const [drilldown, setDrilldown]       = useState<DrilldownKPI | null>(null)
  const [allOrdersRaw, setAllOrdersRaw] = useState<any[]>([])
  const [allEventsRaw, setAllEventsRaw] = useState<any[]>([])
  const [ga4Summary, setGa4Summary]     = useState<{ sessions: number; users: number; conversions: number; revenue: number } | null>(null)
  const [activeTab, setActiveTab]       = useState<Tab>('overview')
  const [loadedTabs, setLoadedTabs]     = useState<Set<Tab>>(new Set(['overview']))
  const [metaSummary, setMetaSummary]     = useState<AdsTotals | null>(null)
  const [googleSummary, setGoogleSummary] = useState<AdsTotals | null>(null)
  const [tiktokSummary, setTiktokSummary]       = useState<AdsTotals | null>(null)
  const [pinterestSummary, setPinterestSummary] = useState<AdsTotals | null>(null)
  const [ga4Channels, setGa4Channels]   = useState<Ga4Channel[]>([])
  const [ga4Funnel, setGa4Funnel]       = useState<Ga4Funnel | null>(null)
  const [ga4TopPages, setGa4TopPages]   = useState<Ga4TopPage[]>([])
  const [ltvStats, setLtvStats]         = useState<LtvStats | null>(null)
  const [refundsSummary, setRefundsSummary] = useState<{ count: number; total: number; rate_pct: number } | null>(null)

  // Derived from allOrdersRaw + country filter — recomputes only when raw data
  // or country changes, not on every unrelated setState (insights, pacing, etc.)
  const heatmap = useMemo(() => {
    const grid: number[][] = Array.from({ length: 7 }, () => Array(8).fill(0))
    const filtered = country === 'all'
      ? allOrdersRaw
      : allOrdersRaw.filter((o: any) => o.shipping_country === country)
    filtered.forEach((o: any) => {
      if (!o.created_at) return
      const d     = new Date(o.created_at)
      const day   = d.getDay()
      const block = Math.min(Math.floor(d.getHours() / 3), 7)
      grid[day][block] += 1
    })
    return grid
  }, [allOrdersRaw, country])
  const [productSort, setProductSort]         = useState<'purchases' | 'views'>('purchases')

  const params = useParams()
  // Memoized so loadData's useCallback dep array stays stable across unrelated renders
  const CLIENT_PIXEL_ID = useMemo(
    () => (params?.clientId as string) || process.env.NEXT_PUBLIC_CLIENT_PIXEL_ID || 'lk-sneakers',
    [params?.clientId],
  )

  // Cache clientId so we don't hit Supabase on every filter change
  const clientIdRef = useRef<string | null>(null)

  // Reset cached UUID whenever the client changes (navigation between clients)
  useEffect(() => {
    clientIdRef.current = null
    setMetaSummary(null)
    setGoogleSummary(null)
    setTiktokSummary(null)
    setPinterestSummary(null)
    setGa4Summary(null)
    setKpis(null)
  }, [CLIENT_PIXEL_ID])

  const loadData = useCallback(async () => {
    if (period === 'custom' && (!from || !to)) return
    setLoading(true)

    // ── Resolve clientId (cached after first call) ────────────────────────────
    let clientId = clientIdRef.current
    if (!clientId) {
      const isUUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(CLIENT_PIXEL_ID)
      const { data: clientData } = isUUID
        ? await supabase.from('clients').select('id, name').eq('id', CLIENT_PIXEL_ID).limit(1).single()
        : await supabase.from('clients').select('id, name').eq('pixel_id', CLIENT_PIXEL_ID).limit(1).single()
      if (!clientData) { setLoading(false); return }
      clientId = clientData.id
      clientIdRef.current = clientId
      if (clientData.name) setClientName(clientData.name)
    }

    // ── Date range computation ────────────────────────────────────────────────
    const now = new Date()
    let startDate: Date, endDate: Date
    if (period === 'custom' && from && to) {
      startDate = new Date(from + 'T00:00:00')
      endDate   = new Date(to   + 'T23:59:59')
    } else if (period === '1d') {
      startDate = new Date(now); startDate.setDate(startDate.getDate() - 1); startDate.setHours(0, 0, 0, 0)
      endDate   = new Date(startDate); endDate.setHours(23, 59, 59, 999)
    } else {
      const d = period === '7d' ? 7 : period === '30d' ? 30 : 90
      startDate = new Date(); startDate.setDate(startDate.getDate() - d)
      endDate   = now
    }
    const days      = Math.max(1, Math.ceil((endDate.getTime() - startDate.getTime()) / 86400000))
    const prevStart = new Date(startDate); prevStart.setDate(prevStart.getDate() - days)

    const startStr     = startDate.toISOString().split('T')[0]
    const endStr       = endDate.toISOString().split('T')[0]
    const prevEndStr   = new Date(startDate.getTime() - 1).toISOString().split('T')[0]
    const prevStartStr = prevStart.toISOString().split('T')[0]
    const dParam       = device !== 'all' ? `&device=${device}` : ''

    // ── Single parallel batch — all Supabase direct, no Railway hops ────────
    const [
      { data: orders },
      { data: ordersPrev },
      { data: productEvents },
      funnelResult,
      prevFunnelResult,
      nrResult,
      { data: refundOrders },
    ] = await Promise.all([
      supabase.from('orders')
        .select('id, email, total_price, gross_profit, margin_pct, financial_status, platform_source, utm_source, utm_medium, utm_campaign, is_first_purchase, shipping_country, created_at')
        .eq('client_id', clientId)
        .eq('financial_status', 'paid')
        .gt('total_price', 0)
        .gte('created_at', startDate.toISOString())
        .lte('created_at', endDate.toISOString())
        .order('created_at', { ascending: false })
        .limit(5000),
      supabase.from('orders')
        .select('total_price, created_at')
        .eq('client_id', clientId)
        .eq('financial_status', 'paid')
        .gt('total_price', 0)
        .gte('created_at', prevStart.toISOString())
        .lt('created_at', startDate.toISOString())
        .limit(5000),
      supabase.from('tracking_events')
        .select('event_type, product_name')
        .eq('client_id', clientId)
        .gte('created_at', startDate.toISOString())
        .lte('created_at', endDate.toISOString())
        .not('product_name', 'is', null)
        .limit(2000),
      Promise.resolve(supabase.rpc('funnel_stats', {
        p_client_id: clientId,
        p_start:     startDate.toISOString(),
        p_end:       endDate.toISOString(),
        p_device:    device !== 'all' ? device : null,
      })).catch(() => ({ data: null })),
      Promise.resolve(supabase.rpc('funnel_stats', {
        p_client_id: clientId,
        p_start:     prevStart.toISOString(),
        p_end:       new Date(startDate.getTime() - 1).toISOString(),
        p_device:    device !== 'all' ? device : null,
      })).catch(() => ({ data: null })),
      Promise.resolve(supabase.rpc('new_returning_stats', {
        p_client_id: clientId,
        p_start:     startDate.toISOString(),
        p_end:       endDate.toISOString(),
      })).catch(() => ({ data: null })),
      supabase.from('orders')
        .select('total_price')
        .eq('client_id', clientId)
        .eq('financial_status', 'refunded')
        .gte('created_at', startDate.toISOString())
        .lte('created_at', endDate.toISOString())
        .limit(500),
    ])

    const funnelJson     = (funnelResult     as any)?.data ?? null
    const prevFunnelJson = (prevFunnelResult as any)?.data ?? null

    const totalVisitors = funnelJson?.unique_visitors     ?? 0
    const prevVisitors  = prevFunnelJson?.unique_visitors ?? 0

    setAllOrdersRaw(orders || [])
    setAllEventsRaw([])

    // Country filter applies to orders only (visitors don't have a known country until checkout)
    const filterOrder = (o: any) => country === 'all' || o.shipping_country === country

    const allOrders  = (orders || []).filter(filterOrder)
    const prevOrders = ordersPrev || []
    const prodEvents = (productEvents || [])

    // ── KPIs ─────────────────────────────────────────────────────────────────
    const totalRevenue  = allOrders.reduce((s, o) => s + (o.total_price || 0), 0)
    const avgOrderValue = allOrders.length ? totalRevenue / allOrders.length : 0
    const conversionRate = totalVisitors > 0 ? (allOrders.length / totalVisitors) * 100 : 0

    // Margin (only counted for orders that have COGS — partial coverage = honest reporting)
    const ordersWithMargin = allOrders.filter((o: any) => o.gross_profit != null)
    const totalProfit = ordersWithMargin.length
      ? ordersWithMargin.reduce((s: number, o: any) => s + (o.gross_profit || 0), 0)
      : null
    const revenueWithMargin = ordersWithMargin.reduce((s: number, o: any) => s + (o.total_price || 0), 0)
    const marginPct = revenueWithMargin > 0 && totalProfit != null
      ? (totalProfit / revenueWithMargin) * 100
      : null

    const prevRevenue        = prevOrders.reduce((s, o: any) => s + (o.total_price || 0), 0)
    const prevAvgOrderValue  = prevOrders.length ? prevRevenue / prevOrders.length : 0
    const prevConversionRate = prevVisitors > 0 ? (prevOrders.length / prevVisitors) * 100 : 0

    setKpis({
      totalRevenue, totalOrders: allOrders.length, totalVisitors, avgOrderValue,
      revenueChange:       prevRevenue        > 0 ? ((totalRevenue            - prevRevenue)        / prevRevenue)        * 100 : 0,
      ordersChange:        prevOrders.length  > 0 ? ((allOrders.length        - prevOrders.length)  / prevOrders.length)  * 100 : 0,
      visitorsChange:      prevVisitors       > 0 ? ((totalVisitors           - prevVisitors)        / prevVisitors)       * 100 : 0,
      avgOrderValueChange: prevAvgOrderValue  > 0 ? ((avgOrderValue           - prevAvgOrderValue)   / prevAvgOrderValue)  * 100 : 0,
      conversionRateChange: prevConversionRate > 0 ? ((conversionRate         - prevConversionRate)  / prevConversionRate) * 100 : 0,
      conversionRate,
      totalProfit,
      marginPct,
    })

    setRecentOrders(allOrders.slice(0, 6) as Order[])

    // ── Revenue chart ─────────────────────────────────────────────────────────
    const chartDays = days  // Bug fix: chart covers full selected period
    const byDay: Record<string, { revenue: number; orders: number }> = {}
    allOrders.forEach(o => {
      const day = fmtDate(o.created_at)
      if (!byDay[day]) byDay[day] = { revenue: 0, orders: 0 }
      byDay[day].revenue += o.total_price || 0
      byDay[day].orders  += 1
    })
    const points: RevenuePoint[] = []
    for (let i = chartDays - 1; i >= 0; i--) {
      const d = new Date(); d.setDate(d.getDate() - i)
      const label = fmtDate(d.toISOString())
      points.push({ date: label, ...(byDay[label] || { revenue: 0, orders: 0 }) })
    }
    // Overlay prev period shifted forward by `days` for comparison line
    const prevByDay: Record<string, number> = {}
    prevOrders.forEach((o: any) => {
      if (!o.created_at) return
      const shifted = new Date(o.created_at)
      shifted.setDate(shifted.getDate() + days)
      const key = fmtDate(shifted.toISOString())
      prevByDay[key] = (prevByDay[key] || 0) + (o.total_price || 0)
    })
    setRevenueData(points.map(p => ({ ...p, prevRevenue: prevByDay[p.date] ?? 0 })))

    // ── Refunds summary ───────────────────────────────────────────────────────
    if (refundOrders && refundOrders.length > 0) {
      const refTotal = refundOrders.reduce((s: number, r: any) => s + (r.total_price || 0), 0)
      const revForRate = allOrders.reduce((s, o) => s + (o.total_price || 0), 0)
      setRefundsSummary({ count: refundOrders.length, total: refTotal, rate_pct: revForRate > 0 ? (refTotal / revForRate) * 100 : 0 })
    } else {
      setRefundsSummary(null)
    }

    // ── Conversion funnel — Supabase RPC funnel_stats returns fields directly
    const fd   = funnelJson || {}
    const fTop = fd.pageview || 1
    setFunnelSteps([
      { label: 'Pageviews',         count: fd.pageview       || 0, pct: 100 },
      { label: 'Produto Visto',     count: fd.view_product   || 0, pct: ((fd.view_product   || 0) / fTop) * 100 },
      { label: 'Add ao Carrinho',   count: fd.add_to_cart    || 0, pct: ((fd.add_to_cart    || 0) / fTop) * 100 },
      { label: 'Checkout Iniciado', count: fd.begin_checkout || 0, pct: ((fd.begin_checkout || 0) / fTop) * 100 },
      { label: 'Compras',           count: fd.purchase       || allOrders.length, pct: ((fd.purchase || allOrders.length) / fTop) * 100 },
    ])

    // ── Campaign attribution table ────────────────────────────────────────────
    const campMap: Record<string, { orders: number; revenue: number }> = {}
    allOrders.forEach((o: any) => {
      const key = [
        o.utm_source   || 'direto',
        o.utm_medium   || '—',
        o.utm_campaign || '—',
      ].join('|||')
      if (!campMap[key]) campMap[key] = { orders: 0, revenue: 0 }
      campMap[key].orders  += 1
      campMap[key].revenue += o.total_price || 0
    })
    setCampaigns(
      Object.entries(campMap).map(([key, v]) => {
        const [source, medium, campaign] = key.split('|||')
        return {
          source, medium, campaign,
          orders: v.orders, revenue: v.revenue,
          pctRevenue: totalRevenue ? (v.revenue / totalRevenue) * 100 : 0,
          avgTicket:  v.orders ? v.revenue / v.orders : 0,
        }
      }).sort((a, b) => b.revenue - a.revenue)
    )

    // ── Revenue by channel ────────────────────────────────────────────────────
    const chMap: Record<string, { revenue: number; orders: number }> = {}
    allOrders.forEach((o: any) => {
      const ch = classifyChannel(o.utm_source, o.utm_medium)
      if (!chMap[ch]) chMap[ch] = { revenue: 0, orders: 0 }
      chMap[ch].revenue += o.total_price || 0
      chMap[ch].orders  += 1
    })
    setChannelRevenue(
      Object.entries(chMap)
        .map(([channel, v]) => ({
          channel,
          revenue:    v.revenue,
          orders:     v.orders,
          pct:        totalRevenue > 0 ? (v.revenue / totalRevenue * 100) : 0,
          colorBar:   CHANNEL_COLORS[channel]?.bar   ?? 'bg-indigo-400',
          colorBadge: CHANNEL_COLORS[channel]?.badge ?? 'bg-indigo-500/15 text-indigo-300 border border-indigo-500/25',
        }))
        .sort((a, b) => b.revenue - a.revenue)
    )

    // ── Attribution quality ───────────────────────────────────────────────────
    setAttribution({
      ordersWithUtm:   allOrders.filter((o: any) => o.utm_source).length,
      ordersWithEmail: allOrders.filter((o: any) => o.email).length,
      total:           allOrders.length,
    })

    // ── Product performance ───────────────────────────────────────────────────
    const prodMap: Record<string, { views: number; cartAdds: number; purchases: number }> = {}
    prodEvents.forEach((e: any) => {
      const name = e.product_name
      if (!name) return
      if (!prodMap[name]) prodMap[name] = { views: 0, cartAdds: 0, purchases: 0 }
      if (e.event_type === 'view_product') prodMap[name].views    += 1
      if (e.event_type === 'add_to_cart')  prodMap[name].cartAdds += 1
      if (e.event_type === 'purchase')     prodMap[name].purchases += 1
    })
    setProducts(
      Object.entries(prodMap)
        .map(([name, v]) => ({ name, ...v }))
        .sort((a, b) => b.views - a.views)
        .slice(0, 8)
    )

    // ── Retention: novos vs recorrentes (resultado já veio no batch paralelo)
    let newOrders = allOrders.length, returningOrders = 0
    try {
      const nrData = (nrResult as any)?.data
      if (nrData) {
        newOrders       = Number(nrData.new_orders       ?? allOrders.length)
        returningOrders = Number(nrData.returning_orders ?? 0)
      }
    } catch (_) {}
    setRetention({ newOrders, returningOrders, total: allOrders.length })

    setLastUpdate(new Date())
    setLoading(false)
  }, [country, device, CLIENT_PIXEL_ID, period, from, to])

  const loadInsights = useCallback(async () => {
    setInsLoading(true)
    let cid = clientIdRef.current
    if (!cid) {
      const { data: cd } = await supabase.from('clients').select('id').eq('pixel_id', CLIENT_PIXEL_ID).limit(1).single()
      if (cd) { cid = cd.id; clientIdRef.current = cid }
    }
    const q = supabase
      .from('ai_insights')
      .select('id, type, severity, title, content, data, is_read, created_at')
      .eq('is_read', false)
      .order('created_at', { ascending: false })
      .limit(10)
    if (cid) q.eq('client_id', cid)
    const { data } = await q
    setInsights((data as Insight[]) || [])
    setInsLoading(false)
  }, [CLIENT_PIXEL_ID])

  const generateInsights = useCallback(async () => {
    setGenerating(true)
    try {
      await fetch(`${API_URL}/insights/${CLIENT_PIXEL_ID}/generate`, { method: 'POST' })
      // Poll for new insights (generation takes ~5-10s)
      await new Promise(r => setTimeout(r, 8000))
      await loadInsights()
    } finally {
      setGenerating(false)
    }
  }, [loadInsights])


  const loadCohort = useCallback(async () => {
    if (!CLIENT_PIXEL_ID) return
    // Use cached clientId; fall back to a lookup only if not yet available
    let cohortClientId = clientIdRef.current
    if (!cohortClientId) {
      const { data: clientData } = await supabase
        .from('clients').select('id').eq('pixel_id', CLIENT_PIXEL_ID).limit(1).single()
      if (!clientData) return
      cohortClientId = clientData.id
      clientIdRef.current = cohortClientId
    }

    // Fetch 90d of orders with is_first_purchase + email
    const start90 = new Date(); start90.setDate(start90.getDate() - 90)
    const { data: allOrders90 } = await supabase
      .from('orders')
      .select('email, created_at')
      .eq('client_id', cohortClientId)
      .eq('financial_status', 'paid')
      .gt('total_price', 0)
      .gte('created_at', start90.toISOString())

    if (!allOrders90) return

    // Email-first-order map — treats first order by each email as the "new buyer" event
    const emailFirst: Record<string, string> = {}
    allOrders90.forEach((o: any) => {
      if (!o.email || !o.created_at) return
      if (!emailFirst[o.email] || o.created_at < emailFirst[o.email]) emailFirst[o.email] = o.created_at
    })

    // Build 3 monthly cohorts
    const months: CohortMonth[] = []
    for (let m = 2; m >= 0; m--) {
      const mStart = new Date(); mStart.setDate(1); mStart.setMonth(mStart.getMonth() - m); mStart.setHours(0,0,0,0)
      const mEnd   = new Date(mStart); mEnd.setMonth(mEnd.getMonth() + 1)
      const label  = mStart.toLocaleDateString('pt-BR', { month: 'short', year: '2-digit' })

      // New buyers = emails whose first-ever order was in this month
      const newEmails = new Set(
        Object.entries(emailFirst)
          .filter(([_, d]) => d >= mStart.toISOString() && d < mEnd.toISOString())
          .map(([email]) => email)
      )

      // Returned = those new buyers who placed any order after this month
      const returned = allOrders90.filter((o: any) =>
        o.created_at >= mEnd.toISOString() &&
        o.email && newEmails.has(o.email)
      )
      const retEmails = new Set(returned.map(o => o.email))

      months.push({
        label,
        newBuyers: newEmails.size,
        returned:  retEmails.size,
        retPct:    newEmails.size > 0 ? Math.round((retEmails.size / newEmails.size) * 100) : 0,
      })
    }
    setCohortData(months)
  }, [])

  const dismissInsight = useCallback(async (insightId: string) => {
    setInsights(prev => prev.filter(i => i.id !== insightId))
    await fetch(`${API_URL}/insights/${CLIENT_PIXEL_ID}/${insightId}/read`, { method: 'PATCH' })
  }, [CLIENT_PIXEL_ID])

  const loadPacing = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/pacing/${CLIENT_PIXEL_ID}`)
      if (res.ok) setPacing(await res.json())
    } catch (_) {}
  }, [])

  const loadAnnotations = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/annotations/${CLIENT_PIXEL_ID}`)
      if (res.ok) setAnnotations((await res.json()).annotations || [])
    } catch (_) {}
  }, [CLIENT_PIXEL_ID])

  const loadGA4 = useCallback(async () => {
    if (period === 'custom' && (!from || !to)) return
    try {
      const qs = periodToQuery(period, from, to)
      const res = await fetch(`${API_URL}/ga4/${CLIENT_PIXEL_ID}/report?${qs}`)
      if (!res.ok) { setGa4Summary(null); return }
      const data = await res.json()
      setGa4Summary(data.summary ?? null)
      setGa4Channels(data.by_channel || [])
    } catch {
      setGa4Summary(null)
      setGa4Channels([])
    }
  }, [CLIENT_PIXEL_ID, period, from, to])

  const loadAdsAbortRef = useRef<AbortController | null>(null)

  const loadAds = useCallback(async () => {
    if (!CLIENT_PIXEL_ID) return
    if (period === 'custom' && (!from || !to)) return

    // Cancel any in-flight request to avoid stale overwrites
    if (loadAdsAbortRef.current) loadAdsAbortRef.current.abort()
    const controller = new AbortController()
    loadAdsAbortRef.current = controller

    const qs = periodToQuery(period, from, to)
    const [metaRes, googleRes, tiktokRes, pinterestRes] = await Promise.all([
      fetch(`${API_URL}/meta-ads/${CLIENT_PIXEL_ID}/overview?${qs}`, { signal: controller.signal }).catch(() => null),
      fetch(`${API_URL}/google-ads/${CLIENT_PIXEL_ID}/overview?${qs}`, { signal: controller.signal }).catch(() => null),
      fetch(`${API_URL}/tiktok-ads/${CLIENT_PIXEL_ID}/overview?${qs}`, { signal: controller.signal }).catch(() => null),
      fetch(`${API_URL}/pinterest-ads/${CLIENT_PIXEL_ID}/overview?${qs}`, { signal: controller.signal }).catch(() => null),
    ])

    if (controller.signal.aborted) return

    if (metaRes?.ok) {
      const data = await metaRes.json()
      const t = data.totals
      if (t) setMetaSummary({ spend: t.spend ?? 0, roas: t.roas ?? null, cpa: t.cpa ?? null, purchases: t.purchases ?? 0, revenue: t.revenue ?? 0 })
    } else {
      setMetaSummary(null)
    }
    if (googleRes?.ok) {
      const data = await googleRes.json()
      const t = data.totals
      if (t) setGoogleSummary({ spend: t.spend ?? 0, roas: t.roas ?? null, cpa: t.cpa ?? null, purchases: t.orders ?? t.purchases ?? 0, revenue: t.revenue ?? 0 })
    } else {
      setGoogleSummary(null)
    }
    if (tiktokRes?.ok) {
      const data = await tiktokRes.json()
      const t = data.totals
      if (t && (t.orders > 0 || t.spend > 0)) setTiktokSummary({ spend: t.spend ?? 0, roas: t.roas ?? null, cpa: t.cpa ?? null, purchases: t.orders ?? 0, revenue: t.revenue ?? 0 })
      else setTiktokSummary(null)
    } else {
      setTiktokSummary(null)
    }
    if (pinterestRes?.ok) {
      const data = await pinterestRes.json()
      const t = data.totals
      if (t && (t.orders > 0 || t.spend > 0)) setPinterestSummary({ spend: t.spend ?? 0, roas: t.roas ?? null, cpa: t.cpa ?? null, purchases: t.orders ?? 0, revenue: t.revenue ?? 0 })
      else setPinterestSummary(null)
    } else {
      setPinterestSummary(null)
    }
  }, [CLIENT_PIXEL_ID, period, from, to])

  const loadTrafficTab = useCallback(async () => {
    if (!CLIENT_PIXEL_ID) return
    const qs = periodToQuery(period, from, to)
    const [funnelRes, pagesRes] = await Promise.all([
      fetch(`${API_URL}/ga4/${CLIENT_PIXEL_ID}/funnel?${qs}`).catch(() => null),
      fetch(`${API_URL}/ga4/${CLIENT_PIXEL_ID}/top-pages?${qs}`).catch(() => null),
    ])
    if (funnelRes?.ok) {
      const data = await funnelRes.json()
      // API returns { summary: {...}, by_channel: [...] } — extract summary
      setGa4Funnel(data?.summary ?? null)
    }
    if (pagesRes?.ok) {
      const data = await pagesRes.json()
      setGa4TopPages(Array.isArray(data) ? data : (data.pages || []))
    }
  }, [CLIENT_PIXEL_ID, period, from, to])

  const loadClientsTab = useCallback(async () => {
    if (!CLIENT_PIXEL_ID) return
    let cid = clientIdRef.current
    if (!cid) {
      const isUUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(CLIENT_PIXEL_ID)
      const { data: cd } = isUUID
        ? await supabase.from('clients').select('id').eq('id', CLIENT_PIXEL_ID).limit(1).single()
        : await supabase.from('clients').select('id').eq('pixel_id', CLIENT_PIXEL_ID).limit(1).single()
      if (!cd) return
      cid = cd.id
      clientIdRef.current = cid
    }
    const { data: allOrders } = await supabase
      .from('orders')
      .select('email, total_price, created_at')
      .eq('client_id', cid)
      .eq('financial_status', 'paid')
      .gt('total_price', 0)
      .not('email', 'is', null)
      .order('created_at', { ascending: false })
      .limit(10000)
    if (!allOrders) return
    const custMap: Record<string, { total: number; orders: number; lastOrder: string }> = {}
    allOrders.forEach((o: any) => {
      if (!o.email) return
      if (!custMap[o.email]) custMap[o.email] = { total: 0, orders: 0, lastOrder: o.created_at }
      custMap[o.email].total += o.total_price || 0
      custMap[o.email].orders += 1
    })
    const customers = Object.entries(custMap).map(([email, v]) => ({ email, ...v }))
    const totalRev = customers.reduce((s, c) => s + c.total, 0)
    const avgLtv = customers.length > 0 ? totalRev / customers.length : 0
    const sixtyDaysAgo = new Date(); sixtyDaysAgo.setDate(sixtyDaysAgo.getDate() - 60)
    const atRisk = customers.filter(c => new Date(c.lastOrder) < sixtyDaysAgo).length
    const topCustomers = [...customers].sort((a, b) => b.total - a.total).slice(0, 5).map(c => ({ email: c.email, total: c.total, orders: c.orders }))
    setLtvStats({ avgLtv, topCustomers, atRisk, totalCustomers: customers.length })
  }, [CLIENT_PIXEL_ID])

  const handleTabChange = useCallback((tab: Tab) => {
    setActiveTab(tab)
    setLoadedTabs(prev => new Set([...prev, tab]))
  }, [])

  async function addAnnotation() {
    if (!annoDate || !annoLabel.trim()) return
    await fetch(`${API_URL}/annotations/${CLIENT_PIXEL_ID}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date: annoDate, label: annoLabel.trim() }),
    })
    setAnnoLabel(''); setAnnoDate(''); setAnnoOpen(false)
    loadAnnotations()
  }

  async function delAnnotation(id: string) {
    await fetch(`${API_URL}/annotations/${CLIENT_PIXEL_ID}/${id}`, { method: 'DELETE' })
    setAnnotations(prev => prev.filter(a => a.id !== id))
  }

  useEffect(() => { loadData() }, [loadData])
  useEffect(() => { loadInsights() }, [loadInsights])
  useEffect(() => { loadCohort() }, [loadCohort])
  useEffect(() => { loadPacing() }, [loadPacing])
  useEffect(() => { loadAnnotations() }, [loadAnnotations])
  useEffect(() => { loadGA4() }, [loadGA4])
  useEffect(() => { loadAds() }, [loadAds])
  useEffect(() => {
    if (activeTab === 'traffic') loadTrafficTab()
  }, [activeTab, loadTrafficTab])
  useEffect(() => {
    if (activeTab === 'clients') loadClientsTab()
  }, [activeTab, loadClientsTab])

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">

      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white">{clientName || CLIENT_PIXEL_ID}</h1>
          <p className="text-xs text-slate-500">Tracking Dashboard</p>
        </div>
        <div className="flex items-center gap-3">
          <select
            value={device}
            onChange={e => setDevice(e.target.value)}
            className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-3 py-1.5 text-xs text-slate-200 outline-none focus:border-indigo-500"
            title="Filtrar por dispositivo"
          >
            <option value="all">Todos dispositivos</option>
            <option value="mobile">Mobile</option>
            <option value="desktop">Desktop</option>
            <option value="tablet">Tablet</option>
          </select>
          <select
            value={country}
            onChange={e => setCountry(e.target.value)}
            className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-3 py-1.5 text-xs text-slate-200 outline-none focus:border-indigo-500"
            title="Filtrar por país"
          >
            <option value="all">Todos países</option>
            {Array.from(new Set(allOrdersRaw.map(o => o.shipping_country).filter(Boolean))).map(c => (
              <option key={c as string} value={c as string}>{c as string}</option>
            ))}
          </select>
          <PeriodPicker period={period} from={from} to={to} onPreset={setPreset} onCustom={setCustom} />
          <button onClick={() => loadData()}
            className="flex items-center gap-2 text-xs text-slate-400 hover:text-white transition-colors">
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            {lastUpdate.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}
          </button>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="border-b border-[#2a2f3e] px-6">
        <nav className="flex gap-1">
          {([
            { id: 'overview' as Tab, label: 'Visão Geral' },
            { id: 'traffic'   as Tab, label: 'Tráfego & SEO' },
            { id: 'campaigns' as Tab, label: 'Campanhas' },
            { id: 'clients'   as Tab, label: 'Clientes' },
          ]).map(tab => (
            <button
              key={tab.id}
              onClick={() => handleTabChange(tab.id)}
              className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors -mb-px ${
                activeTab === tab.id
                  ? 'border-indigo-500 text-indigo-400'
                  : 'border-transparent text-slate-500 hover:text-slate-300'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      <div className="p-6 space-y-6">

        {/* ── OVERVIEW TAB ─────────────────────────────────────────────────── */}
        {activeTab === 'overview' && <>

        {CLIENT_PIXEL_ID && <IntegrationsHealth pixelId={CLIENT_PIXEL_ID} />}

        {pacing && pacing.monthly_revenue_goal && (
          <PacingWidget pacing={pacing} />
        )}

        {/* KPIs — clicáveis abrem drilldown */}
        <div className="grid grid-cols-2 lg:grid-cols-6 gap-4">
          <KPICard title="Receita"      value={kpis ? fmt(kpis.totalRevenue) : '—'}
            icon={TrendingUp} change={kpis?.revenueChange}
            spark={revenueData.map(p => p.revenue)}
            color="bg-emerald-500/10 text-emerald-400"
            hint={period === '1d' ? `⚠ parcial — ${Math.round((new Date().getHours() * 60 + new Date().getMinutes()) / 1440 * 100)}% do dia decorrido` : undefined}
            onClick={() => setDrilldown('revenue')} />
          {kpis?.totalProfit != null && (
            <KPICard
              title="Margem"
              value={fmt(kpis.totalProfit)}
              icon={TrendingUp}
              color="bg-teal-500/10 text-teal-400"
              hint={kpis.marginPct != null ? `${kpis.marginPct.toFixed(1)}% bruta` : undefined}
              onClick={() => setDrilldown('profit')}
            />
          )}
          <KPICard title="Pedidos"      value={kpis ? kpis.totalOrders.toString() : '—'}
            icon={ShoppingBag} change={kpis?.ordersChange}
            spark={revenueData.map(p => p.orders)}
            color="bg-blue-500/10 text-blue-400"
            onClick={() => setDrilldown('orders')} />
          <KPICard
            title={kpis?.totalVisitors === 0 && ga4Summary ? 'Sessões (GA4)' : 'Visitantes'}
            value={
              kpis?.totalVisitors === 0 && ga4Summary
                ? ga4Summary.sessions.toLocaleString('pt-BR')
                : (kpis ? kpis.totalVisitors.toLocaleString('pt-BR') : '—')
            }
            icon={Users}
            change={kpis?.visitorsChange}
            hint={
              kpis?.totalVisitors === 0 && ga4Summary
                ? 'via Google Analytics 4'
                : kpis?.totalVisitors === 0
                ? '⚠ Pixel sem dados · verificar tracking'
                : undefined
            }
            color="bg-purple-500/10 text-purple-400" />
          <KPICard title="Ticket Médio" value={kpis ? fmt(kpis.avgOrderValue) : '—'}
            icon={Activity} change={kpis?.avgOrderValueChange}
            spark={revenueData.map(p => p.orders > 0 ? p.revenue / p.orders : 0)}
            color="bg-orange-500/10 text-orange-400"
            onClick={() => setDrilldown('avgOrderValue')} />
          <KPICard
            title="Conversão"
            value={
              kpis?.totalVisitors === 0 && ga4Summary && ga4Summary.sessions > 0
                ? ((kpis.totalOrders / ga4Summary.sessions) * 100).toFixed(1) + '%'
                : kpis && kpis.totalVisitors === 0 && !ga4Summary
                ? '—'
                : (kpis ? kpis.conversionRate.toFixed(1) + '%' : '—')
            }
            icon={Percent}
            change={
              kpis?.totalVisitors === 0 && !ga4Summary ? undefined : kpis?.conversionRateChange
            }
            hint={
              kpis?.totalVisitors === 0 && ga4Summary
                ? 'pedidos ÷ sessões GA4'
                : kpis?.totalVisitors === 0
                ? 'Sem dados de sessões — verificar pixel'
                : undefined
            }
            color="bg-pink-500/10 text-pink-400" />
        </div>

        {/* Ads Summary — Meta + Google + TikTok + Pinterest */}
        {(metaSummary || googleSummary || tiktokSummary || pinterestSummary) && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {metaSummary && (
              <div className="bg-[#1a1f2e] rounded-xl border border-blue-500/20 p-5">
                <div className="flex items-center gap-2 mb-4">
                  <div className="p-1.5 rounded bg-blue-500/10"><Target size={13} className="text-blue-400" /></div>
                  <span className="text-sm font-semibold text-slate-300">Meta Ads</span>
                  <span className="text-[10px] text-blue-400/70 bg-blue-500/10 border border-blue-500/20 px-1.5 py-0.5 rounded ml-auto">
                    {periodLabelLong(period, from, to)}
                  </span>
                </div>
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                  <div>
                    <p className="text-xs text-slate-500 mb-1">Gasto</p>
                    <p className="text-xl font-bold text-white">{fmt(metaSummary.spend)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">ROAS</p>
                    <p className={`text-xl font-bold ${(metaSummary.roas ?? 0) >= 3 ? 'text-emerald-400' : (metaSummary.roas ?? 0) >= 1.5 ? 'text-yellow-400' : 'text-red-400'}`}>
                      {metaSummary.roas != null ? `${metaSummary.roas.toFixed(2)}x` : '—'}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">CPA</p>
                    <p className="text-xl font-bold text-white">{metaSummary.cpa != null ? fmt(metaSummary.cpa) : '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">Compras</p>
                    <p className="text-xl font-bold text-white">{metaSummary.purchases}</p>
                  </div>
                </div>
              </div>
            )}
            {googleSummary && (
              <div className="bg-[#1a1f2e] rounded-xl border border-red-500/20 p-5">
                <div className="flex items-center gap-2 mb-4">
                  <div className="p-1.5 rounded bg-red-500/10"><Target size={13} className="text-red-400" /></div>
                  <span className="text-sm font-semibold text-slate-300">Google Ads</span>
                  <span className="text-[10px] text-red-400/70 bg-red-500/10 border border-red-500/20 px-1.5 py-0.5 rounded ml-auto">
                    {periodLabelLong(period, from, to)}
                  </span>
                </div>
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                  <div>
                    <p className="text-xs text-slate-500 mb-1">Gasto</p>
                    <p className="text-xl font-bold text-white">{fmt(googleSummary.spend)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">ROAS</p>
                    <p className={`text-xl font-bold ${(googleSummary.roas ?? 0) >= 3 ? 'text-emerald-400' : (googleSummary.roas ?? 0) >= 1.5 ? 'text-yellow-400' : 'text-red-400'}`}>
                      {googleSummary.roas != null ? `${googleSummary.roas.toFixed(2)}x` : '—'}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">CPA</p>
                    <p className="text-xl font-bold text-white">{googleSummary.cpa != null ? fmt(googleSummary.cpa) : '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">Pedidos</p>
                    <p className="text-xl font-bold text-white">{googleSummary.purchases}</p>
                  </div>
                </div>
              </div>
            )}
            {tiktokSummary && (
              <div className="bg-[#1a1f2e] rounded-xl border border-pink-500/20 p-5">
                <div className="flex items-center gap-2 mb-4">
                  <div className="p-1.5 rounded bg-pink-500/10">
                    <span className="text-pink-400 text-[11px] font-black leading-none">T</span>
                  </div>
                  <span className="text-sm font-semibold text-slate-300">TikTok Ads</span>
                  <span className="text-[10px] text-pink-400/70 bg-pink-500/10 border border-pink-500/20 px-1.5 py-0.5 rounded ml-auto">
                    {periodLabelLong(period, from, to)}
                  </span>
                </div>
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                  <div>
                    <p className="text-xs text-slate-500 mb-1">Gasto</p>
                    <p className="text-xl font-bold text-white">{tiktokSummary.spend > 0 ? fmt(tiktokSummary.spend) : '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">ROAS</p>
                    <p className={`text-xl font-bold ${(tiktokSummary.roas ?? 0) >= 3 ? 'text-emerald-400' : (tiktokSummary.roas ?? 0) >= 1.5 ? 'text-yellow-400' : 'text-slate-400'}`}>
                      {tiktokSummary.roas != null ? `${tiktokSummary.roas.toFixed(2)}x` : '—'}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">CPA</p>
                    <p className="text-xl font-bold text-white">{tiktokSummary.cpa != null ? fmt(tiktokSummary.cpa) : '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">Pedidos</p>
                    <p className="text-xl font-bold text-white">{tiktokSummary.purchases}</p>
                  </div>
                </div>
              </div>
            )}
            {pinterestSummary && (
              <div className="bg-[#1a1f2e] rounded-xl border border-rose-500/20 p-5">
                <div className="flex items-center gap-2 mb-4">
                  <div className="p-1.5 rounded bg-rose-500/10"><Target size={13} className="text-rose-400" /></div>
                  <span className="text-sm font-semibold text-slate-300">Pinterest Ads</span>
                  <span className="text-[10px] text-rose-400/70 bg-rose-500/10 border border-rose-500/20 px-1.5 py-0.5 rounded ml-auto">
                    {periodLabelLong(period, from, to)}
                  </span>
                </div>
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                  <div>
                    <p className="text-xs text-slate-500 mb-1">Gasto</p>
                    <p className="text-xl font-bold text-white">{pinterestSummary.spend > 0 ? fmt(pinterestSummary.spend) : '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">ROAS</p>
                    <p className={`text-xl font-bold ${(pinterestSummary.roas ?? 0) >= 3 ? 'text-emerald-400' : (pinterestSummary.roas ?? 0) >= 1.5 ? 'text-yellow-400' : 'text-slate-400'}`}>
                      {pinterestSummary.roas != null ? `${pinterestSummary.roas.toFixed(2)}x` : '—'}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">CPA</p>
                    <p className="text-xl font-bold text-white">{pinterestSummary.cpa != null ? fmt(pinterestSummary.cpa) : '—'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">Pedidos</p>
                    <p className="text-xl font-bold text-white">{pinterestSummary.purchases}</p>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* GA4 summary — só aparece quando ga4_reporting_enabled=true (API retorna 403 caso contrário) */}
        {ga4Summary && (
          <div className="rounded-xl border border-indigo-500/20 bg-indigo-500/5 px-5 py-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <BarChart2 size={13} className="text-indigo-400" />
                <span className="text-xs font-semibold text-slate-300">Google Analytics 4</span>
                <span className="text-[10px] text-indigo-400/70 bg-indigo-500/10 border border-indigo-500/20 px-1.5 py-0.5 rounded">fonte: GA4</span>
              </div>
              <Link href={`/clients/${CLIENT_PIXEL_ID}/ga4`}
                className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors">
                Ver relatório completo →
              </Link>
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <div>
                <p className="text-xs text-slate-500 mb-1">Sessões</p>
                <p className="text-xl font-bold text-white">{ga4Summary.sessions.toLocaleString('pt-BR')}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500 mb-1">Usuários</p>
                <p className="text-xl font-bold text-white">{ga4Summary.users.toLocaleString('pt-BR')}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500 mb-1">Conversões GA4</p>
                <p className="text-xl font-bold text-white">{ga4Summary.conversions.toLocaleString('pt-BR')}</p>
              </div>
              {ga4Summary.revenue > 0 ? (
                <div>
                  <p className="text-xs text-slate-500 mb-1">Receita GA4</p>
                  <p className="text-xl font-bold text-white">{fmt(ga4Summary.revenue)}</p>
                </div>
              ) : (
                <div>
                  <p className="text-xs text-slate-500 mb-1">Receita GA4</p>
                  <p className="text-xl font-bold text-slate-600">—</p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Revenue + Funnel */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2 bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-slate-300">
                Receita — {periodLabelLong(period, from, to).toLowerCase()}
              </h2>
              <div className="flex items-center gap-3">
                {period === '90d' && revenueData.filter(p => p.revenue > 0).length < 45 && (
                  <span className="text-xs text-slate-500">
                    Rastreamento desde {revenueData.find(p => p.revenue > 0)?.date ?? 'Abr/26'}
                  </span>
                )}
                <button onClick={() => setAnnoOpen(v => !v)} className="text-xs text-slate-400 hover:text-white">
                  📌 Marcar evento
                </button>
              </div>
            </div>
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={revenueData}>
                <defs>
                  <linearGradient id="colorRevenue" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2f3e" />
                <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 11 }} tickFormatter={fmtDateAxis} />
                <YAxis tick={{ fill: '#64748b', fontSize: 11 }} tickFormatter={v => `R$${v}`} />
                <Tooltip
                  contentStyle={{ background: '#1a1f2e', border: '1px solid #2a2f3e', borderRadius: 8 }}
                  formatter={(v, name) => [fmt(Number(v)), name === 'prevRevenue' ? 'Período anterior' : 'Receita']}
                />
                {annotations.filter(a => revenueData.some(p => p.date === a.date)).map(a => (
                  <ReferenceLine key={a.id} x={a.date} stroke="#f59e0b" strokeDasharray="4 4"
                    label={{ value: '📌', position: 'top', fontSize: 11 }} />
                ))}
                <Area type="monotone" dataKey="revenue" stroke="#10b981" fill="url(#colorRevenue)" strokeWidth={2} />
                {revenueData.some(p => (p.prevRevenue ?? 0) > 0) && (
                  <Line type="monotone" dataKey="prevRevenue" stroke="#475569" strokeWidth={1.5}
                    strokeDasharray="4 4" dot={false} name="Período anterior" />
                )}
              </AreaChart>
            </ResponsiveContainer>

            {annoOpen && (
              <div className="mt-3 flex items-center gap-2 flex-wrap">
                <input type="date" value={annoDate} onChange={e => setAnnoDate(e.target.value)}
                  className="bg-[#0f1117] border border-[#2a2f3e] rounded px-2 py-1 text-xs text-slate-200 outline-none focus:border-indigo-500" />
                <input type="text" value={annoLabel} onChange={e => setAnnoLabel(e.target.value)}
                  placeholder="ex.: Black Friday, mudamos o checkout…" maxLength={120}
                  className="flex-1 min-w-[180px] bg-[#0f1117] border border-[#2a2f3e] rounded px-2 py-1 text-xs text-slate-200 placeholder-slate-600 outline-none focus:border-indigo-500" />
                <button onClick={addAnnotation} disabled={!annoDate || !annoLabel.trim()}
                  className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 text-white text-xs px-3 py-1 rounded">Adicionar</button>
              </div>
            )}
            {annotations.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {annotations.map(a => (
                  <span key={a.id} className="inline-flex items-center gap-1.5 text-xs bg-amber-500/10 text-amber-300 border border-amber-500/20 rounded px-2 py-1">
                    📌 {a.date.slice(8, 10)}/{a.date.slice(5, 7)} · {a.label}
                    <button onClick={() => delAnnotation(a.id)} className="text-amber-400/60 hover:text-amber-300">×</button>
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">Funil de Conversão</h2>
            {funnelSteps.length === 0 || funnelSteps[0].count === 0 ? (
              <EmptyState
                type="setup"
                title="Funil via GA4 ou Shopify"
                description="Não recebemos eventos de sessão do pixel Noro neste período. O funil completo (sessões → carrinho → checkout → compra) está disponível no GA4."
                link={{ label: 'Ver funil GA4', href: `/clients/${CLIENT_PIXEL_ID}/ga4` }}
                compact
              />
            ) : <FunnelBar steps={funnelSteps} />}
          </div>
        </div>

        {/* Receita por Canal */}
        {channelRevenue.length > 0 && (
          <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-hidden">
            <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between flex-wrap gap-2">
              <div>
                <h2 className="text-sm font-semibold text-slate-300 flex items-center gap-2">
                  Receita Real por Canal
                  <ColHeader
                    label=""
                    right={false}
                    tooltip="Receita atribuída por UTM last-click no momento do checkout. Fonte: pedidos Shopify com utm_source. Difere da 'Receita Meta' (que usa janela de 7 dias + view-through) e da 'Receita Google Ads' (conversões reportadas). Use esta coluna para decisões de budget — é o ground truth."
                  />
                  <SourceBadge source="shopify" />
                </h2>
                <p className="text-xs text-slate-500 mt-0.5">Last-click attribution · utm_source no checkout · pedidos pagos no Shopify</p>
              </div>
              <div className="flex items-center gap-3 text-xs text-slate-500">
                <span>{channelRevenue.reduce((s, c) => s + c.orders, 0)} pedidos</span>
                <span className="text-slate-700">·</span>
                <span>{fmt(channelRevenue.reduce((s, c) => s + c.revenue, 0))} total</span>
              </div>
            </div>
            <div className="p-5 space-y-2.5">
              {channelRevenue.map(ch => (
                <div key={ch.channel} className="flex items-center gap-3">
                  {/* Channel badge */}
                  <span className={`text-xs font-medium px-2 py-0.5 rounded w-32 shrink-0 text-center whitespace-nowrap ${ch.colorBadge}`}>
                    {ch.channel}
                  </span>
                  {/* Bar */}
                  <div className="flex-1 h-5 bg-[#0f1117] rounded overflow-hidden">
                    <div
                      className={`h-full rounded transition-all duration-700 ${ch.colorBar} opacity-80`}
                      style={{ width: `${Math.max(ch.pct, ch.orders > 0 ? 1 : 0)}%` }}
                    />
                  </div>
                  {/* Revenue + % */}
                  <div className="shrink-0 w-40 text-right">
                    <span className="text-sm font-semibold text-white">{fmt(ch.revenue)}</span>
                    <span className="text-slate-500 text-xs ml-2 tabular-nums">{ch.pct.toFixed(0)}%</span>
                  </div>
                  {/* Orders */}
                  <div className="shrink-0 w-20 text-right text-xs text-slate-500 tabular-nums">
                    {ch.orders} pedido{ch.orders !== 1 ? 's' : ''}
                  </div>
                  {/* Avg ticket */}
                  <div className="shrink-0 w-24 text-right text-xs text-slate-600 tabular-nums hidden sm:block">
                    {fmt(ch.revenue / ch.orders)}/pedido
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Recent Orders + Refunds */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">Pedidos Recentes</h2>
            <div className="space-y-2 overflow-auto max-h-[280px]">
              {recentOrders.length === 0 ? (
                <EmptyState
                  type="neutral"
                  title="Nenhum pedido no período"
                  description="Sem pedidos pagos ou pendentes no intervalo selecionado."
                  compact
                />
              ) : recentOrders.map(order => (
                <div key={order.id} className="flex items-center justify-between py-2 border-b border-[#2a2f3e] last:border-0">
                  <div className="min-w-0">
                    <p className="text-sm text-slate-200 truncate">{order.email || '—'}</p>
                    <p className="text-xs text-slate-500">
                      {fmtDate(order.created_at)} ·{' '}
                      {order.utm_source ? <span className="text-indigo-400">{order.utm_source}</span> : 'direto'}
                    </p>
                  </div>
                  <div className="text-right ml-4 shrink-0">
                    <p className="text-sm font-medium text-emerald-400">{fmt(order.total_price)}</p>
                    <span className={`text-xs px-1.5 py-0.5 rounded ${order.financial_status === 'paid' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-yellow-500/10 text-yellow-400'}`}>
                      {order.financial_status || 'pendente'}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {refundsSummary ? (
            <div className="bg-[#1a1f2e] rounded-xl p-5 border border-orange-500/20">
              <div className="flex items-center gap-2 mb-4">
                <DollarSign size={13} className="text-orange-400" />
                <h2 className="text-sm font-semibold text-slate-300">Reembolsos</h2>
                <span className="text-[10px] text-orange-400/70 bg-orange-500/10 border border-orange-500/20 px-1.5 py-0.5 rounded ml-auto">{periodLabelLong(period, from, to)}</span>
              </div>
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <p className="text-xs text-slate-500 mb-1">Pedidos</p>
                  <p className="text-2xl font-bold text-orange-400">{refundsSummary.count}</p>
                </div>
                <div>
                  <p className="text-xs text-slate-500 mb-1">Total</p>
                  <p className="text-2xl font-bold text-white">{fmt(refundsSummary.total)}</p>
                </div>
                <div>
                  <p className="text-xs text-slate-500 mb-1">% Receita</p>
                  <p className={`text-2xl font-bold ${refundsSummary.rate_pct > 5 ? 'text-red-400' : refundsSummary.rate_pct > 2 ? 'text-yellow-400' : 'text-emerald-400'}`}>
                    {refundsSummary.rate_pct.toFixed(1)}%
                  </p>
                </div>
              </div>
            </div>
          ) : (
            <div className="p-0">
              <EmptyState
                type="neutral"
                title="Nenhum reembolso no período"
                description="Ótimo sinal — nenhum pedido foi reembolsado neste intervalo."
                compact
              />
            </div>
          )}
        </div>

        </>} {/* end overview tab */}

        {/* ── TRAFFIC & SEO TAB ───────────────────────────────────────────────── */}
        {activeTab === 'traffic' && <>
          {!ga4Summary ? (
            <EmptyState
              type="setup"
              title="GA4 não configurado"
              description="Configure a integração Google Analytics 4 nas configurações do cliente para visualizar dados de tráfego, funil e origem de visitantes."
              link={{ label: 'Ir para Configurações', href: `/clients/${CLIENT_PIXEL_ID}/settings` }}
            />
          ) : (
            <>
              {/* GA4 overview stats */}
              <div className="rounded-xl border border-indigo-500/20 bg-indigo-500/5 px-5 py-4">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <BarChart2 size={13} className="text-indigo-400" />
                    <span className="text-xs font-semibold text-slate-300">Google Analytics 4 — {periodLabelLong(period, from, to)}</span>
                  </div>
                  <Link href={`/clients/${CLIENT_PIXEL_ID}/ga4`} className="text-xs text-indigo-400 hover:text-indigo-300">
                    Relatório completo →
                  </Link>
                </div>
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                  <div><p className="text-xs text-slate-500 mb-1">Sessões</p><p className="text-2xl font-bold text-white">{ga4Summary.sessions.toLocaleString('pt-BR')}</p></div>
                  <div><p className="text-xs text-slate-500 mb-1">Usuários</p><p className="text-2xl font-bold text-white">{ga4Summary.users.toLocaleString('pt-BR')}</p></div>
                  <div><p className="text-xs text-slate-500 mb-1">Conversões</p><p className="text-2xl font-bold text-white">{ga4Summary.conversions.toLocaleString('pt-BR')}</p></div>
                  <div>
                    <p className="text-xs text-slate-500 mb-1">Receita GA4</p>
                    <p className="text-2xl font-bold text-white">{ga4Summary.revenue > 0 ? fmt(ga4Summary.revenue) : '—'}</p>
                  </div>
                </div>
              </div>

              {/* GA4 by channel */}
              {ga4Channels.length > 0 && (
                <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-hidden">
                  <div className="px-5 py-4 border-b border-[#2a2f3e]">
                    <h2 className="text-sm font-semibold text-slate-300">Tráfego por Canal (GA4)</h2>
                    <p className="text-xs text-slate-500 mt-0.5">Sessões, usuários e conversões por canal de aquisição</p>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-[#2a2f3e]">
                          {['Canal', 'Sessões', 'Usuários', 'Conversões', 'Receita'].map(h => (
                            <th key={h} className={`px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap ${h === 'Canal' ? 'text-left' : 'text-right'}`}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {ga4Channels.map((ch, i) => (
                          <tr key={i} className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a]">
                            <td className="px-4 py-3 text-slate-200 text-xs font-medium">{ch.channel}</td>
                            <td className="px-4 py-3 text-right text-slate-300 tabular-nums">{ch.sessions.toLocaleString('pt-BR')}</td>
                            <td className="px-4 py-3 text-right text-slate-400 tabular-nums">{ch.users.toLocaleString('pt-BR')}</td>
                            <td className="px-4 py-3 text-right text-indigo-400 tabular-nums font-medium">{ch.conversions.toLocaleString('pt-BR')}</td>
                            <td className="px-4 py-3 text-right text-emerald-400 font-semibold tabular-nums">{ch.revenue > 0 ? fmt(ch.revenue) : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* GA4 Funnel */}
              {ga4Funnel ? (
                <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] p-5">
                  <h2 className="text-sm font-semibold text-slate-300 mb-4">Funil de Conversão GA4</h2>
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
                    {[
                      { label: 'Sessões', value: ga4Funnel.sessions ?? 0, rate: null },
                      { label: 'Add ao Carrinho', value: ga4Funnel.add_to_cart ?? 0, rate: ga4Funnel.atc_rate ?? null },
                      { label: 'Checkout', value: ga4Funnel.begin_checkout ?? 0, rate: ga4Funnel.checkout_rate ?? null },
                      { label: 'Compras', value: ga4Funnel.purchases ?? 0, rate: ga4Funnel.purchase_rate ?? null },
                    ].map((step, i) => (
                      <div key={i} className="bg-[#0f1117] rounded-xl p-4">
                        <p className="text-xs text-slate-500 mb-2">{step.label}</p>
                        <p className="text-2xl font-bold text-white">{step.value.toLocaleString('pt-BR')}</p>
                        {step.rate != null && (
                          <p className={`text-xs mt-1 font-medium ${step.rate >= 3 ? 'text-emerald-400' : step.rate >= 1 ? 'text-yellow-400' : 'text-red-400'}`}>
                            {step.rate.toFixed(1)}% de conversão
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                  <div className="h-2 bg-[#0f1117] rounded-full overflow-hidden flex gap-0.5">
                    {[ga4Funnel.sessions, ga4Funnel.add_to_cart, ga4Funnel.begin_checkout, ga4Funnel.purchases].map((v, i) => {
                      const colors = ['bg-indigo-500', 'bg-purple-500', 'bg-pink-500', 'bg-emerald-500']
                      const pctW = ga4Funnel.sessions > 0 ? (v / ga4Funnel.sessions) * 100 : 0
                      return <div key={i} className={`h-full ${colors[i]} transition-all duration-700`} style={{ width: `${pctW}%` }} />
                    })}
                  </div>
                </div>
              ) : (
                <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] p-8 text-center">
                  <Loader2 size={20} className="text-slate-600 mx-auto mb-2 animate-spin" />
                  <p className="text-slate-500 text-sm">Carregando funil GA4…</p>
                </div>
              )}

              {/* GA4 Top Pages */}
              {ga4TopPages.length > 0 && (
                <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-hidden">
                  <div className="px-5 py-4 border-b border-[#2a2f3e]">
                    <h2 className="text-sm font-semibold text-slate-300">Top Páginas (GA4)</h2>
                    <p className="text-xs text-slate-500 mt-0.5">Páginas com mais sessões no período</p>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-[#2a2f3e]">
                          {['Página', 'Sessões', 'Conversões', 'Receita', 'Conv. %'].map(h => (
                            <th key={h} className={`px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap ${h === 'Página' ? 'text-left' : 'text-right'}`}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {ga4TopPages.slice(0, 10).map((pg, i) => (
                          <tr key={i} className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a]">
                            <td className="px-4 py-3 max-w-[260px]">
                              <p className="text-slate-200 text-xs truncate">{pg.title || pg.path}</p>
                              <p className="text-slate-600 text-xs truncate">{pg.path}</p>
                            </td>
                            <td className="px-4 py-3 text-right text-slate-300 tabular-nums">{pg.sessions.toLocaleString('pt-BR')}</td>
                            <td className="px-4 py-3 text-right text-indigo-400 tabular-nums">{pg.conversions.toLocaleString('pt-BR')}</td>
                            <td className="px-4 py-3 text-right text-emerald-400 font-semibold tabular-nums">{pg.revenue > 0 ? fmt(pg.revenue) : '—'}</td>
                            <td className="px-4 py-3 text-right text-slate-400 tabular-nums">{pg.conv_rate != null ? pg.conv_rate.toFixed(1) + '%' : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </>}

        {/* ── CAMPAIGNS TAB ───────────────────────────────────────────────────── */}
        {activeTab === 'campaigns' && <>
          {/* Campaign Attribution Table */}
          <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-hidden">
            <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-300">Atribuição de Campanhas</h2>
              {attribution && attribution.total > 0 && (
                <div className="flex items-center gap-4 text-xs">
                  <span className="flex items-center gap-1.5 text-slate-400">
                    <CheckCircle size={12} className={attribution.ordersWithUtm / attribution.total >= 0.5 ? 'text-emerald-400' : 'text-yellow-400'} />
                    {pct(attribution.ordersWithUtm, attribution.total)} com UTM
                  </span>
                  <span className="flex items-center gap-1.5 text-slate-400">
                    <CheckCircle size={12} className={attribution.ordersWithEmail / attribution.total >= 0.9 ? 'text-emerald-400' : 'text-yellow-400'} />
                    {pct(attribution.ordersWithEmail, attribution.total)} com email
                  </span>
                </div>
              )}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#2a2f3e]">
                    {['Origem', 'Mídia', 'Campanha', 'Pedidos', 'Receita', '% Total', 'Ticket Médio'].map(h => (
                      <th key={h} className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {campaigns.length === 0 ? (
                    <tr><td colSpan={7} className="py-8 text-center text-slate-500 text-sm">Sem dados no período</td></tr>
                  ) : campaigns.map((c, i) => (
                    <tr key={i} className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a] transition-colors">
                      <td className="px-4 py-3">
                        <span className={`text-xs px-2 py-0.5 rounded font-medium ${
                          c.source === 'direto' ? 'bg-slate-500/10 text-slate-400' :
                          ['facebook','instagram','meta'].includes(c.source) ? 'bg-blue-500/10 text-blue-400' :
                          c.source === 'google' ? 'bg-red-500/10 text-red-400' :
                          'bg-indigo-500/10 text-indigo-400'
                        }`}>{c.source}</span>
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-400">{c.medium !== '—' ? c.medium : <span className="text-slate-600">—</span>}</td>
                      <td className="px-4 py-3 text-xs text-slate-300 max-w-[180px]"><p className="truncate">{c.campaign !== '—' ? c.campaign : <span className="text-slate-600">—</span>}</p></td>
                      <td className="px-4 py-3 text-slate-200 font-medium">{c.orders}</td>
                      <td className="px-4 py-3 text-emerald-400 font-semibold whitespace-nowrap">{fmt(c.revenue)}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <div className="w-14 h-1.5 bg-[#0f1117] rounded overflow-hidden">
                            <div className="h-full bg-indigo-500 rounded" style={{ width: `${Math.min(c.pctRevenue, 100)}%` }} />
                          </div>
                          <span className="text-slate-400 text-xs">{c.pctRevenue.toFixed(0)}%</span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-slate-300 whitespace-nowrap">{fmt(c.avgTicket)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Heatmap de vendas */}
          {heatmap.length > 0 && <SalesHeatmap grid={heatmap} />}
        </>}

        {/* ── CLIENTS TAB ─────────────────────────────────────────────────────── */}
        {activeTab === 'clients' && <>

          {/* LTV + At-risk */}
          {ltvStats ? (
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <div className="bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
                <p className="text-xs text-slate-500 mb-1">LTV Médio</p>
                <p className="text-3xl font-bold text-white">{fmt(ltvStats.avgLtv)}</p>
                <p className="text-xs text-slate-500 mt-1">{ltvStats.totalCustomers.toLocaleString('pt-BR')} clientes únicos (all-time)</p>
              </div>
              <div className={`bg-[#1a1f2e] rounded-xl p-5 border ${ltvStats.atRisk > 0 ? 'border-orange-500/30' : 'border-[#2a2f3e]'}`}>
                <div className="flex items-center gap-2 mb-1">
                  <UserX size={13} className={ltvStats.atRisk > 0 ? 'text-orange-400' : 'text-slate-500'} />
                  <p className="text-xs text-slate-500">Em Risco (60d+)</p>
                </div>
                <p className={`text-3xl font-bold ${ltvStats.atRisk > 0 ? 'text-orange-400' : 'text-white'}`}>{ltvStats.atRisk}</p>
                <p className="text-xs text-slate-500 mt-1">clientes sem compra em 60+ dias</p>
              </div>
              <div className="bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
                <p className="text-xs text-slate-500 mb-1">Top Cliente (LTV)</p>
                {ltvStats.topCustomers[0] ? (
                  <>
                    <p className="text-sm font-bold text-white truncate">{ltvStats.topCustomers[0].email}</p>
                    <p className="text-2xl font-bold text-emerald-400 mt-1">{fmt(ltvStats.topCustomers[0].total)}</p>
                    <p className="text-xs text-slate-500 mt-1">{ltvStats.topCustomers[0].orders} pedidos</p>
                  </>
                ) : <p className="text-slate-600 text-sm mt-2">—</p>}
              </div>
            </div>
          ) : (
            <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] p-8 text-center">
              <Loader2 size={20} className="text-slate-600 mx-auto mb-2 animate-spin" />
              <p className="text-slate-500 text-sm">Calculando LTV…</p>
            </div>
          )}

          {/* Top Customers table */}
          {ltvStats && ltvStats.topCustomers.length > 0 && (
            <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-hidden">
              <div className="px-5 py-4 border-b border-[#2a2f3e]">
                <h2 className="text-sm font-semibold text-slate-300">Top Clientes por LTV</h2>
                <p className="text-xs text-slate-500 mt-0.5">Clientes com maior valor acumulado (all-time)</p>
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#2a2f3e]">
                    {['#', 'Email', 'Pedidos', 'LTV Total'].map(h => (
                      <th key={h} className={`px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider ${h === 'Email' ? 'text-left' : 'text-right'}`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {ltvStats.topCustomers.map((c, i) => (
                    <tr key={i} className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a]">
                      <td className="px-4 py-3 text-right text-slate-600 text-xs w-8">{i + 1}</td>
                      <td className="px-4 py-3 text-slate-200 text-xs">{c.email}</td>
                      <td className="px-4 py-3 text-right text-slate-400 tabular-nums">{c.orders}</td>
                      <td className="px-4 py-3 text-right text-emerald-400 font-semibold tabular-nums">{fmt(c.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* New vs Returning */}
          {retention && retention.total > 0 && (
            <div className="bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
              <h2 className="text-sm font-semibold text-slate-300 mb-4">Novos vs Recorrentes</h2>
              <div className="grid grid-cols-3 gap-4 mb-4">
                <div>
                  <p className="text-2xl font-bold text-emerald-400">{retention.newOrders}</p>
                  <p className="text-xs text-slate-500 mt-0.5">Novos clientes</p>
                  <p className="text-xs text-slate-400 mt-0.5">{retention.total > 0 ? ((retention.newOrders / retention.total) * 100).toFixed(0) : 0}% dos pedidos</p>
                </div>
                <div>
                  <p className="text-2xl font-bold text-indigo-400">{retention.returningOrders}</p>
                  <p className="text-xs text-slate-500 mt-0.5">Recorrentes</p>
                  <p className="text-xs text-slate-400 mt-0.5">{retention.total > 0 ? ((retention.returningOrders / retention.total) * 100).toFixed(0) : 0}% dos pedidos</p>
                </div>
                <div>
                  <p className="text-2xl font-bold text-slate-200">{retention.total}</p>
                  <p className="text-xs text-slate-500 mt-0.5">Total no período</p>
                </div>
              </div>
              <div className="h-2.5 bg-[#0f1117] rounded-full overflow-hidden flex">
                <div className="h-full bg-emerald-500 transition-all duration-700" style={{ width: `${retention.total > 0 ? (retention.newOrders / retention.total) * 100 : 0}%` }} />
                <div className="h-full bg-indigo-500 transition-all duration-700" style={{ width: `${retention.total > 0 ? (retention.returningOrders / retention.total) * 100 : 0}%` }} />
              </div>
              <div className="flex items-center gap-4 mt-2 flex-wrap">
                <span className="flex items-center gap-1.5 text-xs text-slate-500"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" />Novos</span>
                <span className="flex items-center gap-1.5 text-xs text-slate-500"><span className="w-2 h-2 rounded-full bg-indigo-500 inline-block" />Recorrentes</span>
                {retention.total - retention.newOrders - retention.returningOrders > 0 && (
                  <span
                    className="flex items-center gap-1 text-xs text-slate-600 cursor-help"
                    title="Pedidos sem email no checkout — não é possível identificar se são novos ou recorrentes. Verifique se o campo de email está obrigatório na loja."
                  >
                    <AlertTriangle size={10} className="text-orange-400" />
                    {retention.total - retention.newOrders - retention.returningOrders} sem identificação
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Cohort Retention */}
          {cohortData.length > 0 && cohortData.some(c => c.newBuyers > 0) && (
            <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] p-5">
              <div className="mb-4">
                <h2 className="text-sm font-semibold text-slate-300">Retenção por Coorte Mensal</h2>
                <p className="text-xs text-slate-500 mt-0.5">% de novos compradores de cada mês que fizeram uma segunda compra</p>
              </div>
              <div className="grid grid-cols-3 gap-4">
                {cohortData.map((c, i) => {
                  const monthsAgo = 2 - i
                  const cohortStart = new Date()
                  cohortStart.setDate(1)
                  cohortStart.setMonth(cohortStart.getMonth() - monthsAgo)
                  cohortStart.setHours(0, 0, 0, 0)
                  const cohortAgeDays = Math.floor((Date.now() - cohortStart.getTime()) / 86_400_000)
                  const isForming = cohortAgeDays < 60
                  const isMatureNoReturn = !isForming && c.retPct === 0 && c.newBuyers > 0
                  return (
                    <div key={c.label} className="bg-[#0f1117] rounded-xl p-4">
                      <div className="flex items-center justify-between mb-3">
                        <p className="text-xs text-slate-500 uppercase tracking-wider">{c.label}</p>
                        {isForming && c.retPct === 0 && (
                          <span
                            className="text-xs text-slate-600 cursor-help"
                            title="Coorte em formação — clientes novos têm em média 30-60 dias para fazer a segunda compra. Compare com coortes mais antigos."
                          >
                            ⏳ em formação
                          </span>
                        )}
                        {isMatureNoReturn && (
                          <span
                            className="text-xs text-red-400 font-medium cursor-help"
                            title={`Coorte de ${c.label} com mais de 60 dias e ainda sem recompras. Considere uma campanha de retenção para estes clientes.`}
                          >
                            ⚠ sem recompras
                          </span>
                        )}
                      </div>
                      <div className="flex items-end gap-3 mb-3">
                        <div>
                          <p className={`text-2xl font-bold ${c.retPct >= 20 ? 'text-emerald-400' : c.retPct >= 10 ? 'text-yellow-400' : 'text-red-400'}`}>{c.retPct}%</p>
                          <p className="text-xs text-slate-500">retornaram</p>
                        </div>
                        <div className="text-right ml-auto">
                          <p className="text-sm font-medium text-white">{c.newBuyers}</p>
                          <p className="text-xs text-slate-600">novos</p>
                        </div>
                      </div>
                      <div className="h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
                        <div className={`h-full rounded-full transition-all duration-700 ${c.retPct >= 20 ? 'bg-emerald-500' : c.retPct >= 10 ? 'bg-yellow-500' : 'bg-red-500'}`} style={{ width: `${Math.min(c.retPct, 100)}%` }} />
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Product Performance */}
          <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-hidden">
            <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-300">Performance de Produtos</h2>
              <div className="flex gap-1 bg-[#0f1117] rounded-lg p-0.5">
                {(['purchases', 'views'] as const).map(s => (
                  <button key={s} onClick={() => setProductSort(s)}
                    className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${productSort === s ? 'bg-indigo-600 text-white' : 'text-slate-500 hover:text-slate-300'}`}>
                    {s === 'purchases' ? 'Vendas' : 'Visitas'}
                  </button>
                ))}
              </div>
            </div>
            {products.length === 0 ? (
              <div className="p-4">
                <EmptyState
                  type="neutral"
                  title="Sem movimentação de produtos no período"
                  description="Nenhum evento de visualização ou compra de produto registrado. Tente expandir o período ou verificar se o pixel está capturando eventos de produto."
                  compact
                />
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#2a2f3e]">
                    {['Produto', 'Views', 'Carrinho', 'Compras'].map(h => (
                      <th key={h} className={`px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider ${h === 'Produto' ? 'text-left' : 'text-center'}`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...products].sort((a, b) => productSort === 'purchases' ? b.purchases - a.purchases : b.views - a.views).map((p, i) => (
                    <tr key={i} className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a] transition-colors">
                      <td className="px-4 py-3"><p className="text-slate-200 truncate max-w-[200px] text-xs">{p.name}</p></td>
                      <td className="px-4 py-3 text-center text-slate-400">{p.views}</td>
                      <td className="px-4 py-3 text-center"><span className={p.cartAdds > 0 ? 'text-yellow-400' : 'text-slate-600'}>{p.cartAdds}</span></td>
                      <td className="px-4 py-3 text-center"><span className={p.purchases > 0 ? 'text-emerald-400 font-semibold' : 'text-slate-600'}>{p.purchases}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* AI Insights */}
          <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-hidden">
            <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Sparkles size={15} className="text-indigo-400" />
                <h2 className="text-sm font-semibold text-slate-300">Insights IA</h2>
                {insights.length > 0 && (
                  <span className="bg-indigo-600 text-white text-xs px-1.5 py-0.5 rounded-full font-medium">
                    {insights.length} novo{insights.length > 1 ? 's' : ''}
                  </span>
                )}
              </div>
              <button onClick={generateInsights} disabled={generating}
                className="flex items-center gap-2 text-xs bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg transition-colors font-medium">
                {generating ? <><Loader2 size={12} className="animate-spin" /> Analisando…</> : <><Sparkles size={12} /> Atualizar</>}
              </button>
            </div>
            <div className="p-5">
              {insightsLoading ? (
                <div className="flex items-center gap-2 text-slate-500 text-sm"><Loader2 size={14} className="animate-spin" /> Carregando insights…</div>
              ) : insights.length === 0 ? (
                <div className="text-center py-8">
                  <Sparkles size={32} className="text-slate-600 mx-auto mb-3" />
                  <p className="text-slate-400 text-sm font-medium">Nenhum alerta ativo</p>
                  <p className="text-slate-600 text-xs mt-1">Tudo dentro da normalidade, ou clique em "Atualizar" para forçar análise.</p>
                </div>
              ) : (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                  {insights.map((insight, idx) => (
                    <InsightCard key={insight.id} insight={insight} onDismiss={dismissInsight} autoExpand={idx === 0} />
                  ))}
                </div>
              )}
            </div>
          </div>
        </>}

      </div>

      {drilldown && (
        <KPIDrilldownModal
          kpi={drilldown}
          orders={allOrdersRaw.filter(o => country === 'all' || o.shipping_country === country)}
          onClose={() => setDrilldown(null)}
        />
      )}
    </div>
  )
}
