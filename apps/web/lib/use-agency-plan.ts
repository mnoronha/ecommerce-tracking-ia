'use client'

import { useEffect, useState } from 'react'
import { createSupabaseBrowserClient } from './supabase-browser'
import { type PlanId, planGates } from './plans'

export interface AgencyPlan {
  planId: PlanId
  trialEndsAt: string | null
  ordersLimit: number | null
  clientLimit: number
  gates: Record<string, boolean>
  isTrialing: boolean
}

const DEFAULT: AgencyPlan = {
  planId: 'rastreador',
  trialEndsAt: null,
  ordersLimit: 2000,
  clientLimit: 1,
  gates: planGates('rastreador'),
  isTrialing: false,
}

export function useAgencyPlan(pixelId?: string): { plan: AgencyPlan; loading: boolean } {
  const [plan, setPlan]     = useState<AgencyPlan>(DEFAULT)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!pixelId) { setLoading(false); return }
    const supabase = createSupabaseBrowserClient()

    supabase
      .from('clients')
      .select('agency_id, agencies(plan, trial_ends_at, orders_limit, client_limit)')
      .eq('pixel_id', pixelId)
      .limit(1)
      .single()
      .then(({ data }) => {
        const agency = (data as any)?.agencies as any
        if (agency) {
          const planId = (agency.plan ?? 'rastreador') as PlanId
          const trialEndsAt = agency.trial_ends_at ?? null
          setPlan({
            planId,
            trialEndsAt,
            ordersLimit:  agency.orders_limit ?? 2000,
            clientLimit:  agency.client_limit ?? 1,
            gates:        planGates(planId),
            isTrialing:   trialEndsAt ? new Date(trialEndsAt) > new Date() : false,
          })
        }
        setLoading(false)
      })
  }, [pixelId])

  return { plan, loading }
}
