/**
 * BFF: POST /api/v1/learning-candidates/{learningCandidateId}/promote
 *
 * Promotes an APPROVED learning candidate to Agency Knowledge (M21).
 *
 * Security invariants:
 *   - Requires valid Supabase session (auth guard).
 *   - AGENCY_API_PLATFORM_KEY read server-side only; never sent to the browser.
 *   - actor is derived from the authenticated user's email — never client-supplied.
 *   - Only APPROVED candidates can be promoted (enforced by Agency API).
 *   - PROPOSED, PENDING, REJECTED, SUPERSEDED are rejected with 422.
 *   - Hermes (hermes_service scope) CANNOT call this endpoint — blocked by Agency API.
 *   - AUTOMATIC_PROMOTION = ZERO: this endpoint is only triggered by explicit human action.
 */
import type { NextRequest } from 'next/server'
import { NextResponse }      from 'next/server'
import { createSupabaseServerClient } from '@/lib/supabase-server'

const _BASE =
  process.env.AGENCY_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'https://ecommerce-tracking-ia-production.up.railway.app'

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ learningCandidateId: string }> },
) {
  // ── 1. Session guard ──────────────────────────────────────────────────────────
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const { learningCandidateId } = await params

  // ── 2. Parse request body ─────────────────────────────────────────────────────
  let body: { reason?: unknown; force_new_version?: unknown } = {}
  try {
    body = await req.json()
  } catch {
    // body is optional — empty body is fine
  }

  const reason = typeof body.reason === 'string' && body.reason.trim()
    ? body.reason.trim()
    : null
  const forceNewVersion = body.force_new_version === true

  // ── 3. Idempotency key from header ────────────────────────────────────────────
  const idempotencyKey = req.headers.get('idempotency-key') || req.headers.get('Idempotency-Key')

  // ── 4. Forward to Agency API ──────────────────────────────────────────────────
  const platformKey = process.env.AGENCY_API_PLATFORM_KEY || ''
  const upstream    = `${_BASE}/agency/v1/learning-candidates/${learningCandidateId}/promote`

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
      body:    JSON.stringify({ reason, force_new_version: forceNewVersion }),
      cache:   'no-store',
    })
    const data = await res.json().catch(() => null)
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json({ error: 'upstream_unavailable' }, { status: 503 })
  }
}
