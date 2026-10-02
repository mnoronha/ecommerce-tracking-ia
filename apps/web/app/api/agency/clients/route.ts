import { NextResponse }               from 'next/server'
import { createSupabaseServerClient }  from '@/lib/supabase-server'
import { listClients, ReportError }  from '@/lib/agency-os'

export async function GET() {
  // ── Auth ────────────────────────────────────────────────────────────────────
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  // ── Proxy ───────────────────────────────────────────────────────────────────
  try {
    const clients = await listClients()
    return NextResponse.json(clients)
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
