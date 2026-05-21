'use client'

import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, CheckCircle2, AlertTriangle, XCircle, Circle, Loader2 } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

type Status = 'healthy' | 'expiring_soon' | 'expired' | 'invalid' | 'unknown'

interface PlatformHealth {
  status:       Status
  last_checked: string | null
  error:        string | null
  configured:   boolean
}

interface HealthResponse {
  pixel_id:   string
  meta:       PlatformHealth
  google_ads: PlatformHealth
  ga4:        PlatformHealth
  tiktok:     PlatformHealth
  pinterest:  PlatformHealth
  shopify:    PlatformHealth
}

const PLATFORM_LABEL: Record<string, string> = {
  meta:       'Meta Ads',
  google_ads: 'Google Ads',
  ga4:        'GA4',
  tiktok:     'TikTok Ads',
  pinterest:  'Pinterest',
  shopify:    'Shopify',
}

const ORDER: Array<keyof Omit<HealthResponse, 'pixel_id'>> = [
  'shopify', 'meta', 'google_ads', 'ga4', 'tiktok', 'pinterest',
]

function statusBadge(p: PlatformHealth) {
  if (!p.configured) {
    return { Icon: Circle,        cls: 'bg-slate-800 text-slate-500',          label: 'não conectado' }
  }
  switch (p.status) {
    case 'healthy':
      return { Icon: CheckCircle2, cls: 'bg-emerald-500/10 text-emerald-400',    label: 'saudável' }
    case 'expiring_soon':
      return { Icon: AlertTriangle, cls: 'bg-yellow-500/10 text-yellow-400',     label: 'token expirando' }
    case 'expired':
      return { Icon: XCircle,      cls: 'bg-red-500/10 text-red-400',            label: 'token expirado' }
    case 'invalid':
      return { Icon: XCircle,      cls: 'bg-red-500/10 text-red-400',            label: 'inválido' }
    default:
      return { Icon: Circle,       cls: 'bg-slate-700 text-slate-400',           label: 'sem verificar' }
  }
}

function relativeTime(iso: string | null): string {
  if (!iso) return 'nunca'
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60)   return `${Math.floor(diff)}s atrás`
  if (diff < 3600) return `${Math.floor(diff / 60)}m atrás`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h atrás`
  return `${Math.floor(diff / 86400)}d atrás`
}

export default function IntegrationsHealth({ pixelId }: { pixelId: string }) {
  const [data, setData]       = useState<HealthResponse | null>(null)
  const [probing, setProbing] = useState(false)
  const [busyOne, setBusyOne] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/integrations/${pixelId}/status`, { cache: 'no-store' })
      if (res.ok) setData(await res.json())
    } catch {}
  }, [pixelId])

  useEffect(() => { load() }, [load])

  const probeAll = async () => {
    setProbing(true)
    try {
      const res = await fetch(`${API_URL}/integrations/${pixelId}/status`, { method: 'POST' })
      if (res.ok) setData(await res.json())
    } finally {
      setProbing(false)
    }
  }

  const probeOne = async (platform: string) => {
    setBusyOne(platform)
    try {
      await fetch(`${API_URL}/integrations/${pixelId}/test/${platform}`, { method: 'POST' })
      await load()
    } finally {
      setBusyOne(null)
    }
  }

  if (!data) {
    return (
      <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl px-5 py-4 text-xs text-slate-500">
        Carregando saúde das integrações…
      </div>
    )
  }

  // Headline summary
  const cards = ORDER.map(k => ({ key: k as string, data: data[k] }))
  const issues = cards.filter(c => c.data.configured && c.data.status !== 'healthy' && c.data.status !== 'unknown')

  return (
    <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl">
      <div className="px-5 py-3 border-b border-[#2a2f3e] flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-200">Saúde das integrações</h3>
          <p className="text-xs text-slate-500">
            {issues.length === 0
              ? 'Todas as integrações conectadas estão respondendo.'
              : `${issues.length} integraç${issues.length === 1 ? 'ão precisa' : 'ões precisam'} de atenção.`}
          </p>
        </div>
        <button
          onClick={probeAll}
          disabled={probing}
          className="px-3 py-1.5 rounded-lg bg-[#252a3a] hover:bg-[#2a2f3e] text-xs text-slate-300 border border-[#2a2f3e] flex items-center gap-1.5 disabled:opacity-50"
        >
          {probing ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          Testar todas
        </button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 divide-x divide-y md:divide-y-0 divide-[#2a2f3e]">
        {cards.map(({ key, data: p }) => {
          const badge = statusBadge(p)
          const Icon = badge.Icon
          const inactive = !p.configured
          return (
            <div key={key} className="px-4 py-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-xs font-medium text-slate-300 truncate">{PLATFORM_LABEL[key] || key}</p>
                  <div className={`inline-flex items-center gap-1 mt-1 px-1.5 py-0.5 rounded text-[10px] font-medium ${badge.cls}`}>
                    <Icon size={10} />
                    {badge.label}
                  </div>
                </div>
                {!inactive && (
                  <button
                    onClick={() => probeOne(key)}
                    disabled={busyOne === key}
                    className="text-[10px] text-slate-500 hover:text-slate-300 px-1 py-0.5 disabled:opacity-50"
                    title="Testar agora"
                  >
                    {busyOne === key ? <Loader2 size={10} className="animate-spin" /> : 'Testar'}
                  </button>
                )}
              </div>
              <p className="text-[10px] text-slate-500 mt-1.5 truncate" title={p.error || ''}>
                {inactive ? '—' : (p.error ? p.error : `Verif. ${relativeTime(p.last_checked)}`)}
              </p>
            </div>
          )
        })}
      </div>
    </div>
  )
}
