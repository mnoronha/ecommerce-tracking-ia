/**
 * Specific BFF proxy for HTML report preview.
 * Returns text/html instead of JSON so the browser can render it directly
 * or the client can download it as a file.
 * CTA is always blocked (cta_authorized=False enforced server-side).
 */
import type { NextRequest } from 'next/server'
import { createSupabaseServerClient } from '@/lib/supabase-server'

const _BASE =
  process.env.AGENCY_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'https://ecommerce-tracking-ia-production.up.railway.app'

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ reportId: string }> },
) {
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const { reportId } = await params
  const key = process.env.AGENCY_API_PLATFORM_KEY || ''

  const upstream = new URL(`/agency/v1/canonical-reports/${reportId}/preview-html`, _BASE)
  req.nextUrl.searchParams.forEach((v, k) => upstream.searchParams.set(k, v))

  try {
    const res = await fetch(upstream.toString(), {
      headers: { Authorization: `Bearer ${key}` },
      cache: 'no-store',
    })

    if (!res.ok) {
      const body = await res.text()
      return new Response(body, {
        status: res.status,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    const html = await res.text()
    return new Response(html, {
      status: 200,
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    })
  } catch {
    return new Response(JSON.stringify({ error: 'upstream_unavailable' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    })
  }
}
