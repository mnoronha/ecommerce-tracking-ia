/**
 * Server-side BFF authentication & multi-tenant authorization helpers (PRD §21.2 S4).
 *
 * Enforces:
 *   1. Valid Supabase Auth session (`supabase.auth.getUser()`) -> 401
 *   2. Active membership in `agency_members` (`owner | admin | member`) -> 403
 *   3. Server-side client scope authorization (`clients.agency_id === member.agencyId`) -> 403
 *   4. Signed OAuth state tokens (HMAC-SHA256) to prevent clientId/redirect tampering
 */
import { createHmac, timingSafeEqual } from 'crypto'
import { createClient } from '@supabase/supabase-js'
import { NextResponse } from 'next/server'
import type { User } from '@supabase/supabase-js'
import { createSupabaseServerClient } from '@/lib/supabase-server'

export type AgencyRole = 'owner' | 'admin' | 'member'

export interface BffAgencyAuth {
  user: User
  agencyId: string
  role: AgencyRole
}

export type BffAuthResult =
  | { ok: true; auth: BffAgencyAuth }
  | { ok: false; response: NextResponse }

const ALL_AGENCY_ROLES: AgencyRole[] = ['owner', 'admin', 'member']
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const SAFE_IDENT_RE = /^[a-zA-Z0-9._-]+$/

function getAdminClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY
  if (!url || !key) return null
  return createClient(url, key, {
    auth: { autoRefreshToken: false, persistSession: false },
  })
}

/**
 * Verify that a request comes from an authenticated user who is a member of
 * `agency_members` with one of `allowedRoles` (defaults to owner/admin/member).
 *
 * When `clientRef` is provided (UUID, pixel_id, or canonical client_id slug),
 * also verifies that the referenced client belongs to the user's `agency_id`.
 */
export async function requireAgencyBffAuth(options?: {
  allowedRoles?: AgencyRole[]
  clientRef?: string | null
}): Promise<BffAuthResult> {
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) {
    return {
      ok: false,
      response: NextResponse.json({ error: 'Unauthorized' }, { status: 401 }),
    }
  }

  const allowedRoles = options?.allowedRoles ?? ALL_AGENCY_ROLES

  const { data: member } = await supabase
    .from('agency_members')
    .select('agency_id, role')
    .eq('user_id', user.id)
    .in('role', allowedRoles)
    .limit(1)
    .maybeSingle()

  if (!member?.agency_id || !member?.role) {
    return {
      ok: false,
      response: NextResponse.json(
        { error: 'Forbidden: agency membership required' },
        { status: 403 },
      ),
    }
  }

  const auth: BffAgencyAuth = {
    user,
    agencyId: String(member.agency_id),
    role: member.role as AgencyRole,
  }

  if (options?.clientRef) {
    const allowed = await verifyAgencyClientAccess(auth.agencyId, options.clientRef)
    if (!allowed) {
      return {
        ok: false,
        response: NextResponse.json(
          { error: 'Forbidden: client scope denied' },
          { status: 403 },
        ),
      }
    }
  }

  return { ok: true, auth }
}

/**
 * Verify that `clientRef` (UUID `id`, `pixel_id`, or slug `client_id`) exists
 * and belongs to `agencyId`.
 */
export async function verifyAgencyClientAccess(
  agencyId: string,
  clientRef: string,
): Promise<boolean> {
  const ref = clientRef.trim()
  if (!ref || !SAFE_IDENT_RE.test(ref)) {
    return false
  }

  const db = getAdminClient() ?? (await createSupabaseServerClient())
  const filter = UUID_RE.test(ref)
    ? `id.eq.${ref},pixel_id.eq.${ref},client_id.eq.${ref}`
    : `pixel_id.eq.${ref},client_id.eq.${ref}`

  const { data, error } = await db
    .from('clients')
    .select('id, agency_id')
    .eq('agency_id', agencyId)
    .or(filter)
    .limit(1)
    .maybeSingle()

  if (error || !data) {
    return false
  }
  return String(data.agency_id) === agencyId
}

/**
 * Return the set of all client identifiers (id, pixel_id, client_id) belonging
 * to `agencyId` for server-side list filtering.
 */
export async function getAgencyClientIdentifierSet(agencyId: string): Promise<Set<string>> {
  const db = getAdminClient() ?? (await createSupabaseServerClient())
  const { data } = await db
    .from('clients')
    .select('id, pixel_id, client_id')
    .eq('agency_id', agencyId)

  const ids = new Set<string>()
  for (const row of data ?? []) {
    if (row.id) ids.add(String(row.id))
    if (row.pixel_id) ids.add(String(row.pixel_id))
    if (row.client_id) ids.add(String(row.client_id))
  }
  return ids
}

/**
 * Verify that a backend resource (`recommendation`, `learning_candidate`, or
 * `canonical_report`) either does not exist (letting upstream return 404) or
 * belongs to a client owned by `agencyId` (returning false -> 403 on cross-agency access).
 */
export async function verifyAgencyResourceAccess(
  agencyId: string,
  resourceType: 'recommendation' | 'learning_candidate' | 'canonical_report',
  resourceId: string,
): Promise<boolean> {
  const id = resourceId.trim()
  if (!id || !SAFE_IDENT_RE.test(id)) {
    return false
  }

  const admin = getAdminClient()
  if (!admin) {
    // Cannot resolve service-only core tables without service key; agency_members check already passed.
    return true
  }

  let clientRef: string | null = null

  if (resourceType === 'recommendation') {
    const { data } = await admin
      .from('core_recommendations')
      .select('client_id')
      .eq('id', id)
      .limit(1)
      .maybeSingle()
    if (!data) return true // let upstream return 404
    clientRef = data.client_id ? String(data.client_id) : null
  } else if (resourceType === 'learning_candidate') {
    const { data } = await admin
      .from('core_learning_candidates')
      .select('client_id')
      .eq('id', id)
      .limit(1)
      .maybeSingle()
    if (!data) return true // let upstream return 404
    // Agency-wide learning candidates have client_id === null
    if (!data.client_id) return true
    clientRef = String(data.client_id)
  } else if (resourceType === 'canonical_report') {
    const { data } = await admin
      .from('core_canonical_report_records')
      .select('document')
      .like('entity_key', `report:${id}:v%`)
      .limit(1)
      .maybeSingle()
    if (!data) return true // let upstream return 404
    const doc = data.document as { client_id?: string } | null
    clientRef = doc?.client_id ? String(doc.client_id) : null
  }

  if (!clientRef) return false
  return verifyAgencyClientAccess(agencyId, clientRef)
}

/**
 * Validate dynamic catch-all proxy segments to prevent path traversal (`..`).
 */
export function hasUnsafePathSegments(segments: string[]): boolean {
  if (!segments.length) return true
  return segments.some((seg) => {
    const s = seg.trim()
    if (!s || s === '.' || s === '..') return true
    if (s.includes('/') || s.includes('\\') || s.includes('..')) return true
    try {
      const decoded = decodeURIComponent(s)
      if (decoded === '.' || decoded === '..' || decoded.includes('/') || decoded.includes('\\')) {
        return true
      }
    } catch {
      return true
    }
    return false
  })
}

// ── HMAC-signed OAuth state helpers ───────────────────────────────────────────

function getOAuthStateSecret(): string {
  return (
    process.env.OAUTH_STATE_SECRET ||
    process.env.AGENCY_API_PLATFORM_KEY ||
    process.env.SUPABASE_SERVICE_ROLE_KEY ||
    ''
  )
}

export function signOAuthState(payload: { c: string; n: string; r: 'settings' | 'onboarding' }): string {
  const encoded = Buffer.from(JSON.stringify(payload)).toString('base64url')
  const secret = getOAuthStateSecret()
  if (!secret) {
    throw new Error('OAuth state signing secret is not configured')
  }
  const sig = createHmac('sha256', secret).update(encoded).digest('base64url')
  return `${encoded}.${sig}`
}

export function verifyOAuthState(
  token: string,
): { c: string; n: string; r: 'settings' | 'onboarding' } | null {
  const parts = token.split('.')
  if (parts.length !== 2) return null
  const [encoded, providedSig] = parts
  if (!encoded || !providedSig) return null

  const secret = getOAuthStateSecret()
  if (!secret) return null

  const expectedSig = createHmac('sha256', secret).update(encoded).digest('base64url')
  const a = Buffer.from(providedSig)
  const b = Buffer.from(expectedSig)
  if (a.length !== b.length || !timingSafeEqual(a, b)) {
    return null
  }

  try {
    const parsed = JSON.parse(Buffer.from(encoded, 'base64url').toString('utf8'))
    const c = typeof parsed?.c === 'string' ? parsed.c.trim() : ''
    const n = typeof parsed?.n === 'string' ? parsed.n.trim() : ''
    const r = parsed?.r === 'onboarding' ? 'onboarding' : 'settings'
    if (!c || !n || !SAFE_IDENT_RE.test(c)) return null
    return { c, n, r }
  } catch {
    return null
  }
}
