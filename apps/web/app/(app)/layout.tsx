'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { LayoutDashboard, Users, ShoppingBag, BarChart2, Target } from 'lucide-react'

const NAV = [
  { href: '/dashboard',   label: 'Dashboard',   icon: LayoutDashboard },
  { href: '/visitantes',  label: 'Visitantes',  icon: Users },
  { href: '/pedidos',     label: 'Pedidos',      icon: ShoppingBag },
  { href: '/audiencias',  label: 'Audiências',  icon: Target },
]

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  return (
    <div className="flex min-h-screen bg-[#0f1117]">
      {/* Sidebar */}
      <aside className="w-52 shrink-0 border-r border-[#2a2f3e] flex flex-col">
        <div className="px-5 py-5 border-b border-[#2a2f3e]">
          <div className="flex items-center gap-2">
            <BarChart2 size={18} className="text-indigo-400" />
            <span className="text-sm font-bold text-white">Ecommerce IA</span>
          </div>
          <p className="text-xs text-slate-500 mt-0.5">LK Sneakers</p>
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
      {/* Main */}
      <main className="flex-1 overflow-auto">{children}</main>
    </div>
  )
}
