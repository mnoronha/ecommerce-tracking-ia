/**
 * BFF: POST /api/v1/recommendations/{recommendationId}/decision
 *
 * Human decision transport for the Agency OS recommendation workflow.
 *
 * Security invariants:
 *   - Requires a valid Supabase session (auth guard).
 *   - AGENCY_API_PLATFORM_KEY is read server-side only; never sent to the browser.
 *   - `actor` is derived from the authenticated user's email — never client-supplied.
 *   - Forwards to Agency API with PLATFORM scope; hermes_service is excluded at Core layer.
 *   - No external writes to Google Ads / Meta Ads occur here or downstream.
 */
import type { NextRequest } from 'next/server'
import { NextResponse }      from 'next/server'
import { requireAgencyBffAuth, verifyAgencyResourceAccess } from '@/lib/bff-auth'

const _BASE =
  process.env.AGENCY_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'https://ecommerce-tracking-ia-production.up.railway.app'

const VALID_DECISIONS = new Set(['ACCEPTED', 'REJECTED', 'DEFERRED'])

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ recommendationId: string }> },
) {
  // ── 1. Session + agency_members + resource tenant guard ──────────────────────
  const check = await requireAgencyBffAuth()
  if (!check.ok) return check.response

  const { recommendationId } = await params
  const allowed = await verifyAgencyResourceAccess(
    check.auth.agencyId,
    'recommendation',
    recommendationId,
  )
  if (!allowed) {
    return NextResponse.json({ error: 'Forbidden: client scope denied' }, { status: 403 })
  }
  const { user } = check.auth

  // ── 2. Parse and validate request body ───────────────────────────────────────
  let body: { decision?: unknown; reason?: unknown; idempotency_key?: unknown }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'invalid_body' }, { status: 422 })
  }

  const decision = typeof body.decision === 'string' ? body.decision : ''
  if (!VALID_DECISIONS.has(decision)) {
    return NextResponse.json(
      { error: 'invalid_decision', allowed: ['ACCEPTED', 'REJECTED', 'DEFERRED'] },
      { status: 422 },
    )
  }

  const reason         = typeof body.reason === 'string' && body.reason.trim() ? body.reason.trim() : null
  const idempotencyKey = typeof body.idempotency_key === 'string' ? body.idempotency_key : null

  // ── 3. Derive actor from authenticated user ───────────────────────────────────
  const actor = user.email || user.id

  // ── 4. Forward to Agency API (server-side PLATFORM key) ──────────────────────
  const platformKey = process.env.AGENCY_API_PLATFORM_KEY || ''
  const upstream    = `${_BASE}/agency/v1/recommendations/${recommendationId}/decision`

  const headers: Record<string, string> = {
    Authorization:  `Bearer ${platformKey}`,
    'Content-Type': 'application/json',
  }
  if (idempotencyKey) {
    headers['Idempotency-Key'] = idempotencyKey
  }

  try {
    const res  = await fetch(upstream, {
      method:  'POST',
      headers,
      body:    JSON.stringify({ decision, actor, reason }),
      cache:   'no-store',
    })
    const data = await res.json().catch(() => null)
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json({ error: 'upstream_unavailable' }, { status: 503 })
  }
}
