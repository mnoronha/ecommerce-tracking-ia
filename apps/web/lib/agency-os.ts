/**
 * agency-os.ts — server-side only.
 *
 * Types for norolabs-report-contract-v1 and helpers that read report contracts
 * from the Agency API (/agency/v1/clients/{slug}/report-contracts).
 *
 * Rules:
 * - Never import from client components ("use client" boundary).
 * - Never recalculate or normalise contract fields — read and pass through only.
 * - Metrics come from Agency OS / Hermes and are stored verbatim.
 * - Source of truth: Agency API, not Supabase directly.
 */

import { agencyApiFetch, AgencyApiError } from '@/lib/agency-api-client'

// ── Contract types ────────────────────────────────────────────────────────────

export type MetricStatus =
  | 'available'
  | 'access_missing'
  | 'permission_denied'
  | 'not_contracted'
  | 'no_data'
  | 'unknown'
  | 'missing'
  | 'stale'
  | 'unresolved_mapping'

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
  type:              'weekly' | 'monthly'
  client_slug:        string
  business_model:     BusinessModel
  period:             Period
  comparison_period?: Period | null
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
  [key: string]: unknown
}

export function isMetric(value: unknown): value is Metric {
  return typeof value === 'object' && value !== null && 'value' in value && 'status' in value
}

// Exact presentation routes observed in the integration directory; no fuzzy routing.
const CLIENT_ROUTES: Record<string, string> = {
  'lk-sneakers': 'lk-sneakers', 'dipua': 'dipua', 'dipua-qe5p': 'dipua',
  'enutri': 'enutri', 'enutri-4sph': 'enutri',
  'clinica-tarcio-caetano': 'clinica-tarcio-caetano', 'clinica-dr-tarcio-m970': 'clinica-tarcio-caetano',
  'spiti-auction': 'spiti-auction', 'spiti-auction-d0yf': 'spiti-auction',
  'zipper-galeria': 'zipper-galeria', 'zipper-galeria-bvlu': 'zipper-galeria',
}

export function canonicalClient(route: string): string {
  const slug = CLIENT_ROUTES[route]
  if (!slug) throw new ReportError('not_found', 'Unknown canonical client')
  return slug
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

// ── Agency API response shape (CoreReportContractOut) ──────────────────────────

interface CoreReportContractOut {
  report_contract_id: string
  client_id:          string
  report_type:        'WEEKLY' | 'MONTHLY'
  period:             { start: string; end: string }
  contract:           Record<string, unknown>
  generated_at:       string
  provenance_status:  'REAL' | 'STUB' | 'MISSING'
}

// ── Query helpers (server-side, via Agency API) ───────────────────────────────

/** List all active clients from Agency API. */
export async function listClients(): Promise<AgencyClientInfo[]> {
  try {
    const rows = await agencyApiFetch<Array<{
      client_id: string
      business_model?: string
      status?: string
    }>>('clients')
    return rows
      .filter(r => r.status !== 'inactive')
      .map(r => ({
        slug:           r.client_id,
        business_model: r.business_model as BusinessModel | undefined,
      }))
  } catch (err) {
    if (err instanceof AgencyApiError) {
      throw new ReportError('unavailable', `Agency API error: ${err.message}`)
    }
    throw new ReportError('unavailable', 'Could not list clients')
  }
}

/** Fetch the most-recent weekly report for a client. */
export async function getLatestWeeklyReport(clientSlug: string): Promise<ReportContractV1> {
  return getReport(clientSlug, 'weekly')
}

export async function getLatestMonthlyReport(clientSlug: string): Promise<ReportContractV1> {
  return getReport(clientSlug, 'monthly')
}

export async function getMonthlyReport(clientSlug: string, period: string): Promise<ReportContractV1> {
  return getReport(clientSlug, 'monthly', period)
}

async function getReport(clientRoute: string, type: 'weekly' | 'monthly', period?: string): Promise<ReportContractV1> {
  const clientSlug = canonicalClient(clientRoute)

  if (period && !/^\d{4}-\d{2}-\d{2}_to_\d{4}-\d{2}-\d{2}$/.test(period)) {
    throw new ReportError('not_found', 'Invalid report period')
  }

  let contracts: CoreReportContractOut[]
  try {
    contracts = await agencyApiFetch<CoreReportContractOut[]>(
      `clients/${clientSlug}/report-contracts`,
      { report_type: type.toUpperCase() as 'WEEKLY' | 'MONTHLY' },
    )
  } catch (err) {
    if (err instanceof AgencyApiError) {
      throw new ReportError('unavailable', `Agency API error: ${err.message}`)
    }
    throw new ReportError('unavailable', 'Could not fetch report contracts')
  }

  if (!contracts || contracts.length === 0) {
    throw new ReportError('not_found', `No ${type} report found for client "${clientSlug}".`)
  }

  // If a specific period is requested, filter client-side
  let target: CoreReportContractOut | undefined = contracts[0]
  if (period) {
    const [start, end] = period.split('_to_')
    target = contracts.find(c => c.period.start === start && c.period.end === end)
    if (!target) {
      throw new ReportError('not_found', `No ${type} report found for period "${period}".`)
    }
  }

  if (target.provenance_status === 'MISSING') {
    throw new ReportError('not_found', `No ${type} report found for client "${clientSlug}".`)
  }

  return target.contract as unknown as ReportContractV1
}

/**
 * Fetch a specific weekly report by period.
 * period format: YYYY-MM-DD_to_YYYY-MM-DD (e.g. 2026-09-21_to_2026-09-27)
 */
export async function getWeeklyReport(clientSlug: string, period: string): Promise<ReportContractV1> {
  return getReport(clientSlug, 'weekly', period)
}
