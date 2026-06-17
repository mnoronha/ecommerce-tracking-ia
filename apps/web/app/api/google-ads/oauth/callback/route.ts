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
  let returnTo = 'settings'
  try {
    const parsed = JSON.parse(Buffer.from(state, 'base64url').toString())
    clientId = parsed.c
    nonce    = parsed.n
    returnTo = parsed.r || 'settings'
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
    return NextResponse.redirect(`${origin}/clients/${clientId}/${returnTo}?error=google_oauth_token`)
  }

  const tokens = await tokenRes.json()
  const refreshToken: string | undefined = tokens.refresh_token

  if (!refreshToken) {
    // Google only returns refresh_token on first consent — if missing, revoke and retry
    return NextResponse.redirect(
      `${origin}/clients/${clientId}/${returnTo}?error=google_oauth_no_refresh`
    )
  }

  // Grava via backend (cifra o refresh_token em repouso).
  const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'
  let dbError: { message: string } | null = null
  try {
    const resp = await fetch(`${API_URL}/setup/${encodeURIComponent(clientId)}/credentials`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        google_ads_refresh_token: refreshToken,
        merchant_center_refresh_token: refreshToken,
      }),
    })
    if (!resp.ok) dbError = { message: `HTTP ${resp.status}: ${(await resp.text()).slice(0, 200)}` }
  } catch (e) {
    dbError = { message: e instanceof Error ? e.message : 'fetch failed' }
  }

  if (dbError) {
    console.error('google_ads oauth db save failed:', dbError.message)
    return NextResponse.redirect(`${origin}/clients/${clientId}/${returnTo}?error=google_oauth_db`)
  }

  const response = NextResponse.redirect(
    `${origin}/clients/${clientId}/${returnTo}?connected=google`
  )
  response.cookies.set('_ga_oauth_nonce', '', { maxAge: 0, path: '/' })
  return response
}
