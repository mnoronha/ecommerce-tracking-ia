/**
 * Authenticated BFF proxy for single-platform integration health probe:
 *   POST /api/integrations/[pixelId]/test/[platform] -> POST /integrations/{pixel_id}/test/{platform}
 *
 * Enforces Supabase Auth session + agency_members + client scope + strict
 * platform allowlist, and injects AGENCY_API_PLATFORM_KEY server-side.
 */
import type { NextRequest } from 'next/server'
import { NextResponse } from 'next/server'
import { hasUnsafePathSegments, requireAgencyBffAuth } from '@/lib/bff-auth'

const _BASE =
  process.env.AGENCY_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'https://ecommerce-tracking-ia-production.up.railway.app'

const ALLOWED_PLATFORMS = new Set([
  'meta',
  'google_ads',
  'ga4',
  'tiktok',
  'pinterest',
  'shopify',
])

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ pixelId: string; platform: string }> },
) {
  const { pixelId, platform } = await params
  if (!pixelId || hasUnsafePathSegments([pixelId])) {
    return NextResponse.json({ error: 'Invalid client identifier' }, { status: 400 })
  }
  if (!platform || !ALLOWED_PLATFORMS.has(platform)) {
    return NextResponse.json({ error: 'Unsupported platform' }, { status: 400 })
  }

  const check = await requireAgencyBffAuth({ clientRef: pixelId })
  if (!check.ok) return check.response

  const key = process.env.AGENCY_API_PLATFORM_KEY || ''
  const upstream = new URL(
    `/integrations/${encodeURIComponent(pixelId)}/test/${encodeURIComponent(platform)}`,
    _BASE,
  )

  try {
    const res = await fetch(upstream.toString(), {
      method: 'POST',
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
