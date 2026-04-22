import { createClient } from '@supabase/supabase-js'
import { createSupabaseServerClient } from '@/lib/supabase-server'
import { NextRequest, NextResponse } from 'next/server'

export async function POST(req: NextRequest) {
  // Auth check — only authenticated users can invite
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const { email, role, clientId } = await req.json()
  if (!email || !role || !clientId) {
    return NextResponse.json({ error: 'Missing fields' }, { status: 400 })
  }

  // Use service role to manage auth users and link client_members
  const admin = createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.SUPABASE_SERVICE_ROLE_KEY!,
    { auth: { autoRefreshToken: false, persistSession: false } }
  )

  // Invite user — Supabase sends email automatically
  const { data: inviteData, error: inviteError } = await admin.auth.admin.inviteUserByEmail(email)
  if (inviteError) {
    return NextResponse.json({ error: inviteError.message }, { status: 400 })
  }

  const invitedUserId = inviteData.user.id

  // Link to client_members
  const { error: memberError } = await admin
    .from('client_members')
    .upsert({ client_id: clientId, user_id: invitedUserId, role, invited_by: user.id },
            { onConflict: 'client_id,user_id' })

  if (memberError) {
    return NextResponse.json({ error: memberError.message }, { status: 500 })
  }

  return NextResponse.json({ ok: true })
}
