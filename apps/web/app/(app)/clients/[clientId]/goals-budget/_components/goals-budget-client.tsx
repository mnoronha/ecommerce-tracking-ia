'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  Pencil, Check, X, Target, DollarSign, TrendingUp, Bell,
  RefreshCw, Plus, AlertTriangle, CheckCircle2, Clock, HelpCircle,
} from 'lucide-react'

// ── Types (exported for server component import) ──────────────────────────────

export interface PlatformBudgetPacing {
  platform: string
  monthly_budget: number | null
  currency: string
  spend_mtd: number
  expected_spend_to_date: number | null
  pacing_ratio: number | null
  remaining_budget: number | null
  projected_month_end_spend: number | null
  budget_status: 'ON_PACE' | 'UNDER_PACE' | 'OVER_PACE' | 'UNKNOWN'
}

export interface TargetPacingEntry {
  metric_key: string
  target: number
  unit: string | null
  actual: number | null
  expected_target_to_date: number | null
  attainment_pct: number | null
  projected_target_value: number | null
  target_status: 'ON_PACE' | 'BELOW_PACE' | 'AT_RISK' | 'EXCEEDED' | 'UNKNOWN'
}

export interface PlatformBalanceSummary {
  platform: string
  balance: number | null
  balance_status: string
  estimated_days_remaining: number | null
  currency: string | null
}

export interface PacingContract {
  schema_version: string
  client_id: string
  period_label: string
  computed_at: string
  budget: PlatformBudgetPacing[]
  targets: TargetPacingEntry[]
  balance: PlatformBalanceSummary[]
}

export interface BalanceSnapshot {
  platform: string
  balance_available: number | null
  balance_status: string
  estimated_days_remaining: number | null
  currency: string | null
}

export interface ClientBalanceOut {
  schema_version: string
  client_id: string
  snapshots: BalanceSnapshot[]
  any_prepaid: boolean
}

export interface BudgetConfigOut {
  id: string
  client_id: string
  platform: string
  period_label: string
  monthly_budget: number
  currency: string
  monitoring_enabled: boolean
  updated_at: string
}

export interface BudgetConfigListOut {
  schema_version: string
  client_id: string
  configs: BudgetConfigOut[]
}

export interface TargetEntry {
  target: number
  unit?: string | null
  currency?: string | null
  period?: string | null
  channel?: string | null
}

export interface TargetTruthReadOut {
  schema_version: string
  client_id: string
  target_version: number
  target_truth: Record<string, TargetEntry>
  period_audit: Record<string, string | null>
  valid_from: string
  write_meta?: Record<string, unknown> | null
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  clientId: string
  pacing: PacingContract | null
  balance: ClientBalanceOut | null
  budgetConfig: BudgetConfigListOut | null
  targets: TargetTruthReadOut | null
}

// ── Constants ─────────────────────────────────────────────────────────────────

const PLATFORM_LABEL: Record<string, string> = {
  google: 'Google Ads',
  meta:   'Meta Ads',
}

const METRIC_LABEL: Record<string, string> = {
  revenue_business:        'Receita',
  mer:                     'MER',
  roas_google:             'ROAS Google',
  google_roas_ecommerce:   'ROAS Google Ecommerce',
  cpa_google:              'CPA Google',
  google_cpa_ecommerce:    'CPA Google Ecommerce',
  google_conversions:      'Conversões Google',
  roas_meta:               'ROAS Meta',
  cpa_meta:                'CPA Meta',
  meta_conversions:        'Conversões Meta',
  leads:                   'Leads',
}

const AVAILABLE_METRICS = Object.entries(METRIC_LABEL)

const BUDGET_STATUS_CLASS: Record<string, string> = {
  ON_PACE:    'text-emerald-400',
  UNDER_PACE: 'text-yellow-400',
  OVER_PACE:  'text-red-400',
  UNKNOWN:    'text-slate-500',
}

const BUDGET_STATUS_LABEL: Record<string, string> = {
  ON_PACE:    'No ritmo',
  UNDER_PACE: 'Abaixo do ritmo',
  OVER_PACE:  'Acima do ritmo',
  UNKNOWN:    'Sem dados',
}

const TARGET_STATUS_CLASS: Record<string, string> = {
  ON_PACE:    'text-emerald-400',
  BELOW_PACE: 'text-yellow-400',
  AT_RISK:    'text-red-400',
  EXCEEDED:   'text-indigo-400',
  UNKNOWN:    'text-slate-500',
}

const TARGET_STATUS_LABEL: Record<string, string> = {
  ON_PACE:    'No ritmo',
  BELOW_PACE: 'Abaixo',
  AT_RISK:    'Em risco',
  EXCEEDED:   'Superado',
  UNKNOWN:    'Desconhecido',
}

const BALANCE_STATUS_CLASS: Record<string, string> = {
  OK:        'text-emerald-400',
  LOW:       'text-yellow-400',
  CRITICAL:  'text-red-400',
  EXHAUSTED: 'text-red-400',
  NO_DATA:   'text-slate-500',
  MISSING:   'text-slate-500',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtBRL(v: number | null | undefined, unit?: string | null): string {
  if (v == null) return '—'
  if (unit === 'x' || unit === 'roas') return `${v.toFixed(2)}x`
  if (unit === '%') return `${(v * 100).toFixed(1)}%`
  if (unit === 'BRL' || !unit) {
    return new Intl.NumberFormat('pt-BR', {
      style: 'currency', currency: 'BRL', maximumFractionDigits: 0,
    }).format(v)
  }
  return `${v.toFixed(2)} ${unit}`
}

function fmtPct(v: number | null): string {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

function currentMonthLabel(): string {
  return new Date().toISOString().slice(0, 7)
}

// ── BudgetForm: create or edit a budget config ────────────────────────────────

function BudgetForm({
  clientId,
  platform,
  periodLabel,
  existing,
  onSaved,
  onCancel,
}: {
  clientId: string
  platform: string
  periodLabel: string
  existing: BudgetConfigOut | null
  onSaved: () => void
  onCancel: () => void
}) {
  const [budgetVal, setBudgetVal] = useState(existing ? String(existing.monthly_budget) : '')
  const [currency, setCurrency] = useState(existing?.currency ?? 'BRL')
  const [monitoring, setMonitoring] = useState(existing?.monitoring_enabled ?? true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    const num = parseFloat(budgetVal.replace(',', '.'))
    if (isNaN(num) || num < 0) { setError('Valor inválido'); return }
    setSaving(true); setError(null)
    try {
      const res = await fetch(`/api/v1/clients/${clientId}/budget/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          platform,
          period_label:       periodLabel,
          monthly_budget:     num,
          currency,
          monitoring_enabled: monitoring,
          actor:              'agency_web',
          provenance:         'manual_agency',
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError((body as { detail?: string }).detail || `Erro ${res.status}`)
      } else {
        onSaved()
      }
    } catch {
      setError('Falha de rede')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-[#1a1f2e] border border-indigo-500/40 rounded-xl p-5">
      <p className="text-sm font-medium text-white mb-4">
        {existing ? 'Editar orçamento' : 'Adicionar orçamento'} — {PLATFORM_LABEL[platform] ?? platform} · {periodLabel}
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="text-xs text-slate-500 block mb-1">Orçamento mensal</label>
          <input
            type="text"
            value={budgetVal}
            onChange={e => setBudgetVal(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') onCancel() }}
            placeholder="ex: 15000"
            className="w-36 bg-[#0f1117] border border-[#2a2f3e] focus:border-indigo-500 rounded px-3 py-1.5 text-sm text-white focus:outline-none"
            autoFocus
          />
        </div>
        <div>
          <label className="text-xs text-slate-500 block mb-1">Moeda</label>
          <select
            value={currency}
            onChange={e => setCurrency(e.target.value)}
            className="bg-[#0f1117] border border-[#2a2f3e] rounded px-3 py-1.5 text-sm text-white focus:outline-none"
          >
            <option value="BRL">BRL</option>
            <option value="USD">USD</option>
          </select>
        </div>
        <label className="flex items-center gap-2 cursor-pointer pb-1.5">
          <input
            type="checkbox"
            checked={monitoring}
            onChange={e => setMonitoring(e.target.checked)}
            className="accent-indigo-500"
          />
          <span className="text-xs text-slate-400">Ativar alertas de monitoramento</span>
        </label>
      </div>
      {error && <p className="text-xs text-red-400 mt-2">{error}</p>}
      <div className="flex gap-2 mt-4">
        <button
          onClick={save}
          disabled={saving}
          className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-medium px-4 py-2 rounded-lg transition-colors"
        >
          {saving ? <RefreshCw size={12} className="animate-spin" /> : <Check size={12} />}
          Salvar
        </button>
        <button
          onClick={onCancel}
          className="text-slate-400 hover:text-white text-xs px-3 py-2 rounded-lg hover:bg-[#252a3a] transition-colors"
        >
          Cancelar
        </button>
      </div>
    </div>
  )
}

// ── BudgetRow: inline edit for an existing config ─────────────────────────────

function BudgetRow({
  clientId,
  config,
  pacing,
  periodLabel,
  onSaved,
}: {
  clientId: string
  config: BudgetConfigOut
  pacing: PlatformBudgetPacing | null
  periodLabel: string
  onSaved: () => void
}) {
  const [editing, setEditing] = useState(false)

  if (editing) {
    return (
      <tr className="border-b border-[#2a2f3e]">
        <td colSpan={6} className="px-4 py-3">
          <BudgetForm
            clientId={clientId}
            platform={config.platform}
            periodLabel={periodLabel}
            existing={config}
            onSaved={() => { setEditing(false); onSaved() }}
            onCancel={() => setEditing(false)}
          />
        </td>
      </tr>
    )
  }

  return (
    <tr className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a] group transition-colors">
      <td className="px-4 py-3 text-white font-medium">
        {PLATFORM_LABEL[config.platform] ?? config.platform}
      </td>
      <td className="px-4 py-3 text-right text-slate-300">
        <span className="flex items-center justify-end gap-1.5">
          {fmtBRL(config.monthly_budget, config.currency === 'BRL' ? undefined : config.currency)}
          <button
            onClick={() => setEditing(true)}
            className="opacity-0 group-hover:opacity-100 transition-opacity text-slate-500 hover:text-white"
          >
            <Pencil size={12} />
          </button>
        </span>
      </td>
      <td className="px-4 py-3 text-right text-slate-300">
        {pacing ? fmtBRL(pacing.spend_mtd) : <span className="text-slate-600 text-xs">—</span>}
      </td>
      <td className="px-4 py-3 text-right text-slate-400">
        {pacing?.pacing_ratio != null ? fmtPct(pacing.pacing_ratio) : <span className="text-slate-600 text-xs">—</span>}
      </td>
      <td className="px-4 py-3">
        {pacing && pacing.budget_status !== 'UNKNOWN'
          ? <span className={`text-xs font-medium ${BUDGET_STATUS_CLASS[pacing.budget_status]}`}>
              {BUDGET_STATUS_LABEL[pacing.budget_status]}
            </span>
          : <span className="text-xs text-slate-600">Aguardando ciclo</span>
        }
      </td>
      <td className="px-4 py-3 text-right text-slate-400">
        {pacing?.projected_month_end_spend != null ? fmtBRL(pacing.projected_month_end_spend) : '—'}
      </td>
    </tr>
  )
}

// ── TargetForm: create or edit a target ──────────────────────────────────────

function TargetForm({
  clientId,
  existingKey,
  existingEntry,
  onSaved,
  onCancel,
}: {
  clientId: string
  existingKey?: string
  existingEntry?: TargetEntry
  onSaved: () => void
  onCancel: () => void
}) {
  const [metricKey, setMetricKey] = useState(existingKey ?? '')
  const [targetVal, setTargetVal] = useState(existingEntry ? String(existingEntry.target) : '')
  const [period, setPeriod] = useState(existingEntry?.period ?? 'monthly')
  const [channel, setChannel] = useState(existingEntry?.channel ?? '')
  const [unit, setUnit] = useState(existingEntry?.unit ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isEdit = Boolean(existingKey)

  async function save() {
    if (!metricKey) { setError('Selecione uma métrica'); return }
    const num = parseFloat(targetVal.replace(',', '.'))
    if (isNaN(num) || num < 0) { setError('Valor inválido'); return }
    setSaving(true); setError(null)
    try {
      const res = await fetch(`/api/v1/clients/${clientId}/truth/targets`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': crypto.randomUUID(),
        },
        body: JSON.stringify({
          targets: {
            [metricKey]: {
              target:  num,
              period:  period || null,
              channel: channel || null,
              unit:    unit || null,
            },
          },
          remove_keys: [],
          actor:       'agency_web',
          provenance:  'manual_agency',
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError((body as { detail?: string }).detail || `Erro ${res.status}`)
      } else {
        onSaved()
      }
    } catch {
      setError('Falha de rede')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-[#1a1f2e] border border-indigo-500/40 rounded-xl p-5">
      <p className="text-sm font-medium text-white mb-4">
        {isEdit ? `Editar meta — ${METRIC_LABEL[existingKey!] ?? existingKey}` : 'Adicionar meta'}
      </p>
      <div className="flex flex-wrap items-end gap-3">
        {!isEdit && (
          <div>
            <label className="text-xs text-slate-500 block mb-1">Métrica</label>
            <select
              value={metricKey}
              onChange={e => setMetricKey(e.target.value)}
              className="bg-[#0f1117] border border-[#2a2f3e] focus:border-indigo-500 rounded px-3 py-1.5 text-sm text-white focus:outline-none"
            >
              <option value="">Selecionar...</option>
              {AVAILABLE_METRICS.map(([k, label]) => (
                <option key={k} value={k}>{label}</option>
              ))}
            </select>
          </div>
        )}
        <div>
          <label className="text-xs text-slate-500 block mb-1">Valor da meta</label>
          <input
            type="text"
            value={targetVal}
            onChange={e => setTargetVal(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') onCancel() }}
            placeholder="ex: 90000"
            className="w-32 bg-[#0f1117] border border-[#2a2f3e] focus:border-indigo-500 rounded px-3 py-1.5 text-sm text-white focus:outline-none"
            autoFocus={isEdit}
          />
        </div>
        <div>
          <label className="text-xs text-slate-500 block mb-1">Periodicidade</label>
          <select
            value={period}
            onChange={e => setPeriod(e.target.value)}
            className="bg-[#0f1117] border border-[#2a2f3e] focus:border-indigo-500 rounded px-3 py-1.5 text-sm text-white focus:outline-none"
          >
            <option value="">Não definida</option>
            <option value="monthly">Mensal</option>
            <option value="weekly">Semanal</option>
            <option value="daily">Diário</option>
            <option value="quarterly">Trimestral</option>
            <option value="annual">Anual</option>
            <option value="none">Sem período</option>
          </select>
        </div>
        <div>
          <label className="text-xs text-slate-500 block mb-1">Canal</label>
          <select
            value={channel}
            onChange={e => setChannel(e.target.value)}
            className="bg-[#0f1117] border border-[#2a2f3e] rounded px-3 py-1.5 text-sm text-white focus:outline-none"
          >
            <option value="">Todos</option>
            <option value="google">Google</option>
            <option value="meta">Meta</option>
            <option value="all">all</option>
          </select>
        </div>
        <div>
          <label className="text-xs text-slate-500 block mb-1">Unidade</label>
          <input
            type="text"
            value={unit}
            onChange={e => setUnit(e.target.value)}
            placeholder="BRL, x, %..."
            className="w-20 bg-[#0f1117] border border-[#2a2f3e] rounded px-3 py-1.5 text-sm text-white focus:outline-none"
          />
        </div>
      </div>
      {error && <p className="text-xs text-red-400 mt-2">{error}</p>}
      <div className="flex gap-2 mt-4">
        <button
          onClick={save}
          disabled={saving}
          className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-medium px-4 py-2 rounded-lg transition-colors"
        >
          {saving ? <RefreshCw size={12} className="animate-spin" /> : <Check size={12} />}
          Salvar
        </button>
        <button
          onClick={onCancel}
          className="text-slate-400 hover:text-white text-xs px-3 py-2 rounded-lg hover:bg-[#252a3a] transition-colors"
        >
          Cancelar
        </button>
      </div>
    </div>
  )
}

// ── TargetRow: one target entry ───────────────────────────────────────────────

function TargetRow({
  clientId,
  metricKey,
  entry,
  pacing,
  periodAudit,
  onSaved,
}: {
  clientId: string
  metricKey: string
  entry: TargetEntry
  pacing: TargetPacingEntry | null
  periodAudit: string | null
  onSaved: () => void
}) {
  const [editing, setEditing] = useState(false)
  const periodUnknown = !periodAudit
  const isEfficiency = pacing ? pacing.expected_target_to_date == null : true

  if (editing) {
    return (
      <tr className="border-b border-[#2a2f3e]">
        <td colSpan={6} className="px-4 py-3">
          <TargetForm
            clientId={clientId}
            existingKey={metricKey}
            existingEntry={entry}
            onSaved={() => { setEditing(false); onSaved() }}
            onCancel={() => setEditing(false)}
          />
        </td>
      </tr>
    )
  }

  return (
    <tr className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a] group transition-colors">
      <td className="px-4 py-3 text-white font-medium">
        {METRIC_LABEL[metricKey] ?? metricKey}
      </td>
      <td className="px-4 py-3 text-right">
        <span className="flex items-center justify-end gap-1.5">
          {fmtBRL(entry.target, entry.unit)}
          <button
            onClick={() => setEditing(true)}
            className="opacity-0 group-hover:opacity-100 transition-opacity text-slate-500 hover:text-white"
          >
            <Pencil size={12} />
          </button>
        </span>
      </td>
      <td className="px-4 py-3 text-right text-slate-300">
        {pacing?.actual != null
          ? fmtBRL(pacing.actual, entry.unit)
          : <span className="text-slate-600 text-xs italic">aguardando dados</span>}
      </td>
      <td className="px-4 py-3 text-right text-slate-400">
        {isEfficiency ? '—' : pacing ? fmtPct(pacing.attainment_pct) : '—'}
      </td>
      <td className="px-4 py-3">
        {pacing && pacing.target_status !== 'UNKNOWN'
          ? <span className={`text-xs font-medium ${TARGET_STATUS_CLASS[pacing.target_status]}`}>
              {TARGET_STATUS_LABEL[pacing.target_status]}
            </span>
          : <span className="text-xs text-slate-600">—</span>}
      </td>
      <td className="px-4 py-3">
        {periodUnknown
          ? <button
              onClick={() => setEditing(true)}
              className="flex items-center gap-1 text-xs text-yellow-400 hover:text-yellow-300 transition-colors"
            >
              <AlertTriangle size={11} />
              Periodicidade não definida
            </button>
          : <span className="text-xs text-slate-400">{periodAudit}</span>}
      </td>
    </tr>
  )
}

// ── Monitoring status helpers ─────────────────────────────────────────────────

type MonitorState = 'ativo' | 'aguardando' | 'nao_aplicavel' | 'indisponivel' | 'parcial'

const MONITOR_STATE_STYLE: Record<MonitorState, { cls: string; label: string }> = {
  ativo:          { cls: 'bg-emerald-400/10 text-emerald-400',  label: 'ATIVO' },
  aguardando:     { cls: 'bg-yellow-400/10 text-yellow-400',    label: 'AGUARDANDO CONFIGURAÇÃO' },
  parcial:        { cls: 'bg-yellow-400/10 text-yellow-400',    label: 'AGUARDANDO PERIODICIDADE' },
  nao_aplicavel:  { cls: 'bg-slate-700/40 text-slate-500',      label: 'NÃO APLICÁVEL' },
  indisponivel:   { cls: 'bg-red-400/10 text-red-400',          label: 'INDISPONÍVEL' },
}

function MonitorRow({
  label,
  description,
  state,
}: {
  label: string
  description: string
  state: MonitorState
}) {
  const { cls, label: stateLabel } = MONITOR_STATE_STYLE[state]
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <p className="text-sm text-white font-medium">{label}</p>
        <p className="text-xs text-slate-500 mt-0.5">{description}</p>
      </div>
      <span className={`shrink-0 text-xs font-medium px-2 py-0.5 rounded-full whitespace-nowrap ${cls}`}>
        {stateLabel}
      </span>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function GoalsBudgetClient({ clientId, pacing, balance, budgetConfig, targets }: Props) {
  const router = useRouter()
  const periodLabel = pacing?.period_label ?? currentMonthLabel()

  // Add-form state
  const [addBudgetPlatform, setAddBudgetPlatform] = useState<string | null>(null)
  const [addingTarget, setAddingTarget] = useState(false)

  function refresh() { router.refresh() }

  // ── Budget section derived state ───────────────────────────────────────────
  const currentConfigs = (budgetConfig?.configs ?? []).filter(
    c => c.period_label === periodLabel,
  )
  const hasBudgetConfigs = currentConfigs.length > 0
  // Index pacing by platform for enrichment
  const pacingByPlatform = Object.fromEntries(
    (pacing?.budget ?? []).map(b => [b.platform, b]),
  )
  // Which platforms are NOT yet configured
  const configuredPlatforms = new Set(currentConfigs.map(c => c.platform))
  const missingPlatforms = ['google', 'meta'].filter(p => !configuredPlatforms.has(p))

  // ── Targets section derived state ─────────────────────────────────────────
  const targetTruth = targets?.target_truth ?? {}
  const hasTargets = Object.keys(targetTruth).length > 0
  // Index pacing targets by metric_key for enrichment
  const pacingByMetric = Object.fromEntries(
    (pacing?.targets ?? []).map(t => [t.metric_key, t]),
  )

  // ── Balance derived state ──────────────────────────────────────────────────
  const anyPrepaid = balance?.any_prepaid ?? false
  const balanceSnapshots = balance?.snapshots ?? pacing?.balance.map(b => ({
    platform: b.platform,
    balance_available: b.balance,
    balance_status: b.balance_status,
    estimated_days_remaining: b.estimated_days_remaining,
    currency: b.currency,
  })) ?? []

  // ── Monitoring states ──────────────────────────────────────────────────────
  const balanceMonitorState: MonitorState = anyPrepaid ? 'ativo' : 'nao_aplicavel'

  const budgetMonitorState: MonitorState = !hasBudgetConfigs
    ? 'aguardando'
    : currentConfigs.some(c => c.monitoring_enabled) ? 'ativo' : 'indisponivel'

  const allTargetPeriodsDefined = hasTargets
    && Object.values(targets?.period_audit ?? {}).every(v => v != null)
  const targetMonitorState: MonitorState = !hasTargets
    ? 'aguardando'
    : allTargetPeriodsDefined ? 'ativo' : 'parcial'

  return (
    <div className="p-8 max-w-5xl">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-2 mb-1">
          <Target size={18} className="text-indigo-400" />
          <h1 className="text-xl font-bold text-white">Metas & Orçamento</h1>
          <span className="text-xs text-slate-500 ml-1">{periodLabel}</span>
        </div>
        <p className="text-sm text-slate-500">
          Configuração de orçamento por canal, metas de performance e saldo de contas.
        </p>
      </div>

      {/* ── Section 1: Orçamento por Canal ───────────────────────────────── */}
      <section className="mb-8">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
            <DollarSign size={14} />
            Orçamento por Canal
          </h2>
          {hasBudgetConfigs && missingPlatforms.length > 0 && addBudgetPlatform === null && (
            <div className="flex gap-2">
              {missingPlatforms.map(p => (
                <button
                  key={p}
                  onClick={() => setAddBudgetPlatform(p)}
                  className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300 bg-indigo-400/10 hover:bg-indigo-400/20 px-2.5 py-1 rounded-lg transition-colors"
                >
                  <Plus size={11} />
                  {PLATFORM_LABEL[p]}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Add-form for a missing platform (shown below header) */}
        {addBudgetPlatform && (
          <div className="mb-4">
            <BudgetForm
              clientId={clientId}
              platform={addBudgetPlatform}
              periodLabel={periodLabel}
              existing={null}
              onSaved={() => { setAddBudgetPlatform(null); refresh() }}
              onCancel={() => setAddBudgetPlatform(null)}
            />
          </div>
        )}

        {/* Empty state — no configs at all */}
        {!hasBudgetConfigs && addBudgetPlatform === null && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-6">
            <p className="text-sm text-slate-400 mb-4">
              Você ainda não definiu orçamento para este cliente.
            </p>
            <div className="flex flex-wrap gap-3">
              {['google', 'meta'].map(p => (
                <button
                  key={p}
                  onClick={() => setAddBudgetPlatform(p)}
                  className="flex items-center gap-2 bg-indigo-600/20 hover:bg-indigo-600/30 border border-indigo-500/30 text-indigo-300 hover:text-indigo-200 text-sm px-4 py-2.5 rounded-lg transition-colors"
                >
                  <Plus size={14} />
                  Adicionar orçamento {PLATFORM_LABEL[p]}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Budget table */}
        {hasBudgetConfigs && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#2a2f3e] text-slate-500 text-xs uppercase">
                  <th className="px-4 py-3 text-left">Canal</th>
                  <th className="px-4 py-3 text-right">Orçamento Mensal</th>
                  <th className="px-4 py-3 text-right">Gasto MTD</th>
                  <th className="px-4 py-3 text-right">Ritmo</th>
                  <th className="px-4 py-3 text-left">Status</th>
                  <th className="px-4 py-3 text-right">Projeção EOM</th>
                </tr>
              </thead>
              <tbody>
                {currentConfigs.map(config => (
                  <BudgetRow
                    key={config.id}
                    clientId={clientId}
                    config={config}
                    pacing={pacingByPlatform[config.platform] ?? null}
                    periodLabel={periodLabel}
                    onSaved={refresh}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Note when pacing is unavailable but budget exists */}
        {hasBudgetConfigs && !pacing && (
          <p className="text-xs text-slate-600 mt-2">
            Pacing indisponível — aguardando próximo ciclo de análise (07:15 UTC).
          </p>
        )}
      </section>

      {/* ── Section 2: Metas de Performance ──────────────────────────────── */}
      <section className="mb-8">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
            <TrendingUp size={14} />
            Metas de Performance
          </h2>
          {!addingTarget && (
            <button
              onClick={() => setAddingTarget(true)}
              className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300 bg-indigo-400/10 hover:bg-indigo-400/20 px-2.5 py-1 rounded-lg transition-colors"
            >
              <Plus size={11} />
              Adicionar meta
            </button>
          )}
        </div>

        {/* Add-target form */}
        {addingTarget && (
          <div className="mb-4">
            <TargetForm
              clientId={clientId}
              onSaved={() => { setAddingTarget(false); refresh() }}
              onCancel={() => setAddingTarget(false)}
            />
          </div>
        )}

        {/* No targets at all */}
        {!hasTargets && !addingTarget && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-6">
            <p className="text-sm text-slate-400 mb-4">Nenhuma meta configurada.</p>
            <button
              onClick={() => setAddingTarget(true)}
              className="flex items-center gap-2 bg-indigo-600/20 hover:bg-indigo-600/30 border border-indigo-500/30 text-indigo-300 hover:text-indigo-200 text-sm px-4 py-2.5 rounded-lg transition-colors"
            >
              <Plus size={14} />
              Adicionar primeira meta
            </button>
          </div>
        )}

        {/* Targets table — source is targets.target_truth, pacing enriches it */}
        {hasTargets && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#2a2f3e] text-slate-500 text-xs uppercase">
                  <th className="px-4 py-3 text-left">Métrica</th>
                  <th className="px-4 py-3 text-right">Meta</th>
                  <th className="px-4 py-3 text-right">Realizado MTD</th>
                  <th className="px-4 py-3 text-right">Pace</th>
                  <th className="px-4 py-3 text-left">Status</th>
                  <th className="px-4 py-3 text-left">Período</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(targetTruth).map(([key, entry]) => (
                  <TargetRow
                    key={key}
                    clientId={clientId}
                    metricKey={key}
                    entry={entry}
                    pacing={pacingByMetric[key] ?? null}
                    periodAudit={targets?.period_audit?.[key] ?? null}
                    onSaved={refresh}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── Section 3: Saldo Prepaid ──────────────────────────────────────── */}
      <section className="mb-8">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-2">
          <DollarSign size={14} />
          Saldo Prepaid
        </h2>

        {!anyPrepaid && balanceSnapshots.length === 0 && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5 text-sm text-slate-500">
            Nenhuma conta prepaid configurada.
          </div>
        )}

        {(anyPrepaid || balanceSnapshots.length > 0) && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {balanceSnapshots.map(snap => {
              const statusCls = BALANCE_STATUS_CLASS[snap.balance_status] ?? 'text-slate-500'
              return (
                <div key={snap.platform} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-sm font-medium text-white">
                      {PLATFORM_LABEL[snap.platform] ?? snap.platform}
                    </span>
                    <span className={`text-xs font-medium ${statusCls}`}>
                      {snap.balance_status}
                    </span>
                  </div>
                  <div className="text-2xl font-bold text-white mb-1">
                    {snap.balance_available != null
                      ? fmtBRL(snap.balance_available, snap.currency ?? undefined)
                      : '—'}
                  </div>
                  {snap.estimated_days_remaining != null && (
                    <div className="text-xs text-slate-400">
                      ~{Math.round(snap.estimated_days_remaining)} dias restantes
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>

      {/* ── Section 4: Monitoramento de Alertas ───────────────────────────── */}
      <section className="mb-8">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-2">
          <Bell size={14} />
          Monitoramento de Alertas
        </h2>

        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
          <div className="space-y-4">
            <MonitorRow
              label="Saldo (ACCOUNT_BALANCE_*)"
              description="Dispara quando saldo prepaid atinge limites críticos"
              state={balanceMonitorState}
            />
            <div className="border-t border-[#2a2f3e]" />
            <MonitorRow
              label="Orçamento (BUDGET_PACING_*)"
              description={
                !hasBudgetConfigs
                  ? 'Configure o orçamento mensal acima para ativar'
                  : 'Dispara quando gasto está fora do ritmo esperado'
              }
              state={budgetMonitorState}
            />
            <div className="border-t border-[#2a2f3e]" />
            <MonitorRow
              label="Metas (MER / ROAS / CPA / Receita)"
              description={
                !hasTargets
                  ? 'Adicione metas acima para ativar'
                  : !allTargetPeriodsDefined
                    ? 'Defina a periodicidade das metas para ativar pacing linear'
                    : 'Dispara quando métricas ficam abaixo das metas definidas'
              }
              state={targetMonitorState}
            />
          </div>
        </div>
      </section>
    </div>
  )
}
