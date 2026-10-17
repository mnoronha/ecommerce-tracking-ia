'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams }                         from 'next/navigation'
import Link                                  from 'next/link'
import { ArrowLeft, Target, RefreshCw, Loader2, Info, ShieldCheck } from 'lucide-react'
import { useDatePeriod, periodToRange, periodLabelLong } from '@/lib/use-date-range'
import { PeriodPicker }                      from '@/components/PeriodPicker'

// ── Types (GET /agency/v1/clients/:id/tracking/quality | /tracking/matches) ──

type MatchSource = 'cart_attributes' | 'pixel' | 'customer_history' | 'shopify_fallback' | 'none'

interface Quality {
  mode:                  string
  paid_orders:           number
  paid_revenue:          number
  excluded_orders:       number
  pending_orders:        number
  coverage_orders_pct:   number | null
  coverage_revenue_pct:  number | null
  shopify_paid_orders:   number | null
  shopify_paid_revenue:  number | null
  match_rate_pct:        number | null
  match_sources:         Record<MatchSource, number>
  google_click_rate_pct: number | null
  consent_advertising:   { granted: number; denied: number; unknown: number }
  ga4_ready_pct:         number | null
  ga4_in_session_pct:    number | null
  google_ads_ready_pct:  number | null
  google_ads_click_pct:  number | null
}

interface MatchRow {
  order_id:            string
  order_number:        string | null
  order_created_at:    string | null
  financial_status:    string | null
  total_price:         number | null
  excluded_reason:     string | null
  is_first_purchase:   boolean | null
  match_source:        MatchSource
  utm_source:          string | null
  utm_medium:          string | null
  utm_campaign:        string | null
  click_source:        string | null
  has_google_click:    boolean
  consent_advertising: boolean | null
  ga4_ready:           boolean
  ga4_in_session:      boolean
  google_ads_ready:    boolean
  google_ads_mode:     string | null
}

// ── Labels ────────────────────────────────────────────────────────────────────

const SOURCE_LABEL: Record<MatchSource, string> = {
  cart_attributes:  'Carrinho (cookie do tracker)',
  pixel:            'Pixel do checkout',
  customer_history: 'Histórico do cliente',
  shopify_fallback: 'Landing page (Shopify)',
  none:             'Sem origem identificada',
}

const SOURCE_ORDER: MatchSource[] = ['cart_attributes', 'pixel', 'customer_history', 'shopify_fallback', 'none']

const STATUS_LABEL: Record<string, string> = {
  paid:               'Pago',
  pending:            'Pendente',
  authorized:         'Autorizado',
  refunded:           'Estornado',
  partially_refunded: 'Estorno parcial',
  voided:             'Cancelado',
}

const EXCLUDED_LABEL: Record<string, string> = {
  offline:    'Venda offline',
  zero_value: 'Valor zero',
  test:       'Pedido de teste',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const brl = (v: number | null | undefined) =>
  v == null ? '—' : v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })

const pct = (v: number | null | undefined) =>
  v == null ? '—' : `${v.toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%`

function ymd(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    timeZone: 'America/Sao_Paulo',
  })
}

/** Color by threshold for the headline KPIs (always paired with the number). */
function tone(v: number | null, good: number, ok: number) {
  if (v == null) return 'text-slate-400'
  if (v >= good) return 'text-emerald-400'
  if (v >= ok)   return 'text-yellow-400'
  return 'text-red-400'
}

// ── Small components ──────────────────────────────────────────────────────────

function Kpi({ label, value, sub, valueClass = 'text-white', hint }: {
  label: string; value: string; sub?: string; valueClass?: string; hint?: string
}) {
  return (
    <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5" title={hint}>
      <p className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">{label}</p>
      <p className={`text-2xl font-bold tabular-nums ${valueClass}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
    </div>
  )
}

function Check({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded ${
      ok ? 'bg-emerald-500/10 text-emerald-400' : 'bg-slate-500/10 text-slate-500'
    }`}>
      {ok ? '✓' : '–'} {label}
    </span>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function TrackingQualityPage() {
  const params   = useParams()
  const clientId = params.clientId as string
  const { period, from, to, setPreset, setCustom } = useDatePeriod()

  const [quality, setQuality] = useState<Quality | null>(null)
  const [rows,    setRows]    = useState<MatchRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    const { start, end } = periodToRange(period, from, to)
    const qs = `date_from=${ymd(start)}&date_to=${ymd(end)}`
    try {
      const [qRes, mRes] = await Promise.all([
        fetch(`/api/v1/clients/${clientId}/tracking/quality?${qs}`, { cache: 'no-store' }),
        fetch(`/api/v1/clients/${clientId}/tracking/matches?${qs}&limit=50`, { cache: 'no-store' }),
      ])
      if (!qRes.ok) throw new Error(`HTTP ${qRes.status}`)
      setQuality(await qRes.json())
      if (mRes.ok) {
        const body = await mRes.json()
        setRows(Array.isArray(body?.items) ? body.items : [])
      } else {
        setRows([])
      }
    } catch {
      setError('Não foi possível carregar os dados de tracking.')
      setQuality(null)
      setRows([])
    } finally {
      setLoading(false)
    }
  }, [clientId, period, from, to])

  useEffect(() => { load() }, [load])

  const sourcesTotal = quality
    ? SOURCE_ORDER.reduce((acc, s) => acc + (quality.match_sources?.[s] ?? 0), 0)
    : 0
  const consentTotal = quality
    ? quality.consent_advertising.granted + quality.consent_advertising.denied + quality.consent_advertising.unknown
    : 0

  return (
    <div className="p-8 max-w-6xl">
      {/* Header */}
      <div className="mb-6 flex items-start justify-between gap-4 flex-wrap">
        <div>
          <Link href={`/clients/${clientId}/dashboard`} className="inline-flex items-center gap-1.5 text-slate-500 hover:text-white text-xs mb-3 transition-colors">
            <ArrowLeft size={12} /> Voltar
          </Link>
          <div className="flex items-center gap-2">
            <Target size={16} className="text-indigo-400" />
            <h1 className="text-xl font-bold text-white">Qualidade do Tracking</h1>
          </div>
          <p className="text-xs text-slate-500 mt-1">
            Vendas pagas do Shopify ligadas à navegação · {periodLabelLong(period, from, to)}
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

      {/* Shadow-mode notice */}
      <div className="mb-6 flex items-start gap-2 bg-indigo-500/5 border border-indigo-500/20 rounded-lg px-4 py-3">
        <ShieldCheck size={14} className="text-indigo-400 mt-0.5 shrink-0" />
        <p className="text-xs text-slate-400">
          <span className="text-slate-200 font-medium">Modo só-tracking.</span>{' '}
          Nenhuma conversão é enviada ao Google Ads, GA4 ou Meta. As colunas &ldquo;pronto para&rdquo; mostram o que
          seria enviado quando o envio for liberado.
        </p>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-slate-500 text-sm py-16 justify-center">
          <Loader2 size={16} className="animate-spin" /> Carregando…
        </div>
      )}

      {!loading && error && (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-8 text-center text-sm text-slate-400">
          {error}
        </div>
      )}

      {!loading && !error && quality && quality.paid_orders === 0 && rows.length === 0 && (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-8 text-center">
          <p className="text-sm text-slate-300 mb-1">Nenhum pedido no período.</p>
          <p className="text-xs text-slate-500">
            Os pedidos aparecem aqui assim que os webhooks do Shopify chegam ao Noro.
          </p>
        </div>
      )}

      {!loading && !error && quality && (quality.paid_orders > 0 || rows.length > 0) && (
        <>
          {/* KPIs */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            <Kpi
              label="Vendas pagas"
              value={quality.paid_orders.toLocaleString('pt-BR')}
              sub={`${brl(quality.paid_revenue)} · cobertura ${pct(quality.coverage_orders_pct)}`}
              hint="Pedidos pagos no Noro comparados com os pedidos pagos registrados do Shopify"
            />
            <Kpi
              label="Origem identificada"
              value={pct(quality.match_rate_pct)}
              valueClass={tone(quality.match_rate_pct, 85, 70)}
              sub="meta: acima de 70%"
              hint="Pedidos pagos ligados a uma visita (carrinho, pixel, histórico ou landing page)"
            />
            <Kpi
              label="Com clique do Google"
              value={pct(quality.google_click_rate_pct)}
              sub="gclid, gbraid ou wbraid"
              hint="Pedidos pagos com identificador de clique do Google (só com consentimento)"
            />
            <Kpi
              label="Pronto para o GA4"
              value={pct(quality.ga4_ready_pct)}
              sub={`na sessão original: ${pct(quality.ga4_in_session_pct)}`}
              hint="Pedidos pagos com client_id do GA4; 'na sessão' também tem o session_id"
            />
          </div>

          <div className="grid lg:grid-cols-2 gap-4 mb-6">
            {/* Match sources */}
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
              <h2 className="text-sm font-semibold text-white mb-1">Como cada venda foi ligada</h2>
              <p className="text-xs text-slate-500 mb-4">Vendas pagas, na ordem de confiança</p>
              <div className="space-y-3">
                {SOURCE_ORDER.map(s => {
                  const n     = quality.match_sources?.[s] ?? 0
                  const share = sourcesTotal ? (100 * n) / sourcesTotal : 0
                  return (
                    <div key={s} title={`${SOURCE_LABEL[s]}: ${n} (${pct(share)})`}>
                      <div className="flex justify-between text-xs mb-1">
                        <span className={s === 'none' ? 'text-slate-500' : 'text-slate-300'}>{SOURCE_LABEL[s]}</span>
                        <span className="text-slate-400 tabular-nums">{n} · {pct(share)}</span>
                      </div>
                      <div className="h-1.5 bg-[#0f1117] rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full ${s === 'none' ? 'bg-slate-600' : 'bg-indigo-500'}`}
                          style={{ width: `${share}%` }}
                        />
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Consent + Google Ads readiness */}
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
              <h2 className="text-sm font-semibold text-white mb-1">Consentimento e prontidão</h2>
              <p className="text-xs text-slate-500 mb-4">O que poderia ir para o Google Ads</p>
              <div className="space-y-2 text-xs">
                {([
                  ['Consentimento de marketing concedido', quality.consent_advertising.granted],
                  ['Consentimento negado',                quality.consent_advertising.denied],
                  ['Consentimento desconhecido',          quality.consent_advertising.unknown],
                ] as [string, number][]).map(([label, n]) => (
                  <div key={label} className="flex justify-between">
                    <span className="text-slate-400">{label}</span>
                    <span className="text-slate-300 tabular-nums">
                      {n} · {pct(consentTotal ? (100 * n) / consentTotal : null)}
                    </span>
                  </div>
                ))}
                <div className="border-t border-[#2a2f3e] my-3" />
                <div className="flex justify-between">
                  <span className="text-slate-400">Pronto para o Google Ads</span>
                  <span className="text-slate-300 tabular-nums">{pct(quality.google_ads_ready_pct)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">… ligado ao clique (gclid)</span>
                  <span className="text-slate-300 tabular-nums">{pct(quality.google_ads_click_pct)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Pendentes (PIX/boleto não pagos)</span>
                  <span className="text-slate-300 tabular-nums">{quality.pending_orders}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Excluídos (offline, teste, valor zero)</span>
                  <span className="text-slate-300 tabular-nums">{quality.excluded_orders}</span>
                </div>
              </div>
              <p className="flex items-start gap-1.5 text-[11px] text-slate-500 mt-4">
                <Info size={12} className="mt-0.5 shrink-0" />
                Sem clique, o envio usa Enhanced Conversions (email com hash do pedido).
              </p>
            </div>
          </div>

          {/* Orders table */}
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-[#2a2f3e]">
              <h2 className="text-sm font-semibold text-white">Pedidos recentes</h2>
              <p className="text-xs text-slate-500">Últimos 50 do período · sem dados pessoais</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-slate-500 text-left">
                  <tr className="border-b border-[#2a2f3e]">
                    <th className="px-5 py-2 font-medium">Pedido</th>
                    <th className="px-3 py-2 font-medium">Data</th>
                    <th className="px-3 py-2 font-medium">Status</th>
                    <th className="px-3 py-2 font-medium text-right">Valor</th>
                    <th className="px-3 py-2 font-medium">Ligação</th>
                    <th className="px-3 py-2 font-medium">Origem</th>
                    <th className="px-5 py-2 font-medium">Pronto para</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.order_id} className="border-b border-[#2a2f3e]/60 hover:bg-[#252b3b]">
                      <td className="px-5 py-2.5 text-slate-200 font-mono">
                        {r.order_number || r.order_id}
                        {r.is_first_purchase && <span className="ml-1.5 text-[10px] text-indigo-300">novo</span>}
                      </td>
                      <td className="px-3 py-2.5 text-slate-400 whitespace-nowrap">{fmtDate(r.order_created_at)}</td>
                      <td className="px-3 py-2.5 text-slate-300">
                        {r.excluded_reason
                          ? <span className="text-slate-500">{EXCLUDED_LABEL[r.excluded_reason] ?? r.excluded_reason}</span>
                          : (STATUS_LABEL[r.financial_status ?? ''] ?? r.financial_status ?? '—')}
                      </td>
                      <td className="px-3 py-2.5 text-slate-200 text-right tabular-nums">{brl(r.total_price)}</td>
                      <td className={`px-3 py-2.5 ${r.match_source === 'none' ? 'text-slate-500' : 'text-slate-300'}`}>
                        {SOURCE_LABEL[r.match_source] ?? r.match_source}
                      </td>
                      <td className="px-3 py-2.5 text-slate-400">
                        {r.utm_source
                          ? `${r.utm_source}${r.utm_medium ? ` / ${r.utm_medium}` : ''}`
                          : (r.click_source ?? '—')}
                        {r.utm_campaign && <div className="text-[10px] text-slate-500 truncate max-w-[180px]">{r.utm_campaign}</div>}
                      </td>
                      <td className="px-5 py-2.5">
                        {r.financial_status === 'paid' && !r.excluded_reason ? (
                          <div className="flex gap-1 flex-wrap">
                            <Check ok={r.ga4_in_session} label="GA4" />
                            <Check ok={r.google_ads_ready} label={r.google_ads_mode === 'click' ? 'Ads · clique' : 'Ads'} />
                          </div>
                        ) : <span className="text-slate-600">—</span>}
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr><td colSpan={7} className="px-5 py-6 text-center text-slate-500">Sem pedidos no período.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
