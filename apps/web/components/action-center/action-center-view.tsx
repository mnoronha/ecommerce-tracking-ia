'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import Link from 'next/link'
import {
  Zap, Bell, FileText, Lightbulb, Calendar, BookOpen,
  RefreshCw, Loader2, CheckCircle2, XCircle, Clock,
  AlertTriangle, Send, Upload, ExternalLink, Filter,
} from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ClientSummary {
  client_id: string
  client_uuid: string
  name: string
}

interface ActionCenterAlert {
  id: string
  client_id: string
  client_name: string
  alert_type: string
  severity: string
  status: string
  title: string
  message: string
  occurrence_count?: number
  detected_at: string
}

interface ActionCenterReport {
  report_id: string
  version: number
  report_type: string
  status: string
  period_start: string
  period_end: string
  generated_at?: string
  client_id: string
  client_name: string
  quality_scores?: {
    data_quality?: number
    data_label?: string
    analysis_quality?: number
    analysis_label?: string
  } | null
}

interface ActionCenterRecommendation {
  id: string
  client_id: string
  client_name: string
  title: string
  action: string
  rationale: string
  priority: string
  risk?: string | null
  expected_impact?: string | null
  status: string
  created_at: string
}

interface ActionCenterMeetingItem {
  task: string
  owner?: string | null
  due_date?: string | null
  priority?: string | null
  meeting_id?: string | null
  meeting_title?: string | null
  client_id: string
  client_name: string
}

interface ActionCenterLearningCandidate {
  id: string
  client_id: string
  client_name: string
  statement: string
  scope: string
  evidence_level: string
  status: string
  pattern?: string | null
  observed_outcome?: {
    outcome_status?: string | null
    delta_pct?: number | null
  }
  created_at: string
}

interface ActionCenterPayload {
  generated_at: string
  scope: 'agency' | 'client'
  client_id: string | null
  clients: ClientSummary[]
  counts: {
    alerts: number
    critical_alerts: number
    reports_ready_for_review: number
    pending_recommendations: number
    meeting_action_items: number
    learning_candidates_pending: number
    learning_candidates_approved: number
  }
  alerts: ActionCenterAlert[]
  reports_ready_for_review: ActionCenterReport[]
  pending_recommendations: ActionCenterRecommendation[]
  meeting_action_items: ActionCenterMeetingItem[]
  learning_candidates: ActionCenterLearningCandidate[]
  section_errors: Record<string, string[]>
}

type SectionTab =
  | 'all'
  | 'alerts'
  | 'reports'
  | 'recommendations'
  | 'meetings'
  | 'learnings'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso?: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'America/Sao_Paulo',
  })
}

const SEV_BADGE: Record<string, string> = {
  HIGH: 'bg-red-500/20 text-red-400 border border-red-500/30',
  MEDIUM: 'bg-amber-500/20 text-amber-400 border border-amber-500/30',
  LOW: 'bg-indigo-500/20 text-indigo-400 border border-indigo-500/30',
}

const PRIO_BADGE: Record<string, string> = {
  HIGH: 'bg-red-500/15 text-red-400 border border-red-500/30',
  MEDIUM: 'bg-amber-500/15 text-amber-400 border border-amber-500/30',
  LOW: 'bg-slate-500/15 text-slate-400 border border-slate-500/30',
}

// ── Component ─────────────────────────────────────────────────────────────────

export function ActionCenterView({ clientId }: { clientId?: string }) {
  const [data, setData] = useState<ActionCenterPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<SectionTab>('all')
  const [selectedClient, setSelectedClient] = useState<string>(clientId || 'ALL')

  // Per-item action states
  const [busyId, setBusyId] = useState<string | null>(null)
  const [recFeedback, setRecFeedback] = useState<
    Record<string, { ok: boolean; msg: string; notion?: string | null; taskId?: string | null }>
  >({})
  const [reportFeedback, setReportFeedback] = useState<
    Record<string, { ok: boolean; msg: string }>
  >({})
  const [candFeedback, setCandFeedback] = useState<
    Record<string, { ok: boolean; msg: string; obsidianUrl?: string | null }>
  >({})

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const url = clientId
        ? `/api/v1/action-center?client_id=${encodeURIComponent(clientId)}`
        : '/api/v1/action-center'
      const res = await fetch(url, { cache: 'no-store' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.error || `HTTP ${res.status}`)
      }
      const payload = (await res.json()) as ActionCenterPayload
      setData(payload)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Erro ao carregar Action Center')
    } finally {
      setLoading(false)
    }
  }, [clientId])

  useEffect(() => {
    load()
  }, [load])

  const filterByClient = useCallback(
    <T extends { client_id: string }>(items: T[]) => {
      if (clientId || selectedClient === 'ALL') return items
      return items.filter((item) => item.client_id === selectedClient)
    },
    [clientId, selectedClient],
  )

  const alerts = useMemo(() => filterByClient(data?.alerts ?? []), [data, filterByClient])
  const reports = useMemo(
    () => filterByClient(data?.reports_ready_for_review ?? []),
    [data, filterByClient],
  )
  const recs = useMemo(
    () => filterByClient(data?.pending_recommendations ?? []),
    [data, filterByClient],
  )
  const meetingItems = useMemo(
    () => filterByClient(data?.meeting_action_items ?? []),
    [data, filterByClient],
  )
  const candidates = useMemo(
    () => filterByClient(data?.learning_candidates ?? []),
    [data, filterByClient],
  )

  // ── Mutations (all via existing hardened BFF routes) ────────────────────────

  async function handleRecommendationDecision(
    rec: ActionCenterRecommendation,
    decision: 'ACCEPTED' | 'DEFERRED' | 'REJECTED',
  ) {
    const key = `rec:${rec.id}`
    setBusyId(key)
    try {
      const res = await fetch(`/api/v1/recommendations/${rec.id}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          decision,
          reason:
            decision === 'ACCEPTED'
              ? 'Aprovada via Action Center (1-clique)'
              : null,
          idempotency_key: `ac-${rec.id}-${decision}-${Date.now()}`,
        }),
      })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) {
        setRecFeedback((prev) => ({
          ...prev,
          [rec.id]: { ok: false, msg: body.detail || body.error || `HTTP ${res.status}` },
        }))
        return
      }
      setRecFeedback((prev) => ({
        ...prev,
        [rec.id]: {
          ok: true,
          msg:
            decision === 'ACCEPTED'
              ? 'Aprovada — task criada no Core'
              : decision === 'DEFERRED'
              ? 'Adiada'
              : 'Rejeitada',
          taskId: body.task_id ?? null,
          notion: body.notion_sync_status ?? null,
        },
      }))
      // Remove from pending list on terminal decision
      setData((prev) =>
        prev
          ? {
              ...prev,
              pending_recommendations: prev.pending_recommendations.map((r) =>
                r.id === rec.id
                  ? { ...r, status: decision === 'ACCEPTED' ? 'APPROVED' : decision }
                  : r,
              ),
            }
          : prev,
      )
    } catch (e) {
      setRecFeedback((prev) => ({
        ...prev,
        [rec.id]: { ok: false, msg: e instanceof Error ? e.message : 'Erro de rede' },
      }))
    } finally {
      setBusyId(null)
    }
  }

  async function handleSendTestEmail(report: ActionCenterReport) {
    const key = `rpt:${report.report_id}:v${report.version}`
    setBusyId(key)
    try {
      const res = await fetch(`/api/v1/canonical-reports/${report.report_id}/test-email`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': `ac-test-${report.report_id}-v${report.version}-${Date.now()}`,
        },
        body: JSON.stringify({
          version: report.version,
          recipient_email: 'contato@norolabs.com.br',
        }),
      })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) {
        setReportFeedback((prev) => ({
          ...prev,
          [key]: { ok: false, msg: body.detail || body.error || `HTTP ${res.status}` },
        }))
        return
      }
      setReportFeedback((prev) => ({
        ...prev,
        [key]: {
          ok: true,
          msg: `E-mail [TESTE INTERNO] enviado para contato@norolabs.com.br (${body.status || 'SENT'})`,
        },
      }))
    } catch (e) {
      setReportFeedback((prev) => ({
        ...prev,
        [key]: { ok: false, msg: e instanceof Error ? e.message : 'Erro de rede' },
      }))
    } finally {
      setBusyId(null)
    }
  }

  async function handleCandidateDecision(
    cand: ActionCenterLearningCandidate,
    decision: 'APPROVED' | 'REJECTED',
  ) {
    const key = `cand:${cand.id}`
    setBusyId(key)
    try {
      const res = await fetch(`/api/v1/learning-candidates/${cand.id}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          decision,
          note: decision === 'APPROVED' ? 'Aprovado via Action Center' : 'Rejeitado via Action Center',
          idempotency_key: `ac-cand-${cand.id}-${decision}-${Date.now()}`,
        }),
      })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) {
        setCandFeedback((prev) => ({
          ...prev,
          [cand.id]: { ok: false, msg: body.detail || body.error || `HTTP ${res.status}` },
        }))
        return
      }
      setCandFeedback((prev) => ({
        ...prev,
        [cand.id]: {
          ok: true,
          msg:
            decision === 'APPROVED'
              ? 'Aprovado — pronto para promoção manual ao Obsidian'
              : 'Rejeitado',
        },
      }))
      setData((prev) =>
        prev
          ? {
              ...prev,
              learning_candidates: prev.learning_candidates.map((c) =>
                c.id === cand.id ? { ...c, status: decision } : c,
              ),
            }
          : prev,
      )
    } catch (e) {
      setCandFeedback((prev) => ({
        ...prev,
        [cand.id]: { ok: false, msg: e instanceof Error ? e.message : 'Erro de rede' },
      }))
    } finally {
      setBusyId(null)
    }
  }

  async function handlePromoteToObsidian(cand: ActionCenterLearningCandidate) {
    const key = `promote:${cand.id}`
    setBusyId(key)
    try {
      const res = await fetch(`/api/v1/learning-candidates/${cand.id}/promote`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': `ac-promote-${cand.id}-${Date.now()}`,
        },
        body: JSON.stringify({ reason: 'Promoção manual via Action Center' }),
      })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) {
        setCandFeedback((prev) => ({
          ...prev,
          [cand.id]: { ok: false, msg: body.detail || body.error || `HTTP ${res.status}` },
        }))
        return
      }
      const obsidianStatus = body.obsidian_status || body.status || 'PROMOTED'
      setCandFeedback((prev) => ({
        ...prev,
        [cand.id]: {
          ok: true,
          msg: `Promovido (${body.status || 'COMPLETED'} · Obsidian: ${obsidianStatus})`,
          obsidianUrl: body.obsidian_url ?? null,
        },
      }))
    } catch (e) {
      setCandFeedback((prev) => ({
        ...prev,
        [cand.id]: { ok: false, msg: e instanceof Error ? e.message : 'Erro de rede' },
      }))
    } finally {
      setBusyId(null)
    }
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  const show = (s: SectionTab) => tab === 'all' || tab === s

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6 text-slate-200">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5">
            <Zap size={18} className="text-indigo-400" />
            <h1 className="text-xl font-bold text-white">Action Center</h1>
            <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">
              {clientId ? 'Cliente' : 'Portfólio Agência'}
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Cockpit diário unificado · Alertas H6, relatórios em revisão, decisões Hermes → Notion, reuniões e aprendizados → Obsidian
            {data?.generated_at ? ` · Atualizado ${fmtDate(data.generated_at)}` : ''}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {!clientId && data && data.clients.length > 1 && (
            <div className="flex items-center gap-1.5 bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-2.5 py-1.5">
              <Filter size={13} className="text-slate-400" />
              <select
                value={selectedClient}
                onChange={(e) => setSelectedClient(e.target.value)}
                className="bg-transparent text-xs text-slate-200 focus:outline-none"
              >
                <option value="ALL" className="bg-[#1a1f2e]">
                  Todos os clientes ({data.clients.length})
                </option>
                {data.clients.map((c) => (
                  <option key={c.client_id} value={c.client_id} className="bg-[#1a1f2e]">
                    {c.name}
                  </option>
                ))}
              </select>
            </div>
          )}
          <button
            onClick={load}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#1a1f2e] border border-[#2a2f3e] text-xs text-slate-300 hover:text-white hover:border-slate-600 transition-colors disabled:opacity-50"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            Atualizar
          </button>
        </div>
      </div>

      {/* KPI Strip */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        {[
          {
            id: 'alerts' as SectionTab,
            label: 'Alertas H6',
            value: alerts.length,
            sub: `${alerts.filter((a) => a.severity === 'HIGH').length} críticos`,
            accent: alerts.some((a) => a.severity === 'HIGH') ? 'text-red-400' : 'text-slate-200',
            icon: Bell,
          },
          {
            id: 'reports' as SectionTab,
            label: 'Relatórios',
            value: reports.length,
            sub: 'READY_FOR_REVIEW',
            accent: reports.length > 0 ? 'text-indigo-400' : 'text-slate-200',
            icon: FileText,
          },
          {
            id: 'recommendations' as SectionTab,
            label: 'Recomendações',
            value: recs.filter((r) => r.status === 'PROPOSED').length,
            sub: 'Aguardando decisão',
            accent: recs.length > 0 ? 'text-emerald-400' : 'text-slate-200',
            icon: Lightbulb,
          },
          {
            id: 'meetings' as SectionTab,
            label: 'Reuniões',
            value: meetingItems.length,
            sub: 'Action items abertos',
            accent: 'text-sky-400',
            icon: Calendar,
          },
          {
            id: 'learnings' as SectionTab,
            label: 'Aprendizados',
            value: candidates.length,
            sub: `${candidates.filter((c) => c.status === 'PENDING').length} PENDING · ${
              candidates.filter((c) => c.status === 'APPROVED').length
            } p/ Obsidian`,
            accent: 'text-amber-400',
            icon: BookOpen,
          },
        ].map((card) => (
          <button
            key={card.id}
            onClick={() => setTab(tab === card.id ? 'all' : card.id)}
            className={`text-left rounded-xl border p-3.5 transition-colors ${
              tab === card.id
                ? 'bg-indigo-600/15 border-indigo-500/50'
                : 'bg-[#1a1f2e] border-[#2a2f3e] hover:border-slate-600'
            }`}
          >
            <div className="flex items-center justify-between text-slate-400 text-xs">
              <span>{card.label}</span>
              <card.icon size={14} />
            </div>
            <p className={`text-2xl font-bold mt-1 ${card.accent}`}>{card.value}</p>
            <p className="text-[11px] text-slate-500 mt-0.5 truncate">{card.sub}</p>
          </button>
        ))}
      </div>

      {/* Filter Tabs */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-[#2a2f3e] pb-3">
        {(
          [
            ['all', 'Visão Unificada'],
            ['alerts', `Alertas H6 (${alerts.length})`],
            ['reports', `Relatórios (${reports.length})`],
            ['recommendations', `Recomendações (${recs.length})`],
            ['meetings', `Action Items (${meetingItems.length})`],
            ['learnings', `Aprendizados (${candidates.length})`],
          ] as Array<[SectionTab, string]>
        ).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              tab === id
                ? 'bg-indigo-600/25 text-indigo-300 border border-indigo-500/40'
                : 'text-slate-400 hover:text-white hover:bg-[#1a1f2e]'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Error / Partial warnings */}
      {error && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300 flex items-center gap-2">
          <AlertTriangle size={16} />
          <span>{error}</span>
        </div>
      )}

      {data && Object.keys(data.section_errors || {}).length > 0 && (
        <div className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-3 text-xs text-amber-300">
          <strong>Aviso de cobertura parcial:</strong> algumas fontes retornaram erro temporário (
          {Object.keys(data.section_errors).join(', ')}). Ausência de itens não confirma ausência de ocorrências.
        </div>
      )}

      {loading && !data ? (
        <div className="flex items-center justify-center py-16 text-slate-400 gap-2 text-sm">
          <Loader2 size={18} className="animate-spin text-indigo-400" />
          Carregando filas operacionais do Core…
        </div>
      ) : (
        <div className="space-y-6">
          {/* (a) Alertas críticos H6 ativos */}
          {show('alerts') && (
            <section className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-white flex items-center gap-2">
                  <Bell size={15} className="text-red-400" />
                  Alertas H6 Ativos ({alerts.length})
                </h2>
                <Link
                  href={clientId ? `/clients/${clientId}/alertas` : '/alertas'}
                  className="text-xs text-indigo-400 hover:text-indigo-300"
                >
                  Ver todos →
                </Link>
              </div>
              {alerts.length === 0 ? (
                <p className="rounded-xl border border-[#2a2f3e] bg-[#1a1f2e]/60 p-4 text-xs text-slate-500">
                  Nenhum alerta H6 aberto no momento.
                </p>
              ) : (
                <div className="grid gap-2.5 md:grid-cols-2">
                  {alerts.slice(0, tab === 'all' ? 6 : 50).map((a) => (
                    <article
                      key={a.id}
                      className="rounded-xl border border-[#2a2f3e] bg-[#1a1f2e] p-4 space-y-1.5"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <span
                            className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${
                              SEV_BADGE[a.severity] || SEV_BADGE.LOW
                            }`}
                          >
                            {a.severity}
                          </span>
                          {!clientId && (
                            <span className="text-[11px] text-slate-400 truncate">
                              {a.client_name}
                            </span>
                          )}
                        </div>
                        <span className="text-[11px] text-slate-500 shrink-0">
                          {fmtDate(a.detected_at)}
                        </span>
                      </div>
                      <h3 className="text-sm font-semibold text-white">{a.title}</h3>
                      <p className="text-xs text-slate-400 line-clamp-2">{a.message}</p>
                      <div className="pt-1 flex items-center justify-between text-[11px]">
                        <span className="text-slate-500 font-mono">{a.alert_type}</span>
                        <Link
                          href={`/clients/${a.client_id}/alertas`}
                          className="text-indigo-400 hover:text-indigo-300 inline-flex items-center gap-1"
                        >
                          Investigar <ExternalLink size={11} />
                        </Link>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          )}

          {/* (b) Relatórios READY_FOR_REVIEW */}
          {show('reports') && (
            <section className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-white flex items-center gap-2">
                  <FileText size={15} className="text-indigo-400" />
                  Relatórios READY_FOR_REVIEW ({reports.length})
                </h2>
                {clientId && (
                  <Link
                    href={`/clients/${clientId}/canonical-reports`}
                    className="text-xs text-indigo-400 hover:text-indigo-300"
                  >
                    Abrir tela de relatórios →
                  </Link>
                )}
              </div>
              {reports.length === 0 ? (
                <p className="rounded-xl border border-[#2a2f3e] bg-[#1a1f2e]/60 p-4 text-xs text-slate-500">
                  Nenhum relatório aguardando revisão humana.
                </p>
              ) : (
                <div className="space-y-2.5">
                  {reports.map((r) => {
                    const fbKey = `rpt:${r.report_id}:v${r.version}`
                    const fb = reportFeedback[fbKey]
                    return (
                      <article
                        key={fbKey}
                        className="rounded-xl border border-[#2a2f3e] bg-[#1a1f2e] p-4 flex flex-wrap items-center justify-between gap-4"
                      >
                        <div className="space-y-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-xs font-bold uppercase px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300">
                              {r.report_type === 'monthly' ? 'H8 Mensal' : 'H7 Semanal'} · v{r.version}
                            </span>
                            {!clientId && (
                              <span className="text-xs font-medium text-white">{r.client_name}</span>
                            )}
                            <span className="text-xs text-slate-400">
                              {r.period_start} → {r.period_end}
                            </span>
                            {r.quality_scores?.data_label && (
                              <span className="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">
                                Dados: {r.quality_scores.data_label}
                              </span>
                            )}
                          </div>
                          {fb && (
                            <p
                              className={`text-xs ${
                                fb.ok ? 'text-emerald-400' : 'text-red-400'
                              }`}
                            >
                              {fb.msg}
                            </p>
                          )}
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => handleSendTestEmail(r)}
                            disabled={busyId === fbKey}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border border-amber-500/30 text-xs font-medium transition-colors disabled:opacity-50"
                            title="Dispara exclusivamente para contato@norolabs.com.br com [TESTE INTERNO] e CTA=false"
                          >
                            {busyId === fbKey ? (
                              <Loader2 size={12} className="animate-spin" />
                            ) : (
                              <Send size={12} />
                            )}
                            Enviar teste interno
                          </button>
                          <Link
                            href={`/clients/${r.client_id}/canonical-reports`}
                            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/30 text-xs font-medium transition-colors"
                          >
                            Revisar / Aprovar <ExternalLink size={12} />
                          </Link>
                        </div>
                      </article>
                    )
                  })}
                </div>
              )}
            </section>
          )}

          {/* (c) Recomendações Hermes pendentes (1-clique -> Notion) */}
          {show('recommendations') && (
            <section className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-white flex items-center gap-2">
                  <Lightbulb size={15} className="text-emerald-400" />
                  Recomendações Hermes Pendentes ({recs.length})
                </h2>
                {clientId && (
                  <Link
                    href={`/clients/${clientId}/recommendations`}
                    className="text-xs text-indigo-400 hover:text-indigo-300"
                  >
                    Ver histórico completo →
                  </Link>
                )}
              </div>
              {recs.length === 0 ? (
                <p className="rounded-xl border border-[#2a2f3e] bg-[#1a1f2e]/60 p-4 text-xs text-slate-500">
                  Nenhuma recomendação pendente de decisão humana.
                </p>
              ) : (
                <div className="space-y-3">
                  {recs.map((rec) => {
                    const busy = busyId === `rec:${rec.id}`
                    const fb = recFeedback[rec.id]
                    const decidable = rec.status === 'PROPOSED' || rec.status === 'DEFERRED'
                    return (
                      <article
                        key={rec.id}
                        className="rounded-xl border border-[#2a2f3e] bg-[#1a1f2e] p-4 space-y-2.5"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex items-center gap-2">
                            <span
                              className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${
                                PRIO_BADGE[rec.priority] || PRIO_BADGE.LOW
                              }`}
                            >
                              {rec.priority}
                            </span>
                            {!clientId && (
                              <span className="text-xs text-indigo-300 font-medium">
                                {rec.client_name}
                              </span>
                            )}
                            <span className="text-[11px] text-slate-500">
                              {fmtDate(rec.created_at)}
                            </span>
                          </div>
                          <span className="text-[11px] px-2 py-0.5 rounded bg-slate-800 text-slate-300">
                            {rec.status}
                          </span>
                        </div>
                        <h3 className="text-sm font-semibold text-white">{rec.title}</h3>
                        <p className="text-xs text-slate-300">{rec.action}</p>
                        {rec.rationale && (
                          <p className="text-xs text-slate-500 line-clamp-2">{rec.rationale}</p>
                        )}

                        {fb && (
                          <div
                            className={`rounded-lg px-3 py-2 text-xs flex items-center gap-2 flex-wrap ${
                              fb.ok
                                ? 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-300'
                                : 'bg-red-500/10 border border-red-500/30 text-red-300'
                            }`}
                          >
                            <span>{fb.msg}</span>
                            {fb.taskId && (
                              <span className="font-mono text-[11px] text-slate-400">
                                task:{fb.taskId.slice(0, 8)}…
                              </span>
                            )}
                            {fb.notion && (
                              <span className="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-300 text-[11px]">
                                Notion: {fb.notion}
                              </span>
                            )}
                          </div>
                        )}

                        {decidable && (
                          <div className="pt-1 flex flex-wrap items-center gap-2">
                            <button
                              onClick={() => handleRecommendationDecision(rec, 'ACCEPTED')}
                              disabled={busy}
                              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/40 text-xs font-medium transition-colors disabled:opacity-50"
                            >
                              {busy ? (
                                <Loader2 size={12} className="animate-spin" />
                              ) : (
                                <CheckCircle2 size={12} />
                              )}
                              Aprovar & Criar Task Notion
                            </button>
                            <button
                              onClick={() => handleRecommendationDecision(rec, 'DEFERRED')}
                              disabled={busy}
                              className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 border border-amber-500/30 text-xs transition-colors disabled:opacity-50"
                            >
                              <Clock size={12} />
                              Adiar
                            </button>
                            <button
                              onClick={() => handleRecommendationDecision(rec, 'REJECTED')}
                              disabled={busy}
                              className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-300 border border-red-500/30 text-xs transition-colors disabled:opacity-50"
                            >
                              <XCircle size={12} />
                              Rejeitar
                            </button>
                          </div>
                        )}
                      </article>
                    )
                  })}
                </div>
              )}
            </section>
          )}

          {/* (d) Action items extraídos de reuniões recentes */}
          {show('meetings') && (
            <section className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-white flex items-center gap-2">
                  <Calendar size={15} className="text-sky-400" />
                  Action Items de Reuniões Recentes ({meetingItems.length})
                </h2>
                {clientId && (
                  <Link
                    href={`/clients/${clientId}/meetings`}
                    className="text-xs text-indigo-400 hover:text-indigo-300"
                  >
                    Ver reuniões →
                  </Link>
                )}
              </div>
              {meetingItems.length === 0 ? (
                <p className="rounded-xl border border-[#2a2f3e] bg-[#1a1f2e]/60 p-4 text-xs text-slate-500">
                  Nenhum action item aberto nas últimas 3 reuniões.
                </p>
              ) : (
                <div className="grid gap-2.5 md:grid-cols-2">
                  {meetingItems.slice(0, tab === 'all' ? 8 : 50).map((item, idx) => (
                    <article
                      key={`${item.meeting_id || 'm'}-${idx}`}
                      className="rounded-xl border border-[#2a2f3e] bg-[#1a1f2e] p-3.5 space-y-1.5"
                    >
                      <div className="flex items-center justify-between gap-2 text-[11px] text-slate-400">
                        <span className="truncate">
                          {!clientId ? `${item.client_name} · ` : ''}
                          {item.meeting_title || 'Reunião'}
                        </span>
                        {item.priority && (
                          <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 shrink-0">
                            {item.priority}
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-slate-100">{item.task}</p>
                      <div className="flex items-center justify-between text-[11px] text-slate-500 pt-0.5">
                        <span>
                          {item.owner ? `Responsável: ${item.owner}` : 'Sem responsável definido'}
                          {item.due_date ? ` · Prazo: ${item.due_date}` : ''}
                        </span>
                        <Link
                          href={`/clients/${item.client_id}/meetings`}
                          className="text-indigo-400 hover:text-indigo-300"
                        >
                          Ata →
                        </Link>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          )}

          {/* (e) Candidatos de aprendizado (PENDING & APPROVED -> Obsidian) */}
          {show('learnings') && (
            <section className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-white flex items-center gap-2">
                  <BookOpen size={15} className="text-amber-400" />
                  Candidatos de Aprendizado &amp; Promoção Obsidian ({candidates.length})
                </h2>
                {clientId && (
                  <Link
                    href={`/clients/${clientId}/learning-candidates`}
                    className="text-xs text-indigo-400 hover:text-indigo-300"
                  >
                    Gerenciar aprendizados →
                  </Link>
                )}
              </div>
              <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3.5 py-2 text-[11px] text-amber-300">
                <strong>Governança:</strong> Aprovar um candidato valida a evidência (APPROVED ≠ PROMOTED). A escrita no vault Obsidian exige clique explícito em <em>Promover p/ Obsidian</em>.
              </div>
              {candidates.length === 0 ? (
                <p className="rounded-xl border border-[#2a2f3e] bg-[#1a1f2e]/60 p-4 text-xs text-slate-500">
                  Nenhum candidato de aprendizado pendente de validação ou promoção.
                </p>
              ) : (
                <div className="space-y-2.5">
                  {candidates.map((cand) => {
                    const busyDec = busyId === `cand:${cand.id}`
                    const busyPromo = busyId === `promote:${cand.id}`
                    const fb = candFeedback[cand.id]
                    return (
                      <article
                        key={cand.id}
                        className="rounded-xl border border-[#2a2f3e] bg-[#1a1f2e] p-4 space-y-2"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span
                              className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${
                                cand.status === 'PENDING'
                                  ? 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
                                  : 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                              }`}
                            >
                              {cand.status}
                            </span>
                            {!clientId && (
                              <span className="text-xs text-indigo-300 font-medium">
                                {cand.client_name}
                              </span>
                            )}
                            <span className="text-[11px] text-slate-500">
                              {cand.scope} · {cand.evidence_level}
                            </span>
                            {cand.observed_outcome?.outcome_status && (
                              <span className="text-[11px] font-mono text-slate-400">
                                outcome:{cand.observed_outcome.outcome_status}
                              </span>
                            )}
                          </div>
                          <span className="text-[11px] text-slate-500">
                            {fmtDate(cand.created_at)}
                          </span>
                        </div>
                        <p className="text-sm text-slate-100">{cand.statement}</p>
                        {cand.pattern && (
                          <p className="text-[11px] font-mono text-slate-500">{cand.pattern}</p>
                        )}

                        {fb && (
                          <div
                            className={`rounded-lg px-3 py-2 text-xs flex items-center gap-2 ${
                              fb.ok
                                ? 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-300'
                                : 'bg-red-500/10 border border-red-500/30 text-red-300'
                            }`}
                          >
                            <span>{fb.msg}</span>
                            {fb.obsidianUrl && (
                              <a
                                href={fb.obsidianUrl}
                                target="_blank"
                                rel="noreferrer"
                                className="underline text-indigo-300 inline-flex items-center gap-1"
                              >
                                Abrir no vault <ExternalLink size={11} />
                              </a>
                            )}
                          </div>
                        )}

                        <div className="pt-1 flex flex-wrap items-center gap-2">
                          {cand.status === 'PENDING' && (
                            <>
                              <button
                                onClick={() => handleCandidateDecision(cand, 'APPROVED')}
                                disabled={busyDec}
                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/40 text-xs font-medium transition-colors disabled:opacity-50"
                              >
                                {busyDec ? (
                                  <Loader2 size={12} className="animate-spin" />
                                ) : (
                                  <CheckCircle2 size={12} />
                                )}
                                Aprovar candidato
                              </button>
                              <button
                                onClick={() => handleCandidateDecision(cand, 'REJECTED')}
                                disabled={busyDec}
                                className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-300 border border-red-500/30 text-xs transition-colors disabled:opacity-50"
                              >
                                <XCircle size={12} />
                                Rejeitar
                              </button>
                            </>
                          )}
                          {cand.status === 'APPROVED' && (
                            <button
                              onClick={() => handlePromoteToObsidian(cand)}
                              disabled={busyPromo}
                              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600/25 hover:bg-indigo-600/35 text-indigo-200 border border-indigo-500/40 text-xs font-medium transition-colors disabled:opacity-50"
                            >
                              {busyPromo ? (
                                <Loader2 size={12} className="animate-spin" />
                              ) : (
                                <Upload size={12} />
                              )}
                              Promover p/ Obsidian
                            </button>
                          )}
                        </div>
                      </article>
                    )
                  })}
                </div>
              )}
            </section>
          )}
        </div>
      )}
    </div>
  )
}
