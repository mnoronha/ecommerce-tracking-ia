'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import {
  Target, DollarSign, Save, Loader2, CheckCircle,
  AlertCircle, ChevronDown, ChevronUp, Pencil, Check, X,
} from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

const CHANNELS = [
  { value: 'meta_ads',   label: 'Meta Ads',    key: 'meta_ads_budget'   },
  { value: 'google_ads', label: 'Google Ads',  key: 'google_ads_budget' },
  { value: 'tiktok_ads', label: 'TikTok Ads',  key: 'tiktok_ads_budget' },
] as const

function monthStart(offset = 0): string {
  const d = new Date()
  d.setDate(1)
  d.setMonth(d.getMonth() - offset)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  return `${y}-${m}-01`
}

function fmtMonth(m: string) {
  const [y, mo] = m.split('-')
  return new Date(Number(y), Number(mo) - 1).toLocaleDateString('pt-BR', {
    month: 'long', year: 'numeric',
  })
}

function fmtBRL(n: number | null | undefined) {
  if (n == null) return '—'
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 })
}

function daysInMonth(isoDate: string) {
  const [y, m] = isoDate.split('-')
  return new Date(Number(y), Number(m), 0).getDate()
}

interface PersistentGoals {
  revenue_goal:      number | null
  roas_goal:         number | null
  cpa_target:        number | null
  meta_ads_budget:   number | null
  google_ads_budget: number | null
  tiktok_ads_budget: number | null
}

type GoalHistory = {
  id: string; month: string
  revenue_goal: number | null; roas_goal: number | null; cpa_target: number | null
}

type Progress = {
  revenue: number; orders: number
  spendByChannel: Record<string, number>
}

const INPUT = 'w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-600 outline-none transition-colors focus:border-indigo-500'

function ProgressBar({ value, max, color = 'bg-indigo-500' }: { value: number; max: number; color?: string }) {
  const pct = max > 0 ? Math.min(value / max * 100, 100) : 0
  return (
    <div className="w-full bg-[#2a2f3e] rounded-full h-1.5 overflow-hidden">
      <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
    </div>
  )
}

function Msg({ msg }: { msg: { ok: boolean; text: string } }) {
  return (
    <div className={`flex items-center gap-2 text-xs rounded-lg px-3 py-2.5 ${
      msg.ok
        ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
        : 'bg-red-500/10 border border-red-500/20 text-red-400'
    }`}>
      {msg.ok ? <CheckCircle size={12} /> : <AlertCircle size={12} />}
      {msg.text}
    </div>
  )
}

function ProgressCard({
  label, value, subtitle, pct, pacePct, barColor, invertLabel,
}: {
  label: string; value: string; subtitle: string
  pct: number; pacePct: number; barColor: string; invertLabel?: boolean
}) {
  const pctCapped = Math.min(pct, 150)
  const statusText = invertLabel
    ? (pct >= 100 ? 'no alvo' : pct >= 80 ? 'acima' : 'muito acima')
    : (pct >= pacePct * 0.9 ? 'no ritmo' : pct >= pacePct * 0.7 ? 'abaixo' : 'muito abaixo')
  return (
    <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-4">
      <p className="text-xs text-slate-500 mb-2">{label}</p>
      <p className="text-lg font-bold text-white mb-0.5">{value}</p>
      <p className="text-xs text-slate-600 mb-3">{subtitle}</p>
      <ProgressBar value={pctCapped} max={100} color={barColor} />
      <div className="flex items-center justify-between mt-1.5">
        <span className="text-xs text-slate-600">{pct.toFixed(0)}% da meta</span>
        <span className={`text-xs ${barColor.includes('emerald') ? 'text-emerald-500' : barColor.includes('red') ? 'text-red-400' : barColor.includes('yellow') ? 'text-yellow-400' : 'text-indigo-400'}`}>
          {statusText}
        </span>
      </div>
    </div>
  )
}

// ── Inline edit field ─────────────────────────────────────────────────────────

function GoalField({
  label, value, unit = 'R$', hint,
  onChange,
}: {
  label: string; value: string; unit?: string; hint?: string
  onChange: (v: string) => void
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-300 mb-1.5">
        {label} {unit && <span className="text-slate-500">({unit})</span>}
      </label>
      <input
        type="number" min="0" step={unit === 'x' ? '0.01' : '1'}
        value={value} placeholder="—"
        onChange={e => onChange(e.target.value)}
        className={INPUT}
      />
      {hint && <p className="text-xs text-slate-600 mt-1">{hint}</p>}
    </div>
  )
}

export default function MetasPage() {
  const params  = useParams()
  const pixelId = params.clientId as string

  const currentMonth = monthStart(0)

  const [clientUUID, setClientUUID] = useState<string | null>(null)
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState<string | null>(null)

  // Persistent goals form
  const [revenueGoal,    setRevenueGoal]    = useState('')
  const [roasGoal,       setRoasGoal]       = useState('')
  const [cpaTarget,      setCpaTarget]      = useState('')
  const [metaBudget,     setMetaBudget]     = useState('')
  const [googleBudget,   setGoogleBudget]   = useState('')
  const [tiktokBudget,   setTiktokBudget]   = useState('')
  const [saving,         setSaving]         = useState(false)
  const [msg,            setMsg]            = useState<{ ok: boolean; text: string } | null>(null)
  const [editing,        setEditing]        = useState(false)

  // Snapshot to revert on cancel
  const [snapshot, setSnapshot] = useState<Record<string, string>>({})

  // History
  const [goalHistory,   setGoalHistory]   = useState<GoalHistory[]>([])
  const [showHistory,   setShowHistory]   = useState(false)

  // MTD progress
  const [progress, setProgress] = useState<Progress | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)

    const { data: client } = await supabase
      .from('clients').select('id').eq('pixel_id', pixelId).limit(1).single()
    if (!client) { setError('Cliente não encontrado'); setLoading(false); return }
    setClientUUID(client.id)

    const [goalsRes, histRes, ordersRes, spendRes] = await Promise.all([
      fetch(`${API_URL}/clients/${pixelId}/goals`),

      supabase.from('goals')
        .select('id,month,revenue_goal,roas_goal,cpa_target')
        .eq('client_id', client.id)
        .in('month', Array.from({ length: 6 }, (_, i) => monthStart(i)))
        .order('month', { ascending: false }),

      supabase.from('orders').select('total_price')
        .eq('client_id', client.id).eq('financial_status', 'paid')
        .gt('total_price', 0).gte('created_at', currentMonth),

      supabase.from('ad_spend').select('channel, spend')
        .eq('client_id', client.id).gte('date', currentMonth),
    ])

    if (goalsRes.ok) {
      const g: PersistentGoals = await goalsRes.json()
      setRevenueGoal(g.revenue_goal   != null ? String(g.revenue_goal)   : '')
      setRoasGoal(   g.roas_goal      != null ? String(g.roas_goal)      : '')
      setCpaTarget(  g.cpa_target     != null ? String(g.cpa_target)     : '')
      setMetaBudget( g.meta_ads_budget   != null ? String(g.meta_ads_budget)   : '')
      setGoogleBudget(g.google_ads_budget != null ? String(g.google_ads_budget) : '')
      setTiktokBudget(g.tiktok_ads_budget != null ? String(g.tiktok_ads_budget) : '')
    }

    setGoalHistory((histRes.data || []) as GoalHistory[])

    const orders = ordersRes.data || []
    const spendByChannel: Record<string, number> = {}
    for (const row of spendRes.data || []) {
      spendByChannel[row.channel] = (spendByChannel[row.channel] || 0) + Number(row.spend)
    }
    setProgress({
      revenue:        orders.reduce((s, o) => s + Number(o.total_price), 0),
      orders:         orders.length,
      spendByChannel,
    })

    setLoading(false)
  }, [pixelId, currentMonth])

  useEffect(() => { load() }, [load])

  function startEdit() {
    setSnapshot({ revenueGoal, roasGoal, cpaTarget, metaBudget, googleBudget, tiktokBudget })
    setEditing(true)
    setMsg(null)
  }

  function cancelEdit() {
    setRevenueGoal(snapshot.revenueGoal)
    setRoasGoal(snapshot.roasGoal)
    setCpaTarget(snapshot.cpaTarget)
    setMetaBudget(snapshot.metaBudget)
    setGoogleBudget(snapshot.googleBudget)
    setTiktokBudget(snapshot.tiktokBudget)
    setEditing(false)
    setMsg(null)
  }

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true); setMsg(null)
    try {
      const res = await fetch(`${API_URL}/clients/${pixelId}/goals`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          revenue_goal:      revenueGoal   ? Number(revenueGoal)   : null,
          roas_goal:         roasGoal      ? Number(roasGoal)      : null,
          cpa_target:        cpaTarget     ? Number(cpaTarget)     : null,
          meta_ads_budget:   metaBudget    ? Number(metaBudget)    : null,
          google_ads_budget: googleBudget  ? Number(googleBudget)  : null,
          tiktok_ads_budget: tiktokBudget  ? Number(tiktokBudget)  : null,
        }),
      })
      if (res.ok) {
        setMsg({ ok: true, text: 'Metas salvas. Valem para todos os meses até você alterar.' })
        setEditing(false)
        load()
      } else {
        const d = await res.json()
        setMsg({ ok: false, text: d.detail || 'Erro ao salvar' })
      }
    } catch (err: unknown) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Erro de rede' })
    }
    setSaving(false)
  }

  // Derived MTD metrics
  const totalSpend  = progress ? Object.values(progress.spendByChannel).reduce((s, v) => s + v, 0) : 0
  const mtdRoas     = totalSpend > 0 && progress ? progress.revenue / totalSpend : null
  const mtdCpa      = progress && progress.orders > 0 ? totalSpend / progress.orders : null
  const daysDone    = new Date().getDate()
  const daysTotal   = daysInMonth(currentMonth)
  const pacePct     = daysDone / daysTotal * 100

  const revenueGoalN = revenueGoal  ? Number(revenueGoal)  : null
  const roasGoalN    = roasGoal     ? Number(roasGoal)      : null
  const cpaTargetN   = cpaTarget    ? Number(cpaTarget)     : null
  const budgets: Record<string, number | null> = {
    meta_ads:   metaBudget   ? Number(metaBudget)   : null,
    google_ads: googleBudget ? Number(googleBudget) : null,
    tiktok_ads: tiktokBudget ? Number(tiktokBudget) : null,
  }
  const totalBudget = CHANNELS.reduce((s, ch) => s + (budgets[ch.value] || 0), 0)

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <Loader2 size={20} className="animate-spin text-slate-500" />
    </div>
  )

  if (error) return (
    <div className="p-6">
      <div className="flex items-center gap-2 text-red-400 bg-red-500/10 border border-red-500/20 rounded-xl px-4 py-3 text-sm">
        <AlertCircle size={14} /> {error}
      </div>
    </div>
  )

  const hasGoals = revenueGoalN || roasGoalN || cpaTargetN || totalBudget > 0

  return (
    <div className="p-6 space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Metas & Orçamentos</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            {fmtMonth(currentMonth)} · dia {daysDone}/{daysTotal} ({pacePct.toFixed(0)}% do mês)
          </p>
        </div>
        {!editing && hasGoals && (
          <button
            onClick={startEdit}
            className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white bg-[#1a1f2e] border border-[#2a2f3e] hover:border-slate-600 px-3 py-2 rounded-lg transition-colors"
          >
            <Pencil size={12} /> Editar metas
          </button>
        )}
      </div>

      {/* MTD Progress cards */}
      {progress && hasGoals && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {revenueGoalN && (
            <ProgressCard
              label="Receita MTD"
              value={fmtBRL(progress.revenue)}
              subtitle={`meta ${fmtBRL(revenueGoalN)}`}
              pct={progress.revenue / revenueGoalN * 100}
              pacePct={pacePct}
              barColor={progress.revenue / revenueGoalN >= pacePct / 100 * 0.9 ? 'bg-emerald-500' : 'bg-yellow-500'}
            />
          )}
          {roasGoalN && mtdRoas !== null && (
            <ProgressCard
              label="ROAS MTD"
              value={`${mtdRoas.toFixed(2)}x`}
              subtitle={`meta ${roasGoalN.toFixed(1)}x`}
              pct={mtdRoas / roasGoalN * 100}
              pacePct={100}
              barColor={mtdRoas >= roasGoalN ? 'bg-emerald-500' : mtdRoas >= roasGoalN * 0.8 ? 'bg-yellow-500' : 'bg-red-500'}
            />
          )}
          {cpaTargetN && mtdCpa !== null && (
            <ProgressCard
              label="CPA MTD"
              value={fmtBRL(mtdCpa)}
              subtitle={`alvo ${fmtBRL(cpaTargetN)}`}
              pct={cpaTargetN / mtdCpa * 100}
              pacePct={100}
              barColor={mtdCpa <= cpaTargetN ? 'bg-emerald-500' : mtdCpa <= cpaTargetN * 1.2 ? 'bg-yellow-500' : 'bg-red-500'}
              invertLabel
            />
          )}
          {totalBudget > 0 && (
            <ProgressCard
              label="Investimento MTD"
              value={fmtBRL(totalSpend)}
              subtitle={`orçamento ${fmtBRL(totalBudget)}`}
              pct={totalSpend / totalBudget * 100}
              pacePct={pacePct}
              barColor={totalSpend / totalBudget > 1.05 ? 'bg-red-500' : totalSpend / totalBudget >= pacePct / 100 * 0.9 ? 'bg-indigo-500' : 'bg-yellow-500'}
            />
          )}
        </div>
      )}

      {/* Per-channel spend breakdown */}
      {progress && totalBudget > 0 && (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-slate-300 mb-4">Orçamento por canal — mês atual</h2>
          <div className="space-y-4">
            {CHANNELS.filter(ch => budgets[ch.value] != null && budgets[ch.value]! > 0).map(({ value, label }) => {
              const budget = budgets[value] || 0
              const spent  = progress.spendByChannel[value] || 0
              const pct    = budget > 0 ? spent / budget * 100 : 0
              const over   = pct > 105
              return (
                <div key={value}>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-xs text-slate-400">{label}</span>
                    <span className={`text-xs font-medium ${over ? 'text-red-400' : 'text-slate-300'}`}>
                      {fmtBRL(spent)} / {fmtBRL(budget)}
                      <span className="ml-1.5 text-slate-600">({pct.toFixed(0)}%)</span>
                    </span>
                  </div>
                  <ProgressBar
                    value={spent} max={budget}
                    color={over ? 'bg-red-500' : pct >= pacePct * 0.9 ? 'bg-indigo-500' : 'bg-yellow-500'}
                  />
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Goals form */}
      <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-2">
            <Target size={15} className="text-indigo-400" />
            <div>
              <h2 className="text-sm font-semibold text-white">Metas do cliente</h2>
              <p className="text-xs text-slate-500 mt-0.5">Persistentes — valem para todos os meses até serem alteradas</p>
            </div>
          </div>
          {!editing && !hasGoals && (
            <button onClick={startEdit}
              className="flex items-center gap-1.5 text-xs bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-2 rounded-lg transition-colors">
              <Pencil size={12} /> Definir metas
            </button>
          )}
        </div>

        {!editing && hasGoals ? (
          /* Read-only view */
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
            <GoalReadItem label="Meta de faturamento" value={revenueGoal ? fmtBRL(Number(revenueGoal)) : null} />
            <GoalReadItem label="ROAS mínimo"         value={roasGoal    ? `${roasGoal}x`              : null} />
            <GoalReadItem label="CPA máximo"          value={cpaTarget   ? fmtBRL(Number(cpaTarget))   : null} />
            <div className="col-span-2 sm:col-span-3 border-t border-[#2a2f3e] pt-4">
              <p className="text-xs font-medium text-slate-400 mb-3">Orçamentos mensais</p>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                <GoalReadItem label="Meta Ads"    value={metaBudget    ? fmtBRL(Number(metaBudget))    : null} />
                <GoalReadItem label="Google Ads"  value={googleBudget  ? fmtBRL(Number(googleBudget))  : null} />
                <GoalReadItem label="TikTok Ads"  value={tiktokBudget  ? fmtBRL(Number(tiktokBudget))  : null} />
              </div>
            </div>
          </div>
        ) : editing ? (
          /* Edit form */
          <form onSubmit={save} className="space-y-5">
            <div>
              <p className="text-xs font-medium text-slate-400 mb-3">Metas de resultado</p>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <GoalField label="Meta de faturamento" value={revenueGoal} onChange={setRevenueGoal}
                  hint="Receita total mensal desejada" />
                <GoalField label="ROAS mínimo" value={roasGoal} unit="x" onChange={setRoasGoal}
                  hint="Ex: 3 = R$3 gerados por R$1 investido" />
                <GoalField label="CPA máximo" value={cpaTarget} onChange={setCpaTarget}
                  hint="Custo máximo por compra" />
              </div>
            </div>
            <div className="border-t border-[#2a2f3e] pt-5">
              <p className="text-xs font-medium text-slate-400 mb-3">Orçamentos mensais por canal</p>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <GoalField label="Meta Ads"   value={metaBudget}   onChange={setMetaBudget} />
                <GoalField label="Google Ads" value={googleBudget} onChange={setGoogleBudget} />
                <GoalField label="TikTok Ads" value={tiktokBudget} onChange={setTiktokBudget} />
              </div>
            </div>
            {msg && <Msg msg={msg} />}
            <div className="flex items-center gap-2">
              <button type="submit" disabled={saving}
                className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors">
                {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                Salvar metas
              </button>
              <button type="button" onClick={cancelEdit}
                className="flex items-center gap-1.5 text-sm text-slate-400 hover:text-white bg-[#0f1117] border border-[#2a2f3e] px-4 py-2 rounded-lg transition-colors">
                <X size={13} /> Cancelar
              </button>
            </div>
          </form>
        ) : (
          /* Empty state */
          <div className="text-center py-6 text-slate-500">
            <Target size={28} className="mx-auto mb-2 opacity-30" />
            <p className="text-sm">Nenhuma meta definida ainda.</p>
            <button onClick={startEdit}
              className="mt-3 text-xs text-indigo-400 hover:text-indigo-300 underline underline-offset-2">
              Definir agora
            </button>
          </div>
        )}

        {!editing && msg && (
          <div className="mt-4"><Msg msg={msg} /></div>
        )}
      </div>

      {/* Goal history */}
      {goalHistory.length > 0 && (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
          <button
            onClick={() => setShowHistory(v => !v)}
            className="w-full flex items-center justify-between px-5 py-4 hover:bg-white/[0.02] transition-colors"
          >
            <h2 className="text-sm font-semibold text-slate-300">Histórico de metas por mês</h2>
            {showHistory ? <ChevronUp size={14} className="text-slate-500" /> : <ChevronDown size={14} className="text-slate-500" />}
          </button>
          {showHistory && (
            <div className="overflow-x-auto border-t border-[#2a2f3e]">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#2a2f3e]">
                    {['Mês', 'Receita', 'ROAS', 'CPA máx.'].map(h => (
                      <th key={h} className="text-left px-5 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {goalHistory.map(g => (
                    <tr key={g.id} className={`border-b border-[#2a2f3e] last:border-0 ${g.month === currentMonth ? 'bg-indigo-500/5' : 'hover:bg-[#252a3a]'}`}>
                      <td className="px-5 py-3 text-slate-200 font-medium whitespace-nowrap">
                        {fmtMonth(g.month)}
                        {g.month === currentMonth && (
                          <span className="ml-2 text-xs bg-indigo-500/20 text-indigo-400 px-1.5 py-0.5 rounded">atual</span>
                        )}
                      </td>
                      <td className="px-5 py-3 text-slate-400 whitespace-nowrap">{fmtBRL(g.revenue_goal)}</td>
                      <td className="px-5 py-3 text-slate-400">{g.roas_goal != null ? `${g.roas_goal}x` : '—'}</td>
                      <td className="px-5 py-3 text-slate-400 whitespace-nowrap">{fmtBRL(g.cpa_target)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

    </div>
  )
}

function GoalReadItem({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="bg-[#0f1117] rounded-lg px-4 py-3">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className={`text-base font-semibold ${value ? 'text-white' : 'text-slate-700'}`}>
        {value ?? '—'}
      </p>
    </div>
  )
}
