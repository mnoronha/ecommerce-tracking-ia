'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { Bell, AlertTriangle, AlertCircle, Info, CheckCircle, RefreshCw, Loader2, Settings2, ChevronDown, ChevronUp, X } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

type Alert = {
  id: string
  severity: 'critical' | 'warning' | 'info'
  title: string
  message: string
  data: Record<string, unknown>
  created_at: string
  resolved_at: string | null
  alert_rule_id: string | null
}

type Rule = {
  id: string
  name: string
  rule_key: string
  severity: 'critical' | 'warning' | 'info'
  enabled: boolean
  throttle_minutes: number
  config: Record<string, unknown>
  client_id: string | null
}

const SEV_STYLE: Record<string, string> = {
  critical: 'border-red-500/30 bg-red-500/5',
  warning:  'border-yellow-500/30 bg-yellow-500/5',
  info:     'border-indigo-500/30 bg-indigo-500/5',
}

const SEV_BADGE: Record<string, string> = {
  critical: 'bg-red-500/20 text-red-400',
  warning:  'bg-yellow-500/20 text-yellow-400',
  info:     'bg-indigo-500/20 text-indigo-400',
}

const SEV_ICON: Record<string, React.ElementType> = {
  critical: AlertCircle,
  warning:  AlertTriangle,
  info:     Info,
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

export default function AlertasPage() {
  const params  = useParams()
  const pixelId = params.clientId as string

  const [alerts,          setAlerts]          = useState<Alert[]>([])
  const [rules,           setRules]           = useState<Rule[]>([])
  const [loading,         setLoading]         = useState(true)
  const [includeResolved, setIncludeResolved] = useState(false)
  const [running,         setRunning]         = useState(false)
  const [runResult,       setRunResult]       = useState<{ rules: number; new: number; resolved: number } | null>(null)
  const [error,           setError]           = useState<string | null>(null)
  const [showRules,       setShowRules]       = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const url = `${API_URL}/alerts/${pixelId}?include_resolved=${includeResolved}&limit=100`
      const res = await fetch(url)
      if (!res.ok) {
        const txt = await res.text()
        setError(`Erro ${res.status}: ${txt.slice(0, 120)}`)
        setAlerts([])
      } else {
        const data = await res.json()
        setAlerts(data.alerts || [])
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erro ao carregar alertas')
    }
    setLoading(false)
  }, [pixelId, includeResolved])

  const loadRules = useCallback(async () => {
    try {
      const res  = await fetch(`${API_URL}/alerts/rules/${pixelId}`)
      if (res.ok) setRules((await res.json()).rules || [])
    } catch (_) {}
  }, [pixelId])

  useEffect(() => { load() }, [load])
  useEffect(() => { loadRules() }, [loadRules])

  async function runEngine() {
    setRunning(true)
    setRunResult(null)
    try {
      const res = await fetch(`${API_URL}/alerts/run`, { method: 'POST' })
      if (res.ok) setRunResult(await res.json())
    } catch (_) {}
    setRunning(false)
    load()
  }

  async function resolveAlert(alertId: string) {
    try {
      await fetch(`${API_URL}/alerts/${alertId}/resolve`, { method: 'POST' })
      load()
    } catch (_) {}
  }

  async function toggleRule(ruleId: string, enabled: boolean) {
    try {
      await fetch(`${API_URL}/alerts/rules/${ruleId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
      setRules(prev => prev.map(r => r.id === ruleId ? { ...r, enabled } : r))
    } catch (_) {}
  }

  const open     = alerts.filter(a => !a.resolved_at)
  const resolved = alerts.filter(a => a.resolved_at)
  const critical = open.filter(a => a.severity === 'critical').length

  return (
    <div className="p-6 space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold text-white">Alertas</h1>
            {critical > 0 && (
              <span className="bg-red-500 text-white text-xs font-bold px-2 py-0.5 rounded-full">
                {critical} crítico{critical > 1 ? 's' : ''}
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-0.5">Monitoramento automático a cada 30 min</p>
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
            onClick={load}
            className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white transition-colors"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            Atualizar
          </button>
          <button
            onClick={runEngine}
            disabled={running}
            className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors"
          >
            {running ? <Loader2 size={12} className="animate-spin" /> : <Bell size={12} />}
            Rodar engine
          </button>
        </div>
      </div>

      {/* Run result flash */}
      {runResult && (
        <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs rounded-lg px-4 py-2.5">
          <CheckCircle size={13} />
          Engine executada — {runResult.rules} regras · {runResult.new} novo{runResult.new !== 1 ? 's' : ''} · {runResult.resolved} resolvido{runResult.resolved !== 1 ? 's' : ''}
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
          {/* Open alerts */}
          {open.length === 0 ? (
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-10 text-center">
              <CheckCircle size={32} className="text-emerald-500/40 mx-auto mb-3" />
              <p className="text-slate-300 font-medium text-sm">Nenhum alerta aberto</p>
              <p className="text-slate-600 text-xs mt-1">Tudo parece saudável por agora</p>
            </div>
          ) : (
            <div className="space-y-3">
              {open.map(a => (
                <AlertCard key={a.id} alert={a} onResolve={() => resolveAlert(a.id)} />
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
                {resolved.map(a => <AlertCard key={a.id} alert={a} />)}
              </div>
            </div>
          )}

          {/* Rules panel */}
          {rules.length > 0 && (
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
              <button
                onClick={() => setShowRules(v => !v)}
                className="w-full flex items-center justify-between px-5 py-3.5 text-left hover:bg-white/[0.02] transition-colors"
              >
                <div className="flex items-center gap-2">
                  <Settings2 size={14} className="text-slate-400" />
                  <span className="text-sm font-medium text-slate-300">Regras de alerta</span>
                  <span className="text-xs text-slate-600">({rules.filter(r => r.enabled).length}/{rules.length} ativas)</span>
                </div>
                {showRules ? <ChevronUp size={14} className="text-slate-500" /> : <ChevronDown size={14} className="text-slate-500" />}
              </button>
              {showRules && (
                <div className="divide-y divide-[#2a2f3e] border-t border-[#2a2f3e]">
                  {rules.map(rule => (
                    <RuleRow key={rule.id} rule={rule} onToggle={enabled => toggleRule(rule.id, enabled)} />
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function AlertCard({ alert: a, onResolve }: { alert: Alert; onResolve?: () => void }) {
  const [expanded,  setExpanded]  = useState(false)
  const [resolving, setResolving] = useState(false)
  const Icon = SEV_ICON[a.severity] || Info

  async function handleResolve() {
    if (!onResolve) return
    setResolving(true)
    await onResolve()
    setResolving(false)
  }

  return (
    <div className={`rounded-xl border p-4 ${SEV_STYLE[a.severity] || SEV_STYLE.info}`}>
      <div className="flex items-start gap-3">
        <Icon size={15} className={a.severity === 'critical' ? 'text-red-400' : a.severity === 'warning' ? 'text-yellow-400' : 'text-indigo-400'} />
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2 flex-wrap">
            <p className="text-sm font-semibold text-white">{a.title}</p>
            <div className="flex items-center gap-2 shrink-0">
              <span className={`text-xs px-2 py-0.5 rounded font-medium ${SEV_BADGE[a.severity] || SEV_BADGE.info}`}>
                {a.severity === 'critical' ? 'Crítico' : a.severity === 'warning' ? 'Atenção' : 'Info'}
              </span>
              {a.resolved_at ? (
                <span className="text-xs px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400">Resolvido</span>
              ) : onResolve && (
                <button
                  onClick={handleResolve}
                  disabled={resolving}
                  className="flex items-center gap-1 text-xs px-2 py-0.5 rounded bg-slate-500/15 text-slate-400 hover:bg-emerald-500/15 hover:text-emerald-400 transition-colors disabled:opacity-50"
                  title="Marcar como resolvido"
                >
                  {resolving ? <Loader2 size={10} className="animate-spin" /> : <X size={10} />}
                  Resolver
                </button>
              )}
            </div>
          </div>

          <p className="text-xs text-slate-400 mt-1 leading-relaxed">{a.message}</p>

          {expanded && a.data && Object.keys(a.data).length > 0 && (
            <div className="mt-3 bg-[#0f1117] rounded-lg p-3 border border-[#2a2f3e]">
              <p className="text-xs font-medium text-slate-400 mb-2">Dados</p>
              <pre className="text-xs text-slate-500 whitespace-pre-wrap">{JSON.stringify(a.data, null, 2)}</pre>
            </div>
          )}

          <div className="flex items-center gap-3 mt-2">
            <span className="text-xs text-slate-600">{fmtDate(a.created_at)}</span>
            {a.resolved_at && (
              <span className="text-xs text-slate-600">→ resolvido {fmtDate(a.resolved_at)}</span>
            )}
            {a.data && Object.keys(a.data).length > 0 && (
              <button
                onClick={() => setExpanded(v => !v)}
                className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
              >
                {expanded ? 'Ocultar dados' : 'Ver dados'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

const RULE_LABEL: Record<string, string> = {
  meta_token_expiring:   'Token Meta expirando',
  integration_unhealthy: 'Integração com falha',
  roas_below_goal:       'ROAS abaixo da meta',
  budget_overspent:      'Orçamento estourado',
  tracking_stopped:      'Tracking parado',
  cpa_over_target:       'CPA acima da meta',
}

function RuleRow({ rule, onToggle }: { rule: Rule; onToggle: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between px-5 py-3 gap-4">
      <div className="flex-1 min-w-0">
        <p className="text-sm text-slate-300">{RULE_LABEL[rule.rule_key] || rule.name}</p>
        <p className="text-xs text-slate-600 mt-0.5">
          {rule.severity === 'critical' ? 'Crítico' : rule.severity === 'warning' ? 'Atenção' : 'Info'}
          {rule.throttle_minutes > 0 && ` · throttle ${rule.throttle_minutes}min`}
          {rule.client_id ? ' · específica deste cliente' : ' · para todos os clientes'}
        </p>
      </div>
      <button
        onClick={() => onToggle(!rule.enabled)}
        className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${
          rule.enabled ? 'bg-indigo-600' : 'bg-slate-700'
        }`}
      >
        <span
          className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${
            rule.enabled ? 'translate-x-4' : 'translate-x-1'
          }`}
        />
      </button>
    </div>
  )
}
