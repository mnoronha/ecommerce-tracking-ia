'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams }                         from 'next/navigation'
import Link                                  from 'next/link'
import {
  Lightbulb, ClipboardList, CheckCircle2, BarChart2,
  Loader2, RefreshCw, ChevronDown, ChevronUp, Clock,
  AlertCircle, CheckCircle,
} from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Recommendation {
  id: string
  title: string
  action: string
  rationale: string
  priority: string
  status: string
  requires_human_approval: boolean
  created_at: string
  created_by: string
  expected_impact?: string | null
  risk?: string | null
}

interface Task {
  id: string
  title: string
  description?: string | null
  status: string
  priority: string
  owner?: string | null
  due_date?: string | null
  source_type?: string | null
  source_id?: string | null
  sync_state?: string | null
  created_at?: string
}

interface ActionEvent {
  id: string
  recommendation_id: string
  event_type: string
  actor: string
  occurred_at: string
  note?: string | null
  change_id?: string | null
}

type Tab = 'recomendacoes' | 'tarefas' | 'executadas' | 'outcomes'
type TaskFilter = 'ALL' | 'OPEN' | 'IN_PROGRESS' | 'DONE'
type PendingDecision = {
  recId: string
  decision: 'ACCEPTED' | 'REJECTED' | 'DEFERRED'
  reason: string
  submitting: boolean
  error: string | null
}

// ── Constants ─────────────────────────────────────────────────────────────────

const PRIORITY_ORDER: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 }
const PRIORITY_LABEL: Record<string, string>  = { HIGH: 'P0', MEDIUM: 'P1', LOW: 'P2' }
const PRIORITY_BADGE: Record<string, string>  = {
  HIGH:   'bg-red-500/15 text-red-400 border border-red-500/30',
  MEDIUM: 'bg-yellow-500/15 text-yellow-400 border border-yellow-500/30',
  LOW:    'bg-slate-500/15 text-slate-400 border border-slate-500/30',
}

const STATUS_BADGE: Record<string, string> = {
  PROPOSED: 'bg-indigo-500/15 text-indigo-400',
  APPROVED: 'bg-emerald-500/15 text-emerald-400',
  REJECTED: 'bg-red-500/15 text-red-400',
  DEFERRED: 'bg-amber-500/15 text-amber-400',
  EXECUTED: 'bg-sky-500/15 text-sky-400',
}

const TASK_STATUS_BADGE: Record<string, string> = {
  OPEN:        'bg-blue-500/15 text-blue-400',
  IN_PROGRESS: 'bg-yellow-500/15 text-yellow-400',
  DONE:        'bg-emerald-500/15 text-emerald-400',
  CANCELLED:   'bg-slate-500/15 text-slate-500',
  ARCHIVED:    'bg-slate-500/15 text-slate-500',
  BLOCKED:     'bg-red-500/15 text-red-400',
}

const TASK_STATUS_LABEL: Record<string, string> = {
  OPEN: 'Aberta', IN_PROGRESS: 'Em andamento', DONE: 'Concluída',
  CANCELLED: 'Cancelada', ARCHIVED: 'Arquivada', BLOCKED: 'Bloqueada',
}

const SOURCE_LABEL: Record<string, string> = {
  recommendation: 'Recomendação', meeting: 'Reunião', manual: 'Manual', notion: 'Notion',
}

const EVENT_BADGE: Record<string, string> = {
  APPROVED: 'bg-emerald-500/15 text-emerald-400',
  REJECTED: 'bg-red-500/15 text-red-400',
  EXECUTED: 'bg-sky-500/15 text-sky-400',
  REVERTED: 'bg-yellow-500/15 text-yellow-400',
  NOTED:    'bg-slate-500/15 text-slate-400',
}

const EVENT_LABEL: Record<string, string> = {
  APPROVED: 'Aprovada', REJECTED: 'Rejeitada', EXECUTED: 'Executada',
  REVERTED: 'Revertida', NOTED: 'Nota',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso?: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
    timeZone: 'America/Sao_Paulo',
  })
}

function sortedRecs(recs: Recommendation[]): Recommendation[] {
  return [...recs].sort((a, b) => {
    const pa = PRIORITY_ORDER[a.priority] ?? 9
    const pb = PRIORITY_ORDER[b.priority] ?? 9
    if (pa !== pb) return pa - pb
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  })
}

// ── Sub-components ────────────────────────────────────────────────────────────

function EmptyState({ icon, message }: { icon: React.ReactNode; message: string }) {
  return (
    <div className="py-12 flex flex-col items-center gap-3 text-center">
      <div className="text-slate-600">{icon}</div>
      <p className="text-xs text-slate-500">{message}</p>
    </div>
  )
}

// ── Recomendações Tab ─────────────────────────────────────────────────────────

function RecomendacoesTab({ recs, onUpdated }: { recs: Recommendation[]; onUpdated: (r: Recommendation) => void }) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const [pending,  setPending]  = useState<PendingDecision | null>(null)

  const sorted = sortedRecs(recs)

  function toggle(id: string) {
    setExpanded(prev => prev === id ? null : id)
    setPending(prev => prev?.recId === id ? null : prev)
  }

  function startDecision(recId: string, decision: PendingDecision['decision']) {
    setPending({ recId, decision, reason: '', submitting: false, error: null })
  }

  async function submitDecision() {
    if (!pending) return
    setPending(p => p ? { ...p, submitting: true, error: null } : p)
    try {
      const res = await fetch(`/api/v1/recommendations/${pending.recId}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          decision:        pending.decision,
          actor:           'agency_admin',
          reason:          pending.reason.trim() || undefined,
          idempotency_key: crypto.randomUUID(),
        }),
      })
      const data = await res.json().catch(() => null)
      if (!res.ok) {
        const msg = (data as { detail?: string })?.detail ?? `Erro ${res.status}`
        setPending(p => p ? { ...p, submitting: false, error: msg } : p)
        return
      }
      if (data && typeof data === 'object' && 'id' in data) {
        onUpdated(data as Recommendation)
      }
      setPending(null)
      setExpanded(null)
    } catch {
      setPending(p => p ? { ...p, submitting: false, error: 'Falha de comunicação.' } : p)
    }
  }

  if (sorted.length === 0) return (
    <EmptyState icon={<Lightbulb size={28} />} message="Nenhuma recomendação encontrada para este cliente." />
  )

  return (
    <div className="space-y-2">
      {sorted.map(rec => {
        const isOpen    = expanded === rec.id
        const isDecForm = pending?.recId === rec.id
        const canDecide = rec.status === 'PROPOSED' || rec.status === 'DEFERRED'
        const accent =
          pending?.decision === 'ACCEPTED' ? 'emerald' :
          pending?.decision === 'REJECTED' ? 'red' : 'amber'

        return (
          <div key={rec.id} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
            <button
              onClick={() => toggle(rec.id)}
              className="w-full text-left px-4 py-3 flex items-start gap-3 hover:bg-[#252b3b] transition-colors"
            >
              <Lightbulb size={13} className="shrink-0 mt-0.5 text-yellow-400" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 flex-wrap mb-1">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${PRIORITY_BADGE[rec.priority] ?? PRIORITY_BADGE.LOW}`}>
                    {PRIORITY_LABEL[rec.priority] ?? rec.priority}
                  </span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${STATUS_BADGE[rec.status] ?? 'bg-slate-500/15 text-slate-400'}`}>
                    {rec.status}
                  </span>
                </div>
                <p className="text-sm font-medium text-white leading-snug">{rec.title}</p>
                <p className="text-[10px] text-slate-500 mt-0.5">{fmtDate(rec.created_at)} · {rec.created_by}</p>
              </div>
              {isOpen
                ? <ChevronUp size={13} className="text-slate-500 shrink-0 mt-0.5" />
                : <ChevronDown size={13} className="text-slate-500 shrink-0 mt-0.5" />}
            </button>

            {isOpen && (
              <div className="px-4 pb-4 border-t border-[#2a2f3e] space-y-3 pt-3">
                <div className="space-y-2">
                  <div>
                    <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">Ação sugerida</p>
                    <p className="text-xs text-slate-200 leading-relaxed">{rec.action}</p>
                  </div>
                  <div>
                    <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">Evidência / Racional</p>
                    <p className="text-xs text-slate-400 leading-relaxed">{rec.rationale}</p>
                  </div>
                  {rec.expected_impact && (
                    <div>
                      <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">Impacto esperado</p>
                      <p className="text-xs text-slate-400 leading-relaxed">{rec.expected_impact}</p>
                    </div>
                  )}
                  {rec.risk && (
                    <p className="text-xs text-yellow-300 italic border-l-2 border-yellow-500/40 pl-2">{rec.risk}</p>
                  )}
                </div>

                {canDecide && !isDecForm && (
                  <div className="flex items-center gap-2 pt-2 border-t border-[#2a2f3e]">
                    <p className="text-[10px] text-slate-500 mr-1">Decisão:</p>
                    <button
                      onClick={() => startDecision(rec.id, 'ACCEPTED')}
                      className="text-xs px-2.5 py-1 rounded-md bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-600/40 transition-colors"
                    >Aprovar</button>
                    <button
                      onClick={() => startDecision(rec.id, 'DEFERRED')}
                      className="text-xs px-2.5 py-1 rounded-md bg-amber-600/20 hover:bg-amber-600/30 text-amber-300 border border-amber-600/40 transition-colors"
                    >Adiar</button>
                    <button
                      onClick={() => startDecision(rec.id, 'REJECTED')}
                      className="text-xs px-2.5 py-1 rounded-md bg-red-600/20 hover:bg-red-600/30 text-red-300 border border-red-600/40 transition-colors"
                    >Rejeitar</button>
                  </div>
                )}

                {isDecForm && pending && (
                  <div className={`rounded-lg border p-3 space-y-2 ${
                    accent === 'emerald' ? 'border-emerald-500/30 bg-emerald-500/5' :
                    accent === 'red'     ? 'border-red-500/30 bg-red-500/5'         :
                                          'border-amber-500/30 bg-amber-500/5'
                  }`}>
                    <p className={`text-xs font-semibold ${
                      accent === 'emerald' ? 'text-emerald-400' :
                      accent === 'red'     ? 'text-red-400'     : 'text-amber-400'
                    }`}>
                      {pending.decision === 'ACCEPTED' ? 'Confirmar aprovação?' : pending.decision === 'REJECTED' ? 'Confirmar rejeição?' : 'Confirmar adiamento?'}
                    </p>
                    <textarea
                      value={pending.reason}
                      onChange={e => setPending(p => p ? { ...p, reason: e.target.value } : p)}
                      disabled={pending.submitting}
                      placeholder="Motivo (opcional)"
                      rows={2}
                      className="w-full rounded-md bg-[#252b3b] border border-[#3a3f4e] text-xs text-slate-300 placeholder-slate-600 px-3 py-1.5 resize-none focus:outline-none disabled:opacity-50"
                    />
                    {pending.error && <p className="text-xs text-red-400">{pending.error}</p>}
                    <div className="flex gap-2 items-center">
                      <button
                        onClick={submitDecision}
                        disabled={pending.submitting}
                        className={`text-xs px-3 py-1.5 rounded-md border transition-colors disabled:opacity-50 ${
                          accent === 'emerald' ? 'bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border-emerald-600/40' :
                          accent === 'red'     ? 'bg-red-600/20 hover:bg-red-600/30 text-red-300 border-red-600/40'                 :
                                                'bg-amber-600/20 hover:bg-amber-600/30 text-amber-300 border-amber-600/40'
                        }`}
                      >
                        {pending.submitting ? <Loader2 size={11} className="animate-spin inline" /> : 'Confirmar'}
                      </button>
                      <button onClick={() => setPending(null)} disabled={pending.submitting} className="text-xs text-slate-500 hover:text-slate-300 transition-colors">Cancelar</button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Tarefas Tab ───────────────────────────────────────────────────────────────

function TarefasTab({ tasks }: { tasks: Task[] }) {
  const [filter, setFilter] = useState<TaskFilter>('ALL')

  const displayed = filter === 'ALL'
    ? tasks
    : tasks.filter(t => t.status === filter)

  const counts: Record<TaskFilter, number> = {
    ALL:         tasks.length,
    OPEN:        tasks.filter(t => t.status === 'OPEN').length,
    IN_PROGRESS: tasks.filter(t => t.status === 'IN_PROGRESS').length,
    DONE:        tasks.filter(t => t.status === 'DONE').length,
  }

  if (tasks.length === 0) return (
    <EmptyState icon={<ClipboardList size={28} />} message="Nenhuma tarefa encontrada para este cliente." />
  )

  return (
    <div className="space-y-4">
      {/* Status filter */}
      <div className="flex gap-1 bg-[#151a27] rounded-lg p-1 border border-[#2a2f3e] w-fit">
        {(['ALL', 'OPEN', 'IN_PROGRESS', 'DONE'] as TaskFilter[]).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
              filter === f ? 'bg-[#2a3050] text-white' : 'text-slate-400 hover:text-white'
            }`}
          >
            {f === 'ALL' ? 'Todas' : f === 'IN_PROGRESS' ? 'Em andamento' : f === 'OPEN' ? 'Abertas' : 'Concluídas'}
            <span className="ml-1 text-[10px] text-slate-500">({counts[f]})</span>
          </button>
        ))}
      </div>

      {displayed.length === 0 ? (
        <p className="text-xs text-slate-600 pl-2">Nenhuma tarefa com este status.</p>
      ) : (
        <div className="space-y-1.5">
          {displayed.map(task => (
            <div key={task.id} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-4 py-3">
              <div className="flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap mb-1">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${TASK_STATUS_BADGE[task.status] ?? 'bg-slate-500/15 text-slate-400'}`}>
                      {TASK_STATUS_LABEL[task.status] ?? task.status}
                    </span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${PRIORITY_BADGE[task.priority] ?? PRIORITY_BADGE.LOW}`}>
                      {PRIORITY_LABEL[task.priority] ?? task.priority}
                    </span>
                    {task.source_type && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-slate-700/40 text-slate-400">
                        {SOURCE_LABEL[task.source_type.toLowerCase()] ?? task.source_type}
                      </span>
                    )}
                    {task.sync_state === 'synced' && (
                      <span className="text-[10px] text-emerald-500 flex items-center gap-0.5">
                        <CheckCircle size={9} />Notion
                      </span>
                    )}
                  </div>
                  <p className="text-xs font-medium text-white">{task.title}</p>
                  {task.description && (
                    <p className="text-[10px] text-slate-500 mt-0.5 line-clamp-2">{task.description}</p>
                  )}
                </div>
                <div className="shrink-0 text-right space-y-1">
                  {task.due_date && (
                    <div className="flex items-center gap-1 text-[10px] text-slate-500 justify-end">
                      <Clock size={9} />
                      {new Date(task.due_date + 'T00:00:00').toLocaleDateString('pt-BR')}
                    </div>
                  )}
                  {task.owner && (
                    <p className="text-[10px] text-slate-600">{task.owner}</p>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Executadas Tab ────────────────────────────────────────────────────────────

function ExecutadasTab({ events, recs }: { events: ActionEvent[]; recs: Recommendation[] }) {
  const recMap = Object.fromEntries(recs.map(r => [r.id, r]))

  if (events.length === 0) return (
    <EmptyState icon={<CheckCircle2 size={28} />} message="Nenhuma ação registrada ainda." />
  )

  return (
    <div className="space-y-1.5">
      {events.map(ev => {
        const rec = recMap[ev.recommendation_id]
        return (
          <div key={ev.id} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-4 py-3">
            <div className="flex items-start gap-3">
              <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0 mt-0.5 ${EVENT_BADGE[ev.event_type] ?? 'bg-slate-500/15 text-slate-400'}`}>
                {EVENT_LABEL[ev.event_type] ?? ev.event_type}
              </span>
              <div className="flex-1 min-w-0">
                {rec ? (
                  <p className="text-xs text-slate-200 font-medium truncate">{rec.title}</p>
                ) : (
                  <p className="text-xs text-slate-500 font-mono truncate">{ev.recommendation_id}</p>
                )}
                {ev.note && (
                  <p className="text-[10px] text-slate-500 mt-0.5 truncate">{ev.note}</p>
                )}
                {ev.change_id && (
                  <p className="text-[10px] text-slate-600 mt-0.5">
                    Relacionado à mudança <span className="font-mono">{ev.change_id.slice(0, 8)}</span>
                  </p>
                )}
              </div>
              <div className="shrink-0 text-right">
                <p className="text-[10px] text-slate-500">{fmtDate(ev.occurred_at)}</p>
                <p className="text-[10px] text-slate-600 mt-0.5">{ev.actor}</p>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Outcomes Tab ──────────────────────────────────────────────────────────────

function OutcomesTab({ events, recs }: { events: ActionEvent[]; recs: Recommendation[] }) {
  const executed = events.filter(e => e.event_type === 'EXECUTED')
  const recMap   = Object.fromEntries(recs.map(r => [r.id, r]))

  if (executed.length === 0) return (
    <EmptyState
      icon={<BarChart2 size={28} />}
      message="Nenhum outcome registrado. Outcomes são gerados automaticamente após execução e janela de avaliação."
    />
  )

  return (
    <div className="space-y-3">
      <p className="text-[10px] text-slate-500">
        {executed.length} ação(ões) executada(s) · outcomes pendentes de avaliação automática
      </p>
      <div className="space-y-1.5">
        {executed.map(ev => {
          const rec = recMap[ev.recommendation_id]
          return (
            <div key={ev.id} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-4 py-3">
              <div className="flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700/40 text-slate-400 font-medium">
                      PENDING
                    </span>
                  </div>
                  {rec ? (
                    <p className="text-xs text-slate-200 font-medium">{rec.title}</p>
                  ) : (
                    <p className="text-xs text-slate-500 font-mono">{ev.recommendation_id}</p>
                  )}
                  <p className="text-[10px] text-slate-600 mt-1">
                    Executada em {fmtDate(ev.occurred_at)} · {ev.actor}
                  </p>
                  <p className="text-[10px] text-slate-600 mt-0.5 italic">
                    Aguardando coleta de métricas antes/depois para avaliação.
                  </p>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
  { id: 'recomendacoes', label: 'Recomendações', icon: <Lightbulb size={12} /> },
  { id: 'tarefas',       label: 'Tarefas',        icon: <ClipboardList size={12} /> },
  { id: 'executadas',    label: 'Executadas',      icon: <CheckCircle2 size={12} /> },
  { id: 'outcomes',      label: 'Outcomes',        icon: <BarChart2 size={12} /> },
]

export default function ActionsPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [tab,     setTab]     = useState<Tab>('recomendacoes')
  const [recs,    setRecs]    = useState<Recommendation[]>([])
  const [tasks,   setTasks]   = useState<Task[]>([])
  const [events,  setEvents]  = useState<ActionEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [rRes, tRes, eRes] = await Promise.all([
        fetch(`/api/v1/clients/${clientId}/recommendations?limit=50`),
        fetch(`/api/v1/clients/${clientId}/tasks?limit=50`),
        fetch(`/api/v1/clients/${clientId}/action-events?limit=50`),
      ])
      if (rRes.ok) setRecs(await rRes.json())
      if (tRes.ok) setTasks(await tRes.json())
      if (eRes.ok) setEvents(await eRes.json())
      if (!rRes.ok && !tRes.ok && !eRes.ok) setError('Não foi possível carregar os dados.')
    } catch {
      setError('Falha de comunicação com o servidor.')
    } finally {
      setLoading(false)
    }
  }, [clientId])

  useEffect(() => { load() }, [load])

  function handleRecUpdated(updated: Recommendation) {
    setRecs(prev => prev.map(r => r.id === updated.id ? updated : r))
  }

  // Tab badge counts
  const pendingCount  = recs.filter(r => r.status === 'PROPOSED').length
  const openTaskCount = tasks.filter(t => t.status === 'OPEN' || t.status === 'IN_PROGRESS').length

  return (
    <div className="p-6 max-w-4xl space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-white">Ações</h2>
          <p className="text-[10px] text-slate-500 mt-0.5">decisões · tarefas · execuções · resultados</p>
        </div>
        <button onClick={load} disabled={loading} className="text-slate-500 hover:text-white transition-colors disabled:opacity-50">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl border border-red-500/20 bg-red-500/5 p-3">
          <AlertCircle size={13} className="text-red-400 shrink-0" />
          <p className="text-xs text-red-400">{error}</p>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 bg-[#151a27] rounded-lg p-1 border border-[#2a2f3e] w-fit">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors ${
              tab === t.id ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
            }`}
          >
            {t.icon}
            {t.label}
            {t.id === 'recomendacoes' && pendingCount > 0 && (
              <span className={`text-[10px] px-1 rounded-full font-bold ${tab === t.id ? 'bg-white/20 text-white' : 'bg-yellow-500/20 text-yellow-400'}`}>
                {pendingCount}
              </span>
            )}
            {t.id === 'tarefas' && openTaskCount > 0 && (
              <span className={`text-[10px] px-1 rounded-full font-bold ${tab === t.id ? 'bg-white/20 text-white' : 'bg-sky-500/20 text-sky-400'}`}>
                {openTaskCount}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex items-center gap-2 text-slate-500 text-xs py-8">
          <Loader2 size={13} className="animate-spin" /> Carregando…
        </div>
      ) : (
        <>
          {tab === 'recomendacoes' && (
            <RecomendacoesTab recs={recs} onUpdated={handleRecUpdated} />
          )}
          {tab === 'tarefas' && (
            <TarefasTab tasks={tasks} />
          )}
          {tab === 'executadas' && (
            <ExecutadasTab events={events} recs={recs} />
          )}
          {tab === 'outcomes' && (
            <OutcomesTab events={events} recs={recs} />
          )}
        </>
      )}

      {/* Cross-link */}
      {!loading && (
        <div className="pt-2 border-t border-[#2a2f3e] flex items-center gap-4">
          <Link href={`/clients/${clientId}/changes`} className="text-[10px] text-slate-600 hover:text-indigo-400 transition-colors">
            Ver mudanças →
          </Link>
        </div>
      )}
    </div>
  )
}
