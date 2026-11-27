/**
 * BFF: GET /api/v1/action-center
 *
 * Unified daily operations aggregator for Noro Platform Action Center (PL-02 — PRD §7.2).
 *
 * Aggregates from Core's canonical Agency API endpoints (zero duplicated business rules):
 *   (a) Active H6 alerts (OPEN, prioritized HIGH -> MEDIUM -> LOW)
 *   (b) Canonical reports in READY_FOR_REVIEW awaiting review / internal test email
 *   (c) Pending Hermes recommendations (PROPOSED / DEFERRED) for 1-click approve -> Notion task
 *   (d) Open action items extracted from recent meetings (last 3 meetings per client)
 *   (e) Learning candidates (PENDING for human validation + APPROVED awaiting Obsidian promotion)
 *
 * Security invariants (AGENTS.md & PRD §21.2 S4):
 *   - Requires valid Supabase session + active agency_members row (`requireAgencyBffAuth`).
 *   - Enforces server-side client scope (`verifyAgencyClientAccess` when client_id is passed,
 *     or `getAgencyClientIdentifierSet` filtering across authorized agency clients).
 *   - AGENCY_API_PLATFORM_KEY is used server-side only; never exposed to the browser.
 */
import type { NextRequest } from 'next/server'
import { NextResponse } from 'next/server'
import { createClient } from '@supabase/supabase-js'
import { createSupabaseServerClient } from '@/lib/supabase-server'
import { requireAgencyBffAuth } from '@/lib/bff-auth'

const _BASE =
  process.env.AGENCY_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'https://ecommerce-tracking-ia-production.up.railway.app'

interface ClientRow {
  id: string
  client_id: string
  pixel_id: string | null
  name: string | null
}

function getAdminClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY
  if (!url || !key) return null
  return createClient(url, key, {
    auth: { autoRefreshToken: false, persistSession: false },
  })
}

async function listAuthorizedClients(
  agencyId: string,
  clientRef?: string | null,
): Promise<ClientRow[]> {
  const db = getAdminClient() ?? (await createSupabaseServerClient())
  const { data } = await db
    .from('clients')
    .select('id, client_id, pixel_id, name')
    .eq('agency_id', agencyId)
    .order('name', { ascending: true })

  const all: ClientRow[] = (data ?? []).map((r) => ({
    id: String(r.id),
    client_id: String(r.client_id || r.pixel_id || r.id),
    pixel_id: r.pixel_id ? String(r.pixel_id) : null,
    name: r.name ? String(r.name) : String(r.client_id || r.pixel_id || r.id),
  }))

  if (!clientRef) return all
  const ref = clientRef.trim()
  return all.filter(
    (c) => c.id === ref || c.client_id === ref || (c.pixel_id !== null && c.pixel_id === ref),
  )
}

async function fetchCoreJson<T>(
  path: string,
  searchParams?: Record<string, string | undefined>,
): Promise<{ ok: true; data: T } | { ok: false; error: string }> {
  const key = process.env.AGENCY_API_PLATFORM_KEY || ''
  const url = new URL(`/agency/v1/${path}`, _BASE)
  if (searchParams) {
    for (const [k, v] of Object.entries(searchParams)) {
      if (v !== undefined && v !== null && v !== '') {
        url.searchParams.set(k, v)
      }
    }
  }
  try {
    const res = await fetch(url.toString(), {
      headers: { Authorization: `Bearer ${key}` },
      cache: 'no-store',
    })
    if (!res.ok) {
      return { ok: false, error: `HTTP ${res.status}` }
    }
    const data = (await res.json()) as T
    return { ok: true, data }
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : 'upstream_unavailable',
    }
  }
}

const SEV_RANK: Record<string, number> = { HIGH: 0, CRITICAL: 0, MEDIUM: 1, LOW: 2 }
const PRIO_RANK: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 }

export async function GET(req: NextRequest) {
  const clientRef = req.nextUrl.searchParams.get('client_id')?.trim() || null
  const check = await requireAgencyBffAuth({ clientRef })
  if (!check.ok) return check.response

  const clients = await listAuthorizedClients(check.auth.agencyId, clientRef)
  if (clientRef && clients.length === 0) {
    return NextResponse.json({ error: 'Forbidden: client scope denied' }, { status: 403 })
  }

  const sectionErrors: Record<string, string[]> = {
    alerts: [],
    reports: [],
    recommendations: [],
    meeting_action_items: [],
    learning_candidates: [],
  }

  // Cap fan-out safely for agency-wide view while covering all active portfolio clients
  const targetClients = clients.slice(0, 12)

  const alertsOut: Array<Record<string, unknown>> = []
  const reportsOut: Array<Record<string, unknown>> = []
  const recsOut: Array<Record<string, unknown>> = []
  const meetingItemsOut: Array<Record<string, unknown>> = []
  const candidatesOut: Array<Record<string, unknown>> = []

  await Promise.all(
    targetClients.map(async (client) => {
      const slug = client.client_id
      const clientMeta = {
        client_id: slug,
        client_uuid: client.id,
        client_name: client.name || slug,
      }

      const [alertsRes, reportsRes, recsRes, meetingsRes, candidatesRes] = await Promise.all([
        fetchCoreJson<Array<Record<string, unknown>>>(`clients/${encodeURIComponent(slug)}/alerts`, {
          status: 'OPEN',
          limit: '30',
        }),
        fetchCoreJson<Array<Record<string, unknown>>>('canonical-reports/list', {
          client_id: slug,
          status: 'READY_FOR_REVIEW',
          limit: '10',
        }),
        fetchCoreJson<Array<Record<string, unknown>>>(
          `clients/${encodeURIComponent(slug)}/recommendations`,
          { status: 'PROPOSED', limit: '25' },
        ),
        fetchCoreJson<{
          open_action_items?: Array<Record<string, unknown>>
          recent_decisions?: Array<Record<string, unknown>>
        }>(`clients/${encodeURIComponent(slug)}/meeting-context`, { limit: '3' }),
        fetchCoreJson<Array<Record<string, unknown>>>(
          `clients/${encodeURIComponent(slug)}/learning-candidates`,
          { limit: '25' },
        ),
      ])

      if (alertsRes.ok && Array.isArray(alertsRes.data)) {
        for (const a of alertsRes.data) {
          alertsOut.push({ ...a, ...clientMeta })
        }
      } else if (!alertsRes.ok) {
        sectionErrors.alerts.push(`${slug}: ${alertsRes.error}`)
      }

      if (reportsRes.ok && Array.isArray(reportsRes.data)) {
        for (const r of reportsRes.data) {
          reportsOut.push({
            report_id: r.report_id,
            version: r.version,
            report_type: r.report_type,
            status: r.status,
            period_start: r.period_start,
            period_end: r.period_end,
            generated_at: r.generated_at,
            quality_scores: r.quality_scores ?? null,
            ...clientMeta,
          })
        }
      } else if (!reportsRes.ok) {
        sectionErrors.reports.push(`${slug}: ${reportsRes.error}`)
      }

      if (recsRes.ok && Array.isArray(recsRes.data)) {
        for (const rec of recsRes.data) {
          recsOut.push({ ...rec, ...clientMeta })
        }
      } else if (!recsRes.ok) {
        sectionErrors.recommendations.push(`${slug}: ${recsRes.error}`)
      }

      if (meetingsRes.ok && meetingsRes.data) {
        for (const item of meetingsRes.data.open_action_items ?? []) {
          meetingItemsOut.push({ ...item, ...clientMeta })
        }
      } else if (!meetingsRes.ok) {
        sectionErrors.meeting_action_items.push(`${slug}: ${meetingsRes.error}`)
      }

      if (candidatesRes.ok && Array.isArray(candidatesRes.data)) {
        for (const cand of candidatesRes.data) {
          const st = String(cand.status || '').toUpperCase()
          if (st === 'PENDING' || st === 'APPROVED') {
            candidatesOut.push({ ...cand, ...clientMeta })
          }
        }
      } else if (!candidatesRes.ok) {
        sectionErrors.learning_candidates.push(`${slug}: ${candidatesRes.error}`)
      }
    }),
  )

  // Deterministic priority sorting
  alertsOut.sort((a, b) => {
    const sa = SEV_RANK[String(a.severity || '').toUpperCase()] ?? 9
    const sb = SEV_RANK[String(b.severity || '').toUpperCase()] ?? 9
    if (sa !== sb) return sa - sb
    return String(b.detected_at || '').localeCompare(String(a.detected_at || ''))
  })

  reportsOut.sort((a, b) =>
    String(b.period_end || b.generated_at || '').localeCompare(
      String(a.period_end || a.generated_at || ''),
    ),
  )

  recsOut.sort((a, b) => {
    const pa = PRIO_RANK[String(a.priority || '').toUpperCase()] ?? 9
    const pb = PRIO_RANK[String(b.priority || '').toUpperCase()] ?? 9
    if (pa !== pb) return pa - pb
    return String(b.created_at || '').localeCompare(String(a.created_at || ''))
  })

  candidatesOut.sort((a, b) => {
    // PENDING first, then APPROVED awaiting Obsidian promotion
    const sa = String(a.status || '') === 'PENDING' ? 0 : 1
    const sb = String(b.status || '') === 'PENDING' ? 0 : 1
    if (sa !== sb) return sa - sb
    return String(b.created_at || '').localeCompare(String(a.created_at || ''))
  })

  return NextResponse.json({
    generated_at: new Date().toISOString(),
    scope: clientRef ? 'client' : 'agency',
    client_id: clientRef,
    clients: targetClients.map((c) => ({
      client_id: c.client_id,
      client_uuid: c.id,
      name: c.name || c.client_id,
    })),
    counts: {
      alerts: alertsOut.length,
      critical_alerts: alertsOut.filter(
        (a) => String(a.severity || '').toUpperCase() === 'HIGH',
      ).length,
      reports_ready_for_review: reportsOut.length,
      pending_recommendations: recsOut.length,
      meeting_action_items: meetingItemsOut.length,
      learning_candidates_pending: candidatesOut.filter(
        (c) => String(c.status || '').toUpperCase() === 'PENDING',
      ).length,
      learning_candidates_approved: candidatesOut.filter(
        (c) => String(c.status || '').toUpperCase() === 'APPROVED',
      ).length,
    },
    alerts: alertsOut,
    reports_ready_for_review: reportsOut,
    pending_recommendations: recsOut,
    meeting_action_items: meetingItemsOut,
    learning_candidates: candidatesOut,
    section_errors: Object.fromEntries(
      Object.entries(sectionErrors).filter(([, errs]) => errs.length > 0),
    ),
  })
}
