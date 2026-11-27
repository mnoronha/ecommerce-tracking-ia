/**
 * Specific BFF proxy for HTML report preview.
 * Returns text/html instead of JSON so the browser can render it directly
 * or the client can download it as a file.
 * CTA is always blocked (cta_authorized=False enforced server-side).
 */
import type { NextRequest } from 'next/server'
import { requireAgencyBffAuth, verifyAgencyResourceAccess } from '@/lib/bff-auth'

const _BASE =
  process.env.AGENCY_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'https://ecommerce-tracking-ia-production.up.railway.app'

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ reportId: string }> },
) {
  const check = await requireAgencyBffAuth({
    clientRef: req.nextUrl.searchParams.get('client_id'),
  })
  if (!check.ok) return check.response

  const { reportId } = await params
  const resourceOk = await verifyAgencyResourceAccess(
    check.auth.agencyId,
    'canonical_report',
    reportId,
  )
  if (!resourceOk) {
    return new Response(JSON.stringify({ error: 'Forbidden: client scope denied' }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const key = process.env.AGENCY_API_PLATFORM_KEY || ''

  const upstream = new URL(`/agency/v1/canonical-reports/${encodeURIComponent(reportId)}/preview-html`, _BASE)
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
