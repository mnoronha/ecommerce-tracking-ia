'use client'

import { Info } from 'lucide-react'
import type { ReactNode } from 'react'

// ── Source badge ───────────────────────────────────────────────────────────────

type SourceKey = 'shopify' | 'meta' | 'google' | 'tiktok' | 'pinterest' | 'server' | 'ga4'

const SOURCE_CONFIG: Record<SourceKey, { label: string; color: string }> = {
  shopify:   { label: 'Shopify (servidor)',    color: 'text-emerald-400/70 bg-emerald-500/8 border-emerald-500/15' },
  meta:      { label: 'Meta Marketing API',    color: 'text-blue-400/70 bg-blue-500/8 border-blue-500/15' },
  google:    { label: 'Google Ads API',        color: 'text-red-400/70 bg-red-500/8 border-red-500/15' },
  tiktok:    { label: 'TikTok Ads API',        color: 'text-slate-400/70 bg-slate-500/8 border-slate-500/15' },
  pinterest: { label: 'Pinterest Ads API',     color: 'text-rose-400/70 bg-rose-500/8 border-rose-500/15' },
  server:    { label: 'Servidor próprio (CAPI)', color: 'text-indigo-400/70 bg-indigo-500/8 border-indigo-500/15' },
  ga4:       { label: 'Google Analytics 4',   color: 'text-orange-400/70 bg-orange-500/8 border-orange-500/15' },
}

export function SourceBadge({ source }: { source: SourceKey }) {
  const cfg = SOURCE_CONFIG[source]
  return (
    <span className={`inline-flex items-center text-[10px] font-medium px-1.5 py-0.5 rounded border ${cfg.color}`}>
      {cfg.label}
    </span>
  )
}

// ── Inline (i) tooltip ─────────────────────────────────────────────────────────

interface MetricTooltipProps {
  children: ReactNode
  tooltip: string
  source?: SourceKey
  size?: 'sm' | 'xs'
}

export function MetricTooltip({ children, tooltip, source, size = 'sm' }: MetricTooltipProps) {
  const iconSize = size === 'xs' ? 10 : 12
  return (
    <span className="inline-flex items-center gap-1">
      {children}
      <span
        className="inline-flex items-center cursor-help text-slate-600 hover:text-slate-400 transition-colors shrink-0"
        title={tooltip}
      >
        <Info size={iconSize} />
      </span>
      {source && <SourceBadge source={source} />}
    </span>
  )
}

// ── Table column header with optional (i) ─────────────────────────────────────

interface ColHeaderProps {
  label: string
  tooltip?: string
  right?: boolean
}

export function ColHeader({ label, tooltip, right = true }: ColHeaderProps) {
  if (!tooltip) return <span>{label}</span>
  return (
    <span className={`inline-flex items-center gap-1 ${right ? 'justify-end' : 'justify-start'} w-full`}>
      {!right && label}
      <span
        className="cursor-help text-slate-600 hover:text-slate-400 transition-colors shrink-0"
        title={tooltip}
      >
        <Info size={10} />
      </span>
      {right && label}
    </span>
  )
}

// ── Reconciliation box ─────────────────────────────────────────────────────────

interface ReconciliationProps {
  platformLabel: string
  platformRevenue: number | null
  serverRevenue: number | null
  platformOrders: number | null
  serverOrders: number | null
  windowNote: string
  fmt: (v: number) => string
}

export function ReconciliationBox({
  platformLabel, platformRevenue, serverRevenue,
  platformOrders, serverOrders, windowNote, fmt,
}: ReconciliationProps) {
  const diff = (platformRevenue ?? 0) - (serverRevenue ?? 0)
  const hasDiff = platformRevenue !== null && serverRevenue !== null && Math.abs(diff) > 1

  return (
    <details className="group bg-[#0f1117] border border-[#2a2f3e] rounded-xl overflow-hidden">
      <summary className="flex items-center justify-between px-4 py-3 cursor-pointer select-none list-none">
        <span className="flex items-center gap-2 text-xs font-medium text-slate-400">
          <Info size={13} className="text-indigo-400 shrink-0" />
          Por que {platformLabel} difere da receita real?
        </span>
        <span className="text-[10px] text-slate-600 group-open:hidden">▼ expandir</span>
        <span className="text-[10px] text-slate-600 hidden group-open:inline">▲ recolher</span>
      </summary>

      <div className="px-4 pb-4 space-y-3 border-t border-[#2a2f3e] pt-3">
        {/* Comparison table */}
        <div className="grid grid-cols-3 gap-2 text-xs">
          <div />
          <div className="text-right text-slate-500 font-medium">Receita</div>
          <div className="text-right text-slate-500 font-medium">Pedidos / Conv.</div>

          <div className="text-slate-400">📊 {platformLabel}</div>
          <div className="text-right text-slate-200 tabular-nums font-medium">
            {platformRevenue !== null ? fmt(platformRevenue) : '—'}
          </div>
          <div className="text-right text-slate-400 tabular-nums">
            {platformOrders !== null ? platformOrders : '—'}
          </div>

          <div className="text-slate-400">🛒 Server-side (UTM)</div>
          <div className="text-right text-slate-200 tabular-nums font-medium">
            {serverRevenue !== null ? fmt(serverRevenue) : '—'}
          </div>
          <div className="text-right text-slate-400 tabular-nums">
            {serverOrders !== null ? serverOrders : '—'}
          </div>

          {hasDiff && (
            <>
              <div className="text-slate-500">Diferença</div>
              <div className={`text-right tabular-nums font-medium ${diff > 0 ? 'text-amber-400' : 'text-red-400'}`}>
                {diff > 0 ? '+' : ''}{fmt(diff)}
              </div>
              <div />
            </>
          )}
        </div>

        {/* Explanation */}
        <div className="space-y-2 text-xs text-slate-500 leading-relaxed">
          <p>
            <span className="text-slate-300 font-medium">📊 {platformLabel}</span> reporta usando
            janela de atribuição própria{windowNote ? ` (${windowNote})` : ''}, incluindo
            view-through e re-atribuição de cliques anteriores.
          </p>
          <p>
            <span className="text-slate-300 font-medium">🛒 Server-side</span> captura apenas
            pedidos com <code className="text-indigo-300 bg-indigo-500/10 px-1 rounded">utm_source</code> correspondente
            ao canal no momento do checkout — atribuição last-click ground truth.
          </p>
          {hasDiff && (
            <p className="text-amber-400/80">
              A diferença de <span className="font-medium">{fmt(Math.abs(diff))}</span> são compras
              que {platformLabel} atribui mas que chegaram ao checkout via outro canal ou sem UTM.
            </p>
          )}
          <div className="bg-[#1a1f2e] rounded-lg px-3 py-2 mt-2">
            <p className="text-slate-400 font-medium mb-1">Qual usar?</p>
            <p>• <span className="text-slate-300">Benchmark vs concorrentes:</span> use {platformLabel} (padrão da indústria)</p>
            <p>• <span className="text-slate-300">ROI real / decisão de budget:</span> use server-side (ground truth)</p>
            <p>• <span className="text-slate-300">ROAS calculado nesta tela:</span> usa {platformLabel} (comparável ao Looker)</p>
          </div>
        </div>
      </div>
    </details>
  )
}
