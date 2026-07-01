import { createServerClient } from '@supabase/ssr'
import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl

  // Only intercept /portal/* — skip login and auth callback
  if (
    !pathname.startsWith('/portal/') ||
    pathname.startsWith('/portal/login') ||
    pathname.startsWith('/portal/auth/')
  ) {
    return NextResponse.next()
  }

  // Extract clientId: /portal/[clientId]/...
  const clientId = pathname.split('/')[2]
  if (!clientId) return NextResponse.next()

  let response = NextResponse.next({ request })

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll()
        },
        setAll(toSet) {
          toSet.forEach(({ name, value }) => request.cookies.set(name, value))
          response = NextResponse.next({ request })
          toSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options)
          )
        },
      },
    }
  )

  const { data: { user } } = await supabase.auth.getUser()

  if (!user) {
    const loginUrl = new URL('/portal/login', request.url)
    loginUrl.searchParams.set('redirect', pathname)
    return NextResponse.redirect(loginUrl)
  }

  // Check access in client_users table
  const { data: access } = await supabase
    .from('client_users')
    .select('id')
    .eq('email', user.email ?? '')
    .eq('pixel_id', clientId)
    .maybeSingle()

  if (!access) {
    return NextResponse.redirect(new URL('/portal/acesso-negado', request.url))
  }

  return response
}

export const config = {
  matcher: ['/portal/:path*'],
}
