/**
 * agency-os.ts — server-side only.
 *
 * Types for norolabs-report-contract-v1 and Supabase query helpers that read
 * contracts stored in agency_report_contracts.
 *
 * Rules:
 * - Never import from client components ("use client" boundary).
 * - Never recalculate or normalise contract fields — read and pass through only.
 * - Metrics come from Agency OS / Hermes and are stored verbatim.
 */

import { createSupabaseServerClient } from '@/lib/supabase-server'

// ── Contract types ────────────────────────────────────────────────────────────

export type MetricStatus =
  | 'available'
  | 'access_missing'
  | 'permission_denied'
  | 'not_contracted'
  | 'no_data'
  | 'unknown'

export interface Metric<T = number> {
  value: T | null
  status: MetricStatus
}

export type BusinessModel = 'ecommerce' | 'lead_generation'

export interface Period {
  start: string
  end: string
  label?: string
}

export interface ReportMeta {
  client_slug:        string
  business_model:     BusinessModel
  period:             Period
  comparison_period?: Period
}

export interface EcommerceBusiness {
  revenue?: Metric
  orders?: Metric<number>
  average_order_value?: Metric
  mer?: Metric
}

export interface LeadGenBusiness {
  leads?: Metric<number>
  qualified_leads?: Metric<number>
  appointments?: Metric<number>
  cost_per_business_lead?: Metric
}

export interface PaidMedia {
  total_spend?: Metric
  google_ads?:  ChannelMetrics
  meta_ads?:    ChannelMetrics
}

export interface ChannelMetrics {
  [key: string]: Metric | undefined
}

export interface ReportContractV1 {
  schema_version:   'norolabs-report-contract-v1'
  report:            ReportMeta
  client?:           unknown
  targets?:          unknown
  business:          EcommerceBusiness | LeadGenBusiness
  paid_media?:       PaidMedia
  journey?:          unknown
  products?:         unknown
  retention?:        unknown
  diagnostics?:      unknown
  recommendations?:  unknown
  governance?:       unknown
  provenance?:       unknown
}

export interface AgencyClientInfo {
  slug: string
  business_model?: BusinessModel
  latest_period_end?: string
}

// ── Error types ───────────────────────────────────────────────────────────────

export type ReportErrorCode = 'not_found' | 'unavailable'

export class ReportError extends Error {
  constructor(
    public readonly code: ReportErrorCode,
    message: string,
  ) {
    super(message)
    this.name = 'ReportError'
  }
}

// ── Row type (matches agency_report_contracts table) ─────────────────────────

interface ContractRow {
  client_slug:             string
  report_type:             string
  business_model:          string
  period_start:            string
  period_end:              string
  comparison_period_start: string | null
  comparison_period_end:   string | null
  schema_version:          string
  source_run_id:           string | null
  generated_at:            string | null
  contract:                ReportContractV1
  updated_at:              string
}

// ── Query helpers (server-side, use Supabase with session RLS) ────────────────

/** List all clients that have at least one stored report contract. */
export async function listClients(): Promise<AgencyClientInfo[]> {
  const supabase = await createSupabaseServerClient()
  const { data, error } = await supabase
    .from('agency_report_contracts')
    .select('client_slug, business_model, period_end')
    .order('client_slug')
    .order('period_end', { ascending: false })

  if (error) {
    throw new ReportError('unavailable', `Supabase error listing clients: ${error.message}`)
  }

  // Deduplicate — keep only the most-recent row per slug
  const seen = new Set<string>()
  const clients: AgencyClientInfo[] = []
  for (const row of (data ?? [])) {
    if (!seen.has(row.client_slug)) {
      seen.add(row.client_slug)
      clients.push({
        slug:             row.client_slug,
        business_model:   row.business_model as BusinessModel,
        latest_period_end: row.period_end,
      })
    }
  }
  return clients
}

/** Fetch the most-recent weekly report for a client. */
export async function getLatestWeeklyReport(
  clientSlug: string,
): Promise<ReportContractV1> {
  const supabase = await createSupabaseServerClient()
  const { data, error } = await supabase
    .from('agency_report_contracts')
    .select('contract')
    .eq('client_slug', clientSlug)
    .eq('report_type', 'weekly')
    .order('period_end', { ascending: false })
    .limit(1)
    .maybeSingle()

  if (error) {
    throw new ReportError('unavailable', `Supabase error: ${error.message}`)
  }
  if (!data) {
    throw new ReportError('not_found', `No weekly report found for client "${clientSlug}".`)
  }
  return (data as ContractRow).contract
}

/**
 * Fetch a specific weekly report by period.
 * period format: YYYY-MM-DD_to_YYYY-MM-DD (e.g. 2026-09-21_to_2026-09-27)
 */
export async function getWeeklyReport(
  clientSlug: string,
  period: string,
): Promise<ReportContractV1> {
  const [start, end] = period.split('_to_')
  if (!start || !end) {
    throw new ReportError('not_found', `Invalid period format: "${period}". Expected YYYY-MM-DD_to_YYYY-MM-DD.`)
  }

  const supabase = await createSupabaseServerClient()
  const { data, error } = await supabase
    .from('agency_report_contracts')
    .select('contract')
    .eq('client_slug', clientSlug)
    .eq('report_type', 'weekly')
    .eq('period_start', start)
    .eq('period_end', end)
    .maybeSingle()

  if (error) {
    throw new ReportError('unavailable', `Supabase error: ${error.message}`)
  }
  if (!data) {
    throw new ReportError('not_found', `No weekly report found for "${clientSlug}" in period "${period}".`)
  }
  return (data as ContractRow).contract
}
