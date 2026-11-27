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
import {
  getAgencyClientIdentifierSet,
  hasUnsafePathSegments,
  requireAgencyBffAuth,
  verifyAgencyClientAccess,
} from '@/lib/bff-auth'

const _BASE =
  process.env.AGENCY_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'https://ecommerce-tracking-ia-production.up.railway.app'

function extractClientRef(
  segments: string[],
  req: NextRequest,
  body?: unknown,
): string | null {
  if (segments[0] === 'clients' && segments[1]) {
    return segments[1]
  }
  const queryClient = req.nextUrl.searchParams.get('client_id')
  if (queryClient) return queryClient
  if (body && typeof body === 'object' && 'client_id' in body) {
    const raw = (body as { client_id?: unknown }).client_id
    if (typeof raw === 'string' && raw.trim()) return raw.trim()
  }
  return null
}

function isClientItemAllowed(item: unknown, allowed: Set<string>): boolean {
  if (!item || typeof item !== 'object') return false
  const rec = item as Record<string, unknown>
  return ['id', 'pixel_id', 'client_id', 'slug'].some((k) => {
    const v = rec[k]
    return typeof v === 'string' && allowed.has(v)
  })
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ segments: string[] }> },
) {
  const { segments } = await params
  if (hasUnsafePathSegments(segments)) {
    return NextResponse.json({ error: 'Invalid path' }, { status: 400 })
  }
  if (segments[0] === 'admin' || segments[segments.length - 1] === 'test-email') {
    return NextResponse.json({ error: 'Forbidden' }, { status: 403 })
  }

  // ── 1. Session + agency_members + client_id guard ───────────────────────────
  const clientRef = extractClientRef(segments, req)
  const check = await requireAgencyBffAuth({ clientRef })
  if (!check.ok) return check.response

  // ── 2. Build upstream URL ───────────────────────────────────────────────────
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
    if (res.ok && segments.length === 1 && segments[0] === 'clients' && Array.isArray(body)) {
      const allowed = await getAgencyClientIdentifierSet(check.auth.agencyId)
      return NextResponse.json(
        body.filter((item) => isClientItemAllowed(item, allowed)),
        { status: res.status },
      )
    }
    return NextResponse.json(body, { status: res.status })
  } catch {
    return NextResponse.json({ error: 'upstream_unavailable' }, { status: 503 })
  }
}

// POST — used for lifecycle transitions (report-narratives, recommendations, etc.)
// actor is injected server-side from the authenticated user session; never from the browser.
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ segments: string[] }> },
) {
  const { segments } = await params
  if (hasUnsafePathSegments(segments)) {
    return NextResponse.json({ error: 'Invalid path' }, { status: 400 })
  }
  if (segments[0] === 'admin' || segments[segments.length - 1] === 'test-email') {
    return NextResponse.json({ error: 'Forbidden' }, { status: 403 })
  }

  const clientBody = await req.json().catch(() => ({}))
  const clientRef = extractClientRef(segments, req, clientBody)
  const check = await requireAgencyBffAuth({ clientRef })
  if (!check.ok) return check.response

  const path = segments.join('/')
  const key  = process.env.AGENCY_API_PLATFORM_KEY || ''

  const upstream = new URL(`/agency/v1/${path}`, _BASE)
  req.nextUrl.searchParams.forEach((v, k) => upstream.searchParams.set(k, v))

  const idempotencyKey = req.headers.get('idempotency-key') || crypto.randomUUID()

  // Inject actor from session — prevents impersonation from browser
  const actor = check.auth.user.email || check.auth.user.id
  const mergedBody = typeof clientBody === 'object' && clientBody !== null
    ? { ...clientBody, actor }
    : clientBody

  try {
    const res = await fetch(upstream.toString(), {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${key}`,
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify(mergedBody),
      cache: 'no-store',
    })
    const data = await res.json().catch(() => null)
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json({ error: 'upstream_unavailable' }, { status: 503 })
  }
}

export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ segments: string[] }> },
) {
  const { segments } = await params
  if (hasUnsafePathSegments(segments)) {
    return NextResponse.json({ error: 'Invalid path' }, { status: 400 })
  }
  if (segments[0] === 'admin' || segments[segments.length - 1] === 'test-email') {
    return NextResponse.json({ error: 'Forbidden' }, { status: 403 })
  }

  const body = await req.json().catch(() => null)
  const clientRef = extractClientRef(segments, req, body)
  const check = await requireAgencyBffAuth({ clientRef })
  if (!check.ok) return check.response

  // Double-check client_id if passed in both path and body (must match agency scope)
  if (
    body &&
    typeof body === 'object' &&
    'client_id' in body &&
    typeof (body as { client_id?: unknown }).client_id === 'string'
  ) {
    const ok = await verifyAgencyClientAccess(
      check.auth.agencyId,
      String((body as { client_id: string }).client_id),
    )
    if (!ok) {
      return NextResponse.json({ error: 'Forbidden: client scope denied' }, { status: 403 })
    }
  }

  // ── 2. Build upstream URL ───────────────────────────────────────────────────
  const path = segments.join('/')
  const key  = process.env.AGENCY_API_PLATFORM_KEY || ''

  const upstream = new URL(`/agency/v1/${path}`, _BASE)
  req.nextUrl.searchParams.forEach((v, k) => upstream.searchParams.set(k, v))

  // ── 3. Forward with PUT + body ──────────────────────────────────────────────
  const idempotencyKey = req.headers.get('idempotency-key') || crypto.randomUUID()

  try {
    const res = await fetch(upstream.toString(), {
      method: 'PUT',
      headers: {
        Authorization: `Bearer ${key}`,
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify(body),
      cache: 'no-store',
    })
    const data = await res.json().catch(() => null)
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json({ error: 'upstream_unavailable' }, { status: 503 })
  }
}
