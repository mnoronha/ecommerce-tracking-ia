import test from 'node:test'
import assert from 'node:assert/strict'
import {
  formatCurrencyOrDash,
  formatIntOrDash,
  formatPctOrDash,
  resolveOverviewKpis,
  shouldShowPartialDayHint,
} from './overview-kpis.ts'

test('Business Truth READY (e.g. LK Sneakers / Enutri) uses business revenue/orders/AOV and GA4 sessions for visitors & conversion', () => {
  const result = resolveOverviewKpis({
    canonicalHealth: { business: 'READY', ga4: 'READY' },
    canonicalMetrics: [
      { metric_key: 'revenue_business', value: 120000, value_status: 'OK', currency: 'BRL', domain: 'business' },
      { metric_key: 'orders_count', value: 300, value_status: 'OK', domain: 'business' },
      { metric_key: 'ga4_sessions', value: 15000, value_status: 'OK', domain: 'ga4' },
      { metric_key: 'ga4_purchases', value: 280, value_status: 'OK', domain: 'ga4' },
      { metric_key: 'ga4_revenue', value: 112000, value_status: 'OK', currency: 'BRL', domain: 'ga4' },
    ],
  })

  assert.equal(result.primarySource, 'business')
  assert.equal(result.isBusinessTruth, true)
  assert.equal(result.isGa4Fallback, false)

  assert.equal(result.revenue.value, 120000)
  assert.equal(result.revenue.sourceBadge, 'Business Truth')
  assert.equal(result.orders.value, 300)
  assert.equal(result.orders.sourceBadge, 'Business Truth')
  assert.equal(result.avgOrderValue.value, 400)
  assert.equal(result.avgOrderValue.sourceBadge, 'Business Truth')

  assert.equal(result.visitors.value, 15000)
  assert.equal(result.visitors.source, 'ga4')
  assert.equal(result.visitors.sourceBadge, 'Fonte: GA4')

  assert.ok(result.conversionRate.value !== null)
  assert.ok(Math.abs(result.conversionRate.value - 2.0) < 1e-9)
  assert.equal(result.conversionRate.sourceBadge, 'Business Truth')
})

test('Business MISSING / NOT_CONTRACTED / NO_DATA falls back explicitly to GA4 Journey Truth (Dipua scenario)', () => {
  for (const state of ['MISSING', 'NOT_CONTRACTED', 'NO_DATA']) {
    const result = resolveOverviewKpis({
      canonicalHealth: { business: state, ga4: 'READY' },
      canonicalMetrics: [
        { metric_key: 'ga4_sessions', value: 24666, value_status: 'OK', domain: 'ga4' },
        { metric_key: 'ga4_purchases', value: 600, value_status: 'OK', domain: 'ga4' },
        { metric_key: 'ga4_revenue', value: 194856.64, value_status: 'OK', currency: 'BRL', domain: 'ga4' },
      ],
      // Live Supabase orders table has 0 rows for Dipua (VNDA)
      liveKpis: {
        totalRevenue: 0,
        totalOrders: 0,
        totalVisitors: 0,
        avgOrderValue: 0,
        conversionRate: 0,
      },
      ga4Summary: null,
    })

    assert.equal(result.primarySource, 'ga4', `state=${state}`)
    assert.equal(result.isBusinessTruth, false, `state=${state}`)
    assert.equal(result.isGa4Fallback, true, `state=${state}`)

    assert.equal(result.revenue.value, 194856.64)
    assert.equal(result.revenue.sourceBadge, 'Fonte: GA4')
    assert.match(result.revenue.formatted, /194\.856,64/)

    assert.equal(result.orders.value, 600)
    assert.equal(result.orders.formatted, '600')
    assert.equal(result.orders.sourceBadge, 'Fonte: GA4')

    assert.equal(result.visitors.value, 24666)
    assert.equal(result.visitors.formatted, '24.666')
    assert.equal(result.visitors.sourceBadge, 'Fonte: GA4')

    assert.ok(result.avgOrderValue.value !== null)
    assert.ok(Math.abs(result.avgOrderValue.value - 324.7610666667) < 1e-4)
    assert.match(result.avgOrderValue.formatted, /324,76/)
    assert.equal(result.avgOrderValue.sourceBadge, 'Fonte: GA4')

    assert.ok(result.conversionRate.value !== null)
    assert.ok(Math.abs(result.conversionRate.value - 2.4324981756) < 1e-4)
    assert.equal(result.conversionRate.formatted, '2,43%')
    assert.equal(result.conversionRate.sourceBadge, 'Fonte: GA4')
  }
})

test('No valid Business or GA4 source renders strictly "—" and never coerces null to R$ 0,00 or 0', () => {
  const result = resolveOverviewKpis({
    canonicalHealth: { business: 'MISSING', ga4: 'MISSING' },
    canonicalMetrics: [
      { metric_key: 'revenue_business', value: null, value_status: 'MISSING', domain: 'business' },
      { metric_key: 'orders_count', value: null, value_status: 'MISSING', domain: 'business' },
      { metric_key: 'ga4_sessions', value: null, value_status: 'NO_DATA', domain: 'ga4' },
    ],
    liveKpis: {
      totalRevenue: 0,
      totalOrders: 0,
      totalVisitors: 0,
      avgOrderValue: 0,
      conversionRate: 0,
    },
    ga4Summary: null,
  })

  assert.equal(result.primarySource, 'missing')
  assert.equal(result.revenue.value, null)
  assert.equal(result.revenue.formatted, '—')
  assert.equal(result.revenue.sourceBadge, null)

  assert.equal(result.orders.value, null)
  assert.equal(result.orders.formatted, '—')

  assert.equal(result.visitors.value, null)
  assert.equal(result.visitors.formatted, '—')

  assert.equal(result.avgOrderValue.value, null)
  assert.equal(result.avgOrderValue.formatted, '—')

  assert.equal(result.conversionRate.value, null)
  assert.equal(result.conversionRate.formatted, '—')
})

test('Zero denominators in AOV and Conversion produce null / "—" without NaN or Infinity', () => {
  const result = resolveOverviewKpis({
    canonicalHealth: { business: 'MISSING', ga4: 'READY' },
    canonicalMetrics: [
      { metric_key: 'ga4_revenue', value: 500, value_status: 'OK', domain: 'ga4' },
      { metric_key: 'ga4_purchases', value: 0, value_status: 'OK', domain: 'ga4' },
      { metric_key: 'ga4_sessions', value: 0, value_status: 'OK', domain: 'ga4' },
    ],
  })

  assert.equal(result.avgOrderValue.value, null)
  assert.equal(result.avgOrderValue.formatted, '—')
  assert.equal(result.conversionRate.value, null)
  assert.equal(result.conversionRate.formatted, '—')
})

test('Formatters never turn null/undefined/NaN into zero', () => {
  assert.equal(formatCurrencyOrDash(null), '—')
  assert.equal(formatCurrencyOrDash(undefined), '—')
  assert.equal(formatCurrencyOrDash(Number.NaN), '—')
  assert.equal(formatIntOrDash(null), '—')
  assert.equal(formatPctOrDash(null), '—')
})

test('shouldShowPartialDayHint is false for "Ontem" (1d), 7d, 30d, 90d and past ranges; true ONLY for current day', () => {
  const today = '2026-11-28'
  const yesterday = '2026-11-27'

  assert.equal(shouldShowPartialDayHint('1d', undefined, undefined, today), false)
  assert.equal(shouldShowPartialDayHint('7d', undefined, undefined, today), false)
  assert.equal(shouldShowPartialDayHint('30d', undefined, undefined, today), false)
  assert.equal(shouldShowPartialDayHint('90d', undefined, undefined, today), false)
  assert.equal(shouldShowPartialDayHint('custom', yesterday, yesterday, today), false)
  assert.equal(shouldShowPartialDayHint('custom', yesterday, today, today), false)

  assert.equal(shouldShowPartialDayHint('today', undefined, undefined, today), true)
  assert.equal(shouldShowPartialDayHint('custom', today, today, today), true)
})

test('Live Business Truth orders for exact period are used when business is READY and paired with GA4 sessions', () => {
  const result = resolveOverviewKpis({
    canonicalHealth: { business: 'READY', ga4: 'READY' },
    canonicalMetrics: [
      { metric_key: 'revenue_business', value: 300000, value_status: 'OK', currency: 'BRL', domain: 'business' },
      { metric_key: 'orders_count', value: 750, value_status: 'OK', domain: 'business' },
      { metric_key: 'ga4_sessions', value: 4000, value_status: 'OK', domain: 'ga4' },
    ],
    liveKpis: {
      totalRevenue: 25000,
      totalOrders: 50,
      totalVisitors: 0,
      avgOrderValue: 500,
      conversionRate: 0,
    },
    ga4Summary: null,
  })

  assert.equal(result.primarySource, 'business')
  assert.equal(result.revenue.value, 25000)
  assert.equal(result.revenue.sourceBadge, 'Business Truth')
  assert.equal(result.orders.value, 50)
  assert.equal(result.avgOrderValue.value, 500)
  assert.equal(result.visitors.value, 4000)
  assert.equal(result.visitors.sourceBadge, 'Fonte: GA4')
  assert.equal(result.conversionRate.formatted, '1,25%')
})

