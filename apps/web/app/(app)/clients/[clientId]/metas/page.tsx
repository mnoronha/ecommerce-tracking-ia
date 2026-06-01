'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import {
  Target, DollarSign, Save, Loader2, CheckCircle,
  AlertCircle, TrendingUp, Copy, ChevronDown, ChevronUp,
} from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

const CHANNELS = [
  { value: 'meta_ads',    label: 'Meta Ads' },
  { value: 'google_ads',  label: 'Google Ads' },
  { value: 'tiktok_ads',  label: 'TikTok Ads' },
]

function monthStart(offset = 0): string {
  const d = new Date()
  d.setDate(1)
  d.setMonth(d.getMonth() - offset)
  d.setHours(0, 0, 0, 0)
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

type Goal = {
  id: string
  month: string
  leads_goal: number | null
  conversions_goal: number | null
  revenue_goal: number | null
  roas_goal: number | null
  cpa_target: number | null
}

type Budget = {
  id: string
  month: string
  channel: string
  amount: number
}

type Progress = {
  revenue:     number
  orders:      number
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

export default function MetasPage() {
  const params  = useParams()
  const pixelId = params.clientId as string

  const [clientUUID, setClientUUID] = useState<string | null>(null)
  const [agencyId,   setAgencyId]   = useState<string | null>(null)
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState<string | null>(null)

  const currentMonth = monthStart(0)
  const prevMonth    = monthStart(1)

  // Goal form
  const [goal,        setGoal]        = useState<Goal | null>(null)
  const [leadsGoal,   setLeadsGoal]   = useState('')
  const [convsGoal,   setConvsGoal]   = useState('')
  const [revenueGoal, setRevenueGoal] = useState('')
  const [roasGoal,    setRoasGoal]    = useState('')
  const [cpaTarget,   setCpaTarget]   = useState('')
  const [savingGoal,  setSavingGoal]  = useState(false)
  const [goalMsg,     setGoalMsg]     = useState<{ ok: boolean; text: string } | null>(null)

  // Budget form
  const [budgets,         setBudgets]         = useState<Record<string, string>>({ meta_ads: '', google_ads: '', tiktok_ads: '' })
  const [existingBudgets, setExistingBudgets] = useState<Budget[]>([])
  const [savingBudget,    setSavingBudget]     = useState(false)
  const [budgetMsg,       setBudgetMsg]        = useState<{ ok: boolean; text: string } | null>(null)

  // History
  const [pastGoals,   setPastGoals]   = useState<Goal[]>([])
  const [pastBudgets, setPastBudgets] = useState<Budget[]>([])
  const [showBudgetHistory, setShowBudgetHistory] = useState(false)

  // MTD progress
  const [progress, setProgress] = useState<Progress | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)

    const { data: client } = await supabase
      .from('clients')
      .select('id, agency_id')
      .eq('pixel_id', pixelId)
      .limit(1)
      .single()

    if (!client) { setError('Cliente não encontrado'); setLoading(false); return }
    setClientUUID(client.id)
    setAgencyId(client.agency_id)

    const [goalRes, budgetRes, histGoalRes, histBudgetRes, ordersRes, spendRes] = await Promise.all([
      supabase.from('goals').select('id,month,leads_goal,conversions_goal,revenue_goal,roas_goal,cpa_target')
        .eq('client_id', client.id).eq('month', currentMonth).limit(1).single(),

      supabase.from('budgets').select('id,month,channel,amount')
        .eq('client_id', client.id).eq('month', currentMonth),

      supabase.from('goals').select('id,month,leads_goal,conversions_goal,revenue_goal,roas_goal,cpa_target')
        .eq('client_id', client.id)
        .in('month', Array.from({ length: 6 }, (_, i) => monthStart(i)))
        .order('month', { ascending: false }),

      supabase.from('budgets').select('id,month,channel,amount')
        .eq('client_id', client.id)
        .in('month', Array.from({ length: 6 }, (_, i) => monthStart(i)))
        .order('month', { ascending: false }),

      supabase.from('orders').select('total_price')
        .eq('client_id', client.id)
        .eq('financial_status', 'paid')
        .gt('total_price', 0)
        .gte('created_at', currentMonth),

      supabase.from('ad_spend').select('channel, spend')
        .eq('client_id', client.id)
        .gte('date', currentMonth),
    ])

    // Goals
    const g = goalRes.data as Goal | null
    setGoal(g)
    setLeadsGoal(String(g?.leads_goal ?? ''))
    setConvsGoal(String(g?.conversions_goal ?? ''))
    setRevenueGoal(String(g?.revenue_goal ?? ''))
    setRoasGoal(String(g?.roas_goal ?? ''))
    setCpaTarget(String(g?.cpa_target ?? ''))

    // Budgets
    const brows = (budgetRes.data || []) as Budget[]
    setExistingBudgets(brows)
    const initB: Record<string, string> = { meta_ads: '', google_ads: '', tiktok_ads: '' }
    brows.forEach(b => { if (b.channel in initB) initB[b.channel] = String(b.amount) })
    setBudgets(initB)

    // History
    setPastGoals((histGoalRes.data || []) as Goal[])
    setPastBudgets((histBudgetRes.data || []) as Budget[])

    // MTD progress
    const orders = (ordersRes.data || [])
    const spendByChannel: Record<string, number> = {}
    for (const row of (spendRes.data || [])) {
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

  async function copyFromLastMonth() {
    if (!clientUUID) return
    const { data: prev } = await supabase
      .from('goals')
      .select('leads_goal,conversions_goal,revenue_goal,roas_goal,cpa_target')
      .eq('client_id', clientUUID)
      .eq('month', prevMonth)
      .limit(1)
      .single()
    if (!prev) return
    setLeadsGoal(String(prev.leads_goal ?? ''))
    setConvsGoal(String(prev.conversions_goal ?? ''))
    setRevenueGoal(String(prev.revenue_goal ?? ''))
    setRoasGoal(String(prev.roas_goal ?? ''))
    setCpaTarget(String(prev.cpa_target ?? ''))

    // Also copy budgets
    const { data: prevBudgets } = await supabase
      .from('budgets').select('channel,amount')
      .eq('client_id', clientUUID).eq('month', prevMonth)
    if (prevBudgets?.length) {
      const copied: Record<string, string> = { meta_ads: '', google_ads: '', tiktok_ads: '' }
      prevBudgets.forEach(b => { if (b.channel in copied) copied[b.channel] = String(b.amount) })
      setBudgets(copied)
    }
    setGoalMsg({ ok: true, text: 'Valores copiados do mês anterior — clique em Salvar para confirmar.' })
    setBudgetMsg({ ok: true, text: 'Orçamentos copiados — clique em Salvar para confirmar.' })
  }

  async function saveGoals(e: React.FormEvent) {
    e.preventDefault()
    setSavingGoal(true); setGoalMsg(null)
    try {
      const res = await fetch(`${API_URL}/goals/${pixelId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          month:            currentMonth,
          leads_goal:       leadsGoal   ? Number(leadsGoal)   : null,
          conversions_goal: convsGoal   ? Number(convsGoal)   : null,
          revenue_goal:     revenueGoal ? Number(revenueGoal) : null,
          roas_goal:        roasGoal    ? Number(roasGoal)    : null,
          cpa_target:       cpaTarget   ? Number(cpaTarget)   : null,
        }),
      })
      if (!res.ok) {
        const d = await res.json()
        setGoalMsg({ ok: false, text: d.detail || 'Erro ao salvar' })
      } else {
        setGoalMsg({ ok: true, text: 'Metas salvas.' })
        load()
      }
    } catch (err: unknown) {
      setGoalMsg({ ok: false, text: err instanceof Error ? err.message : 'Erro de rede' })
    }
    setSavingGoal(false)
  }

  async function saveBudgets(e: React.FormEvent) {
    e.preventDefault()
    if (!clientUUID || !agencyId) return
    setSavingBudget(true); setBudgetMsg(null)
    let err: string | null = null
    for (const { value: channel } of CHANNELS) {
      const amount   = budgets[channel] ? Number(budgets[channel]) : null
      const existing = existingBudgets.find(b => b.channel === channel)
      if (existing) {
        if (amount == null) {
          const { error: e } = await supabase.from('budgets').delete().eq('id', existing.id)
          if (e) { err = e.message; break }
        } else {
          const { error: e } = await supabase.from('budgets').update({ amount }).eq('id', existing.id)
          if (e) { err = e.message; break }
        }
      } else if (amount != null) {
        const { error: e } = await supabase.from('budgets').insert({
          agency_id: agencyId, client_id: clientUUID, month: currentMonth, channel, amount,
        })
        if (e) { err = e.message; break }
      }
    }
    setSavingBudget(false)
    if (err) { setBudgetMsg({ ok: false, text: err }); return }
    setBudgetMsg({ ok: true, text: 'Orçamentos salvos.' })
    load()
  }

  // Derived MTD metrics
  const totalSpend = progress ? Object.values(progress.spendByChannel).reduce((s, v) => s + v, 0) : 0
  const mtdRoas    = totalSpend > 0 && progress ? progress.revenue / totalSpend : null
  const mtdCpa     = progress && progress.orders > 0 ? totalSpend / progress.orders : null
  const daysDone   = new Date().getDate()
  const daysTotal  = daysInMonth(currentMonth)
  const pacePct    = daysDone / daysTotal * 100

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

  const revenueGoalN = goal?.revenue_goal ? Number(goal.revenue_goal) : null
  const roasGoalN    = goal?.roas_goal    ? Number(goal.roas_goal)    : null
  const cpaTargetN   = goal?.cpa_target   ? Number(goal.cpa_target)   : null

  return (
    <div className="p-6 space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Metas & Orçamentos</h1>
          <p className="text-xs text-slate-500 mt-0.5">{fmtMonth(currentMonth)} · dia {daysDone}/{daysTotal} ({pacePct.toFixed(0)}% do mês)</p>
        </div>
        <button
          onClick={copyFromLastMonth}
          className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white bg-[#1a1f2e] border border-[#2a2f3e] hover:border-slate-600 px-3 py-2 rounded-lg transition-colors"
        >
          <Copy size={12} /> Copiar do mês anterior
        </button>
      </div>

      {/* MTD Progress cards */}
      {progress && (revenueGoalN || roasGoalN || cpaTargetN || Object.keys(progress.spendByChannel).length > 0) && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">

          {/* Revenue vs goal */}
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

          {/* ROAS vs goal */}
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

          {/* CPA vs target */}
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

          {/* Spend vs total budget */}
          {(() => {
            const totalBudget = existingBudgets.reduce((s, b) => s + b.amount, 0)
            if (totalBudget <= 0) return null
            return (
              <ProgressCard
                label="Investimento MTD"
                value={fmtBRL(totalSpend)}
                subtitle={`orçamento ${fmtBRL(totalBudget)}`}
                pct={totalSpend / totalBudget * 100}
                pacePct={pacePct}
                barColor={totalSpend / totalBudget > 1.05 ? 'bg-red-500' : totalSpend / totalBudget >= pacePct / 100 * 0.9 ? 'bg-indigo-500' : 'bg-yellow-500'}
              />
            )
          })()}
        </div>
      )}

      {/* Per-channel spend progress */}
      {progress && existingBudgets.length > 0 && (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-slate-300 mb-4">Orçamento por canal — mês atual</h2>
          <div className="space-y-4">
            {CHANNELS.filter(ch => existingBudgets.find(b => b.channel === ch.value)).map(({ value, label }) => {
              const budget = existingBudgets.find(b => b.channel === value)?.amount || 0
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

      <div className="grid gap-6 lg:grid-cols-2">

        {/* Goals form */}
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
          <div className="flex items-center gap-2 mb-5">
            <Target size={15} className="text-indigo-400" />
            <h2 className="text-sm font-semibold text-white">Metas — {fmtMonth(currentMonth)}</h2>
          </div>
          <form onSubmit={saveGoals} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">Receita (R$)</label>
                <input type="number" min="0" step="0.01" value={revenueGoal} placeholder="—"
                  onChange={e => setRevenueGoal(e.target.value)} className={INPUT} />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">ROAS mínimo</label>
                <input type="number" min="0" step="0.01" value={roasGoal} placeholder="—"
                  onChange={e => setRoasGoal(e.target.value)} className={INPUT} />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">CPA máximo (R$)</label>
                <input type="number" min="0" step="0.01" value={cpaTarget} placeholder="—"
                  onChange={e => setCpaTarget(e.target.value)} className={INPUT} />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">Conversões</label>
                <input type="number" min="0" value={convsGoal} placeholder="—"
                  onChange={e => setConvsGoal(e.target.value)} className={INPUT} />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-slate-300 mb-1.5">Leads</label>
                <input type="number" min="0" value={leadsGoal} placeholder="—"
                  onChange={e => setLeadsGoal(e.target.value)} className={INPUT} />
              </div>
            </div>
            {goalMsg && <Msg msg={goalMsg} />}
            <button type="submit" disabled={savingGoal}
              className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors">
              {savingGoal ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
              Salvar metas
            </button>
          </form>
        </div>

        {/* Budgets form */}
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
          <div className="flex items-center gap-2 mb-5">
            <DollarSign size={15} className="text-indigo-400" />
            <h2 className="text-sm font-semibold text-white">Orçamentos — {fmtMonth(currentMonth)}</h2>
          </div>
          <form onSubmit={saveBudgets} className="space-y-4">
            {CHANNELS.map(({ value, label }) => (
              <div key={value}>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">{label} (R$)</label>
                <input type="number" min="0" step="0.01" value={budgets[value]} placeholder="—"
                  onChange={e => setBudgets(prev => ({ ...prev, [value]: e.target.value }))}
                  className={INPUT} />
              </div>
            ))}
            {budgetMsg && <Msg msg={budgetMsg} />}
            <button type="submit" disabled={savingBudget}
              className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors">
              {savingBudget ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
              Salvar orçamentos
            </button>
          </form>
        </div>

      </div>

      {/* Goal history */}
      {pastGoals.length > 0 && (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-[#2a2f3e]">
            <h2 className="text-sm font-semibold text-slate-300">Histórico de metas</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#2a2f3e]">
                  {['Mês', 'Receita', 'ROAS', 'CPA máx.', 'Conversões', 'Leads'].map(h => (
                    <th key={h} className="text-left px-5 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {pastGoals.map(g => (
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
                    <td className="px-5 py-3 text-slate-400">{g.conversions_goal ?? '—'}</td>
                    <td className="px-5 py-3 text-slate-400">{g.leads_goal ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Budget history */}
      {pastBudgets.length > 0 && (
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
          <button
            onClick={() => setShowBudgetHistory(v => !v)}
            className="w-full flex items-center justify-between px-5 py-4 hover:bg-white/[0.02] transition-colors"
          >
            <h2 className="text-sm font-semibold text-slate-300">Histórico de orçamentos</h2>
            {showBudgetHistory ? <ChevronUp size={14} className="text-slate-500" /> : <ChevronDown size={14} className="text-slate-500" />}
          </button>
          {showBudgetHistory && (
            <div className="overflow-x-auto border-t border-[#2a2f3e]">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#2a2f3e]">
                    {['Mês', 'Canal', 'Orçamento'].map(h => (
                      <th key={h} className="text-left px-5 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {pastBudgets.map(b => (
                    <tr key={b.id} className={`border-b border-[#2a2f3e] last:border-0 ${b.month === currentMonth ? 'bg-indigo-500/5' : 'hover:bg-[#252a3a]'}`}>
                      <td className="px-5 py-3 text-slate-200 font-medium whitespace-nowrap">
                        {fmtMonth(b.month)}
                        {b.month === currentMonth && (
                          <span className="ml-2 text-xs bg-indigo-500/20 text-indigo-400 px-1.5 py-0.5 rounded">atual</span>
                        )}
                      </td>
                      <td className="px-5 py-3 text-slate-400">{CHANNELS.find(c => c.value === b.channel)?.label || b.channel}</td>
                      <td className="px-5 py-3 text-slate-400 whitespace-nowrap font-medium">{fmtBRL(b.amount)}</td>
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
