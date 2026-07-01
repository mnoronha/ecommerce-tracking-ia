'use client'

import Link from 'next/link'
import { usePathname, useParams } from 'next/navigation'
import { useEffect, useState } from 'react'
import {
  LayoutDashboard, TrendingUp, BarChart2, Target,
  GitBranch, BarChart,
} from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

export default function PortalLayout({ children }: { children: React.ReactNode }) {
  const pathname  = usePathname()
  const params    = useParams()
  const clientId  = params.clientId as string

  const [clientName, setClientName] = useState<string>('')
  const [logoUrl,    setLogoUrl]    = useState<string | null>(null)

  useEffect(() => {
    if (!clientId) return
    fetch(`${API_URL}/setup/clients`)
      .then(r => r.json())
      .then((d: any[]) => {
        const list = Array.isArray(d) ? d : d.clients || []
        const c = list.find((x: any) => x.pixel_id === clientId)
        if (c) {
          setClientName(c.name || clientId)
          setLogoUrl(c.logo_url || null)
        }
      })
      .catch(() => {})
  }, [clientId])

  const NAV = [
    { href: `/portal/${clientId}/dashboard`,     label: 'Dashboard',     icon: LayoutDashboard },
    { href: `/portal/${clientId}/attribution`,   label: 'Atribuição',    icon: GitBranch },
    { href: `/portal/${clientId}/meta-ads`,      label: 'Meta Ads',      icon: Target },
    { href: `/portal/${clientId}/google-ads`,    label: 'Google Ads',    icon: TrendingUp },
    { href: `/portal/${clientId}/tiktok-ads`,    label: 'TikTok Ads',    icon: BarChart },
    { href: `/portal/${clientId}/pinterest-ads`, label: 'Pinterest Ads', icon: BarChart2 },
    { href: `/portal/${clientId}/ga4`,           label: 'GA4',           icon: BarChart2 },
  ]

  return (
    <div className="flex min-h-screen bg-[#0f1117]">
      {/* Sidebar */}
      <aside className="w-56 shrink-0 border-r border-[#2a2f3e] flex flex-col">
        {/* Brand + client */}
        <div className="px-4 py-5 border-b border-[#2a2f3e]">
          {logoUrl ? (
            <img src={logoUrl} alt={clientName} className="h-8 object-contain mb-3" />
          ) : (
            <div className="flex items-center gap-2 mb-3">
              <div className="w-7 h-7 rounded-lg bg-indigo-600 flex items-center justify-center shrink-0">
                <span className="text-white text-xs font-black">N</span>
              </div>
              <span className="text-sm font-bold text-white">Noro</span>
            </div>
          )}
          <p className="text-xs font-semibold text-slate-300 truncate" title={clientName}>
            {clientName || clientId}
          </p>
          <p className="text-[10px] text-slate-600 mt-0.5">Portal do cliente</p>
        </div>

        {/* Nav */}
        <nav className="flex-1 p-3 space-y-0.5 overflow-y-auto">
          {NAV.map(item => {
            const active = pathname === item.href || pathname.startsWith(item.href + '/')
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
                <span>{item.label}</span>
              </Link>
            )
          })}
        </nav>

        {/* Footer */}
        <div className="p-4 border-t border-[#2a2f3e] text-center">
          <p className="text-[10px] text-slate-600">
            Powered by{' '}
            <span className="text-indigo-500 font-semibold">Noro</span>
          </p>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-auto">
        {children}
      </main>
    </div>
  )
}
