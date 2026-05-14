import { createClient } from '@supabase/supabase-js'
import { createSupabaseServerClient } from '@/lib/supabase-server'
import { NextRequest, NextResponse } from 'next/server'
import type { PlanId } from '@/lib/plans'

const PLAN_LIMITS: Record<PlanId, { client_limit: number; orders_limit: number }> = {
  rastreador:   { client_limit: 1,    orders_limit: 2000  },
  inteligencia: { client_limit: 3,    orders_limit: 20000 },
  predicao:     { client_limit: 9999, orders_limit: 9999999 },
}

function slugify(str: string) {
  return str
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 50)
}

export async function POST(req: NextRequest) {
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const { agencyName, plan } = await req.json() as { agencyName: string; plan: PlanId }
  if (!agencyName || !plan) {
    return NextResponse.json({ error: 'agencyName and plan required' }, { status: 400 })
  }

  const admin = createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.SUPABASE_SERVICE_ROLE_KEY!,
    { auth: { autoRefreshToken: false, persistSession: false } }
  )

  // Build a unique slug
  let slug = slugify(agencyName)
  const { count } = await admin.from('agencies').select('id', { count: 'exact', head: true }).like('slug', `${slug}%`)
  if ((count ?? 0) > 0) slug = `${slug}-${Date.now().toString(36)}`

  const limits = PLAN_LIMITS[plan] ?? PLAN_LIMITS.rastreador

  const { data: agency, error: agencyErr } = await admin
    .from('agencies')
    .insert({
      name:          agencyName,
      slug,
      plan,
      plan_started_at: new Date().toISOString(),
      trial_ends_at: new Date(Date.now() + 14 * 86400_000).toISOString(),
      billing_email: user.email,
      client_limit:  limits.client_limit,
      orders_limit:  limits.orders_limit,
    })
    .select('id, slug')
    .single()

  if (agencyErr) {
    return NextResponse.json({ error: agencyErr.message }, { status: 500 })
  }

  const { error: memberErr } = await admin
    .from('agency_members')
    .insert({ agency_id: agency.id, user_id: user.id, role: 'owner' })

  if (memberErr) {
    return NextResponse.json({ error: memberErr.message }, { status: 500 })
  }

  return NextResponse.json({ agencyId: agency.id, slug: agency.slug })
}
