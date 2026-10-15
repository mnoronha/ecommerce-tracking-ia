/**
 * Finance proxy — server-side only.
 *
 * Auth: validates Supabase session AND checks owner role in agency_members.
 * Forwards to backend /finance/* with AGENCY_API_ADMIN_KEY (never exposed to browser).
 *
 * Required env vars (server-side):
 *   AGENCY_API_ADMIN_KEY  — admin Bearer token for the finance endpoints
 *   NEXT_PUBLIC_API_URL   — backend base URL (fallback)
 */
import type { NextRequest } from 'next/server'
import { NextResponse }      from 'next/server'
import { createSupabaseServerClient } from '@/lib/supabase-server'

const _BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  'https://ecommerce-tracking-ia-production.up.railway.app'

async function _proxyFinance(
  req: NextRequest,
  slug: string[],
  method: string,
): Promise<NextResponse> {
  // ── 1. Session guard ────────────────────────────────────────────────────────
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  // ── 2. Owner guard — finance is owner-only ──────────────────────────────────
  const { data: ownerRow } = await supabase
    .from('agency_members')
    .select('role')
    .eq('user_id', user.id)
    .eq('role', 'owner')
    .maybeSingle()
  if (!ownerRow) {
    return NextResponse.json({ error: 'Forbidden' }, { status: 403 })
  }

  // ── 3. Build upstream URL ───────────────────────────────────────────────────
  const path     = slug.join('/')
  const adminKey = process.env.AGENCY_API_ADMIN_KEY || ''
  const upstream = new URL(`/finance/${path}`, _BASE)
  req.nextUrl.searchParams.forEach((v, k) => upstream.searchParams.set(k, v))

  // ── 4. Forward body if present ──────────────────────────────────────────────
  let body: BodyInit | undefined
  if (method !== 'GET' && method !== 'DELETE') {
    const text = await req.text()
    if (text) body = text
  }

  // ── 5. Proxy ────────────────────────────────────────────────────────────────
  try {
    const res = await fetch(upstream.toString(), {
      method,
      headers: {
        Authorization: `Bearer ${adminKey}`,
        'Content-Type': 'application/json',
      },
      body,
      cache: 'no-store',
    })
    const data = await res.json().catch(() => null)
    if (data === null) {
      return new NextResponse(null, { status: res.status })
    }
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json({ error: 'upstream_unavailable' }, { status: 503 })
  }
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ slug: string[] }> },
) {
  const { slug } = await params
  return _proxyFinance(req, slug, 'GET')
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ slug: string[] }> },
) {
  const { slug } = await params
  return _proxyFinance(req, slug, 'POST')
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ slug: string[] }> },
) {
  const { slug } = await params
  return _proxyFinance(req, slug, 'PATCH')
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ slug: string[] }> },
) {
  const { slug } = await params
  return _proxyFinance(req, slug, 'DELETE')
}
