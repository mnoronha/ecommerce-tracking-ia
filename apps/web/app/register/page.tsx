'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { createSupabaseBrowserClient } from '@/lib/supabase-browser'
import { PLANS, fmtPrice, type PlanId } from '@/lib/plans'
import { BarChart2, Check, X, Loader2, ArrowRight, ArrowLeft, Zap } from 'lucide-react'

type Step = 'plans' | 'account'

export default function RegisterPage() {
  const router = useRouter()
  const [step,      setStep]      = useState<Step>('plans')
  const [annual,    setAnnual]    = useState(false)
  const [planId,    setPlanId]    = useState<PlanId>('inteligencia')
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState('')

  const [form, setForm] = useState({ name: '', agencyName: '', email: '', password: '' })
  function set(k: string, v: string) { setForm(f => ({ ...f, [k]: v })) }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)

    const supabase = createSupabaseBrowserClient()

    // 1 — create auth user
    const { data, error: authErr } = await supabase.auth.signUp({
      email:    form.email,
      password: form.password,
      options:  { data: { name: form.name } },
    })
    if (authErr || !data.user) {
      setError(authErr?.message || 'Erro ao criar conta.')
      setLoading(false)
      return
    }

    // 2 — create agency + membership via API
    const res = await fetch('/api/register', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ agencyName: form.agencyName || form.name, plan: planId }),
    })
    if (!res.ok) {
      const { error: apiErr } = await res.json()
      setError(apiErr || 'Erro ao criar agência.')
      setLoading(false)
      return
    }

    router.push('/clients/new?fresh=1')
  }

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between max-w-6xl mx-auto">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-indigo-600/20 border border-indigo-500/30 flex items-center justify-center">
            <BarChart2 size={16} className="text-indigo-400" />
          </div>
          <span className="font-bold text-white">Ecommerce Tracking IA</span>
          <span className="text-slate-600 text-xs ml-1">· by Pareto Plus</span>
        </div>
        <Link href="/login" className="text-xs text-slate-500 hover:text-white transition-colors">
          Já tenho conta →
        </Link>
      </div>

      <div className="max-w-6xl mx-auto px-6 py-12">
        {step === 'plans' ? (
          <>
            <div className="text-center mb-10">
              <h1 className="text-3xl font-bold text-white mb-3">
                Escolha seu plano
              </h1>
              <p className="text-slate-400 text-base">
                14 dias grátis em qualquer plano. Cancele quando quiser.
              </p>

              {/* Annual toggle */}
              <div className="inline-flex items-center gap-3 mt-6 bg-[#1a1f2e] rounded-xl p-1.5 border border-[#2a2f3e]">
                <button
                  onClick={() => setAnnual(false)}
                  className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${!annual ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'}`}
                >
                  Mensal
                </button>
                <button
                  onClick={() => setAnnual(true)}
                  className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5 ${annual ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'}`}
                >
                  Anual
                  <span className="text-xs bg-emerald-500/20 text-emerald-300 px-1.5 py-0.5 rounded">-17%</span>
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {PLANS.map(plan => {
                const price    = annual ? plan.priceAnnual : plan.price
                const selected = planId === plan.id
                return (
                  <div
                    key={plan.id}
                    onClick={() => setPlanId(plan.id)}
                    className={`relative rounded-2xl border-2 p-6 cursor-pointer transition-all ${
                      selected
                        ? plan.highlight
                          ? 'border-indigo-500 bg-indigo-500/5'
                          : plan.id === 'predicao'
                          ? 'border-purple-500 bg-purple-500/5'
                          : 'border-slate-400 bg-slate-500/5'
                        : 'border-[#2a2f3e] bg-[#1a1f2e] hover:border-slate-500'
                    }`}
                  >
                    {plan.badge && (
                      <div className={`absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 rounded-full text-xs font-bold ${
                        plan.highlight
                          ? 'bg-indigo-600 text-white'
                          : 'bg-purple-600 text-white'
                      }`}>
                        {plan.badge}
                      </div>
                    )}

                    <div className="mb-5">
                      <h3 className="text-lg font-bold text-white">{plan.name}</h3>
                      <p className="text-xs text-slate-500 mt-1 leading-relaxed">{plan.tagline}</p>
                    </div>

                    <div className="mb-6">
                      <div className="flex items-end gap-1">
                        <span className="text-3xl font-bold text-white">{fmtPrice(price)}</span>
                        <span className="text-slate-500 text-sm mb-1">/mês</span>
                      </div>
                      {annual && (
                        <p className="text-xs text-emerald-400 mt-0.5">
                          {fmtPrice(price * 12)}/ano · economize {fmtPrice((plan.price - plan.priceAnnual) * 12)}
                        </p>
                      )}
                    </div>

                    <ul className="space-y-2 mb-6">
                      {plan.features.map((f, i) => (
                        <li key={i} className="flex items-start gap-2 text-sm">
                          <Check size={14} className={`shrink-0 mt-0.5 ${plan.highlight ? 'text-indigo-400' : plan.id === 'predicao' ? 'text-purple-400' : 'text-emerald-400'}`} />
                          <span className={i === 0 ? 'text-white font-medium' : 'text-slate-300'}>{f}</span>
                        </li>
                      ))}
                      {plan.gates.map((f, i) => (
                        <li key={i} className="flex items-start gap-2 text-sm opacity-40">
                          <X size={14} className="shrink-0 mt-0.5 text-slate-500" />
                          <span className="text-slate-500 line-through">{f}</span>
                        </li>
                      ))}
                    </ul>

                    <button
                      onClick={e => { e.stopPropagation(); setPlanId(plan.id); setStep('account') }}
                      className={`w-full py-2.5 rounded-xl text-sm font-semibold transition-colors ${
                        plan.highlight
                          ? 'bg-indigo-600 hover:bg-indigo-700 text-white'
                          : plan.id === 'predicao'
                          ? 'bg-purple-600 hover:bg-purple-700 text-white'
                          : 'bg-[#252b3b] hover:bg-[#2e3448] text-white border border-[#3a4058]'
                      }`}
                    >
                      Começar trial grátis
                    </button>
                  </div>
                )
              })}
            </div>

            <p className="text-center text-xs text-slate-600 mt-8">
              Sem cartão de crédito no trial · Suporte em português · Cancele a qualquer momento
            </p>
          </>
        ) : (
          /* Step 2: Account form */
          <div className="max-w-md mx-auto">
            <button onClick={() => setStep('plans')} className="flex items-center gap-1.5 text-slate-500 hover:text-white text-sm mb-8 transition-colors">
              <ArrowLeft size={14} /> Voltar aos planos
            </button>

            {/* Selected plan recap */}
            {(() => {
              const plan = PLANS.find(p => p.id === planId)!
              return (
                <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl px-5 py-4 mb-8 flex items-center justify-between">
                  <div>
                    <p className="text-xs text-slate-500">Plano selecionado</p>
                    <p className="text-white font-semibold">{plan.name}</p>
                    <p className="text-xs text-slate-400 mt-0.5">{plan.clientLimit === Infinity ? 'Lojas ilimitadas' : `${plan.clientLimit} loja${plan.clientLimit > 1 ? 's' : ''}`} · {plan.ordersLimit?.toLocaleString('pt-BR') ?? '∞'} pedidos/mês</p>
                  </div>
                  <div className="text-right">
                    <p className="text-lg font-bold text-white">{fmtPrice(annual ? plan.priceAnnual : plan.price)}</p>
                    <p className="text-xs text-slate-500">/mês</p>
                  </div>
                </div>
              )
            })()}

            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-2xl p-7">
              <div className="flex items-center gap-2 mb-1">
                <Zap size={16} className="text-indigo-400" />
                <h2 className="text-base font-semibold text-white">Criar sua conta</h2>
              </div>
              <p className="text-xs text-slate-500 mb-6">14 dias grátis, sem cartão de crédito</p>

              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1.5">Seu nome</label>
                  <input
                    type="text" required value={form.name} onChange={e => set('name', e.target.value)}
                    placeholder="João Silva"
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-600 outline-none focus:border-indigo-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1.5">Nome da sua marca / agência</label>
                  <input
                    type="text" required value={form.agencyName} onChange={e => set('agencyName', e.target.value)}
                    placeholder="LK Sneakers"
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-600 outline-none focus:border-indigo-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1.5">Email</label>
                  <input
                    type="email" required autoComplete="email" value={form.email} onChange={e => set('email', e.target.value)}
                    placeholder="joao@lksneakers.com"
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-600 outline-none focus:border-indigo-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1.5">Senha (mín. 8 caracteres)</label>
                  <input
                    type="password" required minLength={8} autoComplete="new-password" value={form.password} onChange={e => set('password', e.target.value)}
                    placeholder="••••••••"
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-600 outline-none focus:border-indigo-500"
                  />
                </div>

                {error && (
                  <p className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">{error}</p>
                )}

                <button
                  type="submit" disabled={loading}
                  className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2 transition-colors"
                >
                  {loading
                    ? <><Loader2 size={14} className="animate-spin" /> Criando conta…</>
                    : <><ArrowRight size={14} /> Começar trial grátis</>}
                </button>

                <p className="text-xs text-center text-slate-600">
                  Ao criar sua conta, você concorda com os termos de uso.
                </p>
              </form>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
