'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams }                         from 'next/navigation'
import Link                                  from 'next/link'
import { ArrowLeft, Users, RefreshCw, Loader2, X, ShoppingBag, MousePointerClick, Info } from 'lucide-react'
import { useDatePeriod, periodToRange, periodLabelLong } from '@/lib/use-date-range'
import { PeriodPicker }                      from '@/components/PeriodPicker'

// ── Types (GET /agency/v1/clients/:id/tracking/visitors | …/journey) ─────────

interface VisitorRow {
  visitor_id:          string
  first_seen_at:       string | null
  last_seen_at:        string | null
  sessions:            number
  first_utm_source:    string | null
  first_utm_medium:    string | null
  first_utm_campaign:  string | null
  landing_url:         string | null
  orders:              number
  pending_orders:      number
  revenue:             number
  has_google_click:    boolean
  has_ga4:             boolean
  consent_advertising: boolean | null
}

interface VisitorsOut {
  total:              number
  tracking_installed: boolean
  items:              VisitorRow[]
}

interface TimelineItem {
  kind:              'session' | 'event' | 'order'
  at:                string | null
  label:             string
  url?:              string | null
  utm_source?:       string | null
  utm_medium?:       string | null
  utm_campaign?:     string | null
  product_name?:     string | null
  source?:           string | null
  order_number?:     string | null
  value?:            number | null
  financial_status?: string | null
}

interface JourneyOut {
  visitor:  VisitorRow
  timeline: TimelineItem[]
}

const PAGE_SIZE = 50

// ── Helpers ───────────────────────────────────────────────────────────────────

const brl = (v: number | null | undefined) =>
  v == null ? '—' : v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })

function ymd(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    timeZone: 'America/Sao_Paulo',
  })
}

function source(v: { first_utm_source: string | null; first_utm_medium: string | null }) {
  if (!v.first_utm_source) return 'Direto / orgânico'
  return v.first_utm_medium ? `${v.first_utm_source} / ${v.first_utm_medium}` : v.first_utm_source
}

function shortPath(url: string | null | undefined) {
  if (!url) return null
  return url.length > 60 ? url.slice(0, 57) + '…' : url
}

const DOT: Record<TimelineItem['kind'], string> = {
  session: 'bg-sky-400',
  event:   'bg-slate-500',
  order:   'bg-emerald-400',
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function VisitorsPage() {
  const params   = useParams()
  const clientId = params.clientId as string
  const { period, from, to, setPreset, setCustom } = useDatePeriod()

  const [data,     setData]     = useState<VisitorsOut | null>(null)
  const [page,     setPage]     = useState(0)
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)
  const [selected, setSelected] = useState<VisitorRow | null>(null)
  const [journey,  setJourney]  = useState<JourneyOut | null>(null)
  const [jLoading, setJLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    const { start, end } = periodToRange(period, from, to)
    const qs = `date_from=${ymd(start)}&date_to=${ymd(end)}&limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`
    try {
      const res = await fetch(`/api/v1/clients/${clientId}/tracking/visitors?${qs}`, { cache: 'no-store' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setData(await res.json())
    } catch {
      setError('Não foi possível carregar os visitantes.')
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [clientId, period, from, to, page])

  useEffect(() => { load() }, [load])
  useEffect(() => { setPage(0) }, [period, from, to])

  const openVisitor = useCallback(async (v: VisitorRow) => {
    setSelected(v)
    setJourney(null)
    setJLoading(true)
    try {
      const res = await fetch(
        `/api/v1/clients/${clientId}/tracking/visitors/${encodeURIComponent(v.visitor_id)}/journey`,
        { cache: 'no-store' },
      )
      if (res.ok) setJourney(await res.json())
    } finally {
      setJLoading(false)
    }
  }, [clientId])

  const rows       = data?.items ?? []
  const total      = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="p-8 max-w-6xl">
      {/* Header */}
      <div className="mb-6 flex items-start justify-between gap-4 flex-wrap">
        <div>
          <Link href={`/clients/${clientId}/dashboard`} className="inline-flex items-center gap-1.5 text-slate-500 hover:text-white text-xs mb-3 transition-colors">
            <ArrowLeft size={12} /> Voltar
          </Link>
          <div className="flex items-center gap-2">
            <Users size={16} className="text-indigo-400" />
            <h1 className="text-xl font-bold text-white">Visitantes</h1>
          </div>
          <p className="text-xs text-slate-500 mt-1">
            Navegação registrada pelo Noro Tracking · {periodLabelLong(period, from, to)}
            {data && data.total > 0 && ` · ${data.total.toLocaleString('pt-BR')} visitantes`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <PeriodPicker period={period} from={from} to={to} onPreset={setPreset} onCustom={setCustom} />
          <button
            onClick={load}
            disabled={loading}
            className="text-slate-500 hover:text-white disabled:opacity-50 transition-colors"
            aria-label="Recarregar"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-slate-500 text-sm py-16 justify-center">
          <Loader2 size={16} className="animate-spin" /> Carregando…
        </div>
      )}

      {!loading && error && (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-8 text-center text-sm text-slate-400">{error}</div>
      )}

      {!loading && !error && data && !data.tracking_installed && (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-8 text-center">
          <p className="text-sm text-slate-300 mb-1">O Noro Tracking ainda não está instalado nesta loja.</p>
          <p className="text-xs text-slate-500 max-w-md mx-auto">
            Os pedidos continuam disponíveis em Pedidos e em Qualidade do Tracking (vindos do Shopify).
            A navegação dos visitantes aparece aqui depois da instalação do tracker no tema.
          </p>
          <Link href={`/clients/${clientId}/tracking`} className="inline-block mt-4 text-xs text-indigo-400 hover:text-indigo-300">
            Ver Qualidade do Tracking →
          </Link>
        </div>
      )}

      {!loading && !error && data && data.tracking_installed && (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-slate-500 text-left">
                <tr className="border-b border-[#2a2f3e]">
                  <th className="px-5 py-2 font-medium">Visitante</th>
                  <th className="px-3 py-2 font-medium">Primeira origem</th>
                  <th className="px-3 py-2 font-medium text-right">Sessões</th>
                  <th className="px-3 py-2 font-medium text-right">Pedidos pagos</th>
                  <th className="px-3 py-2 font-medium text-right">Receita</th>
                  <th className="px-3 py-2 font-medium">Sinais</th>
                  <th className="px-5 py-2 font-medium">Última visita</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(v => (
                  <tr
                    key={v.visitor_id}
                    onClick={() => openVisitor(v)}
                    className="border-b border-[#2a2f3e]/60 hover:bg-[#252b3b] cursor-pointer"
                  >
                    <td className="px-5 py-2.5 text-slate-300 font-mono">{v.visitor_id.slice(0, 8)}</td>
                    <td className="px-3 py-2.5 text-slate-400">
                      {source(v)}
                      {v.first_utm_campaign && <div className="text-[10px] text-slate-500 truncate max-w-[200px]">{v.first_utm_campaign}</div>}
                    </td>
                    <td className="px-3 py-2.5 text-right text-slate-300 tabular-nums">{v.sessions}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      <span className={v.orders > 0 ? 'text-emerald-400' : 'text-slate-500'}>{v.orders}</span>
                      {v.pending_orders > 0 && <span className="text-[10px] text-yellow-400 ml-1">+{v.pending_orders} pend.</span>}
                    </td>
                    <td className="px-3 py-2.5 text-right text-slate-200 tabular-nums">{v.revenue > 0 ? brl(v.revenue) : '—'}</td>
                    <td className="px-3 py-2.5">
                      <div className="flex gap-1">
                        {v.has_google_click && <span className="text-[10px] px-1.5 py-0.5 rounded bg-sky-500/10 text-sky-300">clique Google</span>}
                        {v.consent_advertising === false && <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-500/10 text-slate-400">sem consent.</span>}
                      </div>
                    </td>
                    <td className="px-5 py-2.5 text-slate-400 whitespace-nowrap">{fmtDate(v.last_seen_at)}</td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr><td colSpan={7} className="px-5 py-6 text-center text-slate-500">Nenhum visitante no período.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          {total > PAGE_SIZE && (
            <div className="flex items-center justify-between px-5 py-3 border-t border-[#2a2f3e] text-xs text-slate-500">
              <span>Página {page + 1} de {totalPages}</span>
              <div className="flex gap-2">
                <button disabled={page === 0} onClick={() => setPage(p => p - 1)} className="px-2 py-1 rounded hover:text-white disabled:opacity-40">Anterior</button>
                <button disabled={page + 1 >= totalPages} onClick={() => setPage(p => p + 1)} className="px-2 py-1 rounded hover:text-white disabled:opacity-40">Próxima</button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Journey panel */}
      {selected && (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/40" onClick={() => setSelected(null)}>
          <div
            className="w-full max-w-md h-full overflow-y-auto bg-[#141824] border-l border-[#2a2f3e] p-6"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-start justify-between mb-4">
              <div>
                <p className="text-[11px] uppercase tracking-wide text-slate-500">Jornada do visitante</p>
                <p className="text-sm font-mono text-white">{selected.visitor_id.slice(0, 8)}</p>
                <p className="text-xs text-slate-500 mt-1">
                  {selected.sessions} sessões · {selected.orders} pedidos pagos · {brl(selected.revenue)}
                </p>
              </div>
              <button onClick={() => setSelected(null)} className="text-slate-500 hover:text-white" aria-label="Fechar">
                <X size={16} />
              </button>
            </div>

            {jLoading && (
              <div className="flex items-center gap-2 text-slate-500 text-sm py-10 justify-center">
                <Loader2 size={14} className="animate-spin" /> Carregando…
              </div>
            )}

            {!jLoading && journey && (
              <ol className="relative border-l border-[#2a2f3e] ml-1.5 space-y-4">
                {journey.timeline.map((it, i) => (
                  <li key={i} className="ml-4">
                    <span className={`absolute -left-[5px] mt-1.5 w-2.5 h-2.5 rounded-full ${DOT[it.kind]}`} />
                    <div className="flex items-center gap-1.5 text-xs">
                      {it.kind === 'order' && <ShoppingBag size={12} className="text-emerald-400" />}
                      {it.kind === 'session' && <MousePointerClick size={12} className="text-sky-400" />}
                      <span className={it.kind === 'order' ? 'text-emerald-300 font-medium' : 'text-slate-200'}>{it.label}</span>
                      <span className="text-slate-500 ml-auto whitespace-nowrap">{fmtDate(it.at)}</span>
                    </div>
                    {it.kind === 'session' && (
                      <p className="text-[11px] text-slate-500 mt-0.5">
                        {it.utm_source ? `${it.utm_source}${it.utm_medium ? ` / ${it.utm_medium}` : ''}` : 'Direto / orgânico'}
                        {it.utm_campaign ? ` · ${it.utm_campaign}` : ''}
                      </p>
                    )}
                    {it.kind === 'event' && (it.product_name || it.url) && (
                      <p className="text-[11px] text-slate-500 mt-0.5 truncate">{it.product_name || shortPath(it.url)}</p>
                    )}
                    {it.kind === 'order' && (
                      <p className="text-[11px] text-slate-400 mt-0.5">{it.order_number} · {brl(it.value)}</p>
                    )}
                  </li>
                ))}
                {journey.timeline.length === 0 && (
                  <li className="ml-4 text-xs text-slate-500">Sem eventos registrados.</li>
                )}
              </ol>
            )}

            <p className="flex items-start gap-1.5 text-[11px] text-slate-500 mt-6">
              <Info size={12} className="mt-0.5 shrink-0" />
              Visitante identificado por cookie próprio do Noro. Nenhum dado pessoal é exibido.
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
