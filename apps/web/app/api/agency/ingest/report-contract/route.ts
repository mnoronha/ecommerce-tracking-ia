/**
 * POST /api/agency/ingest/report-contract
 *
 * Private endpoint — called by Hermes (Agency OS), not by the browser.
 * Auth: Authorization: Bearer {AGENCY_OS_INGEST_KEY}
 * AGENCY_OS_INGEST_KEY is server-side only. Never use NEXT_PUBLIC_.
 *
 * Accepts the canonical norolabs-report-contract-v1 (optionally wrapped in a
 * transport envelope with report_type / source_run_id / generated_at).
 * Stores the contract JSONB verbatim — no recalculation, no field removal.
 */

import { createClient }  from '@supabase/supabase-js'
import type { NextRequest } from 'next/server'
import { NextResponse }   from 'next/server'
import type { ReportContractV1 } from '@/lib/agency-os'

// Transport envelope — carries optional metadata alongside the canonical contract.
// The contract itself is stored as-is; unknown fields are preserved.
interface IngestPayload {
  report_type?:   string
  source_run_id?: string
  generated_at?:  string
  contract:       ReportContractV1
}

function createServiceClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY
  if (!url || !key) throw new Error('Supabase service role env vars missing')
  return createClient(url, key, { auth: { persistSession: false } })
}

export async function POST(req: NextRequest) {
  // ── Bearer auth ─────────────────────────────────────────────────────────────
  const ingestKey = process.env.AGENCY_OS_INGEST_KEY
  if (!ingestKey) {
    return NextResponse.json(
      { error: 'server_misconfigured', message: 'AGENCY_OS_INGEST_KEY is not set.' },
      { status: 503 },
    )
  }

  const authHeader = req.headers.get('authorization') ?? ''
  const token = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : ''
  if (token !== ingestKey) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 })
  }

  // ── Parse body ───────────────────────────────────────────────────────────────
  let payload: IngestPayload
  try {
    payload = (await req.json()) as IngestPayload
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 })
  }

  const { report_type = 'weekly', source_run_id, generated_at, contract } = payload

  // ── Validate schema_version ──────────────────────────────────────────────────
  if (!contract || contract.schema_version !== 'norolabs-report-contract-v1') {
    return NextResponse.json(
      { error: 'invalid_schema', message: 'contract.schema_version must be "norolabs-report-contract-v1"' },
      { status: 400 },
    )
  }

  // ── Extract metadata from canonical contract fields ──────────────────────────
  const client_slug   = contract.report?.client_slug
  const businessModel = contract.report?.business_model
  const periodStart   = contract.report?.period?.start
  const periodEnd     = contract.report?.period?.end

  if (!client_slug || typeof client_slug !== 'string') {
    return NextResponse.json(
      { error: 'missing_client_slug', message: 'contract.report.client_slug is required' },
      { status: 400 },
    )
  }
  if (!businessModel) {
    return NextResponse.json(
      { error: 'missing_business_model', message: 'contract.report.business_model is required' },
      { status: 400 },
    )
  }
  if (!periodStart || !periodEnd) {
    return NextResponse.json(
      { error: 'missing_period', message: 'contract.report.period.start and .end are required' },
      { status: 400 },
    )
  }

  // ── Upsert — contract stored verbatim ────────────────────────────────────────
  let supabase: ReturnType<typeof createServiceClient>
  try {
    supabase = createServiceClient()
  } catch (err) {
    return NextResponse.json(
      { error: 'server_misconfigured', message: (err as Error).message },
      { status: 503 },
    )
  }

  const { error } = await supabase
    .from('agency_report_contracts')
    .upsert(
      {
        client_slug,
        report_type,
        business_model:  businessModel,
        period_start:    periodStart,
        period_end:      periodEnd,
        schema_version:  contract.schema_version,
        source_run_id:   source_run_id ?? null,
        generated_at:    generated_at ?? null,
        contract,
      },
      { onConflict: 'client_slug,report_type,period_start,period_end' },
    )

  if (error) {
    return NextResponse.json(
      { error: 'upsert_failed', message: error.message },
      { status: 500 },
    )
  }

  return NextResponse.json(
    {
      status:  'accepted',
      client:  client_slug,
      period:  `${periodStart}_to_${periodEnd}`,
    },
    { status: 200 },
  )
}
