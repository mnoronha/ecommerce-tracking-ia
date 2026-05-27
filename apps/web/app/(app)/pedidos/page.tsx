'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import { Search, ChevronLeft, ChevronRight, CheckCircle, XCircle, MinusCircle } from 'lucide-react'

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
  capi_sent: boolean | null
  capi_last_error: string | null
  google_sent: boolean | null
  google_last_error: string | null
  tiktok_sent: boolean | null
  tiktok_last_error: string | null
  created_at: string
}

type StatusFilter = 'all' | 'paid' | 'pending'
type DatePreset   = 'today' | '1d' | '7d' | '30d' | 'all'

// ── Constants ─────────────────────────────────────────────────────────────────

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

function dateRange(preset: DatePreset): { gte?: string; lte?: string } {
  const now = new Date()
  if (preset === 'today') {
    const s = new Date(now); s.setHours(0, 0, 0, 0)
    return { gte: s.toISOString() }
  }
  if (preset === '1d') {
    const s = new Date(now); s.setDate(s.getDate() - 1); s.setHours(0, 0, 0, 0)
    const e = new Date(s); e.setHours(23, 59, 59, 999)
    return { gte: s.toISOString(), lte: e.toISOString() }
  }
  if (preset === '7d') {
    const s = new Date(now); s.setDate(s.getDate() - 7)
    return { gte: s.toISOString() }
  }
  if (preset === '30d') {
    const s = new Date(now); s.setDate(s.getDate() - 30)
    return { gte: s.toISOString() }
  }
  return {}
}

// ── CAPI dot component ────────────────────────────────────────────────────────

function CapiDot({ sent, error, label }: { sent: boolean | null; error: string | null; label: string }) {
  if (sent === null) return (
    <span title={`${label}: não configurado`}>
      <MinusCircle size={13} className="text-slate-600" />
    </span>
  )
  return sent ? (
    <span title={`${label}: enviado`}>
      <CheckCircle size={13} className="text-emerald-400" />
    </span>
  ) : (
    <span title={`${label}: ${error || 'erro'}`}>
      <XCircle size={13} className="text-red-400" />
    </span>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function PedidosPage() {
  const params = useParams()
  const CLIENT_PIXEL_ID = (params?.clientId as string) || process.env.NEXT_PUBLIC_CLIENT_PIXEL_ID || 'lk-sneakers'

  const [orders, setOrders]           = useState<Order[]>([])
  const [total, setTotal]             = useState(0)
  const [totalRevenue, setTotalRev]   = useState(0)
  const [page, setPage]               = useState(0)
  const [search, setSearch]           = useState('')
  const [debSearch, setDebSearch]     = useState('')
  const [statusFilter, setStatus]     = useState<StatusFilter>('paid')
  const [datePreset, setDatePreset]   = useState<DatePreset>('30d')
  const [loading, setLoading]         = useState(true)
  const [clientId, setClientId]       = useState<string | null>(null)
  const [expandedId, setExpandedId]   = useState<string | null>(null)

  // Resolve client UUID once
  useEffect(() => {
    const isUUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(CLIENT_PIXEL_ID)
    supabase.from('clients').select('id')
      .eq(isUUID ? 'id' : 'pixel_id', CLIENT_PIXEL_ID).limit(1).single()
      .then(({ data }) => setClientId(data?.id ?? null))
  }, [CLIENT_PIXEL_ID])

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => { setDebSearch(search); setPage(0) }, 400)
    return () => clearTimeout(t)
  }, [search])

  // Reset page on filter change
  useEffect(() => { setPage(0) }, [statusFilter, datePreset])

  // Load orders
  useEffect(() => {
    if (!clientId) return
    setLoading(true)

    const { gte, lte } = dateRange(datePreset)

    let q = supabase.from('orders')
      .select(
        'id, platform_order_number, email, total_price, currency, financial_status, utm_source, utm_medium, utm_campaign, platform_source, capi_sent, capi_last_error, google_sent, google_last_error, tiktok_sent, tiktok_last_error, created_at',
        { count: 'exact' }
      )
      .eq('client_id', clientId)
      .gt('total_price', 0)
      .order('created_at', { ascending: false })
      .range(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE - 1)

    if (gte)                    q = (q as any).gte('created_at', gte)
    if (lte)                    q = (q as any).lte('created_at', lte)
    if (debSearch)              q = (q as any).ilike('email', `%${debSearch}%`)
    if (statusFilter !== 'all') q = (q as any).eq('financial_status', statusFilter)

    q.then(({ data, count }) => {
      setOrders((data as Order[]) || [])
      setTotal(count || 0)
      setLoading(false)
    })
  }, [clientId, page, debSearch, statusFilter, datePreset])

  // Total revenue for current filter (all pages)
  useEffect(() => {
    if (!clientId) return
    const { gte, lte } = dateRange(datePreset)
    let q = supabase.from('orders').select('total_price')
      .eq('client_id', clientId).gt('total_price', 0)
    if (gte)                    q = (q as any).gte('created_at', gte)
    if (lte)                    q = (q as any).lte('created_at', lte)
    if (statusFilter !== 'all') q = (q as any).eq('financial_status', statusFilter)
    if (debSearch)              q = (q as any).ilike('email', `%${debSearch}%`)
    q.then(({ data }) => {
      setTotalRev(((data as any[]) || []).reduce((s, o) => s + (o.total_price || 0), 0))
    })
  }, [clientId, debSearch, statusFilter, datePreset])

  const totalPages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-lg font-bold text-white">Pedidos</h1>
          <p className="text-xs text-slate-500">
            {total.toLocaleString('pt-BR')} pedido{total !== 1 ? 's' : ''} · {fmt(totalRevenue)} receita
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {/* Date presets */}
          <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
            {([['today','Hoje'],['1d','Ontem'],['7d','7d'],['30d','30d'],['all','Todos']] as [DatePreset,string][]).map(([v,l]) => (
              <button key={v} onClick={() => setDatePreset(v)}
                className={`px-3 py-1 rounded text-xs font-medium transition-colors ${datePreset === v ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'}`}>
                {l}
              </button>
            ))}
          </div>
          {/* Status */}
          <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
            {(['all','paid','pending'] as StatusFilter[]).map(s => (
              <button key={s} onClick={() => setStatus(s)}
                className={`px-3 py-1 rounded text-xs font-medium transition-colors ${statusFilter === s ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'}`}>
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
              className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg pl-9 pr-4 py-2 text-sm text-slate-200 placeholder-slate-500 outline-none focus:border-indigo-500 w-52"
            />
          </div>
        </div>
      </div>

      <div className="p-6">
        <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#2a2f3e]">
                {['Pedido', 'Cliente', 'Total', 'Status', 'Origem', 'CAPI', 'Data'].map(h => (
                  <th key={h} className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={7} className="py-12 text-center text-slate-500">Carregando...</td></tr>
              ) : orders.length === 0 ? (
                <tr><td colSpan={7} className="py-12 text-center text-slate-500">Nenhum pedido encontrado</td></tr>
              ) : orders.map(o => (
                <>
                  <tr
                    key={o.id}
                    onClick={() => setExpandedId(expandedId === o.id ? null : o.id)}
                    className="border-b border-[#2a2f3e] hover:bg-[#252a3a] transition-colors cursor-pointer"
                  >
                    <td className="px-4 py-3">
                      <p className="text-slate-200 font-mono text-xs font-medium">
                        #{o.platform_order_number || o.id.slice(0, 8)}
                      </p>
                      <p className="text-xs text-slate-500 mt-0.5">{o.platform_source || 'shopify'}</p>
                    </td>
                    <td className="px-4 py-3">
                      <p className="text-slate-200 truncate max-w-[180px] text-xs">{o.email || '—'}</p>
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
                            {o.utm_source}{o.utm_medium ? ` / ${o.utm_medium}` : ''}
                          </span>
                          {o.utm_campaign && (
                            <p className="text-xs text-slate-500 mt-0.5 truncate max-w-[150px]">{o.utm_campaign}</p>
                          )}
                        </div>
                      ) : (
                        <span className="text-xs text-slate-500">direto</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1.5" title="Meta · Google · TikTok">
                        <CapiDot sent={o.capi_sent}    error={o.capi_last_error}   label="Meta" />
                        <CapiDot sent={o.google_sent}  error={o.google_last_error} label="Google" />
                        <CapiDot sent={o.tiktok_sent}  error={o.tiktok_last_error} label="TikTok" />
                      </div>
                    </td>
                    <td className="px-4 py-3 text-slate-400 text-xs whitespace-nowrap">
                      {fmtDt(o.created_at)}
                    </td>
                  </tr>
                  {expandedId === o.id && (
                    <tr key={`${o.id}-detail`} className="bg-[#0f1117]">
                      <td colSpan={7} className="px-6 py-4">
                        <div className="grid grid-cols-3 gap-4 text-xs">
                          <div>
                            <p className="text-slate-500 mb-1 font-medium uppercase tracking-wider">Meta CAPI</p>
                            <p className={o.capi_sent ? 'text-emerald-400' : o.capi_sent === false ? 'text-red-400' : 'text-slate-600'}>
                              {o.capi_sent ? 'Enviado' : o.capi_sent === false ? 'Falhou' : 'Não configurado'}
                            </p>
                            {o.capi_last_error && (
                              <p className="text-red-400 font-mono mt-1 bg-red-500/5 border border-red-500/10 rounded px-2 py-1 break-all">{o.capi_last_error}</p>
                            )}
                          </div>
                          <div>
                            <p className="text-slate-500 mb-1 font-medium uppercase tracking-wider">Google Ads</p>
                            <p className={o.google_sent ? 'text-emerald-400' : o.google_sent === false ? 'text-red-400' : 'text-slate-600'}>
                              {o.google_sent ? 'Enviado' : o.google_sent === false ? 'Falhou' : 'Não configurado'}
                            </p>
                            {o.google_last_error && (
                              <p className="text-red-400 font-mono mt-1 bg-red-500/5 border border-red-500/10 rounded px-2 py-1 break-all">{o.google_last_error}</p>
                            )}
                          </div>
                          <div>
                            <p className="text-slate-500 mb-1 font-medium uppercase tracking-wider">TikTok CAPI</p>
                            <p className={o.tiktok_sent ? 'text-emerald-400' : o.tiktok_sent === false ? 'text-red-400' : 'text-slate-600'}>
                              {o.tiktok_sent ? 'Enviado' : o.tiktok_sent === false ? 'Falhou' : 'Não configurado'}
                            </p>
                            {o.tiktok_last_error && (
                              <p className="text-red-400 font-mono mt-1 bg-red-500/5 border border-red-500/10 rounded px-2 py-1 break-all">{o.tiktok_last_error}</p>
                            )}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
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
              <button onClick={() => setPage(p => p - 1)} disabled={page === 0}
                className="p-1.5 rounded hover:bg-[#1a1f2e] disabled:opacity-30 transition-colors">
                <ChevronLeft size={16} />
              </button>
              <button onClick={() => setPage(p => p + 1)} disabled={page >= totalPages - 1}
                className="p-1.5 rounded hover:bg-[#1a1f2e] disabled:opacity-30 transition-colors">
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
