'use client'

import { useEffect, useState } from 'react'
import { supabase } from '@/lib/supabase'
import { Search, ChevronLeft, ChevronRight } from 'lucide-react'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Order {
  id: string
  platform_order_number: string | null
  email: string | null
  total_price: number
  currency: string
  financial_status: string | null
  utm_source: string | null
  utm_medium: string | null
  utm_campaign: string | null
  platform_source: string | null
  created_at: string
}

type StatusFilter = 'all' | 'paid' | 'pending'

// ── Constants ─────────────────────────────────────────────────────────────────

const CLIENT_PIXEL_ID = process.env.NEXT_PUBLIC_CLIENT_PIXEL_ID || 'lk-sneakers'
const PAGE_SIZE = 25

const STATUS_STYLE: Record<string, string> = {
  paid:      'bg-emerald-500/10 text-emerald-400',
  pending:   'bg-yellow-500/10 text-yellow-400',
  refunded:  'bg-red-500/10 text-red-400',
  cancelled: 'bg-red-500/10 text-red-400',
  voided:    'bg-slate-500/10 text-slate-400',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmt = (n: number, currency = 'BRL') =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(n)

const fmtDt = (iso: string) =>
  new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function PedidosPage() {
  const [orders, setOrders]         = useState<Order[]>([])
  const [total, setTotal]           = useState(0)
  const [totalRevenue, setTotalRev] = useState(0)
  const [page, setPage]             = useState(0)
  const [search, setSearch]         = useState('')
  const [debSearch, setDebSearch]   = useState('')
  const [statusFilter, setStatus]   = useState<StatusFilter>('all')
  const [loading, setLoading]       = useState(true)
  const [clientId, setClientId]     = useState<string | null>(null)

  // Resolve client UUID once
  useEffect(() => {
    supabase.from('clients').select('id')
      .eq('pixel_id', CLIENT_PIXEL_ID).limit(1).single()
      .then(({ data }) => setClientId(data?.id ?? null))
  }, [])

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => { setDebSearch(search); setPage(0) }, 400)
    return () => clearTimeout(t)
  }, [search])

  // Reset page on filter change
  useEffect(() => { setPage(0) }, [statusFilter])

  // Load orders
  useEffect(() => {
    if (!clientId) return
    setLoading(true)

    let q = supabase.from('orders')
      .select(
        'id, platform_order_number, email, total_price, currency, financial_status, utm_source, utm_medium, utm_campaign, platform_source, created_at',
        { count: 'exact' }
      )
      .eq('client_id', clientId)
      .order('created_at', { ascending: false })
      .range(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE - 1)

    if (debSearch)          q = (q as any).ilike('email', `%${debSearch}%`)
    if (statusFilter !== 'all') q = (q as any).eq('financial_status', statusFilter)

    q.then(({ data, count }) => {
      const rows = (data as Order[]) || []
      setOrders(rows)
      setTotal(count || 0)
      setTotalRev(rows.reduce((s, o) => s + (o.total_price || 0), 0))
      setLoading(false)
    })
  }, [clientId, page, debSearch, statusFilter])

  // Separate query for total revenue (all filtered, not just this page)
  useEffect(() => {
    if (!clientId) return
    let q = supabase.from('orders')
      .select('total_price')
      .eq('client_id', clientId)
    if (statusFilter !== 'all') q = (q as any).eq('financial_status', statusFilter)
    if (debSearch)              q = (q as any).ilike('email', `%${debSearch}%`)
    q.then(({ data }) => {
      setTotalRev(((data as any[]) || []).reduce((s, o) => s + (o.total_price || 0), 0))
    })
  }, [clientId, debSearch, statusFilter])

  const totalPages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white">Pedidos</h1>
          <p className="text-xs text-slate-500">
            {total.toLocaleString('pt-BR')} pedido{total !== 1 ? 's' : ''} · {fmt(totalRevenue)} total
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Status tabs */}
          <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
            {(['all', 'paid', 'pending'] as StatusFilter[]).map(s => (
              <button
                key={s}
                onClick={() => setStatus(s)}
                className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                  statusFilter === s
                    ? 'bg-indigo-600 text-white'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {s === 'all' ? 'Todos' : s === 'paid' ? 'Pagos' : 'Pendentes'}
              </button>
            ))}
          </div>
          {/* Search */}
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              placeholder="Buscar por email…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg pl-9 pr-4 py-2 text-sm text-slate-200 placeholder-slate-500 outline-none focus:border-indigo-500 w-56"
            />
          </div>
        </div>
      </div>

      <div className="p-6">
        <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#2a2f3e]">
                {['Pedido', 'Cliente', 'Total', 'Status', 'Origem / Campanha', 'Data'].map(h => (
                  <th key={h} className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={6} className="py-12 text-center text-slate-500">Carregando...</td></tr>
              ) : orders.length === 0 ? (
                <tr><td colSpan={6} className="py-12 text-center text-slate-500">Nenhum pedido encontrado</td></tr>
              ) : orders.map(o => (
                <tr key={o.id} className="border-b border-[#2a2f3e] hover:bg-[#252a3a] transition-colors">
                  <td className="px-4 py-3">
                    <p className="text-slate-200 font-mono text-xs font-medium">
                      #{o.platform_order_number || o.id.slice(0, 8)}
                    </p>
                    <p className="text-xs text-slate-500 mt-0.5">{o.platform_source || 'shopify'}</p>
                  </td>
                  <td className="px-4 py-3">
                    <p className="text-slate-200 truncate max-w-[200px]">{o.email || '—'}</p>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <p className="text-emerald-400 font-semibold">{fmt(o.total_price, o.currency)}</p>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded font-medium ${STATUS_STYLE[o.financial_status || ''] || 'bg-slate-500/10 text-slate-400'}`}>
                      {o.financial_status || 'pendente'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {o.utm_source ? (
                      <div>
                        <span className="text-xs bg-indigo-500/10 text-indigo-400 px-2 py-0.5 rounded">
                          {o.utm_source}
                          {o.utm_medium ? ` / ${o.utm_medium}` : ''}
                        </span>
                        {o.utm_campaign && (
                          <p className="text-xs text-slate-500 mt-1 truncate max-w-[160px]">{o.utm_campaign}</p>
                        )}
                      </div>
                    ) : (
                      <span className="text-xs text-slate-500">direto</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-400 text-xs whitespace-nowrap">
                    {fmtDt(o.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between mt-4 text-sm text-slate-400">
            <span>
              {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} de {total.toLocaleString('pt-BR')}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage(p => p - 1)}
                disabled={page === 0}
                className="p-1.5 rounded hover:bg-[#1a1f2e] disabled:opacity-30 transition-colors"
              >
                <ChevronLeft size={16} />
              </button>
              <button
                onClick={() => setPage(p => p + 1)}
                disabled={page >= totalPages - 1}
                className="p-1.5 rounded hover:bg-[#1a1f2e] disabled:opacity-30 transition-colors"
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
