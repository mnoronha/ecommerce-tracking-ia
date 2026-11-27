/**
 * Portal clients proxy — server-side only.
 *
 * Validates Supabase session, then fetches client list from Agency API
 * using AGENCY_API_PLATFORM_KEY (never exposed to browser).
 * Replaces the legacy unauthenticated /setup/clients call in portal layout.
 */
import { NextResponse }      from 'next/server'
import { createSupabaseServerClient } from '@/lib/supabase-server'
import { getAgencyClientIdentifierSet } from '@/lib/bff-auth'

const _BASE =
  process.env.AGENCY_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'https://ecommerce-tracking-ia-production.up.railway.app'

export async function GET() {
  // ── 1. Session guard ────────────────────────────────────────────────────────
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  // ── 2. Resolve authorized client identifiers for this user ──────────────────
  const allowedIds = new Set<string>()

  const { data: agencyMember } = await supabase
    .from('agency_members')
    .select('agency_id, role')
    .eq('user_id', user.id)
    .in('role', ['owner', 'admin', 'member'])
    .limit(1)
    .maybeSingle()

  if (agencyMember?.agency_id) {
    const agencyClients = await getAgencyClientIdentifierSet(String(agencyMember.agency_id))
    agencyClients.forEach((id) => allowedIds.add(id))
  } else {
    // Portal user: restrict strictly to their own client_users / client_members rows
    if (user.email) {
      const { data: portalRows } = await supabase
        .from('client_users')
        .select('pixel_id')
        .eq('email', user.email)
      for (const r of portalRows ?? []) {
        if (r.pixel_id) allowedIds.add(String(r.pixel_id))
      }
    }
    const { data: memberRows } = await supabase
      .from('client_members')
      .select('client_id')
      .eq('user_id', user.id)
    for (const r of memberRows ?? []) {
      if (r.client_id) allowedIds.add(String(r.client_id))
    }

    if (allowedIds.size === 0) {
      return NextResponse.json({ error: 'Forbidden' }, { status: 403 })
    }
  }

  // ── 3. Fetch client list from Agency API and filter to authorized tenants ───
  const key = process.env.AGENCY_API_PLATFORM_KEY || ''
  try {
    const res  = await fetch(new URL('/agency/v1/clients', _BASE).toString(), {
      headers: { Authorization: `Bearer ${key}` },
      cache: 'no-store',
    })
    const data = await res.json().catch(() => [])
    if (!Array.isArray(data)) {
      return NextResponse.json(data, { status: res.status })
    }
    const filtered = data.filter((item) => {
      if (!item || typeof item !== 'object') return false
      const rec = item as Record<string, unknown>
      return ['id', 'pixel_id', 'client_id', 'slug'].some((k) => {
        const v = rec[k]
        return typeof v === 'string' && allowedIds.has(v)
      })
    })
    return NextResponse.json(filtered, { status: res.status })
  } catch {
    return NextResponse.json([], { status: 200 })
  }
}
