'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams }                         from 'next/navigation'
import Link                                  from 'next/link'
import {
  ArrowLeft, Lightbulb, ChevronDown, ChevronUp,
  Loader2, RefreshCw, CheckCircle2, XCircle, Clock,
  Zap, BarChart2, BookOpen,
} from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Recommendation {
  id:                      string
  diagnosis_id:            string
  alert_id:                string
  client_id?:              string
  title:                   string
  action:                  string
  rationale:               string
  priority:                string
  risk?:                   string
  expected_impact?:        string
  requires_human_approval: boolean
  status:                  string
  created_at:              string
  created_by:              string
}

interface Action {
  id:                      string
  recommendation_id:       string
  action_type:             string
  platform?:               string | null
  entity_type?:            string | null
  entity_id?:              string | null
  status:                  string
  executed_by?:            string | null
  executed_at?:            string | null
  evaluation_window_days:  number
  before_state:            Record<string, unknown>
  after_state:             Record<string, unknown>
  reason?:                 string | null
  requires_external_write: boolean
  change_log_id?:          string | null
  created_at:              string
}

interface Outcome {
  id:                     string
  action_id?:             string | null
  status:                 string
  confidence?:            string | null
  evaluation_window_days?: number | null
  delta?:                 Record<string, unknown> | null
  data_health:            Record<string, unknown>
  measured_at?:           string | null
  created_at:             string
}

type DecisionType = 'ACCEPTED' | 'REJECTED' | 'DEFERRED'

interface ActiveDecision {
  recId:      string
  decision:   DecisionType
  reason:     string
  submitting: boolean
  error:      string | null
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const PRIORITY_BADGE: Record<string, string> = {
  HIGH:    'bg-red-500/15 text-red-400 border-red-500/30',
  MEDIUM:  'bg-yellow-500/15 text-yellow-400 border-yellow-500/30',
  LOW:     'bg-slate-500/15 text-slate-400 border-slate-500/30',
}

const STATUS_BADGE: Record<string, string> = {
  PROPOSED:  'bg-indigo-500/15 text-indigo-400',
  APPROVED:  'bg-emerald-500/15 text-emerald-400',
  REJECTED:  'bg-red-500/15 text-red-400',
  DEFERRED:  'bg-amber-500/15 text-amber-400',
  EXECUTED:  'bg-sky-500/15 text-sky-400',
}

const OUTCOME_BADGE: Record<string, string> = {
  POSITIVE:             'bg-emerald-500/15 text-emerald-400',
  NEGATIVE:             'bg-red-500/15 text-red-400',
  NEUTRAL:              'bg-slate-500/15 text-slate-400',
  INCONCLUSIVE:         'bg-amber-500/15 text-amber-400',
  BLOCKED_DATA_QUALITY: 'bg-orange-500/15 text-orange-400',
  PENDING:              'bg-slate-500/10 text-slate-500',
  CONFIRMED:            'bg-emerald-500/15 text-emerald-400',
}

const DECISION_LABELS: Record<DecisionType, string> = {
  ACCEPTED: 'Aprovar',
  DEFERRED: 'Adiar',
  REJECTED: 'Rejeitar',
}

const DECIDABLE_STATES = new Set(['PROPOSED', 'DEFERRED'])

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
    timeZone: 'America/Sao_Paulo',
  })
}

// ── Decision confirmation form ─────────────────────────────────────────────────

function DecisionForm({
  active,
  onReasonChange,
  onConfirm,
  onCancel,
}: {
  active:         ActiveDecision
  onReasonChange: (v: string) => void
  onConfirm:      () => void
  onCancel:       () => void
}) {
  const label = DECISION_LABELS[active.decision]
  const accent =
    active.decision === 'ACCEPTED' ? 'emerald' :
    active.decision === 'REJECTED' ? 'red' : 'amber'

  const accentBorder =
    accent === 'emerald' ? 'border-emerald-500/30' :
    accent === 'red'     ? 'border-red-500/30'     : 'border-amber-500/30'

  const accentText =
    accent === 'emerald' ? 'text-emerald-400' :
    accent === 'red'     ? 'text-red-400'     : 'text-amber-400'

  const confirmCls =
    accent === 'emerald'
      ? 'bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-600/40'
      : accent === 'red'
      ? 'bg-red-600/20 hover:bg-red-600/30 text-red-300 border border-red-600/40'
      : 'bg-amber-600/20 hover:bg-amber-600/30 text-amber-300 border border-amber-600/40'

  return (
    <div className={`mt-4 rounded-lg border ${accentBorder} bg-[#1a1f2e]/80 p-4 space-y-3`}>
      <p className={`text-xs font-semibold ${accentText}`}>Confirmar: {label}?</p>
      <textarea
        value={active.reason}
        onChange={e => onReasonChange(e.target.value)}
        disabled={active.submitting}
        placeholder="Motivo (opcional)"
        rows={2}
        className="w-full rounded-md bg-[#252b3b] border border-[#3a3f4e] text-sm text-slate-300 placeholder-slate-600 px-3 py-2 resize-none focus:outline-none focus:border-slate-500 disabled:opacity-50"
      />
      {active.error && (
        <p className="text-xs text-red-400">{active.error}</p>
      )}
      <div className="flex items-center gap-2">
        <button
          onClick={onConfirm}
          disabled={active.submitting}
          className={`inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md transition-colors disabled:opacity-50 ${confirmCls}`}
        >
          {active.submitting
            ? <><Loader2 size={11} className="animate-spin" /> Enviando…</>
            : label}
        </button>
        <button
          onClick={onCancel}
          disabled={active.submitting}
          className="text-xs text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-50"
        >
          Cancelar
        </button>
      </div>
    </div>
  )
}

// ── Decision banner (post-decision status) ────────────────────────────────────

function DecisionBanner({ status }: { status: string }) {
  if (status === 'APPROVED') {
    return (
      <div className="flex items-start gap-2 rounded-lg bg-emerald-500/10 border border-emerald-500/25 px-4 py-3 mt-4">
        <CheckCircle2 size={14} className="shrink-0 mt-0.5 text-emerald-400" />
        <p className="text-xs text-emerald-300 leading-relaxed">
          Aprovada para possível execução. Registre uma ação abaixo para fechar o loop.
        </p>
      </div>
    )
  }
  if (status === 'DEFERRED') {
    return (
      <div className="flex items-start gap-2 rounded-lg bg-amber-500/10 border border-amber-500/25 px-4 py-3 mt-4">
        <Clock size={14} className="shrink-0 mt-0.5 text-amber-400" />
        <p className="text-xs text-amber-300 leading-relaxed">
          Adiada — nenhuma alteração será executada.
        </p>
      </div>
    )
  }
  if (status === 'REJECTED') {
    return (
      <div className="flex items-start gap-2 rounded-lg bg-red-500/10 border border-red-500/25 px-4 py-3 mt-4">
        <XCircle size={14} className="shrink-0 mt-0.5 text-red-400" />
        <p className="text-xs text-red-300 leading-relaxed">Rejeitada.</p>
      </div>
    )
  }
  return null
}

// ── Outcome badge ─────────────────────────────────────────────────────────────

function OutcomeChip({ status, confidence }: { status: string; confidence?: string | null }) {
  const cls = OUTCOME_BADGE[status] || 'bg-slate-500/10 text-slate-500'
  return (
    <span className={`text-xs px-2 py-0.5 rounded font-medium ${cls}`}>
      {status}{confidence ? ` · ${confidence}` : ''}
    </span>
  )
}

// ── Action panel ──────────────────────────────────────────────────────────────

function ActionPanel({ recommendationId }: { recommendationId: string }) {
  const [actions,  setActions]  = useState<Action[]>([])
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({})
  const [loading,  setLoading]  = useState(true)
  const [creating, setCreating] = useState(false)
  const [formErr,  setFormErr]  = useState<string | null>(null)

  const [form, setForm] = useState({
    action_type:             'MANUAL_EXTERNAL_ACTION',
    platform:                '',
    entity_type:             '',
    reason:                  '',
    evaluation_window_days:  '7',
    requires_external_write: false,
  })

  const loadActions = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`/api/v1/recommendations/${recommendationId}/actions`)
      if (res.ok) {
        const data: Action[] = await res.json()
        setActions(data)
        // Load outcomes for EXECUTED actions
        const executed = data.filter(a => a.status === 'EXECUTED')
        for (const a of executed) {
          if (!outcomes[a.id]) {
            const or = await fetch(`/api/v1/actions/${a.id}/outcome`)
            if (or.ok) {
              const o: Outcome | null = await or.json()
              if (o) setOutcomes(prev => ({ ...prev, [a.id]: o }))
            }
          }
        }
      }
    } finally {
      setLoading(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recommendationId])

  useEffect(() => { loadActions() }, [loadActions])

  async function submitCreate() {
    setCreating(true)
    setFormErr(null)
    try {
      const res = await fetch(`/api/v1/recommendations/${recommendationId}/actions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action_type:             form.action_type,
          platform:                form.platform || null,
          entity_type:             form.entity_type || null,
          reason:                  form.reason || null,
          evaluation_window_days:  parseInt(form.evaluation_window_days, 10) || 7,
          requires_external_write: form.requires_external_write,
          idempotency_key:         crypto.randomUUID(),
        }),
      })
      const data = await res.json().catch(() => null)
      if (!res.ok) {
        const msg = (data as { detail?: string; error?: string })?.detail ||
          (data as { detail?: string; error?: string })?.error ||
          `Erro ${res.status}`
        setFormErr(msg)
        return
      }
      await loadActions()
      setForm({ action_type: 'MANUAL_EXTERNAL_ACTION', platform: '', entity_type: '', reason: '', evaluation_window_days: '7', requires_external_write: false })
    } catch {
      setFormErr('Falha de comunicação. Tente novamente.')
    } finally {
      setCreating(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-slate-500 text-xs py-3">
        <Loader2 size={11} className="animate-spin" /> Carregando ações…
      </div>
    )
  }

  return (
    <div className="mt-4 space-y-3">
      <div className="flex items-center gap-2">
        <Zap size={12} className="text-sky-400" />
        <p className="text-xs font-semibold text-slate-300">Ações registradas</p>
      </div>

      {actions.length === 0 ? (
        <p className="text-xs text-slate-500 pl-4">Nenhuma ação registrada ainda.</p>
      ) : (
        <div className="space-y-2 pl-4">
          {actions.map(a => {
            const outcome = outcomes[a.id]
            return (
              <div key={a.id} className="rounded-lg bg-[#252b3b] border border-[#3a3f4e] px-4 py-3 space-y-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs text-slate-300 font-medium">{a.action_type}</span>
                  <span className={`text-xs px-2 py-0.5 rounded font-medium ${
                    a.status === 'EXECUTED'  ? 'bg-sky-500/15 text-sky-400' :
                    a.status === 'PLANNED'   ? 'bg-slate-500/15 text-slate-400' :
                    a.status === 'FAILED'    ? 'bg-red-500/15 text-red-400' :
                    a.status === 'CANCELLED' ? 'bg-slate-500/15 text-slate-500' :
                    'bg-slate-500/15 text-slate-400'
                  }`}>{a.status}</span>
                  {a.platform && <span className="text-xs text-slate-500">{a.platform}</span>}
                </div>
                {a.status === 'EXECUTED' && (
                  <p className="text-xs text-slate-500">
                    Executado por {a.executed_by || '—'} em {fmtDate(a.executed_at)}
                  </p>
                )}
                {a.reason && <p className="text-xs text-slate-400 italic">{a.reason}</p>}
                {outcome && (
                  <div className="flex items-center gap-2 pt-1">
                    <BarChart2 size={10} className="text-slate-500" />
                    <span className="text-xs text-slate-500">Outcome:</span>
                    <OutcomeChip status={outcome.status} confidence={outcome.confidence} />
                    {outcome.measured_at && (
                      <span className="text-xs text-slate-600">{fmtDate(outcome.measured_at)}</span>
                    )}
                  </div>
                )}
                {a.status === 'EXECUTED' && !outcome && (
                  <p className="text-xs text-slate-600 italic">Avaliação de outcome pendente (janela: {a.evaluation_window_days}d)</p>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Create action form — only if no EXECUTED action exists yet */}
      {!actions.some(a => a.status === 'EXECUTED') && (
        <div className="pl-4 pt-2">
          <p className="text-xs text-slate-500 mb-2">Registrar ação executada externamente:</p>
          <div className="space-y-2">
            <select
              value={form.action_type}
              onChange={e => setForm(prev => ({ ...prev, action_type: e.target.value }))}
              className="w-full rounded-md bg-[#252b3b] border border-[#3a3f4e] text-xs text-slate-300 px-3 py-1.5 focus:outline-none focus:border-slate-500"
            >
              <option value="MANUAL_EXTERNAL_ACTION">Ação externa manual</option>
              <option value="INTERNAL_ACTION">Ação interna</option>
              <option value="NO_ACTION_REQUIRED">Nenhuma ação necessária</option>
            </select>
            <div className="flex gap-2">
              <input
                value={form.platform}
                onChange={e => setForm(prev => ({ ...prev, platform: e.target.value }))}
                placeholder="Plataforma (ex: google, meta)"
                className="flex-1 rounded-md bg-[#252b3b] border border-[#3a3f4e] text-xs text-slate-300 placeholder-slate-600 px-3 py-1.5 focus:outline-none focus:border-slate-500"
              />
              <input
                value={form.entity_type}
                onChange={e => setForm(prev => ({ ...prev, entity_type: e.target.value }))}
                placeholder="Tipo entidade (ex: CAMPAIGN)"
                className="flex-1 rounded-md bg-[#252b3b] border border-[#3a3f4e] text-xs text-slate-300 placeholder-slate-600 px-3 py-1.5 focus:outline-none focus:border-slate-500"
              />
            </div>
            <input
              value={form.reason}
              onChange={e => setForm(prev => ({ ...prev, reason: e.target.value }))}
              placeholder="Descrição / motivo (opcional)"
              className="w-full rounded-md bg-[#252b3b] border border-[#3a3f4e] text-xs text-slate-300 placeholder-slate-600 px-3 py-1.5 focus:outline-none focus:border-slate-500"
            />
            <div className="flex items-center gap-2">
              <input
                type="number"
                min="1"
                max="90"
                value={form.evaluation_window_days}
                onChange={e => setForm(prev => ({ ...prev, evaluation_window_days: e.target.value }))}
                className="w-24 rounded-md bg-[#252b3b] border border-[#3a3f4e] text-xs text-slate-300 px-3 py-1.5 focus:outline-none focus:border-slate-500"
              />
              <span className="text-xs text-slate-500">dias de avaliação</span>
            </div>
            {formErr && <p className="text-xs text-red-400">{formErr}</p>}
            <button
              onClick={submitCreate}
              disabled={creating}
              className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md bg-sky-600/20 hover:bg-sky-600/30 text-sky-300 border border-sky-600/40 transition-colors disabled:opacity-50"
            >
              {creating ? <><Loader2 size={11} className="animate-spin" /> Registrando…</> : 'Registrar ação'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function RecommendationsPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [items,    setItems]    = useState<Recommendation[]>([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [active,   setActive]   = useState<ActiveDecision | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/v1/clients/${clientId}/recommendations`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setItems(await res.json())
    } catch {
      setError('Não foi possível carregar as recomendações.')
    } finally {
      setLoading(false)
    }
  }, [clientId])

  useEffect(() => { load() }, [load])

  function toggle(id: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
    setActive(prev => (prev?.recId === id ? null : prev))
  }

  function startDecision(recId: string, decision: DecisionType) {
    setActive({ recId, decision, reason: '', submitting: false, error: null })
  }

  async function submitDecision() {
    if (!active) return

    const idempotencyKey = crypto.randomUUID()
    setActive(prev => prev ? { ...prev, submitting: true, error: null } : prev)

    try {
      const res = await fetch(
        `/api/v1/recommendations/${active.recId}/decision`,
        {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({
            decision:        active.decision,
            reason:          active.reason.trim() || undefined,
            idempotency_key: idempotencyKey,
          }),
        },
      )

      const data = await res.json().catch(() => null)

      if (!res.ok) {
        const msg =
          (data as { detail?: string; error?: string })?.detail ||
          (data as { detail?: string; error?: string })?.error ||
          `Erro ${res.status}`
        setActive(prev => prev ? { ...prev, submitting: false, error: msg } : prev)
        return
      }

      if (data && typeof data === 'object' && 'id' in data) {
        setItems(prev => prev.map(r => r.id === active.recId ? { ...r, ...(data as Partial<Recommendation>) } : r))
      }
      setActive(null)
    } catch {
      setActive(prev => prev ? { ...prev, submitting: false, error: 'Falha de comunicação. Tente novamente.' } : prev)
    }
  }

  return (
    <div className="p-8 max-w-4xl">
      {/* Header */}
      <div className="mb-8 flex items-start justify-between gap-4">
        <div>
          <Link href={`/clients/${clientId}/alertas`} className="inline-flex items-center gap-1.5 text-slate-500 hover:text-white text-xs mb-3 transition-colors">
            <ArrowLeft size={12} /> Alertas
          </Link>
          <div className="flex items-center gap-2">
            <Lightbulb size={16} className="text-indigo-400" />
            <h1 className="text-xl font-bold text-white">Recomendações</h1>
          </div>
          <p className="text-xs text-slate-500 mt-1">
            Recomendações geradas pelo Hermes · fonte: Agency API Core
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="text-slate-500 hover:text-white disabled:opacity-50 transition-colors"
          aria-label="Recarregar"
        >
          <RefreshCw size={14} />
        </button>
      </div>

      {error && (
        <div className="rounded-xl border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-400 mb-6">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-slate-500 text-sm py-12">
          <Loader2 size={16} className="animate-spin" /> Carregando…
        </div>
      ) : items.length === 0 ? (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-12 text-center">
          <Lightbulb size={32} className="text-slate-600 mx-auto mb-3" />
          <p className="text-slate-400 font-medium">Nenhuma recomendação registrada</p>
          <p className="text-slate-600 text-xs mt-1">
            O Hermes ainda não gerou recomendações para este cliente.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map(rec => {
            const isOpen       = expanded.has(rec.id)
            const isDecidable  = DECIDABLE_STATES.has(rec.status) && rec.requires_human_approval
            const isActiveForm = active?.recId === rec.id
            const showActions  = rec.status === 'APPROVED' || rec.status === 'EXECUTED'

            return (
              <div key={rec.id} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
                <button
                  onClick={() => toggle(rec.id)}
                  className="w-full text-left px-5 py-4 flex items-start gap-3 hover:bg-[#252b3b] transition-colors"
                >
                  <Lightbulb size={14} className="shrink-0 mt-0.5 text-indigo-400" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                      <span className={`text-xs px-2 py-0.5 rounded border font-medium ${PRIORITY_BADGE[rec.priority] || PRIORITY_BADGE.LOW}`}>
                        {rec.priority}
                      </span>
                      <span className={`text-xs px-2 py-0.5 rounded font-medium ${STATUS_BADGE[rec.status] || 'bg-slate-500/15 text-slate-400'}`}>
                        {rec.status}
                      </span>
                      {rec.requires_human_approval && (
                        <span className="text-xs text-slate-500">· aprovação humana</span>
                      )}
                      {showActions && (
                        <span className="inline-flex items-center gap-1 text-xs text-sky-500">
                          <Zap size={9} /> ação
                        </span>
                      )}
                    </div>
                    <p className="text-sm font-semibold text-white">{rec.title}</p>
                    <p className="text-xs text-slate-500 mt-0.5">{fmtDate(rec.created_at)}</p>
                  </div>
                  {isOpen ? <ChevronUp size={14} className="text-slate-500 shrink-0" /> : <ChevronDown size={14} className="text-slate-500 shrink-0" />}
                </button>

                {isOpen && (
                  <div className="px-5 pb-5 border-t border-[#2a2f3e] space-y-4">
                    <div className="mt-4">
                      <p className="text-xs font-semibold text-slate-400 mb-1">Ação</p>
                      <p className="text-sm text-slate-300 leading-relaxed">{rec.action}</p>
                    </div>
                    <div>
                      <p className="text-xs font-semibold text-slate-400 mb-1">Racional</p>
                      <p className="text-sm text-slate-300 leading-relaxed">{rec.rationale}</p>
                    </div>
                    {rec.expected_impact && (
                      <div>
                        <p className="text-xs font-semibold text-slate-400 mb-1">Impacto esperado</p>
                        <p className="text-sm text-slate-300 leading-relaxed">{rec.expected_impact}</p>
                      </div>
                    )}
                    {rec.risk && (
                      <div>
                        <p className="text-xs font-semibold text-slate-400 mb-1">Risco</p>
                        <p className="text-sm text-yellow-300 leading-relaxed">{rec.risk}</p>
                      </div>
                    )}

                    {/* Decision status banner */}
                    <DecisionBanner status={rec.status} />

                    {/* Decision buttons — only for decidable recommendations */}
                    {isDecidable && !isActiveForm && (
                      <div className="pt-3 border-t border-[#2a2f3e]">
                        <p className="text-xs text-slate-500 mb-2">Decisão humana</p>
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => startDecision(rec.id, 'ACCEPTED')}
                            className="text-xs px-3 py-1.5 rounded-md bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-600/40 transition-colors"
                          >
                            Aprovar
                          </button>
                          <button
                            onClick={() => startDecision(rec.id, 'DEFERRED')}
                            className="text-xs px-3 py-1.5 rounded-md bg-amber-600/20 hover:bg-amber-600/30 text-amber-300 border border-amber-600/40 transition-colors"
                          >
                            Adiar
                          </button>
                          <button
                            onClick={() => startDecision(rec.id, 'REJECTED')}
                            className="text-xs px-3 py-1.5 rounded-md bg-red-600/20 hover:bg-red-600/30 text-red-300 border border-red-600/40 transition-colors"
                          >
                            Rejeitar
                          </button>
                        </div>
                      </div>
                    )}

                    {/* Active decision confirmation form */}
                    {isActiveForm && active && (
                      <DecisionForm
                        active={active}
                        onReasonChange={v => setActive(prev => prev ? { ...prev, reason: v } : prev)}
                        onConfirm={submitDecision}
                        onCancel={() => setActive(null)}
                      />
                    )}

                    {/* Action → Outcome → Learning panel (shown after APPROVED) */}
                    {showActions && (
                      <div className="pt-3 border-t border-[#2a2f3e]">
                        <ActionPanel recommendationId={rec.id} />
                      </div>
                    )}

                    {/* Learning candidates link */}
                    {showActions && (
                      <div className="flex items-center gap-2 pt-1">
                        <BookOpen size={10} className="text-slate-500" />
                        <Link
                          href={`/clients/${clientId}/learning-candidates`}
                          className="text-xs text-slate-500 hover:text-slate-400"
                        >
                          Ver candidatos de aprendizado →
                        </Link>
                      </div>
                    )}

                    <div className="flex items-center gap-4 pt-2 border-t border-[#2a2f3e]">
                      <Link
                        href={`/clients/${clientId}/alertas`}
                        className="text-xs text-indigo-400 hover:text-indigo-300"
                      >
                        ← Ver alerta
                      </Link>
                      <Link
                        href={`/api/v1/alerts/${rec.alert_id}/diagnoses`}
                        className="text-xs text-slate-500 hover:text-slate-400"
                        target="_blank"
                      >
                        diagnóstico ↗
                      </Link>
                      <span className="text-xs text-slate-600">por {rec.created_by}</span>
                    </div>
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
