'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { FileText, Loader2, RefreshCw, CheckCircle2 } from 'lucide-react'


interface CanonicalReport {
  report_id: string
  report_type: 'weekly' | 'monthly'
  canonical_engine: 'H7' | 'H8'
  version: number
  period_start: string
  period_end: string
  status: string
  artifact_hash: string
  approval_hash?: string | null
  approved_by?: string | null
  approved_at?: string | null
  published_at?: string | null
  superseded_at?: string | null
  superseded_by_version?: number | null
  certification_status?: Array<{ source: string; status: string }>
}

const STATUS_BADGE: Record<string, string> = {
  DRAFT:            'bg-slate-500/15 text-slate-400 border-slate-500/20',
  READY_FOR_REVIEW: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/20',
  APPROVED:         'bg-emerald-500/15 text-emerald-400 border-emerald-500/20',
  PUBLISHED:        'bg-indigo-500/15 text-indigo-400 border-indigo-500/20',
  SUPERSEDED:       'bg-slate-700/30 text-slate-600 border-slate-700/20',
}

const STATUS_FILTER = ['all', 'READY_FOR_REVIEW', 'APPROVED', 'PUBLISHED', 'SUPERSEDED']

type StatusFilter = typeof STATUS_FILTER[number]

function fmtDate(iso?: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit' })
}

function fmtDateTime(iso?: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export default function CanonicalReportsPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [reports,     setReports]     = useState<CanonicalReport[]>([])
  const [loading,     setLoading]     = useState(true)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [typeFilter,  setTypeFilter]  = useState<'all' | 'weekly' | 'monthly'>('all')
  const [expanded,    setExpanded]    = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ client_id: clientId, limit: '50' })
      if (statusFilter !== 'all') qs.set('status', statusFilter)
      if (typeFilter   !== 'all') qs.set('report_type', typeFilter)
      const res = await fetch(`/api/v1/canonical-reports/list?${qs}`)
      if (res.ok) setReports(await res.json())
      else setReports([])
    } catch {
      setReports([])
    } finally {
      setLoading(false)
    }
  }, [clientId, statusFilter, typeFilter])

  useEffect(() => { load() }, [load])

  const grouped = reports.reduce<Record<string, CanonicalReport[]>>((acc, r) => {
    const key = `${r.report_type}:${r.period_start}:${r.period_end}`
    ;(acc[key] = acc[key] || []).push(r)
    return acc
  }, {})

  return (
    <div className="p-6 space-y-5 max-w-4xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <FileText size={15} className="text-indigo-400" />
          <h2 className="text-sm font-semibold text-white">Relatórios Canônicos</h2>
          <span className="text-xs text-slate-500">· CAS store · H7/H8</span>
          {!loading && (
            <span className="text-xs px-1.5 py-0.5 rounded-full bg-slate-700/50 text-slate-400">{reports.length}</span>
          )}
        </div>
        <button onClick={load} className="text-slate-500 hover:text-white transition-colors">
          <RefreshCw size={13} />
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2">
        <div className="flex gap-1 bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg p-1">
          {STATUS_FILTER.map(s => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                statusFilter === s ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
              }`}
            >
              {s === 'all' ? 'Todos' : s}
            </button>
          ))}
        </div>
        <div className="flex gap-1 bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg p-1">
          {(['all', 'weekly', 'monthly'] as const).map(t => (
            <button
              key={t}
              onClick={() => setTypeFilter(t)}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                typeFilter === t ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
              }`}
            >
              {t === 'all' ? 'Tipo' : t === 'weekly' ? 'Semanal' : 'Mensal'}
            </button>
          ))}
        </div>
      </div>

      {/* List */}
      {loading ? (
        <div className="flex items-center gap-2 text-slate-500 text-xs py-8">
          <Loader2 size={13} className="animate-spin" /> Carregando…
        </div>
      ) : reports.length === 0 ? (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-10 text-center">
          <FileText size={28} className="text-slate-700 mx-auto mb-2" />
          <p className="text-slate-500 text-xs">Nenhum relatório canônico encontrado</p>
        </div>
      ) : (
        <div className="space-y-2">
          {Object.entries(grouped).map(([key, versions]) => {
            const main   = versions[0]
            const isOpen = expanded === key
            const hasMulti = versions.length > 1
            return (
              <div key={key} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
                <button
                  onClick={() => setExpanded(isOpen ? null : key)}
                  className="w-full flex items-center gap-3 px-4 py-3 hover:bg-[#252b3b] transition-colors text-left"
                >
                  {/* Engine badge */}
                  <span className={`text-xs font-bold px-1.5 py-0.5 rounded shrink-0 ${
                    main.canonical_engine === 'H8' ? 'bg-sky-500/15 text-sky-400' : 'bg-violet-500/15 text-violet-400'
                  }`}>
                    {main.canonical_engine}
                  </span>

                  {/* Type + period */}
                  <div className="flex-1 min-w-0 grid grid-cols-4 gap-2 items-center">
                    <span className="text-xs text-slate-300 font-medium capitalize">{main.report_type}</span>
                    <span className="text-xs text-slate-400 col-span-1">
                      {main.period_start} → {main.period_end}
                    </span>
                    {/* Latest status */}
                    <span className={`text-xs px-2 py-0.5 rounded border font-medium w-fit ${STATUS_BADGE[main.status] || STATUS_BADGE.DRAFT}`}>
                      {main.status}
                    </span>
                    <span className="text-xs text-slate-500">
                      {hasMulti ? `v${versions.map(v => v.version).join(', v')}` : `v${main.version}`}
                    </span>
                  </div>

                  <span className="text-slate-600 text-xs shrink-0">{isOpen ? '▲' : '▼'}</span>
                </button>

                {isOpen && (
                  <div className="border-t border-[#2a2f3e] divide-y divide-[#2a2f3e]/60">
                    {versions.map(v => (
                      <div key={`${v.report_id}:v${v.version}`} className="px-4 py-3 bg-[#0f1117]/30">
                        <div className="flex items-start justify-between gap-3">
                          <div className="space-y-1 text-xs">
                            <div className="flex items-center gap-2">
                              <span className={`px-1.5 py-0.5 rounded border font-medium ${STATUS_BADGE[v.status] || STATUS_BADGE.DRAFT}`}>
                                v{v.version} · {v.status}
                              </span>
                              {v.status === 'PUBLISHED' && <CheckCircle2 size={11} className="text-indigo-400" />}
                            </div>
                            <p className="text-slate-500 font-mono text-[10px]">
                              hash: {v.artifact_hash.slice(0, 16)}…
                              {v.approval_hash && v.approval_hash === v.artifact_hash && (
                                <span className="text-emerald-500 ml-2">approval_hash=artifact_hash ✓</span>
                              )}
                            </p>
                            {v.approved_by && (
                              <p className="text-slate-500">
                                <span className="text-slate-600">Aprovado por</span> {v.approved_by} · {fmtDateTime(v.approved_at)}
                              </p>
                            )}
                            {v.published_at && (
                              <p className="text-slate-500">
                                <span className="text-slate-600">Publicado</span> {fmtDateTime(v.published_at)}
                              </p>
                            )}
                            {v.superseded_at && (
                              <p className="text-slate-600">
                                Supersedido em {fmtDate(v.superseded_at)}
                                {v.superseded_by_version && ` → v${v.superseded_by_version}`}
                              </p>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
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
