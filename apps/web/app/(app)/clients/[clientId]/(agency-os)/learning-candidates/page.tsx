'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams }                         from 'next/navigation'
import Link                                  from 'next/link'
import {
  BookOpen, ArrowLeft, ChevronDown, ChevronUp,
  Loader2, RefreshCw, CheckCircle2, XCircle,
  AlertTriangle, ExternalLink, Info, Upload, Sparkles,
} from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface LearningCandidate {
  id:                       string
  statement:                string
  scope:                    string
  evidence_level:           string
  evidence_refs:            string[]
  context:                  Record<string, unknown>
  client_id?:               string | null
  visibility_scope:         string
  status:                   string
  source_action_id?:        string | null
  source_recommendation_id?: string | null
  pattern?:                 string | null
  observed_outcome:         Record<string, unknown>
  created_at:               string
}

interface PromotionResult {
  id:                   string
  knowledge_id?:        string | null
  status:               string
  document_path?:       string | null
  document_version?:    number
  obsidian_status?:     string
  obsidian_url?:        string | null
  promoted_at?:         string | null
  error?:               string | null
}

type Decision = 'APPROVED' | 'REJECTED'

interface ActiveDecision {
  candidateId: string
  decision:    Decision
  reason:      string
  submitting:  boolean
  error:       string | null
}

interface ActivePromotion {
  candidateId: string
  reason:      string
  submitting:  boolean
  error:       string | null
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const SCOPE_LABEL: Record<string, string> = {
  CLIENT_ONLY:          'Somente este cliente',
  BUSINESS_MODEL:       'Modelo de negócio',
  PLATFORM:             'Plataforma',
  AGENCY_WIDE_CANDIDATE: 'Candidato agência',
  CLIENT:               'Somente este cliente',
  AGENCY:               'Candidato agência',
}

const EVIDENCE_LABEL: Record<string, string> = {
  OBSERVATION: 'Observação',
  WEAK:        'Fraca',
  MODERATE:    'Moderada',
  STRONG:      'Forte',
}

const STATUS_CLS: Record<string, string> = {
  PENDING:    'bg-amber-500/15 text-amber-400 border border-amber-500/30',
  APPROVED:   'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30',
  REJECTED:   'bg-red-500/15 text-red-400 border border-red-500/30',
  SUPERSEDED: 'bg-slate-500/15 text-slate-500 border border-slate-500/20',
}

const OUTCOME_CLS: Record<string, string> = {
  POSITIVE:             'text-emerald-400',
  NEGATIVE:             'text-red-400',
  NEUTRAL:              'text-slate-400',
  INCONCLUSIVE:         'text-amber-400',
  BLOCKED_DATA_QUALITY: 'text-orange-400',
}

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
    timeZone: 'America/Sao_Paulo',
  })
}

function shortId(id: string | null | undefined) {
  if (!id) return null
  return id.slice(0, 8) + '…'
}

// ── Governance notice ─────────────────────────────────────────────────────────

function GovernanceNotice() {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 mb-6">
      <AlertTriangle size={14} className="shrink-0 mt-0.5 text-amber-400" />
      <div className="text-xs text-amber-300 space-y-0.5">
        <p className="font-semibold">APPROVED ≠ PROMOTED TO KNOWLEDGE</p>
        <p className="text-amber-400/80 leading-relaxed">
          Aprovar um candidato significa validar que o aprendizado é confiável.
          Isso <strong>não</strong> publica automaticamente no Obsidian / Agency Knowledge Base.
          A promoção para conhecimento é uma etapa separada, controlada manualmente pela agência.
        </p>
      </div>
    </div>
  )
}

// ── Decision form ─────────────────────────────────────────────────────────────

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
  const isApprove = active.decision === 'APPROVED'
  const accentCls = isApprove
    ? 'border-emerald-500/30 text-emerald-400'
    : 'border-red-500/30 text-red-400'
  const btnCls = isApprove
    ? 'bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-600/40'
    : 'bg-red-600/20 hover:bg-red-600/30 text-red-300 border border-red-600/40'

  return (
    <div className={`mt-4 rounded-lg border ${accentCls.split(' ')[0]} bg-[#1a1f2e]/80 p-4 space-y-3`}>
      <p className={`text-xs font-semibold ${accentCls.split(' ')[1]}`}>
        Confirmar: {isApprove ? 'Aprovar aprendizado?' : 'Rejeitar aprendizado?'}
      </p>
      {isApprove && (
        <p className="text-xs text-amber-400/80 leading-relaxed">
          Lembre-se: aprovação não publica no Knowledge Base. É uma validação humana do candidato.
        </p>
      )}
      <textarea
        value={active.reason}
        onChange={e => onReasonChange(e.target.value)}
        disabled={active.submitting}
        placeholder="Motivo / justificativa (opcional)"
        rows={2}
        className="w-full rounded-md bg-[#252b3b] border border-[#3a3f4e] text-sm text-slate-300 placeholder-slate-600 px-3 py-2 resize-none focus:outline-none focus:border-slate-500 disabled:opacity-50"
      />
      {active.error && <p className="text-xs text-red-400">{active.error}</p>}
      <div className="flex items-center gap-2">
        <button
          onClick={onConfirm}
          disabled={active.submitting}
          className={`inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md transition-colors disabled:opacity-50 ${btnCls}`}
        >
          {active.submitting
            ? <><Loader2 size={11} className="animate-spin" /> Enviando…</>
            : isApprove ? 'Aprovar aprendizado' : 'Rejeitar aprendizado'}
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

// ── Promotion form ────────────────────────────────────────────────────────────

function PromotionForm({
  active,
  onReasonChange,
  onConfirm,
  onCancel,
}: {
  active:         ActivePromotion
  onReasonChange: (v: string) => void
  onConfirm:      () => void
  onCancel:       () => void
}) {
  return (
    <div className="mt-4 rounded-lg border border-indigo-500/30 bg-[#1a1f2e]/80 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Sparkles size={12} className="text-indigo-400" />
        <p className="text-xs font-semibold text-indigo-300">
          Confirmar: Promover para Agency Knowledge?
        </p>
      </div>
      <div className="text-xs text-slate-500 space-y-1 pl-5">
        <p>• Este aprendizado será publicado permanentemente no Knowledge Base.</p>
        <p>• Hermes poderá lê-lo como inteligência aprovada pela agência.</p>
        <p>• Ação irreversível — cria um documento versionado.</p>
      </div>
      <textarea
        value={active.reason}
        onChange={e => onReasonChange(e.target.value)}
        disabled={active.submitting}
        placeholder="Motivo da promoção (opcional, mas recomendado)"
        rows={2}
        className="w-full rounded-md bg-[#252b3b] border border-[#3a3f4e] text-sm text-slate-300 placeholder-slate-600 px-3 py-2 resize-none focus:outline-none focus:border-slate-500 disabled:opacity-50"
      />
      {active.error && <p className="text-xs text-red-400">{active.error}</p>}
      <div className="flex items-center gap-2">
        <button
          onClick={onConfirm}
          disabled={active.submitting}
          className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md transition-colors disabled:opacity-50 bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-600/40"
        >
          {active.submitting
            ? <><Loader2 size={11} className="animate-spin" /> Promovendo…</>
            : <><Upload size={11} /> Promover para Knowledge</>}
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

// ── Promotion result badge ────────────────────────────────────────────────────

function PromotedBadge({ result }: { result: PromotionResult }) {
  const isNew     = result.status === 'COMPLETED' || result.status === 'NEW_VERSION'
  const isExist   = result.status === 'EXISTING_KNOWLEDGE'
  const isFailed  = result.status === 'FAILED'

  if (isFailed) {
    return (
      <div className="flex items-start gap-2 rounded-lg bg-red-500/10 border border-red-500/25 px-4 py-3">
        <XCircle size={13} className="shrink-0 mt-0.5 text-red-400" />
        <div className="text-xs text-red-300 space-y-0.5">
          <p className="font-semibold">Falha na promoção</p>
          {result.error && <p className="text-red-400/80">{result.error}</p>}
        </div>
      </div>
    )
  }

  return (
    <div className="flex items-start gap-2 rounded-lg bg-indigo-500/10 border border-indigo-500/25 px-4 py-3">
      <Sparkles size={13} className="shrink-0 mt-0.5 text-indigo-400" />
      <div className="text-xs text-indigo-300 space-y-1">
        <p className="font-semibold">
          {isExist ? 'Padrão já existe no Knowledge Base' : 'Promovido para Agency Knowledge'}
          {result.status === 'NEW_VERSION' && ' (nova versão)'}
        </p>
        {result.knowledge_id && (
          <p className="text-indigo-400/80 font-mono text-xs">{shortId(result.knowledge_id)}</p>
        )}
        {result.document_path && (
          <p className="text-indigo-400/70 font-mono text-xs break-all">{result.document_path}</p>
        )}
        {result.document_version && result.document_version > 1 && (
          <p className="text-indigo-400/70">versão {result.document_version}</p>
        )}
        {result.obsidian_url && (
          <a
            href={result.obsidian_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-indigo-400 hover:text-indigo-300"
          >
            Ver no GitHub <ExternalLink size={9} />
          </a>
        )}
        {result.promoted_at && (
          <p className="text-indigo-500">{fmtDate(result.promoted_at)}</p>
        )}
        {(isNew || isExist) && (
          <p className="text-xs text-amber-400/70 mt-1">
            Hermes pode ler este conhecimento como inteligência aprovada.
          </p>
        )}
      </div>
    </div>
  )
}

// ── Traceability row ──────────────────────────────────────────────────────────

function TraceRow({
  label,
  id,
  href,
}: {
  label: string
  id: string | null | undefined
  href?: string
}) {
  if (!id) return null
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-slate-600 w-36 shrink-0">{label}</span>
      {href ? (
        <Link href={href} className="inline-flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300 font-mono">
          {shortId(id)} <ExternalLink size={9} />
        </Link>
      ) : (
        <span className="text-xs text-slate-500 font-mono">{shortId(id)}</span>
      )}
    </div>
  )
}

// ── Candidate card ────────────────────────────────────────────────────────────

function CandidateCard({
  candidate,
  clientId,
  activeDecision,
  activePromotion,
  promotionResults,
  onStartDecision,
  onDecisionReasonChange,
  onDecisionConfirm,
  onDecisionCancel,
  onStartPromotion,
  onPromotionReasonChange,
  onPromotionConfirm,
  onPromotionCancel,
}: {
  candidate:              LearningCandidate
  clientId:               string
  activeDecision:         ActiveDecision | null
  activePromotion:        ActivePromotion | null
  promotionResults:       Record<string, PromotionResult>
  onStartDecision:        (id: string, d: Decision) => void
  onDecisionReasonChange: (v: string) => void
  onDecisionConfirm:      () => void
  onDecisionCancel:       () => void
  onStartPromotion:       (id: string) => void
  onPromotionReasonChange:(v: string) => void
  onPromotionConfirm:     () => void
  onPromotionCancel:      () => void
}) {
  const [open, setOpen] = useState(false)
  const isPending        = candidate.status === 'PENDING'
  const isApproved       = candidate.status === 'APPROVED'
  const isDecisionForm   = activeDecision?.candidateId === candidate.id
  const isPromotionForm  = activePromotion?.candidateId === candidate.id
  const promotionResult  = promotionResults[candidate.id]
  const isPromoted       = !!promotionResult && (
    promotionResult.status === 'COMPLETED' ||
    promotionResult.status === 'NEW_VERSION' ||
    promotionResult.status === 'EXISTING_KNOWLEDGE'
  )

  const outcomeStatus  = candidate.observed_outcome?.outcome_status as string | undefined
  const deltaPct       = candidate.observed_outcome?.delta_pct as number | null | undefined

  return (
    <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full text-left px-5 py-4 flex items-start gap-3 hover:bg-[#252b3b] transition-colors"
      >
        <BookOpen size={14} className="shrink-0 mt-0.5 text-indigo-400" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span className={`text-xs px-2 py-0.5 rounded font-medium ${STATUS_CLS[candidate.status] || STATUS_CLS.PENDING}`}>
              {candidate.status}
            </span>
            {isPromoted && (
              <span className="text-xs px-2 py-0.5 rounded font-medium bg-indigo-500/15 text-indigo-400 border border-indigo-500/30 inline-flex items-center gap-1">
                <Sparkles size={9} /> PROMOTED
              </span>
            )}
            <span className="text-xs px-2 py-0.5 rounded bg-slate-500/10 text-slate-500">
              {SCOPE_LABEL[candidate.scope] || candidate.scope}
            </span>
            <span className="text-xs px-2 py-0.5 rounded bg-slate-500/10 text-slate-400">
              {EVIDENCE_LABEL[candidate.evidence_level] || candidate.evidence_level}
            </span>
            {outcomeStatus && (
              <span className={`text-xs font-medium ${OUTCOME_CLS[outcomeStatus] || 'text-slate-400'}`}>
                ↳ {outcomeStatus}{deltaPct != null ? ` (${deltaPct > 0 ? '+' : ''}${deltaPct.toFixed(1)}%)` : ''}
              </span>
            )}
          </div>
          <p className="text-sm font-medium text-white leading-snug">{candidate.statement}</p>
          {candidate.pattern && (
            <p className="text-xs text-slate-600 mt-0.5 font-mono">{candidate.pattern}</p>
          )}
          <p className="text-xs text-slate-600 mt-0.5">{fmtDate(candidate.created_at)}</p>
        </div>
        {open ? <ChevronUp size={14} className="text-slate-500 shrink-0" /> : <ChevronDown size={14} className="text-slate-500 shrink-0" />}
      </button>

      {open && (
        <div className="px-5 pb-5 border-t border-[#2a2f3e] space-y-4">

          {/* Context */}
          {Object.keys(candidate.context).length > 0 && (
            <div className="mt-4">
              <p className="text-xs font-semibold text-slate-400 mb-1">Contexto</p>
              <pre className="text-xs text-slate-400 bg-[#252b3b] rounded-lg px-3 py-2 overflow-x-auto whitespace-pre-wrap">
                {JSON.stringify(candidate.context, null, 2)}
              </pre>
            </div>
          )}

          {/* Observed outcome */}
          {Object.keys(candidate.observed_outcome).length > 0 && (
            <div>
              <p className="text-xs font-semibold text-slate-400 mb-1">Outcome observado</p>
              <div className="flex items-center gap-3 flex-wrap">
                {outcomeStatus && (
                  <span className={`text-xs font-semibold ${OUTCOME_CLS[outcomeStatus] || 'text-slate-400'}`}>
                    {outcomeStatus}
                  </span>
                )}
                {deltaPct != null && (
                  <span className="text-xs text-slate-400">
                    Δ {deltaPct > 0 ? '+' : ''}{deltaPct.toFixed(2)}%
                  </span>
                )}
                {(candidate.observed_outcome.action_type as string) && (
                  <span className="text-xs text-slate-500">
                    via {candidate.observed_outcome.action_type as string}
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Evidence refs */}
          {candidate.evidence_refs.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-slate-400 mb-1">Evidências</p>
              <div className="space-y-1">
                {candidate.evidence_refs.map((ref, i) => (
                  <span key={i} className="block text-xs text-slate-500 font-mono">{ref}</span>
                ))}
              </div>
            </div>
          )}

          {/* Traceability */}
          <div>
            <p className="text-xs font-semibold text-slate-400 mb-2">Rastreabilidade</p>
            <div className="space-y-1.5 pl-2 border-l border-[#2a2f3e]">
              <TraceRow
                label="Learning Candidate"
                id={candidate.id}
              />
              <TraceRow
                label="← Ação"
                id={candidate.source_action_id}
              />
              <TraceRow
                label="← Recomendação"
                id={candidate.source_recommendation_id}
                href={candidate.source_recommendation_id
                  ? `/clients/${clientId}/recommendations`
                  : undefined}
              />
            </div>
            {(!candidate.source_action_id && !candidate.source_recommendation_id) && (
              <p className="text-xs text-slate-600 pl-2 italic">Sem rastreabilidade de ação/recomendação</p>
            )}
          </div>

          {/* Promotion result (if already promoted) */}
          {promotionResult && (
            <PromotedBadge result={promotionResult} />
          )}

          {/* APPROVED governance notice + promote button */}
          {isApproved && !isPromoted && !isPromotionForm && (
            <div className="space-y-3">
              <div className="flex items-start gap-2 rounded-lg bg-emerald-500/10 border border-emerald-500/25 px-4 py-3">
                <CheckCircle2 size={13} className="shrink-0 mt-0.5 text-emerald-400" />
                <div className="text-xs text-emerald-300 space-y-0.5">
                  <p className="font-semibold">Validado por humano</p>
                  <p className="text-emerald-400/80">
                    Ainda não publicado no Knowledge Base. A promoção abaixo é uma etapa manual e permanente.
                  </p>
                </div>
              </div>
              <div className="pt-1">
                <button
                  onClick={() => onStartPromotion(candidate.id)}
                  className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md transition-colors bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-600/40"
                >
                  <Upload size={11} /> Promover para Agency Knowledge
                </button>
              </div>
            </div>
          )}

          {isApproved && !isPromoted && isPromotionForm && activePromotion && (
            <PromotionForm
              active={activePromotion}
              onReasonChange={onPromotionReasonChange}
              onConfirm={onPromotionConfirm}
              onCancel={onPromotionCancel}
            />
          )}

          {candidate.status === 'REJECTED' && (
            <div className="flex items-start gap-2 rounded-lg bg-red-500/10 border border-red-500/25 px-4 py-3">
              <XCircle size={13} className="shrink-0 mt-0.5 text-red-400" />
              <p className="text-xs text-red-300">Rejeitado — não será promovido para Knowledge Base.</p>
            </div>
          )}

          {/* Decision buttons */}
          {isPending && !isDecisionForm && (
            <div className="pt-3 border-t border-[#2a2f3e]">
              <p className="text-xs text-slate-500 mb-2">Decisão humana</p>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => onStartDecision(candidate.id, 'APPROVED')}
                  className="text-xs px-3 py-1.5 rounded-md bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-600/40 transition-colors"
                >
                  Aprovar aprendizado
                </button>
                <button
                  onClick={() => onStartDecision(candidate.id, 'REJECTED')}
                  className="text-xs px-3 py-1.5 rounded-md bg-red-600/20 hover:bg-red-600/30 text-red-300 border border-red-600/40 transition-colors"
                >
                  Rejeitar aprendizado
                </button>
              </div>
            </div>
          )}

          {isDecisionForm && activeDecision && (
            <DecisionForm
              active={activeDecision}
              onReasonChange={onDecisionReasonChange}
              onConfirm={onDecisionConfirm}
              onCancel={onDecisionCancel}
            />
          )}
        </div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

const STATUS_FILTERS = ['ALL', 'PENDING', 'APPROVED', 'REJECTED', 'SUPERSEDED'] as const
type StatusFilter = typeof STATUS_FILTERS[number]

export default function LearningCandidatesPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [items,            setItems]            = useState<LearningCandidate[]>([])
  const [loading,          setLoading]          = useState(true)
  const [error,            setError]            = useState<string | null>(null)
  const [filter,           setFilter]           = useState<StatusFilter>('ALL')
  const [activeDecision,   setActiveDecision]   = useState<ActiveDecision | null>(null)
  const [activePromotion,  setActivePromotion]  = useState<ActivePromotion | null>(null)
  const [promotionResults, setPromotionResults] = useState<Record<string, PromotionResult>>({})

  const load = useCallback(async (statusFilter: StatusFilter = filter) => {
    setLoading(true)
    setError(null)
    try {
      const qs = statusFilter !== 'ALL' ? `?status=${statusFilter}` : ''
      const res = await fetch(`/api/v1/clients/${clientId}/learning-candidates${qs}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setItems(await res.json())
    } catch {
      setError('Não foi possível carregar os aprendizados.')
    } finally {
      setLoading(false)
    }
  }, [clientId, filter])

  useEffect(() => { load() }, [load])

  function handleFilterChange(f: StatusFilter) {
    setFilter(f)
    load(f)
  }

  // ── Decision handlers ─────────────────────────────────────────────────────

  function startDecision(candidateId: string, decision: Decision) {
    setActivePromotion(null)
    setActiveDecision({ candidateId, decision, reason: '', submitting: false, error: null })
  }

  async function submitDecision() {
    if (!activeDecision) return
    const idempotencyKey = crypto.randomUUID()
    setActiveDecision(prev => prev ? { ...prev, submitting: true, error: null } : prev)

    try {
      const res = await fetch(
        `/api/v1/learning-candidates/${activeDecision.candidateId}/decision`,
        {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({
            decision:        activeDecision.decision,
            note:            activeDecision.reason.trim() || undefined,
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
        setActiveDecision(prev => prev ? { ...prev, submitting: false, error: msg } : prev)
        return
      }

      if (data && typeof data === 'object' && 'id' in data) {
        setItems(prev =>
          prev.map(c => c.id === activeDecision.candidateId ? { ...c, ...(data as Partial<LearningCandidate>) } : c),
        )
      }
      setActiveDecision(null)
    } catch {
      setActiveDecision(prev => prev ? { ...prev, submitting: false, error: 'Falha de comunicação. Tente novamente.' } : prev)
    }
  }

  // ── Promotion handlers ────────────────────────────────────────────────────

  function startPromotion(candidateId: string) {
    setActiveDecision(null)
    setActivePromotion({ candidateId, reason: '', submitting: false, error: null })
  }

  async function submitPromotion() {
    if (!activePromotion) return
    const idempotencyKey = crypto.randomUUID()
    setActivePromotion(prev => prev ? { ...prev, submitting: true, error: null } : prev)

    try {
      const res = await fetch(
        `/api/v1/learning-candidates/${activePromotion.candidateId}/promote`,
        {
          method:  'POST',
          headers: {
            'Content-Type':  'application/json',
            'Idempotency-Key': idempotencyKey,
          },
          body: JSON.stringify({
            reason: activePromotion.reason.trim() || null,
          }),
        },
      )
      const data = await res.json().catch(() => null) as PromotionResult | null

      if (!res.ok) {
        const msg =
          (data as { detail?: string; error?: string } | null)?.detail ||
          (data as { detail?: string; error?: string } | null)?.error ||
          `Erro ${res.status}`
        setActivePromotion(prev => prev ? { ...prev, submitting: false, error: msg } : prev)
        return
      }

      if (data) {
        setPromotionResults(prev => ({ ...prev, [activePromotion.candidateId]: data }))
      }
      setActivePromotion(null)
    } catch {
      setActivePromotion(prev => prev ? { ...prev, submitting: false, error: 'Falha de comunicação. Tente novamente.' } : prev)
    }
  }

  const pendingCount  = items.filter(c => c.status === 'PENDING').length
  const approvedCount = items.filter(c => c.status === 'APPROVED').length

  return (
    <div className="p-8 max-w-4xl">
      {/* Header */}
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <Link
            href={`/clients/${clientId}/recommendations`}
            className="inline-flex items-center gap-1.5 text-slate-500 hover:text-white text-xs mb-3 transition-colors"
          >
            <ArrowLeft size={12} /> Recomendações
          </Link>
          <div className="flex items-center gap-2">
            <BookOpen size={16} className="text-indigo-400" />
            <h1 className="text-xl font-bold text-white">Aprendizados</h1>
            {pendingCount > 0 && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-400 font-medium">
                {pendingCount} pendente{pendingCount > 1 ? 's' : ''}
              </span>
            )}
            {approvedCount > 0 && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/20 font-medium">
                {approvedCount} aprovado{approvedCount > 1 ? 's' : ''}
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-1">
            Candidatos a aprendizado extraídos de outcomes validados · revisão humana obrigatória
          </p>
        </div>
        <button
          onClick={() => load()}
          disabled={loading}
          className="text-slate-500 hover:text-white disabled:opacity-50 transition-colors"
          aria-label="Recarregar"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Governance notice */}
      <GovernanceNotice />

      {/* Filter pills */}
      <div className="flex items-center gap-2 flex-wrap mb-5">
        {STATUS_FILTERS.map(f => (
          <button
            key={f}
            onClick={() => handleFilterChange(f)}
            className={`text-xs px-3 py-1 rounded-full border transition-colors ${
              filter === f
                ? 'bg-indigo-600/20 text-indigo-300 border-indigo-500/40'
                : 'text-slate-500 border-[#2a2f3e] hover:text-slate-300 hover:border-slate-500/40'
            }`}
          >
            {f === 'ALL' ? 'Todos' : f}
          </button>
        ))}
      </div>

      {/* Info box about decision audit */}
      <div className="flex items-start gap-2 rounded-lg bg-[#1a1f2e] border border-[#2a2f3e] px-4 py-3 mb-5">
        <Info size={12} className="shrink-0 mt-0.5 text-slate-500" />
        <p className="text-xs text-slate-500 leading-relaxed">
          Decisões são registradas com <strong className="text-slate-400">actor</strong> (email do usuário autenticado),
          decisão, motivo e timestamp. Apenas humanos autenticados podem aprovar, rejeitar ou promover.
          O Hermes não tem permissão para promover.
        </p>
      </div>

      {error && (
        <div className="rounded-xl border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-400 mb-6">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-slate-500 text-sm py-12">
          <Loader2 size={16} className="animate-spin" /> Carregando aprendizados…
        </div>
      ) : items.length === 0 ? (
        /* Empty state */
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-12 text-center">
          <BookOpen size={32} className="text-slate-600 mx-auto mb-4" />
          <p className="text-slate-400 font-medium mb-2">Nenhum aprendizado disponível ainda</p>
          <p className="text-slate-600 text-xs max-w-sm mx-auto leading-relaxed">
            Os candidatos serão criados automaticamente após uma ação executada possuir um Outcome
            válido (POSITIVE, NEGATIVE ou NEUTRAL).
          </p>
          <div className="mt-6 flex items-center justify-center gap-2 text-xs text-slate-600">
            <span>Alerta</span>
            <span className="text-slate-700">→</span>
            <span>Diagnóstico</span>
            <span className="text-slate-700">→</span>
            <span>Recomendação</span>
            <span className="text-slate-700">→</span>
            <span>Ação</span>
            <span className="text-slate-700">→</span>
            <span className="text-slate-400 font-medium">Outcome</span>
            <span className="text-slate-700">→</span>
            <span className="text-indigo-400 font-medium">Aprendizado</span>
            <span className="text-slate-700">→</span>
            <span className="text-indigo-300 font-medium">Knowledge</span>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map(c => (
            <CandidateCard
              key={c.id}
              candidate={c}
              clientId={clientId}
              activeDecision={activeDecision?.candidateId === c.id ? activeDecision : null}
              activePromotion={activePromotion?.candidateId === c.id ? activePromotion : null}
              promotionResults={promotionResults}
              onStartDecision={startDecision}
              onDecisionReasonChange={v => setActiveDecision(prev => prev ? { ...prev, reason: v } : prev)}
              onDecisionConfirm={submitDecision}
              onDecisionCancel={() => setActiveDecision(null)}
              onStartPromotion={startPromotion}
              onPromotionReasonChange={v => setActivePromotion(prev => prev ? { ...prev, reason: v } : prev)}
              onPromotionConfirm={submitPromotion}
              onPromotionCancel={() => setActivePromotion(null)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
