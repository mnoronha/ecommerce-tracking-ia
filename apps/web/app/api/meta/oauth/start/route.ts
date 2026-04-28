import { randomBytes } from 'crypto'
import { NextRequest, NextResponse } from 'next/server'
import { createSupabaseServerClient } from '@/lib/supabase-server'

const META_AUTH_URL = 'https://www.facebook.com/v19.0/dialog/oauth'

// Scopes for Conversions API + Custom Audiences + Ads insights
const SCOPES = [
  'ads_management',     // CAPI events, manage audiences
  'ads_read',           // read campaigns, ROAS data
  'business_management', // access ad accounts
  'email',
].join(',')

export async function GET(req: NextRequest) {
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const clientId = req.nextUrl.searchParams.get('clientId')
  if (!clientId) return NextResponse.json({ error: 'Missing clientId' }, { status: 400 })

  const appId = process.env.META_APP_ID
  if (!appId) return NextResponse.json({ error: 'Meta OAuth not configured on server' }, { status: 500 })

  const nonce = randomBytes(16).toString('hex')
  const state = Buffer.from(JSON.stringify({ c: clientId, n: nonce })).toString('base64url')
  const redirectUri = `${req.nextUrl.origin}/api/meta/oauth/callback`

  const url = new URL(META_AUTH_URL)
  url.searchParams.set('client_id', appId)
  url.searchParams.set('redirect_uri', redirectUri)
  url.searchParams.set('response_type', 'code')
  url.searchParams.set('scope', SCOPES)
  url.searchParams.set('state', state)
  url.searchParams.set('auth_type', 'rerequest')

  const response = NextResponse.redirect(url.toString())
  response.cookies.set('_meta_oauth_nonce', nonce, {
    httpOnly: true,
    secure: true,
    sameSite: 'lax',
    maxAge: 600,
    path: '/',
  })

  return response
}
