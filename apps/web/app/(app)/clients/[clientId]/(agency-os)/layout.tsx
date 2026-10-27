'use client'

/**
 * Agency OS — uma área com abas para as telas do Hermes / Core.
 * As URLs continuam as mesmas (route group), só a navegação foi unificada.
 */

import Link from 'next/link'
import { usePathname, useParams } from 'next/navigation'

const TABS: { seg: string; label: string }[] = [
  { seg: 'agency-os',           label: 'Visão geral' },
  { seg: 'performance',         label: 'Performance' },
  { seg: 'canonical-reports',   label: 'Relatórios' },
  { seg: 'actions',             label: 'Ações' },
  { seg: 'changes',             label: 'Mudanças' },
  { seg: 'data-health',         label: 'Saúde dos Dados' },
  { seg: 'recommendations',     label: 'Recomendações' },
  { seg: 'learning-candidates', label: 'Aprendizados' },
]

export default function AgencyOsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const params   = useParams()
  const base     = `/clients/${params.clientId as string}`

  return (
    <div>
      <div className="px-6 pt-5 border-b border-[#2a2f3e] overflow-x-auto">
        <p className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider mb-2">Action Center</p>
        <nav className="flex gap-1 min-w-max">
          {TABS.map(t => {
            const href   = `${base}/${t.seg}`
            const active = pathname === href || pathname.startsWith(`${href}/`)
            return (
              <Link
                key={t.seg}
                href={href}
                className={`px-3 py-2 text-xs rounded-t-md border-b-2 transition-colors ${
                  active
                    ? 'border-indigo-500 text-white font-medium'
                    : 'border-transparent text-slate-500 hover:text-slate-200'
                }`}
              >
                {t.label}
              </Link>
            )
          })}
        </nav>
      </div>
      {children}
    </div>
  )
}
