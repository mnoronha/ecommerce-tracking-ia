'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams } from 'next/navigation'
import {
  Bell, AlertTriangle, AlertCircle, Info, CheckCircle,
  RefreshCw, Loader2, Settings2, ChevronDown, ChevronUp, X, Clock,
} from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ─────────────────────────────────────────────────────────────────────

type Severity = 'critical' | 'warning' | 'info'

type Alert = {
  id: string
  severity: Severity
  title: string
  message: string
  data: Record<string, unknown>
  created_at: string
  resolved_at: string | null
  alert_rule_id: string | null
}

type Rule = {
  rule_key: string
  name: string
  severity: Severity
  enabled: boolean
  global_enabled: boolean
  overridden: boolean
  throttle_minutes: number
  config: Record<string, unknown>
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

const RULE_LABEL: Record<string, string> = {
  // alert_engine rules
  meta_token_expiring:    'Token Meta expirando',
  integration_unhealthy:  'Integração com falha',
  roas_below_goal:        'ROAS abaixo da meta',
  budget_overspent:       'Orçamento estourado',
  tracking_stopped:       'Tracking parado',
  cpa_over_target:        'CPA acima da meta',
  revenue_drop:           'Queda de receita',
  // health_monitor checks
  snippet_volume:         'Volume do snippet',
  fbp_coverage:           'Cobertura fbp',
  meta_dispatch:          'Dispatch Meta CAPI',
  google_dispatch:        'Dispatch Google Ads',
  pos_filter:             'Filtro POS offline',
}

// O que dispara cada alerta + a proteção contra falso positivo.
const RULE_DESC: Record<string, string> = {
  meta_token_expiring:    'Token da Meta perto de expirar (precisa renovar).',
  integration_unhealthy:  'Integração Meta/Google/Shopify com falha de conexão.',
  roas_below_goal:        'ROAS do mês abaixo da meta definida (com tolerância).',
  budget_overspent:       'Gasto do mês ultrapassou o orçamento definido.',
  tracking_stopped:       'Nenhum evento de tracking recebido há horas (snippet/CAPI).',
  cpa_over_target:        'CPA do mês acima da meta (com tolerância e mínimo de pedidos).',
  revenue_drop:           'Faturamento 24h muito abaixo da mediana 7d. Só alerta canal que vende quase todo dia (evita falso por canal esparso).',
  views_drop:             'Pageviews das últimas 2h despencaram vs o mesmo horário dos 7 dias (ignora tráfego baixo).',
  zero_sales:             'Nenhuma venda online em horário comercial por X horas seguidas.',
  checkout_drop:          'Checkouts iniciados nas últimas 2h despencaram vs baseline (ignora volume baixo).',
  low_balance_meta:       'Saldo pré-pago da Meta abaixo do limite.',
  low_balance_google:     'Saldo/dias restantes do Google estimados baixos pelo ritmo de gasto.',
  google_conversion_drop: 'Conversões enviadas ao Google 24h abaixo da mediana 7d. Só canal consistente (evita falso).',
  high_ticket_anomaly:    'Pedido muito acima do ticket médio — apenas informativo.',
  roas_drop_channel:      'ROAS 24h do canal caiu vs 7d mesmo com investimento ativo (mínimo de spend).',
  spend_below_expected:   'Investimento do canal abaixo do esperado — possível campanha pausada.',
  utm_null_ratio:         'Muitos pedidos sem UTM nas últimas 24h (snippet não está passando a origem).',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

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

// ── Alert data pretty-printer ─────────────────────────────────────────────────

function AlertDataView({ data }: { data: Record<string, unknown> }) {
  const keys = Object.keys(data).filter(k => k !== 'check')
  if (keys.length === 0) return null

  // Show key findings if present
  const finding = data.finding as Record<string, unknown> | undefined
  const message = finding?.message as string | undefined

  if (message) {
    return (
      <div className="mt-2 bg-[#0f1117] rounded-lg px-3 py-2 border border-[#2a2f3e] text-xs text-slate-400">
        {message}
      </div>
    )
  }

  // Fallback: show compact key/value pairs (skip deep objects)
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

// ── AlertCard ─────────────────────────────────────────────────────────────────

function AlertCard({ alert: a, onResolve }: { alert: Alert; onResolve?: () => void }) {
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
        <Icon size={15} className={
          a.severity === 'critical' ? 'text-red-400 mt-0.5 shrink-0' :
          a.severity === 'warning'  ? 'text-yellow-400 mt-0.5 shrink-0' :
                                      'text-indigo-400 mt-0.5 shrink-0'
        } />
        <div className="flex-1 min-w-0">
          {/* Title row */}
          <div className="flex items-start justify-between gap-2 flex-wrap">
            <p className="text-sm font-semibold text-white leading-tight">{a.title}</p>
            <div className="flex items-center gap-2 shrink-0">
              <span className={`text-xs px-2 py-0.5 rounded font-medium ${SEV_BADGE[a.severity] || SEV_BADGE.info}`}>
                {SEV_LABEL[a.severity] || a.severity}
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

          {/* Message */}
          <p className="text-xs text-slate-400 mt-1 leading-relaxed">{a.message}</p>

          {/* Pretty data (only when useful) */}
          {a.data && Object.keys(a.data).length > 0 && (
            <AlertDataView data={a.data} />
          )}

          {/* Footer timestamps */}
          <div className="flex items-center gap-3 mt-2.5">
            <span className="flex items-center gap-1 text-xs text-slate-600">
              <Clock size={10} />
              {fmtRelative(a.created_at)}
              <span className="text-slate-700 ml-1">{fmtDate(a.created_at)}</span>
            </span>
            {a.resolved_at && (
              <span className="text-xs text-slate-600">→ resolvido {fmtRelative(a.resolved_at)}</span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── RuleRow ───────────────────────────────────────────────────────────────────

function RuleRow({ rule, onToggle }: { rule: Rule; onToggle: (v: boolean) => void }) {
  return (
    <div className="flex items-start justify-between px-5 py-3 gap-4">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <p className={`text-sm ${rule.enabled ? 'text-slate-200' : 'text-slate-500'}`}>{rule.name || RULE_LABEL[rule.rule_key] || rule.rule_key}</p>
          <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${SEV_BADGE[rule.severity] || SEV_BADGE.info}`}>
            {SEV_LABEL[rule.severity] || rule.severity}
          </span>
          {rule.overridden && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/15 text-indigo-300 border border-indigo-500/25" title="Diferente do padrão da agência — específico deste cliente">
              personalizado
            </span>
          )}
        </div>
        {RULE_DESC[rule.rule_key] && (
          <p className="text-xs text-slate-600 mt-0.5 leading-relaxed">{RULE_DESC[rule.rule_key]}</p>
        )}
      </div>
      <button
        onClick={() => onToggle(!rule.enabled)}
        className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors mt-0.5 ${
          rule.enabled ? 'bg-indigo-600' : 'bg-slate-700'
        }`}
        aria-label={rule.enabled ? 'Desativar alerta' : 'Ativar alerta'}
      >
        <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${
          rule.enabled ? 'translate-x-4' : 'translate-x-1'
        }`} />
      </button>
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

const AUTO_REFRESH_MS = 5 * 60 * 1000  // 5 min

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
  const [showRules,       setShowRules]       = useState(true)
  const [filter,          setFilter]          = useState<Filter>('all')
  const [lastRefresh,     setLastRefresh]     = useState<Date>(new Date())
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    setError(null)
    try {
      const url = `${API_URL}/alerts/${pixelId}?include_resolved=${includeResolved}&limit=200`
      const res = await fetch(url)
      if (!res.ok) {
        setError(`Erro ${res.status}: ${(await res.text()).slice(0, 120)}`)
        setAlerts([])
      } else {
        setAlerts((await res.json()).alerts || [])
        setLastRefresh(new Date())
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erro ao carregar alertas')
    }
    if (!silent) setLoading(false)
  }, [pixelId, includeResolved])

  const loadRules = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/alerts/rules/${pixelId}`)
      if (res.ok) setRules((await res.json()).rules || [])
    } catch (_) {}
  }, [pixelId])

  // Initial load + auto-refresh
  useEffect(() => {
    load()
    loadRules()
  }, [load, loadRules])

  useEffect(() => {
    timerRef.current = setTimeout(() => load(true), AUTO_REFRESH_MS)
    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [lastRefresh, load])

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
      load(true)
    } catch (_) {}
  }

  async function toggleRule(rule: Rule, enabled: boolean) {
    // Otimista: atualiza o estado efetivo + flag de personalizado.
    setRules(prev => prev.map(r =>
      (r.rule_key === rule.rule_key && r.name === rule.name)
        ? { ...r, enabled, overridden: enabled !== r.global_enabled }
        : r
    ))
    try {
      await fetch(`${API_URL}/alerts/rules/${pixelId}/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rule_key: rule.rule_key, name: rule.name, enabled }),
      })
    } catch (_) {
      loadRules()  // reverte pro estado real em caso de falha
    }
  }

  // Derived state
  const open     = alerts.filter(a => !a.resolved_at)
  const resolved = alerts.filter(a =>  a.resolved_at)
  const counts   = {
    critical: open.filter(a => a.severity === 'critical').length,
    warning:  open.filter(a => a.severity === 'warning').length,
    info:     open.filter(a => a.severity === 'info').length,
  }
  const filtered = filter === 'all' ? open : open.filter(a => a.severity === filter)

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
            Engine a cada 30 min · Monitor diário 09:30 BRT
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

      {/* Summary cards */}
      {!loading && (
        <div className="grid grid-cols-3 gap-3">
          <SummaryCard severity="critical" count={counts.critical} />
          <SummaryCard severity="warning"  count={counts.warning} />
          <SummaryCard severity="info"     count={counts.info} />
        </div>
      )}

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
                  <span className="text-sm font-medium text-slate-300">Alertas configurados</span>
                  <span className="text-xs text-slate-600">
                    ({rules.filter(r => r.enabled).length}/{rules.length} ativos neste cliente)
                  </span>
                </div>
                {showRules
                  ? <ChevronUp  size={14} className="text-slate-500" />
                  : <ChevronDown size={14} className="text-slate-500" />
                }
              </button>
              {showRules && (
                <>
                  <p className="px-5 pt-3 pb-1 text-xs text-slate-600 border-t border-[#2a2f3e]">
                    Ligue/desligue cada alerta só para este cliente. Mudar aqui não afeta os outros clientes da agência.
                  </p>
                  <div className="divide-y divide-[#2a2f3e]">
                    {rules.map(rule => (
                      <RuleRow
                        key={`${rule.rule_key}:${rule.name}`}
                        rule={rule}
                        onToggle={enabled => toggleRule(rule, enabled)}
                      />
                    ))}
                  </div>
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
