'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import {
  ShoppingBag, RefreshCw, Loader2, AlertCircle, CheckCircle,
  XCircle, Clock, TrendingUp, TrendingDown, Package, Tag,
  BarChart2, Zap, Settings, ChevronDown, ChevronUp,
} from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Snapshot {
  snapshot_date:            string
  total_products:           number
  approved_products:        number
  pending_products:         number
  disapproved_products:     number
  out_of_stock_products:    number
  products_with_errors:     number
  products_with_warnings:   number
  total_errors:             number
  total_warnings:           number
  feed_health_score:        number
  top_issue_codes:          { code: string; count: number }[]
  products_above_market_price: number
  products_below_market_price: number
  avg_price_difference_pct:    number | null
}

interface HealthPoint {
  snapshot_date:     string
  feed_health_score: number
  approved_products: number
  total_products:    number
}

interface Product {
  product_id:   string
  offer_id:     string
  title:        string
  brand:        string | null
  price:        number | null
  sale_price:   number | null
  currency:     string | null
  availability: string
  image_link:   string | null
  link:         string | null
}

interface Issue {
  code:        string
  severity:    string
  description: string
  count:       number
}

interface PriceSummary {
  total_with_benchmark:     number
  competitive:              number
  above_market:             number
  below_market:             number
  avg_price_difference_pct: number | null
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function ScoreBadge({ score }: { score: number }) {
  const color = score >= 80 ? 'text-emerald-400' : score >= 60 ? 'text-yellow-400' : 'text-red-400'
  return <span className={`text-4xl font-bold ${color}`}>{score}</span>
}

function ApprovalBar({ snap }: { snap: Snapshot }) {
  const total    = snap.total_products || 1
  const approvedPct    = (snap.approved_products / total) * 100
  const pendingPct     = (snap.pending_products / total) * 100
  const disapprovedPct = (snap.disapproved_products / total) * 100
  return (
    <div className="mt-2">
      <div className="flex h-3 rounded-full overflow-hidden bg-[#1a1f2e] gap-0.5">
        <div className="bg-emerald-500 transition-all" style={{ width: `${approvedPct}%` }} title={`${snap.approved_products} aprovados`} />
        <div className="bg-yellow-500 transition-all"  style={{ width: `${pendingPct}%`     }} title={`${snap.pending_products} pendentes`} />
        <div className="bg-red-500 transition-all"     style={{ width: `${disapprovedPct}%` }} title={`${snap.disapproved_products} reprovados`} />
      </div>
      <div className="flex gap-4 mt-1.5 text-xs">
        <span className="flex items-center gap-1 text-emerald-400"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" />{snap.approved_products} aprovados</span>
        <span className="flex items-center gap-1 text-yellow-400"><span className="w-2 h-2 rounded-full bg-yellow-500 inline-block" />{snap.pending_products} pendentes</span>
        <span className="flex items-center gap-1 text-red-400"><span className="w-2 h-2 rounded-full bg-red-500 inline-block" />{snap.disapproved_products} reprovados</span>
      </div>
    </div>
  )
}

function SeverityIcon({ severity }: { severity: string }) {
  if (severity === 'error')   return <XCircle size={14} className="text-red-400 shrink-0" />
  if (severity === 'warning') return <AlertCircle size={14} className="text-yellow-400 shrink-0" />
  return <CheckCircle size={14} className="text-slate-400 shrink-0" />
}

function fmt(n: number | null | undefined, decimals = 0) {
  if (n == null) return '—'
  return n.toLocaleString('pt-BR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

function fmtPct(n: number | null | undefined, decimals = 1) {
  if (n == null) return '—'
  return `${n >= 0 ? '+' : ''}${n.toFixed(decimals)}%`
}

// ── Tab types ──────────────────────────────────────────────────────────────────
type Tab = 'overview' | 'products' | 'issues' | 'pricing'

// ── Page ──────────────────────────────────────────────────────────────────────

export default function MerchantCenterPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [tab, setTab]                 = useState<Tab>('overview')
  const [loading, setLoading]         = useState(true)
  const [syncing, setSyncing]         = useState(false)
  const [configured, setConfigured]   = useState(false)
  const [snapshot, setSnapshot]       = useState<Snapshot | null>(null)
  const [history, setHistory]         = useState<HealthPoint[]>([])
  const [products, setProducts]       = useState<Product[]>([])
  const [issues, setIssues]           = useState<Issue[]>([])
  const [pricing, setPricing]         = useState<PriceSummary | null>(null)
  const [productPage, setProductPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [error, setError]             = useState<string | null>(null)

  const base = `${API_URL}/merchant-center/${clientId}`

  const loadSummary = useCallback(async () => {
    try {
      const r = await fetch(`${base}/summary`)
      const d = await r.json()
      setConfigured(!!d.configured)
      if (d.has_data) {
        setSnapshot(d.snapshot || null)
        setHistory(d.trend_7d || [])
      }
    } catch (e) {
      setError('Falha ao carregar dados do Merchant Center')
    }
  }, [base])

  const loadProducts = useCallback(async () => {
    const params = new URLSearchParams({ page: String(productPage), per_page: '50' })
    if (statusFilter) params.set('status', statusFilter)
    const r = await fetch(`${base}/products?${params}`)
    const d = await r.json()
    setProducts(d.products || [])
  }, [base, productPage, statusFilter])

  const loadIssues = useCallback(async () => {
    const r = await fetch(`${base}/issues`)
    setIssues(await r.json() || [])
  }, [base])

  const loadPricing = useCallback(async () => {
    const r = await fetch(`${base}/pricing`)
    setPricing(await r.json())
  }, [base])

  useEffect(() => {
    setLoading(true)
    loadSummary().finally(() => setLoading(false))
  }, [loadSummary])

  useEffect(() => {
    if (tab === 'products') loadProducts()
    if (tab === 'issues')   loadIssues()
    if (tab === 'pricing')  loadPricing()
  }, [tab, loadProducts, loadIssues, loadPricing])

  async function forceSync() {
    setSyncing(true)
    try {
      const r = await fetch(`${base}/sync`, { method: 'POST' })
      if (!r.ok) {
        const e = await r.json()
        setError(e.detail || 'Erro ao sincronizar')
      } else {
        await loadSummary()
      }
    } finally {
      setSyncing(false)
    }
  }

  if (loading) return (
    <div className="flex items-center justify-center h-96">
      <Loader2 size={24} className="animate-spin text-indigo-400" />
    </div>
  )

  if (!configured) return (
    <div className="p-8 max-w-lg mx-auto mt-16 text-center">
      <ShoppingBag size={48} className="text-indigo-400 mx-auto mb-4" />
      <h2 className="text-xl font-bold text-white mb-2">Google Merchant Center</h2>
      <p className="text-slate-400 mb-6">
        Conecte o Merchant Center para monitorar saúde do feed, reprovações e competitividade de preços.
      </p>
      <a href={`/clients/${clientId}/settings`} className="inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm transition-colors">
        <Settings size={15} />
        Configurar nas Configurações
      </a>
    </div>
  )

  const TABS: { key: Tab; label: string; icon: React.ElementType }[] = [
    { key: 'overview', label: 'Visão Geral',   icon: BarChart2    },
    { key: 'products', label: 'Produtos',       icon: Package      },
    { key: 'issues',   label: 'Issues',         icon: AlertCircle  },
    { key: 'pricing',  label: 'Preços',         icon: Tag          },
  ]

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <ShoppingBag size={22} className="text-indigo-400" />
          <h1 className="text-xl font-bold text-white">Merchant Center</h1>
        </div>
        <button
          onClick={forceSync}
          disabled={syncing}
          className="flex items-center gap-2 text-sm text-slate-400 hover:text-white transition-colors disabled:opacity-50"
        >
          <RefreshCw size={15} className={syncing ? 'animate-spin' : ''} />
          {syncing ? 'Sincronizando…' : 'Sincronizar agora'}
        </button>
      </div>

      {error && (
        <div className="bg-red-900/20 border border-red-800 rounded-lg px-4 py-3 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1">
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-2 flex-1 justify-center px-3 py-2 rounded-md text-sm transition-colors ${
              tab === t.key
                ? 'bg-indigo-600 text-white font-medium'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            <t.icon size={14} />
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Overview ── */}
      {tab === 'overview' && (
        <div className="space-y-5">
          {!snapshot ? (
            <div className="text-center py-16 text-slate-500">
              <Clock size={32} className="mx-auto mb-3 opacity-50" />
              <p>Nenhum dado ainda. Clique em "Sincronizar agora" para coletar o primeiro snapshot.</p>
            </div>
          ) : (
            <>
              {/* Score + bar */}
              <div className="bg-[#1a1f2e] rounded-xl p-5">
                <div className="flex items-start justify-between mb-4">
                  <div>
                    <p className="text-slate-400 text-sm mb-1">Feed Health Score</p>
                    <div className="flex items-baseline gap-2">
                      <ScoreBadge score={snapshot.feed_health_score} />
                      <span className="text-slate-500 text-sm">/100</span>
                    </div>
                    <p className="text-slate-500 text-xs mt-1">{snapshot.snapshot_date}</p>
                  </div>
                  <div className="grid grid-cols-2 gap-3 text-right">
                    <div>
                      <p className="text-2xl font-bold text-white">{fmt(snapshot.total_products)}</p>
                      <p className="text-slate-500 text-xs">Total produtos</p>
                    </div>
                    <div>
                      <p className="text-2xl font-bold text-red-400">{fmt(snapshot.total_errors)}</p>
                      <p className="text-slate-500 text-xs">Total erros</p>
                    </div>
                  </div>
                </div>
                <ApprovalBar snap={snapshot} />
              </div>

              {/* KPI cards */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {[
                  { label: 'Com erros',   value: snapshot.products_with_errors,   color: 'text-red-400' },
                  { label: 'Avisos',      value: snapshot.products_with_warnings, color: 'text-yellow-400' },
                  { label: 'Sem estoque', value: snapshot.out_of_stock_products,  color: 'text-slate-400' },
                  { label: 'Warnings',    value: snapshot.total_warnings,         color: 'text-orange-400' },
                ].map(k => (
                  <div key={k.label} className="bg-[#1a1f2e] rounded-lg p-4">
                    <p className={`text-2xl font-bold ${k.color}`}>{fmt(k.value)}</p>
                    <p className="text-slate-500 text-xs mt-1">{k.label}</p>
                  </div>
                ))}
              </div>

              {/* Health trend */}
              {history.length > 1 && (
                <div className="bg-[#1a1f2e] rounded-xl p-5">
                  <h3 className="text-sm font-semibold text-white mb-3">Trend — últimos 7 dias</h3>
                  <div className="flex items-end gap-1 h-24">
                    {history.map((h, i) => {
                      const pct = h.feed_health_score
                      const color = pct >= 80 ? 'bg-emerald-500' : pct >= 60 ? 'bg-yellow-500' : 'bg-red-500'
                      return (
                        <div key={i} className="flex-1 flex flex-col items-center gap-1" title={`${h.snapshot_date}: ${pct}`}>
                          <div className={`w-full rounded-t ${color}`} style={{ height: `${pct}%` }} />
                          <span className="text-[10px] text-slate-500">{h.snapshot_date.slice(5)}</span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* Top issues */}
              {(snapshot.top_issue_codes || []).length > 0 && (
                <div className="bg-[#1a1f2e] rounded-xl p-5">
                  <h3 className="text-sm font-semibold text-white mb-3">Top issues</h3>
                  <div className="space-y-2">
                    {snapshot.top_issue_codes.slice(0, 5).map(item => (
                      <div key={item.code} className="flex items-center justify-between">
                        <span className="text-sm text-slate-300 font-mono">{item.code}</span>
                        <span className="text-sm text-slate-400">{item.count} produtos</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* ── Products ── */}
      {tab === 'products' && (
        <div className="space-y-4">
          <div className="flex gap-3">
            <select
              value={statusFilter}
              onChange={e => { setStatusFilter(e.target.value); setProductPage(1) }}
              className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
            >
              <option value="">Todos os status</option>
              <option value="approved">Aprovados</option>
              <option value="pending">Pendentes</option>
              <option value="disapproved">Reprovados</option>
            </select>
          </div>
          <div className="bg-[#1a1f2e] rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#2a2f3e]">
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Produto</th>
                  <th className="text-right px-4 py-3 text-slate-400 font-medium">Preço</th>
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Estoque</th>
                </tr>
              </thead>
              <tbody>
                {products.length === 0 ? (
                  <tr><td colSpan={3} className="py-12 text-center text-slate-500">Nenhum produto encontrado</td></tr>
                ) : products.map(p => (
                  <tr key={p.product_id} className="border-b border-[#1f2433] hover:bg-[#1f2433] transition-colors">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        {p.image_link ? (
                          <img src={p.image_link} alt="" className="w-10 h-10 rounded object-cover bg-[#2a2f3e]" />
                        ) : (
                          <div className="w-10 h-10 rounded bg-[#2a2f3e] flex items-center justify-center">
                            <Package size={16} className="text-slate-500" />
                          </div>
                        )}
                        <div>
                          <p className="text-white font-medium line-clamp-1">{p.title}</p>
                          {p.brand && <p className="text-slate-500 text-xs">{p.brand}</p>}
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right text-white">
                      {p.sale_price != null ? (
                        <div>
                          <span className="line-through text-slate-500 text-xs mr-1">{fmt(p.price, 2)}</span>
                          <span className="text-emerald-400">{fmt(p.sale_price, 2)}</span>
                        </div>
                      ) : (
                        fmt(p.price, 2)
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${
                        p.availability === 'in_stock'
                          ? 'bg-emerald-900/40 text-emerald-400'
                          : p.availability === 'out_of_stock'
                          ? 'bg-red-900/40 text-red-400'
                          : 'bg-yellow-900/40 text-yellow-400'
                      }`}>
                        {p.availability?.replace(/_/g, ' ') || '—'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {products.length === 50 && (
            <div className="flex justify-center">
              <button
                onClick={() => setProductPage(p => p + 1)}
                className="text-indigo-400 hover:text-indigo-300 text-sm"
              >
                Carregar mais
              </button>
            </div>
          )}
        </div>
      )}

      {/* ── Issues ── */}
      {tab === 'issues' && (
        <div className="space-y-3">
          {issues.length === 0 ? (
            <div className="text-center py-16 text-slate-500">
              <CheckCircle size={32} className="mx-auto mb-3 opacity-50" />
              <p>Nenhuma issue encontrada.</p>
            </div>
          ) : issues.map(issue => (
            <div key={issue.code} className="bg-[#1a1f2e] rounded-xl p-4 flex items-start gap-3">
              <SeverityIcon severity={issue.severity} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-sm text-white">{issue.code}</span>
                  <span className="text-slate-400 text-sm shrink-0">{issue.count} produto{issue.count !== 1 ? 's' : ''}</span>
                </div>
                {issue.description && (
                  <p className="text-slate-400 text-xs mt-1 line-clamp-2">{issue.description}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Pricing ── */}
      {tab === 'pricing' && (
        <div className="space-y-4">
          {!pricing || pricing.total_with_benchmark === 0 ? (
            <div className="text-center py-16 text-slate-500">
              <Tag size={32} className="mx-auto mb-3 opacity-50" />
              <p>Dados de benchmark de preço ainda não disponíveis.</p>
              <p className="text-xs mt-1 text-slate-600">O Google leva alguns dias para calcular benchmarks após o primeiro sync.</p>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="bg-[#1a1f2e] rounded-lg p-4">
                  <p className="text-2xl font-bold text-white">{fmt(pricing.total_with_benchmark)}</p>
                  <p className="text-slate-500 text-xs mt-1">Com benchmark</p>
                </div>
                <div className="bg-[#1a1f2e] rounded-lg p-4">
                  <p className="text-2xl font-bold text-emerald-400">{fmt(pricing.competitive)}</p>
                  <p className="text-slate-500 text-xs mt-1">Competitivos</p>
                </div>
                <div className="bg-[#1a1f2e] rounded-lg p-4">
                  <p className="text-2xl font-bold text-red-400">{fmt(pricing.above_market)}</p>
                  <p className="text-slate-500 text-xs mt-1">Acima do mercado</p>
                </div>
                <div className="bg-[#1a1f2e] rounded-lg p-4">
                  <p className="text-2xl font-bold text-blue-400">{fmt(pricing.below_market)}</p>
                  <p className="text-slate-500 text-xs mt-1">Abaixo do mercado</p>
                </div>
              </div>
              {pricing.avg_price_difference_pct != null && (
                <div className="bg-[#1a1f2e] rounded-xl p-5">
                  <div className="flex items-center gap-3">
                    {pricing.avg_price_difference_pct > 0 ? (
                      <TrendingUp size={20} className="text-red-400" />
                    ) : (
                      <TrendingDown size={20} className="text-emerald-400" />
                    )}
                    <div>
                      <p className="text-2xl font-bold text-white">{fmtPct(pricing.avg_price_difference_pct)}</p>
                      <p className="text-slate-500 text-sm">diferença média vs benchmark de mercado</p>
                    </div>
                  </div>
                  <p className="text-slate-400 text-xs mt-3">
                    {pricing.avg_price_difference_pct > 5
                      ? 'Seus preços estão acima da média do mercado. Considere revisar a estratégia de preços para aumentar a competitividade.'
                      : pricing.avg_price_difference_pct < -5
                      ? 'Seus preços estão abaixo da média do mercado. Boa posição competitiva.'
                      : 'Seus preços estão alinhados com o mercado.'}
                  </p>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
