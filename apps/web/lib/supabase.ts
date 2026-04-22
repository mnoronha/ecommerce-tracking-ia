import { createBrowserClient } from '@supabase/ssr'

// Uses @supabase/ssr so the auth session from cookies is shared
// with the middleware and server components.
export const supabase = createBrowserClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
)
