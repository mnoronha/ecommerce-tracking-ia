'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import {
  Loader2, RefreshCw, Store, TrendingUp, ShoppingBag, Users,
  AlertTriangle, RotateCcw, ArrowUpRight,
} from 'lucide-react'
import { detectOutlier } from '@/lib/outlier-detection'
import { OutlierBadge } from '@/components/outlier-badge'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, BarChart, Bar, Cell,
} from 'recharts'
import { useDatePeriod, periodToQuery } from '@/lib/use-date-range'
import { PeriodPicker } from '@/components/PeriodPicker'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ─────────────────────────────────────────────────────────────────────

interface RevSummary {
  gmv: number; net_revenue: number; refunds: number
  paid_orders: number; pending_orders: number; refund_orders: number; voided_orders: number
  avg_ticket: number; new_customers: number; returning_customers: number
}

interface DailyPoint  { date: string; revenue: number; orders: number }
interface Channel     { channel: string; revenue: number; orders: number; pct: number }
interface Product     { name: string; sku: string | null; units: number; revenue: number }
interface GeoRow      { country: string; orders: number; revenue: number; pct: number }
interface UTMRow      { source: string; medium: string; campaign: string; orders: number; revenue: number; pct: number }

interface RevenueData {
  period:          { start: string; end: string; days: number }
  client_name:     string
  summary:         RevSummary
  prev:            RevSummary
  deltas:          { gmv: number | null; paid_orders: number | null; avg_ticket: number | null; net_revenue: number | null }
  daily:           DailyPoint[]
  channels:        Channel[]
  top_products:    Product[]
  geo_breakdown:   GeoRow[]
  utm_breakdown:   UTMRow[]
  status_dist:     { paid: number; pending: number; refunded: number; voided: number }
  total_all_orders: number
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(n: number) {
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 })
}
function fmtN(n: number) { return n.toLocaleString('pt-BR') }

function Delta({ d }: { d: number | null }) {
  if (d === null) return <span className="text-xs text-slate-600">—</span>
  const pos = d >= 0
  return (
    <span className={`text-xs font-medium ${pos ? 'text-emerald-400' : 'text-red-400'}`}>
      {pos ? '▲' : '▼'} {Math.abs(d).toFixed(1)}% vs anterior
    </span>
  )
}

const CHANNEL_COLORS: Record<string, string> = {
  'Meta Ads':        '#3b82f6',
  'Google Ads':      '#ef4444',
  'Google Orgânico': '#f97316',
  'TikTok Ads':      '#ec4899',
  'Email / CRM':     '#eab308',
  'Orgânico':        '#10b981',
  'Direto':          '#64748b',
  'Outros':          '#8b5cf6',
}

type Tab = 'overview' | 'products' | 'channels' | 'geo'

// ── Sub-components ────────────────────────────────────────────────────────────

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`bg-[#0f1117] border border-[#2a2f3e] rounded-xl ${className}`}>{children}</div>
}

function CardHeader({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="px-5 py-4 border-b border-[#2a2f3e]">
      <h2 className="text-sm font-semibold text-white">{title}</h2>
      {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
    </div>
  )
}

function KPI({
  label, value, d, hint, color = 'text-white',
}: {
  label: string; value: string; d?: number | null; hint?: string; color?: string
}) {
  return (
    <Card className="p-4">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className={`text-2xl font-bold ${color}`}>{value}</p>
      {d !== undefined && <div className="mt-1"><Delta d={d} /></div>}
      {hint && <p className="text-xs text-slate-600 mt-0.5">{hint}</p>}
    </Card>
  )
}

// ── Tab: Overview ─────────────────────────────────────────────────────────────

function OverviewTab({ data }: { data: RevenueData }) {
  const { summary, daily, status_dist } = data
  const totalPaid = summary.paid_orders || 1

  return (
    <div className="space-y-6">
      {/* Daily revenue chart */}
      {daily.length > 0 && (
        <Card className="p-5">
          <h2 className="text-sm font-semibold text-white mb-4">Receita diária — pedidos pagos</h2>
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={daily} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="revGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#6366f1" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2435" />
              <XAxis
                dataKey="date"
                tick={{ fill: '#64748b', fontSize: 11 }}
                tickFormatter={d => d.slice(5)}
              />
              <YAxis
                tick={{ fill: '#64748b', fontSize: 11 }}
                width={60}
                tickFormatter={v => `R$${(v / 1000).toFixed(0)}k`}
              />
              <Tooltip
                contentStyle={{ background: '#1a1f2e', border: '1px solid #2a2f3e', borderRadius: 8 }}
                labelStyle={{ color: '#94a3b8' }}
                formatter={(v, name) => [
                  name === 'revenue' ? fmt(v as number) : fmtN(v as number),
                  name === 'revenue' ? 'Receita' : 'Pedidos',
                ] as [string, string]}
              />
              <Area
                type="monotone"
                dataKey="revenue"
                stroke="#6366f1"
                fill="url(#revGrad)"
                strokeWidth={2}
                dot={false}
                name="revenue"
              />
            </AreaChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* Status distribution */}
      <Card className="p-5">
        <h2 className="text-sm font-semibold text-white mb-5">Status dos pedidos</h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-6">
          {[
            { label: 'Pagos',        value: status_dist.paid,     color: 'text-emerald-400', bg: 'bg-emerald-500/10' },
            { label: 'Pendentes',    value: status_dist.pending,  color: 'text-amber-400',   bg: 'bg-amber-500/10' },
            { label: 'Reembolsados', value: status_dist.refunded, color: 'text-red-400',     bg: 'bg-red-500/10' },
            { label: 'Cancelados',   value: status_dist.voided,   color: 'text-slate-500',   bg: 'bg-slate-500/10' },
          ].map(({ label, value, color, bg }) => (
            <div key={label} className={`rounded-xl p-4 ${bg} text-center`}>
              <p className={`text-3xl font-bold ${color}`}>{fmtN(value)}</p>
              <p className="text-xs text-slate-400 mt-1">{label}</p>
              <p className="text-xs text-slate-600 mt-0.5">
                {totalPaid > 0 ? `${Math.round(value / totalPaid * 100)}%` : '—'} do total
              </p>
            </div>
          ))}
        </div>
      </Card>

      {/* New vs returning */}
      {(summary.new_customers > 0 || summary.returning_customers > 0) && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Card className="p-5">
            <div className="flex items-center gap-2 mb-3">
              <Users size={14} className="text-indigo-400" />
              <h3 className="text-sm font-semibold text-indigo-300">Novos clientes</h3>
            </div>
            <p className="text-3xl font-bold text-white">{fmtN(summary.new_customers)}</p>
            <p className="text-xs text-slate-500 mt-1">
              {summary.paid_orders > 0 ? `${Math.round(summary.new_customers / summary.paid_orders * 100)}%` : '—'} dos pedidos
            </p>
          </Card>
          <Card className="p-5">
            <div className="flex items-center gap-2 mb-3">
              <RotateCcw size={14} className="text-emerald-400" />
              <h3 className="text-sm font-semibold text-emerald-300">Clientes recorrentes</h3>
            </div>
            <p className="text-3xl font-bold text-white">{fmtN(summary.returning_customers)}</p>
            <p className="text-xs text-slate-500 mt-1">
              {summary.paid_orders > 0 ? `${Math.round(summary.returning_customers / summary.paid_orders * 100)}%` : '—'} dos pedidos
            </p>
          </Card>
        </div>
      )}
    </div>
  )
}

// ── Tab: Products ─────────────────────────────────────────────────────────────

function ProductsTab({ data }: { data: RevenueData }) {
  const totalRevenue = data.summary.gmv || 1
  const productUnitValues = data.top_products.map(p => p.units)
  return (
    <Card className="overflow-hidden">
      <CardHeader title="Top produtos por receita" sub={`${data.period.start} → ${data.period.end}`} />
      {data.top_products.length === 0 ? (
        <div className="p-8 text-center text-slate-500 text-sm">
          Nenhum produto encontrado. Os dados de itens são populados pelo sync do Shopify.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#2a2f3e]">
                {['#', 'Produto', 'SKU', 'Unidades', 'Receita', '% Total'].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs text-slate-500 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.top_products.map((p, i) => {
                const outlier = detectOutlier(p.units, productUnitValues)
                return (
                <tr key={i} className="border-b border-[#1a1f2e] hover:bg-[#1a1f2e] transition-colors">
                  <td className="px-4 py-3 text-slate-600 text-xs">{i + 1}</td>
                  <td className="px-4 py-3 text-slate-300 max-w-xs">
                    <div className="flex items-center gap-2">
                      <p className="truncate font-medium">{p.name}</p>
                      {outlier.isOutlier && (
                        <OutlierBadge
                          outlier={outlier}
                          tooltip={
                            outlier.direction === 'positive'
                              ? `${p.units} unidades — produto acima da média de vendas do período.`
                              : `${p.units} unidades — produto abaixo da média.`
                          }
                        />
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-500 text-xs">{p.sku || '—'}</td>
                  <td className="px-4 py-3 text-slate-300">{fmtN(p.units)}</td>
                  <td className="px-4 py-3 text-slate-300 font-medium">{fmt(p.revenue)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-16 h-1.5 bg-[#2a2f3e] rounded-full overflow-hidden">
                        <div
                          className="h-full bg-indigo-500 rounded-full"
                          style={{ width: `${Math.round(p.revenue / totalRevenue * 100)}%` }}
                        />
                      </div>
                      <span className="text-xs text-slate-500">
                        {Math.round(p.revenue / totalRevenue * 100)}%
                      </span>
                    </div>
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

// ── Tab: Channels ─────────────────────────────────────────────────────────────

function ChannelsTab({ data }: { data: RevenueData }) {
  const channelRevenueValues = data.channels.map(c => c.revenue)
  return (
    <div className="space-y-6">
      {/* Channel summary */}
      <Card className="overflow-hidden">
        <CardHeader title="Receita por canal" sub="Baseado em UTMs dos pedidos pagos" />
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#2a2f3e]">
                {['Canal', 'Pedidos', '% Receita', 'Receita', 'Ticket Médio'].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs text-slate-500 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.channels.map((ch, i) => {
                const outlier = detectOutlier(ch.revenue, channelRevenueValues)
                return (
                <tr key={i} className="border-b border-[#1a1f2e] hover:bg-[#1a1f2e] transition-colors">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span
                        className="w-2.5 h-2.5 rounded-full shrink-0"
                        style={{ backgroundColor: CHANNEL_COLORS[ch.channel] || '#94a3b8' }}
                      />
                      <span className="text-slate-300 font-medium">{ch.channel}</span>
                      {outlier.isOutlier && (
                        <OutlierBadge
                          outlier={outlier}
                          tooltip={
                            outlier.direction === 'positive'
                              ? `${ch.channel} representa ${ch.pct}% da receita — canal dominante no período.`
                              : `${ch.channel} com receita abaixo da média dos canais.`
                          }
                        />
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-300">{fmtN(ch.orders)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-20 h-1.5 bg-[#2a2f3e] rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${ch.pct}%`,
                            backgroundColor: CHANNEL_COLORS[ch.channel] || '#6366f1',
                          }}
                        />
                      </div>
                      <span className="text-slate-400 text-xs">{ch.pct}%</span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-300 font-medium">{fmt(ch.revenue)}</td>
                  <td className="px-4 py-3 text-slate-400 text-xs">
                    {ch.orders > 0 ? fmt(ch.revenue / ch.orders) : '—'}
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {/* UTM campaign breakdown */}
      {data.utm_breakdown.length > 0 && (
        <Card className="overflow-hidden">
          <CardHeader title="Campanhas (UTMs)" sub="Source / Medium / Campaign dos pedidos pagos" />
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#2a2f3e]">
                  {['Source', 'Medium', 'Campaign', 'Pedidos', 'Receita', '%'].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs text-slate-500 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.utm_breakdown.slice(0, 20).map((row, i) => (
                  <tr key={i} className="border-b border-[#1a1f2e] hover:bg-[#1a1f2e] transition-colors">
                    <td className="px-4 py-3 text-slate-300 text-xs font-mono">{row.source}</td>
                    <td className="px-4 py-3 text-slate-500 text-xs">{row.medium}</td>
                    <td className="px-4 py-3 text-slate-400 text-xs max-w-xs truncate">{row.campaign}</td>
                    <td className="px-4 py-3 text-slate-300">{fmtN(row.orders)}</td>
                    <td className="px-4 py-3 text-slate-300 font-medium">{fmt(row.revenue)}</td>
                    <td className="px-4 py-3 text-slate-500 text-xs">{row.pct}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}

// ── Tab: Geo ──────────────────────────────────────────────────────────────────

function GeoTab({ data }: { data: RevenueData }) {
  return (
    <Card className="overflow-hidden">
      <CardHeader title="Distribuição geográfica" sub="Baseado no endereço de entrega dos pedidos pagos" />
      {data.geo_breakdown.length === 0 ? (
        <div className="p-8 text-center text-slate-500 text-sm">
          Sem dados geográficos disponíveis.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#2a2f3e]">
                {['País', 'Pedidos', '% Pedidos', 'Receita'].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs text-slate-500 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.geo_breakdown.map((g, i) => (
                <tr key={i} className="border-b border-[#1a1f2e] hover:bg-[#1a1f2e] transition-colors">
                  <td className="px-4 py-3 text-slate-300 font-medium">{g.country}</td>
                  <td className="px-4 py-3 text-slate-300">{fmtN(g.orders)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-16 h-1.5 bg-[#2a2f3e] rounded-full overflow-hidden">
                        <div className="h-full bg-indigo-500 rounded-full" style={{ width: `${g.pct}%` }} />
                      </div>
                      <span className="text-xs text-slate-500">{g.pct}%</span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-300 font-medium">{fmt(g.revenue)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function ShopifyRevenuePage() {
  const { clientId } = useParams<{ clientId: string }>()
  const { period, from, to, setPreset, setCustom } = useDatePeriod()
  const [activeTab, setActiveTab] = useState<Tab>('overview')
  const [data,    setData]    = useState<RevenueData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const qs = periodToQuery(period, from, to)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_URL}/shopify/${clientId}/revenue?${qs}`)
      if (!res.ok) {
        const b = await res.json().catch(() => ({}))
        setError(b.detail || `Erro ${res.status}`)
        return
      }
      setData(await res.json())
    } catch {
      setError('Falha de rede')
    } finally {
      setLoading(false)
    }
  }, [clientId, qs])

  useEffect(() => { load() }, [load])

  const TABS: { id: Tab; label: string }[] = [
    { id: 'overview',  label: 'Visão Geral' },
    { id: 'products',  label: 'Produtos' },
    { id: 'channels',  label: 'Canais' },
    { id: 'geo',       label: 'Geográfico' },
  ]

  if (loading) return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <Loader2 size={28} className="animate-spin text-indigo-400" />
    </div>
  )

  if (error) return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-3">
      <AlertTriangle size={32} className="text-red-400" />
      <p className="text-sm text-red-400">{error}</p>
      <button onClick={load} className="text-xs text-slate-400 hover:text-white underline">
        Tentar novamente
      </button>
    </div>
  )

  if (!data) return null

  const { summary, deltas } = data

  return (
    <div className="space-y-6 p-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <Store size={20} className="text-indigo-400" />
            Faturamento Shopify
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Dados reais de pedidos · {data.period.start} → {data.period.end}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <PeriodPicker period={period} from={from} to={to} onPreset={setPreset} onCustom={setCustom} />
          <button
            onClick={load}
            className="p-2 text-slate-400 hover:text-white border border-[#2a2f3e] rounded-lg hover:border-slate-500 transition-colors"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* Primary KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPI
          label="GMV (pedidos pagos)"
          value={fmt(summary.gmv)}
          d={deltas.gmv}
          color="text-white"
        />
        <KPI
          label="Receita Líquida"
          value={fmt(summary.net_revenue)}
          d={deltas.net_revenue}
          hint={summary.refunds > 0 ? `−${fmt(summary.refunds)} em reembolsos` : undefined}
        />
        <KPI
          label="Pedidos Pagos"
          value={fmtN(summary.paid_orders)}
          d={deltas.paid_orders}
        />
        <KPI
          label="Ticket Médio"
          value={fmt(summary.avg_ticket)}
          d={deltas.avg_ticket}
        />
      </div>

      {/* Secondary KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPI
          label="Novos Clientes"
          value={fmtN(summary.new_customers)}
          hint={
            summary.paid_orders > 0
              ? `${Math.round(summary.new_customers / summary.paid_orders * 100)}% dos pedidos`
              : undefined
          }
          color="text-indigo-300"
        />
        <KPI
          label="Clientes Recorrentes"
          value={fmtN(summary.returning_customers)}
          color="text-emerald-300"
        />
        <KPI label="Pendentes"    value={fmtN(summary.pending_orders)} color="text-amber-300" />
        <KPI
          label="Reembolsados"
          value={fmtN(summary.refund_orders)}
          hint={summary.refunds > 0 ? fmt(summary.refunds) : undefined}
          color="text-red-300"
        />
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-[#2a2f3e] overflow-x-auto">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={`px-4 py-2.5 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
              activeTab === id
                ? 'border-indigo-500 text-indigo-400'
                : 'border-transparent text-slate-500 hover:text-slate-300'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'overview'  && <OverviewTab  data={data} />}
      {activeTab === 'products'  && <ProductsTab  data={data} />}
      {activeTab === 'channels'  && <ChannelsTab  data={data} />}
      {activeTab === 'geo'       && <GeoTab       data={data} />}

    </div>
  )
}
