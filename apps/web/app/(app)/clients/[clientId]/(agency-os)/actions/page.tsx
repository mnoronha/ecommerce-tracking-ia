'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams }                         from 'next/navigation'
import Link                                  from 'next/link'
import {
  Zap, ClipboardList, Lightbulb, CheckCircle2,
  XCircle, Clock, Loader2, RefreshCw, ChevronDown, ChevronUp,
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
}

interface ActionEvent {
  id: string
  recommendation_id: string
  event_type: string
  actor: string
  occurred_at: string
  note?: string | null
}

type DecisionType = 'ACCEPTED' | 'REJECTED' | 'DEFERRED'

interface PendingDecision {
  recId: string
  decision: DecisionType
  reason: string
  submitting: boolean
  error: string | null
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const PRIORITY_BADGE: Record<string, string> = {
  HIGH:   'bg-red-500/15 text-red-400 border-red-500/30',
  MEDIUM: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30',
  LOW:    'bg-slate-500/15 text-slate-400 border-slate-500/30',
}

const REC_STATUS_BADGE: Record<string, string> = {
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
}

const EVENT_BADGE: Record<string, string> = {
  APPROVED: 'bg-emerald-500/15 text-emerald-400',
  REJECTED: 'bg-red-500/15 text-red-400',
  EXECUTED: 'bg-sky-500/15 text-sky-400',
  REVERTED: 'bg-yellow-500/15 text-yellow-400',
  NOTED:    'bg-slate-500/15 text-slate-400',
}

function fmtDate(iso?: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    timeZone: 'America/Sao_Paulo',
  })
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ActionsPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [recs,    setRecs]    = useState<Recommendation[]>([])
  const [tasks,   setTasks]   = useState<Task[]>([])
  const [events,  setEvents]  = useState<ActionEvent[]>([])
  const [loading, setLoading] = useState(true)

  const [expanded, setExpanded]   = useState<string | null>(null)
  const [pending,  setPending]    = useState<PendingDecision | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [rRes, tRes, eRes] = await Promise.all([
        fetch(`/api/v1/clients/${clientId}/recommendations`),
        fetch(`/api/v1/clients/${clientId}/tasks?status=OPEN`),
        fetch(`/api/v1/clients/${clientId}/action-events`),
      ])
      if (rRes.ok) setRecs(await rRes.json())
      if (tRes.ok) {
        const d = await tRes.json()
        setTasks(Array.isArray(d) ? d : (d.tasks ?? []))
      }
      if (eRes.ok) setEvents(await eRes.json())
    } finally {
      setLoading(false)
    }
  }, [clientId])

  useEffect(() => { load() }, [load])

  function toggle(id: string) {
    setExpanded(prev => prev === id ? null : id)
    setPending(prev => prev?.recId === id ? null : prev)
  }

  function startDecision(recId: string, decision: DecisionType) {
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
        setRecs(prev => prev.map(r => r.id === pending.recId ? { ...r, ...(data as Partial<Recommendation>) } : r))
      }
      setPending(null)
    } catch {
      setPending(p => p ? { ...p, submitting: false, error: 'Falha de comunicação.' } : p)
    }
  }

  const actionable = recs.filter(r => r.status === 'PROPOSED' && r.requires_human_approval)
  const otherRecs  = recs.filter(r => !(r.status === 'PROPOSED' && r.requires_human_approval))
  const activeTasks = tasks.filter(t => t.status === 'OPEN' || t.status === 'IN_PROGRESS')
  const recentEvents = events.slice(0, 8)

  return (
    <div className="p-6 space-y-8 max-w-4xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Zap size={15} className="text-indigo-400" />
          <h2 className="text-sm font-semibold text-white">Ações</h2>
          <span className="text-xs text-slate-500">· decisões pendentes · tarefas · execuções</span>
        </div>
        <button onClick={load} disabled={loading} className="text-slate-500 hover:text-white transition-colors">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-slate-500 text-xs py-8">
          <Loader2 size={13} className="animate-spin" /> Carregando…
        </div>
      ) : (
        <>
          {/* ── Decisões pendentes ──────────────────────────────────────── */}
          <section className="space-y-3">
            <div className="flex items-center gap-2">
              <Lightbulb size={12} className="text-yellow-400" />
              <h3 className="text-xs font-semibold text-slate-300">
                Decisões pendentes
                {actionable.length > 0 && (
                  <span className="ml-1.5 px-1.5 py-0.5 rounded-full bg-yellow-500/20 text-yellow-400 text-[10px]">
                    {actionable.length}
                  </span>
                )}
              </h3>
            </div>

            {actionable.length === 0 ? (
              <p className="text-xs text-slate-600 pl-4">Nenhuma recomendação aguardando decisão.</p>
            ) : (
              <div className="space-y-2">
                {actionable.map(rec => {
                  const isOpen    = expanded === rec.id
                  const isDecForm = pending?.recId === rec.id
                  const accent    =
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
                          <div className="flex items-center gap-2 flex-wrap mb-0.5">
                            <span className={`text-xs px-1.5 py-0.5 rounded border font-medium ${PRIORITY_BADGE[rec.priority] ?? PRIORITY_BADGE.LOW}`}>
                              {rec.priority}
                            </span>
                            <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${REC_STATUS_BADGE[rec.status] ?? ''}`}>
                              {rec.status}
                            </span>
                          </div>
                          <p className="text-sm font-medium text-white leading-snug">{rec.title}</p>
                          <p className="text-xs text-slate-500">{fmtDate(rec.created_at)} · {rec.created_by}</p>
                        </div>
                        {isOpen
                          ? <ChevronUp size={13} className="text-slate-500 shrink-0" />
                          : <ChevronDown size={13} className="text-slate-500 shrink-0" />}
                      </button>

                      {isOpen && (
                        <div className="px-4 pb-4 border-t border-[#2a2f3e] space-y-3 mt-0">
                          <div className="pt-3 space-y-2">
                            <div>
                              <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">Ação</p>
                              <p className="text-xs text-slate-300 leading-relaxed">{rec.action}</p>
                            </div>
                            <div>
                              <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">Racional</p>
                              <p className="text-xs text-slate-400 leading-relaxed">{rec.rationale}</p>
                            </div>
                            {rec.expected_impact && (
                              <div>
                                <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">Impacto esperado</p>
                                <p className="text-xs text-slate-400 leading-relaxed">{rec.expected_impact}</p>
                              </div>
                            )}
                            {rec.risk && (
                              <p className="text-xs text-yellow-300 italic">{rec.risk}</p>
                            )}
                          </div>

                          {/* Decision buttons */}
                          {!isDecForm && (
                            <div className="flex items-center gap-2 pt-2 border-t border-[#2a2f3e]">
                              <p className="text-xs text-slate-500 mr-1">Decisão:</p>
                              <button
                                onClick={() => startDecision(rec.id, 'ACCEPTED')}
                                className="text-xs px-2.5 py-1 rounded-md bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-600/40 transition-colors"
                              >
                                Aprovar
                              </button>
                              <button
                                onClick={() => startDecision(rec.id, 'DEFERRED')}
                                className="text-xs px-2.5 py-1 rounded-md bg-amber-600/20 hover:bg-amber-600/30 text-amber-300 border border-amber-600/40 transition-colors"
                              >
                                Adiar
                              </button>
                              <button
                                onClick={() => startDecision(rec.id, 'REJECTED')}
                                className="text-xs px-2.5 py-1 rounded-md bg-red-600/20 hover:bg-red-600/30 text-red-300 border border-red-600/40 transition-colors"
                              >
                                Rejeitar
                              </button>
                            </div>
                          )}

                          {/* Decision confirmation */}
                          {isDecForm && pending && (
                            <div className={`rounded-lg border p-3 space-y-2 ${
                              accent === 'emerald' ? 'border-emerald-500/30 bg-emerald-500/5' :
                              accent === 'red'     ? 'border-red-500/30 bg-red-500/5' :
                                                    'border-amber-500/30 bg-amber-500/5'
                            }`}>
                              <p className={`text-xs font-semibold ${
                                accent === 'emerald' ? 'text-emerald-400' :
                                accent === 'red'     ? 'text-red-400'     : 'text-amber-400'
                              }`}>
                                Confirmar: {pending.decision === 'ACCEPTED' ? 'Aprovar' : pending.decision === 'REJECTED' ? 'Rejeitar' : 'Adiar'}?
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
                                    accent === 'red'     ? 'bg-red-600/20 hover:bg-red-600/30 text-red-300 border-red-600/40' :
                                                          'bg-amber-600/20 hover:bg-amber-600/30 text-amber-300 border-amber-600/40'
                                  }`}
                                >
                                  {pending.submitting ? <Loader2 size={11} className="animate-spin inline" /> : 'Confirmar'}
                                </button>
                                <button
                                  onClick={() => setPending(null)}
                                  disabled={pending.submitting}
                                  className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
                                >
                                  Cancelar
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}

            {otherRecs.length > 0 && (
              <Link
                href={`/clients/${clientId}/recommendations`}
                className="text-xs text-slate-500 hover:text-indigo-400 transition-colors pl-4"
              >
                Ver todas as recomendações ({recs.length}) →
              </Link>
            )}
          </section>

          {/* ── Tarefas abertas ─────────────────────────────────────────── */}
          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <ClipboardList size={12} className="text-sky-400" />
                <h3 className="text-xs font-semibold text-slate-300">
                  Tarefas abertas
                  {activeTasks.length > 0 && (
                    <span className="ml-1.5 px-1.5 py-0.5 rounded-full bg-sky-500/20 text-sky-400 text-[10px]">
                      {activeTasks.length}
                    </span>
                  )}
                </h3>
              </div>
              <Link
                href={`/clients/${clientId}/tasks`}
                className="text-xs text-slate-600 hover:text-indigo-400 transition-colors"
              >
                Ver todas →
              </Link>
            </div>

            {activeTasks.length === 0 ? (
              <p className="text-xs text-slate-600 pl-4">Nenhuma tarefa aberta.</p>
            ) : (
              <div className="space-y-1.5">
                {activeTasks.slice(0, 10).map(task => (
                  <div
                    key={task.id}
                    className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-4 py-2.5 flex items-center gap-3"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 flex-wrap mb-0.5">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${TASK_STATUS_BADGE[task.status] ?? ''}`}>
                          {task.status === 'IN_PROGRESS' ? 'Em andamento' : task.status === 'OPEN' ? 'Aberta' : task.status}
                        </span>
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${PRIORITY_BADGE[task.priority]?.split(' ')[0] ?? ''} ${PRIORITY_BADGE[task.priority]?.split(' ')[1] ?? ''}`}>
                          {task.priority === 'HIGH' ? 'Alta' : task.priority === 'MEDIUM' ? 'Média' : 'Baixa'}
                        </span>
                      </div>
                      <p className="text-xs text-white font-medium truncate">{task.title}</p>
                    </div>
                    {task.due_date && (
                      <div className="flex items-center gap-1 text-[10px] text-slate-500 shrink-0">
                        <Clock size={9} />
                        {new Date(task.due_date).toLocaleDateString('pt-BR')}
                      </div>
                    )}
                    {task.owner && (
                      <span className="text-[10px] text-slate-600 shrink-0">{task.owner}</span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* ── Execuções recentes ──────────────────────────────────────── */}
          <section className="space-y-3">
            <div className="flex items-center gap-2">
              <CheckCircle2 size={12} className="text-emerald-400" />
              <h3 className="text-xs font-semibold text-slate-300">Execuções recentes</h3>
            </div>

            {recentEvents.length === 0 ? (
              <p className="text-xs text-slate-600 pl-4">Nenhuma execução registrada.</p>
            ) : (
              <div className="space-y-1.5">
                {recentEvents.map(ev => (
                  <div
                    key={ev.id}
                    className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-4 py-2.5 flex items-center gap-3"
                  >
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0 ${EVENT_BADGE[ev.event_type] ?? 'bg-slate-500/15 text-slate-400'}`}>
                      {ev.event_type}
                    </span>
                    <span className="text-xs text-slate-400 flex-1 truncate">{ev.note || ev.recommendation_id}</span>
                    <span className="text-[10px] text-slate-600 shrink-0">{ev.actor} · {fmtDate(ev.occurred_at)}</span>
                  </div>
                ))}
              </div>
            )}

            <Link
              href={`/clients/${clientId}/changes`}
              className="text-xs text-slate-600 hover:text-indigo-400 transition-colors pl-4"
            >
              Ver change log completo →
            </Link>
          </section>
        </>
      )}
    </div>
  )
}
