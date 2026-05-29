'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { Loader2, RefreshCw } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

type DatePreset = '7d' | '30d' | '90d'

interface RoasCampaign {
  campaign_name:  string
  utm_source:     string | null
  spend:          number
  revenue:        number
  gross_profit:   number | null
  margin_pct:     number | null
  margin_roas:    number | null
  orders:         number
  roas:           number | null
  cpa:            number | null
  impressions:    number
  clicks:         number
  ctr:            number | null
  cpm:            number | null
  meta_purchases: number
  meta_revenue:   number
  meta_cpa:       number | null
  meta_roas:      number | null
  cpa_diff_pct:   number | null
  purchases_diff: number
}

interface RoasData {
  has_ads_credentials: boolean
  has_cogs:   boolean
  days:       number
  campaigns:  RoasCampaign[]
  totals: {
    spend:           number
    revenue:         number
    gross_profit:    number | null
    margin_pct:      number | null
    margin_roas:     number | null
    orders:          number
    roas:            number | null
    total_cpa:       number | null
    meta_purchases:  number
    meta_revenue:    number
    meta_cpa:        number | null
    meta_roas:       number | null
    cpa_diff_pct:    number | null
  }
  paid_only?: {
    revenue:      number
    orders:       number
    spend:        number
    roas:         number | null
    cpa:          number | null
    gross_profit: number | null
    margin_roas:  number | null
    campaigns:    number
  }
}

const fmt = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n)

function StatCard({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: 'emerald' | 'teal' | 'yellow' | 'red' }) {
  const color = accent === 'emerald' ? 'text-emerald-400' : accent === 'teal' ? 'text-teal-400' : accent === 'yellow' ? 'text-yellow-400' : accent === 'red' ? 'text-red-400' : 'text-white'
  return (
    <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl px-5 py-4 text-center">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className={`text-xl font-bold ${color}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
    </div>
  )
}

export default function MetaAdsPage() {
  const params  = useParams()
  const pixelId = params.clientId as string

  const [preset,  setPreset]  = useState<DatePreset>('30d')
  const [data,    setData]    = useState<RoasData | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async (p: DatePreset) => {
    setLoading(true)
    try {
      const days = p === '7d' ? 7 : p === '30d' ? 30 : 90
      const res  = await fetch(`${API_URL}/meta-ads/${pixelId}/roas?days=${days}`)
      if (res.ok) setData(await res.json())
    } catch (_) {}
    setLoading(false)
  }, [pixelId])

  useEffect(() => { load(preset) }, [preset, load])

  const visibleCampaigns = data?.has_ads_credentials
    ? (data.campaigns || []).filter(c => c.impressions > 0 || c.spend > 0)
    : (data?.campaigns || [])

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white">Meta Ads — ROAS por Campanha</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Gasto Meta Ads × receita server-side — comparação real vs reportado
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
            {(['7d', '30d', '90d'] as DatePreset[]).map(p => (
              <button key={p} onClick={() => setPreset(p)}
                className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                  preset === p ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
                }`}>
                {p}
              </button>
            ))}
          </div>
          <button onClick={() => load(preset)} className="text-slate-500 hover:text-white transition-colors">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      <div className="p-6 space-y-6 max-w-7xl">

        {/* No credentials warning */}
        {data && !data.has_ads_credentials && (
          <div className="bg-yellow-500/10 border border-yellow-500/20 rounded-xl p-4">
            <p className="text-sm font-medium text-yellow-400">Conta Meta Ads não configurada</p>
            <p className="text-xs text-slate-400 mt-1">
              Configure <code className="bg-yellow-500/10 px-1 rounded">meta_ad_account_id</code> nas configurações do cliente para ver gasto e ROAS.
            </p>
          </div>
        )}

        {/* Token warning — credentials set but no spend */}
        {data?.has_ads_credentials && data.totals.spend === 0 && !loading && (
          <div className="bg-orange-500/10 border border-orange-500/20 rounded-xl p-4">
            <p className="text-xs text-orange-400">
              Sem dados de gasto no período — verifique se o token Meta Ads está válido em Configurações → Integrações.
            </p>
          </div>
        )}

        {/* KPI strip */}
        {data?.paid_only && data.has_ads_credentials && data.totals.spend > 0 && (
          <div className={`grid gap-3 ${data.has_cogs ? 'grid-cols-2 md:grid-cols-5' : 'grid-cols-2 md:grid-cols-4'}`}>
            <StatCard label="Gasto" value={fmt(data.paid_only.spend)} />
            <StatCard label="Receita atribuída" value={fmt(data.paid_only.revenue)}
              sub={data.totals.meta_revenue > 0 ? `Meta diz: ${fmt(data.totals.meta_revenue)}` : undefined}
              accent="emerald" />
            <StatCard label="ROAS pago" value={data.paid_only.roas != null ? `${data.paid_only.roas.toFixed(2)}x` : '—'}
              sub={data.totals.meta_roas != null ? `Meta diz: ${data.totals.meta_roas.toFixed(2)}x` : undefined}
              accent={data.paid_only.roas != null ? (data.paid_only.roas >= 3 ? 'emerald' : data.paid_only.roas >= 1.5 ? 'yellow' : 'red') : undefined} />
            <StatCard label="CPA real" value={data.paid_only.cpa != null ? fmt(data.paid_only.cpa) : '—'}
              sub={data.totals.meta_cpa != null ? `Meta diz: ${fmt(data.totals.meta_cpa)}` : undefined} />
            {data.has_cogs && data.paid_only.gross_profit != null && (
              <StatCard label="ROAS Margem"
                value={data.paid_only.margin_roas != null ? `${data.paid_only.margin_roas.toFixed(2)}x` : '—'}
                sub={data.totals.margin_pct != null ? `Margem: ${data.totals.margin_pct.toFixed(1)}%` : undefined}
                accent="teal" />
            )}
          </div>
        )}

        {/* CPA diff alert */}
        {data?.totals.cpa_diff_pct != null && Math.abs(data.totals.cpa_diff_pct) >= 5 && (
          <div className={`rounded-xl px-5 py-3 text-xs ${
            data.totals.cpa_diff_pct > 0
              ? 'bg-yellow-500/5 border border-yellow-500/20 text-yellow-300'
              : 'bg-emerald-500/5 border border-emerald-500/20 text-emerald-300'
          }`}>
            {data.totals.cpa_diff_pct > 0 ? (
              <>⚠ Meta está <strong>subestimando</strong> seu CPA em <strong>{data.totals.cpa_diff_pct.toFixed(0)}%</strong>.
              O CPA real é {Math.abs(data.totals.cpa_diff_pct).toFixed(0)}% maior do que o painel Meta mostra.</>
            ) : (
              <>✓ Meta está reportando CPA {Math.abs(data.totals.cpa_diff_pct).toFixed(0)}% acima do real
              ({data.totals.meta_purchases} compras Meta vs {data.totals.orders} server).</>
            )}
          </div>
        )}

        {/* Campaigns table */}
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-[#2a2f3e]">
            <h2 className="text-sm font-semibold text-slate-300">Campanhas</h2>
            {data && <p className="text-xs text-slate-500 mt-0.5">{visibleCampaigns.length} campanhas · últimos {preset}</p>}
          </div>

          {loading ? (
            <div className="flex items-center gap-2 p-8 text-slate-500 text-sm justify-center">
              <Loader2 size={16} className="animate-spin" /> Carregando…
            </div>
          ) : visibleCampaigns.length === 0 ? (
            <p className="p-8 text-slate-500 text-sm text-center">Nenhuma campanha com dados no período</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#2a2f3e]">
                    {[
                      'Campanha',
                      'Pedidos',
                      'Receita',
                      ...(data?.has_cogs ? ['Lucro Bruto', 'ROAS Margem'] : []),
                      ...(data?.has_ads_credentials ? ['Gasto', 'ROAS', 'CPA real', 'CPA Meta', 'Diff', 'Clicks', 'Impressões'] : []),
                    ].map(h => (
                      <th key={h} className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {visibleCampaigns.map((c, i) => (
                    <tr key={i} className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a] transition-colors">
                      <td className="px-4 py-3 max-w-[220px]">
                        <p className="text-slate-200 text-xs truncate font-medium">{c.campaign_name}</p>
                        {c.utm_source && (
                          <span className={`text-xs px-1.5 py-0.5 rounded mt-0.5 inline-block ${
                            ['facebook','instagram','meta'].includes(c.utm_source)
                              ? 'bg-blue-500/10 text-blue-400'
                              : c.utm_source === 'google' ? 'bg-red-500/10 text-red-400'
                              : 'bg-slate-500/10 text-slate-400'
                          }`}>{c.utm_source}</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-slate-200 font-medium tabular-nums">
                        {c.orders}
                        {c.purchases_diff !== 0 && c.meta_purchases > 0 && (
                          <span className="text-xs text-slate-500 ml-1">(Meta: {c.meta_purchases})</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-emerald-400 font-semibold whitespace-nowrap tabular-nums">{fmt(c.revenue)}</td>
                      {data?.has_cogs && (
                        <>
                          <td className="px-4 py-3 whitespace-nowrap tabular-nums">
                            {c.gross_profit != null
                              ? <span className="text-teal-400 font-medium">{fmt(c.gross_profit)}</span>
                              : <span className="text-slate-600">—</span>}
                            {c.margin_pct != null && <span className="text-xs text-slate-500 ml-1">{c.margin_pct.toFixed(0)}%</span>}
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap tabular-nums">
                            {c.margin_roas != null
                              ? <span className={`font-bold ${c.margin_roas >= 2 ? 'text-teal-400' : c.margin_roas >= 1 ? 'text-yellow-400' : 'text-red-400'}`}>{c.margin_roas.toFixed(2)}x</span>
                              : <span className="text-slate-600">—</span>}
                          </td>
                        </>
                      )}
                      {data?.has_ads_credentials && (
                        <>
                          <td className="px-4 py-3 text-slate-300 whitespace-nowrap tabular-nums">
                            {c.spend > 0 ? fmt(c.spend) : <span className="text-slate-600">—</span>}
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap tabular-nums">
                            {c.roas != null
                              ? <span className={`font-bold ${c.roas >= 3 ? 'text-emerald-400' : c.roas >= 1.5 ? 'text-yellow-400' : 'text-red-400'}`}>{c.roas.toFixed(2)}x</span>
                              : <span className="text-slate-600">—</span>}
                          </td>
                          <td className="px-4 py-3 text-slate-300 whitespace-nowrap tabular-nums">
                            {c.cpa != null ? fmt(c.cpa) : <span className="text-slate-600">—</span>}
                          </td>
                          <td className="px-4 py-3 text-slate-500 whitespace-nowrap tabular-nums text-xs">
                            {c.meta_cpa != null ? fmt(c.meta_cpa) : <span className="text-slate-600">—</span>}
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap tabular-nums">
                            {c.cpa_diff_pct != null
                              ? <span className={`text-xs font-medium ${Math.abs(c.cpa_diff_pct) < 10 ? 'text-slate-400' : c.cpa_diff_pct > 0 ? 'text-yellow-400' : 'text-emerald-400'}`}>
                                  {c.cpa_diff_pct > 0 ? '+' : ''}{c.cpa_diff_pct.toFixed(0)}%
                                </span>
                              : <span className="text-slate-600 text-xs">—</span>}
                          </td>
                          <td className="px-4 py-3 text-slate-400 tabular-nums">
                            {c.clicks > 0 ? c.clicks.toLocaleString('pt-BR') : <span className="text-slate-600">—</span>}
                          </td>
                          <td className="px-4 py-3 text-slate-500 tabular-nums text-xs">
                            {c.impressions > 0 ? c.impressions.toLocaleString('pt-BR') : <span className="text-slate-600">—</span>}
                          </td>
                        </>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Explanation */}
        {data?.has_ads_credentials && (
          <p className="text-xs text-slate-500 leading-relaxed">
            <span className="text-slate-400 font-medium">Como ler:</span> "Receita atribuída" são pedidos do nosso servidor com utm_campaign correspondente.
            "Meta diz" é o que o Meta Ads reporta internamente (janela de atribuição 7d clique / 1d view).
            Diff de CPA mostra o quanto o Meta está sub ou super-estimando seu custo real.
          </p>
        )}
      </div>
    </div>
  )
}
