'use client'

import Link from 'next/link'
import { usePathname, useParams } from 'next/navigation'
import { LayoutDashboard, Users, ShoppingBag, Target, Settings, ArrowLeft, BarChart2, TrendingUp, Radio, DollarSign } from 'lucide-react'

export default function ClientLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const params   = useParams()
  const clientId = params.clientId as string

  const NAV = [
    { href: `/clients/${clientId}/dashboard`,   label: 'Dashboard',  icon: LayoutDashboard },
    { href: `/clients/${clientId}/live`,        label: 'Ao vivo',    icon: Radio },
    { href: `/clients/${clientId}/visitantes`,  label: 'Visitantes', icon: Users },
    { href: `/clients/${clientId}/pedidos`,     label: 'Pedidos',    icon: ShoppingBag },
    { href: `/clients/${clientId}/audiencias`,  label: 'Audiências', icon: Target },
    { href: `/clients/${clientId}/attribution`, label: 'Atribuição', icon: TrendingUp },
    { href: `/clients/${clientId}/cogs`,        label: 'Custos & Margem', icon: DollarSign },
    { href: `/clients/${clientId}/settings`,    label: 'Configurações', icon: Settings },
  ]

  return (
    <div className="flex min-h-screen bg-[#0f1117]">
      <aside className="w-52 shrink-0 border-r border-[#2a2f3e] flex flex-col">
        <div className="px-4 py-4 border-b border-[#2a2f3e]">
          <Link href="/clients" className="flex items-center gap-1.5 text-slate-500 hover:text-white transition-colors text-xs mb-3">
            <ArrowLeft size={12} />
            Todos os clientes
          </Link>
          <div className="flex items-center gap-2">
            <BarChart2 size={16} className="text-indigo-400 shrink-0" />
            <span className="text-sm font-bold text-white truncate">{clientId}</span>
          </div>
        </div>
        <nav className="flex-1 p-3 space-y-1">
          {NAV.map(item => {
            const active = pathname === item.href
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
                {item.label}
              </Link>
            )
          })}
        </nav>
      </aside>
      <main className="flex-1 overflow-auto">{children}</main>
    </div>
  )
}
