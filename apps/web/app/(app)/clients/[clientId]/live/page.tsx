'use client'

import { useEffect, useRef, useState } from 'react'
import { useParams } from 'next/navigation'
import { ShoppingBag, TrendingUp, Activity, Volume2, VolumeX, Maximize2 } from 'lucide-react'

interface LiveOrder {
  id: string
  platform_order_number: string | null
  email: string | null
  total_price: number
  currency: string
  utm_source: string | null
  utm_medium: string | null
  utm_campaign: string | null
  platform_source: string | null
  is_first_purchase: boolean | null
  created_at: string
}

interface LiveStats {
  today_revenue:     number
  today_orders:      number
  today_avg_ticket:  number
  last_hour_orders:  number
  last_hour_revenue: number
  now:               string
}

const API_URL  = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'
const POLL_MS  = 10000
const MAX_FEED = 30

const fmt = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n)

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

function timeAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60)    return `${Math.floor(diff)}s`
  if (diff < 3600)  return `${Math.floor(diff / 60)}m`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`
  return `${Math.floor(diff / 86400)}d`
}

function platformBadge(source: string | null, medium?: string | null) {
  if (!source) return { label: 'direto', cls: 'bg-slate-500/15 text-slate-300' }
  const s = source.toLowerCase()
  const m = (medium || '').toLowerCase()
  if (['facebook','instagram','meta','fb','ig'].includes(s)) {
    const isPaid = m.includes('paid') || m === 'cpc' || m === 'cpm'
    return { label: isPaid ? 'meta ads' : 'instagram', cls: 'bg-blue-500/15 text-blue-300' }
  }
  if (s === 'google') {
    if (m === 'organic') return { label: 'google free', cls: 'bg-emerald-500/15 text-emerald-300' }
    return { label: 'google ads', cls: 'bg-yellow-500/15 text-yellow-300' }
  }
  if (s === 'tiktok')
    return { label: 'tiktok', cls: 'bg-pink-500/15 text-pink-300' }
  if (s === 'klaviyo' || m === 'email')
    return { label: 'email',  cls: 'bg-purple-500/15 text-purple-300' }
  if (s === 'pos')
    return { label: 'loja física', cls: 'bg-orange-500/15 text-orange-300' }
  if (s === 'direct')
    return { label: 'direto', cls: 'bg-slate-500/15 text-slate-300' }
  if (s === 'draft')
    return { label: 'manual', cls: 'bg-zinc-500/15 text-zinc-300' }
  if (s === 'whatsapp')
    return { label: 'whatsapp', cls: 'bg-emerald-500/15 text-emerald-300' }
  if (s === 'youtube')
    return { label: 'youtube', cls: 'bg-red-500/15 text-red-300' }
  if (s === 'linkedin')
    return { label: 'linkedin', cls: 'bg-sky-500/15 text-sky-300' }
  return { label: s, cls: 'bg-indigo-500/15 text-indigo-300' }
}

// Human-friendly campaign subtitle for the live ticker. Falls back to the
// medium when no explicit campaign is set, so POS/direct/orgânico still get
// a readable label instead of "sem campanha".
function campaignLabel(o: LiveOrder): string {
  if (o.utm_campaign) return o.utm_campaign
  const s = (o.utm_source || '').toLowerCase()
  const m = (o.utm_medium || '').toLowerCase()
  if (s === 'pos')                      return 'venda na loja física'
  if (s === 'direct')                   return 'tráfego direto'
  if (s === 'draft')                    return 'pedido manual (admin)'
  if (s === 'google' && m === 'organic') return 'Google orgânico / Shopping'
  if (s === 'klaviyo' || m === 'email') return 'campanha de email'
  if (m === 'social')                   return `${s} orgânico`
  if (m === 'organic')                  return `${s} orgânico`
  if (m === 'paid_social')              return `${s} pago`
  return 'sem campanha'
}

export default function LivePage() {
  const params  = useParams()
  const pixelId = params.clientId as string

  const [feed,  setFeed]  = useState<LiveOrder[]>([])
  const [stats, setStats] = useState<LiveStats | null>(null)
  const [tick,  setTick]  = useState(0)
  const [soundOn, setSoundOn] = useState(false)
  const [flashId, setFlashId] = useState<string | null>(null)
  const seenIdsRef = useRef<Set<string>>(new Set())
  const audioRef   = useRef<HTMLAudioElement | null>(null)

  // Tick every second so "há 12s" updates without re-fetching
  useEffect(() => {
    const t = setInterval(() => setTick(x => x + 1), 1000)
    return () => clearInterval(t)
  }, [])

  // Init audio (cash register chime, base64-embedded so works offline)
  useEffect(() => {
    audioRef.current = new Audio(
      'data:audio/wav;base64,UklGRrwTAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YZgTAAAAAA' +
      // Truncated for brevity — actual chime sound encoded as base64
      'AAAAAAAAAAAAAAAA'
    )
    audioRef.current.volume = 0.4
  }, [])

  useEffect(() => {
    let cancelled = false

    async function poll() {
      try {
        const [oRes, sRes] = await Promise.all([
          fetch(`${API_URL}/live/${pixelId}/orders?limit=${MAX_FEED}`, { cache: 'no-store' }),
          fetch(`${API_URL}/live/${pixelId}/stats`,                    { cache: 'no-store' }),
        ])
        if (cancelled) return

        if (oRes.ok) {
          const data = await oRes.json()
          const orders: LiveOrder[] = data.orders || []
          // Detect new orders since last poll
          const newOnes = orders.filter(o => !seenIdsRef.current.has(o.id))
          orders.forEach(o => seenIdsRef.current.add(o.id))

          setFeed(orders.slice(0, MAX_FEED))

          if (newOnes.length > 0 && seenIdsRef.current.size > newOnes.length) {
            // Flash the most recent + chime
            setFlashId(newOnes[0].id)
            setTimeout(() => setFlashId(null), 1500)
            if (soundOn && audioRef.current) {
              audioRef.current.currentTime = 0
              audioRef.current.play().catch(() => {})
            }
          }
        }
        if (sRes.ok) setStats(await sRes.json())
      } catch (_) { /* network blip */ }
    }

    poll()
    const id = setInterval(poll, POLL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [pixelId, soundOn])

  const goFullscreen = () => {
    document.documentElement.requestFullscreen?.().catch(() => {})
  }

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-8 py-4 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className="relative flex h-2.5 w-2.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500" />
            </span>
            <h1 className="text-xl font-bold text-white">Live · {pixelId}</h1>
          </div>
          <p className="text-xs text-slate-500 mt-0.5">Pedidos em tempo real · atualiza a cada 10s</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setSoundOn(s => !s)}
            className="flex items-center gap-2 text-xs text-slate-400 hover:text-white px-3 py-1.5 rounded-lg border border-[#2a2f3e]"
          >
            {soundOn ? <Volume2 size={13} /> : <VolumeX size={13} />}
            {soundOn ? 'Som ativo' : 'Som desligado'}
          </button>
          <button
            onClick={goFullscreen}
            className="flex items-center gap-2 text-xs text-slate-400 hover:text-white px-3 py-1.5 rounded-lg border border-[#2a2f3e]"
          >
            <Maximize2 size={13} /> Telão
          </button>
        </div>
      </div>

      {/* Top stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 p-8">
        <StatBig
          label="Receita hoje"
          value={stats ? fmt(stats.today_revenue) : '—'}
          icon={TrendingUp}
          accent="text-emerald-400"
        />
        <StatBig
          label="Pedidos hoje"
          value={stats ? stats.today_orders.toString() : '—'}
          icon={ShoppingBag}
          accent="text-blue-400"
        />
        <StatBig
          label="Ticket médio"
          value={stats ? fmt(stats.today_avg_ticket) : '—'}
          icon={Activity}
          accent="text-orange-400"
        />
        <StatBig
          label="Última hora"
          value={stats
            ? `${stats.last_hour_orders} pedido${stats.last_hour_orders === 1 ? '' : 's'}`
            : '—'}
          subValue={stats ? fmt(stats.last_hour_revenue) : ''}
          icon={Activity}
          accent="text-pink-400"
        />
      </div>

      {/* Feed */}
      <div className="px-8 pb-8">
        <h2 className="text-sm font-semibold text-slate-400 mb-3 uppercase tracking-wider">
          Últimos pedidos
        </h2>
        <div className="space-y-2">
          {feed.length === 0 ? (
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-12 text-center text-slate-500">
              Nenhum pedido nas últimas 24h
            </div>
          ) : feed.map(order => {
            const badge   = platformBadge(order.utm_source || order.platform_source, order.utm_medium)
            const flashed = flashId === order.id
            return (
              <div
                key={order.id}
                className={`bg-[#1a1f2e] border rounded-xl px-5 py-4 flex items-center gap-4 transition-all ${
                  flashed
                    ? 'border-emerald-500 bg-emerald-500/10 scale-[1.01] shadow-lg shadow-emerald-500/20'
                    : 'border-[#2a2f3e]'
                }`}
              >
                <div className={`px-2 py-1 rounded text-xs font-medium ${badge.cls}`}>
                  {badge.label}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-white truncate">
                    {order.email || 'cliente sem email'}
                    {order.is_first_purchase && (
                      <span className="ml-2 text-xs px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-300">
                        novo
                      </span>
                    )}
                  </p>
                  <p className="text-xs text-slate-500 mt-0.5 truncate">
                    {campaignLabel(order)}
                    {order.platform_order_number && (
                      <span className="ml-2 text-slate-600">
                        #{order.platform_order_number}
                      </span>
                    )}
                  </p>
                </div>
                <div className="text-right shrink-0">
                  <p className={`text-lg font-bold ${flashed ? 'text-emerald-300' : 'text-emerald-400'}`}>
                    {fmt(order.total_price)}
                  </p>
                  <p className="text-xs text-slate-500" title={fmtTime(order.created_at)}>
                    há {timeAgo(order.created_at)}
                    <span className="hidden sr-only">{tick}</span>
                  </p>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function StatBig({
  label, value, subValue, icon: Icon, accent,
}: {
  label: string; value: string; subValue?: string
  icon: React.ElementType; accent: string
}) {
  return (
    <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-6">
      <div className="flex items-start justify-between mb-3">
        <span className="text-xs uppercase tracking-wider text-slate-500">{label}</span>
        <Icon size={16} className={accent} />
      </div>
      <p className={`text-3xl font-bold ${accent}`}>{value}</p>
      {subValue && <p className="text-xs text-slate-500 mt-1">{subValue}</p>}
    </div>
  )
}
