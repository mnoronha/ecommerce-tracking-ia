import { redirect } from 'next/navigation'
import Link from 'next/link'
import { createSupabaseServerClient } from '@/lib/supabase-server'
import { PLANS, getPlan, fmtPrice, type PlanId } from '@/lib/plans'
import { CheckCircle, Lock, Zap, ArrowRight, Users, ShoppingBag, Clock } from 'lucide-react'

async function getBillingData(userId: string) {
  const supabase = await createSupabaseServerClient()

  const { data: member } = await supabase
    .from('agency_members')
    .select('agency_id, agencies(plan, trial_ends_at, orders_limit, client_limit, billing_email)')
    .eq('user_id', userId)
    .limit(1)
    .single()

  if (!member) return null

  const agency = (member as any).agencies as any
  const planId  = (agency?.plan ?? 'rastreador') as PlanId

  // MTD orders across all clients
  const monthStart = new Date()
  monthStart.setDate(1)
  monthStart.setHours(0, 0, 0, 0)

  const { data: clients } = await supabase
    .from('clients')
    .select('id')
    .eq('agency_id', member.agency_id)

  const clientIds = (clients ?? []).map(c => c.id)

  let ordersThisMonth = 0
  if (clientIds.length > 0) {
    const { count } = await supabase
      .from('orders')
      .select('id', { count: 'exact', head: true })
      .in('client_id', clientIds)
      .gte('created_at', monthStart.toISOString())
      .gt('total_price', 0)
    ordersThisMonth = count ?? 0
  }

  return {
    planId,
    trialEndsAt:  agency?.trial_ends_at ?? null,
    ordersLimit:  agency?.orders_limit  ?? 2000,
    clientLimit:  agency?.client_limit  ?? 1,
    billingEmail: agency?.billing_email ?? null,
    clientsCount: clientIds.length,
    ordersThisMonth,
  }
}

function fmt(n: number) {
  return new Intl.NumberFormat('pt-BR').format(n)
}

function UsageBar({ value, max, label }: { value: number; max: number | null; label: string }) {
  const pct = max ? Math.min(value / max * 100, 100) : 0
  const color = pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-yellow-500' : 'bg-indigo-500'
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs text-slate-400">{label}</span>
        <span className="text-xs text-slate-300 tabular-nums">
          {fmt(value)} / {max ? fmt(max) : '∞'}
          {max && <span className="text-slate-600 ml-1">({pct.toFixed(0)}%)</span>}
        </span>
      </div>
      {max && (
        <div className="w-full bg-[#2a2f3e] rounded-full h-1.5 overflow-hidden">
          <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  )
}

export default async function BillingPage() {
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) redirect('/login')

  const billing = await getBillingData(user.id)
  if (!billing) redirect('/dashboard')

  const currentPlan = getPlan(billing.planId)
  const isTrialing  = billing.trialEndsAt ? new Date(billing.trialEndsAt) > new Date() : false
  const trialDaysLeft = billing.trialEndsAt
    ? Math.max(0, Math.ceil((new Date(billing.trialEndsAt).getTime() - Date.now()) / 86_400_000))
    : null

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-8">
      <div>
        <h1 className="text-xl font-bold text-white">Plano & Cobrança</h1>
        <p className="text-sm text-slate-500 mt-0.5">Gerencie seu plano e acompanhe o uso</p>
      </div>

      {/* Current plan + usage */}
      <div className="grid gap-4 lg:grid-cols-2">
        {/* Plan card */}
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5 space-y-4">
          <div className="flex items-start justify-between">
            <div>
              <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">Plano atual</p>
              <p className="text-2xl font-bold text-white capitalize">{currentPlan.name}</p>
              <p className="text-sm text-slate-400 mt-0.5">{currentPlan.tagline}</p>
            </div>
            <div className="text-right">
              <p className="text-xl font-bold text-white">{fmtPrice(currentPlan.price)}</p>
              <p className="text-xs text-slate-500">por mês</p>
            </div>
          </div>

          {isTrialing && trialDaysLeft !== null && (
            <div className="flex items-center gap-2 bg-indigo-500/10 border border-indigo-500/20 rounded-lg px-3 py-2.5">
              <Clock size={13} className="text-indigo-400 shrink-0" />
              <p className="text-xs text-indigo-300">
                Trial ativo — <span className="font-semibold">{trialDaysLeft} dia{trialDaysLeft !== 1 ? 's' : ''} restante{trialDaysLeft !== 1 ? 's' : ''}</span>
              </p>
            </div>
          )}

          <div className="space-y-2 pt-2">
            {currentPlan.features.map(f => (
              <div key={f} className="flex items-start gap-2">
                <CheckCircle size={13} className="text-emerald-400 shrink-0 mt-0.5" />
                <span className="text-xs text-slate-300">{f}</span>
              </div>
            ))}
            {currentPlan.gates.map(f => (
              <div key={f} className="flex items-start gap-2 opacity-40">
                <Lock size={13} className="text-slate-500 shrink-0 mt-0.5" />
                <span className="text-xs text-slate-500">{f}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Usage card */}
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5 space-y-5">
          <p className="text-xs text-slate-500 uppercase tracking-wider">Uso este mês</p>

          <div className="space-y-4">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center shrink-0">
                <ShoppingBag size={14} className="text-indigo-400" />
              </div>
              <div className="flex-1">
                <UsageBar
                  value={billing.ordersThisMonth}
                  max={billing.ordersLimit}
                  label="Pedidos"
                />
              </div>
            </div>
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center shrink-0">
                <Users size={14} className="text-indigo-400" />
              </div>
              <div className="flex-1">
                <UsageBar
                  value={billing.clientsCount}
                  max={billing.clientLimit === Infinity ? null : billing.clientLimit}
                  label="Lojas conectadas"
                />
              </div>
            </div>
          </div>

          {billing.billingEmail && (
            <div className="pt-2 border-t border-[#2a2f3e]">
              <p className="text-xs text-slate-500">
                Cobranças enviadas para <span className="text-slate-400">{billing.billingEmail}</span>
              </p>
            </div>
          )}

          <div className="pt-2 border-t border-[#2a2f3e]">
            <p className="text-xs text-slate-500 mb-2">Para alterar plano ou dados de cobrança:</p>
            <a
              href="https://wa.me/5511999999999?text=Olá%2C%20quero%20alterar%20meu%20plano"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
            >
              Falar com suporte <ArrowRight size={11} />
            </a>
          </div>
        </div>
      </div>

      {/* Plan comparison */}
      <div>
        <h2 className="text-sm font-semibold text-white mb-4">Comparar planos</h2>
        <div className="grid gap-4 md:grid-cols-3">
          {PLANS.map(plan => {
            const isCurrent = plan.id === billing.planId
            const isUpgrade = PLANS.indexOf(plan) > PLANS.findIndex(p => p.id === billing.planId)
            return (
              <div
                key={plan.id}
                className={`bg-[#1a1f2e] rounded-xl p-5 space-y-4 border transition-colors ${
                  isCurrent
                    ? 'border-indigo-500/50'
                    : 'border-[#2a2f3e] hover:border-[#3a3f4e]'
                }`}
              >
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <p className="text-sm font-semibold text-white">{plan.name}</p>
                    {isCurrent && (
                      <span className="text-xs bg-indigo-500/20 text-indigo-400 px-2 py-0.5 rounded-full font-medium">
                        atual
                      </span>
                    )}
                    {plan.badge && !isCurrent && (
                      <span className="text-xs bg-slate-700/50 text-slate-400 px-2 py-0.5 rounded-full">
                        {plan.badge}
                      </span>
                    )}
                  </div>
                  <p className="text-xl font-bold text-white">{fmtPrice(plan.price)}<span className="text-xs font-normal text-slate-500">/mês</span></p>
                  <p className="text-xs text-slate-500 mt-0.5">{fmtPrice(plan.priceAnnual)}/mês no anual</p>
                </div>

                <div className="space-y-1.5">
                  {plan.features.slice(0, 6).map(f => (
                    <div key={f} className="flex items-start gap-1.5">
                      <CheckCircle size={11} className="text-emerald-400 shrink-0 mt-0.5" />
                      <span className="text-xs text-slate-400">{f}</span>
                    </div>
                  ))}
                </div>

                {isUpgrade && (
                  <a
                    href={`https://wa.me/5511999999999?text=Quero%20fazer%20upgrade%20para%20o%20plano%20${plan.name}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center justify-center gap-1.5 w-full bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-medium py-2.5 rounded-lg transition-colors"
                  >
                    <Zap size={12} />
                    Fazer upgrade
                  </a>
                )}
                {isCurrent && (
                  <div className="flex items-center justify-center gap-1.5 w-full bg-indigo-600/10 border border-indigo-600/20 text-indigo-400 text-xs font-medium py-2.5 rounded-lg">
                    <CheckCircle size={12} />
                    Plano ativo
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
