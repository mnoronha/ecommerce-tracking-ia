'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams }                         from 'next/navigation'
import Link                                  from 'next/link'
import {
  GitCommit, TrendingUp, TrendingDown, ArrowRight,
  Loader2, RefreshCw, ChevronDown, ChevronUp, AlertCircle,
  PlusCircle, CheckCircle2,
} from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface DigestChange {
  change_type: string
  impact: string
  entity_name: string
  occurred_at: string
  source?: string
  campaign_name?: string
  extra?: Record<string, unknown>
  batch_count?: number
  linked_action_id?: string
  linked_recommendation_id?: string
}

interface DigestSummary {
  total: number
  high_impact: number
  budget_changes: number
  campaign_activations: number
  campaign_pauses: number
}

interface ChangeDigest {
  id: string
  client_id: string
  platform: string
  period_start: string
  period_end: string
  generated_at: string
  total_raw_count: number
  relevant_count: number
  noise_count: number
  changes: DigestChange[]
  summary: DigestSummary
  limitations: string[]
  updated_at: string
}

interface ChangeRow {
  id:                  string
  occurred_at:         string
  channel:             string
  entity_type:         string
  entity_name_at_time: string
  change_type:         string
  source:              string
  reported_by?:        string
  confidence:          string
  before?:             unknown
  after?:              unknown
  reason?:             string
  task_id?:            string
  recommendation_id?:  string
}

// ── Constants ─────────────────────────────────────────────────────────────────

const PLATFORM_LABEL: Record<string, string> = {
  meta: 'Meta Ads', google: 'Google Ads', ga4: 'GA4',
}

const IMPACT_BADGE: Record<string, string> = {
  high:   'bg-red-500/15 text-red-400',
  medium: 'bg-yellow-500/15 text-yellow-400',
  low:    'bg-slate-500/15 text-slate-400',
}

const IMPACT_LABEL: Record<string, string> = {
  high: 'Alto', medium: 'Médio', low: 'Baixo',
}

const CHANGE_TYPE_LABEL: Record<string, string> = {
  BUDGET_CHANGE:          'Budget',
  CAMPAIGN_ACTIVATION:    'Campanha ativada',
  CAMPAIGN_PAUSE:         'Campanha pausada',
  CAMPAIGN_GROUP_ACTIVATION: 'Conjunto ativado',
  CAMPAIGN_GROUP_PAUSE:   'Conjunto pausado',
  AD_ACTIVATION:          'Anúncio ativado',
  AD_PAUSE:               'Anúncio pausado',
  NEW_ENTITY_CREATED:     'Entidade criada',
  SCHEDULE_CHANGE:        'Agendamento',
  AUDIENCE_CHANGE:        'Público',
  BALANCE_RECHARGE:       'Recarga de saldo',
}

const CHANGE_TYPE_BADGE: Record<string, string> = {
  BUDGET_CHANGE:    'bg-yellow-500/15 text-yellow-400',
  STATUS_CHANGE:    'bg-sky-500/15 text-sky-400',
  BID_CHANGE:       'bg-purple-500/15 text-purple-400',
  CREATIVE_CHANGE:  'bg-pink-500/15 text-pink-400',
  TARGETING_CHANGE: 'bg-orange-500/15 text-orange-400',
  CAMPAIGN_ACTIVATION:   'bg-emerald-500/15 text-emerald-400',
  CAMPAIGN_PAUSE:        'bg-red-500/15 text-red-400',
  OTHER:            'bg-slate-500/15 text-slate-400',
}

const PLATFORM_ACCENT: Record<string, string> = {
  meta:   'border-blue-500/20 bg-blue-500/5',
  google: 'border-red-500/20 bg-red-500/5',
  ga4:    'border-orange-500/20 bg-orange-500/5',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso?: string | null, short = false) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit',
    ...(short ? {} : { year: '2-digit', hour: '2-digit', minute: '2-digit' }),
    timeZone: 'America/Sao_Paulo',
  })
}

function fmtBRL(centavos: number): string {
  return `R$ ${(centavos / 100).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function renderValue(v: unknown): string {
  if (v == null) return '—'
  if (typeof v === 'string') return v
  if (typeof v === 'number') return String(v)
  if (typeof v === 'boolean') return v ? 'Sim' : 'Não'
  try { return JSON.stringify(v) } catch { return String(v) }
}

// ── Change Digest entry ────────────────────────────────────────────────────────

function DigestEntry({ ch }: { ch: DigestChange }) {
  const isBudget = ch.change_type === 'BUDGET_CHANGE'
  const extra = ch.extra ?? {}
  const beforeBRL = typeof extra.before_brl_centavos === 'number' ? fmtBRL(extra.before_brl_centavos) : null
  const afterBRL  = typeof extra.after_brl_centavos  === 'number' ? fmtBRL(extra.after_brl_centavos)  : null
  const delta = (typeof extra.before_brl_centavos === 'number' && typeof extra.after_brl_centavos === 'number' && extra.before_brl_centavos > 0)
    ? ((extra.after_brl_centavos - extra.before_brl_centavos) / extra.before_brl_centavos * 100)
    : null

  return (
    <div className="py-2.5 border-b border-[#2a2f3e] last:border-b-0">
      <div className="flex items-start gap-2">
        <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0 mt-0.5 ${IMPACT_BADGE[ch.impact] ?? IMPACT_BADGE.low}`}>
          {IMPACT_LABEL[ch.impact] ?? ch.impact}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <p className="text-xs text-slate-200 font-medium">{CHANGE_TYPE_LABEL[ch.change_type] ?? ch.change_type.replace(/_/g, ' ')}</p>
            {ch.batch_count && ch.batch_count > 1 && (
              <span className="text-[10px] text-slate-500">×{ch.batch_count}</span>
            )}
          </div>
          <p className="text-[10px] text-slate-500 mt-0.5 truncate">{ch.entity_name || ch.campaign_name || '—'}</p>

          {isBudget && beforeBRL && afterBRL && (
            <div className="flex items-center gap-1.5 mt-1 text-xs">
              <span className="text-slate-400">{beforeBRL}</span>
              <ArrowRight size={10} className="text-slate-600" />
              <span className={delta != null ? (delta > 0 ? 'text-emerald-400' : 'text-red-400') : 'text-slate-400'}>{afterBRL}</span>
              {delta != null && (
                <span className={`font-medium ${delta > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {delta > 0 ? <TrendingUp size={10} className="inline" /> : <TrendingDown size={10} className="inline" />}
                  {' '}{delta > 0 ? '+' : ''}{delta.toFixed(0)}%
                </span>
              )}
            </div>
          )}

          {ch.linked_recommendation_id && (
            <p className="text-[10px] text-indigo-400 mt-0.5">Relacionado a uma recomendação</p>
          )}
          {ch.linked_action_id && (
            <p className="text-[10px] text-sky-400 mt-0.5">Relacionado a uma ação</p>
          )}
        </div>
        <span className="text-[10px] text-slate-600 shrink-0">{fmtDate(ch.occurred_at, true)}</span>
      </div>
    </div>
  )
}

// ── Resumo Tab ────────────────────────────────────────────────────────────────

function ResumoTab({ digests }: { digests: ChangeDigest[] }) {
  if (digests.length === 0) return (
    <div className="py-12 flex flex-col items-center gap-3 text-center">
      <GitCommit size={28} className="text-slate-600" />
      <p className="text-xs text-slate-500">Nenhum digest de mudanças disponível.<br />Gerado automaticamente na revisão semanal.</p>
    </div>
  )

  // Group by period, then platform within period
  const byPeriod: Record<string, ChangeDigest[]> = {}
  for (const d of digests) {
    const key = `${d.period_start}/${d.period_end}`
    if (!byPeriod[key]) byPeriod[key] = []
    byPeriod[key].push(d)
  }

  return (
    <div className="space-y-6">
      {Object.entries(byPeriod).map(([period, group]) => {
        const [start, end] = period.split('/')
        return (
          <div key={period} className="space-y-3">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider">
              {fmtDate(start, true)} – {fmtDate(end, true)}
            </p>
            {group.map(d => {
              const highImpact = (d.changes ?? []).filter(c => c.impact === 'high')
              const medImpact  = (d.changes ?? []).filter(c => c.impact === 'medium')
              const shown      = [...highImpact, ...medImpact]

              return (
                <div key={d.id} className={`rounded-xl border p-4 space-y-3 ${PLATFORM_ACCENT[d.platform] ?? 'border-[#2a2f3e] bg-[#1a1f2e]'}`}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-bold text-white uppercase">{PLATFORM_LABEL[d.platform] ?? d.platform}</span>
                      <span className="text-[10px] text-slate-500">
                        {d.relevant_count} relevantes · {d.noise_count} ruído
                      </span>
                    </div>
                    <span className="text-[10px] text-slate-600">{fmtDate(d.generated_at, true)}</span>
                  </div>

                  {shown.length === 0 ? (
                    <p className="text-xs text-slate-600 italic">Nenhuma mudança relevante neste período.</p>
                  ) : (
                    <div className="divide-y-0">
                      {shown.map((ch, i) => <DigestEntry key={i} ch={ch} />)}
                    </div>
                  )}

                  {(d.limitations ?? []).length > 0 && (
                    <div className="pt-1 space-y-0.5">
                      {d.limitations.map((l, i) => (
                        <p key={i} className="text-[10px] text-slate-600 italic">{l}</p>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

// ── Histórico Tab ─────────────────────────────────────────────────────────────

function HistoricoTab({ clientId }: { clientId: string }) {
  const [changes,  setChanges]  = useState<ChangeRow[]>([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  // Filters
  const [filterPlatform,   setFilterPlatform]   = useState('')
  const [filterChangeType, setFilterChangeType] = useState('')

  // Debounced values to avoid rapid-fire requests on filter change
  const [debouncedPlatform,    setDebouncedPlatform]    = useState('')
  const [debouncedChangeType,  setDebouncedChangeType]  = useState('')

  useEffect(() => {
    const t = setTimeout(() => setDebouncedPlatform(filterPlatform), 400)
    return () => clearTimeout(t)
  }, [filterPlatform])

  useEffect(() => {
    const t = setTimeout(() => setDebouncedChangeType(filterChangeType), 400)
    return () => clearTimeout(t)
  }, [filterChangeType])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ limit: '50' })
      if (debouncedPlatform)    params.set('platform',    debouncedPlatform)
      if (debouncedChangeType)  params.set('change_type', debouncedChangeType)
      const res = await fetch(`/api/v1/clients/${clientId}/changes?${params}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setChanges(await res.json())
    } catch {
      setError('Não foi possível carregar o histórico.')
    } finally {
      setLoading(false)
    }
  }, [clientId, debouncedPlatform, debouncedChangeType])

  useEffect(() => { load() }, [load])

  function toggle(id: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  // Derive available platforms from current data
  const platforms    = [...new Set(changes.map(c => c.channel).filter(Boolean))]
  const changeTypes  = [...new Set(changes.map(c => c.change_type).filter(Boolean))]

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex items-center gap-2 flex-wrap">
        <select
          value={filterPlatform}
          onChange={e => setFilterPlatform(e.target.value)}
          className="text-xs bg-[#1a1f2e] border border-[#2a2f3e] text-slate-300 rounded-lg px-2 py-1.5 focus:outline-none"
        >
          <option value="">Todos os canais</option>
          {platforms.map(p => (
            <option key={p} value={p}>{PLATFORM_LABEL[p] ?? p}</option>
          ))}
        </select>
        <select
          value={filterChangeType}
          onChange={e => setFilterChangeType(e.target.value)}
          className="text-xs bg-[#1a1f2e] border border-[#2a2f3e] text-slate-300 rounded-lg px-2 py-1.5 focus:outline-none"
        >
          <option value="">Todos os tipos</option>
          {changeTypes.map(t => (
            <option key={t} value={t}>{CHANGE_TYPE_LABEL[t] ?? t.replace(/_/g, ' ')}</option>
          ))}
        </select>
        {(filterPlatform || filterChangeType) && (
          <button
            onClick={() => { setFilterPlatform(''); setFilterChangeType('') }}
            className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
          >
            Limpar filtros
          </button>
        )}
        {!loading && (
          <span className="text-[10px] text-slate-600 ml-auto">{changes.length} registro(s)</span>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl border border-red-500/20 bg-red-500/5 p-3">
          <AlertCircle size={13} className="text-red-400 shrink-0" />
          <p className="text-xs text-red-400">{error}</p>
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-slate-500 text-xs py-8">
          <Loader2 size={13} className="animate-spin" /> Carregando…
        </div>
      ) : changes.length === 0 ? (
        <div className="py-12 flex flex-col items-center gap-3 text-center">
          <GitCommit size={28} className="text-slate-600" />
          <p className="text-xs text-slate-500">Nenhuma mudança encontrada com estes filtros.</p>
        </div>
      ) : (
        <div className="space-y-1.5">
          {changes.map(ch => {
            const isOpen = expanded.has(ch.id)
            const bVal = (ch.before as { value?: unknown } | null | undefined)?.value
            const aVal = (ch.after  as { value?: unknown } | null | undefined)?.value
            const hasDelta = bVal != null && aVal != null

            return (
              <div key={ch.id} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
                <button
                  onClick={() => toggle(ch.id)}
                  className="w-full text-left px-4 py-3 flex items-center gap-3 hover:bg-[#252b3b] transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${CHANGE_TYPE_BADGE[ch.change_type] ?? CHANGE_TYPE_BADGE.OTHER}`}>
                        {CHANGE_TYPE_LABEL[ch.change_type] ?? ch.change_type.replace(/_/g, ' ')}
                      </span>
                      {ch.channel && (
                        <span className="text-[10px] font-semibold text-slate-400 uppercase">{PLATFORM_LABEL[ch.channel] ?? ch.channel}</span>
                      )}
                      <span className="text-xs text-slate-400 truncate">{ch.entity_name_at_time}</span>
                    </div>
                    {hasDelta ? (
                      <div className="flex items-center gap-1 mt-0.5">
                        <span className="text-[10px] text-slate-500">{renderValue(bVal)}</span>
                        <ArrowRight size={9} className="text-slate-600" />
                        <span className="text-[10px] text-slate-400">{renderValue(aVal)}</span>
                      </div>
                    ) : (
                      <p className="text-[10px] text-slate-600 mt-0.5">{fmtDate(ch.occurred_at)}</p>
                    )}
                  </div>
                  {isOpen
                    ? <ChevronUp size={13} className="text-slate-500 shrink-0" />
                    : <ChevronDown size={13} className="text-slate-500 shrink-0" />}
                </button>

                {isOpen && (
                  <div className="px-4 pb-4 border-t border-[#2a2f3e] pt-3 space-y-3">
                    <div className="grid grid-cols-2 gap-3 text-xs">
                      <div>
                        <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">Data</p>
                        <p className="text-slate-300">{fmtDate(ch.occurred_at)}</p>
                      </div>
                      <div>
                        <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">Entidade</p>
                        <p className="text-slate-300">{ch.entity_type}</p>
                      </div>
                      <div>
                        <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">Fonte</p>
                        <p className="text-slate-300">{ch.source}</p>
                      </div>
                      <div>
                        <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">Confiança</p>
                        <p className="text-slate-300">{ch.confidence}</p>
                      </div>
                      {ch.reported_by && (
                        <div>
                          <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">Registrado por</p>
                          <p className="text-slate-300">{ch.reported_by}</p>
                        </div>
                      )}
                    </div>

                    <div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">Motivo</p>
                      <p className="text-xs text-slate-300">{ch.reason ?? 'Motivo não registrado'}</p>
                    </div>

                    {hasDelta && (
                      <div className="grid grid-cols-2 gap-3">
                        {bVal != null && (
                          <div>
                            <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">Antes</p>
                            <pre className="text-[10px] text-slate-400 bg-[#0f1117] rounded p-2 overflow-auto">
                              {renderValue(bVal)}
                            </pre>
                          </div>
                        )}
                        {aVal != null && (
                          <div>
                            <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">Depois</p>
                            <pre className="text-[10px] text-slate-400 bg-[#0f1117] rounded p-2 overflow-auto">
                              {renderValue(aVal)}
                            </pre>
                          </div>
                        )}
                      </div>
                    )}

                    {(ch.recommendation_id || ch.task_id) && (
                      <div className="space-y-0.5">
                        {ch.recommendation_id && (
                          <p className="text-[10px] text-indigo-400">Relacionado à recomendação</p>
                        )}
                        {ch.task_id && (
                          <p className="text-[10px] text-sky-400">Relacionado à tarefa</p>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

type Tab = 'resumo' | 'historico' | 'registrar'

// ── RegistrarTab ──────────────────────────────────────────────────────────────

const CHANNEL_OPTIONS = [
  { value: 'meta',      label: 'Meta Ads' },
  { value: 'google',    label: 'Google Ads' },
  { value: 'pinterest', label: 'Pinterest' },
  { value: 'tracking',  label: 'Tracking' },
  { value: 'site',      label: 'Site' },
  { value: 'outro',     label: 'Outro' },
]

const CHANGE_TYPE_OPTIONS = [
  { value: 'BUDGET',         label: 'Orçamento' },
  { value: 'BID_STRATEGY',   label: 'Estratégia de Lance' },
  { value: 'CAMPAIGN_LAUNCH', label: 'Campanha Ativada' },
  { value: 'CAMPAIGN_PAUSE', label: 'Campanha Pausada' },
  { value: 'AUDIENCE',       label: 'Público' },
  { value: 'CREATIVE_LAUNCH', label: 'Criativo Lançado' },
  { value: 'CREATIVE_PAUSE', label: 'Criativo Pausado' },
  { value: 'ASSET_CHANGE',   label: 'Produto / Feed' },
  { value: 'TRACKING',       label: 'Tracking' },
  { value: 'CONVERSION_ACTION_CHANGE', label: 'Conversão' },
  { value: 'TARGET_ROAS',    label: 'Meta de ROAS' },
  { value: 'TARGET_CPA',     label: 'Meta de CPA' },
  { value: 'OTHER',          label: 'Outro' },
]

interface RegistrarForm {
  occurred_at:               string
  channel:                   string
  change_type:               string
  entity_name:               string
  entity_type:               string
  entity_id:                 string
  before:                    string
  after:                     string
  reason:                    string
  description:               string
  reported_by:               string
  recommendation_id:         string
  task_id:                   string
  linked_provider_change_id: string
}

function nowLocalISO(): string {
  const d = new Date()
  d.setSeconds(0, 0)
  return d.toISOString().slice(0, 16)
}

function RegistrarTab({ clientId }: { clientId: string }) {
  const [form, setForm] = useState<RegistrarForm>({
    occurred_at: nowLocalISO(),
    channel: 'meta',
    change_type: 'BUDGET',
    entity_name: '',
    entity_type: 'CAMPAIGN',
    entity_id: '',
    before: '',
    after: '',
    reason: '',
    description: '',
    reported_by: '',
    recommendation_id: '',
    task_id: '',
    linked_provider_change_id: '',
  })
  const [saving,  setSaving]  = useState(false)
  const [success, setSuccess] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  const set = (k: keyof RegistrarForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!form.entity_name.trim()) { setError('Nome da entidade é obrigatório.'); return }
    if (!form.reported_by.trim()) { setError('Responsável é obrigatório.'); return }
    setSaving(true)
    setError(null)
    setSuccess(false)
    try {
      const body: Record<string, unknown> = {
        occurred_at: new Date(form.occurred_at).toISOString(),
        channel:     form.channel,
        change_type: form.change_type,
        entity_name: form.entity_name.trim(),
        entity_type: form.entity_type.trim() || 'CAMPAIGN',
        reported_by: form.reported_by.trim(),
      }
      if (form.entity_id.trim())                 body.entity_id                 = form.entity_id.trim()
      if (form.before.trim())                    body.before                    = form.before.trim()
      if (form.after.trim())                     body.after                     = form.after.trim()
      if (form.reason.trim())                    body.reason                    = form.reason.trim()
      if (form.description.trim())               body.description               = form.description.trim()
      if (form.recommendation_id.trim())         body.recommendation_id         = form.recommendation_id.trim()
      if (form.task_id.trim())                   body.task_id                   = form.task_id.trim()
      if (form.linked_provider_change_id.trim()) body.linked_provider_change_id = form.linked_provider_change_id.trim()

      const res = await fetch(`/api/v1/clients/${clientId}/changes/manual`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(body),
      })
      if (!res.ok) {
        const json = await res.json().catch(() => ({}))
        throw new Error((json as { detail?: string }).detail || `Erro ${res.status}`)
      }
      setSuccess(true)
      setForm(f => ({ ...f, entity_name: '', entity_id: '', before: '', after: '', reason: '', description: '' }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Falha ao registrar alteração.')
    } finally {
      setSaving(false)
    }
  }

  const inputCls = 'w-full bg-[#0f1117] border border-[#2a2f3e] text-slate-200 text-xs rounded-lg px-3 py-2 focus:outline-none focus:border-indigo-500'
  const labelCls = 'block text-[10px] text-slate-500 mb-1 uppercase tracking-wide'

  return (
    <form onSubmit={handleSubmit} className="space-y-5 max-w-2xl">
      {success && (
        <div className="flex items-center gap-2 rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-3">
          <CheckCircle2 size={13} className="text-emerald-400 shrink-0" />
          <p className="text-xs text-emerald-400">Alteração registrada com sucesso.</p>
        </div>
      )}
      {error && (
        <div className="flex items-center gap-2 rounded-xl border border-red-500/20 bg-red-500/5 p-3">
          <AlertCircle size={13} className="text-red-400 shrink-0" />
          <p className="text-xs text-red-400">{error}</p>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className={labelCls}>Data / Hora</label>
          <input type="datetime-local" value={form.occurred_at} onChange={set('occurred_at')} className={inputCls} required />
        </div>
        <div>
          <label className={labelCls}>Plataforma</label>
          <select value={form.channel} onChange={set('channel')} className={inputCls}>
            {CHANNEL_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className={labelCls}>Tipo de Alteração</label>
          <select value={form.change_type} onChange={set('change_type')} className={inputCls}>
            {CHANGE_TYPE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div>
          <label className={labelCls}>Tipo de Entidade</label>
          <input placeholder="CAMPAIGN / ADSET / AD / FEED" value={form.entity_type} onChange={set('entity_type')} className={inputCls} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className={labelCls}>Entidade (nome) *</label>
          <input placeholder="Ex: Campanha Core — Tênis" value={form.entity_name} onChange={set('entity_name')} className={inputCls} required />
        </div>
        <div>
          <label className={labelCls}>ID da Entidade</label>
          <input placeholder="ID da campanha/adset (opcional)" value={form.entity_id} onChange={set('entity_id')} className={inputCls} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className={labelCls}>Antes</label>
          <input placeholder="Ex: R$ 263/dia" value={form.before} onChange={set('before')} className={inputCls} />
        </div>
        <div>
          <label className={labelCls}>Depois</label>
          <input placeholder="Ex: R$ 378/dia" value={form.after} onChange={set('after')} className={inputCls} />
        </div>
      </div>

      <div>
        <label className={labelCls}>Motivo da Alteração</label>
        <textarea rows={2} placeholder="Por que foi feita essa alteração?" value={form.reason} onChange={set('reason')} className={`${inputCls} resize-none`} />
      </div>

      <div>
        <label className={labelCls}>Hipótese / Objetivo</label>
        <textarea rows={2} placeholder="O que se espera que aconteça com essa mudança?" value={form.description} onChange={set('description')} className={`${inputCls} resize-none`} />
      </div>

      <div>
        <label className={labelCls}>Responsável *</label>
        <input placeholder="Nome de quem realizou a alteração" value={form.reported_by} onChange={set('reported_by')} className={inputCls} required />
      </div>

      <details className="group">
        <summary className="text-[10px] text-slate-600 cursor-pointer hover:text-slate-400 select-none uppercase tracking-wide">
          Vínculos (opcional)
        </summary>
        <div className="mt-3 space-y-3">
          <div>
            <label className={labelCls}>ID da Recomendação relacionada</label>
            <input placeholder="UUID de core_recommendations" value={form.recommendation_id} onChange={set('recommendation_id')} className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>ID da Tarefa relacionada</label>
            <input placeholder="UUID de core_tasks" value={form.task_id} onChange={set('task_id')} className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>Vincular a alteração automática (provider change)</label>
            <input placeholder="UUID de core_change_log (detectado automaticamente)" value={form.linked_provider_change_id} onChange={set('linked_provider_change_id')} className={inputCls} />
            <p className="text-[10px] text-slate-600 mt-1">Associa o contexto humano a uma mudança detectada pelo Google/Meta.</p>
          </div>
        </div>
      </details>

      <div className="pt-2">
        <button
          type="submit"
          disabled={saving}
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-semibold rounded-lg transition-colors"
        >
          {saving ? <Loader2 size={12} className="animate-spin" /> : <PlusCircle size={12} />}
          {saving ? 'Registrando…' : 'Registrar alteração'}
        </button>
      </div>
    </form>
  )
}

export default function ChangesPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [tab,     setTab]     = useState<Tab>('resumo')
  const [digests, setDigests] = useState<ChangeDigest[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const loadDigests = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/v1/clients/${clientId}/change-digests?limit=10`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setDigests(await res.json())
    } catch {
      setError('Não foi possível carregar o resumo de mudanças.')
    } finally {
      setLoading(false)
    }
  }, [clientId])

  useEffect(() => { loadDigests() }, [loadDigests])

  return (
    <div className="p-6 max-w-4xl space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-white">Mudanças</h2>
          <p className="text-[10px] text-slate-500 mt-0.5">resumo por período · histórico completo</p>
        </div>
        {tab === 'resumo' && (
          <button onClick={loadDigests} disabled={loading} className="text-slate-500 hover:text-white transition-colors disabled:opacity-50">
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-[#151a27] rounded-lg p-1 border border-[#2a2f3e] w-fit">
        {([
          { id: 'resumo',    label: 'Resumo' },
          { id: 'historico', label: 'Histórico' },
          { id: 'registrar', label: 'Registrar alteração' },
        ] as { id: Tab; label: string }[]).map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-1.5 rounded text-xs font-medium transition-colors ${
              tab === t.id ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
            }`}
          >
            {t.id === 'registrar' && <PlusCircle size={10} className="inline mr-1 -mt-0.5" />}
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'resumo' && (
        <>
          {error && (
            <div className="flex items-center gap-2 rounded-xl border border-red-500/20 bg-red-500/5 p-3">
              <AlertCircle size={13} className="text-red-400 shrink-0" />
              <p className="text-xs text-red-400">{error}</p>
            </div>
          )}
          {loading ? (
            <div className="flex items-center gap-2 text-slate-500 text-xs py-8">
              <Loader2 size={13} className="animate-spin" /> Carregando resumo…
            </div>
          ) : (
            <ResumoTab digests={digests} />
          )}
        </>
      )}

      {tab === 'historico' && (
        <HistoricoTab clientId={clientId} />
      )}

      {tab === 'registrar' && (
        <RegistrarTab clientId={clientId} />
      )}

      {/* Cross-link */}
      {tab !== 'registrar' && (
        <div className="pt-2 border-t border-[#2a2f3e] flex items-center gap-4">
          <Link href={`/clients/${clientId}/actions`} className="text-[10px] text-slate-600 hover:text-indigo-400 transition-colors">
            Ver ações →
          </Link>
        </div>
      )}
    </div>
  )
}
