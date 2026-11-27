/**
 * Dedicated BFF proxy for internal test email sending (PRD §16.3 / R02).
 *
 * Security invariants:
 *   - Requires authenticated Supabase session AND active `agency_members` membership
 *     AND client ownership (`requireAgencyBffAuth` + `verifyAgencyResourceAccess`).
 *   - Rejects any caller-supplied external recipient / cc / bcc (allowlist is
 *     strictly `contato@norolabs.com.br`, enforced both here and in FastAPI + DB).
 *   - Derives `actor` exclusively from the authenticated user session.
 *   - Forwards `Idempotency-Key` to prevent duplicate sends on double-clicks.
 */
import type { NextRequest } from 'next/server'
import { NextResponse } from 'next/server'
import { requireAgencyBffAuth, verifyAgencyResourceAccess } from '@/lib/bff-auth'

const _BASE =
  process.env.AGENCY_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'https://ecommerce-tracking-ia-production.up.railway.app'

const ALLOWED_TEST_RECIPIENT = 'contato@norolabs.com.br'

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ reportId: string }> },
) {
  let body: Record<string, unknown>
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'invalid_body' }, { status: 422 })
  }

  const version = typeof body.version === 'number' ? Math.trunc(body.version) : Number(body.version)
  const clientId = typeof body.client_id === 'string' ? body.client_id.trim() : ''

  if (!Number.isInteger(version) || version < 1 || !clientId) {
    return NextResponse.json(
      { error: 'version (>=1) and client_id are required' },
      { status: 422 },
    )
  }

  // Reject any attempt to redirect test email to an external address or add cc/bcc
  if (body.cc !== undefined || body.bcc !== undefined) {
    return NextResponse.json({ error: 'INTERNAL_TEST_RECIPIENT_ONLY' }, { status: 403 })
  }
  for (const field of ['recipient', 'to', 'email'] as const) {
    const val = body[field]
    if (val !== undefined && val !== null) {
      if (typeof val !== 'string' || val.trim().toLowerCase() !== ALLOWED_TEST_RECIPIENT) {
        return NextResponse.json({ error: 'INTERNAL_TEST_RECIPIENT_ONLY' }, { status: 403 })
      }
    }
  }

  // 1. Session + agency_members + client_id scope check
  const check = await requireAgencyBffAuth({ clientRef: clientId })
  if (!check.ok) return check.response

  // 2. Resource ownership check on reportId
  const { reportId } = await params
  const resourceOk = await verifyAgencyResourceAccess(
    check.auth.agencyId,
    'canonical_report',
    reportId,
  )
  if (!resourceOk) {
    return NextResponse.json({ error: 'Forbidden: client scope denied' }, { status: 403 })
  }

  // 3. Server-derived actor & Idempotency-Key
  const actor = check.auth.user.email || check.auth.user.id
  const idempotencyKey =
    req.headers.get('idempotency-key') ||
    req.headers.get('Idempotency-Key') ||
    crypto.randomUUID()

  const platformKey = process.env.AGENCY_API_PLATFORM_KEY || ''
  const upstream = new URL(
    `/agency/v1/canonical-reports/${encodeURIComponent(reportId)}/versions/${version}/test-email`,
    _BASE,
  )

  try {
    const res = await fetch(upstream.toString(), {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${platformKey}`,
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify({ client_id: clientId, actor }),
      cache: 'no-store',
    })
    const data = await res.json().catch(() => null)
    const response = NextResponse.json(data, { status: res.status })
    const retryAfter = res.headers.get('retry-after')
    if (retryAfter) {
      response.headers.set('Retry-After', retryAfter)
    }
    return response
  } catch {
    return NextResponse.json({ error: 'upstream_unavailable' }, { status: 503 })
  }
}
