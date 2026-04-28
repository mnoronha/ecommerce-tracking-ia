import { createClient } from '@supabase/supabase-js'
import { NextRequest, NextResponse } from 'next/server'

const TOKEN_URL = 'https://oauth2.googleapis.com/token'

export async function GET(req: NextRequest) {
  const { searchParams, origin } = req.nextUrl
  const code  = searchParams.get('code')
  const state = searchParams.get('state')
  const error = searchParams.get('error')

  if (error) {
    return NextResponse.redirect(`${origin}/clients?error=google_oauth_denied`)
  }

  if (!code || !state) {
    return NextResponse.redirect(`${origin}/clients?error=google_oauth_invalid`)
  }

  // Decode state and verify CSRF nonce
  let clientId: string
  let nonce: string
  try {
    const parsed = JSON.parse(Buffer.from(state, 'base64url').toString())
    clientId = parsed.c
    nonce    = parsed.n
    if (!clientId || !nonce) throw new Error('incomplete state')
  } catch {
    return NextResponse.redirect(`${origin}/clients?error=google_oauth_invalid`)
  }

  const storedNonce = req.cookies.get('_ga_oauth_nonce')?.value
  if (!storedNonce || storedNonce !== nonce) {
    return NextResponse.redirect(`${origin}/clients?error=google_oauth_csrf`)
  }

  // Exchange authorization code for tokens
  const tokenRes = await fetch(TOKEN_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      code,
      client_id:     process.env.GOOGLE_ADS_OAUTH_CLIENT_ID!,
      client_secret: process.env.GOOGLE_ADS_OAUTH_CLIENT_SECRET!,
      redirect_uri:  `${origin}/api/google-ads/oauth/callback`,
      grant_type:    'authorization_code',
    }),
  })

  if (!tokenRes.ok) {
    const body = await tokenRes.text()
    console.error('google_ads oauth token exchange failed:', body)
    return NextResponse.redirect(`${origin}/clients/${clientId}/settings?error=google_oauth_token`)
  }

  const tokens = await tokenRes.json()
  const refreshToken: string | undefined = tokens.refresh_token

  if (!refreshToken) {
    // Google only returns refresh_token on first consent — if missing, revoke and retry
    return NextResponse.redirect(
      `${origin}/clients/${clientId}/settings?error=google_oauth_no_refresh`
    )
  }

  // Persist refresh_token for this client (service role bypasses RLS)
  const admin = createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.SUPABASE_SERVICE_ROLE_KEY!,
    { auth: { autoRefreshToken: false, persistSession: false } }
  )

  const { error: dbError } = await admin
    .from('clients')
    .update({ google_ads_refresh_token: refreshToken })
    .eq('pixel_id', clientId)

  if (dbError) {
    console.error('google_ads oauth db save failed:', dbError.message)
    return NextResponse.redirect(`${origin}/clients/${clientId}/settings?error=google_oauth_db`)
  }

  const response = NextResponse.redirect(
    `${origin}/clients/${clientId}/settings?connected=google`
  )
  response.cookies.set('_ga_oauth_nonce', '', { maxAge: 0, path: '/' })
  return response
}
