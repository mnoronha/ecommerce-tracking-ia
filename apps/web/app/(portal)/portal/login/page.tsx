'use client'

import { useState, Suspense } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { createSupabaseBrowserClient } from '@/lib/supabase-browser'
import { BarChart2, Loader2, Mail, Lock, Eye, EyeOff } from 'lucide-react'

function PortalLoginForm() {
  const searchParams = useSearchParams()
  const router       = useRouter()
  const redirect     = searchParams.get('redirect') || '/portal'

  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [showPw,   setShowPw]   = useState(false)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState('')

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)

    const supabase = createSupabaseBrowserClient()
    const { error: authError } = await supabase.auth.signInWithPassword({
      email:    email.trim().toLowerCase(),
      password: password,
    })

    if (authError) {
      setError('Email ou senha incorretos.')
      setLoading(false)
      return
    }

    router.replace(redirect)
  }

  return (
    <div className="min-h-screen bg-[#0f1117] flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-indigo-600/20 border border-indigo-500/30 mb-4">
            <BarChart2 size={22} className="text-indigo-400" />
          </div>
          <h1 className="text-xl font-bold text-white">Portal do Cliente</h1>
          <p className="text-sm text-slate-500 mt-1">Powered by Noro</p>
        </div>

        <div className="bg-[#1a1f2e] rounded-2xl border border-[#2a2f3e] p-6">
          <h2 className="text-base font-semibold text-white mb-1">Acessar portal</h2>
          <p className="text-xs text-slate-500 mb-5">
            Entre com email e senha fornecidos pela sua agência.
          </p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">Email</label>
              <div className="relative">
                <Mail size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                <input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder="seu@email.com"
                  required
                  className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg pl-9 pr-3 py-2.5 text-sm text-white placeholder-slate-600 outline-none focus:border-indigo-500 transition-colors"
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">Senha</label>
              <div className="relative">
                <Lock size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                <input
                  type={showPw ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="••••••••"
                  required
                  className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg pl-9 pr-10 py-2.5 text-sm text-white placeholder-slate-600 outline-none focus:border-indigo-500 transition-colors"
                />
                <button
                  type="button"
                  onClick={() => setShowPw(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                  tabIndex={-1}
                >
                  {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>

            {error && <p className="text-xs text-red-400">{error}</p>}

            <button
              type="submit"
              disabled={loading || !email || !password}
              className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg py-2.5 transition-colors flex items-center justify-center gap-2"
            >
              {loading ? <><Loader2 size={14} className="animate-spin" /> Entrando...</> : 'Entrar'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}

export default function PortalLoginPage() {
  return (
    <Suspense>
      <PortalLoginForm />
    </Suspense>
  )
}
