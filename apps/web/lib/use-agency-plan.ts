'use client'

import { useEffect, useState } from 'react'
import { createSupabaseBrowserClient } from './supabase-browser'
import { type PlanId, planGates } from './plans'

export interface AgencyPlan {
  planId: PlanId
  clientName: string
  trialEndsAt: string | null
  ordersLimit: number | null
  clientLimit: number
  gates: Record<string, boolean>
  isTrialing: boolean
}

const DEFAULT: AgencyPlan = {
  planId: 'predicao',
  clientName: '',
  trialEndsAt: null,
  ordersLimit: null,
  clientLimit: Infinity,
  gates: planGates('predicao'),
  isTrialing: false,
}

// Module-level cache keyed by pixelId — survives re-renders, resets on full page reload.
const _cache = new Map<string, AgencyPlan>()

export function useAgencyPlan(pixelId?: string): { plan: AgencyPlan; loading: boolean } {
  const cached  = pixelId ? _cache.get(pixelId) : undefined
  const [plan,    setPlan]    = useState<AgencyPlan>(cached ?? DEFAULT)
  const [loading, setLoading] = useState(!cached)

  useEffect(() => {
    if (!pixelId) { setLoading(false); return }
    if (_cache.has(pixelId)) { setPlan(_cache.get(pixelId)!); setLoading(false); return }

    createSupabaseBrowserClient()
      .from('clients')
      // Single query: name + agency plan in one round-trip
      .select('name, agencies(plan, trial_ends_at, orders_limit, client_limit)')
      .eq('pixel_id', pixelId)
      .limit(1)
      .single()
      .then(({ data }) => {
        const agency  = (data as any)?.agencies as any
        const planId  = (agency?.plan ?? 'rastreador') as PlanId
        const trialEndsAt = agency?.trial_ends_at ?? null
        const result: AgencyPlan = {
          planId,
          clientName:  (data as any)?.name ?? pixelId,
          trialEndsAt,
          ordersLimit: agency?.orders_limit ?? 2000,
          clientLimit: agency?.client_limit ?? 1,
          gates:       planGates(planId),
          isTrialing:  trialEndsAt ? new Date(trialEndsAt) > new Date() : false,
        }
        _cache.set(pixelId, result)
        setPlan(result)
        setLoading(false)
      })
  }, [pixelId])

  return { plan, loading }
}
