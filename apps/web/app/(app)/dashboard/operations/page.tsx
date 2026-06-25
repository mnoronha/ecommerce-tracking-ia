'use client'

import { useEffect, useState, useCallback } from 'react'
import { Clock, Loader2, RefreshCw, PlusCircle, FileText, Zap, Eye, ThumbsUp, Globe, Wrench, BarChart2 } from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

interface Client { pixel_id: string; name: string }

interface TimeLog {
  id: string
  activity_type: string
  duration_minutes: number
  description: string | null
  logged_at: string
}

const ACTIVITY_TYPES = [
  { value: 'briefing',   label: 'Briefing',     icon: FileText,  minutes: 30  },
  { value: 'generation', label: 'Geração IA',   icon: Zap,       minutes: 5   },
  { value: 'review',     label: 'Revisão',      icon: Eye,       minutes: 60  },
  { value: 'approval',   label: 'Aprovação',    icon: ThumbsUp,  minutes: 5   },
  { value: 'publish',    label: 'Publicação',   icon: Globe,     minutes: 15  },
  { value: 'technical',  label: 'Técnico',      icon: Wrench,    minutes: 30  },
  { value: 'reporting',  label: 'Relatório',    icon: BarChart2, minutes: 45  },
  { value: 'manual',     label: 'Manual',       icon: Clock,     minutes: 30  },
]

const ACT_LABELS: Record<string, string> = Object.fromEntries(ACTIVITY_TYPES.map(a => [a.value, a.label]))
const ACT_COLORS: Record<string, string> = {
  briefing:   'text-indigo-400',
  generation: 'text-purple-400',
  review:     'text-yellow-400',
  approval:   'text-emerald-400',
  publish:    'text-blue-400',
  technical:  'text-orange-400',
  reporting:  'text-pink-400',
  manual:     'text-slate-400',
}

function fmtTime(mins: number) {
  if (mins < 60) return `${mins}min`
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return m > 0 ? `${h}h ${m}min` : `${h}h`
}

export default function OperationsDashboardPage() {
  const [clients,    setClients]    = useState<Client[]>([])
  const [selClient,  setSelClient]  = useState('')
  const [logs,       setLogs]       = useState<TimeLog[]>([])
  const [loading,    setLoading]    = useState(false)
  const [showAdd,    setShowAdd]    = useState(false)
  const [actType,    setActType]    = useState('briefing')
  const [actMins,    setActMins]    = useState(30)
  const [actDesc,    setActDesc]    = useState('')
  const [saving,     setSaving]     = useState(false)

  useEffect(() => {
    fetch(`${API}/setup/clients`)
      .then(r => r.json())
      .then(d => { const list = Array.isArray(d) ? d : d.clients || []; setClients(list) })
      .catch(() => {})
  }, [])

  const loadLogs = useCallback(async () => {
    if (!selClient) return
    setLoading(true)
    try {
      const r = await fetch(`${API}/technical/${selClient}/time-logs?limit=50`)
      setLogs(Array.isArray(await r.json()) ? await fetch(`${API}/technical/${selClient}/time-logs?limit=50`).then(r => r.json()) : [])
    } catch { setLogs([]) }
    finally { setLoading(false) }
  }, [selClient])

  useEffect(() => { loadLogs() }, [loadLogs])

  async function addLog() {
    if (!selClient) return
    setSaving(true)
    try {
      await fetch(`${API}/technical/${selClient}/time-logs`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ activity_type: actType, duration_minutes: actMins, description: actDesc || undefined }),
      })
      setShowAdd(false)
      setActDesc('')
      loadLogs()
    } finally { setSaving(false) }
  }

  // Aggregations
  const thisWeek = logs.filter(l => {
    const d = new Date(l.logged_at)
    const now = new Date()
    const diff = (now.getTime() - d.getTime()) / (1000 * 60 * 60 * 24)
    return diff <= 7
  })
  const weekMins  = thisWeek.reduce((s, l) => s + l.duration_minutes, 0)
  const totalMins = logs.reduce((s, l) => s + l.duration_minutes, 0)

  // By activity
  const byActivity: Record<string, number> = {}
  for (const log of logs) {
    byActivity[log.activity_type] = (byActivity[log.activity_type] || 0) + log.duration_minutes
  }

  // Capacity indicator (assuming 10h/week capacity)
  const WEEK_CAP_MINS = 600
  const capPct = Math.min((weekMins / WEEK_CAP_MINS) * 100, 100)
  const capStatus = capPct < 70 ? { label: 'Abaixo da capacidade', color: 'text-emerald-400' }
    : capPct < 90 ? { label: 'Em ritmo', color: 'text-yellow-400' }
    : { label: 'Acima da capacidade', color: 'text-red-400' }

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-bold text-white flex items-center gap-2">
            <Clock size={20} className="text-indigo-400" />
            Dashboard de Operações
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">Registro de tempo e visão de capacidade operacional</p>
        </div>
      </div>

      {/* Client selector */}
      <select
        value={selClient}
        onChange={e => setSelClient(e.target.value)}
        className="h-9 px-3 bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg text-sm text-white focus:outline-none"
      >
        <option value="">Selecionar cliente...</option>
        {clients.map(c => (
          <option key={c.pixel_id} value={c.pixel_id}>{c.name}</option>
        ))}
      </select>

      {selClient && (
        <>
          {/* KPIs */}
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-[#1a1f2e] rounded-xl p-4">
              <p className="text-2xl font-bold text-white">{fmtTime(weekMins)}</p>
              <p className="text-xs text-slate-500 mt-1">Esta semana</p>
              <p className={`text-xs mt-2 font-medium ${capStatus.color}`}>{capStatus.label}</p>
            </div>
            <div className="bg-[#1a1f2e] rounded-xl p-4">
              <p className="text-2xl font-bold text-white">{fmtTime(totalMins)}</p>
              <p className="text-xs text-slate-500 mt-1">Total registrado</p>
            </div>
            <div className="bg-[#1a1f2e] rounded-xl p-4">
              <p className="text-2xl font-bold text-white">{logs.length}</p>
              <p className="text-xs text-slate-500 mt-1">Registros totais</p>
            </div>
          </div>

          {/* Capacity bar */}
          <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl p-4">
            <div className="flex items-center justify-between mb-2">
              <p className="text-xs text-slate-400">Capacidade semanal (10h)</p>
              <p className={`text-xs font-semibold ${capStatus.color}`}>{capPct.toFixed(0)}%</p>
            </div>
            <div className="h-2 bg-[#0f1117] rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  capPct < 70 ? 'bg-emerald-500' : capPct < 90 ? 'bg-yellow-500' : 'bg-red-500'
                }`}
                style={{ width: `${capPct}%` }}
              />
            </div>
          </div>

          {/* By activity */}
          {Object.keys(byActivity).length > 0 && (
            <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl p-4">
              <p className="text-xs text-slate-400 font-medium mb-3">Tempo por atividade</p>
              <div className="space-y-2">
                {Object.entries(byActivity).sort((a, b) => b[1] - a[1]).map(([type, mins]) => {
                  const pct = Math.min((mins / totalMins) * 100, 100)
                  return (
                    <div key={type} className="flex items-center gap-3">
                      <span className={`text-xs w-24 shrink-0 ${ACT_COLORS[type] || 'text-slate-400'}`}>
                        {ACT_LABELS[type] || type}
                      </span>
                      <div className="flex-1 bg-[#0f1117] rounded-full h-1.5">
                        <div className="h-1.5 rounded-full bg-indigo-500" style={{ width: `${pct}%` }} />
                      </div>
                      <span className="text-xs text-slate-400 w-14 text-right">{fmtTime(mins)}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Add log */}
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-white">Histórico de tempo</h2>
            <button onClick={() => setShowAdd(s => !s)}
              className="h-8 px-3 bg-indigo-600 hover:bg-indigo-500 rounded text-xs text-white flex items-center gap-1.5 transition-colors">
              <PlusCircle size={12} /> Registrar tempo
            </button>
          </div>

          {showAdd && (
            <div className="bg-[#151b27] border border-indigo-500/20 rounded-xl p-4 space-y-4">
              <p className="text-sm font-medium text-white">Nova entrada de tempo</p>
              <div className="grid grid-cols-3 gap-2">
                {ACTIVITY_TYPES.map(a => (
                  <button key={a.value} onClick={() => { setActType(a.value); setActMins(a.minutes) }}
                    className={`text-xs px-3 py-2 rounded-lg border transition-colors ${
                      actType === a.value
                        ? 'border-indigo-500 bg-indigo-500/20 text-white'
                        : 'border-[#2a2f3e] bg-[#1a1f2e] text-slate-400 hover:text-white'
                    }`}>
                    {a.label}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-3">
                <div>
                  <label className="text-[10px] text-slate-500 block mb-1">Minutos</label>
                  <input type="number" value={actMins} onChange={e => setActMins(Number(e.target.value))} min={1}
                    className="w-20 h-8 px-2 bg-[#1a1f2e] border border-[#2a2f3e] rounded text-sm text-white focus:outline-none focus:border-indigo-500" />
                </div>
                <div className="flex-1">
                  <label className="text-[10px] text-slate-500 block mb-1">Descrição (opcional)</label>
                  <input type="text" value={actDesc} onChange={e => setActDesc(e.target.value)}
                    placeholder="Ex: revisão da peça guia de tênis de corrida"
                    className="w-full h-8 px-3 bg-[#1a1f2e] border border-[#2a2f3e] rounded text-sm text-slate-300 focus:outline-none focus:border-indigo-500" />
                </div>
              </div>
              <div className="flex gap-2">
                <button onClick={() => setShowAdd(false)}
                  className="flex-1 h-9 bg-[#1a1f2e] border border-[#2a2f3e] rounded text-sm text-slate-300 hover:text-white transition-colors">
                  Cancelar
                </button>
                <button onClick={addLog} disabled={saving}
                  className="flex-1 h-9 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded text-sm text-white flex items-center justify-center gap-2 transition-colors">
                  {saving ? <Loader2 size={14} className="animate-spin" /> : null} Salvar
                </button>
              </div>
            </div>
          )}

          {/* Logs table */}
          {loading ? (
            <div className="flex justify-center py-8">
              <Loader2 size={20} className="animate-spin text-slate-500" />
            </div>
          ) : logs.length === 0 ? (
            <p className="text-center text-slate-600 text-sm py-8">Nenhum registro ainda. Clique em "Registrar tempo".</p>
          ) : (
            <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl overflow-hidden">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-[#2a2f3e]">
                    <th className="text-left px-4 py-3 text-slate-500 font-medium">Atividade</th>
                    <th className="text-left px-4 py-3 text-slate-500 font-medium">Descrição</th>
                    <th className="text-right px-4 py-3 text-slate-500 font-medium">Tempo</th>
                    <th className="text-right px-4 py-3 text-slate-500 font-medium">Data</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map(log => (
                    <tr key={log.id} className="border-b border-[#1a1f2e] hover:bg-[#1a1f2e]/50">
                      <td className={`px-4 py-2.5 font-medium ${ACT_COLORS[log.activity_type] || 'text-slate-400'}`}>
                        {ACT_LABELS[log.activity_type] || log.activity_type}
                      </td>
                      <td className="px-4 py-2.5 text-slate-400 max-w-xs truncate">{log.description || '—'}</td>
                      <td className="px-4 py-2.5 text-right text-white tabular-nums">{fmtTime(log.duration_minutes)}</td>
                      <td className="px-4 py-2.5 text-right text-slate-500">
                        {new Date(log.logged_at).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
