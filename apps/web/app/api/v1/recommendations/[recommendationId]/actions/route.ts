/**
 * BFF: POST /api/v1/recommendations/{recommendationId}/actions
 *
 * Creates a structured Action record for an accepted recommendation.
 *
 * Security invariants:
 *   - Requires valid Supabase session (auth guard).
 *   - AGENCY_API_PLATFORM_KEY read server-side only; never sent to the browser.
 *   - No writes to Google Ads / Meta Ads occur here or downstream.
 *   - requires_external_write must be explicitly set by the caller; Core never
 *     auto-executes any external platform write.
 */
import type { NextRequest } from 'next/server'
import { NextResponse }      from 'next/server'
import { createSupabaseServerClient } from '@/lib/supabase-server'

const _BASE =
  process.env.AGENCY_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'https://ecommerce-tracking-ia-production.up.railway.app'

const VALID_ACTION_TYPES = new Set([
  'MANUAL_EXTERNAL_ACTION',
  'INTERNAL_ACTION',
  'NO_ACTION_REQUIRED',
])

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ recommendationId: string }> },
) {
  // ── 1. Session guard ──────────────────────────────────────────────────────────
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const { recommendationId } = await params

  // ── 2. Parse and validate request body ───────────────────────────────────────
  let body: Record<string, unknown>
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'invalid_body' }, { status: 422 })
  }

  const action_type = typeof body.action_type === 'string' ? body.action_type : ''
  if (!VALID_ACTION_TYPES.has(action_type)) {
    return NextResponse.json(
      { error: 'invalid_action_type', allowed: [...VALID_ACTION_TYPES] },
      { status: 422 },
    )
  }

  const idempotencyKey = typeof body.idempotency_key === 'string' ? body.idempotency_key : null

  // ── 3. Forward to Agency API ──────────────────────────────────────────────────
  const platformKey = process.env.AGENCY_API_PLATFORM_KEY || ''
  const upstream    = `${_BASE}/agency/v1/recommendations/${recommendationId}/actions`

  const headers: Record<string, string> = {
    Authorization:  `Bearer ${platformKey}`,
    'Content-Type': 'application/json',
  }
  if (idempotencyKey) {
    headers['Idempotency-Key'] = idempotencyKey
  }

  const payload = {
    action_type,
    platform:               body.platform ?? null,
    entity_type:            body.entity_type ?? null,
    entity_id:              body.entity_id ?? null,
    evaluation_window_days: body.evaluation_window_days ?? 7,
    before_state:           body.before_state ?? {},
    reason:                 body.reason ?? null,
    provenance:             body.provenance ?? {},
    requires_external_write: body.requires_external_write ?? false,
  }

  try {
    const res  = await fetch(upstream, {
      method:  'POST',
      headers,
      body:    JSON.stringify(payload),
      cache:   'no-store',
    })
    const data = await res.json().catch(() => null)
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json({ error: 'upstream_unavailable' }, { status: 503 })
  }
}
