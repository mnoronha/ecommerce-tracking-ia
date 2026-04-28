import { randomBytes } from 'crypto'
import { NextRequest, NextResponse } from 'next/server'
import { createSupabaseServerClient } from '@/lib/supabase-server'

const GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
const SCOPE = 'https://www.googleapis.com/auth/adwords'

export async function GET(req: NextRequest) {
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const clientId = req.nextUrl.searchParams.get('clientId')
  if (!clientId) return NextResponse.json({ error: 'Missing clientId' }, { status: 400 })

  const oauthClientId = process.env.GOOGLE_ADS_OAUTH_CLIENT_ID
  if (!oauthClientId) return NextResponse.json({ error: 'OAuth not configured on server' }, { status: 500 })

  const nonce = randomBytes(16).toString('hex')
  const state = Buffer.from(JSON.stringify({ c: clientId, n: nonce })).toString('base64url')
  const redirectUri = `${req.nextUrl.origin}/api/google-ads/oauth/callback`

  const url = new URL(GOOGLE_AUTH_URL)
  url.searchParams.set('client_id', oauthClientId)
  url.searchParams.set('redirect_uri', redirectUri)
  url.searchParams.set('response_type', 'code')
  url.searchParams.set('scope', SCOPE)
  url.searchParams.set('access_type', 'offline')
  url.searchParams.set('prompt', 'consent')
  url.searchParams.set('state', state)

  const response = NextResponse.redirect(url.toString())
  response.cookies.set('_ga_oauth_nonce', nonce, {
    httpOnly: true,
    secure: true,
    sameSite: 'lax',
    maxAge: 600,
    path: '/',
  })

  return response
}
