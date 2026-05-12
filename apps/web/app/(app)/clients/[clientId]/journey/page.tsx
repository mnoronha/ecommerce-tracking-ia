'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { Loader2, ChevronDown, ChevronRight, Package, Megaphone, RefreshCw, Target, Sparkles } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

type DateRange = 7 | 30 | 90
type Lens = 'campaign' | 'product' | 'meta-attribution' | 'declared-source'

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

export default function JourneyPage() {
  const params  = useParams()
  const pixelId = params.clientId as string

  const [lens,    setLens]    = useState<Lens>('campaign')
  const [days,    setDays]    = useState<DateRange>(30)
  const [campaigns, setCampaigns] = useState<CampaignRow[]>([])
  const [products,  setProducts]  = useState<ProductRow[]>([])
  const [metaAttr, setMetaAttr] = useState<MetaAttrRow[]>([])
  const [metaTotals, setMetaTotals] = useState<MetaAttrTotals | null>(null)
  const [declared, setDeclared] = useState<DeclaredSourceRow[]>([])
  const [declaredTotal, setDeclaredTotal] = useState(0)
  const [loading,  setLoading]  = useState(true)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [search,   setSearch]   = useState('')
  const [resolving, setResolving] = useState(false)
  const [resolveMsg, setResolveMsg] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [matching, setMatching] = useState(false)
  const [actionMsg, setActionMsg] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      if (lens === 'campaign') {
        const res = await fetch(`${API_URL}/journey/${pixelId}/by-campaign?days=${days}&top_products=10`)
        if (res.ok) setCampaigns((await res.json()).campaigns || [])
      } else if (lens === 'product') {
        const res = await fetch(`${API_URL}/journey/${pixelId}/by-product?days=${days}&top_campaigns=10`)
        if (res.ok) setProducts((await res.json()).products || [])
      } else if (lens === 'meta-attribution') {
        const res = await fetch(`${API_URL}/journey/${pixelId}/by-meta-attribution?days=${days}`)
        if (res.ok) {
          const data = await res.json()
          setMetaAttr(data.campaigns || [])
          setMetaTotals(data.totals || null)
        }
      } else {
        const res = await fetch(`${API_URL}/journey/${pixelId}/by-declared-source?days=${days}`)
        if (res.ok) {
          const data = await res.json()
          setDeclared(data.by_source || [])
          setDeclaredTotal(data.total_responses || 0)
        }
      }
    } finally {
      setLoading(false)
    }
  }, [lens, days, pixelId])

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

  const filteredCampaigns = search
    ? campaigns.filter(c =>
        c.campaign.toLowerCase().includes(search.toLowerCase()) ||
        c.source.toLowerCase().includes(search.toLowerCase()))
    : campaigns
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
    : lens === 'product'
      ? products.reduce((s, p) => s + p.revenue, 0)
      : lens === 'declared-source'
        ? declared.reduce((s, d) => s + d.declared_revenue, 0)
        : metaTotals?.meta_revenue || 0

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white">Jornada — Campanha × Produto</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Quem comprou o quê veio de onde
          </p>
        </div>
        <div className="flex items-center gap-3">
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={lens === 'campaign' ? 'Buscar campanha…' : 'Buscar produto…'}
            className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder-slate-600 outline-none focus:border-indigo-500 w-48"
          />
          <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
            {([7, 30, 90] as DateRange[]).map(r => (
              <button key={r} onClick={() => setDays(r)}
                className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                  days === r ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
                }`}>
                {r}d
              </button>
            ))}
          </div>
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
          </div>
          {lens === 'campaign' || lens === 'product' ? (
            <button
              onClick={handleResolveMeta}
              disabled={resolving}
              title="Busca os nomes reais das campanhas no Meta Ads quando elas aparecem como ID numérico"
              className="flex items-center gap-2 text-xs bg-[#1a1f2e] hover:bg-[#252a3a] border border-[#2a2f3e] text-slate-300 px-3 py-2 rounded-lg transition-colors"
            >
              {resolving
                ? <><Loader2 size={12} className="animate-spin" />Sincronizando...</>
                : <><RefreshCw size={12} />Resolver nomes Meta</>}
            </button>
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
        <p className="text-xs text-slate-500 mt-3">
          Receita total no período: <span className="text-emerald-400 font-semibold">{fmt(totalRevenue)}</span>
        </p>
      </div>

      {/* Content */}
      <div className="p-6">
        {loading ? (
          <div className="flex items-center gap-2 text-slate-500 text-sm py-12 justify-center">
            <Loader2 size={16} className="animate-spin" /> Carregando jornadas...
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
                        <th className="px-5 py-2.5 text-right font-medium">Miss UTM (rescate)</th>
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
        ) : lens === 'campaign' ? (
          <div className="space-y-2">
            {filteredCampaigns.length === 0 ? (
              <p className="text-slate-500 text-sm text-center py-8">Sem dados no período</p>
            ) : filteredCampaigns.map(c => {
              const key = `${c.source}|${c.medium}|${c.campaign}`
              const open = expanded.has(key)
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
                      <p className="text-sm text-white font-medium truncate">{c.campaign}</p>
                      <p className="text-xs text-slate-500 truncate">{c.source} · {c.medium}</p>
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
                          {p.top_campaigns.map((c, i) => (
                            <tr key={`${p.product_id}-${i}`} className="border-t border-[#2a2f3e] last:border-0">
                              <td className="px-5 py-2.5">
                                <div className={`inline-block px-2 py-0.5 rounded text-xs font-medium border ${badge(c.platform)}`}>
                                  {c.platform}
                                </div>
                              </td>
                              <td className="px-5 py-2.5 text-slate-200 text-xs max-w-md truncate">
                                {c.campaign}
                                <span className="text-slate-600 ml-2">{c.source}</span>
                              </td>
                              <td className="px-5 py-2.5 text-right text-slate-400 text-xs whitespace-nowrap">{c.units} un.</td>
                              <td className="px-5 py-2.5 text-right text-slate-400 text-xs whitespace-nowrap">{c.orders} pedidos</td>
                              <td className="px-5 py-2.5 text-right text-emerald-400 font-semibold whitespace-nowrap">{fmt(c.revenue)}</td>
                            </tr>
                          ))}
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
