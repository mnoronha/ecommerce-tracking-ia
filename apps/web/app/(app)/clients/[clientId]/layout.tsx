'use client'

import Link from 'next/link'
import { usePathname, useParams } from 'next/navigation'
import { useClientConfig } from '@/lib/use-client-config'
import { CriticalAlertsBar } from '@/components/alerts/critical-alerts-bar'
import {
  LayoutDashboard, TrendingUp, FileText, Target, History, Calendar, Settings,
  ArrowLeft, BarChart2, Share2, Search,
} from 'lucide-react'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Icon = React.ComponentType<any>

interface NavItem { href: string; label: string; icon: Icon; match?: string[] }

export default function ClientLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const params   = useParams()
  const clientId = params.clientId as string

  const { config } = useClientConfig(clientId)
  const clientName = config.name || clientId
  const base = `/clients/${clientId}`

  const NAV_ITEMS: NavItem[] = [
    { href: `${base}/dashboard`,        label: 'Visão Geral',  icon: LayoutDashboard },
    { href: `${base}/performance`,       label: 'Performance',  icon: TrendingUp, match: ['performance'] },
    { href: `${base}/meta-ads`,          label: 'Meta Ads',     icon: Share2,     match: ['meta-ads'] },
    { href: `${base}/google-ads`,        label: 'Google Ads',   icon: Search,     match: ['google-ads', 'merchant-center', 'ga4', 'search-console'] },
    { href: `${base}/creatives`,         label: 'Criativos',    icon: BarChart2,  match: ['creatives'] },
    { href: `${base}/canonical-reports`, label: 'Relatórios',   icon: FileText,   match: ['canonical-reports'] },
    { href: `${base}/actions`,           label: 'Ações',        icon: Target,     match: ['actions'] },
    { href: `${base}/changes`,           label: 'Mudanças',     icon: History,    match: ['changes'] },
    { href: `${base}/meetings`,          label: 'Reuniões',     icon: Calendar,   match: ['meetings'] },
    {
      href: `${base}/settings`,
      label: 'Configurações',
      icon: Settings,
      match: ['settings', 'goals-budget', 'cogs', 'users', 'data-health'],
    },
  ]

  const isActive = (item: NavItem) =>
    pathname === item.href ||
    (item.match ?? []).some(seg =>
      pathname === `${base}/${seg}` || pathname.startsWith(`${base}/${seg}/`)
    )

  return (
    <div className="flex min-h-screen bg-[#0f1117]">
      <aside className="w-52 shrink-0 border-r border-[#2a2f3e] flex flex-col">
        <div className="px-4 py-4 border-b border-[#2a2f3e]">
          <Link
            href="/clients"
            className="flex items-center gap-1.5 text-slate-500 hover:text-white transition-colors text-xs mb-3"
          >
            <ArrowLeft size={12} />
            Todos os clientes
          </Link>
          <div className="flex items-center gap-2">
            <BarChart2 size={16} className="text-indigo-400 shrink-0" />
            <span className="text-sm font-bold text-white truncate" title={clientId}>
              {clientName}
            </span>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto p-3">
          <div className="space-y-0.5">
            {NAV_ITEMS.map(item => {
              const active = isActive(item)
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                    active
                      ? 'bg-indigo-600/20 text-indigo-400 font-medium'
                      : 'text-slate-400 hover:text-white hover:bg-[#1a1f2e]'
                  }`}
                >
                  <item.icon size={15} />
                  <span className="flex-1">{item.label}</span>
                </Link>
              )
            })}
          </div>
        </nav>

        <div className="p-3 border-t border-[#2a2f3e]">
          <Link
            href="/privacidade"
            className="block px-3 text-[11px] text-slate-600 hover:text-slate-400 transition-colors"
          >
            Política de Privacidade
          </Link>
        </div>
      </aside>

      <main className="flex-1 overflow-auto">
        <CriticalAlertsBar clientId={clientId} />
        {children}
      </main>
    </div>
  )
}
