'use client'

import Link from 'next/link'
import { Lock } from 'lucide-react'
import { type PlanId, planGates, PLANS } from '@/lib/plans'

interface PlanGateProps {
  feature: string
  planId: PlanId
  children: React.ReactNode
  /** When true, replaces children with a full-page lock message instead of an overlay */
  fullPage?: boolean
}

/** Wraps content that requires a higher plan. Shows lock overlay or full-page gate. */
export function PlanGate({ feature, planId, children, fullPage }: PlanGateProps) {
  const allowed = planGates(planId)[feature] ?? true
  if (allowed) return <>{children}</>

  const requiredPlan = PLANS.find(p => planGates(p.id)[feature])
  const planName = requiredPlan?.name ?? 'Inteligência'

  if (fullPage) {
    return (
      <div className="flex-1 flex items-center justify-center min-h-[60vh] p-8">
        <div className="text-center max-w-sm space-y-4">
          <div className="w-14 h-14 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center mx-auto">
            <Lock size={22} className="text-indigo-400" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-white">Feature do plano {planName}</h2>
            <p className="text-sm text-slate-400 mt-1">
              Esta funcionalidade requer o plano <span className="text-indigo-400 font-medium">{planName}</span> ou superior.
            </p>
          </div>
          <Link
            href="/billing"
            className="inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-5 py-2.5 rounded-lg transition-colors"
          >
            Ver planos e fazer upgrade
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="relative">
      <div className="pointer-events-none select-none opacity-30 blur-[2px]">{children}</div>
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5 text-center max-w-xs shadow-xl">
          <Lock size={18} className="text-indigo-400 mx-auto mb-2" />
          <p className="text-sm font-semibold text-white">Plano {planName}</p>
          <p className="text-xs text-slate-400 mt-1 mb-3">
            Faça upgrade para acessar esta funcionalidade.
          </p>
          <Link
            href="/billing"
            className="inline-flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-medium px-4 py-2 rounded-lg transition-colors"
          >
            Ver planos →
          </Link>
        </div>
      </div>
    </div>
  )
}

/** Inline lock badge for nav items */
export function PlanLockBadge({ show }: { show: boolean }) {
  if (!show) return null
  return (
    <Lock
      size={10}
      className="text-slate-600 ml-auto shrink-0"
      aria-label="Feature bloqueada"
    />
  )
}
