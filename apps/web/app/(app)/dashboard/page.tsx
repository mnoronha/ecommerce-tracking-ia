'use client'

import { useEffect, useState, useCallback } from 'react'
import { supabase } from '@/lib/supabase'
import { ShoppingBag, Users, TrendingUp, Activity, RefreshCw, Percent, CheckCircle } from 'lucide-react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer,
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
  email: string | null
  total_price: number
  financial_status: string | null
  platform_source: string | null
  utm_source: string | null
  utm_medium: string | null
  utm_campaign: string | null
  created_at: string
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

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmt = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n)

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })

const pct = (n: number, total: number) =>
  total > 0 ? ((n / total) * 100).toFixed(0) + '%' : '—'

// ── Sub-components ────────────────────────────────────────────────────────────

function KPICard({ title, value, icon: Icon, change, color }: {
  title: string; value: string; icon: React.ElementType
  change?: number; color: string
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
  const [kpis, setKpis]               = useState<KPIs | null>(null)
  const [revenueData, setRevenueData] = useState<RevenuePoint[]>([])
  const [recentOrders, setRecentOrders] = useState<Order[]>([])
  const [funnelSteps, setFunnelSteps] = useState<FunnelStep[]>([])
  const [campaigns, setCampaigns]     = useState<CampaignRow[]>([])
  const [products, setProducts]       = useState<ProductRow[]>([])
  const [attribution, setAttribution] = useState<Attribution | null>(null)
  const [loading, setLoading]         = useState(true)
  const [lastUpdate, setLastUpdate]   = useState<Date>(new Date())
  const [dateRange, setDateRange]     = useState<DateRange>('30d')

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
      { data: productEvents },
    ] = await Promise.all([
      supabase.from('orders')
        .select('id, email, total_price, financial_status, platform_source, utm_source, utm_medium, utm_campaign, created_at')
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
      supabase.from('tracking_events')
        .select('event_type, product_name')
        .eq('client_id', clientId)
        .gte('created_at', startDate.toISOString())
        .not('product_name', 'is', null),
    ])

    const allOrders  = orders || []
    const prevOrders = ordersPrev || []
    const allEvents  = events || []
    const prodEvents = productEvents || []
    const totalVisitors = visitorCount || 0

    // ── KPIs ─────────────────────────────────────────────────────────────────
    const totalRevenue   = allOrders.reduce((s, o) => s + (o.total_price || 0), 0)
    const prevRevenue    = prevOrders.reduce((s, o) => s + (o.total_price || 0), 0)
    const avgOrderValue  = allOrders.length ? totalRevenue / allOrders.length : 0
    const conversionRate = totalVisitors > 0 ? (allOrders.length / totalVisitors) * 100 : 0

    setKpis({
      totalRevenue, totalOrders: allOrders.length, totalVisitors, avgOrderValue,
      revenueChange: prevRevenue ? ((totalRevenue - prevRevenue) / prevRevenue) * 100 : 0,
      ordersChange:  prevOrders.length ? ((allOrders.length - prevOrders.length) / prevOrders.length) * 100 : 0,
      conversionRate,
    })

    setRecentOrders(allOrders.slice(0, 6) as Order[])

    // ── Revenue chart ─────────────────────────────────────────────────────────
    const chartDays = range === '7d' ? 7 : range === '30d' ? 14 : 30
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

    // ── Conversion funnel ─────────────────────────────────────────────────────
    const uniq = (type: string) =>
      new Set(allEvents.filter(e => e.event_type === type).map(e => e.visitor_id)).size
    const pageviews     = uniq('pageview')
    const productViewed = uniq('view_product')
    const addToCart     = uniq('add_to_cart')
    const checkout      = uniq('begin_checkout')
    const purchases     = allOrders.length
    const top           = pageviews || 1
    setFunnelSteps([
      { label: 'Pageviews',         count: pageviews,     pct: 100 },
      { label: 'Produto Visto',     count: productViewed, pct: (productViewed / top) * 100 },
      { label: 'Add ao Carrinho',   count: addToCart,     pct: (addToCart / top) * 100 },
      { label: 'Checkout Iniciado', count: checkout,      pct: (checkout / top) * 100 },
      { label: 'Compras',           count: purchases,     pct: (purchases / top) * 100 },
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
          <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
            {(['7d', '30d', '90d'] as DateRange[]).map(r => (
              <button key={r} onClick={() => setDateRange(r)}
                className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                  dateRange === r ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
                }`}>
                {r === '7d' ? '7 dias' : r === '30d' ? '30 dias' : '90 dias'}
              </button>
            ))}
          </div>
          <button onClick={() => loadData(dateRange)}
            className="flex items-center gap-2 text-xs text-slate-400 hover:text-white transition-colors">
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            {lastUpdate.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}
          </button>
        </div>
      </div>

      <div className="p-6 space-y-6">

        {/* KPIs */}
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
          <KPICard title="Receita"      value={kpis ? fmt(kpis.totalRevenue) : '—'}
            icon={TrendingUp} change={kpis?.revenueChange} color="bg-emerald-500/10 text-emerald-400" />
          <KPICard title="Pedidos"      value={kpis ? kpis.totalOrders.toString() : '—'}
            icon={ShoppingBag} change={kpis?.ordersChange} color="bg-blue-500/10 text-blue-400" />
          <KPICard title="Visitantes"   value={kpis ? kpis.totalVisitors.toString() : '—'}
            icon={Users} color="bg-purple-500/10 text-purple-400" />
          <KPICard title="Ticket Médio" value={kpis ? fmt(kpis.avgOrderValue) : '—'}
            icon={Activity} color="bg-orange-500/10 text-orange-400" />
          <KPICard title="Conversão"    value={kpis ? kpis.conversionRate.toFixed(1) + '%' : '—'}
            icon={Percent} color="bg-pink-500/10 text-pink-400" />
        </div>

        {/* Revenue + Funnel */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2 bg-[#1a1f2e] rounded-xl p-5 border border-[#2a2f3e]">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">
              Receita — {dateRange === '7d' ? 'últimos 7 dias' : dateRange === '30d' ? 'últimos 14 dias' : 'últimos 30 dias'}
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
      </div>
    </div>
  )
}
