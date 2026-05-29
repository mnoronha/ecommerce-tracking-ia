'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import { Search, ChevronLeft, ChevronRight, X } from 'lucide-react'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Visitor {
  id: string
  visitor_id: string
  email: string | null
  phone: string | null
  first_utm_source: string | null
  first_utm_medium: string | null
  first_utm_campaign: string | null
  first_platform: string | null
  total_pageviews: number
  total_orders: number
  total_revenue: number | null
  retargeting_score: number | null
  first_seen_at: string | null
  last_seen_at: string | null
}

interface TrackingEvent {
  id: string
  event_type: string
  url: string | null
  created_at: string
  utm_source: string | null
  product_name: string | null
}

// ── Constants ─────────────────────────────────────────────────────────────────

// CLIENT_PIXEL_ID resolved dynamically via useParams inside component
const PAGE_SIZE = 25

const EVENT_META: Record<string, { label: string; color: string }> = {
  pageview:       { label: 'Pageview',   color: 'text-slate-400' },
  view_product:   { label: 'Produto',    color: 'text-blue-400' },
  add_to_cart:    { label: 'Carrinho',   color: 'text-yellow-400' },
  begin_checkout: { label: 'Checkout',   color: 'text-orange-400' },
  purchase:       { label: 'Compra',     color: 'text-emerald-400' },
  search:         { label: 'Busca',      color: 'text-purple-400' },
  custom:         { label: 'Custom',     color: 'text-slate-500' },
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmtDt = (iso: string | null) => iso
  ? new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })
  : '—'

const fmtBRL = (n: number | null) => n
  ? new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n)
  : '—'

// ── Visitor detail panel ──────────────────────────────────────────────────────

function VisitorPanel({
  visitor,
  events,
  loading,
  onClose,
}: {
  visitor: Visitor
  events: TrackingEvent[]
  loading: boolean
  onClose: () => void
}) {
  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-end z-50"
      onClick={onClose}
    >
      <div
        className="w-96 h-full bg-[#1a1f2e] border-l border-[#2a2f3e] flex flex-col overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* Panel header */}
        <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-start justify-between shrink-0">
          <div className="min-w-0 mr-3">
            <p className="font-medium text-white truncate">
              {visitor.email || 'Visitante anônimo'}
            </p>
            <p className="text-xs text-slate-500 font-mono mt-0.5">
              {visitor.visitor_id.slice(0, 28)}…
            </p>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white shrink-0">
            <X size={18} />
          </button>
        </div>

        {/* Stats */}
        <div className="p-5 border-b border-[#2a2f3e] shrink-0">
          <div className="grid grid-cols-2 gap-3">
            {[
              { label: 'Pageviews',  value: visitor.total_pageviews || 0 },
              { label: 'Pedidos',    value: visitor.total_orders || 0 },
              { label: 'Origem',     value: visitor.first_utm_source || 'direto' },
              { label: 'Plataforma', value: visitor.first_platform || 'web' },
            ].map(s => (
              <div key={s.label} className="bg-[#0f1117] rounded-lg p-3">
                <p className="text-xs text-slate-500">{s.label}</p>
                <p className="text-sm font-medium text-white mt-0.5 truncate">{s.value}</p>
              </div>
            ))}
          </div>
          {visitor.first_utm_campaign && (
            <div className="mt-3 bg-[#0f1117] rounded-lg p-3">
              <p className="text-xs text-slate-500">Campanha</p>
              <p className="text-sm text-white mt-0.5 truncate">{visitor.first_utm_campaign}</p>
            </div>
          )}
          {visitor.total_revenue != null && visitor.total_revenue > 0 && (
            <div className="mt-3 bg-[#0f1117] rounded-lg p-3 border border-emerald-500/20">
              <p className="text-xs text-slate-500">LTV — Receita Total</p>
              <p className="text-base font-bold text-emerald-400 mt-0.5">{fmtBRL(visitor.total_revenue)}</p>
            </div>
          )}
        </div>

        {/* Event timeline */}
        <div className="flex-1 overflow-auto p-5">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">
            Histórico de eventos
          </p>
          {loading ? (
            <p className="text-slate-500 text-sm">Carregando...</p>
          ) : events.length === 0 ? (
            <p className="text-slate-500 text-sm">Nenhum evento registrado</p>
          ) : (
            <div className="space-y-2.5">
              {events.map(e => {
                const m = EVENT_META[e.event_type] || { label: e.event_type, color: 'text-slate-400' }
                return (
                  <div key={e.id} className="flex gap-3 text-xs">
                    <span className={`shrink-0 font-medium w-[72px] ${m.color}`}>{m.label}</span>
                    <div className="min-w-0 flex-1">
                      {e.product_name && (
                        <p className="text-slate-300 truncate">{e.product_name}</p>
                      )}
                      {e.url && (
                        <p className="text-slate-500 truncate">
                          {e.url.replace(/https?:\/\/[^/]+/, '') || '/'}
                        </p>
                      )}
                      <p className="text-slate-600">{fmtDt(e.created_at)}</p>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function VisitantesPage() {
  const params = useParams()
  const CLIENT_PIXEL_ID = (params?.clientId as string) || process.env.NEXT_PUBLIC_CLIENT_PIXEL_ID || 'lk-sneakers'

  const [visitors, setVisitors]   = useState<Visitor[]>([])
  const [total, setTotal]         = useState(0)
  const [page, setPage]           = useState(0)
  const [search, setSearch]       = useState('')
  const [debSearch, setDebSearch] = useState('')
  const [loading, setLoading]     = useState(true)
  const [clientId, setClientId]   = useState<string | null>(null)

  const [selected, setSelected]         = useState<Visitor | null>(null)
  const [panelEvents, setPanelEvents]   = useState<TrackingEvent[]>([])
  const [panelLoading, setPanelLoading] = useState(false)

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

  // Load visitors
  useEffect(() => {
    if (!clientId) return
    setLoading(true)

    let q = supabase.from('visitors')
      .select(
        'id, visitor_id, email, phone, first_utm_source, first_utm_medium, first_utm_campaign, first_platform, total_pageviews, total_orders, total_revenue, retargeting_score, first_seen_at, last_seen_at',
        { count: 'exact' }
      )
      .eq('client_id', clientId)
      .order('last_seen_at', { ascending: false, nullsFirst: false })
      .range(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE - 1)

    if (debSearch) q = (q as any).ilike('email', `%${debSearch}%`)

    q.then(({ data, count }) => {
      setVisitors((data as Visitor[]) || [])
      setTotal(count || 0)
      setLoading(false)
    })
  }, [clientId, page, debSearch])

  // Open visitor detail
  const openVisitor = useCallback(async (v: Visitor) => {
    setSelected(v)
    setPanelLoading(true)
    const { data } = await supabase
      .from('tracking_events')
      .select('id, event_type, url, created_at, utm_source, product_name')
      .eq('visitor_id', v.visitor_id)
      .order('created_at', { ascending: false })
      .limit(60)
    setPanelEvents((data as TrackingEvent[]) || [])
    setPanelLoading(false)
  }, [])

  const totalPages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white">Visitantes</h1>
          <p className="text-xs text-slate-500">
            {total.toLocaleString('pt-BR')} visitantes registrados
          </p>
        </div>
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            placeholder="Buscar por email…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg pl-9 pr-4 py-2 text-sm text-slate-200 placeholder-slate-500 outline-none focus:border-indigo-500 w-64"
          />
        </div>
      </div>

      <div className="p-6">
        <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#2a2f3e]">
                {['Visitante', 'Origem', 'Pageviews', 'Pedidos', 'LTV', 'Score', 'Última visita'].map(h => (
                  <th key={h} className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={7} className="py-12 text-center text-slate-500">Carregando...</td></tr>
              ) : visitors.length === 0 ? (
                <tr><td colSpan={7} className="py-12 text-center text-slate-500">Nenhum visitante encontrado</td></tr>
              ) : visitors.map(v => (
                <tr
                  key={v.id}
                  onClick={() => openVisitor(v)}
                  className="border-b border-[#2a2f3e] hover:bg-[#252a3a] cursor-pointer transition-colors"
                >
                  <td className="px-4 py-3">
                    {v.email ? (
                      <p className="text-slate-200 font-medium">{v.email}</p>
                    ) : (
                      <p className="text-slate-500 font-mono text-xs">{v.visitor_id.slice(0, 18)}…</p>
                    )}
                    <p className="text-xs text-slate-500 mt-0.5">{v.first_platform || 'web'}</p>
                  </td>
                  <td className="px-4 py-3">
                    {v.first_utm_source ? (
                      <span className="text-xs bg-indigo-500/10 text-indigo-400 px-2 py-0.5 rounded">
                        {v.first_utm_source}
                      </span>
                    ) : (
                      <span className="text-xs text-slate-500">direto</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-300 text-center">
                    {(v.total_pageviews || 0).toLocaleString('pt-BR')}
                  </td>
                  <td className="px-4 py-3 text-center">
                    {(v.total_orders || 0) > 0 ? (
                      <span className="text-emerald-400 font-semibold">{v.total_orders}</span>
                    ) : (
                      <span className="text-slate-500">0</span>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    {v.total_revenue && v.total_revenue > 0 ? (
                      <span className="text-emerald-400 font-semibold text-sm">{fmtBRL(v.total_revenue)}</span>
                    ) : (
                      <span className="text-slate-600 text-xs">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-center">
                    {(v.retargeting_score || 0) > 0 ? (
                      <span className={`text-xs font-bold px-2 py-0.5 rounded ${
                        (v.retargeting_score || 0) >= 35 ? 'bg-red-500/15 text-red-400' :
                        (v.retargeting_score || 0) >= 20 ? 'bg-orange-500/15 text-orange-400' :
                        'bg-yellow-500/15 text-yellow-400'
                      }`}>
                        {v.retargeting_score}
                      </span>
                    ) : (
                      <span className="text-slate-600 text-xs">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-400 text-xs whitespace-nowrap">
                    {fmtDt(v.last_seen_at || v.first_seen_at)}
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

      {selected && (
        <VisitorPanel
          visitor={selected}
          events={panelEvents}
          loading={panelLoading}
          onClose={() => { setSelected(null); setPanelEvents([]) }}
        />
      )}
    </div>
  )
}
