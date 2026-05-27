'use client'

import React, { useEffect, useState, useCallback, useRef } from 'react'
import { useParams } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import { ShoppingBag, Users, TrendingUp, Activity, RefreshCw, Percent, CheckCircle, Sparkles, AlertTriangle, Lightbulb, BarChart2, Loader2 } from 'lucide-react'
import IntegrationsHealth from '@/components/IntegrationsHealth'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer,
} from 'recharts'

// ── Types ──────────────────────────────────────────────────────────────────────

type DateRange = '1d' | '7d' | '30d' | '90d' | 'custom'

interface KPIs {
  totalRevenue: number
  totalOrders: number
  totalVisitors: number
  avgOrderValue: number
  revenueChange: number
  ordersChange: number
  conversionRate: number
  totalProfit:   number | null
  marginPct:     number | null
}

interface RevenuePoint { date: string; revenue: number; orders: number }

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

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })

const pct = (n: number, total: number) =>
  total > 0 ? ((n / total) * 100).toFixed(0) + '%' : '—'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Sub-components ────────────────────────────────────────────────────────────

function KPICard({ title, value, icon: Icon, change, color, hint, onClick }: {
  title: string; value: string; icon: React.ElementType
  change?: number; color: string; hint?: string
  onClick?: () => void
}) {
  const Tag: any = onClick ? 'button' : 'div'
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
        <div className={`text-xs ${change >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
          {change >= 0 ? '+' : ''}{change.toFixed(1)}% vs período anterior
        </div>
      )}
      {hint && <div className="text-xs text-slate-500">{hint}</div>}
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
                <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 10 }} />
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

function InsightCard({ insight, onRead }: { insight: Insight; onRead: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const Icon = INSIGHT_ICON[insight.type] || Lightbulb

  return (
    <div
      className={`rounded-xl border p-4 transition-all ${SEVERITY_STYLE[insight.severity] || SEVERITY_STYLE.info} ${insight.is_read ? 'opacity-60' : ''}`}
    >
      <div className="flex items-start gap-3">
        <div className={`mt-0.5 shrink-0 ${SEVERITY_ICON_COLOR[insight.severity]}`}>
          <Icon size={16} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <p className={`text-sm font-semibold ${insight.is_read ? 'text-slate-400' : 'text-white'}`}>
              {insight.title}
            </p>
            {!insight.is_read && (
              <span className="shrink-0 w-2 h-2 rounded-full bg-indigo-400 mt-1" />
            )}
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
              <div className="flex items-center gap-3 mt-3">
                <button
                  onClick={() => setExpanded(false)}
                  className="text-xs text-slate-500 hover:text-slate-300"
                >
                  Fechar
                </button>
                {!insight.is_read && (
                  <button
                    onClick={() => onRead(insight.id)}
                    className="text-xs text-indigo-400 hover:text-indigo-300"
                  >
                    Marcar como lido
                  </button>
                )}
              </div>
            </>
          ) : (
            <button
              onClick={() => { setExpanded(true); if (!insight.is_read) onRead(insight.id) }}
              className="text-xs text-slate-500 hover:text-slate-300 mt-1"
            >
              Ver análise completa →
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
  const [heatmap, setHeatmap]           = useState<number[][]>([])
  const [roasData, setRoasData]         = useState<RoasData | null>(null)
  const [roasLoading, setRoasLoading]   = useState(false)
  const [cohortData, setCohortData]     = useState<CohortMonth[]>([])
  const [pacing, setPacing]             = useState<PacingData | null>(null)
  const [loading, setLoading]           = useState(true)
  const [lastUpdate, setLastUpdate]     = useState<Date>(new Date())
  const [clientName, setClientName]     = useState<string>('')
  const [dateRange, setDateRange]       = useState<DateRange>('30d')
  const [fromDate, setFromDate]         = useState<string>('')
  const [toDate, setToDate]             = useState<string>('')
  const fromDateRef                     = useRef<string>('')
  const toDateRef                       = useRef<string>('')
  const [device,   setDevice]           = useState<string>('all')
  const [country,  setCountry]          = useState<string>('all')
  const [drilldown, setDrilldown]       = useState<DrilldownKPI | null>(null)
  const [allOrdersRaw, setAllOrdersRaw] = useState<any[]>([])
  const [allEventsRaw, setAllEventsRaw] = useState<any[]>([])

  const params = useParams()
  const CLIENT_PIXEL_ID = (params?.clientId as string) || process.env.NEXT_PUBLIC_CLIENT_PIXEL_ID || 'lk-sneakers'

  const loadData = useCallback(async (range: DateRange) => {
    setLoading(true)

    // Check if CLIENT_PIXEL_ID is a UUID (client ID) or a pixel_id
    const isUUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(CLIENT_PIXEL_ID)
    console.log('[loadData] CLIENT_PIXEL_ID:', CLIENT_PIXEL_ID, 'isUUID:', isUUID)
    const { data: clientData, error: clientError } = isUUID
      ? await supabase.from('clients').select('id').eq('id', CLIENT_PIXEL_ID).limit(1).single()
      : await supabase.from('clients').select('id').eq('pixel_id', CLIENT_PIXEL_ID).limit(1).single()

    console.log('[loadData] clientData:', clientData, 'error:', clientError)
    if (!clientData) {
      console.log('[loadData] No client data found')
      setLoading(false)
      return
    }

    const clientId = clientData.id

    // Fetch client name for the header
    const isUUID2 = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(CLIENT_PIXEL_ID)
    supabase.from('clients').select('name').eq(isUUID2 ? 'id' : 'pixel_id', CLIENT_PIXEL_ID).limit(1).single()
      .then(({ data }) => { if (data?.name) setClientName(data.name) })

    // Date range computation
    const now = new Date()
    let startDate: Date, endDate: Date
    const _from = fromDateRef.current
    const _to   = toDateRef.current
    if (range === 'custom' && _from && _to) {
      startDate = new Date(_from + 'T00:00:00')
      endDate   = new Date(_to   + 'T23:59:59')
    } else if (range === '1d') {
      startDate = new Date(now); startDate.setDate(startDate.getDate() - 1); startDate.setHours(0, 0, 0, 0)
      endDate   = new Date(startDate); endDate.setHours(23, 59, 59, 999)
    } else {
      const d = range === '7d' ? 7 : range === '30d' ? 30 : 90
      startDate = new Date(); startDate.setDate(startDate.getDate() - d)
      endDate   = now
    }
    const days = Math.max(1, Math.ceil((endDate.getTime() - startDate.getTime()) / 86400000))
    const prevStart = new Date(startDate); prevStart.setDate(prevStart.getDate() - days)

    const [
      { data: orders },
      { data: ordersPrev },
      { data: productEvents },
    ] = await Promise.all([
      supabase.from('orders')
        .select('id, email, total_price, gross_profit, margin_pct, financial_status, platform_source, utm_source, utm_medium, utm_campaign, is_first_purchase, shipping_country, created_at')
        .eq('client_id', clientId)
        .eq('financial_status', 'paid')
        .gt('total_price', 0)
        .gte('created_at', startDate.toISOString())
        .lte('created_at', endDate.toISOString())
        .order('created_at', { ascending: false }),
      supabase.from('orders')
        .select('total_price')
        .eq('client_id', clientId)
        .eq('financial_status', 'paid')
        .gt('total_price', 0)
        .gte('created_at', prevStart.toISOString())
        .lt('created_at', startDate.toISOString()),
      supabase.from('tracking_events')
        .select('event_type, product_name')
        .eq('client_id', clientId)
        .gte('created_at', startDate.toISOString())
        .lte('created_at', endDate.toISOString())
        .not('product_name', 'is', null),
    ])

    // Fetch funnel via API — bypasses PostgREST 1000-row limit + handles device filter server-side
    let funnelJson: any = null
    try {
      const startStr = startDate.toISOString().split('T')[0]
      const endStr   = endDate.toISOString().split('T')[0]
      const dParam   = device !== 'all' ? `&device=${device}` : ''
      const fRes = await fetch(`${API_URL}/insights/${CLIENT_PIXEL_ID}/funnel?start=${startStr}&end=${endStr}${dParam}`)
      if (fRes.ok) funnelJson = await fRes.json()
    } catch (_) {}

    const totalVisitors = funnelJson?.unique_visitors ?? 0

    setAllOrdersRaw(orders || [])
    setAllEventsRaw([])

    // Country filter applies to orders only (visitors don't have a known country until checkout)
    const filterOrder = (o: any) => country === 'all' || o.shipping_country === country

    const allOrders  = (orders || []).filter(filterOrder)
    const prevOrders = ordersPrev || []
    const prodEvents = (productEvents || [])

    // ── KPIs ─────────────────────────────────────────────────────────────────
    const totalRevenue   = allOrders.reduce((s, o) => s + (o.total_price || 0), 0)
    const prevRevenue    = prevOrders.reduce((s, o) => s + (o.total_price || 0), 0)
    const avgOrderValue  = allOrders.length ? totalRevenue / allOrders.length : 0
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

    setKpis({
      totalRevenue, totalOrders: allOrders.length, totalVisitors, avgOrderValue,
      revenueChange: prevRevenue ? ((totalRevenue - prevRevenue) / prevRevenue) * 100 : 0,
      ordersChange:  prevOrders.length ? ((allOrders.length - prevOrders.length) / prevOrders.length) * 100 : 0,
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
    setRevenueData(points)

    // ── Conversion funnel (from API — COUNT DISTINCT via SQL, no row-limit issues)
    const fd   = funnelJson?.funnel || {}
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

    // ── Retention: novos vs recorrentes (via RPC — is_first_purchase coluna não confiável)
    let newOrders = allOrders.length, returningOrders = 0
    try {
      const nrRes = await supabase.rpc('new_returning_stats', {
        p_client_id: clientId,
        p_start:     startDate.toISOString(),
        p_end:       endDate.toISOString(),
      })
      if (nrRes.data) {
        newOrders       = Number(nrRes.data.new_orders       ?? allOrders.length)
        returningOrders = Number(nrRes.data.returning_orders ?? 0)
      }
    } catch (_) {}
    setRetention({ newOrders, returningOrders, total: allOrders.length })

    // ── Heatmap de vendas — 7 dias × 8 blocos de 3h ──────────────────────────
    const grid: number[][] = Array.from({ length: 7 }, () => Array(8).fill(0))
    allOrders.forEach((o: any) => {
      if (!o.created_at) return
      const d     = new Date(o.created_at)
      const day   = d.getDay()                       // 0=Dom…6=Sáb
      const block = Math.min(Math.floor(d.getHours() / 3), 7)  // 0-7
      grid[day][block] += 1
    })
    setHeatmap(grid)

    setLastUpdate(new Date())
    setLoading(false)
  }, [country, device, CLIENT_PIXEL_ID])

  const loadInsights = useCallback(async () => {
    setInsLoading(true)
    const { data } = await supabase
      .from('ai_insights')
      .select('id, type, severity, title, content, data, is_read, created_at')
      .order('created_at', { ascending: false })
      .limit(10)
    setInsights((data as Insight[]) || [])
    setInsLoading(false)
  }, [])

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

  const loadRoas = useCallback(async (range: DateRange) => {
    setRoasLoading(true)
    try {
      const days = range === '7d' ? 7 : range === '30d' ? 30 : 90
      const res  = await fetch(`${API_URL}/meta-ads/${CLIENT_PIXEL_ID}/roas?days=${days}`)
      if (res.ok) setRoasData(await res.json())
    } catch (_) {}
    setRoasLoading(false)
  }, [])

  const loadCohort = useCallback(async () => {
    if (!CLIENT_PIXEL_ID) return
    const { data: clientData } = await supabase
      .from('clients').select('id').eq('pixel_id', CLIENT_PIXEL_ID).limit(1).single()
    if (!clientData) return

    // Fetch 90d of orders with is_first_purchase + email
    const start90 = new Date(); start90.setDate(start90.getDate() - 90)
    const { data: allOrders90 } = await supabase
      .from('orders')
      .select('email, created_at')
      .eq('client_id', clientData.id)
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

  const markRead = useCallback(async (insightId: string) => {
    setInsights(prev => prev.map(i => i.id === insightId ? { ...i, is_read: true } : i))
    await fetch(`${API_URL}/insights/${CLIENT_PIXEL_ID}/${insightId}/read`, { method: 'PATCH' })
  }, [])

  const loadPacing = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/pacing/${CLIENT_PIXEL_ID}`)
      if (res.ok) setPacing(await res.json())
    } catch (_) {}
  }, [])

  useEffect(() => { loadData(dateRange) }, [dateRange, loadData])
  useEffect(() => { loadInsights() }, [loadInsights])
  useEffect(() => { loadRoas(dateRange) }, [dateRange, loadRoas])
  useEffect(() => { loadCohort() }, [loadCohort])
  useEffect(() => { loadPacing() }, [loadPacing])

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
          <div className="flex items-center gap-2">
            <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
              {(['1d', '7d', '30d', '90d', 'custom'] as DateRange[]).map(r => (
                <button key={r} onClick={() => setDateRange(r)}
                  className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                    dateRange === r ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
                  }`}>
                  {r === '1d' ? 'Ontem' : r === '7d' ? '7d' : r === '30d' ? '30d' : r === '90d' ? '90d' : 'Custom'}
                </button>
              ))}
            </div>
            {dateRange === 'custom' && (
              <div className="flex items-center gap-1.5">
                <input
                  type="date"
                  value={fromDate}
                  onChange={e => { setFromDate(e.target.value); fromDateRef.current = e.target.value }}
                  className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-2 py-1 text-xs text-slate-200 outline-none focus:border-indigo-500"
                />
                <span className="text-slate-500 text-xs">–</span>
                <input
                  type="date"
                  value={toDate}
                  onChange={e => { setToDate(e.target.value); toDateRef.current = e.target.value }}
                  className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-2 py-1 text-xs text-slate-200 outline-none focus:border-indigo-500"
                />
                <button
                  onClick={() => loadData('custom')}
                  disabled={!fromDate || !toDate}
                  className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 text-white px-3 py-1 rounded-lg text-xs font-medium transition-colors"
                >
                  Aplicar
                </button>
              </div>
            )}
          </div>
          <button onClick={() => loadData(dateRange)}
            className="flex items-center gap-2 text-xs text-slate-400 hover:text-white transition-colors">
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            {lastUpdate.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}
          </button>
        </div>
      </div>

      <div className="p-6 space-y-6">

        {/* Saúde das integrações — só aparece se algo precisar de atenção
            ou se forçarmos via reload. Card ocupa pouco espaço quando tudo
            está verde, então deixamos sempre visível. */}
        {CLIENT_PIXEL_ID && <IntegrationsHealth pixelId={CLIENT_PIXEL_ID} />}

        {/* Pacing — month-to-date vs goal */}
        {pacing && pacing.monthly_revenue_goal && (
          <PacingWidget pacing={pacing} />
        )}

        {/* KPIs — clicáveis abrem drilldown */}
        <div className="grid grid-cols-2 lg:grid-cols-6 gap-4">
          <KPICard title="Receita"      value={kpis ? fmt(kpis.totalRevenue) : '—'}
            icon={TrendingUp} change={kpis?.revenueChange}
            color="bg-emerald-500/10 text-emerald-400"
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
            color="bg-blue-500/10 text-blue-400"
            onClick={() => setDrilldown('orders')} />
          <KPICard title="Visitantes"   value={kpis ? kpis.totalVisitors.toString() : '—'}
            icon={Users}
            color="bg-purple-500/10 text-purple-400" />
          <KPICard title="Ticket Médio" value={kpis ? fmt(kpis.avgOrderValue) : '—'}
            icon={Activity}
            color="bg-orange-500/10 text-orange-400"
            onClick={() => setDrilldown('avgOrderValue')} />
          <KPICard title="Conversão"    value={kpis ? kpis.conversionRate.toFixed(1) + '%' : '—'}
            icon={Percent}
            color="bg-pink-500/10 text-pink-400" />
        </div>

        {/* Revenue + Funnel */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2 bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">
              Receita — {dateRange === '7d' ? 'últimos 7 dias' : dateRange === '30d' ? 'últimos 30 dias' : 'últimos 90 dias'}
            </h2>
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={revenueData}>
                <defs>
                  <linearGradient id="colorRevenue" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2f3e" />
                <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 11 }} />
                <YAxis tick={{ fill: '#64748b', fontSize: 11 }} tickFormatter={v => `R$${v}`} />
                <Tooltip
                  contentStyle={{ background: '#1a1f2e', border: '1px solid #2a2f3e', borderRadius: 8 }}
                  formatter={(v) => [fmt(Number(v)), 'Receita']}
                />
                <Area type="monotone" dataKey="revenue" stroke="#10b981" fill="url(#colorRevenue)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          <div className="bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">Funil de Conversão</h2>
            {funnelSteps.length === 0 || funnelSteps[0].count === 0 ? (
              <p className="text-slate-500 text-sm">Sem dados de eventos</p>
            ) : <FunnelBar steps={funnelSteps} />}
          </div>
        </div>

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
                        c.source === 'direto'    ? 'bg-slate-500/10 text-slate-400' :
                        ['facebook','instagram','meta'].includes(c.source) ? 'bg-blue-500/10 text-blue-400' :
                        c.source === 'google'   ? 'bg-red-500/10 text-red-400' :
                        'bg-indigo-500/10 text-indigo-400'
                      }`}>{c.source}</span>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-400">{c.medium !== '—' ? c.medium : <span className="text-slate-600">—</span>}</td>
                    <td className="px-4 py-3 text-xs text-slate-300 max-w-[180px]">
                      <p className="truncate">{c.campaign !== '—' ? c.campaign : <span className="text-slate-600">—</span>}</p>
                    </td>
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

        {/* Novos vs Recorrentes */}
        {retention && retention.total > 0 && (
          <div className="bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">Novos vs Recorrentes</h2>
            <div className="grid grid-cols-3 gap-4 mb-4">
              <div>
                <p className="text-2xl font-bold text-emerald-400">{retention.newOrders}</p>
                <p className="text-xs text-slate-500 mt-0.5">Novos clientes</p>
                <p className="text-xs text-slate-400 mt-0.5">
                  {retention.total > 0 ? ((retention.newOrders / retention.total) * 100).toFixed(0) : 0}% dos pedidos
                </p>
              </div>
              <div>
                <p className="text-2xl font-bold text-indigo-400">{retention.returningOrders}</p>
                <p className="text-xs text-slate-500 mt-0.5">Recorrentes</p>
                <p className="text-xs text-slate-400 mt-0.5">
                  {retention.total > 0 ? ((retention.returningOrders / retention.total) * 100).toFixed(0) : 0}% dos pedidos
                </p>
              </div>
              <div>
                <p className="text-2xl font-bold text-slate-200">{retention.total}</p>
                <p className="text-xs text-slate-500 mt-0.5">Total no período</p>
                <p className="text-xs text-slate-600 mt-0.5">
                  {retention.total - retention.newOrders - retention.returningOrders > 0
                    ? `${retention.total - retention.newOrders - retention.returningOrders} sem dado`
                    : ''}
                </p>
              </div>
            </div>
            <div className="h-2.5 bg-[#0f1117] rounded-full overflow-hidden flex">
              <div
                className="h-full bg-emerald-500 transition-all duration-700"
                style={{ width: `${retention.total > 0 ? (retention.newOrders / retention.total) * 100 : 0}%` }}
              />
              <div
                className="h-full bg-indigo-500 transition-all duration-700"
                style={{ width: `${retention.total > 0 ? (retention.returningOrders / retention.total) * 100 : 0}%` }}
              />
            </div>
            <div className="flex items-center gap-4 mt-2">
              <span className="flex items-center gap-1.5 text-xs text-slate-500">
                <span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" />Novos
              </span>
              <span className="flex items-center gap-1.5 text-xs text-slate-500">
                <span className="w-2 h-2 rounded-full bg-indigo-500 inline-block" />Recorrentes
              </span>
              {retention.total - retention.newOrders - retention.returningOrders > 0 && (
                <span className="flex items-center gap-1.5 text-xs text-slate-500">
                  <span className="w-2 h-2 rounded-full bg-slate-600 inline-block" />Sem dado
                </span>
              )}
            </div>
          </div>
        )}

        {/* Product Performance + Recent Orders */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

          {/* Product Performance */}
          <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-hidden">
            <div className="px-5 py-4 border-b border-[#2a2f3e]">
              <h2 className="text-sm font-semibold text-slate-300">Performance de Produtos</h2>
            </div>
            {products.length === 0 ? (
              <p className="p-5 text-slate-500 text-sm">Sem dados de produto no período</p>
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
                  {products.map((p, i) => (
                    <tr key={i} className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a] transition-colors">
                      <td className="px-4 py-3">
                        <p className="text-slate-200 truncate max-w-[180px] text-xs">{p.name}</p>
                      </td>
                      <td className="px-4 py-3 text-center text-slate-400">{p.views}</td>
                      <td className="px-4 py-3 text-center">
                        <span className={p.cartAdds > 0 ? 'text-yellow-400' : 'text-slate-600'}>{p.cartAdds}</span>
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span className={p.purchases > 0 ? 'text-emerald-400 font-semibold' : 'text-slate-600'}>{p.purchases}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Recent Orders */}
          <div className="bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">Pedidos Recentes</h2>
            <div className="space-y-2 overflow-auto max-h-[280px]">
              {recentOrders.length === 0 ? (
                <p className="text-slate-500 text-sm">Nenhum pedido ainda</p>
              ) : recentOrders.map(order => (
                <div key={order.id} className="flex items-center justify-between py-2 border-b border-[#2a2f3e] last:border-0">
                  <div className="min-w-0">
                    <p className="text-sm text-slate-200 truncate">{order.email || '—'}</p>
                    <p className="text-xs text-slate-500">
                      {fmtDate(order.created_at)} ·{' '}
                      {order.utm_source
                        ? <span className="text-indigo-400">{order.utm_source}</span>
                        : 'direto'}
                    </p>
                  </div>
                  <div className="text-right ml-4 shrink-0">
                    <p className="text-sm font-medium text-emerald-400">{fmt(order.total_price)}</p>
                    <span className={`text-xs px-1.5 py-0.5 rounded ${
                      order.financial_status === 'paid'
                        ? 'bg-emerald-500/10 text-emerald-400'
                        : 'bg-yellow-500/10 text-yellow-400'
                    }`}>{order.financial_status || 'pendente'}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

        </div>

        {/* Heatmap de vendas */}
        {heatmap.length > 0 && <SalesHeatmap grid={heatmap} />}

        {/* Meta Ads ROAS */}
        {roasData && (
          <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-hidden">
            <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-slate-300">Meta Ads — ROAS por Campanha</h2>
                {!roasData.has_ads_credentials && (
                  <p className="text-xs text-yellow-500 mt-0.5">
                    Configure <code className="bg-yellow-500/10 px-1 rounded">meta_ad_account_id</code> no cliente para ver gasto e ROAS
                  </p>
                )}
              </div>
              {roasData.paid_only?.roas != null && (
                <div className="text-right">
                  <p className="text-2xl font-bold text-indigo-400">{roasData.paid_only.roas.toFixed(2)}x</p>
                  <p className="text-xs text-slate-500">ROAS pago</p>
                </div>
              )}
            </div>

            {/* Summary strip — only paid traffic. Orgânico/POS/email is in
                the Fontes UTM card below, separated on purpose. */}
            {roasData.has_ads_credentials && roasData.totals.spend > 0 && roasData.paid_only && (
              <div className="border-b border-[#2a2f3e]">
                <div className={`grid divide-x divide-[#2a2f3e] ${roasData.has_cogs && roasData.paid_only.gross_profit != null ? 'grid-cols-5' : 'grid-cols-4'}`}>
                  {[
                    { label: 'Gasto',                value: fmt(roasData.paid_only.spend) },
                    { label: 'Receita atribuída',    value: fmt(roasData.paid_only.revenue),
                      sub: roasData.totals.meta_revenue
                        ? `Meta diz: ${fmt(roasData.totals.meta_revenue)}`
                        : undefined },
                    { label: 'ROAS pago',            value: roasData.paid_only.roas != null ? `${roasData.paid_only.roas.toFixed(2)}x` : '—',
                      sub: roasData.totals.meta_roas != null
                        ? `Meta diz: ${roasData.totals.meta_roas.toFixed(2)}x`
                        : undefined },
                    { label: 'CPA real',             value: roasData.paid_only.cpa != null ? fmt(roasData.paid_only.cpa) : '—',
                      sub: roasData.totals.meta_cpa != null
                        ? `Meta diz: ${fmt(roasData.totals.meta_cpa)}`
                        : undefined },
                    ...(roasData.has_cogs && roasData.paid_only.gross_profit != null ? [{
                      label: 'ROAS de Margem',
                      value: roasData.paid_only.margin_roas != null ? `${roasData.paid_only.margin_roas.toFixed(2)}x` : '—',
                      sub: roasData.totals.margin_pct != null
                        ? `Margem: ${roasData.totals.margin_pct.toFixed(1)}%`
                        : undefined,
                    }] : []),
                  ].map(s => (
                    <div key={s.label} className="px-5 py-3 text-center">
                      <p className="text-xs text-slate-500">{s.label}</p>
                      <p className="text-sm font-bold text-white mt-0.5">{s.value}</p>
                      {s.sub && <p className="text-xs text-slate-500 mt-0.5">{s.sub}</p>}
                    </div>
                  ))}
                </div>
                {roasData.totals.cpa_diff_pct != null && Math.abs(roasData.totals.cpa_diff_pct) >= 5 && (
                  <div className={`px-5 py-2 text-xs ${
                    roasData.totals.cpa_diff_pct > 0
                      ? 'bg-yellow-500/5 text-yellow-300'
                      : 'bg-emerald-500/5 text-emerald-300'
                  }`}>
                    {roasData.totals.cpa_diff_pct > 0 ? (
                      <>
                        ⚠ Meta está <strong>subestimando</strong> seu CPA em <strong>{roasData.totals.cpa_diff_pct.toFixed(0)}%</strong>.
                        O CPA real é {Math.abs(roasData.totals.cpa_diff_pct).toFixed(0)}% maior do que o painel do Meta mostra.
                      </>
                    ) : (
                      <>
                        ✓ Meta está reportando CPA {Math.abs(roasData.totals.cpa_diff_pct).toFixed(0)}% acima do real
                        ({roasData.totals.meta_purchases} compras vs {roasData.totals.orders} no servidor).
                      </>
                    )}
                  </div>
                )}
              </div>
            )}

            <div className="overflow-x-auto">
              {roasLoading ? (
                <div className="flex items-center gap-2 p-5 text-slate-500 text-sm">
                  <Loader2 size={14} className="animate-spin" /> Carregando…
                </div>
              ) : (roasData.has_ads_credentials
                    ? roasData.campaigns.filter(c => c.impressions > 0 || c.spend > 0)
                    : roasData.campaigns).length === 0 ? (
                <p className="p-5 text-slate-500 text-sm">Nenhuma campanha no período</p>
              ) : (() => {
                // When Meta credentials are configured, show only rows that came from the
                // Meta Ads API (impressions > 0 or spend > 0). This hides UTM ad-set/ad
                // name rows that pollute the campaign view when UTMs are configured below
                // campaign level. Without credentials we show all UTM rows.
                const visibleCampaigns = roasData.has_ads_credentials
                  ? roasData.campaigns.filter(c => c.impressions > 0 || c.spend > 0)
                  : roasData.campaigns
                return (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-[#2a2f3e]">
                      {['Campanha', 'Pedidos', 'Receita',
                        ...(roasData.has_cogs ? ['Lucro Bruto', 'ROAS Margem'] : []),
                        ...(roasData.has_ads_credentials ? ['Gasto', 'ROAS', 'CPA real', 'CPA Meta', 'Diff', 'Clicks'] : [])
                      ].map(h => (
                        <th key={h} className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {visibleCampaigns.map((c, i) => (
                      <tr key={i} className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a] transition-colors">
                        <td className="px-4 py-3 max-w-[200px]">
                          <p className="text-slate-200 text-xs truncate">{c.campaign_name}</p>
                          {c.utm_source && (
                            <span className={`text-xs px-1.5 py-0.5 rounded mt-0.5 inline-block ${
                              ['facebook','instagram','meta'].includes(c.utm_source)
                                ? 'bg-blue-500/10 text-blue-400'
                                : c.utm_source === 'google' ? 'bg-red-500/10 text-red-400'
                                : 'bg-slate-500/10 text-slate-400'
                            }`}>{c.utm_source}</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-slate-200 font-medium">
                          {c.orders}
                          {c.purchases_diff !== 0 && c.meta_purchases > 0 && (
                            <span className="text-xs text-slate-500 ml-1" title="Meta reportou">
                              (Meta: {c.meta_purchases})
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-emerald-400 font-semibold whitespace-nowrap">{fmt(c.revenue)}</td>
                        {roasData.has_cogs && (
                          <>
                            <td className="px-4 py-3 whitespace-nowrap">
                              {c.gross_profit != null
                                ? <span className="text-teal-400 font-medium">{fmt(c.gross_profit)}</span>
                                : <span className="text-slate-600">—</span>}
                              {c.margin_pct != null && (
                                <span className="text-xs text-slate-500 ml-1">{c.margin_pct.toFixed(0)}%</span>
                              )}
                            </td>
                            <td className="px-4 py-3 whitespace-nowrap">
                              {c.margin_roas != null ? (
                                <span className={`font-bold ${c.margin_roas >= 2 ? 'text-teal-400' : c.margin_roas >= 1 ? 'text-yellow-400' : 'text-red-400'}`}>
                                  {c.margin_roas.toFixed(2)}x
                                </span>
                              ) : <span className="text-slate-600">—</span>}
                            </td>
                          </>
                        )}
                        {roasData.has_ads_credentials && (
                          <>
                            <td className="px-4 py-3 text-slate-300 whitespace-nowrap">
                              {c.spend > 0 ? fmt(c.spend) : <span className="text-slate-600">—</span>}
                            </td>
                            <td className="px-4 py-3 whitespace-nowrap">
                              {c.roas != null ? (
                                <span className={`font-bold ${c.roas >= 3 ? 'text-emerald-400' : c.roas >= 1.5 ? 'text-yellow-400' : 'text-red-400'}`}>
                                  {c.roas.toFixed(2)}x
                                </span>
                              ) : <span className="text-slate-600">—</span>}
                            </td>
                            <td className="px-4 py-3 text-slate-300 whitespace-nowrap">
                              {c.cpa != null ? fmt(c.cpa) : <span className="text-slate-600">—</span>}
                            </td>
                            <td className="px-4 py-3 text-slate-500 whitespace-nowrap text-xs">
                              {c.meta_cpa != null ? fmt(c.meta_cpa) : <span className="text-slate-600">—</span>}
                            </td>
                            <td className="px-4 py-3 whitespace-nowrap">
                              {c.cpa_diff_pct != null ? (
                                <span className={`text-xs font-medium ${
                                  Math.abs(c.cpa_diff_pct) < 10 ? 'text-slate-400' :
                                  c.cpa_diff_pct > 0 ? 'text-yellow-400' : 'text-emerald-400'
                                }`}>
                                  {c.cpa_diff_pct > 0 ? '+' : ''}{c.cpa_diff_pct.toFixed(0)}%
                                </span>
                              ) : <span className="text-slate-600 text-xs">—</span>}
                            </td>
                            <td className="px-4 py-3 text-slate-400">
                              {c.clicks > 0 ? c.clicks.toLocaleString('pt-BR') : <span className="text-slate-600">—</span>}
                            </td>
                          </>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
                )
              })()}
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
              {cohortData.map(c => (
                <div key={c.label} className="bg-[#0f1117] rounded-xl p-4">
                  <p className="text-xs text-slate-500 uppercase tracking-wider mb-3">{c.label}</p>
                  <div className="flex items-end gap-3 mb-3">
                    <div>
                      <p className={`text-2xl font-bold ${c.retPct >= 20 ? 'text-emerald-400' : c.retPct >= 10 ? 'text-yellow-400' : 'text-red-400'}`}>
                        {c.retPct}%
                      </p>
                      <p className="text-xs text-slate-500">retornaram</p>
                    </div>
                    <div className="text-right ml-auto">
                      <p className="text-sm font-medium text-white">{c.newBuyers}</p>
                      <p className="text-xs text-slate-600">novos</p>
                    </div>
                  </div>
                  <div className="h-1.5 bg-[#1a1f2e] rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-700 ${c.retPct >= 20 ? 'bg-emerald-500' : c.retPct >= 10 ? 'bg-yellow-500' : 'bg-red-500'}`}
                      style={{ width: `${Math.min(c.retPct, 100)}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* AI Insights */}
        <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-hidden">
          <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles size={15} className="text-indigo-400" />
              <h2 className="text-sm font-semibold text-slate-300">Insights IA</h2>
              {insights.filter(i => !i.is_read).length > 0 && (
                <span className="bg-indigo-600 text-white text-xs px-1.5 py-0.5 rounded-full font-medium">
                  {insights.filter(i => !i.is_read).length} novo{insights.filter(i => !i.is_read).length > 1 ? 's' : ''}
                </span>
              )}
            </div>
            <button
              onClick={generateInsights}
              disabled={generating}
              className="flex items-center gap-2 text-xs bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg transition-colors font-medium"
            >
              {generating
                ? <><Loader2 size={12} className="animate-spin" /> Analisando…</>
                : <><Sparkles size={12} /> Gerar análise</>
              }
            </button>
          </div>
          <div className="p-5">
            {insightsLoading ? (
              <div className="flex items-center gap-2 text-slate-500 text-sm">
                <Loader2 size={14} className="animate-spin" /> Carregando insights…
              </div>
            ) : insights.length === 0 ? (
              <div className="text-center py-8">
                <Sparkles size={32} className="text-slate-600 mx-auto mb-3" />
                <p className="text-slate-400 text-sm font-medium">Nenhum insight gerado ainda</p>
                <p className="text-slate-600 text-xs mt-1">Clique em "Gerar análise" para o Claude analisar seus dados</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                {insights.map(insight => (
                  <InsightCard key={insight.id} insight={insight} onRead={markRead} />
                ))}
              </div>
            )}
          </div>
        </div>

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
