'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'next/navigation'
import {
  FileText, CheckCircle, XCircle, Clock, Send, Download,
  RefreshCw, AlertTriangle, X, Eye, Loader2, History,
  TrendingUp, Target, Info, BarChart2, Lightbulb, ChevronDown,
  ChevronRight, Calendar,
} from 'lucide-react'

// ── Types ────────────────────────────────────────────────────────────────────

type ReportStatus = 'DRAFT' | 'READY_FOR_REVIEW' | 'APPROVED' | 'PUBLISHED' | 'SUPERSEDED'

interface FactItem {
  metric_key: string
  value: number | null
  unit?: string
  domain?: string
  target?: number
  target_ratio?: number
  value_status?: string
}

interface AnalysisLayer {
  status?: string
  executive_summary?: { bullets?: string[]; key_win?: string | null; key_risk?: string | null }
  google_analysis?: Record<string, unknown>
  meta_analysis?: Record<string, unknown>
  diagnosis?: Array<{ finding?: string; severity?: string; channel?: string; detail?: string }>
  recommendations?: Array<{ action: string; priority?: string; expected_effect?: string; risk?: string; confidence?: string }>
  change_digest_section?: { main_changes_narrative?: string; temporal_associations?: string[]; pending_monitoring?: string[] }
  priorities_next_week?: Array<{ action: string; owner?: string; deadline_suggestion?: string; rationale?: string }>
  analysis_notes?: string
}

interface ReportContent {
  client_id: string
  analysis_period: { start: string; end: string }
  schema_version?: string
  business_truth?: string
  facts?: FactItem[]
  channels?: {
    meta?: { spend?: number; conversions?: number; source_state?: string; roas?: number }
    google?: { spend?: number; conversions?: number; source_state?: string; roas?: number }
  }
  change_summary?: Record<string, unknown>
  anomalies_detected?: Array<{ type?: string; metric?: string; description?: string; severity?: string }>
  hypotheses?: Array<{ type: string; statement: string }>
  recommendations?: Array<{ type: string; statement: string }>
  limitations?: string[]
  observations?: Array<{ source_system: string; source_state: string }>
  analysis_layer?: AnalysisLayer
  status?: string
}

interface Report {
  report_id: string
  report_type: 'weekly' | 'monthly'
  client_id: string
  canonical_engine: string
  engine_version: string
  version: number
  status: ReportStatus
  period_start: string
  period_end: string
  generated_at: string
  approved_at?: string | null
  approved_by?: string | null
  published_at?: string | null
  artifact_hash: string
  review_status?: string
  source_state?: Array<{ source_system: string; source_state: string }>
  certification_status?: Array<{ window: string; metric_key: string; certification_status: string }>
  content?: ReportContent
}

interface DeliveryReceipt {
  delivery_id: string
  report_id: string
  report_version: number
  channel: string
  status: string
  attempt: number
  sent_at?: string | null
  provider_message_id?: string | null
  error?: string | null
  requested_by?: string
  requested_at?: string
}

interface Recipient {
  id: string
  name: string
  email: string
  active: boolean
  created_at?: string
  updated_at?: string
}

interface DeliverySettings {
  client_id: string
  weekly_enabled: boolean
  monthly_enabled: boolean
  human_approval_required: boolean
  recipients: Recipient[]
  updated_at?: string | null
}

// ── Helpers ──────────────────────────────────────────────────────────────────

const STATUS_META: Record<ReportStatus, { label: string; cls: string }> = {
  DRAFT:            { label: 'Rascunho',        cls: 'bg-slate-500/15 text-slate-400 border-slate-500/25' },
  READY_FOR_REVIEW: { label: 'Aguarda revisão', cls: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/25' },
  APPROVED:         { label: 'Aprovado',         cls: 'bg-blue-500/15 text-blue-400 border-blue-500/25' },
  PUBLISHED:        { label: 'Publicado',        cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/25' },
  SUPERSEDED:       { label: 'Substituído',      cls: 'bg-slate-500/10 text-slate-600 border-slate-500/15' },
}

function fmtDate(iso?: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

function fmtDateTime(iso?: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function fmtBRL(n?: number | null) {
  if (n == null) return null
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n)
}

async function sha256hex(text: string): Promise<string> {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('')
}

function periodLabel(r: Report) {
  if (!r.period_start) return '—'
  return `${fmtDate(r.period_start)} – ${fmtDate(r.period_end)}`
}

const DELIVERY_META: Record<string, { label: string; Icon: typeof Clock; cls: string }> = {
  NOT_SENT:         { label: 'Não enviado', Icon: Clock,       cls: 'text-slate-500' },
  DELIVERY_PENDING: { label: 'Pendente',    Icon: Clock,       cls: 'text-yellow-500' },
  DELIVERED:        { label: 'Entregue',    Icon: CheckCircle, cls: 'text-emerald-500' },
  DELIVERY_FAILED:  { label: 'Falhou',      Icon: XCircle,     cls: 'text-red-500' },
}

// ── Section component ────────────────────────────────────────────────────────

function Section({ title, icon: Icon, children, defaultOpen = true }: {
  title: string; icon: typeof FileText; children: React.ReactNode; defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border border-[#2a2f3e] rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-4 py-3 bg-[#1a1f2e] hover:bg-[#1e2535] transition-colors text-left"
      >
        <Icon size={14} className="text-indigo-400 shrink-0" />
        <span className="text-sm font-medium text-slate-200 flex-1">{title}</span>
        {open ? <ChevronDown size={14} className="text-slate-500" /> : <ChevronRight size={14} className="text-slate-500" />}
      </button>
      {open && <div className="px-4 py-3 bg-[#0f1117] space-y-2">{children}</div>}
    </div>
  )
}

// ── Report content viewer ────────────────────────────────────────────────────

function ReportViewer({ report, loading }: { report: Report; loading: boolean }) {
  const c = report.content

  if (loading || !c) {
    return (
      <div className="space-y-3 p-4">
        {[1, 2, 3, 4].map(i => (
          <div key={i} className="h-16 bg-[#1a1f2e] rounded-xl animate-pulse" />
        ))}
      </div>
    )
  }

  const fmtMetricKey = (k: string) =>
    k.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())

  const KEY_METRICS = ['revenue_business', 'orders_count', 'total_spend', 'mer', 'roas_meta', 'roas_google', 'cpa_meta', 'cpa_google']
  const keyFacts = (c.facts || []).filter(f => KEY_METRICS.includes(f.metric_key) && f.value != null)

  const al = c.analysis_layer
  const hasExecutive   = al?.executive_summary?.bullets?.length || al?.executive_summary?.key_win || al?.executive_summary?.key_risk
  const hasChanges     = al?.change_digest_section?.main_changes_narrative || (c.change_summary && Object.keys(c.change_summary).length > 0)
  const hasDiagnosis   = (al?.diagnosis?.length ?? 0) > 0 || (c.hypotheses?.length ?? 0) > 0 || (c.anomalies_detected?.length ?? 0) > 0
  const hasRecs        = (al?.recommendations?.length ?? 0) > 0 || (c.recommendations?.length ?? 0) > 0
  const hasPriorities  = (al?.priorities_next_week?.length ?? 0) > 0

  return (
    <div className="space-y-3 p-4">

      {/* Metadata header */}
      <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] px-4 py-3">
        <div className="flex flex-wrap gap-4 text-[11px] text-slate-400">
          <span><span className="text-slate-600">Período:</span> {periodLabel(report)}</span>
          <span><span className="text-slate-600">Versão:</span> v{report.version}</span>
          <span><span className="text-slate-600">Engine:</span> {report.canonical_engine}</span>
          {report.review_status && <span><span className="text-slate-600">Review:</span> {report.review_status}</span>}
          {c.schema_version && <span><span className="text-slate-600">Schema:</span> {c.schema_version}</span>}
        </div>
      </div>

      {/* 1. Executive Summary */}
      {hasExecutive ? (
        <Section title="Sumário Executivo" icon={FileText}>
          {al!.executive_summary!.key_win && (
            <div className="flex gap-2 p-2 bg-emerald-500/5 border border-emerald-500/15 rounded-lg">
              <span className="text-[10px] font-semibold text-emerald-400 shrink-0 mt-0.5">WIN</span>
              <p className="text-xs text-slate-300">{al!.executive_summary!.key_win}</p>
            </div>
          )}
          {al!.executive_summary!.key_risk && (
            <div className="flex gap-2 p-2 bg-red-500/5 border border-red-500/15 rounded-lg">
              <span className="text-[10px] font-semibold text-red-400 shrink-0 mt-0.5">RISCO</span>
              <p className="text-xs text-slate-300">{al!.executive_summary!.key_risk}</p>
            </div>
          )}
          {al?.executive_summary?.bullets?.map((b, i) => (
            <div key={i} className="flex gap-2">
              <span className="text-indigo-400 shrink-0 text-xs mt-0.5">·</span>
              <p className="text-xs text-slate-300">{b}</p>
            </div>
          ))}
        </Section>
      ) : null}

      {/* 2. Business Result */}
      <Section title="Resultado do Negócio" icon={Target}>
        {c.business_truth && (
          <p className="text-xs text-slate-300 leading-relaxed mb-3 p-2 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e]">
            {c.business_truth}
          </p>
        )}
        {keyFacts.length > 0 && (
          <div className="grid grid-cols-2 gap-2">
            {keyFacts.map(f => {
              const isRoas     = f.metric_key.includes('roas') || f.metric_key === 'mer'
              const isCurrency = f.unit === 'BRL' || ['revenue', 'spend', 'cpa'].some(k => f.metric_key.includes(k))
              const displayVal = isCurrency && f.value != null
                ? fmtBRL(f.value)
                : isRoas && f.value != null
                ? `${f.value.toFixed(2)}x`
                : f.value?.toLocaleString('pt-BR') ?? '—'
              const hitTarget = f.target_ratio != null && f.target_ratio >= 1
              return (
                <div key={f.metric_key} className="p-2 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e]">
                  <p className="text-[10px] text-slate-500 mb-0.5">{fmtMetricKey(f.metric_key)}</p>
                  <p className={`text-sm font-bold ${hitTarget ? 'text-emerald-400' : 'text-white'}`}>
                    {displayVal}
                  </p>
                  {f.target != null && (
                    <p className="text-[10px] text-slate-600 mt-0.5">
                      Meta: {isCurrency ? fmtBRL(f.target) : isRoas ? `${f.target.toFixed(1)}x` : f.target}
                      {f.target_ratio != null && ` · ${(f.target_ratio * 100).toFixed(0)}%`}
                    </p>
                  )}
                </div>
              )
            })}
          </div>
        )}
        {keyFacts.length === 0 && !c.business_truth && (
          <p className="text-xs text-slate-600 italic">Sem dados de negócio neste relatório</p>
        )}
      </Section>

      {/* 3. Google Ads */}
      {(c.channels?.google || al?.google_analysis) && (
        <Section title="Google Ads" icon={BarChart2}>
          {c.channels?.google && (
            <div className="grid grid-cols-2 gap-2 mb-2">
              {c.channels.google.spend != null && (
                <div className="p-2 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e]">
                  <p className="text-[10px] text-slate-500">Investimento</p>
                  <p className="text-sm font-bold text-white">{fmtBRL(c.channels.google.spend)}</p>
                </div>
              )}
              {c.channels.google.conversions != null && (
                <div className="p-2 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e]">
                  <p className="text-[10px] text-slate-500">Conversões</p>
                  <p className="text-sm font-bold text-white">{c.channels.google.conversions}</p>
                </div>
              )}
              {c.channels.google.roas != null && (
                <div className="p-2 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e]">
                  <p className="text-[10px] text-slate-500">ROAS</p>
                  <p className={`text-sm font-bold ${c.channels.google.roas >= 3 ? 'text-emerald-400' : c.channels.google.roas >= 1.5 ? 'text-yellow-400' : 'text-red-400'}`}>
                    {c.channels.google.roas.toFixed(2)}x
                  </p>
                </div>
              )}
              <div className="p-2 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e]">
                <p className="text-[10px] text-slate-500">Fonte</p>
                <p className={`text-xs font-medium ${c.channels.google.source_state === 'OK' ? 'text-emerald-400' : 'text-yellow-400'}`}>
                  {c.channels.google.source_state ?? 'UNKNOWN'}
                </p>
              </div>
            </div>
          )}
          {al?.google_analysis && Object.keys(al.google_analysis).length > 0 && (
            <div className="text-xs text-slate-400 space-y-1">
              {Object.entries(al.google_analysis).map(([k, v]) =>
                typeof v === 'string' && v
                  ? <p key={k}><span className="text-slate-600">{fmtMetricKey(k)}:</span> {v}</p>
                  : null
              )}
            </div>
          )}
        </Section>
      )}

      {/* 4. Meta Ads */}
      {(c.channels?.meta || al?.meta_analysis) && (
        <Section title="Meta Ads" icon={BarChart2}>
          {c.channels?.meta && (
            <div className="grid grid-cols-2 gap-2 mb-2">
              {c.channels.meta.spend != null && (
                <div className="p-2 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e]">
                  <p className="text-[10px] text-slate-500">Investimento</p>
                  <p className="text-sm font-bold text-white">{fmtBRL(c.channels.meta.spend)}</p>
                </div>
              )}
              {c.channels.meta.conversions != null && (
                <div className="p-2 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e]">
                  <p className="text-[10px] text-slate-500">Conversões</p>
                  <p className="text-sm font-bold text-white">{c.channels.meta.conversions}</p>
                </div>
              )}
              {c.channels.meta.roas != null && (
                <div className="p-2 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e]">
                  <p className="text-[10px] text-slate-500">ROAS</p>
                  <p className={`text-sm font-bold ${c.channels.meta.roas >= 3 ? 'text-emerald-400' : c.channels.meta.roas >= 1.5 ? 'text-yellow-400' : 'text-red-400'}`}>
                    {c.channels.meta.roas.toFixed(2)}x
                  </p>
                </div>
              )}
              <div className="p-2 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e]">
                <p className="text-[10px] text-slate-500">Fonte</p>
                <p className={`text-xs font-medium ${c.channels.meta.source_state === 'OK' ? 'text-emerald-400' : 'text-yellow-400'}`}>
                  {c.channels.meta.source_state ?? 'UNKNOWN'}
                </p>
              </div>
            </div>
          )}
          {al?.meta_analysis && Object.keys(al.meta_analysis).length > 0 && (
            <div className="text-xs text-slate-400 space-y-1">
              {Object.entries(al.meta_analysis).map(([k, v]) =>
                typeof v === 'string' && v
                  ? <p key={k}><span className="text-slate-600">{fmtMetricKey(k)}:</span> {v}</p>
                  : null
              )}
            </div>
          )}
        </Section>
      )}

      {/* 5. What Changed */}
      {hasChanges && (
        <Section title="O que Mudou" icon={History} defaultOpen={false}>
          {al?.change_digest_section?.main_changes_narrative && (
            <p className="text-xs text-slate-300 leading-relaxed">
              {al.change_digest_section.main_changes_narrative}
            </p>
          )}
          {al?.change_digest_section?.temporal_associations?.map((t, i) => (
            <p key={i} className="text-xs text-slate-400">· {t}</p>
          ))}
          {(al?.change_digest_section?.pending_monitoring?.length ?? 0) > 0 && (
            <div className="mt-2">
              <p className="text-[10px] text-slate-500 uppercase tracking-wide mb-1">Monitorar</p>
              {al!.change_digest_section!.pending_monitoring!.map((m, i) => (
                <p key={i} className="text-xs text-slate-400">· {m}</p>
              ))}
            </div>
          )}
        </Section>
      )}

      {/* 6. Diagnosis */}
      {hasDiagnosis && (
        <Section title="Diagnóstico" icon={AlertTriangle} defaultOpen={false}>
          {al?.diagnosis?.map((d, i) => (
            <div key={i} className="p-2 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e]">
              {d.severity && (
                <span className={`text-[9px] font-bold mr-1 ${d.severity === 'HIGH' ? 'text-red-400' : d.severity === 'MEDIUM' ? 'text-yellow-400' : 'text-slate-500'}`}>
                  {d.severity}
                </span>
              )}
              {d.channel && <span className="text-[9px] text-slate-500 mr-1">{d.channel}</span>}
              {d.finding && <p className="text-xs text-slate-300">{d.finding}</p>}
              {d.detail && <p className="text-[10px] text-slate-500 mt-0.5">{d.detail}</p>}
            </div>
          ))}
          {c.hypotheses?.map((h, i) => (
            <div key={i} className="p-2 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e]">
              <span className="text-[9px] font-bold text-indigo-400 mr-1">{h.type}</span>
              <p className="text-xs text-slate-300">{h.statement}</p>
            </div>
          ))}
          {c.anomalies_detected?.map((a, i) => (
            <div key={i} className="p-2 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e]">
              {a.severity && <span className={`text-[9px] font-bold mr-1 ${a.severity === 'HIGH' ? 'text-red-400' : 'text-yellow-400'}`}>{a.severity}</span>}
              {a.metric && <span className="text-[9px] text-slate-500 mr-1">{a.metric}</span>}
              {a.description && <p className="text-xs text-slate-300">{a.description}</p>}
            </div>
          ))}
        </Section>
      )}

      {/* 7. Recommendations */}
      {hasRecs && (
        <Section title="Recomendações" icon={Lightbulb}>
          {al?.recommendations?.map((r, i) => (
            <div key={i} className="p-3 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e] space-y-1">
              <div className="flex items-start justify-between gap-2">
                <p className="text-xs text-slate-200 leading-snug">{r.action}</p>
                {r.priority && (
                  <span className={`shrink-0 text-[9px] font-bold px-1.5 py-0.5 rounded border ${
                    r.priority === 'P0' ? 'bg-red-500/15 text-red-400 border-red-500/25' :
                    r.priority === 'P1' ? 'bg-yellow-500/15 text-yellow-400 border-yellow-500/25' :
                    'bg-blue-500/15 text-blue-400 border-blue-500/25'
                  }`}>{r.priority}</span>
                )}
              </div>
              {r.expected_effect && <p className="text-[10px] text-slate-500">Efeito esperado: {r.expected_effect}</p>}
              {r.risk && <p className="text-[10px] text-red-400/70">Risco: {r.risk}</p>}
              {r.confidence && <p className="text-[10px] text-slate-600">Confiança: {r.confidence}</p>}
            </div>
          ))}
          {c.recommendations?.map((r, i) => (
            <div key={i} className="p-2 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e]">
              <span className="text-[9px] font-bold text-indigo-400 mr-1">{r.type}</span>
              <p className="text-xs text-slate-300">{r.statement}</p>
            </div>
          ))}
        </Section>
      )}

      {/* 8. Priorities Next Week */}
      {hasPriorities && (
        <Section title="Prioridades Semana Seguinte" icon={Target}>
          {al!.priorities_next_week!.map((p, i) => (
            <div key={i} className="p-3 bg-[#1a1f2e] rounded-lg border border-[#2a2f3e] space-y-1">
              <p className="text-xs text-slate-200">{p.action}</p>
              <div className="flex gap-4 text-[10px] text-slate-500">
                {p.owner && <span>Responsável: {p.owner}</span>}
                {p.deadline_suggestion && <span>Prazo: {p.deadline_suggestion}</span>}
              </div>
              {p.rationale && <p className="text-[10px] text-slate-600 italic">{p.rationale}</p>}
            </div>
          ))}
        </Section>
      )}

      {/* 9. Limitations */}
      {c.limitations && c.limitations.length > 0 && (
        <Section title="Limitações" icon={Info} defaultOpen={false}>
          {c.limitations.map((l, i) => (
            <p key={i} className="text-xs text-slate-500 font-mono leading-relaxed">· {l}</p>
          ))}
          {al?.analysis_notes && (
            <p className="text-xs text-slate-500 italic mt-2">{al.analysis_notes}</p>
          )}
        </Section>
      )}

      {/* Source health */}
      {report.source_state && report.source_state.length > 0 && (
        <Section title="Fontes de Dados" icon={CheckCircle} defaultOpen={false}>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {report.source_state.map(s => (
              <div key={s.source_system} className="flex items-center gap-1.5">
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                  s.source_state === 'OK' ? 'bg-emerald-400' :
                  s.source_state === 'DEGRADED' || s.source_state === 'STALE' ? 'bg-yellow-400' :
                  'bg-red-400'
                }`} />
                <span className="text-[10px] text-slate-400 capitalize">{s.source_system}</span>
                <span className="text-[9px] text-slate-600">{s.source_state}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

    </div>
  )
}

// ── Confirm Modal ────────────────────────────────────────────────────────────

function ConfirmModal({
  type, report, onConfirm, onCancel, loading, error,
}: {
  type: 'approve' | 'publish'
  report: Report
  onConfirm: () => void
  onCancel: () => void
  loading: boolean
  error: string | null
}) {
  const isApprove = type === 'approve'
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={!loading ? onCancel : undefined} />
      <div className="relative bg-[#1a1f2e] border border-[#2a2f3e] rounded-2xl p-6 w-full max-w-md shadow-2xl">
        <h3 className="text-base font-bold text-white mb-1">
          {isApprove ? 'Aprovar relatório' : 'Publicar relatório'}
        </h3>
        <p className="text-sm text-slate-400 mb-4">
          {isApprove
            ? 'Confirme a aprovação. Após aprovado, o relatório poderá ser publicado.'
            : 'Confirme a publicação. Versões anteriores publicadas serão substituídas.'}
        </p>
        <div className="bg-[#0f1117] rounded-xl border border-[#2a2f3e] px-4 py-3 mb-4 text-xs space-y-1.5 text-slate-400">
          <div className="flex justify-between">
            <span className="text-slate-600">Período</span>
            <span>{periodLabel(report)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-600">Tipo</span>
            <span className="capitalize">{report.report_type}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-600">Versão</span>
            <span>v{report.version}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-600">Engine</span>
            <span>{report.canonical_engine}</span>
          </div>
        </div>
        {error && (
          <div className="flex gap-2 p-3 bg-red-500/10 border border-red-500/25 rounded-xl mb-4">
            <AlertTriangle size={14} className="text-red-400 shrink-0 mt-0.5" />
            <p className="text-xs text-red-400">{error}</p>
          </div>
        )}
        <div className="flex gap-3">
          <button
            onClick={onCancel}
            disabled={loading}
            className="flex-1 px-4 py-2.5 rounded-xl border border-[#2a2f3e] text-sm text-slate-400 hover:text-white hover:border-slate-500 transition-colors disabled:opacity-40"
          >
            Cancelar
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            className={`flex-1 px-4 py-2.5 rounded-xl text-sm font-semibold transition-colors disabled:opacity-40 flex items-center justify-center gap-2 ${
              isApprove ? 'bg-blue-600 hover:bg-blue-500 text-white' : 'bg-emerald-600 hover:bg-emerald-500 text-white'
            }`}
          >
            {loading && <Loader2 size={14} className="animate-spin" />}
            {isApprove ? 'Confirmar aprovação' : 'Confirmar publicação'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Send Modal ───────────────────────────────────────────────────────────────

function SendModal({
  report, recipients, selectedRecipientId, step, error, settingsPath,
  onSelectRecipient, onPreview, onConfirm, onClose,
}: {
  report: Report
  recipients: Recipient[]
  selectedRecipientId: string | null
  step: string
  error?: string
  settingsPath: string
  onSelectRecipient: (id: string) => void
  onPreview: () => void
  onConfirm: () => void
  onClose: () => void
}) {
  const activeRecipients = recipients.filter(r => r.active)
  const selectedRecipient = activeRecipients.find(r => r.id === selectedRecipientId) ?? null
  const canSend = selectedRecipient != null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={step !== 'sending' ? onClose : undefined} />
      <div className="relative bg-[#1a1f2e] border border-[#2a2f3e] rounded-2xl p-6 w-full max-w-md shadow-2xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-bold text-white">Enviar relatório</h3>
          {step !== 'sending' && (
            <button onClick={onClose} className="text-slate-500 hover:text-white transition-colors">
              <X size={16} />
            </button>
          )}
        </div>

        {step === 'done' ? (
          <div className="text-center py-4 space-y-3">
            <CheckCircle size={32} className="text-emerald-400 mx-auto" />
            <p className="text-sm text-slate-300">Relatório enviado com sucesso.</p>
            <button onClick={onClose} className="px-6 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold rounded-xl transition-colors">
              Fechar
            </button>
          </div>
        ) : step === 'error' ? (
          <div className="space-y-4">
            <div className="flex gap-2 p-3 bg-red-500/10 border border-red-500/25 rounded-xl">
              <AlertTriangle size={14} className="text-red-400 shrink-0 mt-0.5" />
              <p className="text-xs text-red-400">{error || 'Erro desconhecido'}</p>
            </div>
            <button onClick={onClose} className="w-full px-4 py-2.5 rounded-xl border border-[#2a2f3e] text-sm text-slate-400 hover:text-white transition-colors">
              Fechar
            </button>
          </div>
        ) : (
          <>
            {/* Report summary */}
            <div className="bg-[#0f1117] rounded-xl border border-[#2a2f3e] px-4 py-3 mb-4 text-xs space-y-1.5 text-slate-400">
              <div className="flex justify-between">
                <span className="text-slate-600">Tipo</span>
                <span className="capitalize font-medium text-slate-300">{report.report_type}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-600">Período</span>
                <span>{periodLabel(report)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-600">Versão</span>
                <span>v{report.version}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-600">Status</span>
                <span className="text-emerald-400">Publicado</span>
              </div>
            </div>

            {/* Recipient selection */}
            <div className="mb-4">
              <p className="text-xs text-slate-400 mb-2">Destinatário <span className="text-slate-600">(uso interno da agência · canal: EMAIL)</span></p>
              {activeRecipients.length === 0 ? (
                <div className="flex gap-2 p-3 bg-yellow-500/10 border border-yellow-500/25 rounded-xl">
                  <AlertTriangle size={14} className="text-yellow-400 shrink-0 mt-0.5" />
                  <p className="text-xs text-yellow-400">
                    Nenhum destinatário ativo configurado.{' '}
                    <a href={settingsPath} className="underline hover:text-yellow-300">Configurar em Ajustes</a>
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  {activeRecipients.map(r => (
                    <button
                      key={r.id}
                      onClick={() => onSelectRecipient(r.id)}
                      disabled={step === 'sending'}
                      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl border text-left transition-colors disabled:opacity-50 ${
                        selectedRecipientId === r.id
                          ? 'border-indigo-500 bg-indigo-600/15'
                          : 'border-[#2a2f3e] hover:border-[#3a4050] bg-[#0f1117]'
                      }`}
                    >
                      <div className={`w-3.5 h-3.5 rounded-full border-2 flex-shrink-0 ${
                        selectedRecipientId === r.id ? 'border-indigo-400 bg-indigo-400' : 'border-slate-600'
                      }`} />
                      <div className="min-w-0">
                        <p className="text-xs font-medium text-slate-200 truncate">{r.name}</p>
                        <p className="text-[10px] text-slate-500 truncate">{r.email}</p>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Confirm step: show selection summary */}
            {step === 'confirm' && selectedRecipient && (
              <div className="bg-indigo-500/10 border border-indigo-500/25 rounded-xl px-3 py-2.5 mb-4 text-xs text-slate-300">
                <span className="text-slate-500">Enviando para: </span>
                <span className="font-medium">{selectedRecipient.name}</span>
                <span className="text-slate-500"> &lt;{selectedRecipient.email}&gt;</span>
              </div>
            )}

            {error && (
              <div className="flex gap-2 p-3 bg-red-500/10 border border-red-500/25 rounded-xl mb-4">
                <AlertTriangle size={14} className="text-red-400 shrink-0 mt-0.5" />
                <p className="text-xs text-red-400">{error}</p>
              </div>
            )}

            <div className="flex gap-3">
              <button onClick={onClose} disabled={step === 'sending'} className="flex-1 px-4 py-2.5 rounded-xl border border-[#2a2f3e] text-sm text-slate-400 hover:text-white transition-colors disabled:opacity-40">
                Cancelar
              </button>
              {step === 'preview' ? (
                <button onClick={onPreview} disabled={!canSend} className="flex-1 px-4 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-semibold transition-colors disabled:opacity-40">
                  Pré-visualizar envio
                </button>
              ) : (
                <button onClick={onConfirm} disabled={!canSend || step === 'sending'} className="flex-1 px-4 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-semibold transition-colors disabled:opacity-40 flex items-center justify-center gap-2">
                  {step === 'sending' && <Loader2 size={14} className="animate-spin" />}
                  Confirmar envio
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Delivery History Drawer ──────────────────────────────────────────────────

function HistoryDrawer({
  report, deliveries, loading, onClose,
}: {
  report: Report; deliveries: DeliveryReceipt[]; loading: boolean; onClose: () => void
}) {
  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative bg-[#1a1f2e] border-l border-[#2a2f3e] w-full max-w-sm h-full overflow-y-auto flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#2a2f3e]">
          <div>
            <p className="text-sm font-bold text-white">Histórico de Envios</p>
            <p className="text-[10px] text-slate-500">{periodLabel(report)} · v{report.version}</p>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white transition-colors">
            <X size={16} />
          </button>
        </div>
        <div className="flex-1 p-5">
          {loading ? (
            <div className="space-y-3">
              {[1, 2].map(i => <div key={i} className="h-16 bg-[#252a3a] rounded-xl animate-pulse" />)}
            </div>
          ) : deliveries.length === 0 ? (
            <div className="text-center py-8">
              <Clock size={24} className="text-slate-600 mx-auto mb-2" />
              <p className="text-sm text-slate-600">Nenhum envio registrado</p>
            </div>
          ) : (
            <ul className="space-y-3">
              {deliveries.map(d => {
                const meta = DELIVERY_META[d.status] || DELIVERY_META.NOT_SENT
                const { Icon } = meta
                return (
                  <li key={d.delivery_id} className="p-3 bg-[#0f1117] rounded-xl border border-[#2a2f3e]">
                    <div className="flex items-center gap-2 mb-1.5">
                      <Icon size={13} className={meta.cls} />
                      <span className={`text-xs font-medium ${meta.cls}`}>{meta.label}</span>
                      <span className="text-[10px] text-slate-600 ml-auto">tentativa {d.attempt}</span>
                    </div>
                    <div className="space-y-0.5 text-[10px] text-slate-500">
                      <p>Canal: {d.channel}</p>
                      {d.sent_at && <p>Enviado: {fmtDateTime(d.sent_at)}</p>}
                      {d.requested_at && <p>Solicitado: {fmtDateTime(d.requested_at)}</p>}
                      {d.requested_by && <p>Por: {d.requested_by}</p>}
                      {d.provider_message_id && (
                        <p className="font-mono text-slate-600 truncate">ID: {d.provider_message_id}</p>
                      )}
                      {d.error && <p className="text-red-400">Erro: {d.error}</p>}
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Report Card ──────────────────────────────────────────────────────────────

function ReportCard({
  report, deliveryStatus, downloading,
  onView, onApprove, onPublish, onSend, onHistory, onDownload,
}: {
  report: Report; deliveryStatus: string; downloading: boolean
  onView: () => void; onApprove: () => void; onPublish: () => void
  onSend: () => void; onHistory: () => void; onDownload: () => void
}) {
  const sm = STATUS_META[report.status] || STATUS_META.DRAFT
  const dm = DELIVERY_META[deliveryStatus] || DELIVERY_META.NOT_SENT
  const DeliveryIcon = dm.Icon

  const canApprove = report.status === 'READY_FOR_REVIEW'
  const canPublish = report.status === 'APPROVED'
  const canSend    = report.status === 'PUBLISHED'

  return (
    <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] p-4 hover:border-[#3a4050] transition-colors">
      {/* Header row */}
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <span className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider">
          {report.report_type === 'weekly' ? 'Semanal' : 'Mensal'}
        </span>
        <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${sm.cls}`}>
          {sm.label}
        </span>
        <span className="text-[10px] text-slate-600">v{report.version}</span>
        <span className="text-[9px] text-slate-700 font-mono">{report.canonical_engine}</span>
        <div className={`flex items-center gap-1 ml-auto ${dm.cls}`}>
          <DeliveryIcon size={11} />
          <span className="text-[10px] font-medium">{dm.label}</span>
        </div>
      </div>

      {/* Period */}
      <div className="flex items-center gap-1.5 mb-3">
        <Calendar size={12} className="text-slate-600 shrink-0" />
        <span className="text-sm font-medium text-slate-200">{periodLabel(report)}</span>
      </div>

      {/* Timestamps */}
      <div className="grid grid-cols-3 gap-2 mb-4 text-[10px]">
        <div>
          <p className="text-slate-600 mb-0.5">Gerado</p>
          <p className="text-slate-400">{fmtDateTime(report.generated_at)}</p>
        </div>
        {report.approved_at && (
          <div>
            <p className="text-slate-600 mb-0.5">Aprovado</p>
            <p className="text-slate-400">{fmtDateTime(report.approved_at)}</p>
          </div>
        )}
        {report.published_at && (
          <div>
            <p className="text-slate-600 mb-0.5">Publicado</p>
            <p className="text-slate-400">{fmtDateTime(report.published_at)}</p>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex flex-wrap gap-2">
        <button onClick={onView} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600/20 border border-indigo-600/30 text-indigo-400 text-xs font-medium hover:bg-indigo-600/30 transition-colors">
          <Eye size={12} />
          Ver
        </button>

        {canApprove && (
          <button onClick={onApprove} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600/20 border border-blue-600/30 text-blue-400 text-xs font-medium hover:bg-blue-600/30 transition-colors">
            <CheckCircle size={12} />
            Aprovar
          </button>
        )}

        {canPublish && (
          <button onClick={onPublish} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600/20 border border-emerald-600/30 text-emerald-400 text-xs font-medium hover:bg-emerald-600/30 transition-colors">
            <TrendingUp size={12} />
            Publicar
          </button>
        )}

        {canSend && (
          <button onClick={onSend} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-600/20 border border-violet-600/30 text-violet-400 text-xs font-medium hover:bg-violet-600/30 transition-colors">
            <Send size={12} />
            Enviar
          </button>
        )}

        <button
          onClick={onDownload}
          disabled={downloading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#2a2f3e] text-slate-400 text-xs font-medium hover:text-white hover:border-slate-400 transition-colors disabled:opacity-50"
        >
          {downloading ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
          Download
        </button>

        <button onClick={onHistory} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#2a2f3e] text-slate-500 text-xs font-medium hover:text-white hover:border-slate-500 transition-colors ml-auto">
          <History size={12} />
          Histórico
        </button>
      </div>
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function CanonicalReportsPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [reports,       setReports]       = useState<Report[]>([])
  const [loading,       setLoading]       = useState(true)
  const [error,         setError]         = useState<string | null>(null)
  const [filterType,    setFilterType]    = useState<'all' | 'weekly' | 'monthly'>('all')
  const [filterStatus,  setFilterStatus]  = useState<string>('all')

  const [viewerReport,  setViewerReport]  = useState<Report | null>(null)
  const [viewerLoading, setViewerLoading] = useState(false)

  const [confirm,         setConfirm]         = useState<{ type: 'approve' | 'publish'; report: Report } | null>(null)
  const [transitioning,   setTransitioning]   = useState(false)
  const [transitionError, setTransitionError] = useState<string | null>(null)

  const [historyReport,  setHistoryReport]  = useState<Report | null>(null)
  const [deliveries,     setDeliveries]     = useState<DeliveryReceipt[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [deliveryCache,  setDeliveryCache]  = useState<Record<string, string>>({})

  const [deliverySettings, setDeliverySettings] = useState<DeliverySettings | null>(null)
  const [downloadingId,    setDownloadingId]    = useState<string | null>(null)
  const [sendModal, setSendModal] = useState<{
    report: Report; selectedRecipientId: string | null; step: string; error?: string
  } | null>(null)

  const loadReports = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const qs = new URLSearchParams({ client_id: clientId, limit: '50' })
      if (filterType !== 'all') qs.set('report_type', filterType)
      if (filterStatus !== 'all') qs.set('status', filterStatus.toUpperCase())
      const res = await fetch(`/api/v1/canonical-reports/list?${qs}`)
      if (!res.ok) throw new Error(`Erro ${res.status}`)
      const data = await res.json()
      setReports(Array.isArray(data) ? data : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [clientId, filterType, filterStatus])

  useEffect(() => { loadReports() }, [loadReports])

  useEffect(() => {
    fetch(`/api/v1/canonical-reports/delivery-settings?client_id=${clientId}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) setDeliverySettings(data) })
      .catch(() => {})
  }, [clientId])

  const openViewer = useCallback((report: Report) => {
    if (report.content) { setViewerReport(report); return }
    setViewerReport(report)
    setViewerLoading(true)
    fetch(`/api/v1/canonical-reports/${report.report_id}/versions/${report.version}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) setViewerReport(data) })
      .finally(() => setViewerLoading(false))
  }, [])

  const doTransition = useCallback(async () => {
    if (!confirm) return
    setTransitioning(true)
    setTransitionError(null)
    try {
      const target = confirm.type === 'approve' ? 'APPROVED' : 'PUBLISHED'
      const res = await fetch(
        `/api/v1/canonical-reports/${confirm.report.report_id}/versions/${confirm.report.version}/transitions`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target, artifact_hash: confirm.report.artifact_hash }),
        }
      )
      const data = await res.json()
      if (!res.ok) { setTransitionError(data?.detail || `Erro ${res.status}`); return }
      const patch = (r: Report) =>
        r.report_id === confirm.report.report_id && r.version === confirm.report.version
          ? { ...r, ...data } : r
      setReports(prev => prev.map(patch))
      setViewerReport(prev => prev?.report_id === confirm.report.report_id ? patch(prev) : prev)
      setConfirm(null)
    } catch (e) {
      setTransitionError(e instanceof Error ? e.message : String(e))
    } finally {
      setTransitioning(false)
    }
  }, [confirm])

  const doSend = useCallback(async () => {
    if (!sendModal) return
    const activeRecipients = (deliverySettings?.recipients || []).filter(r => r.active)
    const recipient = activeRecipients.find(r => r.id === sendModal.selectedRecipientId)
    if (!recipient) return
    setSendModal(s => s ? { ...s, step: 'sending' } : null)
    try {
      const deliveryId    = crypto.randomUUID()
      const recipientHash = await sha256hex(recipient.email.trim().toLowerCase())

      const prepRes = await fetch('/api/v1/canonical-reports/delivery-receipts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          delivery_id: deliveryId,
          report_id: sendModal.report.report_id,
          report_version: sendModal.report.version,
          channel: 'EMAIL',
          recipient_hash: recipientHash,
        }),
      })
      if (!prepRes.ok) {
        const d = await prepRes.json()
        setSendModal(s => s ? { ...s, step: 'error', error: d?.detail || `Erro ${prepRes.status}` } : null)
        return
      }
      const sendRes = await fetch(`/api/v1/canonical-reports/delivery-receipts/${deliveryId}/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ recipient: recipient.email.trim() }),
      })
      if (!sendRes.ok) {
        const d = await sendRes.json()
        setSendModal(s => s ? { ...s, step: 'error', error: d?.detail || `Erro ${sendRes.status}` } : null)
        return
      }
      setDeliveryCache(c => ({ ...c, [sendModal.report.report_id]: 'DELIVERY_PENDING' }))
      setSendModal(s => s ? { ...s, step: 'done' } : null)
    } catch (e) {
      setSendModal(s => s ? { ...s, step: 'error', error: e instanceof Error ? e.message : String(e) } : null)
    }
  }, [sendModal, deliverySettings])

  const doDownload = useCallback(async (report: Report) => {
    setDownloadingId(report.report_id)
    try {
      const res = await fetch(`/api/v1/canonical-reports/${report.report_id}/artifact?version=${report.version}`)
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        alert(d?.detail || `Erro no download: ${res.status}`)
        return
      }
      const data = await res.json()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url  = URL.createObjectURL(blob)
      const a    = document.createElement('a')
      a.href     = url
      a.download = `${report.client_id}_${report.report_type}_${report.period_start}_${report.period_end}_v${report.version}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e) {
      console.error('Download failed:', e)
    } finally {
      setDownloadingId(null)
    }
  }, [])

  const openHistory = useCallback(async (report: Report) => {
    setHistoryReport(report)
    setHistoryLoading(true)
    setDeliveries([])
    try {
      const res = await fetch(`/api/v1/canonical-reports/${report.report_id}/deliveries`)
      if (res.ok) {
        const data: DeliveryReceipt[] = await res.json()
        const list = Array.isArray(data) ? data : []
        setDeliveries(list)
        if (list.length > 0) setDeliveryCache(c => ({ ...c, [report.report_id]: list[0].status }))
      }
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  const STATUS_OPTIONS = [
    { value: 'all',              label: 'Todos os status' },
    { value: 'DRAFT',            label: 'Rascunho' },
    { value: 'READY_FOR_REVIEW', label: 'Aguarda revisão' },
    { value: 'APPROVED',         label: 'Aprovado' },
    { value: 'PUBLISHED',        label: 'Publicado' },
    { value: 'SUPERSEDED',       label: 'Substituído' },
  ]

  return (
    <div className="min-h-screen bg-[#0f1117] text-white flex flex-col">

      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-6 py-5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <FileText size={18} className="text-indigo-400" />
          <div>
            <h1 className="text-base font-bold text-white">Relatórios</h1>
            <p className="text-xs text-slate-500">Relatórios canônicos — lifecycle completo</p>
          </div>
        </div>
        <button onClick={loadReports} disabled={loading} className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-white transition-colors">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          Atualizar
        </button>
      </div>

      {/* Filters */}
      <div className="border-b border-[#2a2f3e] px-6 py-3 flex items-center gap-3 flex-wrap">
        <div className="flex rounded-lg overflow-hidden border border-[#2a2f3e]">
          {(['all', 'weekly', 'monthly'] as const).map(t => (
            <button
              key={t}
              onClick={() => setFilterType(t)}
              className={`px-3 py-1.5 text-xs font-medium transition-colors ${
                filterType === t ? 'bg-indigo-600 text-white' : 'text-slate-500 hover:text-white hover:bg-[#1a1f2e]'
              }`}
            >
              {t === 'all' ? 'Todos' : t === 'weekly' ? 'Semanais' : 'Mensais'}
            </button>
          ))}
        </div>
        <select
          value={filterStatus}
          onChange={e => setFilterStatus(e.target.value)}
          className="text-xs bg-[#1a1f2e] border border-[#2a2f3e] text-slate-400 rounded-lg px-3 py-1.5 focus:outline-none focus:border-indigo-500"
        >
          {STATUS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        {!loading && (
          <span className="text-xs text-slate-600 ml-auto">
            {reports.length} relatório{reports.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Content — list + optional side viewer */}
      <div className="flex flex-1 overflow-hidden">

        <div className={`flex-1 overflow-y-auto p-6 ${viewerReport ? 'lg:max-w-[55%]' : ''}`}>
          {loading ? (
            <div className="space-y-4">
              {[1, 2, 3].map(i => <div key={i} className="h-40 bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] animate-pulse" />)}
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center py-16 space-y-4">
              <AlertTriangle size={32} className="text-red-400" />
              <div className="text-center">
                <p className="text-sm text-red-400 font-medium">{error}</p>
                <p className="text-xs text-slate-600 mt-1">Verifique a conexão com a API</p>
              </div>
              <button onClick={loadReports} className="flex items-center gap-1.5 px-4 py-2 bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl text-sm text-slate-400 hover:text-white transition-colors">
                <RefreshCw size={13} /> Tentar novamente
              </button>
            </div>
          ) : reports.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 space-y-3">
              <FileText size={32} className="text-slate-700" />
              <div className="text-center">
                <p className="text-sm text-slate-500 font-medium">Nenhum relatório encontrado</p>
                <p className="text-xs text-slate-600 mt-1">
                  {filterType !== 'all' || filterStatus !== 'all'
                    ? 'Tente ajustar os filtros'
                    : 'Os relatórios aparecem aqui após o scheduler executar (H7 segunda-feira, H8 primeiro do mês)'}
                </p>
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              {reports.map(r => (
                <ReportCard
                  key={`${r.report_id}-${r.version}`}
                  report={r}
                  deliveryStatus={deliveryCache[r.report_id] || 'NOT_SENT'}
                  downloading={downloadingId === r.report_id}
                  onView={() => openViewer(r)}
                  onApprove={() => { setConfirm({ type: 'approve', report: r }); setTransitionError(null) }}
                  onPublish={() => { setConfirm({ type: 'publish', report: r }); setTransitionError(null) }}
                  onSend={() => setSendModal({ report: r, selectedRecipientId: null, step: 'preview' })}
                  onHistory={() => openHistory(r)}
                  onDownload={() => doDownload(r)}
                />
              ))}
            </div>
          )}
        </div>

        {/* Side viewer panel (desktop) */}
        {viewerReport && (
          <div className="hidden lg:flex flex-col border-l border-[#2a2f3e] overflow-hidden" style={{ width: '45%', minWidth: 380, maxWidth: 600 }}>
            <div className="flex items-center justify-between px-4 py-3 border-b border-[#2a2f3e] bg-[#0f1117]">
              <div className="flex items-center gap-2 min-w-0">
                <Eye size={14} className="text-indigo-400 shrink-0" />
                <span className="text-sm font-medium text-white truncate">
                  {viewerReport.report_type === 'weekly' ? 'Semanal' : 'Mensal'} — {periodLabel(viewerReport)}
                </span>
                {viewerLoading && <Loader2 size={12} className="animate-spin text-slate-500 shrink-0" />}
              </div>
              <button onClick={() => setViewerReport(null)} className="text-slate-500 hover:text-white transition-colors shrink-0 ml-2">
                <X size={14} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">
              <ReportViewer report={viewerReport} loading={viewerLoading} />
            </div>
          </div>
        )}
      </div>

      {/* Mobile viewer overlay */}
      {viewerReport && (
        <div className="lg:hidden fixed inset-0 z-30 bg-[#0f1117] flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-[#2a2f3e]">
            <div className="flex items-center gap-2 min-w-0">
              <Eye size={14} className="text-indigo-400 shrink-0" />
              <span className="text-sm font-medium text-white truncate">
                {viewerReport.report_type === 'weekly' ? 'Semanal' : 'Mensal'} — {periodLabel(viewerReport)}
              </span>
              {viewerLoading && <Loader2 size={12} className="animate-spin text-slate-500 shrink-0" />}
            </div>
            <button onClick={() => setViewerReport(null)} className="text-slate-500 hover:text-white transition-colors shrink-0">
              <X size={14} />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto">
            <ReportViewer report={viewerReport} loading={viewerLoading} />
          </div>
        </div>
      )}

      {/* Confirm modal */}
      {confirm && (
        <ConfirmModal
          type={confirm.type} report={confirm.report}
          onConfirm={doTransition} onCancel={() => setConfirm(null)}
          loading={transitioning} error={transitionError}
        />
      )}

      {/* Send modal */}
      {sendModal && (
        <SendModal
          report={sendModal.report}
          recipients={deliverySettings?.recipients || []}
          selectedRecipientId={sendModal.selectedRecipientId}
          step={sendModal.step}
          error={sendModal.error}
          settingsPath={`/clients/${clientId}/settings`}
          onSelectRecipient={id => setSendModal(s => s ? { ...s, selectedRecipientId: id } : null)}
          onPreview={() => setSendModal(s => s ? { ...s, step: 'confirm' } : null)}
          onConfirm={doSend}
          onClose={() => setSendModal(null)}
        />
      )}

      {/* History drawer */}
      {historyReport && (
        <HistoryDrawer
          report={historyReport} deliveries={deliveries}
          loading={historyLoading} onClose={() => setHistoryReport(null)}
        />
      )}
    </div>
  )
}
