'use client'

import type { OutlierResult } from '@/lib/outlier-detection'

// ── Badge ─────────────────────────────────────────────────────────────────────

const BADGE_STYLE: Record<string, Record<string, string>> = {
  positive: {
    extreme: 'bg-amber-400/15 text-amber-300 border border-amber-400/30',
    high:    'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30',
  },
  negative: {
    extreme: 'bg-red-500/15 text-red-300 border border-red-500/30',
    high:    'bg-orange-500/15 text-orange-300 border border-orange-500/30',
  },
}

const BADGE_LABEL: Record<string, Record<string, string>> = {
  positive: { extreme: '🚀 Escalar', high: '↑ Top' },
  negative: { extreme: '⚠ Revisar', high: '↓ Baixo' },
}

type OutlierBadgeProps = { outlier: OutlierResult; tooltip?: string }

export function OutlierBadge({ outlier, tooltip }: OutlierBadgeProps) {
  if (!outlier.isOutlier || !outlier.direction || !outlier.magnitude) return null
  const style = BADGE_STYLE[outlier.direction]?.[outlier.magnitude] ?? ''
  const label = BADGE_LABEL[outlier.direction]?.[outlier.magnitude] ?? ''
  return (
    <span
      className={`inline-flex items-center text-[10px] font-bold px-1.5 py-0.5 rounded whitespace-nowrap ${style}`}
      title={tooltip}
      aria-label={tooltip || label}
    >
      {label}
    </span>
  )
}

// ── Border helpers ────────────────────────────────────────────────────────────

export function outlierCardBorder(direction: string | null, magnitude: string | null): string {
  if (direction === 'positive' && magnitude === 'extreme') return 'border border-amber-400/50 shadow-lg shadow-amber-500/10'
  if (direction === 'positive' && magnitude === 'high')    return 'border border-emerald-500/40'
  if (direction === 'negative' && magnitude === 'extreme') return 'border border-red-500/50'
  if (direction === 'negative' && magnitude === 'high')    return 'border border-orange-500/40'
  return 'border border-[#2a2f3e]'
}

// Left border for table rows — applied via inline style on the first <td>
export function outlierRowLeftBorder(outlier: OutlierResult | undefined): string {
  if (!outlier?.isOutlier) return '3px solid transparent'
  return outlier.direction === 'positive' ? '3px solid #34d399' : '3px solid #f87171'
}
