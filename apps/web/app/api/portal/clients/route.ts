/**
 * Portal clients proxy — server-side only.
 *
 * Validates Supabase session, then fetches client list from Agency API
 * using AGENCY_API_PLATFORM_KEY (never exposed to browser).
 * Replaces the legacy unauthenticated /setup/clients call in portal layout.
 */
import { NextResponse }      from 'next/server'
import { createSupabaseServerClient } from '@/lib/supabase-server'

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

  // ── 2. Fetch client list from Agency API ────────────────────────────────────
  const key = process.env.AGENCY_API_PLATFORM_KEY || ''
  try {
    const res  = await fetch(new URL('/agency/v1/clients', _BASE).toString(), {
      headers: { Authorization: `Bearer ${key}` },
      cache: 'no-store',
    })
    const data = await res.json().catch(() => [])
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json([], { status: 200 })
  }
}
