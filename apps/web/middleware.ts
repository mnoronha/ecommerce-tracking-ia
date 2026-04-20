import { NextRequest, NextResponse } from 'next/server'

const PUBLIC_PATHS = ['/login']

export default function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl

  // Allow static assets and public paths
  if (PUBLIC_PATHS.some(p => pathname.startsWith(p))) {
    return NextResponse.next()
  }

  // Check auth cookie
  const auth = req.cookies.get('dash_auth')?.value
  const password = process.env.DASHBOARD_PASSWORD

  if (!password || auth !== password) {
    const loginUrl = new URL('/login', req.url)
    if (pathname !== '/') loginUrl.searchParams.set('from', pathname)
    return NextResponse.redirect(loginUrl)
  }

  return NextResponse.next()
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|api).*)'],
}
