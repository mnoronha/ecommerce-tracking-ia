'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { BarChart2, Users, LogOut, Bell, LayoutDashboard, CreditCard } from 'lucide-react'
import { createSupabaseBrowserClient } from '@/lib/supabase-browser'
import { useRouter } from 'next/navigation'

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router   = useRouter()

  const isClientsRoot = pathname === '/dashboard' || pathname === '/clients' || pathname === '/clients/new' || pathname === '/alertas' || pathname === '/billing'

  async function handleSignOut() {
    const supabase = createSupabaseBrowserClient()
    await supabase.auth.signOut()
    router.push('/login')
    router.refresh()
  }

  return (
    <div className="flex min-h-screen bg-[#0f1117]">
      {/* Top bar — only on agency-level pages */}
      {isClientsRoot && (
        <div className="fixed top-0 left-0 right-0 z-10 h-12 border-b border-[#2a2f3e] bg-[#0f1117] flex items-center px-5 justify-between">
          <div className="flex items-center gap-2">
            <BarChart2 size={16} className="text-indigo-400" />
            <span className="text-sm font-bold text-white">Ecommerce Tracking IA</span>
            <span className="text-slate-600 text-xs ml-1">· Pareto Plus</span>
          </div>
          <div className="flex items-center gap-4">
            <Link href="/dashboard" className={`text-xs transition-colors flex items-center gap-1 ${pathname === '/dashboard' ? 'text-white' : 'text-slate-500 hover:text-white'}`}>
              <LayoutDashboard size={13} />Visão Geral
            </Link>
            <Link href="/clients" className={`text-xs transition-colors flex items-center gap-1 ${pathname === '/clients' ? 'text-white' : 'text-slate-500 hover:text-white'}`}>
              <Users size={14} />Clientes
            </Link>
            <Link href="/alertas" className={`text-xs transition-colors flex items-center gap-1 ${pathname === '/alertas' ? 'text-white' : 'text-slate-500 hover:text-white'}`}>
              <Bell size={13} />Alertas
            </Link>
            <Link href="/billing" className={`text-xs transition-colors flex items-center gap-1 ${pathname === '/billing' ? 'text-white' : 'text-slate-500 hover:text-white'}`}>
              <CreditCard size={13} />Plano
            </Link>
            <button onClick={handleSignOut} className="text-xs text-slate-500 hover:text-white transition-colors flex items-center gap-1">
              <LogOut size={13} />Sair
            </button>
          </div>
        </div>
      )}
      <main className={`flex-1 overflow-auto ${isClientsRoot ? 'pt-12' : ''}`}>
        {children}
      </main>
    </div>
  )
}
