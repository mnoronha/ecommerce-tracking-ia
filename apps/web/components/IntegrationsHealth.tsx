'use client'

import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, CheckCircle2, AlertTriangle, XCircle, CircleDashed, ChevronDown, ChevronUp } from 'lucide-react'
import type { ClientChannels } from '@/lib/use-client-config'

interface PlatformHealth {
  status: 'ok' | 'expired' | 'invalid' | 'error' | 'not_configured' | 'unchecked' | string
  error?: string | null
  last_checked?: string | null
}

interface HealthResponse {
  pixel_id: string
  meta: PlatformHealth
  google_ads: PlatformHealth
  ga4: PlatformHealth
  tiktok: PlatformHealth
  pinterest: PlatformHealth
  shopify: PlatformHealth
}

const PLATFORMS: Array<{
  key: keyof Omit<HealthResponse, 'pixel_id'>
  label: string
  channelKey?: keyof ClientChannels
}> = [
  { key: 'shopify',    label: 'Shopify',    channelKey: 'shopify' },
  { key: 'meta',       label: 'Meta',       channelKey: 'meta' },
  { key: 'google_ads', label: 'Google Ads', channelKey: 'googleAds' },
  { key: 'ga4',        label: 'GA4',        channelKey: 'ga4' },
  { key: 'tiktok',     label: 'TikTok',     channelKey: 'tiktok' },
]

function isValidHealthPayload(payload: unknown): payload is HealthResponse {
  if (!payload || typeof payload !== 'object') return false
  const rec = payload as Record<string, unknown>
  return typeof rec.pixel_id === 'string' && typeof rec.meta === 'object' && rec.meta !== null
}

function formatAgo(iso?: string | null): string {
  if (!iso) return 'nunca testado'
  const diff = Date.now() - new Date(iso).getTime()
  if (isNaN(diff) || diff < 0) return 'agora'
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'agora'
  if (mins < 60) return `há ${mins}m`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `há ${hrs}h`
  return `há ${Math.floor(hrs / 24)}d`
}

function StatusPill({ status, label }: { status: string; label: string }) {
  const styles: Record<string, { wrap: string; Icon: any }> = {
    ok:             { wrap: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/25', Icon: CheckCircle2 },
    expired:        { wrap: 'bg-red-500/15 text-red-400 border-red-500/30',             Icon: XCircle },
    invalid:        { wrap: 'bg-red-500/15 text-red-400 border-red-500/30',             Icon: XCircle },
    error:          { wrap: 'bg-amber-500/15 text-amber-400 border-amber-500/30',       Icon: AlertTriangle },
    unchecked:      { wrap: 'bg-slate-500/10 text-slate-400 border-slate-500/25',       Icon: CircleDashed },
    not_configured: { wrap: 'bg-slate-800/50 text-slate-500 border-slate-700/40',       Icon: CircleDashed },
  }
  const { wrap, Icon } = styles[status] ?? styles.unchecked
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full border ${wrap}`}>
      <Icon size={12} />
      <span>{label}</span>
    </span>
  )
}

export default function IntegrationsHealth({
  pixelId,
  contractedChannels,
}: {
  pixelId: string
  /** Canais contratados pelo cliente — se informado, só exibe os canais ativos. */
  contractedChannels?: ClientChannels
}) {
  const [data, setData]       = useState<HealthResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)
  const [probing, setProbing] = useState(false)
  const [testing, setTesting] = useState<string | null>(null)
  const [open, setOpen]       = useState(false)

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/integrations/${encodeURIComponent(pixelId)}/status`, {
        cache: 'no-store',
        signal,
      })
      if (!res.ok) {
        const errBody = await res.json().catch(() => null)
        const detail =
          (errBody && typeof errBody.error === 'string' && errBody.error) ||
          `HTTP ${res.status}`
        setError(`Não foi possível carregar a saúde das integrações (${detail}).`)
        return
      }
      const payload = await res.json().catch(() => null)
      if (!isValidHealthPayload(payload)) {
        setError('Resposta inválida ao consultar a saúde das integrações.')
        return
      }
      setData(payload)
    } catch (err) {
      if ((err as { name?: string })?.name === 'AbortError') return
      setError('Falha de conexão ao verificar a saúde das integrações.')
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [pixelId])

  useEffect(() => {
    setData(null)
    const controller = new AbortController()
    load(controller.signal)
    return () => controller.abort()
  }, [load])

  const reprobe = async () => {
    setProbing(true)
    setError(null)
    try {
      const res = await fetch(`/api/integrations/${encodeURIComponent(pixelId)}/status`, {
        method: 'POST',
        cache: 'no-store',
      })
      if (!res.ok) {
        setError(`Falha ao retestar integrações (HTTP ${res.status}).`)
        return
      }
      const payload = await res.json().catch(() => null)
      if (isValidHealthPayload(payload)) {
        setData(payload)
      } else {
        setError('Resposta inválida ao retestar integrações.')
      }
    } catch {
      setError('Falha de conexão ao retestar integrações.')
    } finally {
      setProbing(false)
    }
  }

  const testOne = async (platform: string) => {
    setTesting(platform)
    setError(null)
    try {
      const res = await fetch(
        `/api/integrations/${encodeURIComponent(pixelId)}/test/${encodeURIComponent(platform)}`,
        { method: 'POST', cache: 'no-store' },
      )
      if (!res.ok) {
        setError(`Falha ao testar ${platform} (HTTP ${res.status}).`)
        return
      }
      const r = await res.json().catch(() => null)
      if (r && typeof r.status === 'string') {
        setData(prev => prev ? {
          ...prev,
          [platform]: { status: r.status, error: r.error ?? null, last_checked: r.last_checked },
        } : prev)
      }
    } catch {
      setError(`Falha de conexão ao testar ${platform}.`)
    } finally {
      setTesting(null)
    }
  }

  if (loading && !data) {
    return (
      <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] px-4 py-3 text-xs text-slate-500 flex items-center gap-2">
        <RefreshCw size={12} className="animate-spin" />
        Carregando saúde das integrações…
      </div>
    )
  }

  if (!data) {
    return (
      <div className="bg-[#1a1f2e] rounded-xl border border-amber-500/25 px-4 py-3 text-xs text-amber-300/90 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <AlertTriangle size={13} className="text-amber-400 shrink-0" />
          <span>{error ?? 'Saúde das integrações indisponível no momento.'}</span>
        </div>
        <button
          type="button"
          onClick={() => load()}
          className="inline-flex items-center gap-1.5 text-xs text-slate-300 hover:text-white bg-[#22283a] hover:bg-[#2a3147] border border-[#353d54] px-2.5 py-1 rounded-lg transition-colors"
        >
          <RefreshCw size={11} />
          Tentar novamente
        </button>
      </div>
    )
  }

  const visiblePlatforms = contractedChannels
    ? PLATFORMS.filter(p => !p.channelKey || contractedChannels[p.channelKey])
    : PLATFORMS

  const configured = visiblePlatforms.filter(p => (data[p.key]?.status ?? 'not_configured') !== 'not_configured')
  const broken     = configured.filter(p => ['expired', 'invalid', 'error'].includes(data[p.key]?.status ?? ''))
  const allOk      = configured.length > 0 && broken.length === 0

  return (
    <div className={`bg-[#1a1f2e] rounded-xl border ${broken.length > 0 ? 'border-red-500/35' : 'border-[#2a2f3e]'}`}>
      <div className="px-4 py-3 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2.5 flex-wrap">
          <span className="text-xs font-semibold text-slate-300">Integrações</span>
          {configured.length === 0 ? (
            <span className="text-xs text-slate-500">Nenhuma integração configurada</span>
          ) : (
            configured.map(p => (
              <StatusPill key={p.key} status={data[p.key]?.status ?? 'unchecked'} label={p.label} />
            ))
          )}
          {broken.length > 0 && (
            <span className="text-xs text-red-400 font-medium">
              · {broken.length} {broken.length === 1 ? 'precisa de atenção' : 'precisam de atenção'}
            </span>
          )}
          {allOk && (
            <span className="text-xs text-slate-500">· tudo operacional</span>
          )}
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={reprobe}
            disabled={probing}
            className="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-white bg-[#22283a] hover:bg-[#2a3147] border border-[#353d54] px-2.5 py-1 rounded-lg transition-colors disabled:opacity-50"
            title="Testar todas as integrações agora"
          >
            <RefreshCw size={11} className={probing ? 'animate-spin' : ''} />
            {probing ? 'Testando…' : 'Retestar'}
          </button>
          <button
            type="button"
            onClick={() => setOpen(v => !v)}
            className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-white px-2 py-1 rounded-lg"
          >
            Detalhes
            {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>
        </div>
      </div>

      {error && (
        <div className="px-4 py-2 border-t border-amber-500/20 bg-amber-500/5 text-xs text-amber-300 flex items-center justify-between gap-2">
          <span>{error}</span>
          <button
            type="button"
            onClick={() => setError(null)}
            className="text-amber-200/80 hover:text-amber-100 underline"
          >
            Fechar
          </button>
        </div>
      )}

      {(open || broken.length > 0) && (
        <div className="border-t border-[#2a2f3e] px-4 py-3 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2.5">
          {(open ? visiblePlatforms : broken).map(p => {
            const h = data[p.key] ?? { status: 'not_configured' }
            return (
              <div key={p.key} className="bg-[#131722] rounded-lg border border-[#242a3a] px-3 py-2.5 flex flex-col gap-1.5">
                <div className="flex items-center justify-between">
                  <StatusPill status={h.status} label={p.label} />
                  {h.status !== 'not_configured' && (
                    <button
                      type="button"
                      onClick={() => testOne(p.key)}
                      disabled={testing === p.key}
                      className="text-[11px] text-indigo-400 hover:text-indigo-300 disabled:opacity-50"
                    >
                      {testing === p.key ? 'Testando…' : 'Testar'}
                    </button>
                  )}
                </div>
                {h.error && (
                  <p className="text-[11px] text-red-400/90 leading-snug break-words">{h.error}</p>
                )}
                <p className="text-[10px] text-slate-500">
                  {h.status === 'not_configured'
                    ? 'Não configurada'
                    : `Verificado ${formatAgo(h.last_checked)}`}
                </p>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
