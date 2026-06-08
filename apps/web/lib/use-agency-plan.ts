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
// Each entry carries a fetchedAt timestamp; stale entries (> TTL) are re-fetched.
const _cache = new Map<string, { plan: AgencyPlan; fetchedAt: number }>()
const CACHE_TTL_MS = 5 * 60 * 1000 // 5 minutes

export function useAgencyPlan(pixelId?: string): { plan: AgencyPlan; loading: boolean } {
  const entry   = pixelId ? _cache.get(pixelId) : undefined
  const isFresh = entry ? (Date.now() - entry.fetchedAt) < CACHE_TTL_MS : false
  const [plan,    setPlan]    = useState<AgencyPlan>(isFresh ? entry!.plan : DEFAULT)
  const [loading, setLoading] = useState(!isFresh)

  useEffect(() => {
    if (!pixelId) { setLoading(false); return }
    const e = _cache.get(pixelId)
    if (e && (Date.now() - e.fetchedAt) < CACHE_TTL_MS) {
      setPlan(e.plan); setLoading(false); return
    }

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
        _cache.set(pixelId, { plan: result, fetchedAt: Date.now() })
        setPlan(result)
        setLoading(false)
      })
  }, [pixelId])

  return { plan, loading }
}
