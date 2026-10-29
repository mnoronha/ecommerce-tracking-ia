'use client'

import { useState, useEffect } from 'react'
import { useParams } from 'next/navigation'
import {
  Calendar, Loader2, AlertCircle, ChevronDown, ChevronUp,
  Users, RefreshCw, CheckSquare, Link2,
} from 'lucide-react'

interface Participant {
  name?: string | null
  email?: string | null
  role?: string | null
}

interface Decision {
  decision: string
  owner?: string | null
}

interface FollowUp {
  action: string
  owner?: string | null
  due_date?: string | null
}

interface Commitment {
  who?: string | null
  what: string
  due_date?: string | null
}

interface TaskEntry {
  what?: string | null
  title?: string | null
  status?: 'created' | 'failed' | string
  task_id?: string | null
  reason?: string | null
}

interface MeetingSummary {
  id: string
  summary?: string | null
  decisions: Decision[]
  follow_ups: FollowUp[]
  commitments: Commitment[]
  open_questions: string[]
  tasks_created: TaskEntry[]
  processor: string
  processor_manual_status?: string
  processor_claude_status?: string
  replayed?: boolean
  created_at: string
}

interface Meeting {
  id: string
  title: string
  provider: string
  started_at?: string | null
  ended_at?: string | null
  participants: Participant[]
  transcript_status: string
  processing_status: string
  summary?: MeetingSummary | null
  created_at: string
}

function fmtDate(s?: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' })
}

function fmtTime(s?: string | null) {
  if (!s) return ''
  return new Date(s).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
}

function fmtShortDate(s?: string | null) {
  if (!s) return null
  return new Date(s).toLocaleDateString('pt-BR')
}

export default function MeetingsPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    fetch(`/api/v1/clients/${clientId}/meetings?limit=50`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then(data => setMeetings(Array.isArray(data) ? data : (data.meetings ?? [])))
      .catch(e => setError(e instanceof Error ? e.message : 'Erro ao carregar reuniões'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [clientId]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Calendar size={20} className="text-indigo-400" />
          <h1 className="text-xl font-bold text-white">Reuniões</h1>
        </div>
        <button
          onClick={load}
          className="p-1.5 rounded-lg bg-[#1a1f2e] text-slate-500 hover:text-white transition-colors"
        >
          <RefreshCw size={13} />
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-2 py-12 justify-center text-slate-600">
          <Loader2 size={16} className="animate-spin" />
          <span className="text-sm">Carregando...</span>
        </div>
      )}

      {!loading && error && (
        <div className="flex items-center gap-2 p-4 rounded-xl border border-red-500/20 bg-red-500/5 text-red-400 text-sm">
          <AlertCircle size={16} className="shrink-0" />
          {error}
        </div>
      )}

      {!loading && !error && meetings.length === 0 && (
        <div className="py-16 text-center text-slate-600">
          <Calendar size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">Nenhuma reunião registrada</p>
        </div>
      )}

      {!loading && !error && meetings.length > 0 && (
        <div className="space-y-3">
          {meetings.map(meeting => {
            const open = expanded === meeting.id
            const hasSummary = !!meeting.summary?.summary
            const tasks = meeting.summary?.tasks_created ?? []
            const createdTasks = tasks.filter(t => t.status === 'created')
            return (
              <div key={meeting.id} className="rounded-xl border border-[#2a2f3e] bg-[#141920] overflow-hidden">
                <button
                  className="w-full p-4 text-left hover:bg-[#1a1f2e] transition-colors"
                  onClick={() => setExpanded(open ? null : meeting.id)}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-1.5 mb-1.5">
                        <span className="text-sm font-semibold text-white leading-snug">{meeting.title}</span>
                        {hasSummary && (
                          <span className="px-2 py-0.5 rounded-full text-[10px] bg-green-500/20 text-green-400">resumo</span>
                        )}
                        {createdTasks.length > 0 && (
                          <span className="px-2 py-0.5 rounded-full text-[10px] bg-indigo-500/20 text-indigo-400 flex items-center gap-1">
                            <CheckSquare size={9} />
                            {createdTasks.length} task{createdTasks.length !== 1 ? 's' : ''}
                          </span>
                        )}
                        <span className="px-2 py-0.5 rounded-full text-[10px] bg-slate-500/10 text-slate-500 capitalize">
                          {meeting.provider.toLowerCase()}
                        </span>
                      </div>
                      <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
                        <span>{fmtDate(meeting.started_at)}</span>
                        {meeting.started_at && <span>{fmtTime(meeting.started_at)}</span>}
                        {meeting.participants.length > 0 && (
                          <span className="flex items-center gap-1">
                            <Users size={10} />
                            {meeting.participants.length} participante{meeting.participants.length !== 1 ? 's' : ''}
                          </span>
                        )}
                      </div>
                    </div>
                    {open
                      ? <ChevronUp   size={15} className="text-slate-500 shrink-0 mt-1" />
                      : <ChevronDown size={15} className="text-slate-500 shrink-0 mt-1" />
                    }
                  </div>
                </button>

                {open && (
                  <div className="border-t border-[#2a2f3e] p-4 space-y-4">
                    {meeting.participants.length > 0 && (
                      <div>
                        <p className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider mb-2">Participantes</p>
                        <div className="flex flex-wrap gap-2">
                          {meeting.participants.map((p, i) => (
                            <span key={i} className="px-2.5 py-1 rounded-lg bg-[#1a1f2e] text-xs text-slate-300">
                              {p.name || p.email || 'Desconhecido'}
                              {p.role && <span className="text-slate-600 ml-1.5">· {p.role}</span>}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {meeting.summary?.summary && (
                      <div>
                        <p className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider mb-2">Resumo</p>
                        <p className="text-sm text-slate-300 leading-relaxed">{meeting.summary.summary}</p>
                      </div>
                    )}

                    {(meeting.summary?.decisions?.length ?? 0) > 0 && (
                      <div>
                        <p className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider mb-2">Decisões</p>
                        <ul className="space-y-1.5">
                          {meeting.summary!.decisions.map((d, i) => (
                            <li key={i} className="flex items-start gap-2 text-sm text-slate-300">
                              <span className="text-indigo-400 shrink-0">·</span>
                              <span>
                                {d.decision}
                                {d.owner && <span className="text-slate-500 ml-1.5">({d.owner})</span>}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {(meeting.summary?.follow_ups?.length ?? 0) > 0 && (
                      <div>
                        <p className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider mb-2">Próximos passos</p>
                        <ul className="space-y-1.5">
                          {meeting.summary!.follow_ups.map((f, i) => (
                            <li key={i} className="flex items-start gap-2 text-sm text-slate-300">
                              <span className="text-green-400 shrink-0">→</span>
                              <span>
                                {f.action}
                                {f.owner && <span className="text-slate-500 ml-1.5">({f.owner})</span>}
                                {f.due_date && (
                                  <span className="text-slate-600 ml-1.5">
                                    · {fmtShortDate(f.due_date)}
                                  </span>
                                )}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {(meeting.summary?.commitments?.length ?? 0) > 0 && (
                      <div>
                        <p className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider mb-2">Compromissos</p>
                        <ul className="space-y-1.5">
                          {meeting.summary!.commitments.map((c, i) => (
                            <li key={i} className="flex items-start gap-2 text-sm text-slate-300">
                              <span className="text-amber-400 shrink-0">◆</span>
                              <span>
                                {c.what}
                                {c.who && <span className="text-slate-500 ml-1.5">({c.who})</span>}
                                {c.due_date && (
                                  <span className="text-slate-600 ml-1.5">· até {fmtShortDate(c.due_date)}</span>
                                )}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {(meeting.summary?.open_questions?.length ?? 0) > 0 && (
                      <div>
                        <p className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider mb-2">Questões abertas</p>
                        <ul className="space-y-1">
                          {meeting.summary!.open_questions.map((q, i) => (
                            <li key={i} className="flex items-start gap-2 text-sm text-slate-400">
                              <span className="text-slate-600 shrink-0">?</span>
                              {q}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {tasks.length > 0 && (
                      <div>
                        <p className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider mb-2">Tasks geradas</p>
                        <ul className="space-y-1.5">
                          {tasks.map((t, i) => (
                            <li key={i} className="flex items-start gap-2 text-sm">
                              <Link2 size={12} className={`shrink-0 mt-0.5 ${t.status === 'created' ? 'text-indigo-400' : 'text-red-400'}`} />
                              <span className={t.status === 'created' ? 'text-slate-300' : 'text-slate-500 line-through'}>
                                {t.title || t.what || '—'}
                              </span>
                              {t.status === 'failed' && t.reason && (
                                <span className="text-[10px] text-red-500 ml-1">({t.reason})</span>
                              )}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {!meeting.summary && (
                      <p className="text-xs text-slate-600 italic">Reunião ainda não processada.</p>
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
