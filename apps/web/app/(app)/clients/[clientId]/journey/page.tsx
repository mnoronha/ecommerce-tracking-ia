'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { Loader2, ChevronDown, ChevronRight, Package, Megaphone, RefreshCw } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

type DateRange = 7 | 30 | 90
type Lens = 'campaign' | 'product'

interface ProductInCampaign {
  product_id: string
  name:       string
  sku:        string | null
  units:      number
  revenue:    number
  profit:     number | null
}
interface CampaignRow {
  source:        string
  medium:        string
  campaign:      string
  campaign_id:   string | null
  platform:      string
  orders:        number
  revenue:       number
  profit:        number | null
  units:         number
  avg_ticket:    number
  top_products:  ProductInCampaign[]
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
  const [loading,  setLoading]  = useState(true)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [search,   setSearch]   = useState('')
  const [resolving, setResolving] = useState(false)
  const [resolveMsg, setResolveMsg] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      if (lens === 'campaign') {
        const res = await fetch(`${API_URL}/journey/${pixelId}/by-campaign?days=${days}&top_products=10`)
        if (res.ok) setCampaigns((await res.json()).campaigns || [])
      } else {
        const res = await fetch(`${API_URL}/journey/${pixelId}/by-product?days=${days}&top_campaigns=10`)
        if (res.ok) setProducts((await res.json()).products || [])
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

  const totalRevenue = lens === 'campaign'
    ? campaigns.reduce((s, c) => s + c.revenue, 0)
    : products.reduce((s, p) => s + p.revenue, 0)

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
          </div>
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
          {resolveMsg && (
            <span className="text-xs text-slate-400">{resolveMsg}</span>
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
                      <p className="text-xs text-slate-500">{c.orders} pedidos · {c.units} un.</p>
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

function Mini({ label, value, accent }: { label: string; value: string; accent?: 'emerald' | 'teal' }) {
  return (
    <div>
      <p className="text-xs text-slate-500">{label}</p>
      <p className={`text-sm font-bold mt-0.5 ${
        accent === 'emerald' ? 'text-emerald-400' :
        accent === 'teal'    ? 'text-teal-400'    : 'text-white'
      }`}>{value}</p>
    </div>
  )
}
