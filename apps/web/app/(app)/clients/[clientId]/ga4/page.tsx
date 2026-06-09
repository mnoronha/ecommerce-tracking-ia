'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { Loader2, RefreshCw, BarChart2, Users, MousePointerClick, ShoppingCart, AlertTriangle, Settings } from 'lucide-react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer,
} from 'recharts'
import { useDatePeriod, periodToQuery } from '@/lib/use-date-range'
import { PeriodPicker } from '@/components/PeriodPicker'
import Link from 'next/link'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Summary {
  sessions: number
  users: number
  conversions: number
  revenue: number
}

interface ChannelRow {
  channel: string
  sessions: number
  users: number
  conversions: number
  revenue: number
}

interface DayRow {
  date: string
  sessions: number
  users: number
  conversions: number
}

interface GA4Report {
  summary: Summary
  by_channel: ChannelRow[]
  daily_series: DayRow[]
  period: { start: string; end: string }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(n: number) {
  return n.toLocaleString('pt-BR')
}
function fmtR(n: number) {
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 })
}

const CHANNEL_COLORS: Record<string, string> = {
  'Organic Search':  '#10b981',
  'Paid Search':     '#6366f1',
  'Organic Social':  '#f59e0b',
  'Paid Social':     '#ec4899',
  'Direct':          '#64748b',
  'Email':           '#0ea5e9',
  'Referral':        '#8b5cf6',
  'Display':         '#f97316',
}

function channelColor(ch: string) {
  return CHANNEL_COLORS[ch] || '#94a3b8'
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function GA4Page() {
  const { clientId } = useParams<{ clientId: string }>()
  const { period, from, to, setPreset, setCustom } = useDatePeriod()

  const [data,    setData]    = useState<GA4Report | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const qs = periodToQuery(period, from, to)
      const res = await fetch(`${API_URL}/ga4/${clientId}/report?${qs}`)
      if (res.status === 403) {
        setError('disabled')
        return
      }
      if (res.status === 400) {
        setError('no_property')
        return
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError(body.detail || `Erro ${res.status}`)
        return
      }
      setData(await res.json())
    } catch {
      setError('Falha de rede')
    } finally {
      setLoading(false)
    }
  }, [clientId, period, from, to])

  useEffect(() => { load() }, [load])

  // ── Disabled state ────────────────────────────────────────────────────────

  if (!loading && error === 'disabled') {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 text-center px-4">
        <BarChart2 size={40} className="text-slate-600" />
        <h2 className="text-lg font-semibold text-white">Relatórios GA4 desativados</h2>
        <p className="text-sm text-slate-400 max-w-sm">
          Ative a opção "Relatórios GA4 no dashboard" nas configurações deste cliente e preencha o Property ID.
        </p>
        <Link href={`/clients/${clientId}/settings`}
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors">
          <Settings size={14} /> Ir para Settings
        </Link>
      </div>
    )
  }

  if (!loading && error === 'no_property') {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 text-center px-4">
        <AlertTriangle size={40} className="text-amber-500" />
        <h2 className="text-lg font-semibold text-white">Property ID não configurado</h2>
        <p className="text-sm text-slate-400 max-w-sm">
          Preencha o campo "Property ID" nas settings com o número da propriedade GA4 (aparece na URL do GA4, ex: 267533911).
        </p>
        <Link href={`/clients/${clientId}/settings`}
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors">
          <Settings size={14} /> Ir para Settings
        </Link>
      </div>
    )
  }

  if (!loading && error) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-3 text-center">
        <AlertTriangle size={32} className="text-red-400" />
        <p className="text-sm text-red-400">{error}</p>
        <button onClick={load} className="text-xs text-slate-400 hover:text-white underline">Tentar novamente</button>
      </div>
    )
  }

  // ── Loading ───────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <Loader2 size={28} className="animate-spin text-indigo-400" />
      </div>
    )
  }

  if (!data) return null

  const { summary, by_channel, daily_series } = data
  const totalSessions = summary.sessions || 1

  return (
    <div className="space-y-6 p-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Google Analytics 4</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            {data.period.start} → {data.period.end}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <PeriodPicker period={period} from={from} to={to} onPreset={setPreset} onCustom={setCustom} />
          <button onClick={load} className="p-2 text-slate-400 hover:text-white border border-[#2a2f3e] rounded-lg hover:border-slate-500 transition-colors">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { icon: BarChart2,        label: 'Sessões',     value: fmt(summary.sessions),     color: 'text-indigo-400' },
          { icon: Users,            label: 'Usuários',    value: fmt(summary.users),         color: 'text-emerald-400' },
          { icon: MousePointerClick,label: 'Conversões',  value: fmt(summary.conversions),   color: 'text-amber-400' },
          { icon: ShoppingCart,     label: 'Receita GA4', value: fmtR(summary.revenue),      color: 'text-pink-400' },
        ].map(({ icon: Icon, label, value, color }) => (
          <div key={label} className="bg-[#0f1117] border border-[#2a2f3e] rounded-xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <Icon size={14} className={color} />
              <span className="text-xs text-slate-500">{label}</span>
            </div>
            <p className="text-2xl font-bold text-white">{value}</p>
          </div>
        ))}
      </div>

      {/* Série diária */}
      {daily_series.length > 0 && (
        <div className="bg-[#0f1117] border border-[#2a2f3e] rounded-xl p-5">
          <h2 className="text-sm font-semibold text-white mb-4">Sessões por dia</h2>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={daily_series} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="ga4sessions" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#6366f1" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2435" />
              <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 11 }}
                tickFormatter={d => d.slice(5)} />
              <YAxis tick={{ fill: '#64748b', fontSize: 11 }} width={40} />
              <Tooltip
                contentStyle={{ background: '#1a1f2e', border: '1px solid #2a2f3e', borderRadius: 8 }}
                labelStyle={{ color: '#94a3b8' }}
                itemStyle={{ color: '#e2e8f0' }}
              />
              <Area type="monotone" dataKey="sessions" stroke="#6366f1" fill="url(#ga4sessions)"
                strokeWidth={2} dot={false} name="Sessões" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Por canal */}
      {by_channel.length > 0 && (
        <div className="bg-[#0f1117] border border-[#2a2f3e] rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-[#2a2f3e]">
            <h2 className="text-sm font-semibold text-white">Por canal</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#2a2f3e]">
                  {['Canal', 'Sessões', '% Sessões', 'Usuários', 'Conversões', 'Receita'].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs text-slate-500 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {by_channel.map((row, i) => (
                  <tr key={i} className="border-b border-[#1a1f2e] hover:bg-[#1a1f2e] transition-colors">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="w-2 h-2 rounded-full flex-shrink-0"
                          style={{ backgroundColor: channelColor(row.channel) }} />
                        <span className="text-slate-300 font-medium">{row.channel}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-slate-300">{fmt(row.sessions)}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="w-16 h-1.5 bg-[#2a2f3e] rounded-full overflow-hidden">
                          <div className="h-full rounded-full"
                            style={{
                              width: `${Math.round((row.sessions / totalSessions) * 100)}%`,
                              backgroundColor: channelColor(row.channel),
                            }} />
                        </div>
                        <span className="text-slate-400 text-xs">
                          {Math.round((row.sessions / totalSessions) * 100)}%
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-slate-300">{fmt(row.users)}</td>
                    <td className="px-4 py-3 text-slate-300">{fmt(row.conversions)}</td>
                    <td className="px-4 py-3 text-slate-300">
                      {row.revenue > 0 ? fmtR(row.revenue) : '—'}
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
