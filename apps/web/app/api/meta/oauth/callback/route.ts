import { createClient } from '@supabase/supabase-js'
import { NextRequest, NextResponse } from 'next/server'

const GRAPH = 'https://graph.facebook.com/v19.0'

export async function GET(req: NextRequest) {
  const { searchParams, origin } = req.nextUrl
  const code  = searchParams.get('code')
  const state = searchParams.get('state')
  const error = searchParams.get('error')

  if (error) {
    return NextResponse.redirect(`${origin}/clients?error=meta_oauth_denied`)
  }

  if (!code || !state) {
    return NextResponse.redirect(`${origin}/clients?error=meta_oauth_invalid`)
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
    return NextResponse.redirect(`${origin}/clients?error=meta_oauth_invalid`)
  }

  const storedNonce = req.cookies.get('_meta_oauth_nonce')?.value
  if (!storedNonce || storedNonce !== nonce) {
    return NextResponse.redirect(`${origin}/clients?error=meta_oauth_csrf`)
  }

  const appId     = process.env.META_APP_ID!
  const appSecret = process.env.META_APP_SECRET!
  const redirectUri = `${origin}/api/meta/oauth/callback`

  // ── Step 1: Exchange code for short-lived token (1-2h) ─────────────────────
  const shortRes = await fetch(
    `${GRAPH}/oauth/access_token?` + new URLSearchParams({
      client_id:     appId,
      client_secret: appSecret,
      redirect_uri:  redirectUri,
      code,
    }).toString(),
    { method: 'GET' }
  )

  if (!shortRes.ok) {
    const body = await shortRes.text()
    console.error('meta oauth short-lived exchange failed:', body)
    return NextResponse.redirect(`${origin}/clients/${clientId}/settings?error=meta_oauth_token`)
  }

  const shortData = await shortRes.json()
  const shortToken: string | undefined = shortData.access_token
  if (!shortToken) {
    return NextResponse.redirect(`${origin}/clients/${clientId}/settings?error=meta_oauth_no_token`)
  }

  // ── Step 2: Exchange short-lived for long-lived (60 days) ──────────────────
  const longRes = await fetch(
    `${GRAPH}/oauth/access_token?` + new URLSearchParams({
      grant_type:        'fb_exchange_token',
      client_id:         appId,
      client_secret:     appSecret,
      fb_exchange_token: shortToken,
    }).toString(),
    { method: 'GET' }
  )

  if (!longRes.ok) {
    const body = await longRes.text()
    console.error('meta oauth long-lived exchange failed:', body)
    return NextResponse.redirect(`${origin}/clients/${clientId}/settings?error=meta_oauth_long_token`)
  }

  const longData = await longRes.json()
  const longToken: string | undefined = longData.access_token
  const expiresIn: number = longData.expires_in || (60 * 24 * 60 * 60) // default 60 days
  if (!longToken) {
    return NextResponse.redirect(`${origin}/clients/${clientId}/settings?error=meta_oauth_no_long_token`)
  }

  const expiresAt = new Date(Date.now() + expiresIn * 1000).toISOString()

  // ── Step 3: Persist + try to auto-detect ad account & pixel ────────────────
  const admin = createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.SUPABASE_SERVICE_ROLE_KEY!,
    { auth: { autoRefreshToken: false, persistSession: false } }
  )

  const update: Record<string, unknown> = {
    meta_access_token:     longToken,
    meta_token_expires_at: expiresAt,
    meta_token_health:     'healthy',
  }

  // Try to fetch the user's first ad account if not set yet
  try {
    const { data: existing } = await admin
      .from('clients')
      .select('meta_ad_account_id, meta_pixel_id')
      .eq('pixel_id', clientId)
      .maybeSingle()

    if (existing && !existing.meta_ad_account_id) {
      const accRes = await fetch(
        `${GRAPH}/me/adaccounts?fields=account_id,name&limit=1&access_token=${encodeURIComponent(longToken)}`
      )
      if (accRes.ok) {
        const accData = await accRes.json()
        const firstAcc = (accData.data || [])[0]
        if (firstAcc?.account_id) {
          update.meta_ad_account_id = `act_${firstAcc.account_id}`
          // Try to fetch the first pixel for this ad account
          if (!existing.meta_pixel_id) {
            const pxRes = await fetch(
              `${GRAPH}/act_${firstAcc.account_id}/adspixels?fields=id,name&limit=1&access_token=${encodeURIComponent(longToken)}`
            )
            if (pxRes.ok) {
              const pxData = await pxRes.json()
              const firstPx = (pxData.data || [])[0]
              if (firstPx?.id) update.meta_pixel_id = firstPx.id
            }
          }
        }
      }
    }
  } catch (e) {
    console.warn('meta oauth: ad account auto-detect failed (non-fatal):', e)
  }

  const { error: dbError } = await admin
    .from('clients')
    .update(update)
    .eq('pixel_id', clientId)

  if (dbError) {
    console.error('meta oauth db save failed:', dbError.message)
    return NextResponse.redirect(`${origin}/clients/${clientId}/settings?error=meta_oauth_db`)
  }

  const response = NextResponse.redirect(
    `${origin}/clients/${clientId}/settings?connected=meta`
  )
  response.cookies.set('_meta_oauth_nonce', '', { maxAge: 0, path: '/' })
  return response
}
