'use client'

import { ShieldOff } from 'lucide-react'
import { createSupabaseBrowserClient } from '@/lib/supabase-browser'
import { useRouter } from 'next/navigation'

export default function AcessoNegadoPage() {
  const router = useRouter()

  async function handleSignOut() {
    await createSupabaseBrowserClient().auth.signOut()
    router.push('/portal/login')
  }

  return (
    <div className="min-h-screen bg-[#0f1117] flex items-center justify-center px-4">
      <div className="text-center max-w-sm">
        <div className="inline-flex items-center justify-center w-14 h-14 rounded-xl bg-red-600/20 border border-red-500/30 mb-4">
          <ShieldOff size={26} className="text-red-400" />
        </div>
        <h1 className="text-lg font-bold text-white mb-2">Acesso não autorizado</h1>
        <p className="text-sm text-slate-400 mb-6">
          Seu email não tem permissão para acessar este portal.
          Entre em contato com a agência para solicitar acesso.
        </p>
        <button
          onClick={handleSignOut}
          className="text-sm text-indigo-400 hover:text-indigo-300 transition-colors"
        >
          Sair e tentar outro email
        </button>
      </div>
    </div>
  )
}
