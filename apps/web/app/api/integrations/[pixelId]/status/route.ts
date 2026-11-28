/**
 * Authenticated BFF proxy for integration health status:
 *   GET  /api/integrations/[pixelId]/status -> GET  /integrations/{pixel_id}/status
 *   POST /api/integrations/[pixelId]/status -> POST /integrations/{pixel_id}/status
 *
 * Enforces Supabase Auth session + agency_members + client scope (AGENTS.md §2)
 * and injects AGENCY_API_PLATFORM_KEY server-side (never exposed to the browser).
 */
import type { NextRequest } from 'next/server'
import { NextResponse } from 'next/server'
import { hasUnsafePathSegments, requireAgencyBffAuth } from '@/lib/bff-auth'

const _BASE =
  process.env.AGENCY_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'https://ecommerce-tracking-ia-production.up.railway.app'

async function forwardStatus(
  method: 'GET' | 'POST',
  pixelId: string,
): Promise<NextResponse> {
  if (!pixelId || hasUnsafePathSegments([pixelId])) {
    return NextResponse.json({ error: 'Invalid client identifier' }, { status: 400 })
  }

  const check = await requireAgencyBffAuth({ clientRef: pixelId })
  if (!check.ok) return check.response

  const key = process.env.AGENCY_API_PLATFORM_KEY || ''
  const upstream = new URL(
    `/integrations/${encodeURIComponent(pixelId)}/status`,
    _BASE,
  )

  try {
    const res = await fetch(upstream.toString(), {
      method,
      headers: {
        Authorization: `Bearer ${key}`,
      },
      cache: 'no-store',
    })
    const body = await res.json().catch(() => null)
    return NextResponse.json(body ?? { error: 'upstream_invalid_json' }, {
      status: res.status,
    })
  } catch {
    return NextResponse.json({ error: 'upstream_unavailable' }, { status: 503 })
  }
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ pixelId: string }> },
) {
  const { pixelId } = await params
  return forwardStatus('GET', pixelId)
}

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ pixelId: string }> },
) {
  const { pixelId } = await params
  return forwardStatus('POST', pixelId)
}
