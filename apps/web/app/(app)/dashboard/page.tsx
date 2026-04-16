'use client'

import { useEffect, useState, useCallback } from 'react'
import { supabase } from '@/lib/supabase'
import { ShoppingBag, Users, TrendingUp, Activity, RefreshCw, Percent } from 'lucide-react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, BarChart, Bar
} from 'recharts'

// ── Types ──────────────────────────────────────────────────────────────────────

type DateRange = '7d' | '30d' | '90d'

interface KPIs {
  totalRevenue: number
  totalOrders: number
  totalVisitors: number
  avgOrderValue: number
  revenueChange: number
  ordersChange: number
  conversionRate: number
}

interface RevenuePoint { date: string; revenue: number; orders: number }

interface Order {
  id: string
  email: string
  total_price: number
  financial_status: string
  platform_source: string
  created_at: string
}

interface TopSource { source: string; orders: number; revenue: number }

interface FunnelStep { label: string; count: number; pct: number }

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmt = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n)

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })

// ── Components ────────────────────────────────────────────────────────────────

function KPICard({ title, value, icon: Icon, change, color }: {
  title: string
  value: string
  icon: React.ElementType
  change?: number
  color: string
}) {
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
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
    </div>
  )
}

function FunnelBar({ steps }: { steps: FunnelStep[] }) {
  const colors = ['#6366f1', '#8b5cf6', '#a855f7', '#ec4899', '#10b981']
  return (
    <div className="space-y-3">
      {steps.map((step, i) => (
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
        </div>
      ))}
    </div>
  )
}

// ── Main Dashboard ────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const [kpis, setKpis] = useState<KPIs | null>(null)
  const [revenueData, setRevenueData] = useState<RevenuePoint[]>([])
  const [recentOrders, setRecentOrders] = useState<Order[]>([])
  const [topSources, setTopSources] = useState<TopSource[]>([])
  const [funnelSteps, setFunnelSteps] = useState<FunnelStep[]>([])
  const [loading, setLoading] = useState(true)
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date())
  const [dateRange, setDateRange] = useState<DateRange>('30d')

  const CLIENT_PIXEL_ID = 'lk-sneakers'

  const loadData = useCallback(async (range: DateRange) => {
    setLoading(true)

    const { data: clientData } = await supabase
      .from('clients').select('id')
      .eq('pixel_id', CLIENT_PIXEL_ID).limit(1).single()

    if (!clientData) { setLoading(false); return }

    const clientId = clientData.id
    const days = range === '7d' ? 7 : range === '30d' ? 30 : 90
    const startDate = new Date(); startDate.setDate(startDate.getDate() - days)
    const prevStart = new Date(); prevStart.setDate(prevStart.getDate() - days * 2)

    const [
      { data: orders },
      { data: ordersPrev },
      { count: visitorCount },
      { data: events },
    ] = await Promise.all([
      supabase.from('orders')
        .select('id, email, total_price, financial_status, platform_source, utm_source, created_at')
        .eq('client_id', clientId)
        .gte('created_at', startDate.toISOString())
        .order('created_at', { ascending: false }),
      supabase.from('orders')
        .select('total_price')
        .eq('client_id', clientId)
        .gte('created_at', prevStart.toISOString())
        .lt('created_at', startDate.toISOString()),
      supabase.from('visitors')
        .select('id', { count: 'exact', head: true })
        .eq('client_id', clientId),
      supabase.from('tracking_events')
        .select('event_type, visitor_id')
        .eq('client_id', clientId)
        .gte('created_at', startDate.toISOString()),
    ])

    const allOrders = orders || []
    const prevOrders = ordersPrev || []
    const allEvents = events || []
    const totalVisitors = visitorCount || 0

    const totalRevenue = allOrders.reduce((s, o) => s + (o.total_price || 0), 0)
    const prevRevenue = prevOrders.reduce((s, o) => s + (o.total_price || 0), 0)
    const avgOrderValue = allOrders.length ? totalRevenue / allOrders.length : 0
    const conversionRate = totalVisitors > 0 ? (allOrders.length / totalVisitors) * 100 : 0

    setKpis({
      totalRevenue,
      totalOrders: allOrders.length,
      totalVisitors,
      avgOrderValue,
      revenueChange: prevRevenue ? ((totalRevenue - prevRevenue) / prevRevenue) * 100 : 0,
      ordersChange: prevOrders.length ? ((allOrders.length - prevOrders.length) / prevOrders.length) * 100 : 0,
      conversionRate,
    })

    setRecentOrders(allOrders.slice(0, 8) as Order[])

    // Revenue chart — number of days shown depends on range
    const chartDays = range === '7d' ? 7 : range === '30d' ? 14 : 30
    const byDay: Record<string, { revenue: number; orders: number }> = {}
    allOrders.forEach(o => {
      const day = fmtDate(o.created_at)
      if (!byDay[day]) byDay[day] = { revenue: 0, orders: 0 }
      byDay[day].revenue += o.total_price || 0
      byDay[day].orders += 1
    })
    const points: RevenuePoint[] = []
    for (let i = chartDays - 1; i >= 0; i--) {
      const d = new Date(); d.setDate(d.getDate() - i)
      const label = fmtDate(d.toISOString())
      points.push({ date: label, ...(byDay[label] || { revenue: 0, orders: 0 }) })
    }
    setRevenueData(points)

    // Top sources
    const srcMap: Record<string, { orders: number; revenue: number }> = {}
    allOrders.forEach((o: any) => {
      const src = o.utm_source || 'direto'
      if (!srcMap[src]) srcMap[src] = { orders: 0, revenue: 0 }
      srcMap[src].orders += 1
      srcMap[src].revenue += o.total_price || 0
    })
    setTopSources(
      Object.entries(srcMap)
        .map(([source, v]) => ({ source, ...v }))
        .sort((a, b) => b.revenue - a.revenue)
        .slice(0, 5)
    )

    // Conversion funnel
    const uniq = (type: string) =>
      new Set(allEvents.filter(e => e.event_type === type).map(e => e.visitor_id)).size

    const pageviews     = uniq('pageview')
    const productViewed = uniq('view_product')
    const addToCart     = uniq('add_to_cart')
    const checkout      = uniq('begin_checkout')
    const purchases     = allOrders.length
    const top           = pageviews || 1

    setFunnelSteps([
      { label: 'Pageviews',          count: pageviews,     pct: 100 },
      { label: 'Produto Visto',      count: productViewed, pct: (productViewed / top) * 100 },
      { label: 'Add ao Carrinho',    count: addToCart,     pct: (addToCart / top) * 100 },
      { label: 'Checkout Iniciado',  count: checkout,      pct: (checkout / top) * 100 },
      { label: 'Compras',            count: purchases,     pct: (purchases / top) * 100 },
    ])

    setLastUpdate(new Date())
    setLoading(false)
  }, [])

  useEffect(() => { loadData(dateRange) }, [dateRange, loadData])

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">

      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white">LK Sneakers</h1>
          <p className="text-xs text-slate-500">Tracking Dashboard</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Date Range Tabs */}
          <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
            {(['7d', '30d', '90d'] as DateRange[]).map(r => (
              <button
                key={r}
                onClick={() => setDateRange(r)}
                className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                  dateRange === r
                    ? 'bg-indigo-600 text-white'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {r === '7d' ? '7 dias' : r === '30d' ? '30 dias' : '90 dias'}
              </button>
            ))}
          </div>
          <button
            onClick={() => loadData(dateRange)}
            className="flex items-center gap-2 text-xs text-slate-400 hover:text-white transition-colors"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            {lastUpdate.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}
          </button>
        </div>
      </div>

      <div className="p-6 space-y-6">

        {/* KPIs */}
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
          <KPICard
            title="Receita"
            value={kpis ? fmt(kpis.totalRevenue) : '—'}
            icon={TrendingUp}
            change={kpis?.revenueChange}
            color="bg-emerald-500/10 text-emerald-400"
          />
          <KPICard
            title="Pedidos"
            value={kpis ? kpis.totalOrders.toString() : '—'}
            icon={ShoppingBag}
            change={kpis?.ordersChange}
            color="bg-blue-500/10 text-blue-400"
          />
          <KPICard
            title="Visitantes"
            value={kpis ? kpis.totalVisitors.toString() : '—'}
            icon={Users}
            color="bg-purple-500/10 text-purple-400"
          />
          <KPICard
            title="Ticket Médio"
            value={kpis ? fmt(kpis.avgOrderValue) : '—'}
            icon={Activity}
            color="bg-orange-500/10 text-orange-400"
          />
          <KPICard
            title="Conversão"
            value={kpis ? kpis.conversionRate.toFixed(1) + '%' : '—'}
            icon={Percent}
            color="bg-pink-500/10 text-pink-400"
          />
        </div>

        {/* Revenue chart + Funnel */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2 bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">
              Receita —{' '}
              {dateRange === '7d' ? 'últimos 7 dias' : dateRange === '30d' ? 'últimos 14 dias' : 'últimos 30 dias'}
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
              <p className="text-slate-500 text-sm">Sem dados de eventos no período</p>
            ) : (
              <FunnelBar steps={funnelSteps} />
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

          {/* Top Sources */}
          <div className="bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">Top Fontes de Tráfego</h2>
            {topSources.length === 0 ? (
              <p className="text-slate-500 text-sm">Sem dados de UTM ainda</p>
            ) : (
              <ResponsiveContainer width="100%" height={160}>
                <BarChart data={topSources} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2f3e" horizontal={false} />
                  <XAxis type="number" tick={{ fill: '#64748b', fontSize: 11 }} tickFormatter={v => `R$${v}`} />
                  <YAxis type="category" dataKey="source" tick={{ fill: '#94a3b8', fontSize: 12 }} width={60} />
                  <Tooltip
                    contentStyle={{ background: '#1a1f2e', border: '1px solid #2a2f3e', borderRadius: 8 }}
                    formatter={(v) => [fmt(Number(v)), 'Receita']}
                  />
                  <Bar dataKey="revenue" fill="#6366f1" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>

          {/* Recent Orders */}
          <div className="bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">Pedidos Recentes</h2>
            <div className="space-y-2 overflow-auto max-h-[200px]">
              {recentOrders.length === 0 ? (
                <p className="text-slate-500 text-sm">Nenhum pedido ainda</p>
              ) : recentOrders.map(order => (
                <div key={order.id} className="flex items-center justify-between py-2 border-b border-[#2a2f3e] last:border-0">
                  <div className="min-w-0">
                    <p className="text-sm text-slate-200 truncate">{order.email}</p>
                    <p className="text-xs text-slate-500">{fmtDate(order.created_at)} · {order.platform_source}</p>
                  </div>
                  <div className="text-right ml-4 shrink-0">
                    <p className="text-sm font-medium text-emerald-400">{fmt(order.total_price)}</p>
                    <span className={`text-xs px-1.5 py-0.5 rounded ${
                      order.financial_status === 'paid'
                        ? 'bg-emerald-500/10 text-emerald-400'
                        : 'bg-yellow-500/10 text-yellow-400'
                    }`}>
                      {order.financial_status || 'pendente'}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>

        </div>
      </div>
    </div>
  )
}
