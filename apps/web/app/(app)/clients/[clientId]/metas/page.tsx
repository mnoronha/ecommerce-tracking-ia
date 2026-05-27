'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { createSupabaseBrowserClient } from '@/lib/supabase-browser'
import { Target, DollarSign, Save, Loader2, CheckCircle, AlertCircle } from 'lucide-react'

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

function fmtBRL(n: number | null) {
  if (n == null) return '—'
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 })
}

type Goal = {
  id: string
  month: string
  leads_goal: number | null
  conversions_goal: number | null
  revenue_goal: number | null
  roas_goal: number | null
}

type Budget = {
  id: string
  month: string
  channel: string
  amount: number
}

const INPUT = 'w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-600 outline-none transition-colors focus:border-indigo-500'

export default function MetasPage() {
  const params   = useParams()
  const pixelId  = params.clientId as string
  const supabase = createSupabaseBrowserClient()

  const [clientUUID, setClientUUID] = useState<string | null>(null)
  const [agencyId,   setAgencyId]   = useState<string | null>(null)
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState<string | null>(null)

  const currentMonth = monthStart(0)

  // Goal form state
  const [goal,        setGoal]        = useState<Goal | null>(null)
  const [leadsGoal,   setLeadsGoal]   = useState('')
  const [convsGoal,   setConvsGoal]   = useState('')
  const [revenueGoal, setRevenueGoal] = useState('')
  const [roasGoal,    setRoasGoal]    = useState('')
  const [savingGoal,  setSavingGoal]  = useState(false)
  const [goalMsg,     setGoalMsg]     = useState<{ ok: boolean; text: string } | null>(null)

  // Budget form state
  const [budgets,      setBudgets]      = useState<Record<string, string>>({ meta_ads: '', google_ads: '', tiktok_ads: '' })
  const [existingBudgets, setExistingBudgets] = useState<Budget[]>([])
  const [savingBudget, setSavingBudget] = useState(false)
  const [budgetMsg,    setBudgetMsg]    = useState<{ ok: boolean; text: string } | null>(null)

  // Past goals (last 6 months including current)
  const [pastGoals, setPastGoals] = useState<Goal[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)

    const { data: client } = await supabase
      .from('clients')
      .select('id, agency_id')
      .eq('pixel_id', pixelId)
      .limit(1)
      .single()

    if (!client) {
      setError('Cliente não encontrado')
      setLoading(false)
      return
    }
    setClientUUID(client.id)
    setAgencyId(client.agency_id)

    // Load current month goal
    const { data: goalRow } = await supabase
      .from('goals')
      .select('id, month, leads_goal, conversions_goal, revenue_goal, roas_goal')
      .eq('client_id', client.id)
      .eq('month', currentMonth)
      .limit(1)
      .single()

    if (goalRow) {
      setGoal(goalRow)
      setLeadsGoal(String(goalRow.leads_goal ?? ''))
      setConvsGoal(String(goalRow.conversions_goal ?? ''))
      setRevenueGoal(String(goalRow.revenue_goal ?? ''))
      setRoasGoal(String(goalRow.roas_goal ?? ''))
    } else {
      setGoal(null)
      setLeadsGoal(''); setConvsGoal(''); setRevenueGoal(''); setRoasGoal('')
    }

    // Load current month budgets
    const { data: budgetRows } = await supabase
      .from('budgets')
      .select('id, month, channel, amount')
      .eq('client_id', client.id)
      .eq('month', currentMonth)

    const brows = (budgetRows || []) as Budget[]
    setExistingBudgets(brows)
    const init: Record<string, string> = { meta_ads: '', google_ads: '', tiktok_ads: '' }
    brows.forEach(b => { if (b.channel in init) init[b.channel] = String(b.amount) })
    setBudgets(init)

    // Load past 6 months goals (history)
    const months = Array.from({ length: 6 }, (_, i) => monthStart(i))
    const { data: histRows } = await supabase
      .from('goals')
      .select('id, month, leads_goal, conversions_goal, revenue_goal, roas_goal')
      .eq('client_id', client.id)
      .in('month', months)
      .order('month', { ascending: false })

    setPastGoals((histRows || []) as Goal[])
    setLoading(false)
  }, [pixelId, currentMonth])

  useEffect(() => { load() }, [load])

  async function saveGoals(e: React.FormEvent) {
    e.preventDefault()
    if (!clientUUID || !agencyId) return
    setSavingGoal(true)
    setGoalMsg(null)

    const payload = {
      agency_id:        agencyId,
      client_id:        clientUUID,
      month:            currentMonth,
      leads_goal:       leadsGoal   ? Number(leadsGoal)   : null,
      conversions_goal: convsGoal   ? Number(convsGoal)   : null,
      revenue_goal:     revenueGoal ? Number(revenueGoal) : null,
      roas_goal:        roasGoal    ? Number(roasGoal)    : null,
    }

    const { error: err } = goal
      ? await supabase.from('goals').update(payload).eq('id', goal.id)
      : await supabase.from('goals').insert(payload)

    setSavingGoal(false)
    if (err) { setGoalMsg({ ok: false, text: err.message }); return }
    setGoalMsg({ ok: true, text: 'Metas salvas.' })
    load()
  }

  async function saveBudgets(e: React.FormEvent) {
    e.preventDefault()
    if (!clientUUID || !agencyId) return
    setSavingBudget(true)
    setBudgetMsg(null)

    let err: string | null = null
    for (const { value: channel } of CHANNELS) {
      const raw = budgets[channel]
      const amount = raw ? Number(raw) : null
      const existing = existingBudgets.find(b => b.channel === channel)

      if (existing) {
        if (amount == null) {
          // delete if cleared
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

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-xl font-bold text-white">Metas & Orçamentos</h1>
        <p className="text-xs text-slate-500 mt-0.5">{fmtMonth(currentMonth)}</p>
      </div>

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
                <label className="block text-xs font-medium text-slate-300 mb-1.5">Leads</label>
                <input
                  type="number" min="0" value={leadsGoal} placeholder="—"
                  onChange={e => setLeadsGoal(e.target.value)}
                  className={INPUT}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">Conversões</label>
                <input
                  type="number" min="0" value={convsGoal} placeholder="—"
                  onChange={e => setConvsGoal(e.target.value)}
                  className={INPUT}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">Receita (R$)</label>
                <input
                  type="number" min="0" step="0.01" value={revenueGoal} placeholder="—"
                  onChange={e => setRevenueGoal(e.target.value)}
                  className={INPUT}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">ROAS mínimo</label>
                <input
                  type="number" min="0" step="0.01" value={roasGoal} placeholder="—"
                  onChange={e => setRoasGoal(e.target.value)}
                  className={INPUT}
                />
              </div>
            </div>

            {goalMsg && (
              <div className={`flex items-center gap-2 text-xs rounded-lg px-3 py-2.5 ${
                goalMsg.ok
                  ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                  : 'bg-red-500/10 border border-red-500/20 text-red-400'
              }`}>
                {goalMsg.ok ? <CheckCircle size={12} /> : <AlertCircle size={12} />}
                {goalMsg.text}
              </div>
            )}

            <button
              type="submit"
              disabled={savingGoal}
              className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
            >
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
                <input
                  type="number" min="0" step="0.01"
                  value={budgets[value]}
                  placeholder="—"
                  onChange={e => setBudgets(prev => ({ ...prev, [value]: e.target.value }))}
                  className={INPUT}
                />
              </div>
            ))}

            {budgetMsg && (
              <div className={`flex items-center gap-2 text-xs rounded-lg px-3 py-2.5 ${
                budgetMsg.ok
                  ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                  : 'bg-red-500/10 border border-red-500/20 text-red-400'
              }`}>
                {budgetMsg.ok ? <CheckCircle size={12} /> : <AlertCircle size={12} />}
                {budgetMsg.text}
              </div>
            )}

            <button
              type="submit"
              disabled={savingBudget}
              className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
            >
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
                  {['Mês', 'Leads', 'Conversões', 'Receita', 'ROAS mín.'].map(h => (
                    <th key={h} className="text-left px-5 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {pastGoals.map(g => (
                  <tr
                    key={g.id}
                    className={`border-b border-[#2a2f3e] last:border-0 ${g.month === currentMonth ? 'bg-indigo-500/5' : 'hover:bg-[#252a3a]'}`}
                  >
                    <td className="px-5 py-3 text-slate-200 font-medium whitespace-nowrap">
                      {fmtMonth(g.month)}
                      {g.month === currentMonth && (
                        <span className="ml-2 text-xs bg-indigo-500/20 text-indigo-400 px-1.5 py-0.5 rounded">atual</span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-slate-400">{g.leads_goal ?? '—'}</td>
                    <td className="px-5 py-3 text-slate-400">{g.conversions_goal ?? '—'}</td>
                    <td className="px-5 py-3 text-slate-400 whitespace-nowrap">{fmtBRL(g.revenue_goal)}</td>
                    <td className="px-5 py-3 text-slate-400">
                      {g.roas_goal != null ? `${g.roas_goal}x` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
