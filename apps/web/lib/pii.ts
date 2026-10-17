/**
 * Customer identifiers (LGPD) — same normalization as the API (core/pii.py):
 * email_hash = sha256(lower(trim(email))). Raw emails are not stored, so
 * search by email hashes the typed address and matches it exactly.
 */
export async function emailHash(email: string): Promise<string | null> {
  const e = email.trim().toLowerCase()
  if (!e.includes('@') || typeof crypto === 'undefined' || !crypto.subtle) return null
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(e))
  return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('')
}
