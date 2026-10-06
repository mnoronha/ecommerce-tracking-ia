'use client'

import { useState, useEffect } from 'react'
import { useParams }           from 'next/navigation'
import Link                    from 'next/link'
import { ArrowLeft, GitCommit, Loader2, RefreshCw, ChevronDown, ChevronUp } from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ChangeRow {
  id:                   string
  client_id:            string
  occurred_at:          string
  channel:              string
  entity_type:          string
  entity_name_at_time:  string
  change_type:          string
  source:               string
  reported_by?:         string
  confidence:           string
  before?:              unknown
  after?:               unknown
  reason?:              string
  created_at:           string
}

interface ActionEventRow {
  id:                string
  recommendation_id: string
  event_type:        string
  actor:             string
  occurred_at:       string
  note?:             string
  change_id?:        string
  created_at:        string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const CHANGE_TYPE_BADGE: Record<string, string> = {
  BUDGET_CHANGE:    'bg-yellow-500/15 text-yellow-400',
  STATUS_CHANGE:    'bg-sky-500/15 text-sky-400',
  BID_CHANGE:       'bg-purple-500/15 text-purple-400',
  CREATIVE_CHANGE:  'bg-pink-500/15 text-pink-400',
  TARGETING_CHANGE: 'bg-orange-500/15 text-orange-400',
  OTHER:            'bg-slate-500/15 text-slate-400',
}

const EVENT_TYPE_BADGE: Record<string, string> = {
  APPROVED:  'bg-emerald-500/15 text-emerald-400',
  REJECTED:  'bg-red-500/15 text-red-400',
  EXECUTED:  'bg-sky-500/15 text-sky-400',
  REVERTED:  'bg-yellow-500/15 text-yellow-400',
  NOTED:     'bg-slate-500/15 text-slate-400',
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
    timeZone: 'America/Sao_Paulo',
  })
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ChangesPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [changes,  setChanges]  = useState<ChangeRow[]>([])
  const [events,   setEvents]   = useState<ActionEventRow[]>([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)
  const [tab,      setTab]      = useState<'changes' | 'events'>('changes')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const [chRes] = await Promise.all([
        fetch(`/api/v1/clients/${clientId}/changes`),
      ])
      if (!chRes.ok) throw new Error(`HTTP ${chRes.status}`)
      setChanges(await chRes.json())
    } catch {
      setError('Não foi possível carregar os dados.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [clientId])

  function toggle(id: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  return (
    <div className="p-8 max-w-5xl">
      {/* Header */}
      <div className="mb-8 flex items-start justify-between gap-4">
        <div>
          <Link href={`/clients/${clientId}/dashboard`} className="inline-flex items-center gap-1.5 text-slate-500 hover:text-white text-xs mb-3 transition-colors">
            <ArrowLeft size={12} /> Voltar
          </Link>
          <div className="flex items-center gap-2">
            <GitCommit size={16} className="text-indigo-400" />
            <h1 className="text-xl font-bold text-white">Mudanças & Rastreabilidade</h1>
          </div>
          <p className="text-xs text-slate-500 mt-1">
            Change log e action events · fonte: Agency API Core
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

      {/* Tabs */}
      <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e] mb-6 w-fit">
        {(['changes', 'events'] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-1.5 rounded text-xs font-medium transition-colors ${
              tab === t ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
            }`}
          >
            {t === 'changes' ? `Mudanças (${changes.length})` : `Action Events (${events.length})`}
          </button>
        ))}
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
      ) : tab === 'changes' ? (
        changes.length === 0 ? (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-12 text-center">
            <GitCommit size={32} className="text-slate-600 mx-auto mb-3" />
            <p className="text-slate-400">Nenhuma mudança registrada</p>
          </div>
        ) : (
          <div className="space-y-2">
            {changes.map(ch => {
              const isOpen = expanded.has(ch.id)
              return (
                <div key={ch.id} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
                  <button
                    onClick={() => toggle(ch.id)}
                    className="w-full text-left px-5 py-3.5 flex items-center gap-3 hover:bg-[#252b3b] transition-colors"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className={`text-xs px-2 py-0.5 rounded font-medium ${CHANGE_TYPE_BADGE[ch.change_type] || CHANGE_TYPE_BADGE.OTHER}`}>
                          {ch.change_type.replace(/_/g, ' ')}
                        </span>
                        <span className="text-xs text-slate-400 font-medium">{ch.channel}</span>
                        <span className="text-xs text-slate-500">·</span>
                        <span className="text-xs text-slate-400 truncate">{ch.entity_name_at_time}</span>
                      </div>
                      <p className="text-xs text-slate-600 mt-1">{fmtDate(ch.occurred_at)}</p>
                    </div>
                    {isOpen
                      ? <ChevronUp size={14} className="text-slate-500 shrink-0" />
                      : <ChevronDown size={14} className="text-slate-500 shrink-0" />
                    }
                  </button>

                  {isOpen && (
                    <div className="px-5 pb-4 border-t border-[#2a2f3e] space-y-3">
                      <div className="grid grid-cols-2 gap-3 mt-3">
                        <div>
                          <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-1">Tipo de entidade</p>
                          <p className="text-xs text-slate-300">{ch.entity_type}</p>
                        </div>
                        <div>
                          <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-1">Fonte</p>
                          <p className="text-xs text-slate-300">{ch.source}</p>
                        </div>
                        {ch.reported_by && (
                          <div>
                            <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-1">Reportado por</p>
                            <p className="text-xs text-slate-300">{ch.reported_by}</p>
                          </div>
                        )}
                        <div>
                          <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-1">Confiança</p>
                          <p className="text-xs text-slate-300">{ch.confidence}</p>
                        </div>
                      </div>
                      {ch.reason && (
                        <div>
                          <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-1">Motivo</p>
                          <p className="text-xs text-slate-300">{ch.reason}</p>
                        </div>
                      )}
                      {(ch.before || ch.after) && (
                        <div className="grid grid-cols-2 gap-3">
                          {ch.before != null && (
                            <div>
                              <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-1">Antes</p>
                              <pre className="text-[10px] text-slate-400 bg-[#0f1117] rounded p-2 overflow-auto">
                                {JSON.stringify(ch.before, null, 2)}
                              </pre>
                            </div>
                          )}
                          {ch.after != null && (
                            <div>
                              <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-1">Depois</p>
                              <pre className="text-[10px] text-slate-400 bg-[#0f1117] rounded p-2 overflow-auto">
                                {JSON.stringify(ch.after, null, 2)}
                              </pre>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )
      ) : (
        events.length === 0 ? (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-12 text-center">
            <GitCommit size={32} className="text-slate-600 mx-auto mb-3" />
            <p className="text-slate-400">Nenhum action event registrado</p>
          </div>
        ) : (
          <div className="space-y-2">
            {events.map(ev => (
              <div key={ev.id} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl px-5 py-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                      <span className={`text-xs px-2 py-0.5 rounded font-medium ${EVENT_TYPE_BADGE[ev.event_type] || 'bg-slate-500/15 text-slate-400'}`}>
                        {ev.event_type}
                      </span>
                      <span className="text-xs text-slate-500">por {ev.actor}</span>
                    </div>
                    {ev.note && <p className="text-sm text-slate-300">{ev.note}</p>}
                    <p className="text-xs text-slate-600 mt-1">{fmtDate(ev.occurred_at)}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )
      )}
    </div>
  )
}
