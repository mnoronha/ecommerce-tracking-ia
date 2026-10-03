const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const root = path.resolve(__dirname, '..')
function load(file, overrides) {
  const source = fs.readFileSync(path.join(root, file), 'utf8')
  const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', compiled)(name => overrides[name] ?? require(name), mod, mod.exports)
  return mod.exports
}
const contract = {
  schema_version: 'norolabs-operations-contract-v1', client_slug: 'dipua', source_run_id: 'synthetic-test-run', generated_at: '2026-10-02T12:00:00Z', current_period: '2026-10',
  capabilities: { media_analysis: true, collection_automation_readonly: true, conversion_analysis: false, business_analysis: false, journey_analysis: true, decision_automation: false, execution: false },
  truth_gates: { business_truth: 'access_missing' }, target_truth: { period: '2026-09', state: 'active', status: 'ACTIVE' },
  source_status: { google_ads: 'PASS', meta_ads: 'permission_denied', ga4: 'available' },
  human_dependencies: [{ dependency_id: 'SYNTHETIC-PENDING', type: 'BUSINESS_DATA', status: 'OPEN', blocking_level: 'BLOCKING', requested_action: 'Identificar a fonte oficial de vendas ou CRM.' }],
  workflows: { creative: { state: 'BLOCKED', service_status: 'UNKNOWN', blockers: ['CREATIVE_SERVICE_NOT_CONFIRMED', 'APPROVED_SEED_ASSET_MISSING'], asset_ids: [] }, campaign: { state: 'BLOCKED', blockers: ['CURRENT_TARGET_TRUTH_MISSING', 'CONVERSION_SEMANTICS_UNRESOLVED'], create_paused: true, activate_after_creation: false } },
  governance: { human_review_required: true, decision_automation: false, execution: false, performance_alerts_enabled: false },
  alerts: [{ alert: { alert_id: 'SYNTHETIC-ALERT', client_slug: 'dipua', type: 'PERMISSION_DENIED', severity: 'P1', state: 'OPEN', source: 'meta_ads', created_at: '2026-10-02T12:00:00Z', details: { state: 'permission_denied', evidence: '<script>UNSAFE_MARKER</script>' } }, diagnosis: { diagnosis_id: 'SYNTHETIC-DIAG', diagnosis: 'A fonte registra permissão negada.', confidence: 'OBSERVED_OPERATIONAL_RECORD', facts: [] }, recommendation: { recommendation_id: 'SYNTHETIC-REC', proposed_action: 'Revisar a permissão da integração.', risk: 'Verificar a conta correta.', rollback: 'Preservar o cadastro anterior.', reevaluation_window: 'Após nova coleta validada.' } }],
}
let missing = false
const filters = []
const store = { from(name) { assert.equal(name, 'agency_operations_contracts'); return this }, select() { return this }, order(key, options) { assert.equal(options.ascending, false); return this }, limit(value) { assert.equal(value, 1); return this }, eq(key, value) { filters.push([key, value]); return this }, async maybeSingle() { return { data: missing ? null : { contract }, error: null } } }
const reportLib = load('lib/agency-os.ts', { '@/lib/supabase-server': { createSupabaseServerClient: async () => store } })
const lib = load('lib/agency-operations.ts', { '@/lib/agency-os': reportLib, '@/lib/supabase-server': { createSupabaseServerClient: async () => store } })
const page = load('app/(app)/clients/[clientId]/agency-os/page.tsx', { '@/lib/agency-os': reportLib, '@/lib/agency-operations': lib, 'next/link': { __esModule: true, default: ({ children, ...props }) => React.createElement('a', props, children) } })
const render = async () => renderToStaticMarkup(await page.default({ params: Promise.resolve({ clientId: 'dipua-qe5p' }) }))
;(async () => {
  const html = await render()
  assert(filters.some(([key, value]) => key === 'client_slug' && value === 'dipua'))
  for (const text of ['Agency OS', 'Permissão negada', 'Revisão humana obrigatória', 'Fonte de vendas ou CRM', 'Creative Engine', 'Campaign Builder', 'Aprovar metas e orçamento do período atual']) assert(html.includes(text), text)
  assert(html.includes('&lt;script&gt;UNSAFE_MARKER&lt;/script&gt;') && !html.includes('<script>UNSAFE_MARKER'))
  assert(html.includes('Bloqueada') && !html.includes('R$ 0'))
  if (process.env.OPERATIONS_PREVIEW_OUTPUT) {
    const css = 'body{background:#0f1117;color:#d4dbe9;font:14px system-ui;margin:0}h1{font-size:30px}h2{font-size:20px}h3{font-size:16px}.p-6{padding:24px;max-width:1200px;margin:auto}.p-5{padding:20px}.space-y-6>*+*{margin-top:24px}.space-y-3>*+*{margin-top:12px}.space-y-2>*+*{margin-top:8px}.grid{display:grid;gap:16px}.md\\:grid-cols-2{grid-template-columns:1fr 1fr}.flex{display:flex}.flex-wrap{flex-wrap:wrap}.gap-2{gap:8px}.gap-3{gap:12px}.justify-between{justify-content:space-between}.rounded-xl{border-radius:12px}.border{border:1px solid #303949}.bg-slate-900\\/60{background:#151d2c}.text-sm{font-size:14px}.text-xs{font-size:12px}.text-slate-400{color:#9ba8bd}.text-slate-500{color:#8291a9}.text-amber-300{color:#fcd34d}.text-emerald-300{color:#6ee7b7}.text-indigo-200,.text-indigo-400{color:#a5b4fc}a{color:#a5b4fc}pre{white-space:pre-wrap;background:#080d19;padding:12px;border-radius:8px}strong{font-weight:600}summary{cursor:pointer}.preview{padding:10px;background:#312954;text-align:center}'
    fs.writeFileSync(process.env.OPERATIONS_PREVIEW_OUTPUT, '<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>Prévia Agency OS</title><style>'+css+'</style><body><div class="preview">PRÉVIA PARA REVISÃO · DADOS SINTÉTICOS</div>'+html+'</body></html>')
  }
  contract.alerts[0].alert.state = 'RESOLVED'
  contract.alerts[0].alert.resolution = { reason: 'Synthetic resolution', evidence: 'Synthetic evidence', resolved_by: 'Synthetic reviewer' }
  const resolved = await render()
  assert(resolved.includes('Synthetic resolution') && resolved.includes('Synthetic evidence'))
  missing = true
  const empty = await render()
  assert(empty.includes('Ausência de atualização não confirma que as fontes estão saudáveis.'))
  await assert.rejects(lib.getLatestOperations('colab55'), err => err.code === 'not_found')
  missing = false
  contract.client_slug = 'lk-sneakers'
  await assert.rejects(lib.getLatestOperations('dipua-qe5p'), err => err.code === 'unavailable')
  console.log('PASS: canonical routing, operational states, human resolution, escaping, missing snapshots and client isolation')
})().catch(error => { console.error(error); process.exitCode = 1 })
