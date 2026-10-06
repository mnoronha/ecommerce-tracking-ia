/**
 * Generic read-only BFF proxy for Agency API (/agency/v1/*).
 *
 * Browser → POST /api/v1/clients/lk-sneakers/alerts
 *        is NOT forwarded (POST disallowed at proxy level).
 *
 * Auth: validates Supabase session; forwards to Agency API
 *       with AGENCY_API_PLATFORM_KEY (never exposed to browser).
 */
import type { NextRequest } from 'next/server'
import { NextResponse }      from 'next/server'
import { createSupabaseServerClient } from '@/lib/supabase-server'

const _BASE =
  process.env.AGENCY_API_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'https://ecommerce-tracking-ia-production.up.railway.app'

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ segments: string[] }> },
) {
  // ── 1. Session guard ────────────────────────────────────────────────────────
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  // ── 2. Build upstream URL ───────────────────────────────────────────────────
  const { segments } = await params
  const path = segments.join('/')
  const key  = process.env.AGENCY_API_PLATFORM_KEY || ''

  const upstream = new URL(`/agency/v1/${path}`, _BASE)
  req.nextUrl.searchParams.forEach((v, k) => upstream.searchParams.set(k, v))

  // ── 3. Forward ──────────────────────────────────────────────────────────────
  try {
    const res  = await fetch(upstream.toString(), {
      headers: { Authorization: `Bearer ${key}` },
      cache: 'no-store',
    })
    const body = await res.json().catch(() => null)
    return NextResponse.json(body, { status: res.status })
  } catch {
    return NextResponse.json({ error: 'upstream_unavailable' }, { status: 503 })
  }
}
