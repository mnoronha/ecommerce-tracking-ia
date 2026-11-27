import { NextResponse }               from 'next/server'
import { listClients, ReportError }   from '@/lib/agency-os'
import { getAgencyClientIdentifierSet, requireAgencyBffAuth } from '@/lib/bff-auth'

export async function GET() {
  // ── Auth + agency_members guard ─────────────────────────────────────────────
  const check = await requireAgencyBffAuth()
  if (!check.ok) return check.response

  // ── Proxy (filtered to this agency's clients) ───────────────────────────────
  try {
    const clients = await listClients()
    const allowed = await getAgencyClientIdentifierSet(check.auth.agencyId)
    const filtered = Array.isArray(clients)
      ? clients.filter((c) => {
          const rec = c as unknown as Record<string, unknown>
          return ['id', 'pixel_id', 'client_id', 'slug'].some((k) => {
            const v = rec[k]
            return typeof v === 'string' && allowed.has(v)
          })
        })
      : clients
    return NextResponse.json(filtered)
  } catch (err) {
    if (err instanceof ReportError) {
      const statusMap: Record<string, number> = {
        not_found:   404,
        unavailable: 503,
      }
      return NextResponse.json(
        { error: err.code, message: err.message },
        { status: statusMap[err.code] ?? 500 },
      )
    }
    return NextResponse.json({ error: 'internal_error' }, { status: 500 })
  }
}
