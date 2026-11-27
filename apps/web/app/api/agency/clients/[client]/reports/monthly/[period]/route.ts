import type { NextRequest }                from 'next/server'
import { NextResponse }                     from 'next/server'
import { getMonthlyReport, ReportError }    from '@/lib/agency-os'
import { requireAgencyBffAuth }             from '@/lib/bff-auth'

// Period format: YYYY-MM-DD_to_YYYY-MM-DD  (e.g. 2026-09-21_to_2026-09-27)
const PERIOD_RE = /^\d{4}-\d{2}-\d{2}_to_\d{4}-\d{2}-\d{2}$/

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ client: string; period: string }> },
) {
  const { client, period } = await params

  // ── Auth + agency_members + client_id guard ─────────────────────────────────
  const check = await requireAgencyBffAuth({ clientRef: client })
  if (!check.ok) return check.response

  if (!PERIOD_RE.test(period)) {
    return NextResponse.json(
      { error: 'invalid_period', message: 'period must be YYYY-MM-DD_to_YYYY-MM-DD' },
      { status: 400 },
    )
  }

  // ── Proxy ───────────────────────────────────────────────────────────────────
  try {
    const contract = await getMonthlyReport(client, period)
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
