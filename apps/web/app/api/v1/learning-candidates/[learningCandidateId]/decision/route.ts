/**
 * BFF: POST /api/v1/learning-candidates/{learningCandidateId}/decision
 *
 * Human approval / rejection of a learning candidate.
 *
 * Security invariants:
 *   - Requires valid Supabase session (auth guard).
 *   - AGENCY_API_PLATFORM_KEY read server-side only; never sent to the browser.
 *   - actor is derived from the authenticated user's email — never client-supplied.
 *   - Automatic promotion to Agency Knowledge is NEVER performed here.
 *   - APPROVED status in Core means the human reviewed it; it does NOT mean
 *     it has been promoted to the Agency Knowledge base (requires a separate,
 *     manual governance step).
 */
import type { NextRequest } from 'next/server'
import { NextResponse }      from 'next/server'
import { createSupabaseServerClient } from '@/lib/supabase-server'

const _BASE =
  process.env.AGENCY_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'https://ecommerce-tracking-ia-production.up.railway.app'

const VALID_DECISIONS = new Set(['APPROVED', 'REJECTED', 'SUPERSEDED'])

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

  // ── 2. Parse and validate request body ───────────────────────────────────────
  let body: { decision?: unknown; note?: unknown; idempotency_key?: unknown }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'invalid_body' }, { status: 422 })
  }

  const decision = typeof body.decision === 'string' ? body.decision : ''
  if (!VALID_DECISIONS.has(decision)) {
    return NextResponse.json(
      { error: 'invalid_decision', allowed: [...VALID_DECISIONS] },
      { status: 422 },
    )
  }

  const note           = typeof body.note === 'string' && body.note.trim() ? body.note.trim() : null
  const idempotencyKey = typeof body.idempotency_key === 'string' ? body.idempotency_key : null

  // ── 3. Derive actor from authenticated user ───────────────────────────────────
  const actor = user.email || user.id

  // ── 4. Forward to Agency API ──────────────────────────────────────────────────
  const platformKey = process.env.AGENCY_API_PLATFORM_KEY || ''
  const upstream    = `${_BASE}/agency/v1/learning-candidates/${learningCandidateId}/decision`

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
      body:    JSON.stringify({ decision, actor, note }),
      cache:   'no-store',
    })
    const data = await res.json().catch(() => null)
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json({ error: 'upstream_unavailable' }, { status: 503 })
  }
}
