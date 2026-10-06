/**
 * agency-api-client.ts — server-side only.
 *
 * Typed fetch helper for the Agency API (/agency/v1/).
 * Used by server components and BFF route handlers.
 * NEVER import in 'use client' components — use /api/v1/* BFF routes instead.
 *
 * Required env vars (server-side, never NEXT_PUBLIC_*):
 *   AGENCY_API_PLATFORM_KEY  — platform_web service token
 *   AGENCY_API_URL            — Railway base URL (falls back to NEXT_PUBLIC_API_URL)
 */

const _BASE =
  process.env.AGENCY_API_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'https://ecommerce-tracking-ia-production.up.railway.app'

export class AgencyApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'AgencyApiError'
  }
}

export async function agencyApiFetch<T = unknown>(
  path: string,
  searchParams?: Record<string, string | undefined>,
  init?: RequestInit,
): Promise<T> {
  const key = process.env.AGENCY_API_PLATFORM_KEY || ''
  const url = new URL(`/agency/v1/${path}`, _BASE)

  if (searchParams) {
    for (const [k, v] of Object.entries(searchParams)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, v)
    }
  }

  const res = await fetch(url.toString(), {
    ...init,
    headers: {
      Authorization: `Bearer ${key}`,
      'Content-Type': 'application/json',
      ...((init?.headers as Record<string, string>) || {}),
    },
    cache: 'no-store',
  })

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new AgencyApiError(
      res.status,
      (body as { detail?: string; error?: string }).detail ||
        (body as { detail?: string; error?: string }).error ||
        `Agency API error ${res.status}`,
    )
  }

  return res.json() as Promise<T>
}
