'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'
import { BarChart2, Users, LogOut, Bell, Settings, Wallet } from 'lucide-react'
import { createSupabaseBrowserClient } from '@/lib/supabase-browser'
import { useRouter } from 'next/navigation'

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router   = useRouter()
  const [isOwner, setIsOwner] = useState(false)

  useEffect(() => {
    const check = async () => {
      const supabase = createSupabaseBrowserClient()
      const { data: { user } } = await supabase.auth.getUser()
      if (!user) return
      const { data } = await supabase
        .from('agency_members')
        .select('role')
        .eq('user_id', user.id)
        .eq('role', 'owner')
        .maybeSingle()
      setIsOwner(!!data)
    }
    check()
  }, [])

  // Top bar shows on agency-level pages only (not inside individual client areas).
  // /dashboard and /system-health remain valid URLs; top bar still renders there,
  // but they are no longer listed as nav links.
  const isAgencyPage =
    pathname === '/dashboard'    ||
    pathname === '/clients'      ||
    pathname === '/clients/new'  ||
    pathname === '/alertas'      ||
    pathname === '/configuracoes'||
    pathname === '/financeiro'   ||
    pathname === '/system-health'

  async function handleSignOut() {
    const supabase = createSupabaseBrowserClient()
    await supabase.auth.signOut()
    router.push('/login')
    router.refresh()
  }

  return (
    <div className="flex min-h-screen bg-[#0f1117]">
      {isAgencyPage && (
        <div className="fixed top-0 left-0 right-0 z-10 h-12 border-b border-[#2a2f3e] bg-[#0f1117] flex items-center px-5 justify-between">
          <div className="flex items-center gap-2">
            <BarChart2 size={16} className="text-indigo-400" />
            <span className="text-sm font-bold text-white">Ecommerce Tracking IA</span>
            <span className="text-slate-600 text-xs ml-1">· Noro Labs</span>
          </div>
          <div className="flex items-center gap-4">
            <Link
              href="/clients"
              className={`text-xs transition-colors flex items-center gap-1 ${pathname === '/clients' ? 'text-white' : 'text-slate-500 hover:text-white'}`}
            >
              <Users size={14} />Clientes
            </Link>
            <Link
              href="/alertas"
              className={`text-xs transition-colors flex items-center gap-1 ${pathname === '/alertas' ? 'text-white' : 'text-slate-500 hover:text-white'}`}
            >
              <Bell size={13} />Alertas
            </Link>
            {isOwner && (
              <Link
                href="/financeiro"
                className={`text-xs transition-colors flex items-center gap-1 ${pathname === '/financeiro' ? 'text-white' : 'text-slate-500 hover:text-white'}`}
              >
                <Wallet size={13} />Financeiro
              </Link>
            )}
            <Link
              href="/configuracoes"
              className={`text-xs transition-colors flex items-center gap-1 ${pathname === '/configuracoes' ? 'text-white' : 'text-slate-500 hover:text-white'}`}
            >
              <Settings size={13} />Configurações
            </Link>
            <button
              onClick={handleSignOut}
              className="text-xs text-slate-500 hover:text-white transition-colors flex items-center gap-1"
            >
              <LogOut size={13} />Sair
            </button>
          </div>
        </div>
      )}
      <main className={`flex-1 overflow-auto ${isAgencyPage ? 'pt-12' : ''}`}>
        {children}
      </main>
    </div>
  )
}
