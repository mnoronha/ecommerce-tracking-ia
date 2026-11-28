/**
 * Deterministic source-hierarchy resolver for the Client Overview KPI cards
 * (PRD Noro Platform v3.0 §§ 5.1 & 5.3).
 *
 * Hierarchy rules:
 *   1. Business Truth (orders / core_metric_snapshots `revenue_business` & `orders_count`):
 *      - Used when business source is READY / valid (not MISSING / NOT_CONTRACTED / NO_DATA).
 *      - Revenue, Orders and Avg Order Value come from Business Truth.
 *      - Visitors prefer `ga4_sessions` (Journey Truth), falling back to live tracking visitors.
 *      - Conversion Rate = Orders / Visitors * 100 (null when denominator <= 0).
 *   2. Journey Truth (GA4) fallback:
 *      - Used when Business Truth is MISSING, NOT_CONTRACTED, NO_DATA or unavailable,
 *        and GA4 metrics (`ga4_revenue`, `ga4_purchases`, `ga4_sessions`) are valid.
 *      - Revenue = ga4_revenue
 *      - Orders = ga4_purchases
 *      - Visitors = ga4_sessions
 *      - Avg Order Value = ga4_revenue / ga4_purchases
 *      - Conversion Rate = (ga4_purchases / ga4_sessions) * 100
 *   3. No valid source:
 *      - Never converts `null` / absent metrics into `R$ 0,00` or `0` — always returns `null`
 *        formatted as `'—'`.
 */

export type OverviewSourceKind = 'business' | 'ga4' | 'tracking' | 'missing'

export interface CanonicalMetricInput {
  metric_key: string
  value: number | null
  value_status: string
  unit?: string
  currency?: string
  domain?: string
}

export interface CockpitHealthInput {
  domain?: string
  source_state?: string
  source_system?: string
}

export interface LiveKpisInput {
  totalRevenue: number
  totalOrders: number
  totalVisitors: number
  avgOrderValue: number
  conversionRate: number
  revenueChange?: number
  ordersChange?: number
  visitorsChange?: number
  avgOrderValueChange?: number
  conversionRateChange?: number
}

export interface Ga4SummaryInput {
  sessions: number
  purchases: number
  revenue: number
}

export interface ResolvedMetricCard {
  value: number | null
  formatted: string
  source: OverviewSourceKind
  /** Discreet source badge shown on the card: 'Business Truth' | 'Fonte: GA4' | 'Noro Tracking' | null */
  sourceBadge: string | null
}

export interface ResolvedOverviewKpis {
  primarySource: 'business' | 'ga4' | 'missing'
  isBusinessTruth: boolean
  isGa4Fallback: boolean
  revenue: ResolvedMetricCard
  orders: ResolvedMetricCard
  visitors: ResolvedMetricCard
  avgOrderValue: ResolvedMetricCard
  conversionRate: ResolvedMetricCard
}

export interface ResolveOverviewKpisInput {
  canonicalMetrics?: CanonicalMetricInput[]
  canonicalHealth?: Record<string, string> | null
  cockpitHealth?: CockpitHealthInput[] | null
  liveKpis?: LiveKpisInput | null
  ga4Summary?: Ga4SummaryInput | null
}

const UNAVAILABLE_STATES = new Set([
  'MISSING',
  'NOT_CONTRACTED',
  'NO_DATA',
  'ERROR',
  'ACCESS_MISSING',
  'PERMISSION_DENIED',
  'AUTH_EXPIRED',
  'UNAVAILABLE',
  'UNKNOWN',
])

const READY_STATES = new Set(['READY', 'OK', 'PARTIAL', 'PROVISIONAL', 'STALE'])

const BADGE_LABELS: Record<OverviewSourceKind, string | null> = {
  business: 'Business Truth',
  ga4: 'Fonte: GA4',
  tracking: 'Noro Tracking',
  missing: null,
}

// ── Formatters (never coerce null/NaN into R$ 0,00 or 0) ──────────────────────

export function formatCurrencyOrDash(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency: 'BRL',
  }).format(value)
}

export function formatIntOrDash(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return Math.round(value).toLocaleString('pt-BR')
}

export function formatPctOrDash(
  value: number | null | undefined,
  decimals = 2,
): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${value.toLocaleString('pt-BR', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}%`
}

// ── Internal helpers ──────────────────────────────────────────────────────────

function normalizeState(raw?: string | null): string | null {
  if (!raw || typeof raw !== 'string') return null
  const upper = raw.trim().toUpperCase()
  return upper || null
}

function isValidCanonicalMetric(m?: CanonicalMetricInput | null): m is CanonicalMetricInput & { value: number } {
  if (!m) return false
  if (typeof m.value !== 'number' || !Number.isFinite(m.value)) return false
  const status = normalizeState(m.value_status)
  if (!status) return true
  return !UNAVAILABLE_STATES.has(status)
}

function findMetric(
  metrics: CanonicalMetricInput[],
  keys: string[],
  domain?: string,
): CanonicalMetricInput | undefined {
  for (const key of keys) {
    const match = metrics.find(
      (m) => m.metric_key === key && (!domain || !m.domain || m.domain === domain),
    )
    if (match) return match
  }
  return undefined
}

function resolveDomainHealthState(
  domain: 'business' | 'ga4',
  canonicalHealth?: Record<string, string> | null,
  cockpitHealth?: CockpitHealthInput[] | null,
): string | null {
  if (canonicalHealth) {
    const keys = domain === 'business'
      ? ['business', 'orders', 'ecommerce', 'shopify', 'vnda']
      : ['ga4', 'journey', 'analytics']
    for (const k of keys) {
      const state = normalizeState(canonicalHealth[k])
      if (state) return state
    }
  }

  if (cockpitHealth && cockpitHealth.length > 0) {
    const entries = cockpitHealth.filter((e) => {
      const d = (e.domain || '').toLowerCase()
      const s = (e.source_system || '').toLowerCase()
      if (domain === 'business') {
        return d === 'business' || s === 'shopify' || s === 'vnda' || s === 'orders'
      }
      return d === 'ga4' || d === 'journey' || s === 'ga4'
    })
    if (entries.length > 0) {
      const states = entries
        .map((e) => normalizeState(e.source_state))
        .filter((s): s is string => Boolean(s))
      const ready = states.find((s) => READY_STATES.has(s))
      if (ready) return ready
      if (states[0]) return states[0]
    }
  }

  return null
}

function safeDivide(numerator: number | null, denominator: number | null): number | null {
  if (
    typeof numerator !== 'number' ||
    !Number.isFinite(numerator) ||
    typeof denominator !== 'number' ||
    !Number.isFinite(denominator) ||
    denominator <= 0
  ) {
    return null
  }
  return numerator / denominator
}

function makeCard(
  value: number | null,
  formatter: (v: number | null) => string,
  source: OverviewSourceKind,
): ResolvedMetricCard {
  const effectiveSource: OverviewSourceKind = value === null ? 'missing' : source
  return {
    value,
    formatted: formatter(value),
    source: effectiveSource,
    sourceBadge: BADGE_LABELS[effectiveSource],
  }
}

// ── Main KPI source resolver ──────────────────────────────────────────────────

export function resolveOverviewKpis(input: ResolveOverviewKpisInput): ResolvedOverviewKpis {
  const metrics = input.canonicalMetrics ?? []
  const liveKpis = input.liveKpis ?? null
  const ga4Summary = input.ga4Summary ?? null

  const businessHealthState = resolveDomainHealthState(
    'business',
    input.canonicalHealth,
    input.cockpitHealth,
  )
  const ga4HealthState = resolveDomainHealthState(
    'ga4',
    input.canonicalHealth,
    input.cockpitHealth,
  )

  const businessExplicitlyUnavailable =
    businessHealthState !== null && UNAVAILABLE_STATES.has(businessHealthState)

  const canonicalRevBiz = findMetric(metrics, ['revenue_business', 'business_revenue'], 'business')
  const canonicalOrdersBiz = findMetric(metrics, ['orders_count', 'business_orders'], 'business')

  const hasValidCanonicalBusiness =
    !businessExplicitlyUnavailable &&
    (isValidCanonicalMetric(canonicalRevBiz) || isValidCanonicalMetric(canonicalOrdersBiz))

  const hasLiveBusinessOrders =
    !businessExplicitlyUnavailable &&
    liveKpis !== null &&
    (liveKpis.totalOrders > 0 || liveKpis.totalRevenue > 0)

  // GA4 values: prefer valid canonical snapshots, fall back to live ga4Summary when available
  const canonicalGa4Revenue = findMetric(metrics, ['ga4_revenue'])
  const canonicalGa4Purchases = findMetric(metrics, ['ga4_purchases'])
  const canonicalGa4Sessions = findMetric(metrics, ['ga4_sessions'])

  const ga4ExplicitlyUnavailable =
    ga4HealthState !== null &&
    UNAVAILABLE_STATES.has(ga4HealthState) &&
    !isValidCanonicalMetric(canonicalGa4Revenue) &&
    !isValidCanonicalMetric(canonicalGa4Purchases) &&
    !isValidCanonicalMetric(canonicalGa4Sessions)

  const ga4Revenue = ga4ExplicitlyUnavailable
    ? null
    : isValidCanonicalMetric(canonicalGa4Revenue)
    ? canonicalGa4Revenue.value
    : ga4Summary && Number.isFinite(ga4Summary.revenue) && ga4Summary.revenue > 0
    ? ga4Summary.revenue
    : null

  const ga4Purchases = ga4ExplicitlyUnavailable
    ? null
    : isValidCanonicalMetric(canonicalGa4Purchases)
    ? canonicalGa4Purchases.value
    : ga4Summary && Number.isFinite(ga4Summary.purchases) && ga4Summary.purchases > 0
    ? ga4Summary.purchases
    : null

  const ga4Sessions = ga4ExplicitlyUnavailable
    ? null
    : isValidCanonicalMetric(canonicalGa4Sessions)
    ? canonicalGa4Sessions.value
    : ga4Summary && Number.isFinite(ga4Summary.sessions) && ga4Summary.sessions > 0
    ? ga4Summary.sessions
    : null

  const hasValidGa4 =
    ga4Revenue !== null || ga4Purchases !== null || ga4Sessions !== null

  // ── 1. Business Truth active ────────────────────────────────────────────────
  if (hasLiveBusinessOrders || hasValidCanonicalBusiness) {
    // Prefer exact-period live Business Truth when orders exist for the selected range;
    // otherwise use certified/canonical Business Truth snapshot values.
    const revenue = hasLiveBusinessOrders
      ? liveKpis!.totalRevenue
      : isValidCanonicalMetric(canonicalRevBiz)
      ? canonicalRevBiz.value
      : null

    const orders = hasLiveBusinessOrders
      ? liveKpis!.totalOrders
      : isValidCanonicalMetric(canonicalOrdersBiz)
      ? canonicalOrdersBiz.value
      : null

    const avgOrderValue = safeDivide(revenue, orders)

    // PRD §5.1: Business Truth for revenue/orders/AOV; GA4 sessions for visitors & conversion
    let visitors: number | null = null
    let visitorsSource: OverviewSourceKind = 'missing'
    if (ga4Summary && Number.isFinite(ga4Summary.sessions) && ga4Summary.sessions > 0) {
      visitors = ga4Summary.sessions
      visitorsSource = 'ga4'
    } else if (ga4Sessions !== null) {
      visitors = ga4Sessions
      visitorsSource = 'ga4'
    } else if (liveKpis && Number.isFinite(liveKpis.totalVisitors) && liveKpis.totalVisitors > 0) {
      visitors = liveKpis.totalVisitors
      visitorsSource = 'tracking'
    }

    const conversionRatio = safeDivide(orders, visitors)
    const conversionRate = conversionRatio !== null ? conversionRatio * 100 : null

    return {
      primarySource: 'business',
      isBusinessTruth: true,
      isGa4Fallback: false,
      revenue: makeCard(revenue, formatCurrencyOrDash, 'business'),
      orders: makeCard(orders, formatIntOrDash, 'business'),
      visitors: makeCard(visitors, formatIntOrDash, visitorsSource),
      avgOrderValue: makeCard(avgOrderValue, formatCurrencyOrDash, 'business'),
      conversionRate: makeCard(conversionRate, formatPctOrDash, 'business'),
    }
  }

  // ── 2. Journey Truth (GA4) fallback ─────────────────────────────────────────
  if (hasValidGa4) {
    const avgOrderValue = safeDivide(ga4Revenue, ga4Purchases)
    const conversionRatio = safeDivide(ga4Purchases, ga4Sessions)
    const conversionRate = conversionRatio !== null ? conversionRatio * 100 : null

    return {
      primarySource: 'ga4',
      isBusinessTruth: false,
      isGa4Fallback: true,
      revenue: makeCard(ga4Revenue, formatCurrencyOrDash, 'ga4'),
      orders: makeCard(ga4Purchases, formatIntOrDash, 'ga4'),
      visitors: makeCard(ga4Sessions, formatIntOrDash, 'ga4'),
      avgOrderValue: makeCard(avgOrderValue, formatCurrencyOrDash, 'ga4'),
      conversionRate: makeCard(conversionRate, formatPctOrDash, 'ga4'),
    }
  }

  // ── 3. Missing / no valid source — never coerce null to 0 or R$ 0,00 ────────
  return {
    primarySource: 'missing',
    isBusinessTruth: false,
    isGa4Fallback: false,
    revenue: makeCard(null, formatCurrencyOrDash, 'missing'),
    orders: makeCard(null, formatIntOrDash, 'missing'),
    visitors: makeCard(null, formatIntOrDash, 'missing'),
    avgOrderValue: makeCard(null, formatCurrencyOrDash, 'missing'),
    conversionRate: makeCard(null, formatPctOrDash, 'missing'),
  }
}

// ── Period coherence helpers ("Ontem" vs "Hoje") ──────────────────────────────

export function toLocalIsoDate(date: Date = new Date()): string {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

/**
 * Returns true ONLY when the active date selection represents the current day
 * ("Hoje"). Preset `'1d'` means "Ontem" (yesterday — a closed day) and MUST
 * NEVER display a partial-day warning.
 */
export function shouldShowPartialDayHint(
  period: string,
  from?: string,
  to?: string,
  todayIso: string = toLocalIsoDate(),
): boolean {
  if (period === 'today') return true
  if (period === 'custom' && from && to && from === todayIso && to === todayIso) {
    return true
  }
  return false
}

export function formatPartialDayHint(now: Date = new Date()): string {
  const elapsedPct = Math.round(((now.getHours() * 60 + now.getMinutes()) / 1440) * 100)
  return `⚠ parcial — ${elapsedPct}% do dia decorrido`
}
