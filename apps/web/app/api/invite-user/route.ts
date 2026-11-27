import { createClient } from '@supabase/supabase-js'
import { NextRequest, NextResponse } from 'next/server'
import { requireAgencyBffAuth } from '@/lib/bff-auth'

const ALLOWED_CLIENT_ROLES = new Set(['viewer', 'editor', 'admin'])

export async function POST(req: NextRequest) {
  const { email, role, clientId } = await req.json().catch(() => ({}))
  if (!email || !role || !clientId || typeof email !== 'string' || typeof role !== 'string' || typeof clientId !== 'string') {
    return NextResponse.json({ error: 'Missing fields' }, { status: 400 })
  }
  if (!ALLOWED_CLIENT_ROLES.has(role)) {
    return NextResponse.json({ error: 'Invalid role' }, { status: 400 })
  }

  // Auth + role + client ownership guard (owner/admin in agency_members ONLY)
  const check = await requireAgencyBffAuth({
    allowedRoles: ['owner', 'admin'],
    clientRef: clientId,
  })
  if (!check.ok) return check.response
  const { user } = check.auth

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
