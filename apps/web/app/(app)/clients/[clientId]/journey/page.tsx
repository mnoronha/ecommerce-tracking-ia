'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { Loader2, ChevronDown, ChevronRight, ChevronUp, Package, Megaphone, RefreshCw, Target, Sparkles, AlertTriangle, Layers } from 'lucide-react'
import { useDatePeriod } from '@/lib/use-date-range'
import { PeriodPicker } from '@/components/PeriodPicker'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

type DatePreset = '1d' | '7d' | '30d' | '90d' | 'custom'
type Lens = 'campaign' | 'product' | 'channel' | 'meta-attribution' | 'declared-source' | 'ad'

interface ChannelProduct {
  product_id: string
  name:       string
  variants:   number
  units:      number
  revenue:    number
  profit:     number | null
}
interface ChannelRow {
  channel:       string
  orders:        number
  revenue:       number
  profit:        number | null
  units:         number
  avg_ticket:    number
  product_count: number
  products:      ChannelProduct[]
}

interface DeclaredSourceRow {
  source_declared:    string
  responses:          number
  declared_orders:    number
  declared_revenue:   number
  utm_match_orders:   number
  utm_match_revenue:  number
  utm_miss_orders:    number
  utm_miss_revenue:   number
}

interface ProductInCampaign {
  product_id: string
  name:       string
  sku:        string | null
  units:      number
  revenue:    number
  profit:     number | null
}
interface CampaignRow {
  source:           string
  medium:           string
  campaign:         string
  campaign_id:      string | null
  platform:         string
  orders:           number
  revenue:          number
  revenue_ltv:      number
  ltv_uplift_pct:   number | null
  profit:           number | null
  units:            number
  avg_ticket:       number
  top_products:     ProductInCampaign[]
}
interface CampaignInProduct {
  platform:    string
  source:      string
  campaign:    string
  campaign_id: string | null
  units:       number
  revenue:     number
  orders:      number
}
interface ProductRow {
  product_id:    string
  name:          string
  sku:           string | null
  units:         number
  revenue:       number
  profit:        number | null
  orders:        number
  top_campaigns: CampaignInProduct[]
}
interface AdRow {
  ad_id:        string
  ad_name:      string
  campaign:     string
  platform:     string
  source:       string
  image_url:    string | null
  status:       string | null
  orders:       number
  revenue:      number
  profit:       number | null
  units:        number
  avg_ticket:   number
  top_products: Array<{ product_id: string; name: string; sku: string | null; units: number; revenue: number }>
}

interface MetaAttrRow {
  campaign_id:    string
  campaign_name:  string
  spend:          number
  impressions:    number
  clicks:         number
  meta_purchases: number
  meta_revenue:   number
  meta_roas:      number | null
  meta_cpa:       number | null
  server_orders:  number
  server_revenue: number
  purchases_diff: number
  ads_count:      number
}
interface MetaAttrTotals {
  spend:           number
  meta_purchases:  number
  meta_revenue:    number
  server_orders:   number
  server_revenue:  number
  meta_roas:       number | null
}

const fmt = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n)

const PLATFORM_BADGE: Record<string, string> = {
  meta:      'bg-blue-500/15 text-blue-300 border-blue-500/25',
  google:    'bg-yellow-500/15 text-yellow-300 border-yellow-500/25',
  tiktok:    'bg-pink-500/15 text-pink-300 border-pink-500/25',
  pinterest: 'bg-red-500/15 text-red-300 border-red-500/25',
  organic:   'bg-emerald-500/15 text-emerald-300 border-emerald-500/25',
  email:     'bg-purple-500/15 text-purple-300 border-purple-500/25',
  direto:    'bg-slate-500/15 text-slate-400 border-slate-500/25',
}
function badge(platform: string) {
  return PLATFORM_BADGE[platform] || 'bg-indigo-500/15 text-indigo-300 border-indigo-500/25'
}

const CHANNEL_LABEL: Record<string, string> = {
  meta:      'Meta Ads',
  google:    'Google Ads',
  tiktok:    'TikTok',
  pinterest: 'Pinterest',
  email:     'E-mail',
  organic:   'Orgânico',
  direto:    'Direto',
  direct:    'Direto',
  pos:       'Loja Física',
  ig:        'Instagram',
}
function channelLabel(channel: string): string {
  return CHANNEL_LABEL[channel] || channel.charAt(0).toUpperCase() + channel.slice(1)
}

// A campaign name that looks like a raw UTM template or unresolved ID
function isRawUTM(campaign: string): boolean {
  if (!campaign || campaign === '—') return true
  if (/^\d{10,20}$/.test(campaign)) return true                       // pure Meta ID
  if (/^meta\s+paid/i.test(campaign)) return true                     // UTM template prefix
  if (/^\d+$/.test(campaign) && campaign.length > 5) return true      // any long numeric
  return false
}

function yesterdayStr(): string {
  const d = new Date()
  d.setDate(d.getDate() - 1)
  return d.toISOString().slice(0, 10)
}

function buildQuery(preset: DatePreset, from: string, to: string): string {
  if (preset === '1d') {
    const y = yesterdayStr()
    return `start=${y}&end=${y}`
  }
  if (preset === 'custom' && from && to) return `start=${from}&end=${to}`
  const dMap: Record<string, number> = { '7d': 7, '30d': 30, '90d': 90 }
  return `days=${dMap[preset] ?? 30}`
}

function periodLabel(preset: DatePreset, from: string, to: string): string {
  if (preset === '1d') return 'Ontem'
  if (preset === 'custom' && from && to) {
    const fmt2 = (s: string) => s.slice(5).replace('-', '/')
    return `${fmt2(from)} → ${fmt2(to)}`
  }
  const dMap: Record<string, string> = { '7d': 'Últimos 7 dias', '30d': 'Últimos 30 dias', '90d': 'Últimos 90 dias' }
  return dMap[preset] ?? 'Últimos 30 dias'
}

export default function JourneyPage() {
  const params  = useParams()
  const pixelId = params.clientId as string

  const [lens,    setLens]    = useState<Lens>('campaign')
  const { period, from, to, setPreset, setCustom } = useDatePeriod()

  const [campaigns, setCampaigns] = useState<CampaignRow[]>([])
  const [products,  setProducts]  = useState<ProductRow[]>([])
  const [channels,  setChannels]  = useState<ChannelRow[]>([])
  const [seeAllChannels, setSeeAllChannels] = useState<Set<string>>(new Set())
  const [metaAttr, setMetaAttr] = useState<MetaAttrRow[]>([])
  const [metaTotals, setMetaTotals] = useState<MetaAttrTotals | null>(null)
  const [ads, setAds] = useState<AdRow[]>([])
  const [declared, setDeclared] = useState<DeclaredSourceRow[]>([])
  const [declaredTotal, setDeclaredTotal] = useState(0)
  const [loading,  setLoading]  = useState(true)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [search,   setSearch]   = useState('')
  const [showNamedOnly, setShowNamedOnly] = useState(false)
  const [resolving, setResolving] = useState(false)
  const [resolveMsg, setResolveMsg] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [matching, setMatching] = useState(false)
  const [actionMsg, setActionMsg] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (period === 'custom' && (!from || !to)) return
    setLoading(true)
    const q = buildQuery(period, from, to)
    try {
      if (lens === 'campaign') {
        const res = await fetch(`${API_URL}/journey/${pixelId}/by-campaign?${q}&top_products=10`)
        if (res.ok) setCampaigns((await res.json()).campaigns || [])
      } else if (lens === 'product') {
        const res = await fetch(`${API_URL}/journey/${pixelId}/by-product?${q}&top_campaigns=10`)
        if (res.ok) setProducts((await res.json()).products || [])
      } else if (lens === 'channel') {
        const res = await fetch(`${API_URL}/journey/${pixelId}/by-channel?${q}`)
        if (res.ok) setChannels((await res.json()).channels || [])
      } else if (lens === 'meta-attribution') {
        const res = await fetch(`${API_URL}/journey/${pixelId}/by-meta-attribution?${q}`)
        if (res.ok) {
          const data = await res.json()
          setMetaAttr(data.campaigns || [])
          setMetaTotals(data.totals || null)
        }
      } else if (lens === 'ad') {
        const res = await fetch(`${API_URL}/journey/${pixelId}/by-ad?${q}&top_products=5`)
        if (res.ok) setAds((await res.json()).ads || [])
      } else {
        const res = await fetch(`${API_URL}/journey/${pixelId}/by-declared-source?${q}`)
        if (res.ok) {
          const data = await res.json()
          setDeclared(data.by_source || [])
          setDeclaredTotal(data.total_responses || 0)
        }
      }
    } finally {
      setLoading(false)
    }
  }, [lens, pixelId, period, from, to])

  useEffect(() => { load() }, [load])

  async function handleResolveMeta() {
    setResolving(true); setResolveMsg(null)
    try {
      const res = await fetch(`${API_URL}/journey/${pixelId}/resolve-meta-names`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || data.error || 'Falha ao sincronizar')
      setResolveMsg(`${data.synced} campanhas sincronizadas`)
      await load()
    } catch (e) {
      setResolveMsg('Erro: ' + (e as Error).message)
    } finally {
      setResolving(false)
    }
  }

  async function handleSyncMetaAttribution() {
    setSyncing(true); setActionMsg(null)
    try {
      const res = await fetch(`${API_URL}/journey/${pixelId}/sync-meta-attribution?days=7`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || data.error || 'Falha ao sincronizar')
      setActionMsg(`${data.synced} linhas (ad×dia) sincronizadas`)
      await load()
    } catch (e) {
      setActionMsg('Erro: ' + (e as Error).message)
    } finally {
      setSyncing(false)
    }
  }

  async function handleProbableMatch() {
    setMatching(true); setActionMsg(null)
    const q = buildQuery(period, from, to)
    const days = q.startsWith('days=') ? q.slice(5) : '30'
    try {
      const res = await fetch(`${API_URL}/journey/${pixelId}/probable-match?days=${days}`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || data.error || 'Falha ao rodar match')
      setActionMsg(`${data.matched}/${data.eligible || 0} pedidos atribuídos · ${data.no_data || 0} sem dado`)
    } catch (e) {
      setActionMsg('Erro: ' + (e as Error).message)
    } finally {
      setMatching(false)
    }
  }

  function toggle(key: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function toggleSeeAll(channel: string) {
    setSeeAllChannels(prev => {
      const next = new Set(prev)
      if (next.has(channel)) next.delete(channel)
      else next.add(channel)
      return next
    })
  }

  // Count unresolved Meta campaign names in the campaign lens
  const unresolvedCount = campaigns.filter(c => c.platform === 'meta' && isRawUTM(c.campaign)).length

  const filteredCampaigns = (() => {
    let list = campaigns
    if (showNamedOnly) list = list.filter(c => !isRawUTM(c.campaign))
    if (search) list = list.filter(c =>
      c.campaign.toLowerCase().includes(search.toLowerCase()) ||
      c.source.toLowerCase().includes(search.toLowerCase()))
    return list
  })()

  const filteredProducts = search
    ? products.filter(p =>
        p.name.toLowerCase().includes(search.toLowerCase()) ||
        (p.sku && p.sku.toLowerCase().includes(search.toLowerCase())))
    : products
  const filteredMetaAttr = search
    ? metaAttr.filter(m =>
        m.campaign_name.toLowerCase().includes(search.toLowerCase()) ||
        m.campaign_id.toLowerCase().includes(search.toLowerCase()))
    : metaAttr

  const totalRevenue = lens === 'campaign'
    ? campaigns.reduce((s, c) => s + c.revenue, 0)
    : lens === 'channel'
      ? channels.reduce((s, c) => s + c.revenue, 0)
    : lens === 'product'
      ? products.reduce((s, p) => s + p.revenue, 0)
      : lens === 'declared-source'
        ? declared.reduce((s, d) => s + d.declared_revenue, 0)
        : lens === 'ad'
          ? ads.reduce((s, a) => s + a.revenue, 0)
          : metaTotals?.meta_revenue || 0

  const periodLbl = periodLabel(period, from, to)

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white">Jornada — Campanha × Produto</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Quem comprou o quê veio de onde · <span className="text-slate-400">{periodLbl}</span>
          </p>
        </div>
        <div className="flex items-center gap-3 flex-wrap justify-end">
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={lens === 'campaign' ? 'Buscar campanha…' : 'Buscar produto…'}
            className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder-slate-600 outline-none focus:border-indigo-500 w-44"
          />
          {/* Date preset buttons */}
          <PeriodPicker period={period} from={from} to={to} onPreset={setPreset} onCustom={setCustom} />
        </div>
      </div>

      {/* Lens toggle */}
      <div className="px-6 pt-6">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="inline-flex bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg p-1">
            <button
              onClick={() => { setLens('campaign'); setExpanded(new Set()) }}
              className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                lens === 'campaign' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
              }`}
            >
              <Megaphone size={14} />Por campanha
            </button>
            <button
              onClick={() => { setLens('product'); setExpanded(new Set()) }}
              className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                lens === 'product' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
              }`}
            >
              <Package size={14} />Por produto
            </button>
            <button
              onClick={() => { setLens('channel'); setExpanded(new Set()) }}
              className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                lens === 'channel' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
              }`}
            >
              <Layers size={14} />Por canal
            </button>
            <button
              onClick={() => { setLens('meta-attribution'); setExpanded(new Set()) }}
              className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                lens === 'meta-attribution' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
              }`}
            >
              <Target size={14} />Atribuído pelo Meta
            </button>
            <button
              onClick={() => { setLens('declared-source'); setExpanded(new Set()) }}
              className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                lens === 'declared-source' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
              }`}
            >
              <Sparkles size={14} />Como te conheceram
            </button>
            <button
              onClick={() => { setLens('ad'); setExpanded(new Set()) }}
              className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                lens === 'ad' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
              }`}
            >
              <Megaphone size={14} />Por anúncio
            </button>
          </div>

          {lens === 'campaign' || lens === 'product' ? (
            <>
              <button
                onClick={handleResolveMeta}
                disabled={resolving}
                title="Busca os nomes reais das campanhas no Meta Ads quando elas aparecem como ID numérico"
                className={`flex items-center gap-2 text-xs border px-3 py-2 rounded-lg transition-colors ${
                  unresolvedCount > 0 && lens === 'campaign'
                    ? 'bg-amber-500/10 border-amber-500/30 text-amber-300 hover:bg-amber-500/20'
                    : 'bg-[#1a1f2e] hover:bg-[#252a3a] border-[#2a2f3e] text-slate-300'
                }`}
              >
                {resolving
                  ? <><Loader2 size={12} className="animate-spin" />Sincronizando...</>
                  : <><RefreshCw size={12} />Resolver nomes Meta{unresolvedCount > 0 && lens === 'campaign' ? ` (${unresolvedCount})` : ''}</>}
              </button>
              {lens === 'campaign' && (
                <button
                  onClick={() => setShowNamedOnly(v => !v)}
                  className={`text-xs px-3 py-2 rounded-lg border transition-colors ${
                    showNamedOnly
                      ? 'bg-indigo-600/20 border-indigo-500/40 text-indigo-300'
                      : 'bg-[#1a1f2e] border-[#2a2f3e] text-slate-400 hover:text-white'
                  }`}
                >
                  {showNamedOnly ? 'Todas campanhas' : 'Apenas identificadas'}
                </button>
              )}
            </>
          ) : lens === 'meta-attribution' ? (
            <>
              <button
                onClick={handleSyncMetaAttribution}
                disabled={syncing}
                title="Puxa do Meta Marketing API as conversões reportadas por anúncio (últimos 7d)"
                className="flex items-center gap-2 text-xs bg-[#1a1f2e] hover:bg-[#252a3a] border border-[#2a2f3e] text-slate-300 px-3 py-2 rounded-lg transition-colors"
              >
                {syncing
                  ? <><Loader2 size={12} className="animate-spin" />Sincronizando...</>
                  : <><RefreshCw size={12} />Sincronizar atribuição Meta</>}
              </button>
              <button
                onClick={handleProbableMatch}
                disabled={matching}
                title="Atribui pedidos sem UTM ao anúncio mais provável do dia (com confiança)"
                className="flex items-center gap-2 text-xs bg-indigo-600/20 hover:bg-indigo-600/30 border border-indigo-500/30 text-indigo-200 px-3 py-2 rounded-lg transition-colors"
              >
                {matching
                  ? <><Loader2 size={12} className="animate-spin" />Calculando...</>
                  : <><Sparkles size={12} />Match probabilístico</>}
              </button>
            </>
          ) : null}

          {resolveMsg && (lens === 'campaign' || lens === 'product') && (
            <span className="text-xs text-slate-400">{resolveMsg}</span>
          )}
          {actionMsg && lens === 'meta-attribution' && (
            <span className="text-xs text-slate-400">{actionMsg}</span>
          )}
        </div>

        {/* Unresolved banner */}
        {lens === 'campaign' && unresolvedCount > 0 && !showNamedOnly && (
          <div className="mt-3 flex items-center gap-2 text-xs text-amber-300/80 bg-amber-500/5 border border-amber-500/20 rounded-lg px-4 py-2">
            <AlertTriangle size={13} className="shrink-0 text-amber-400" />
            <span>
              <span className="font-medium text-amber-300">{unresolvedCount} campanha{unresolvedCount > 1 ? 's' : ''}</span> com ID não resolvido (aparecem como "meta paid…").
              Clique <span className="font-medium">Resolver nomes Meta</span> para buscar os nomes reais no Meta Ads.
            </span>
          </div>
        )}

        <p className="text-xs text-slate-500 mt-3">
          Receita total no período: <span className="text-emerald-400 font-semibold">{fmt(totalRevenue)}</span>
          {showNamedOnly && lens === 'campaign' && campaigns.length > filteredCampaigns.length && (
            <span className="ml-3 text-slate-600">
              ({campaigns.length - filteredCampaigns.length} campanhas não identificadas ocultas)
            </span>
          )}
        </p>
      </div>

      {/* Content */}
      <div className="p-6">
        {loading ? (
          <div className="flex items-center gap-2 text-slate-500 text-sm py-12 justify-center">
            <Loader2 size={16} className="animate-spin" /> Carregando jornadas...
          </div>
        ) : lens === 'channel' ? (
          <div className="space-y-2">
            {channels.length === 0 ? (
              <p className="text-slate-500 text-sm text-center py-8">Sem dados no período</p>
            ) : channels.map(c => {
              const key  = `channel-${c.channel}`
              const open = expanded.has(key)
              const INLINE = 5
              const showingAll = seeAllChannels.has(c.channel)
              const visible = showingAll ? c.products : c.products.slice(0, INLINE)
              const more = c.products.length - INLINE
              return (
                <div key={key} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
                  <button
                    onClick={() => toggle(key)}
                    className="w-full px-5 py-3.5 flex items-center gap-4 hover:bg-[#252a3a] transition-colors text-left"
                  >
                    {open ? <ChevronDown size={14} className="text-slate-500 shrink-0" /> : <ChevronRight size={14} className="text-slate-500 shrink-0" />}
                    <div className={`px-2 py-0.5 rounded text-xs font-medium border ${badge(c.channel)} shrink-0`}>
                      {c.channel}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-white font-medium truncate">{channelLabel(c.channel)}</p>
                      <p className="text-xs text-slate-500 truncate">
                        {c.orders} pedido{c.orders === 1 ? '' : 's'} · {c.units} un. · {c.product_count} produto{c.product_count === 1 ? '' : 's'}
                      </p>
                    </div>
                    <div className="text-right shrink-0">
                      <p className="text-sm font-bold text-emerald-400">{fmt(c.revenue)}</p>
                      <p className="text-xs text-slate-500">ticket {fmt(c.avg_ticket)}</p>
                    </div>
                  </button>

                  {open && (
                    <div className="border-t border-[#2a2f3e] bg-[#0f1117]">
                      <div className="px-5 py-3 grid grid-cols-4 gap-4">
                        <Mini label="Pedidos"      value={c.orders.toString()} />
                        <Mini label="Receita"      value={fmt(c.revenue)} accent="emerald" />
                        <Mini label="Ticket médio" value={fmt(c.avg_ticket)} />
                        <Mini label="Margem"       value={c.profit != null ? fmt(c.profit) : '—'} accent="teal" />
                      </div>
                      <div className="border-t border-[#2a2f3e]">
                        <p className="px-5 pt-3 text-xs uppercase tracking-wider text-slate-500 font-medium">
                          Produtos vendidos por {channelLabel(c.channel)} · mais vendidos
                        </p>
                        <table className="w-full text-sm">
                          <tbody>
                            {visible.map(p => (
                              <tr key={p.product_id} className="border-t border-[#2a2f3e] last:border-0">
                                <td className="px-5 py-2.5 text-slate-200 text-xs">
                                  <span className="max-w-md truncate inline-block align-middle">{p.name}</span>
                                  {p.variants > 1 && (
                                    <span className="ml-2 align-middle text-[10px] px-1.5 py-0.5 rounded bg-slate-500/15 text-slate-400 border border-slate-500/25 whitespace-nowrap">
                                      {p.variants} tam.
                                    </span>
                                  )}
                                </td>
                                <td className="px-5 py-2.5 text-right text-white font-semibold text-xs whitespace-nowrap">{p.units} un.</td>
                                <td className="px-5 py-2.5 text-right text-emerald-400 font-semibold whitespace-nowrap">{fmt(p.revenue)}</td>
                                <td className="px-5 py-2.5 text-right text-teal-400 font-medium text-xs whitespace-nowrap">
                                  {p.profit != null ? fmt(p.profit) : <span className="text-slate-600">—</span>}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {more > 0 && (
                          <button
                            onClick={() => toggleSeeAll(c.channel)}
                            className="w-full px-5 py-2.5 text-xs text-indigo-300 hover:text-indigo-200 hover:bg-[#1a1f2e] border-t border-[#2a2f3e] transition-colors flex items-center justify-center gap-1.5"
                          >
                            {showingAll
                              ? <>Ver menos <ChevronUp size={13} /></>
                              : <>Ver mais — todos os {c.product_count} produtos do canal <ChevronDown size={13} /></>}
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        ) : lens === 'meta-attribution' ? (
          <div className="space-y-4">
            {metaTotals && (
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                <Mini label="Spend Meta"        value={fmt(metaTotals.spend)} />
                <Mini label="Compras (Meta)"    value={metaTotals.meta_purchases.toString()} accent="emerald" />
                <Mini label="Receita (Meta)"    value={fmt(metaTotals.meta_revenue)} accent="emerald" />
                <Mini label="Pedidos (server)"  value={metaTotals.server_orders.toString()} />
                <Mini label="ROAS (Meta)"       value={metaTotals.meta_roas != null ? metaTotals.meta_roas.toFixed(2) + 'x' : '—'} accent="teal" />
              </div>
            )}
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-[#2a2f3e] flex items-center justify-between">
                <p className="text-xs uppercase tracking-wider text-slate-500 font-medium">
                  Reconciliação Meta-reported × server-side
                </p>
                <p className="text-xs text-slate-500">
                  diff = compras Meta − pedidos server
                </p>
              </div>
              {filteredMetaAttr.length === 0 ? (
                <p className="text-slate-500 text-sm text-center py-8 px-5">
                  Sem dados. Clique <span className="text-slate-300">"Sincronizar atribuição Meta"</span> para puxar do Marketing API.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-xs text-slate-500 border-b border-[#2a2f3e]">
                        <th className="px-5 py-2.5 text-left font-medium">Campanha</th>
                        <th className="px-5 py-2.5 text-right font-medium">Spend</th>
                        <th className="px-5 py-2.5 text-right font-medium">Cliques</th>
                        <th className="px-5 py-2.5 text-right font-medium">Compras Meta</th>
                        <th className="px-5 py-2.5 text-right font-medium">Receita Meta</th>
                        <th className="px-5 py-2.5 text-right font-medium">ROAS</th>
                        <th className="px-5 py-2.5 text-right font-medium">CPA</th>
                        <th className="px-5 py-2.5 text-right font-medium">Pedidos server</th>
                        <th className="px-5 py-2.5 text-right font-medium">Diff</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredMetaAttr.map(m => {
                        const diff = m.purchases_diff
                        const diffClass = diff === 0
                          ? 'text-slate-500'
                          : diff > 0
                            ? 'text-amber-400'
                            : 'text-rose-400'
                        return (
                          <tr key={m.campaign_id} className="border-t border-[#2a2f3e] hover:bg-[#252a3a]/40">
                            <td className="px-5 py-2.5 max-w-md">
                              <p className="text-slate-200 truncate">{m.campaign_name}</p>
                              <p className="text-xs text-slate-600 font-mono truncate">{m.campaign_id} · {m.ads_count} ads</p>
                            </td>
                            <td className="px-5 py-2.5 text-right text-slate-300 whitespace-nowrap">{fmt(m.spend)}</td>
                            <td className="px-5 py-2.5 text-right text-slate-400 whitespace-nowrap">{m.clicks.toLocaleString('pt-BR')}</td>
                            <td className="px-5 py-2.5 text-right text-emerald-400 font-semibold whitespace-nowrap">{m.meta_purchases}</td>
                            <td className="px-5 py-2.5 text-right text-emerald-400 font-semibold whitespace-nowrap">{fmt(m.meta_revenue)}</td>
                            <td className="px-5 py-2.5 text-right text-teal-400 whitespace-nowrap">
                              {m.meta_roas != null ? m.meta_roas.toFixed(2) + 'x' : <span className="text-slate-600">—</span>}
                            </td>
                            <td className="px-5 py-2.5 text-right text-slate-300 whitespace-nowrap">
                              {m.meta_cpa != null ? fmt(m.meta_cpa) : <span className="text-slate-600">—</span>}
                            </td>
                            <td className="px-5 py-2.5 text-right text-slate-400 whitespace-nowrap">{m.server_orders}</td>
                            <td className={`px-5 py-2.5 text-right font-semibold whitespace-nowrap ${diffClass}`}>
                              {diff > 0 ? `+${diff}` : diff}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            <p className="text-xs text-slate-500 leading-relaxed">
              <span className="text-slate-400 font-medium">Como ler:</span> Meta atribui via janela de cliques/views (até 7d). O server conta pedidos
              cuja UTM bate com o ID da campanha. Diff &gt; 0 = Meta credita compras que o server não vê (provável janela de view ou pedidos sem UTM).
              Use <span className="text-slate-400">Match probabilístico</span> para tentar atribuir esses pedidos ao anúncio mais provável do dia.
            </p>
          </div>
        ) : lens === 'declared-source' ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Mini label="Respostas no período"     value={declaredTotal.toString()} accent="emerald" />
              <Mini
                label="Receita rastreada"
                value={fmt(declared.reduce((s, d) => s + d.declared_revenue, 0))}
                accent="emerald"
              />
              <Mini
                label="Bateu com UTM"
                value={fmt(declared.reduce((s, d) => s + d.utm_match_revenue, 0))}
                accent="teal"
              />
              <Mini
                label="Sem UTM correspondente"
                value={fmt(declared.reduce((s, d) => s + d.utm_miss_revenue, 0))}
                accent="violet"
              />
            </div>
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-[#2a2f3e] flex items-center justify-between">
                <p className="text-xs uppercase tracking-wider text-slate-500 font-medium">
                  Atribuição declarada × UTM
                </p>
                <p className="text-xs text-slate-500">
                  miss = receita que só o survey capturou
                </p>
              </div>
              {declared.length === 0 ? (
                <p className="text-slate-500 text-sm text-center py-8 px-5">
                  Sem respostas no período. O modal aparece automaticamente na página de obrigado.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-xs text-slate-500 border-b border-[#2a2f3e]">
                        <th className="px-5 py-2.5 text-left font-medium">Fonte declarada</th>
                        <th className="px-5 py-2.5 text-right font-medium">Respostas</th>
                        <th className="px-5 py-2.5 text-right font-medium">Receita declarada</th>
                        <th className="px-5 py-2.5 text-right font-medium">Match UTM</th>
                        <th className="px-5 py-2.5 text-right font-medium">Miss UTM (resgate)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {declared.map(d => {
                        const missShare = d.declared_revenue > 0
                          ? (d.utm_miss_revenue / d.declared_revenue) * 100
                          : 0
                        return (
                          <tr key={d.source_declared} className="border-t border-[#2a2f3e] hover:bg-[#252a3a]/40">
                            <td className="px-5 py-2.5">
                              <p className="text-slate-200 capitalize">{d.source_declared.replace(/_/g, ' ')}</p>
                              <p className="text-xs text-slate-600">{d.responses} respostas</p>
                            </td>
                            <td className="px-5 py-2.5 text-right text-slate-400 whitespace-nowrap">{d.declared_orders}</td>
                            <td className="px-5 py-2.5 text-right text-emerald-400 font-semibold whitespace-nowrap">{fmt(d.declared_revenue)}</td>
                            <td className="px-5 py-2.5 text-right text-teal-400 whitespace-nowrap">
                              {fmt(d.utm_match_revenue)}
                              <span className="text-xs text-slate-600 ml-2">{d.utm_match_orders}</span>
                            </td>
                            <td className="px-5 py-2.5 text-right whitespace-nowrap">
                              <span className="text-violet-400 font-semibold">{fmt(d.utm_miss_revenue)}</span>
                              {missShare > 0 && (
                                <span className="text-xs text-violet-400/70 ml-2">{missShare.toFixed(0)}%</span>
                              )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            <p className="text-xs text-slate-500 leading-relaxed">
              <span className="text-slate-400 font-medium">Como ler:</span> a coluna <span className="text-violet-400">Miss UTM</span> é
              a receita que <em>só</em> o survey capturou — pedidos onde o cliente declarou uma fonte (ex: TikTok) mas não tinha UTM/fbclid
              correspondente. Esse é o valor que rebalanceia o orçamento — fontes invisíveis aparecem aqui.
            </p>
          </div>
        ) : lens === 'ad' ? (
          <div className="space-y-2">
            {ads.length === 0 ? (
              <p className="text-slate-500 text-sm text-center py-8">
                Sem dados de anúncio no período. Certifique-se que os pedidos têm <code className="text-slate-400">ad_id</code> preenchido.
              </p>
            ) : ads.map(a => {
              const open = expanded.has(a.ad_id)
              const noAd = a.ad_id === '—'
              return (
                <div key={a.ad_id} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
                  <button
                    onClick={() => toggle(a.ad_id)}
                    className="w-full px-5 py-3.5 flex items-center gap-4 hover:bg-[#252a3a] transition-colors text-left"
                  >
                    {open ? <ChevronDown size={14} className="text-slate-500 shrink-0" /> : <ChevronRight size={14} className="text-slate-500 shrink-0" />}
                    {a.image_url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={a.image_url} alt="" className="w-10 h-10 rounded object-cover shrink-0" />
                    ) : (
                      <div className="w-10 h-10 rounded bg-[#0f1117] shrink-0 flex items-center justify-center">
                        <Megaphone size={14} className="text-slate-600" />
                      </div>
                    )}
                    <div className={`px-2 py-0.5 rounded text-xs font-medium border ${badge(a.platform)} shrink-0`}>
                      {a.platform}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-white font-medium truncate">
                        {noAd ? <span className="text-slate-500 italic">Sem ad_id</span> : a.ad_name}
                      </p>
                      <p className="text-xs text-slate-500 truncate">
                        {a.campaign !== '—' ? a.campaign : a.source}
                        {a.status && <span className={`ml-2 text-[10px] ${a.status === 'ACTIVE' ? 'text-emerald-500' : 'text-slate-600'}`}>{a.status}</span>}
                      </p>
                    </div>
                    <div className="text-right shrink-0">
                      <p className="text-sm font-bold text-emerald-400">{fmt(a.revenue)}</p>
                      <p className="text-xs text-slate-500">{a.orders} pedidos · {a.units} un.</p>
                    </div>
                  </button>

                  {open && (
                    <div className="border-t border-[#2a2f3e] bg-[#0f1117]">
                      <div className="px-5 py-3 grid grid-cols-4 gap-4">
                        <Mini label="Pedidos"      value={a.orders.toString()} />
                        <Mini label="Receita"      value={fmt(a.revenue)} accent="emerald" />
                        <Mini label="Ticket médio" value={fmt(a.avg_ticket)} />
                        <Mini label="Margem"       value={a.profit != null ? fmt(a.profit) : '—'} accent="teal" />
                      </div>
                      {a.top_products.length > 0 && (
                        <div className="border-t border-[#2a2f3e]">
                          <p className="px-5 pt-3 text-xs uppercase tracking-wider text-slate-500 font-medium">
                            Produtos vendidos por este anúncio
                          </p>
                          <table className="w-full text-sm">
                            <tbody>
                              {a.top_products.map(p => (
                                <tr key={p.product_id} className="border-t border-[#2a2f3e] last:border-0">
                                  <td className="px-5 py-2.5 text-slate-200 text-xs max-w-md truncate">
                                    {p.name}
                                    {p.sku && <span className="text-slate-600 ml-2 font-mono">{p.sku}</span>}
                                  </td>
                                  <td className="px-5 py-2.5 text-right text-slate-400 text-xs whitespace-nowrap">{p.units} un.</td>
                                  <td className="px-5 py-2.5 text-right text-emerald-400 font-semibold whitespace-nowrap">{fmt(p.revenue)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        ) : lens === 'campaign' ? (
          <div className="space-y-2">
            {filteredCampaigns.length === 0 ? (
              <p className="text-slate-500 text-sm text-center py-8">
                {showNamedOnly ? 'Nenhuma campanha identificada no período' : 'Sem dados no período'}
              </p>
            ) : filteredCampaigns.map(c => {
              const key = `${c.source}|${c.medium}|${c.campaign}`
              const open = expanded.has(key)
              const raw  = isRawUTM(c.campaign)
              return (
                <div key={key} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
                  <button
                    onClick={() => toggle(key)}
                    className="w-full px-5 py-3.5 flex items-center gap-4 hover:bg-[#252a3a] transition-colors text-left"
                  >
                    {open ? <ChevronDown size={14} className="text-slate-500 shrink-0" /> : <ChevronRight size={14} className="text-slate-500 shrink-0" />}
                    <div className={`px-2 py-0.5 rounded text-xs font-medium border ${badge(c.platform)} shrink-0`}>
                      {c.platform}
                    </div>
                    <div className="flex-1 min-w-0">
                      {raw ? (
                        <>
                          <p className="text-sm text-amber-300/80 font-medium truncate flex items-center gap-1.5">
                            <AlertTriangle size={12} className="text-amber-500 shrink-0" />
                            <span className="font-mono text-xs">{c.campaign}</span>
                          </p>
                          <p className="text-xs text-slate-500 truncate">{c.source} · {c.medium} · ID não resolvido</p>
                        </>
                      ) : (
                        <>
                          <p className="text-sm text-white font-medium truncate">{c.campaign}</p>
                          <p className="text-xs text-slate-500 truncate">
                            {c.source} · {c.medium}
                            {c.campaign_id && (
                              <span className="font-mono ml-2 text-slate-600">{c.campaign_id}</span>
                            )}
                          </p>
                        </>
                      )}
                    </div>
                    <div className="text-right shrink-0">
                      <p className="text-sm font-bold text-emerald-400">{fmt(c.revenue)}</p>
                      <div className="flex items-center gap-2 justify-end">
                        <p className="text-xs text-slate-500">{c.orders} pedidos · {c.units} un.</p>
                        {c.ltv_uplift_pct != null && c.ltv_uplift_pct > 0 && (
                          <span
                            className="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-300 border border-violet-500/30"
                            title={`Receita projetada (LTV): ${fmt(c.revenue_ltv)} — quanto Meta/Google deveriam pagar por estes clientes`}
                          >
                            +{c.ltv_uplift_pct.toFixed(0)}% LTV
                          </span>
                        )}
                      </div>
                    </div>
                  </button>

                  {open && (
                    <div className="border-t border-[#2a2f3e] bg-[#0f1117]">
                      <div className="px-5 py-3 grid grid-cols-5 gap-4">
                        <Mini label="Pedidos"      value={c.orders.toString()} />
                        <Mini label="Receita"      value={fmt(c.revenue)} accent="emerald" />
                        <Mini
                          label="Receita LTV"
                          value={fmt(c.revenue_ltv)}
                          accent="violet"
                        />
                        <Mini label="Ticket médio" value={fmt(c.avg_ticket)} />
                        <Mini label="Margem"       value={c.profit != null ? fmt(c.profit) : '—'} accent="teal" />
                      </div>
                      <div className="border-t border-[#2a2f3e]">
                        <p className="px-5 pt-3 text-xs uppercase tracking-wider text-slate-500 font-medium">
                          Produtos vendidos por essa campanha
                        </p>
                        <table className="w-full text-sm">
                          <tbody>
                            {c.top_products.map(p => (
                              <tr key={p.product_id} className="border-t border-[#2a2f3e] last:border-0">
                                <td className="px-5 py-2.5 text-slate-200 text-xs max-w-md truncate">
                                  {p.name}
                                  {p.sku && <span className="text-slate-600 ml-2 font-mono">{p.sku}</span>}
                                </td>
                                <td className="px-5 py-2.5 text-right text-slate-400 text-xs whitespace-nowrap">{p.units} un.</td>
                                <td className="px-5 py-2.5 text-right text-emerald-400 font-semibold whitespace-nowrap">{fmt(p.revenue)}</td>
                                <td className="px-5 py-2.5 text-right text-teal-400 font-medium text-xs whitespace-nowrap">
                                  {p.profit != null ? fmt(p.profit) : <span className="text-slate-600">—</span>}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        ) : (
          <div className="space-y-2">
            {filteredProducts.length === 0 ? (
              <p className="text-slate-500 text-sm text-center py-8">Sem dados no período</p>
            ) : filteredProducts.map(p => {
              const open = expanded.has(p.product_id)
              return (
                <div key={p.product_id} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
                  <button
                    onClick={() => toggle(p.product_id)}
                    className="w-full px-5 py-3.5 flex items-center gap-4 hover:bg-[#252a3a] transition-colors text-left"
                  >
                    {open ? <ChevronDown size={14} className="text-slate-500 shrink-0" /> : <ChevronRight size={14} className="text-slate-500 shrink-0" />}
                    <Package size={14} className="text-slate-500 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-white font-medium truncate">{p.name}</p>
                      <p className="text-xs text-slate-500">
                        {p.sku && <span className="font-mono">{p.sku}</span>}
                        {p.sku && ' · '}
                        {p.units} un. em {p.orders} pedido{p.orders === 1 ? '' : 's'}
                      </p>
                    </div>
                    <div className="text-right shrink-0">
                      <p className="text-sm font-bold text-emerald-400">{fmt(p.revenue)}</p>
                      {p.profit != null && (
                        <p className="text-xs text-teal-400">margem {fmt(p.profit)}</p>
                      )}
                    </div>
                  </button>

                  {open && (
                    <div className="border-t border-[#2a2f3e] bg-[#0f1117]">
                      <p className="px-5 pt-3 text-xs uppercase tracking-wider text-slate-500 font-medium">
                        Campanhas que trouxeram esse produto
                      </p>
                      <table className="w-full text-sm">
                        <tbody>
                          {p.top_campaigns.map((c, i) => {
                            const raw = isRawUTM(c.campaign)
                            return (
                              <tr key={`${p.product_id}-${i}`} className="border-t border-[#2a2f3e] last:border-0">
                                <td className="px-5 py-2.5">
                                  <div className={`inline-block px-2 py-0.5 rounded text-xs font-medium border ${badge(c.platform)}`}>
                                    {c.platform}
                                  </div>
                                </td>
                                <td className="px-5 py-2.5 text-xs max-w-md truncate">
                                  {raw ? (
                                    <span className="text-amber-300/70 font-mono">{c.campaign}</span>
                                  ) : (
                                    <span className="text-slate-200">{c.campaign}</span>
                                  )}
                                  <span className="text-slate-600 ml-2">{c.source}</span>
                                </td>
                                <td className="px-5 py-2.5 text-right text-slate-400 text-xs whitespace-nowrap">{c.units} un.</td>
                                <td className="px-5 py-2.5 text-right text-slate-400 text-xs whitespace-nowrap">{c.orders} pedidos</td>
                                <td className="px-5 py-2.5 text-right text-emerald-400 font-semibold whitespace-nowrap">{fmt(c.revenue)}</td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function Mini({ label, value, accent }: { label: string; value: string; accent?: 'emerald' | 'teal' | 'violet' }) {
  return (
    <div>
      <p className="text-xs text-slate-500">{label}</p>
      <p className={`text-sm font-bold mt-0.5 ${
        accent === 'emerald' ? 'text-emerald-400' :
        accent === 'teal'    ? 'text-teal-400'    :
        accent === 'violet'  ? 'text-violet-400'  : 'text-white'
      }`}>{value}</p>
    </div>
  )
}
