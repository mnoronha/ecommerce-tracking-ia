const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

const root = path.resolve(__dirname, '..')
function load(file, overrides) {
  const source = fs.readFileSync(path.join(root, file), 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', compiled)(name => overrides[name] ?? require(name), mod, mod.exports)
  return mod.exports
}

const metric = (value, status = 'available') => ({ value, status })
const contract = {
  schema_version: 'norolabs-report-contract-v1',
  report: { type: 'monthly', client_slug: 'dipua', business_model: 'ecommerce', period: { start: '2026-09-01', end: '2026-09-30' }, comparison_period: null },
  business: { revenue: metric(null, 'access_missing'), orders: metric(0), average_order_value: metric(null, 'unknown'), mer: metric(null, 'stale') },
  paid_media: { google_ads: { spend: metric(0), platform_attributed_conversions: metric(4), canonical_conversions: metric(98765, 'unresolved_mapping'), conversion_mapping: { status: 'unresolved_mapping', detail: 'MAPPING_METADATA_MUST_NOT_RENDER_AS_METRIC' }, status: 'available' } },
}
let missing = false
const filters = []
const store = {
  from() { return this }, select() { return this }, order() { return this }, limit() { return this },
  eq(key, value) { filters.push([key, value]); return this },
  async maybeSingle() { return { data: missing ? null : { contract }, error: null } },
}
const lib = load('lib/agency-os.ts', { '@/lib/supabase-server': { createSupabaseServerClient: async () => store } })
const page = load('app/(app)/clients/[clientId]/performance/page.tsx', {
  '@/lib/agency-os': lib,
  'next/link': { __esModule: true, default: ({ children, ...props }) => React.createElement('a', props, children) },
})
async function render() {
  return renderToStaticMarkup(await page.default({ params: Promise.resolve({ clientId: 'dipua-qe5p' }), searchParams: Promise.resolve({ type: 'monthly' }) }))
}
;(async () => {
  const html = await render()
  assert(filters.some(([key, value]) => key === 'client_slug' && value === 'dipua'))
  assert(filters.some(([key, value]) => key === 'report_type' && value === 'monthly'))
  assert(html.includes('Performance Mensal'))
  assert(html.includes('R$ 0,00'))
  assert(html.includes('Acesso ausente'))
  assert(html.includes('Conversão não reconciliada'))
  assert(!html.includes('98765') && !html.includes('MAPPING_METADATA_MUST_NOT_RENDER_AS_METRIC'))
  assert(html.includes('Comparação anterior indisponível.'))
  assert(html.includes('Conversões atribuídas pela plataforma'))
  missing = true
  const errorHtml = await render()
  assert(errorHtml.includes('Relatório não encontrado'))
  assert(errorHtml.includes('?type=monthly') && errorHtml.includes('?type=weekly'))
  await assert.rejects(lib.getLatestMonthlyReport('unregistered-client'), err => err.code === 'not_found')
  console.log('PASS: monthly lookup, client alias, zero/missing distinction, mapping metadata and report navigation')
})().catch(err => { console.error(err); process.exitCode = 1 })
