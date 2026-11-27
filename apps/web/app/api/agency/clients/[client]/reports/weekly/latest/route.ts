import type { NextRequest }               from 'next/server'
import { NextResponse }                    from 'next/server'
import { getLatestWeeklyReport, ReportError } from '@/lib/agency-os'
import { requireAgencyBffAuth }            from '@/lib/bff-auth'

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ client: string }> },
) {
  const { client } = await params

  // ── Auth + agency_members + client_id guard ─────────────────────────────────
  const check = await requireAgencyBffAuth({ clientRef: client })
  if (!check.ok) return check.response

  // ── Proxy ───────────────────────────────────────────────────────────────────
  try {
    const contract = await getLatestWeeklyReport(client)
    return NextResponse.json(contract)
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
