import { redirect } from 'next/navigation'
import {
  AlertTriangle, CheckCircle2, XCircle, HelpCircle, Clock,
  TrendingUp, TrendingDown, Minus, Eye, Zap, Target, List,
} from 'lucide-react'
import { createSupabaseServerClient } from '@/lib/supabase-server'

// ── Types ─────────────────────────────────────────────────────────────────────

interface WowEntry  { change_pct: number | null; current: number | null; prior: number | null }
interface MetricEntry { value: number | null; value_status: string; unit?: string; wow_pct?: number | null }

interface ChannelState {
  contracted: boolean
  health: string
  metrics?: Record<string, MetricEntry>
}

interface DiagnosisEntry {
  id: string
  code: string
  finding: string
  evidence: Record<string, unknown>
  confidence: 'HIGH' | 'MEDIUM' | 'LOW'
  business_impact: 'HIGH' | 'MEDIUM' | 'LOW'
}

interface ActionEntry {
  id: string
  code: string
  action: string
  priority: 'HIGH' | 'MEDIUM' | 'LOW'
  owner_role: string
  expected_effect: string
  status: string
  requires_human_approval: boolean
}

interface WatchlistEntry {
  type: string
  item: string
  reason: string
  change_pct?: number
  target_ratio?: number
  count?: number
}

interface CausalityItem {
  platform: string
  change: { change_type: string; impact: string; entity_type?: string }
  causality: 'LIKELY_RELATED' | 'POSSIBLY_RELATED' | 'NO_EVIDENCE' | 'NEEDS_MORE_DATA'
}

interface DeepReviewContract {
  schema_version:  string
  generated_at:    string
  review_status:   string
  client:          { client_id: string; name: string }
  period:          { start: string; end: string; label: string }
  section_a_executive_state: {
    review_status:    string
    week_label:       string
    top_metric_moves: Array<{ metric: string; change_pct: number; current: number; prior: number }>
    critical_alerts:  unknown[]
    data_quality:     string
  }
  section_b_channel_review: Record<string, ChannelState>
  section_c_campaign_review: { campaigns: unknown[]; total_changes: number }
  section_d_change_impact: {
    impact_assessment: CausalityItem[]
    causality_summary: Record<string, number>
  }
  section_e_tracking_quality: {
    sources:     Record<string, string>
    issues:      Array<{ source: string; state: string; severity: string }>
    limitations: string[]
    overall:     string
  }
  section_f_diagnoses:           DiagnosisEntry[]
  section_g_recommended_actions: ActionEntry[]
  section_h_watchlist:           WatchlistEntry[]
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const REVIEW_STATUS_BADGE: Record<string, { label: string; cls: string }> = {
  READY_FOR_REVIEW: { label: 'Pronto para revisão', cls: 'bg-emerald-500/15 text-emerald-400' },
  PARTIAL:          { label: 'Parcial',              cls: 'bg-yellow-500/15  text-yellow-400'  },
  BLOCKED:          { label: 'Bloqueado',            cls: 'bg-red-500/15     text-red-400'     },
}

const HEALTH_BADGE: Record<string, { label: string; cls: string; Icon: React.ElementType }> = {
  OK:              { label: 'OK',              cls: 'text-emerald-400', Icon: CheckCircle2 },
  PARTIAL:         { label: 'Parcial',         cls: 'text-yellow-400',  Icon: AlertTriangle },
  ERROR:           { label: 'Erro',            cls: 'text-red-400',     Icon: XCircle },
  MISSING:         { label: 'Ausente',         cls: 'text-orange-400',  Icon: AlertTriangle },
  STALE:           { label: 'Desatualizado',   cls: 'text-yellow-400',  Icon: Clock },
  NOT_CONTRACTED:  { label: 'Não contratado',  cls: 'text-slate-500',   Icon: Minus },
  UNKNOWN:         { label: 'Desconhecido',    cls: 'text-slate-500',   Icon: HelpCircle },
}

const IMPACT_CLS: Record<string, string> = {
  HIGH:   'text-red-400',
  MEDIUM: 'text-yellow-400',
  LOW:    'text-slate-500',
}

const CAUSALITY_BADGE: Record<string, { label: string; cls: string }> = {
  LIKELY_RELATED:   { label: 'Provável causa',    cls: 'bg-red-500/15     text-red-400'     },
  POSSIBLY_RELATED: { label: 'Possível relação',  cls: 'bg-yellow-500/15  text-yellow-400'  },
  NO_EVIDENCE:      { label: 'Sem evidência',     cls: 'bg-slate-700      text-slate-400'   },
  NEEDS_MORE_DATA:  { label: 'Dados insuficientes', cls: 'bg-slate-700   text-slate-500'   },
}

const PRIORITY_CLS: Record<string, string> = {
  HIGH:   'text-red-400',
  MEDIUM: 'text-yellow-400',
  LOW:    'text-slate-500',
}

function fmtPct(pct: number | null | undefined): string {
  if (pct == null) return '—'
  return `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' })
}

function WowArrow({ pct }: { pct: number | null }) {
  if (pct == null) return <Minus size={12} className="text-slate-600 inline" />
  if (pct > 0)     return <TrendingUp size={12} className="text-emerald-400 inline" />
  return                   <TrendingDown size={12} className="text-red-400 inline" />
}

function HealthPill({ status }: { status: string }) {
  const s = HEALTH_BADGE[status] ?? HEALTH_BADGE.UNKNOWN
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${s.cls}`}>
      <s.Icon size={11} />
      {s.label}
    </span>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default async function WeeklyDeepReviewPage({
  params,
}: {
  params: Promise<{ clientId: string }>
}) {
  const { clientId } = await params

  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) redirect('/login')

  // Fetch latest weekly_deep_review contract from agency_report_contracts
  const { data: rows, error } = await supabase
    .from('agency_report_contracts')
    .select('id, period_start, period_end, generated_at, schema_version, contract')
    .eq('client_slug', clientId)
    .eq('report_type', 'weekly_deep_review')
    .order('period_end', { ascending: false })
    .limit(1)

  if (error) {
    return (
      <div className="p-8 text-red-400 text-sm">
        Erro ao carregar revisão semanal: {error.message}
      </div>
    )
  }

  const row = rows?.[0] ?? null
  const contract = row?.contract as DeepReviewContract | null

  if (!contract) {
    return (
      <div className="p-8 max-w-3xl">
        <div className="text-slate-500 text-sm">
          Nenhuma revisão semanal encontrada para este cliente.
          <br />
          A revisão é gerada automaticamente toda segunda-feira às 08:15 UTC.
          <br />
          <span className="text-xs text-slate-600 mt-1 block">
            Para gerar agora: POST /clients/{clientId}/weekly-deep-review/replay
          </span>
        </div>
      </div>
    )
  }

  const secA  = contract.section_a_executive_state
  const secB  = contract.section_b_channel_review
  const secC  = contract.section_c_campaign_review
  const secD  = contract.section_d_change_impact
  const secE  = contract.section_e_tracking_quality
  const diagn = contract.section_f_diagnoses     || []
  const actns = contract.section_g_recommended_actions || []
  const watch = contract.section_h_watchlist     || []

  const statusBadge = REVIEW_STATUS_BADGE[contract.review_status] ?? REVIEW_STATUS_BADGE.BLOCKED
  const channels    = ['meta', 'google', 'business', 'ga4'] as const

  return (
    <div className="p-6 max-w-5xl space-y-8">

      {/* ── Header ────────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <h1 className="text-lg font-semibold text-white">Revisão Semanal Interna</h1>
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusBadge.cls}`}>
              {statusBadge.label}
            </span>
          </div>
          <p className="text-sm text-slate-500">
            {secA.week_label} · {fmtDate(row.period_start)} – {fmtDate(row.period_end)}
          </p>
          <p className="text-xs text-slate-600 mt-0.5">Gerado {fmtDate(contract.generated_at)}</p>
        </div>
        <span className="text-[10px] text-slate-700 border border-slate-700 px-2 py-1 rounded font-mono">
          USO INTERNO — Não compartilhar com cliente
        </span>
      </div>

      {/* ── A. Top Metric Moves ────────────────────────────────────────────── */}
      {secA.top_metric_moves.length > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-2">
            <TrendingUp size={13} /> Destaques da semana
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {secA.top_metric_moves.map(m => {
              const isPos = (m.change_pct ?? 0) > 0
              return (
                <div key={m.metric} className="bg-[#1a1f2e] rounded-lg p-3 border border-[#2a2f3e]">
                  <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">{m.metric}</p>
                  <p className={`text-lg font-bold ${isPos ? 'text-emerald-400' : 'text-red-400'}`}>
                    <WowArrow pct={m.change_pct} /> {fmtPct(m.change_pct)}
                  </p>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {m.current?.toLocaleString('pt-BR')} vs {m.prior?.toLocaleString('pt-BR')} semana ant.
                  </p>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {/* ── B. Channel Review ─────────────────────────────────────────────── */}
      <section>
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-2">
          <Eye size={13} /> Canais
        </h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {channels.map(ch => {
            const state = secB[ch]
            if (!state) return null
            const contracted = state.contracted
            return (
              <div key={ch} className="bg-[#1a1f2e] rounded-lg p-3 border border-[#2a2f3e]">
                <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-2">{ch}</p>
                <HealthPill status={state.health} />
                {contracted && state.metrics && (
                  <div className="mt-2 space-y-1">
                    {Object.entries(state.metrics).slice(0, 3).map(([mk, mv]) => (
                      <div key={mk} className="flex justify-between text-xs">
                        <span className="text-slate-600 truncate max-w-[70%]">{mk.replace(/_/g, ' ')}</span>
                        <span className="text-slate-300">
                          {mv.value?.toLocaleString('pt-BR', { maximumFractionDigits: 2 }) ?? '—'}
                          {mv.wow_pct != null && (
                            <span className={mv.wow_pct > 0 ? 'text-emerald-400' : 'text-red-400'}>
                              {' '}{fmtPct(mv.wow_pct)}
                            </span>
                          )}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </section>

      {/* ── F. Diagnoses ──────────────────────────────────────────────────── */}
      {diagn.length > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-2">
            <AlertTriangle size={13} /> Diagnósticos ({diagn.length})
          </h2>
          <div className="space-y-2">
            {diagn.map(d => (
              <div key={d.id} className="bg-[#1a1f2e] rounded-lg p-4 border border-[#2a2f3e]">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm text-white font-medium">{d.finding}</p>
                    <p className="text-[10px] text-slate-600 mt-0.5 font-mono">{d.code}</p>
                  </div>
                  <div className="flex flex-col items-end gap-1 shrink-0">
                    <span className={`text-[10px] font-semibold uppercase ${IMPACT_CLS[d.business_impact]}`}>
                      {d.business_impact}
                    </span>
                    <span className="text-[10px] text-slate-600">confiança: {d.confidence}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── G. Recommended Actions ────────────────────────────────────────── */}
      {actns.length > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-2">
            <Zap size={13} /> Ações recomendadas ({actns.length}) — PROPOSTAS
          </h2>
          <div className="space-y-2">
            {actns.map(a => (
              <div key={a.id} className="bg-[#1a1f2e] rounded-lg p-4 border border-[#2a2f3e]">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1">
                    <p className="text-sm text-white">{a.action}</p>
                    <p className="text-xs text-slate-500 mt-1">{a.expected_effect}</p>
                  </div>
                  <div className="flex flex-col items-end gap-1 shrink-0">
                    <span className={`text-[10px] font-semibold uppercase ${PRIORITY_CLS[a.priority]}`}>
                      {a.priority}
                    </span>
                    <span className="text-[10px] text-slate-600">{a.owner_role}</span>
                    <span className="text-[10px] bg-orange-500/15 text-orange-400 px-1.5 py-0.5 rounded">
                      Requer aprovação
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── D. Change Impact ──────────────────────────────────────────────── */}
      {(secD.impact_assessment || []).length > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-2">
            <Target size={13} /> Impacto de mudanças
          </h2>
          <div className="space-y-1.5">
            {(secD.impact_assessment || []).map((item, i) => {
              const cb = CAUSALITY_BADGE[item.causality] ?? CAUSALITY_BADGE.NO_EVIDENCE
              return (
                <div key={i} className="flex items-center justify-between gap-3 bg-[#1a1f2e] rounded px-3 py-2 border border-[#2a2f3e]">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-[10px] text-slate-600 uppercase shrink-0">{item.platform}</span>
                    <span className="text-xs text-slate-300 truncate">
                      {item.change.change_type?.replace(/_/g, ' ')}
                    </span>
                    <span className={`text-[10px] uppercase font-medium shrink-0 ${
                      item.change.impact === 'high' ? 'text-red-400' : 'text-yellow-400'
                    }`}>
                      {item.change.impact}
                    </span>
                  </div>
                  <span className={`text-[10px] px-2 py-0.5 rounded-full shrink-0 ${cb.cls}`}>
                    {cb.label}
                  </span>
                </div>
              )
            })}
          </div>
          <div className="mt-2 flex gap-3 flex-wrap text-[10px] text-slate-600">
            {Object.entries(secD.causality_summary || {}).map(([k, v]) => (
              v > 0 ? <span key={k}>{k.replace(/_/g, ' ')}: {v}</span> : null
            ))}
          </div>
        </section>
      )}

      {/* ── C. Campaign Changes ───────────────────────────────────────────── */}
      {secC.total_changes > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3 flex items-center gap-2">
            <List size={13} /> Mudanças em campanhas ({secC.total_changes})
          </h2>
          <div className="space-y-2">
            {(secC.campaigns as Array<{
              entity_name: string | null; entity_type: string; channel: string
              changes: Array<{ occurred_at: string; before_status?: string; after_status?: string; before_budget_brl?: string; after_budget_brl?: string }>
            }>).slice(0, 10).map((camp, i) => (
              <div key={i} className="bg-[#1a1f2e] rounded-lg px-3 py-2.5 border border-[#2a2f3e]">
                <div className="flex items-center justify-between gap-2 mb-1.5">
                  <span className="text-xs text-white font-medium truncate">
                    {camp.entity_name ?? camp.entity_type}
                  </span>
                  <span className="text-[10px] text-slate-600 uppercase shrink-0">{camp.channel}</span>
                </div>
                {camp.changes.map((ch, j) => (
                  <div key={j} className="text-[10px] text-slate-500 ml-2">
                    {ch.before_status && ch.after_status
                      ? `${ch.before_status} → ${ch.after_status}`
                      : ch.before_budget_brl
                      ? `${ch.before_budget_brl} → ${ch.after_budget_brl}`
                      : null}
                    {ch.occurred_at && (
                      <span className="text-slate-700 ml-1">
                        {new Date(ch.occurred_at).toLocaleDateString('pt-BR')}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── H. Watchlist ──────────────────────────────────────────────────── */}
      {watch.length > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
            Watchlist — monitorar próxima semana
          </h2>
          <div className="space-y-1.5">
            {watch.map((w, i) => (
              <div key={i} className="flex items-start gap-2 text-xs text-slate-400 bg-[#1a1f2e] rounded px-3 py-2 border border-[#2a2f3e]">
                <Clock size={11} className="text-yellow-500 mt-0.5 shrink-0" />
                <span>{w.reason}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── E. Data Quality ───────────────────────────────────────────────── */}
      {secE.issues.length > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
            Qualidade de dados
          </h2>
          <div className="space-y-1">
            {secE.issues.map((issue, i) => (
              <div key={i} className={`text-xs px-3 py-1.5 rounded border flex items-center gap-2 ${
                issue.severity === 'critical'
                  ? 'border-red-500/30 text-red-400 bg-red-500/5'
                  : 'border-slate-700 text-slate-500 bg-transparent'
              }`}>
                {issue.severity === 'critical' ? <XCircle size={11} /> : <HelpCircle size={11} />}
                {issue.source}: {issue.state}
              </div>
            ))}
          </div>
        </section>
      )}

    </div>
  )
}
