'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import { RefreshCw, Users, ShoppingCart, ShoppingBag, Crown, Clock, Loader2, CheckCircle, AlertCircle, XCircle } from 'lucide-react'

// ── Types ──────────────────────────────────────────────────────────────────────

interface AudienceStatus {
  audience_type:        string
  audience_name:        string
  users_count:          number
  last_synced_at:       string | null
  status:               'synced' | 'error' | 'never_synced' | 'syncing'
  error_message:        string | null
  platform_audience_id: string | null
}

// ── Constants ─────────────────────────────────────────────────────────────────

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'
// CLIENT_PIXEL_ID resolved dynamically via useParams inside component

const AUDIENCE_META: Record<string, {
  label:       string
  description: string
  icon:        React.ElementType
  color:       string
  useCase:     string
}> = {
  high_ltv: {
    label:       'Alto LTV',
    description: 'Clientes com LTV acima do threshold',
    icon:        Crown,
    color:       'text-yellow-400',
    useCase:     'Base para Lookalike Audience — encontra clientes parecidos com seus melhores compradores',
  },
  cart_abandoners: {
    label:       'Abandono de Carrinho',
    description: 'Adicionaram ao carrinho mas nunca compraram',
    icon:        ShoppingCart,
    color:       'text-orange-400',
    useCase:     'Campanha de retargeting com oferta ou urgência para converter carrinhos abandonados',
  },
  recent_buyers: {
    label:       'Compradores Recentes',
    description: 'Compraram nos últimos 7 dias',
    icon:        ShoppingBag,
    color:       'text-emerald-400',
    useCase:     'Lista de supressão — evita gastar verba em quem já comprou recentemente',
  },
  top_customers: {
    label:       'Top 10% LTV',
    description: 'Top clientes por valor de vida (seed)',
    icon:        Crown,
    color:       'text-indigo-400',
    useCase:     'Seed para Lookalike 1%-3% — alta precisão para escalar sua base de compradores premium',
  },
  inactive: {
    label:       'Inativos',
    description: 'Compraram mas não visitam há 90+ dias',
    icon:        Clock,
    color:       'text-red-400',
    useCase:     'Campanha de reativação com cupom ou novidade para trazer clientes dormentes de volta',
  },
}

const STATUS_CONFIG = {
  synced:       { icon: CheckCircle, color: 'text-emerald-400', label: 'Sincronizado' },
  error:        { icon: XCircle,     color: 'text-red-400',     label: 'Erro' },
  never_synced: { icon: AlertCircle, color: 'text-slate-500',   label: 'Nunca sincronizado' },
  syncing:      { icon: Loader2,     color: 'text-indigo-400',  label: 'Sincronizando…' },
}

const fmtDt = (iso: string | null) => iso
  ? new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })
  : '—'

// ── Components ────────────────────────────────────────────────────────────────

function AudienceCard({
  aud,
  onSync,
  syncing,
}: {
  aud:     AudienceStatus
  onSync:  (type: string) => void
  syncing: boolean
}) {
  const meta   = AUDIENCE_META[aud.audience_type]
  const status = STATUS_CONFIG[aud.status] || STATUS_CONFIG.never_synced
  const Icon   = meta?.icon || Users
  const StatusIcon = status.icon

  return (
    <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] p-5 flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className={`p-2 rounded-lg bg-[#0f1117] ${meta?.color || 'text-slate-400'}`}>
            <Icon size={18} />
          </div>
          <div>
            <p className="text-sm font-semibold text-white">{meta?.label || aud.audience_type}</p>
            <p className="text-xs text-slate-500 mt-0.5">{meta?.description || ''}</p>
          </div>
        </div>

        {/* Status badge */}
        <div className={`flex items-center gap-1.5 text-xs ${status.color}`}>
          <StatusIcon size={13} className={aud.status === 'syncing' ? 'animate-spin' : ''} />
          <span>{status.label}</span>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-[#0f1117] rounded-lg p-3">
          <p className="text-xs text-slate-500">Usuários</p>
          <p className="text-lg font-bold text-white mt-0.5">
            {aud.users_count > 0 ? aud.users_count.toLocaleString('pt-BR') : '—'}
          </p>
        </div>
        <div className="bg-[#0f1117] rounded-lg p-3">
          <p className="text-xs text-slate-500">Última sync</p>
          <p className="text-xs font-medium text-slate-300 mt-0.5">{fmtDt(aud.last_synced_at)}</p>
        </div>
      </div>

      {/* Use case */}
      {meta?.useCase && (
        <p className="text-xs text-slate-500 leading-relaxed">{meta.useCase}</p>
      )}

      {/* Error */}
      {aud.status === 'error' && aud.error_message && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-3">
          <p className="text-xs text-red-400">{aud.error_message}</p>
        </div>
      )}

      {/* Meta audience ID */}
      {aud.platform_audience_id && (
        <p className="text-xs text-slate-600 font-mono">ID: {aud.platform_audience_id}</p>
      )}

      {/* Sync button */}
      <button
        onClick={() => onSync(aud.audience_type)}
        disabled={syncing || aud.status === 'syncing'}
        className="w-full flex items-center justify-center gap-2 text-xs bg-indigo-600/20 hover:bg-indigo-600/40 disabled:opacity-40 text-indigo-400 border border-indigo-500/20 px-3 py-2 rounded-lg transition-colors font-medium"
      >
        {syncing ? (
          <><Loader2 size={12} className="animate-spin" /> Sincronizando…</>
        ) : (
          <><RefreshCw size={12} /> Sincronizar agora</>
        )}
      </button>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function AudienciasPage() {
  const params = useParams()
  const CLIENT_PIXEL_ID = (params?.clientId as string) || process.env.NEXT_PUBLIC_CLIENT_PIXEL_ID || 'lk-sneakers'

  const [audiences, setAudiences] = useState<AudienceStatus[]>([])
  const [loading, setLoading]     = useState(true)
  const [syncingAll, setSyncingAll] = useState(false)
  const [syncingOne, setSyncingOne] = useState<string | null>(null)
  const [clientId, setClientId]   = useState<string | null>(null)
  const [hasCredentials, setHasCredentials] = useState(true)

  // Resolve client UUID and check credentials
  useEffect(() => {
    supabase.from('clients')
      .select('id, meta_ad_account_id, meta_access_token')
      .eq('pixel_id', CLIENT_PIXEL_ID)
      .limit(1).single()
      .then(({ data }) => {
        if (data) {
          setClientId(data.id)
          setHasCredentials(!!(data.meta_ad_account_id && data.meta_access_token))
        }
      })
  }, [])

  const loadStatus = useCallback(async (cid: string) => {
    setLoading(true)
    const { data } = await supabase
      .from('audience_syncs')
      .select('audience_type, audience_name, users_count, last_synced_at, status, error_message, platform_audience_id')
      .eq('client_id', cid)
      .eq('platform', 'meta')
      .order('audience_type')

    const synced: Record<string, AudienceStatus> = {}
    for (const row of (data || [])) {
      synced[row.audience_type] = row as AudienceStatus
    }

    const ALL_TYPES = ['high_ltv', 'cart_abandoners', 'recent_buyers', 'top_customers', 'inactive']
    setAudiences(ALL_TYPES.map(t => synced[t] || {
      audience_type:        t,
      audience_name:        AUDIENCE_META[t]?.label || t,
      users_count:          0,
      last_synced_at:       null,
      status:               'never_synced',
      error_message:        null,
      platform_audience_id: null,
    }))

    setLoading(false)
  }, [])

  useEffect(() => {
    if (clientId) loadStatus(clientId)
  }, [clientId, loadStatus])

  const syncAll = useCallback(async () => {
    setSyncingAll(true)
    try {
      await fetch(`${API_URL}/audiences/${CLIENT_PIXEL_ID}/sync`, { method: 'POST' })
      // Mark all as syncing locally
      setAudiences(prev => prev.map(a => ({ ...a, status: 'syncing' as const })))
      // Reload after delay
      await new Promise(r => setTimeout(r, 3000))
      if (clientId) await loadStatus(clientId)
    } finally {
      setSyncingAll(false)
    }
  }, [clientId, loadStatus])

  const syncOne = useCallback(async (audienceType: string) => {
    setSyncingOne(audienceType)
    try {
      await fetch(`${API_URL}/audiences/${CLIENT_PIXEL_ID}/sync/${audienceType}`, { method: 'POST' })
      setAudiences(prev => prev.map(a =>
        a.audience_type === audienceType ? { ...a, status: 'syncing' as const } : a
      ))
      await new Promise(r => setTimeout(r, 3000))
      if (clientId) await loadStatus(clientId)
    } finally {
      setSyncingOne(null)
    }
  }, [clientId, loadStatus])

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white">Meta Custom Audiences</h1>
          <p className="text-xs text-slate-500">Sincronize segmentos de clientes automaticamente com o Meta Ads</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => clientId && loadStatus(clientId)}
            className="text-xs text-slate-400 hover:text-white flex items-center gap-1.5 transition-colors"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            Atualizar
          </button>
          <button
            onClick={syncAll}
            disabled={syncingAll || !hasCredentials}
            className="flex items-center gap-2 text-xs bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-4 py-2 rounded-lg transition-colors font-medium"
          >
            {syncingAll
              ? <><Loader2 size={12} className="animate-spin" /> Sincronizando tudo…</>
              : <><RefreshCw size={12} /> Sincronizar tudo</>
            }
          </button>
        </div>
      </div>

      <div className="p-6 space-y-5">

        {/* Credentials warning */}
        {!hasCredentials && (
          <div className="bg-yellow-500/10 border border-yellow-500/20 rounded-xl p-4">
            <p className="text-sm font-medium text-yellow-400">Credenciais Meta não configuradas</p>
            <p className="text-xs text-slate-400 mt-1">
              Configure <code className="bg-yellow-500/10 px-1 rounded">meta_ad_account_id</code> e{' '}
              <code className="bg-yellow-500/10 px-1 rounded">meta_access_token</code> no cliente para ativar a sincronização.
            </p>
          </div>
        )}

        {/* How it works */}
        <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] p-5">
          <p className="text-sm font-semibold text-slate-300 mb-3">Como funciona</p>
          <div className="grid grid-cols-3 gap-4 text-xs text-slate-500">
            <div className="flex gap-3">
              <span className="text-indigo-400 font-bold text-base leading-none">1</span>
              <div>
                <p className="text-slate-300 font-medium">Segmentação automática</p>
                <p className="mt-0.5">Nosso sistema identifica e classifica clientes em 5 segmentos baseados em comportamento e LTV.</p>
              </div>
            </div>
            <div className="flex gap-3">
              <span className="text-indigo-400 font-bold text-base leading-none">2</span>
              <div>
                <p className="text-slate-300 font-medium">Hash SHA-256</p>
                <p className="mt-0.5">Emails e telefones são criptografados antes de sair do servidor — conformidade com LGPD e políticas Meta.</p>
              </div>
            </div>
            <div className="flex gap-3">
              <span className="text-indigo-400 font-bold text-base leading-none">3</span>
              <div>
                <p className="text-slate-300 font-medium">Sync a cada 6h</p>
                <p className="mt-0.5">Audiências são atualizadas automaticamente. Use-as em campanhas Lookalike, Retargeting e Supressão.</p>
              </div>
            </div>
          </div>
        </div>

        {/* Audience cards */}
        {loading ? (
          <div className="flex items-center gap-2 text-slate-500 text-sm py-8">
            <Loader2 size={16} className="animate-spin" /> Carregando audiências…
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
            {audiences.map(aud => (
              <AudienceCard
                key={aud.audience_type}
                aud={aud}
                onSync={syncOne}
                syncing={syncingOne === aud.audience_type}
              />
            ))}
          </div>
        )}

        {/* Summary stats */}
        {!loading && audiences.some(a => a.status === 'synced') && (
          <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] p-5">
            <p className="text-sm font-semibold text-slate-300 mb-4">Resumo de Sincronização</p>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <p className="text-2xl font-bold text-white">
                  {audiences.filter(a => a.status === 'synced').length}
                </p>
                <p className="text-xs text-slate-500 mt-0.5">Audiências ativas</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-indigo-400">
                  {audiences.reduce((s, a) => s + a.users_count, 0).toLocaleString('pt-BR')}
                </p>
                <p className="text-xs text-slate-500 mt-0.5">Total de usuários (c/ sobreposição)</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-emerald-400">
                  {audiences.filter(a => a.platform_audience_id).length}
                </p>
                <p className="text-xs text-slate-500 mt-0.5">Audiências criadas no Meta</p>
              </div>
            </div>
          </div>
        )}

      </div>
    </div>
  )
}
