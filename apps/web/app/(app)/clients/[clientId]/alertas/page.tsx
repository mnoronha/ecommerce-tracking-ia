'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import {
  Bell, AlertTriangle, AlertCircle, Info, CheckCircle,
  RefreshCw, Loader2, ChevronDown, ChevronUp, Clock, ExternalLink,
} from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────────────────

type Severity = 'critical' | 'warning' | 'info'

type CoreAlert = {
  id: string
  alert_type: string
  severity: string  // CRITICAL | HIGH | MEDIUM | LOW
  status: string    // OPEN | ACKNOWLEDGED | RESOLVED
  platform?: string | null
  title: string
  message: string
  dedup_key: string
  evidence: Record<string, unknown>
  occurrence_count: number
  detected_at: string
  resolved_at?: string | null
}

type Diagnosis = {
  id: string
  alert_id: string
  status: string
  summary: string
  root_cause_hypotheses: Array<{ hypothesis: string; confidence?: string | number }>
  evidence: Record<string, unknown>
  confidence?: string | null  // Level: "LOW" | "MEDIUM" | "HIGH"
  limitations?: string | null
  created_at: string
  created_by: string
}

// ── Visual constants ──────────────────────────────────────────────────────────

const SEV_STYLE: Record<Severity, string> = {
  critical: 'border-red-500/30 bg-red-500/5',
  warning:  'border-yellow-500/30 bg-yellow-500/5',
  info:     'border-indigo-500/30 bg-indigo-500/5',
}

const SEV_BADGE: Record<Severity, string> = {
  critical: 'bg-red-500/20 text-red-400',
  warning:  'bg-yellow-500/20 text-yellow-400',
  info:     'bg-indigo-500/20 text-indigo-400',
}

const SEV_ICON: Record<Severity, React.ElementType> = {
  critical: AlertCircle,
  warning:  AlertTriangle,
  info:     Info,
}

const SEV_LABEL: Record<Severity, string> = {
  critical: 'Crítico',
  warning:  'Atenção',
  info:     'Info',
}

const SEV_COUNT_STYLE: Record<Severity, string> = {
  critical: 'border-red-500/20 bg-red-500/5',
  warning:  'border-yellow-500/20 bg-yellow-500/5',
  info:     'border-indigo-500/20 bg-indigo-500/5',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function mapSeverity(s: string): Severity {
  const u = (s ?? '').toUpperCase()
  if (u === 'CRITICAL' || u === 'HIGH') return 'critical'
  if (u === 'MEDIUM') return 'warning'
  return 'info'
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

function fmtRelative(iso: string) {
  const diff = Date.now() - new Date(iso).getTime()
  const min  = Math.floor(diff / 60_000)
  if (min < 2)   return 'agora'
  if (min < 60)  return `há ${min}min`
  const h = Math.floor(min / 60)
  if (h < 24)    return `há ${h}h`
  const d = Math.floor(h / 24)
  if (d < 7)     return `há ${d}d`
  return fmtDate(iso)
}

// ── Balance alert data renderer ───────────────────────────────────────────────

const _PLATFORM_LABEL: Record<string, string> = { google: 'Google Ads', meta: 'Meta Ads' }

function BalanceAlertView({ data }: { data: Record<string, unknown> }) {
  const balance  = data.balance  as number | null | undefined
  const currency = (data.currency as string | undefined) ?? ''
  const days     = data.estimated_days_remaining as number | null | undefined
  const avg      = data.avg_daily_spend as number | null | undefined
  const window_  = data.spend_avg_window_days as number | undefined
  const platform = (data.platform as string | undefined) ?? ''
  const snapshot = data.snapshot_at as string | undefined

  const fmtBrl = (v: number) =>
    v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  const daysColor =
    days == null ? 'text-slate-400'
    : days <= 1  ? 'text-red-400 font-semibold'
    : days <= 3  ? 'text-yellow-400'
    :              'text-slate-300'

  return (
    <div className="mt-2 bg-[#0f1117] rounded-lg px-3 py-2.5 border border-[#2a2f3e] text-xs space-y-1.5">
      <div className="flex flex-wrap gap-x-5 gap-y-1 items-baseline">
        {balance != null && (
          <span>
            <span className="text-slate-500">Saldo </span>
            <span className="text-white font-medium">R$ {fmtBrl(balance)} {currency}</span>
          </span>
        )}
        {avg != null && (
          <span className="text-slate-400">
            <span className="text-slate-500">Gasto médio </span>
            R$ {fmtBrl(avg)}/dia{window_ ? ` (${window_}d)` : ''}
          </span>
        )}
        {days != null && (
          <span className={daysColor}>
            <span className="text-slate-500">Autonomia </span>
            {days.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} dia(s)
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-x-4 text-slate-600">
        {platform && <span>{_PLATFORM_LABEL[platform] ?? platform}</span>}
        {snapshot && (
          <span>Leitura: {new Date(snapshot).toLocaleString('pt-BR', {
            day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
          })}</span>
        )}
      </div>
    </div>
  )
}

// ── Alert data pretty-printer ─────────────────────────────────────────────────

function AlertDataView({ data }: { data: Record<string, unknown> }) {
  const keys = Object.keys(data).filter(k => k !== 'check')
  if (keys.length === 0) return null

  if (typeof data.balance_status === 'string') return <BalanceAlertView data={data} />

  const finding = data.finding as Record<string, unknown> | undefined
  const message = finding?.message as string | undefined
  if (message) {
    return (
      <div className="mt-2 bg-[#0f1117] rounded-lg px-3 py-2 border border-[#2a2f3e] text-xs text-slate-400">
        {message}
      </div>
    )
  }

  const simple = keys.filter(k => typeof data[k] !== 'object' || data[k] === null)
  if (simple.length === 0) return null
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
      {simple.map(k => (
        <span key={k} className="text-xs text-slate-500">
          <span className="text-slate-600">{k}:</span>{' '}
          <span className="text-slate-400">{String(data[k])}</span>
        </span>
      ))}
    </div>
  )
}

// ── DiagnosisPanel ────────────────────────────────────────────────────────────

function DiagnosisPanel({ alertId, clientId }: { alertId: string; clientId: string }) {
  const [diags,   setDiags]   = useState<Diagnosis[] | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`/api/v1/alerts/${alertId}/diagnoses`)
      .then(r => r.ok ? r.json() : [])
      .then(data => setDiags(Array.isArray(data) ? data : []))
      .catch(() => setDiags([]))
      .finally(() => setLoading(false))
  }, [alertId])

  if (loading) {
    return (
      <div className="mt-2 flex items-center gap-1.5 text-xs text-slate-600">
        <Loader2 size={11} className="animate-spin" /> Carregando diagnósticos…
      </div>
    )
  }

  if (!diags || diags.length === 0) {
    return (
      <p className="mt-2 text-xs text-slate-600 italic">Nenhum diagnóstico registrado para este alerta.</p>
    )
  }

  return (
    <div className="mt-3 space-y-2">
      {diags.map(d => (
        <div key={d.id} className="bg-[#0f1117] rounded-lg border border-[#2a2f3e] px-3 py-2.5">
          <div className="flex items-start justify-between gap-2">
            <p className="text-xs font-semibold text-slate-200 leading-snug">{d.summary}</p>
            {d.confidence != null && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/15 text-indigo-300 shrink-0">
                {d.confidence}
              </span>
            )}
          </div>

          {d.root_cause_hypotheses?.length > 0 && (
            <div className="mt-1.5 space-y-0.5">
              {d.root_cause_hypotheses.slice(0, 2).map((h, i) => (
                <p key={i} className="text-xs text-slate-500 leading-snug">• {h.hypothesis}</p>
              ))}
            </div>
          )}

          {d.limitations && (
            <p className="mt-1 text-xs text-slate-600 italic">{d.limitations}</p>
          )}

          <div className="flex items-center gap-3 mt-2 pt-2 border-t border-[#2a2f3e]">
            <Link
              href={`/clients/${clientId}/recommendations`}
              className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
            >
              Ver recomendações <ExternalLink size={10} />
            </Link>
            <span className="text-xs text-slate-700">por {d.created_by} · {fmtRelative(d.created_at)}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── AlertCard ─────────────────────────────────────────────────────────────────

function AlertCard({ alert: a, clientId }: { alert: CoreAlert; clientId: string }) {
  const [showDiags, setShowDiags] = useState(false)
  const sev  = mapSeverity(a.severity)
  const Icon = SEV_ICON[sev]

  const isResolved = a.status === 'RESOLVED'

  return (
    <div className={`rounded-xl border p-4 ${SEV_STYLE[sev]}`}>
      <div className="flex items-start gap-3">
        <Icon size={15} className={`mt-0.5 shrink-0 ${
          sev === 'critical' ? 'text-red-400' :
          sev === 'warning'  ? 'text-yellow-400' :
                               'text-indigo-400'
        }`} />
        <div className="flex-1 min-w-0">

          {/* Title row */}
          <div className="flex items-start justify-between gap-2 flex-wrap">
            <p className="text-sm font-semibold text-white leading-tight">{a.title}</p>
            <div className="flex items-center gap-2 shrink-0">
              <span className={`text-xs px-2 py-0.5 rounded font-medium ${SEV_BADGE[sev]}`}>
                {SEV_LABEL[sev]}
              </span>
              {isResolved ? (
                <span className="text-xs px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400">Resolvido</span>
              ) : a.status === 'ACKNOWLEDGED' ? (
                <span className="text-xs px-2 py-0.5 rounded bg-slate-500/15 text-slate-400">Reconhecido</span>
              ) : null}
            </div>
          </div>

          {/* Message */}
          <p className="text-xs text-slate-400 mt-1 leading-relaxed">{a.message}</p>

          {/* Evidence */}
          {a.evidence && Object.keys(a.evidence).length > 0 && (
            <AlertDataView data={a.evidence} />
          )}

          {/* Footer */}
          <div className="flex items-center flex-wrap gap-3 mt-2.5">
            <span className="flex items-center gap-1 text-xs text-slate-600">
              <Clock size={10} />
              {fmtRelative(a.detected_at)}
              <span className="text-slate-700 ml-1">{fmtDate(a.detected_at)}</span>
            </span>
            {a.platform && (
              <span className="text-xs text-slate-600">{_PLATFORM_LABEL[a.platform] ?? a.platform}</span>
            )}
            {a.occurrence_count > 1 && (
              <span className="text-xs text-slate-600">{a.occurrence_count}× ocorrências</span>
            )}
            {isResolved && a.resolved_at && (
              <span className="text-xs text-slate-600">→ resolvido {fmtRelative(a.resolved_at)}</span>
            )}
          </div>

          {/* Diagnoses toggle */}
          <button
            onClick={() => setShowDiags(v => !v)}
            className="mt-2.5 flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors"
          >
            {showDiags
              ? <><ChevronUp size={11} /> Ocultar diagnósticos</>
              : <><ChevronDown size={11} /> Ver diagnósticos</>
            }
          </button>

          {showDiags && <DiagnosisPanel alertId={a.id} clientId={clientId} />}
        </div>
      </div>
    </div>
  )
}

// ── SummaryCard ───────────────────────────────────────────────────────────────

function SummaryCard({ severity, count }: { severity: Severity; count: number }) {
  const Icon = SEV_ICON[severity]
  return (
    <div className={`flex items-center gap-3 rounded-xl border px-4 py-3 ${SEV_COUNT_STYLE[severity]}`}>
      <Icon size={16} className={
        severity === 'critical' ? 'text-red-400' :
        severity === 'warning'  ? 'text-yellow-400' : 'text-indigo-400'
      } />
      <div>
        <p className="text-lg font-bold text-white leading-none">{count}</p>
        <p className="text-xs text-slate-500 mt-0.5">{SEV_LABEL[severity]}</p>
      </div>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

type Filter = 'all' | Severity

const AUTO_REFRESH_MS = 5 * 60 * 1000

export default function AlertasPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [alerts,          setAlerts]          = useState<CoreAlert[]>([])
  const [loading,         setLoading]         = useState(true)
  const [includeResolved, setIncludeResolved] = useState(false)
  const [error,           setError]           = useState<string | null>(null)
  const [filter,          setFilter]          = useState<Filter>('all')
  const [lastRefresh,     setLastRefresh]     = useState<Date>(new Date())
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    setError(null)
    try {
      const status = includeResolved ? 'all' : 'OPEN'
      const res = await fetch(`/api/v1/clients/${clientId}/alerts?status=${status}&limit=200`)
      if (!res.ok) {
        setError(`Erro ${res.status}: ${(await res.text()).slice(0, 120)}`)
        setAlerts([])
      } else {
        setAlerts((await res.json()) as CoreAlert[])
        setLastRefresh(new Date())
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erro ao carregar alertas')
    }
    if (!silent) setLoading(false)
  }, [clientId, includeResolved])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    timerRef.current = setTimeout(() => load(true), AUTO_REFRESH_MS)
    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [lastRefresh, load])

  // Derived state
  const open     = alerts.filter(a => a.status !== 'RESOLVED')
  const resolved = alerts.filter(a => a.status === 'RESOLVED')

  const counts = {
    critical: open.filter(a => mapSeverity(a.severity) === 'critical').length,
    warning:  open.filter(a => mapSeverity(a.severity) === 'warning').length,
    info:     open.filter(a => mapSeverity(a.severity) === 'info').length,
  }

  const filtered = filter === 'all'
    ? open
    : open.filter(a => mapSeverity(a.severity) === filter)

  const FILTER_TABS: { key: Filter; label: string; count?: number }[] = [
    { key: 'all',      label: 'Todos',   count: open.length },
    { key: 'critical', label: 'Crítico', count: counts.critical },
    { key: 'warning',  label: 'Atenção', count: counts.warning },
    { key: 'info',     label: 'Info',    count: counts.info },
  ]

  return (
    <div className="p-6 space-y-5">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold text-white">Alertas</h1>
            {counts.critical > 0 && (
              <span className="bg-red-500 text-white text-xs font-bold px-2 py-0.5 rounded-full animate-pulse">
                {counts.critical} crítico{counts.critical > 1 ? 's' : ''}
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-0.5">
            Alertas do sistema Core · verificação automática
            <span className="ml-2 text-slate-700">· atualizado {fmtRelative(lastRefresh.toISOString())}</span>
          </p>
        </div>

        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={includeResolved}
              onChange={e => setIncludeResolved(e.target.checked)}
              className="accent-indigo-500 w-3.5 h-3.5"
            />
            Ver resolvidos
          </label>
          <button
            onClick={() => load()}
            className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white transition-colors"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            Atualizar
          </button>
          <Link
            href={`/clients/${clientId}/recommendations`}
            className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors"
          >
            <Bell size={12} />
            Recomendações
          </Link>
        </div>
      </div>

      {/* Summary cards */}
      {!loading && (
        <div className="grid grid-cols-3 gap-3">
          <SummaryCard severity="critical" count={counts.critical} />
          <SummaryCard severity="warning"  count={counts.warning} />
          <SummaryCard severity="info"     count={counts.info} />
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/20 text-red-400 text-xs rounded-lg px-4 py-2.5">
          <AlertCircle size={13} /> {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-40">
          <Loader2 size={20} className="animate-spin text-slate-500" />
        </div>
      ) : (
        <>
          {/* Severity filter tabs */}
          {open.length > 0 && (
            <div className="flex items-center gap-1 border-b border-[#2a2f3e] pb-0">
              {FILTER_TABS.map(tab => (
                <button
                  key={tab.key}
                  onClick={() => setFilter(tab.key)}
                  className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 transition-colors -mb-px ${
                    filter === tab.key
                      ? 'border-indigo-500 text-indigo-400'
                      : 'border-transparent text-slate-500 hover:text-slate-300'
                  }`}
                >
                  {tab.label}
                  {tab.count !== undefined && tab.count > 0 && (
                    <span className={`px-1.5 py-0.5 rounded text-xs font-bold ${
                      tab.key === 'critical' ? 'bg-red-500/20 text-red-400' :
                      tab.key === 'warning'  ? 'bg-yellow-500/20 text-yellow-400' :
                      tab.key === 'info'     ? 'bg-indigo-500/20 text-indigo-400' :
                                               'bg-slate-700 text-slate-400'
                    }`}>
                      {tab.count}
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}

          {/* Open alerts */}
          {filtered.length === 0 && open.length === 0 ? (
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-10 text-center">
              <CheckCircle size={32} className="text-emerald-500/40 mx-auto mb-3" />
              <p className="text-slate-300 font-medium text-sm">Nenhum alerta aberto</p>
              <p className="text-slate-600 text-xs mt-1">Tudo parece saudável por agora</p>
            </div>
          ) : filtered.length === 0 ? (
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-8 text-center">
              <p className="text-slate-500 text-sm">Nenhum alerta com severidade "{SEV_LABEL[filter as Severity]}"</p>
            </div>
          ) : (
            <div className="space-y-3">
              {filtered.map(a => (
                <AlertCard key={a.id} alert={a} clientId={clientId} />
              ))}
            </div>
          )}

          {/* Resolved alerts */}
          {includeResolved && resolved.length > 0 && (
            <div>
              <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">
                Resolvidos ({resolved.length})
              </h2>
              <div className="space-y-2 opacity-60">
                {resolved.map(a => <AlertCard key={a.id} alert={a} clientId={clientId} />)}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
