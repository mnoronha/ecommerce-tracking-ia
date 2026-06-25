'use client'

import {
  Info, Settings, AlertTriangle, AlertCircle, ExternalLink,
} from 'lucide-react'
import Link from 'next/link'
import type { ElementType } from 'react'

// ── Types ─────────────────────────────────────────────────────────────────────

export type EmptyType = 'neutral' | 'setup' | 'warning' | 'critical'

interface ActionButton { label: string; onClick: () => void }
interface ActionLink   { label: string; href: string }

interface EmptyStateProps {
  /** Visual severity — drives colors */
  type?: EmptyType
  /** Optional icon override; falls back to type-appropriate default */
  icon?: ElementType
  title: string
  description?: string
  /** Primary call-to-action (button) */
  action?: ActionButton
  /** Secondary navigation link */
  link?: ActionLink
  compact?: boolean
}

// ── Style maps ────────────────────────────────────────────────────────────────

const TYPE_CONFIG: Record<EmptyType, {
  wrapper: string
  icon:    string
  title:   string
  desc:    string
  btn:     string
  Default: ElementType
}> = {
  neutral: {
    wrapper: 'bg-[#1a1f2e] border border-[#2a2f3e]',
    icon:    'text-slate-500 bg-slate-500/10',
    title:   'text-slate-300',
    desc:    'text-slate-500',
    btn:     'bg-slate-700 text-slate-200 hover:bg-slate-600',
    Default: Info,
  },
  setup: {
    wrapper: 'bg-blue-500/5 border border-blue-500/20',
    icon:    'text-blue-400 bg-blue-500/10',
    title:   'text-blue-200',
    desc:    'text-slate-400',
    btn:     'bg-blue-600 text-white hover:bg-blue-700',
    Default: Settings,
  },
  warning: {
    wrapper: 'bg-orange-500/5 border border-orange-500/20',
    icon:    'text-orange-400 bg-orange-500/10',
    title:   'text-orange-200',
    desc:    'text-slate-400',
    btn:     'bg-orange-600 text-white hover:bg-orange-700',
    Default: AlertTriangle,
  },
  critical: {
    wrapper: 'bg-red-500/5 border border-red-500/20',
    icon:    'text-red-400 bg-red-500/10',
    title:   'text-red-200',
    desc:    'text-slate-400',
    btn:     'bg-red-600 text-white hover:bg-red-700',
    Default: AlertCircle,
  },
}

// ── Component ─────────────────────────────────────────────────────────────────

export function EmptyState({
  type = 'neutral', icon, title, description, action, link, compact = false,
}: EmptyStateProps) {
  const cfg    = TYPE_CONFIG[type]
  const Icon   = icon ?? cfg.Default
  const py     = compact ? 'py-6' : 'py-10'

  return (
    <div className={`rounded-xl ${cfg.wrapper} ${py} px-6 text-center`}>
      <div className={`inline-flex items-center justify-center w-10 h-10 rounded-xl ${cfg.icon} mx-auto mb-3`}>
        <Icon size={18} />
      </div>
      <p className={`text-sm font-medium ${cfg.title}`}>{title}</p>
      {description && (
        <p className={`text-xs mt-1 leading-relaxed max-w-xs mx-auto ${cfg.desc}`}>{description}</p>
      )}
      {(action || link) && (
        <div className="flex items-center justify-center gap-2 mt-4 flex-wrap">
          {action && (
            <button
              onClick={action.onClick}
              className={`text-xs font-medium px-3 py-1.5 rounded-lg transition-colors ${cfg.btn}`}
            >
              {action.label}
            </button>
          )}
          {link && (
            <Link
              href={link.href}
              className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200 transition-colors"
            >
              {link.label}
              <ExternalLink size={10} />
            </Link>
          )}
        </div>
      )}
    </div>
  )
}

// ── Inline warning — para valores suspeitos dentro de KPIs ou células ─────────

export function InlineWarning({ children, tooltip }: { children: React.ReactNode; tooltip: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 text-orange-300 cursor-help"
      title={tooltip}
    >
      {children}
      <AlertTriangle size={11} className="shrink-0" />
    </span>
  )
}
