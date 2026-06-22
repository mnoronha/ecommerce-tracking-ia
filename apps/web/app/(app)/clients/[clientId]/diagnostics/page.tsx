'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { Activity, CheckCircle, AlertCircle, AlertTriangle, Info, RefreshCw, ShoppingBag } from 'lucide-react'

interface CapiChannel {
  configured: boolean
  sent: number
  sent_pct: number | null
  errors: number
  last_error: string | null
}

interface DiagnosticsData {
  pixel_id: string
  client_name: string
  is_active: boolean
  tracking_enabled: boolean
  now: string
  shopify_sync: {
    orders_24h: number
    orders_7d: number
    orders_30d: number
    last_order_at: string | null
  }
  last_event_at: string | null
  events_24h: number
  events_7d: number
  events_30d: number
  identifiers: {
    visitors_30d: number
    fbp_count: number
    fbp_pct: number | null
    fbc_count: number
    fbc_pct: number | null
    gclid_visitors: number
    gclid_pct: number | null
    ttclid_visitors: number
    ttclid_pct: number | null
  }
  orders_30d: number
  orders_visitor_linked: number
  orders_linked_pct: number | null
  capi: {
    meta: CapiChannel
    google: CapiChannel
    tiktok: CapiChannel
  }
  open_alerts: {
    critical: number
    warning: number
    total: number
  }
}

function fmtRelative(iso: string | null) {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 2)  return 'agora mesmo'
  if (m < 60) return `${m}min atrás`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h atrás`
  return `${Math.floor(h / 24)}d atrás`
}

function PctBar({ value, warn = 80, danger = 50 }: { value: number | null; warn?: number; danger?: number }) {
  if (value === null) return <span className="text-slate-600 text-xs">—</span>
  const color = value >= warn ? 'bg-emerald-500' : value >= danger ? 'bg-yellow-500' : 'bg-red-500'
  const textColor = value >= warn ? 'text-emerald-400' : value >= danger ? 'text-yellow-400' : 'text-red-400'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 bg-[#0f1117] rounded-full h-1.5 max-w-[80px]">
        <div className={`h-1.5 rounded-full ${color}`} style={{ width: `${Math.min(value, 100)}%` }} />
      </div>
      <span className={`text-xs font-medium tabular-nums ${textColor}`}>{value.toFixed(1)}%</span>
    </div>
  )
}

function CapiCard({ label, channel }: { label: string; channel: CapiChannel }) {
  if (!channel.configured) {
    return (
      <div className="bg-[#0f1117] border border-[#2a2f3e] rounded-lg p-4 opacity-50">
        <p className="text-xs font-medium text-slate-500 mb-1">{label}</p>
        <p className="text-xs text-slate-600">não configurado</p>
      </div>
    )
  }
  const ok = channel.errors === 0 && (channel.sent_pct ?? 100) >= 90
  return (
    <div className={`bg-[#0f1117] border rounded-lg p-4 ${ok ? 'border-[#2a2f3e]' : 'border-yellow-500/30'}`}>
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs font-medium text-slate-300">{label}</p>
        {ok
          ? <CheckCircle size={13} className="text-emerald-400" />
          : <AlertTriangle size={13} className="text-yellow-400" />}
      </div>
      <div className="space-y-2">
        <div className="flex justify-between text-xs">
          <span className="text-slate-500">Enviados</span>
          <span className="text-white tabular-nums">{channel.sent}</span>
        </div>
        <div className="flex justify-between items-center text-xs">
          <span className="text-slate-500">Cobertura</span>
          <PctBar value={channel.sent_pct} />
        </div>
        {channel.errors > 0 && (
          <div className="flex justify-between text-xs">
            <span className="text-red-400">Erros</span>
            <span className="text-red-400 tabular-nums">{channel.errors}</span>
          </div>
        )}
        {channel.last_error && (
          <p className="text-xs text-slate-600 mt-1 leading-relaxed line-clamp-2 font-mono bg-[#1a1f2e] rounded px-2 py-1">
            {channel.last_error}
          </p>
        )}
      </div>
    </div>
  )
}

export default function DiagnosticsPage() {
  const params   = useParams()
  const clientId = params.clientId as string
  const API_URL  = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

  const [data,    setData]    = useState<DiagnosticsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState('')

  async function load() {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_URL}/diagnostics/${clientId}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setData(await res.json())
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Falha ao carregar')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [clientId])

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <RefreshCw size={18} className="animate-spin text-slate-500" />
    </div>
  )

  if (error || !data) return (
    <div className="p-6 max-w-3xl mx-auto">
      <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-6 text-center">
        <AlertCircle size={24} className="text-red-400 mx-auto mb-2" />
        <p className="text-sm text-red-400">{error || 'Sem dados'}</p>
      </div>
    </div>
  )

  const ids = data.identifiers

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Activity size={18} className="text-indigo-400" />
          <div>
            <h1 className="text-lg font-bold text-white">
              {data.tracking_enabled === false ? 'Diagnóstico — Tracking nativo' : 'Diagnóstico do Pixel'}
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">
              {data.tracking_enabled === false
                ? <>Último pedido: <span className="text-slate-400">{fmtRelative(data.shopify_sync?.last_order_at ?? null)}</span></>
                : <>Último evento: <span className="text-slate-400">{fmtRelative(data.last_event_at)}</span></>
              }
              {' · '}atualizado agora
            </p>
          </div>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-white transition-colors bg-[#1a1f2e] border border-[#2a2f3e] px-3 py-1.5 rounded-lg"
        >
          <RefreshCw size={12} />
          Atualizar
        </button>
      </div>

      {/* Native tracking banner */}
      {data.tracking_enabled === false && (
        <div className="flex items-start gap-3 rounded-xl px-4 py-3 border bg-sky-500/10 border-sky-500/20">
          <ShoppingBag size={15} className="text-sky-400 mt-0.5 shrink-0" />
          <div>
            <p className="text-sm font-semibold text-sky-300">Tracking nativo Shopify ativo</p>
            <p className="text-xs text-slate-400 mt-0.5">
              O pixel Noro está desativado para este cliente. Métricas de eventos e cobertura de identificadores
              não estão disponíveis. O CAPI de conversões continua enviando normalmente via webhooks.
            </p>
          </div>
        </div>
      )}

      {/* Alerts banner */}
      {data.open_alerts.total > 0 && (
        <div className={`flex items-center gap-3 rounded-xl px-4 py-3 border ${
          data.open_alerts.critical > 0
            ? 'bg-red-500/10 border-red-500/20'
            : 'bg-yellow-500/10 border-yellow-500/20'
        }`}>
          <AlertCircle size={15} className={data.open_alerts.critical > 0 ? 'text-red-400' : 'text-yellow-400'} />
          <p className="text-sm text-slate-300">
            <span className={`font-semibold ${data.open_alerts.critical > 0 ? 'text-red-400' : 'text-yellow-400'}`}>
              {data.open_alerts.total} alerta{data.open_alerts.total > 1 ? 's' : ''} aberto{data.open_alerts.total > 1 ? 's' : ''}
            </span>
            {data.open_alerts.critical > 0 && ` · ${data.open_alerts.critical} crítico${data.open_alerts.critical > 1 ? 's' : ''}`}
          </p>
        </div>
      )}

      {/* Shopify sync (shown when tracking disabled; replaces event volume) */}
      {data.tracking_enabled === false ? (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-4">
            Shopify Sync — pedidos recebidos
          </h3>
          <div className="grid grid-cols-3 gap-4 mb-4">
            {[
              { label: 'Últimas 24h',   value: data.shopify_sync?.orders_24h ?? 0 },
              { label: 'Últimos 7 dias', value: data.shopify_sync?.orders_7d  ?? 0 },
              { label: 'Últimos 30 dias', value: data.shopify_sync?.orders_30d ?? 0 },
            ].map(({ label, value }) => (
              <div key={label} className="bg-[#0f1117] rounded-lg p-3 text-center">
                <p className={`text-xl font-bold tabular-nums ${value > 0 ? 'text-emerald-400' : 'text-slate-500'}`}>
                  {value.toLocaleString('pt-BR')}
                </p>
                <p className="text-xs text-slate-500 mt-0.5">{label}</p>
              </div>
            ))}
          </div>
          <p className="text-xs text-slate-600">
            Último pedido: <span className="text-slate-400">{fmtRelative(data.shopify_sync?.last_order_at ?? null)}</span>
            {' · '}sincronizado a cada hora via shopify_sync
          </p>
        </div>
      ) : (
        <>
          {/* Event volume */}
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-4">Volume de eventos</h3>
            <div className="grid grid-cols-3 gap-4">
              {[
                { label: 'Últimas 24h',    value: data.events_24h },
                { label: 'Últimos 7 dias', value: data.events_7d },
                { label: 'Últimos 30 dias', value: data.events_30d },
              ].map(({ label, value }) => (
                <div key={label} className="bg-[#0f1117] rounded-lg p-3 text-center">
                  <p className="text-xl font-bold text-white tabular-nums">{value.toLocaleString('pt-BR')}</p>
                  <p className="text-xs text-slate-500 mt-0.5">{label}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Identifier coverage */}
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-4">Cobertura de identificadores (30d)</h3>
            <div className="space-y-3">
              {[
                { label: 'fbp (Meta Browser ID)', pct: ids.fbp_pct,    count: ids.fbp_count },
                { label: 'fbc (Meta Click ID)',    pct: ids.fbc_pct,    count: ids.fbc_count,       warn: 30, danger: 10 },
                { label: 'gclid (Google Click)',   pct: ids.gclid_pct,  count: ids.gclid_visitors,  warn: 20, danger: 5 },
                { label: 'ttclid (TikTok Click)',  pct: ids.ttclid_pct, count: ids.ttclid_visitors, warn: 20, danger: 5 },
              ].map(({ label, pct, count, warn, danger }) => (
                <div key={label} className="flex items-center justify-between gap-4">
                  <span className="text-xs text-slate-400 min-w-[200px]">{label}</span>
                  <span className="text-xs text-slate-600 tabular-nums w-16 text-right">{count.toLocaleString('pt-BR')}</span>
                  <div className="flex-1 max-w-[160px]">
                    <PctBar value={pct} warn={warn} danger={danger} />
                  </div>
                </div>
              ))}
              <div className="flex items-center justify-between gap-4 pt-1 border-t border-[#2a2f3e]">
                <span className="text-xs text-slate-400 min-w-[200px]">Pedidos vinculados a visitante</span>
                <span className="text-xs text-slate-600 tabular-nums w-16 text-right">{data.orders_visitor_linked}/{data.orders_30d}</span>
                <div className="flex-1 max-w-[160px]">
                  <PctBar value={data.orders_linked_pct} />
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {/* CAPI status */}
      <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-4">
          Status CAPI — últimos 30 dias ({data.orders_30d} pedidos pagos)
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <CapiCard label="Meta CAPI"    channel={data.capi.meta} />
          <CapiCard label="Google Ads"   channel={data.capi.google} />
          <CapiCard label="TikTok CAPI"  channel={data.capi.tiktok} />
        </div>
      </div>

      {/* Footer note */}
      <p className="text-xs text-slate-600 text-center">
        {data.tracking_enabled === false
          ? 'Tracking nativo Shopify ativo · conversões despachadas via CAPI server-side · Shopify sync a cada hora'
          : 'Dados dos últimos 30 dias · fbc/gclid/ttclid esperados apenas para visitantes vindos de anúncios'
        }
      </p>

    </div>
  )
}
