import { createServerClient } from '@supabase/ssr'
import { NextRequest, NextResponse } from 'next/server'

const PUBLIC_PATHS = ['/login', '/signup', '/auth']

function makeSupabase(request: NextRequest, response: { current: NextResponse }) {
  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll()      { return request.cookies.getAll() },
        setAll(toSet) {
          toSet.forEach(({ name, value }) => request.cookies.set(name, value))
          response.current = NextResponse.next({ request })
          toSet.forEach(({ name, value, options }) => response.current.cookies.set(name, value, options))
        },
      },
    }
  )
}

export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl

  // ── Portal routes (/portal/*) ──────────────────────────────────────────────
  if (pathname.startsWith('/portal/')) {
    if (
      pathname.startsWith('/portal/login') ||
      pathname.startsWith('/portal/auth/')
    ) {
      return NextResponse.next()
    }

    const res = { current: NextResponse.next({ request }) }
    const supabase = makeSupabase(request, res)
    const { data: { user } } = await supabase.auth.getUser()

    if (!user) {
      const loginUrl = new URL('/portal/login', request.url)
      loginUrl.searchParams.set('redirect', pathname)
      return NextResponse.redirect(loginUrl)
    }

    const clientId = pathname.split('/')[2]
    if (clientId && clientId !== 'acesso-negado') {
      const { data: access } = await supabase
        .from('client_users')
        .select('id')
        .eq('email', user.email ?? '')
        .eq('pixel_id', clientId)
        .maybeSingle()

      if (!access) {
        return NextResponse.redirect(new URL('/portal/acesso-negado', request.url))
      }
    }

    return res.current
  }

  // ── Main app routes ────────────────────────────────────────────────────────
  if (PUBLIC_PATHS.some(p => pathname === p || pathname.startsWith(p + '/'))) {
    return NextResponse.next()
  }

  const res = { current: NextResponse.next({ request }) }
  const supabase = makeSupabase(request, res)
  const { data: { user } } = await supabase.auth.getUser()

  if (!user) {
    const loginUrl = new URL('/login', request.url)
    if (pathname !== '/') loginUrl.searchParams.set('from', pathname)
    return NextResponse.redirect(loginUrl)
  }

  if (pathname === '/') {
    return NextResponse.redirect(new URL('/clients', request.url))
  }

  return res.current
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|api).*)'],
}
