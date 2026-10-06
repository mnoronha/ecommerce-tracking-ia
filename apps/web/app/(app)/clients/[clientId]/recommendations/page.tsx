'use client'

import { useState, useEffect } from 'react'
import { useParams }           from 'next/navigation'
import Link                    from 'next/link'
import { ArrowLeft, Lightbulb, ChevronDown, ChevronUp, Loader2, RefreshCw } from 'lucide-react'

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
  EXECUTED:  'bg-sky-500/15 text-sky-400',
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
    timeZone: 'America/Sao_Paulo',
  })
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function RecommendationsPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [items,    setItems]    = useState<Recommendation[]>([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  async function load() {
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
    <div className="p-8 max-w-4xl">
      {/* Header */}
      <div className="mb-8 flex items-start justify-between gap-4">
        <div>
          <Link href={`/clients/${clientId}/diagnostics`} className="inline-flex items-center gap-1.5 text-slate-500 hover:text-white text-xs mb-3 transition-colors">
            <ArrowLeft size={12} /> Voltar
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
            const isOpen = expanded.has(rec.id)
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
                    <div className="flex items-center gap-4 pt-2 border-t border-[#2a2f3e]">
                      <Link
                        href={`/api/v1/diagnoses/${rec.diagnosis_id}/recommendations`}
                        className="text-xs text-indigo-400 hover:text-indigo-300"
                        target="_blank"
                      >
                        Ver diagnóstico →
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
