'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  Pencil, Check, X, Target, DollarSign, TrendingUp, AlertTriangle,
  CheckCircle2, Clock, HelpCircle, XCircle, Bell, RefreshCw,
} from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────────────────

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
  monitoring_enabled?: boolean
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

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtBRL(v: number | null | undefined, unit?: string | null): string {
  if (v == null) return '—'
  if (unit === 'x' || unit === 'roas') return `${v.toFixed(2)}x`
  if (unit === '%') return `${(v * 100).toFixed(1)}%`
  if (unit === 'BRL' || !unit) {
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(v)
  }
  return `${v.toFixed(2)} ${unit}`
}

function fmtPct(v: number | null): string {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

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

const PLATFORM_LABEL: Record<string, string> = {
  google: 'Google Ads',
  meta:   'Meta Ads',
}

const METRIC_LABEL: Record<string, string> = {
  revenue_business:   'Receita',
  mer:                'MER',
  roas_google:        'ROAS Google',
  roas_meta:          'ROAS Meta',
  cpa_google:         'CPA Google',
  cpa_meta:           'CPA Meta',
  google_conversions: 'Conversões Google',
  meta_conversions:   'Conversões Meta',
  leads:              'Leads',
}

// ── Inline editable budget cell ───────────────────────────────────────────────

function BudgetEditCell({
  clientId,
  platform,
  periodLabel,
  currentBudget,
  currency,
  monitoringEnabled,
  onSaved,
}: {
  clientId: string
  platform: string
  periodLabel: string
  currentBudget: number | null
  currency: string
  monitoringEnabled: boolean
  onSaved: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(String(currentBudget ?? ''))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    const num = parseFloat(value.replace(',', '.'))
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
          monitoring_enabled: monitoringEnabled,
          actor:              'agency_web',
          provenance:         'manual_agency',
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError((body as { detail?: string }).detail || `Erro ${res.status}`)
      } else {
        setEditing(false)
        onSaved()
      }
    } catch {
      setError('Falha de rede')
    } finally {
      setSaving(false)
    }
  }

  if (!editing) {
    return (
      <span className="flex items-center gap-1.5 group">
        {currentBudget != null
          ? fmtBRL(currentBudget, currency === 'BRL' ? undefined : currency)
          : <span className="text-slate-600 italic text-xs">não definido</span>}
        <button
          onClick={() => { setValue(String(currentBudget ?? '')); setEditing(true) }}
          className="opacity-0 group-hover:opacity-100 transition-opacity text-slate-500 hover:text-white"
        >
          <Pencil size={12} />
        </button>
      </span>
    )
  }

  return (
    <span className="flex items-center gap-1">
      <input
        type="text"
        value={value}
        onChange={e => setValue(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false) }}
        className="w-28 bg-[#0f1117] border border-indigo-500 rounded px-2 py-0.5 text-sm text-white focus:outline-none"
        autoFocus
      />
      {saving
        ? <RefreshCw size={13} className="animate-spin text-slate-400" />
        : <>
            <button onClick={save} className="text-emerald-400 hover:text-emerald-300"><Check size={13} /></button>
            <button onClick={() => setEditing(false)} className="text-slate-500 hover:text-white"><X size={13} /></button>
          </>
      }
      {error && <span className="text-xs text-red-400 ml-1">{error}</span>}
    </span>
  )
}

// ── Inline editable target cell ───────────────────────────────────────────────

function TargetEditCell({
  clientId,
  metricKey,
  entry,
  onSaved,
}: {
  clientId: string
  metricKey: string
  entry: TargetEntry
  onSaved: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [targetVal, setTargetVal] = useState(String(entry.target))
  const [period, setPeriod] = useState(entry.period ?? '')
  const [channel, setChannel] = useState(entry.channel ?? '')
  const [unit, setUnit] = useState(entry.unit ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
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
        setEditing(false)
        onSaved()
      }
    } catch {
      setError('Falha de rede')
    } finally {
      setSaving(false)
    }
  }

  if (!editing) {
    return (
      <span className="flex items-center gap-1.5 group">
        {fmtBRL(entry.target, entry.unit)}
        <button
          onClick={() => setEditing(true)}
          className="opacity-0 group-hover:opacity-100 transition-opacity text-slate-500 hover:text-white"
        >
          <Pencil size={12} />
        </button>
      </span>
    )
  }

  return (
    <div className="flex flex-col gap-1.5 py-1">
      <div className="flex items-center gap-1">
        <input
          type="text"
          value={targetVal}
          onChange={e => setTargetVal(e.target.value)}
          placeholder="Valor"
          className="w-24 bg-[#0f1117] border border-indigo-500 rounded px-2 py-0.5 text-xs text-white focus:outline-none"
          autoFocus
        />
        <input
          type="text"
          value={unit}
          onChange={e => setUnit(e.target.value)}
          placeholder="Unidade"
          className="w-20 bg-[#0f1117] border border-[#2a2f3e] rounded px-2 py-0.5 text-xs text-white focus:outline-none"
        />
      </div>
      <div className="flex items-center gap-1">
        <select
          value={period}
          onChange={e => setPeriod(e.target.value)}
          className="bg-[#0f1117] border border-[#2a2f3e] rounded px-2 py-0.5 text-xs text-white focus:outline-none"
        >
          <option value="">Período</option>
          <option value="monthly">monthly</option>
          <option value="daily">daily</option>
          <option value="weekly">weekly</option>
          <option value="none">none</option>
        </select>
        <select
          value={channel}
          onChange={e => setChannel(e.target.value)}
          className="bg-[#0f1117] border border-[#2a2f3e] rounded px-2 py-0.5 text-xs text-white focus:outline-none"
        >
          <option value="">Canal</option>
          <option value="all">all</option>
          <option value="google">google</option>
          <option value="meta">meta</option>
        </select>
      </div>
      <div className="flex items-center gap-1">
        {saving
          ? <RefreshCw size={13} className="animate-spin text-slate-400" />
          : <>
              <button onClick={save} className="text-emerald-400 hover:text-emerald-300 text-xs flex items-center gap-0.5"><Check size={12} /> Salvar</button>
              <button onClick={() => setEditing(false)} className="text-slate-500 hover:text-white text-xs flex items-center gap-0.5"><X size={12} /> Cancelar</button>
            </>
        }
        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function GoalsBudgetClient({ clientId, pacing, balance, budgetConfig, targets }: Props) {
  const router = useRouter()
  const periodLabel = pacing?.period_label ?? new Date().toISOString().slice(0, 7)

  function refresh() { router.refresh() }

  // Derive monitoring status
  const hasAnyBudgetConfig = (budgetConfig?.configs ?? []).some(c => c.monitoring_enabled)
  const hasAnyTargets = Object.keys(targets?.target_truth ?? {}).length > 0
  const anyPrepaid = balance?.any_prepaid ?? false

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

      {/* Section 1 — Orçamento por Canal */}
      <section className="mb-8">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-2">
          <DollarSign size={14} />
          Orçamento por Canal
        </h2>

        {!pacing && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-6 text-center text-slate-500 text-sm">
            Dados de pacing indisponíveis
          </div>
        )}

        {pacing && pacing.budget.length === 0 && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-6 text-center text-slate-500 text-sm">
            Nenhum orçamento configurado para {periodLabel}.
            <br />
            <span className="text-xs text-slate-600 mt-1 block">Use o ícone de edição após configurar um orçamento abaixo.</span>
          </div>
        )}

        {pacing && pacing.budget.length > 0 && (
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
                {pacing.budget.map(b => {
                  const config = (budgetConfig?.configs ?? []).find(
                    c => c.platform === b.platform && c.period_label === periodLabel,
                  )
                  return (
                    <tr key={b.platform} className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a] transition-colors">
                      <td className="px-4 py-3 text-white font-medium">
                        {PLATFORM_LABEL[b.platform] ?? b.platform}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <BudgetEditCell
                          clientId={clientId}
                          platform={b.platform}
                          periodLabel={periodLabel}
                          currentBudget={b.monthly_budget}
                          currency={b.currency}
                          monitoringEnabled={config?.monitoring_enabled ?? true}
                          onSaved={refresh}
                        />
                      </td>
                      <td className="px-4 py-3 text-right text-slate-300">
                        {fmtBRL(b.spend_mtd)}
                      </td>
                      <td className="px-4 py-3 text-right text-slate-300">
                        {fmtPct(b.pacing_ratio)}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-xs font-medium ${BUDGET_STATUS_CLASS[b.budget_status]}`}>
                          {BUDGET_STATUS_LABEL[b.budget_status]}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right text-slate-400">
                        {fmtBRL(b.projected_month_end_spend)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Show platforms without pacing data but with budget config */}
        {pacing && pacing.budget.length === 0 && (
          <div className="mt-3 flex gap-3">
            {['google', 'meta'].map(p => (
              <div key={p} className="bg-[#1a1f2e] border border-dashed border-[#2a2f3e] rounded-xl px-4 py-3 flex items-center gap-3">
                <span className="text-sm text-slate-400">{PLATFORM_LABEL[p]}</span>
                <BudgetEditCell
                  clientId={clientId}
                  platform={p}
                  periodLabel={periodLabel}
                  currentBudget={null}
                  currency="BRL"
                  monitoringEnabled={true}
                  onSaved={refresh}
                />
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Section 2 — Metas */}
      <section className="mb-8">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-2">
          <TrendingUp size={14} />
          Metas de Performance
        </h2>

        {!pacing && !targets && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-6 text-center text-slate-500 text-sm">
            Dados de metas indisponíveis
          </div>
        )}

        {pacing && pacing.targets.length === 0 && !targets && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-6 text-center text-slate-500 text-sm">
            Nenhuma meta configurada.
          </div>
        )}

        {(pacing?.targets.length ?? 0) > 0 && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden mb-4">
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
                {pacing!.targets.map(t => {
                  const ttEntry = targets?.target_truth?.[t.metric_key]
                  const periodVal = targets?.period_audit?.[t.metric_key]
                  const isEfficiency = t.expected_target_to_date == null
                  return (
                    <tr key={t.metric_key} className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a] transition-colors">
                      <td className="px-4 py-3 text-white font-medium">
                        {METRIC_LABEL[t.metric_key] ?? t.metric_key}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {ttEntry
                          ? <TargetEditCell
                              clientId={clientId}
                              metricKey={t.metric_key}
                              entry={ttEntry}
                              onSaved={refresh}
                            />
                          : fmtBRL(t.target, t.unit)
                        }
                      </td>
                      <td className="px-4 py-3 text-right text-slate-300">
                        {t.actual != null
                          ? fmtBRL(t.actual, t.unit)
                          : <span className="text-slate-600 text-xs italic">aguardando dados</span>}
                      </td>
                      <td className="px-4 py-3 text-right text-slate-400">
                        {isEfficiency ? '—' : fmtPct(t.attainment_pct)}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-xs font-medium ${TARGET_STATUS_CLASS[t.target_status]}`}>
                          {TARGET_STATUS_LABEL[t.target_status]}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        {periodVal
                          ? <span className="text-xs text-slate-400">{periodVal}</span>
                          : <span className="text-xs text-yellow-400 italic">Periodicidade não definida</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Raw truth/targets (for editing even when pacing has no entries) */}
        {targets && Object.keys(targets.target_truth).length > 0 && (pacing?.targets.length ?? 0) === 0 && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden mb-4">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#2a2f3e] text-slate-500 text-xs uppercase">
                  <th className="px-4 py-3 text-left">Métrica</th>
                  <th className="px-4 py-3 text-right">Meta</th>
                  <th className="px-4 py-3 text-left">Período</th>
                  <th className="px-4 py-3 text-left">Canal</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(targets.target_truth).map(([key, entry]) => {
                  const periodVal = targets.period_audit?.[key]
                  return (
                    <tr key={key} className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a] transition-colors">
                      <td className="px-4 py-3 text-white font-medium">
                        {METRIC_LABEL[key] ?? key}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <TargetEditCell
                          clientId={clientId}
                          metricKey={key}
                          entry={entry}
                          onSaved={refresh}
                        />
                      </td>
                      <td className="px-4 py-3">
                        {periodVal
                          ? <span className="text-xs text-slate-400">{periodVal}</span>
                          : <span className="text-xs text-yellow-400 italic">Periodicidade não definida</span>}
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-400">
                        {entry.channel ?? '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {targets && Object.keys(targets.target_truth).length === 0 && (pacing?.targets.length ?? 0) === 0 && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-6 text-center text-slate-500 text-sm">
            Nenhuma meta configurada.
          </div>
        )}
      </section>

      {/* Section 3 — Saldo Prepaid */}
      <section className="mb-8">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-2">
          <DollarSign size={14} />
          Saldo Prepaid
        </h2>

        {!balance && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-6 text-center text-slate-500 text-sm">
            Dados de saldo indisponíveis
          </div>
        )}

        {balance && !balance.any_prepaid && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-6 text-center text-slate-500 text-sm">
            Nenhuma conta prepaid configurada.
          </div>
        )}

        {balance && balance.any_prepaid && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {balance.snapshots.map(snap => {
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

        {/* Also show from pacing.balance if balance endpoint failed */}
        {!balance && pacing && pacing.balance.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {pacing.balance.map(b => {
              const statusCls = BALANCE_STATUS_CLASS[b.balance_status] ?? 'text-slate-500'
              return (
                <div key={b.platform} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-sm font-medium text-white">
                      {PLATFORM_LABEL[b.platform] ?? b.platform}
                    </span>
                    <span className={`text-xs font-medium ${statusCls}`}>
                      {b.balance_status}
                    </span>
                  </div>
                  <div className="text-2xl font-bold text-white mb-1">
                    {b.balance != null ? fmtBRL(b.balance, b.currency ?? undefined) : '—'}
                  </div>
                  {b.estimated_days_remaining != null && (
                    <div className="text-xs text-slate-400">
                      ~{Math.round(b.estimated_days_remaining)} dias restantes
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>

      {/* Section 4 — Monitoramento */}
      <section className="mb-8">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-2">
          <Bell size={14} />
          Monitoramento de Alertas
        </h2>

        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
          <div className="space-y-3">
            <MonitorRow
              label="Saldo (ACCOUNT_BALANCE_*)"
              description="Alertas quando saldo prepaid fica baixo ou zerado"
              active={anyPrepaid}
            />
            <MonitorRow
              label="Orçamento (BUDGET_PACING_*)"
              description="Alertas quando gasto está fora do ritmo esperado"
              active={hasAnyBudgetConfig}
            />
            <MonitorRow
              label="Metas (MER / ROAS / CPA / Receita)"
              description="Alertas quando métricas ficam abaixo das metas definidas"
              active={hasAnyTargets}
            />
          </div>
        </div>
      </section>
    </div>
  )
}

function MonitorRow({
  label,
  description,
  active,
}: {
  label: string
  description: string
  active: boolean
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <p className="text-sm text-white font-medium">{label}</p>
        <p className="text-xs text-slate-500 mt-0.5">{description}</p>
      </div>
      <span className={`shrink-0 text-xs font-medium px-2 py-0.5 rounded-full ${
        active
          ? 'bg-emerald-400/10 text-emerald-400'
          : 'bg-slate-700/40 text-slate-500'
      }`}>
        {active ? 'ativa' : 'inativa'}
      </span>
    </div>
  )
}
