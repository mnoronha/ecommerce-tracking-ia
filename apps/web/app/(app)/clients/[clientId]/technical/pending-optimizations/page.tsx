'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { Wrench, Loader2, RefreshCw, ChevronRight, AlertTriangle, CheckCircle, Info } from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

interface OptItem {
  id: string
  type: string
  title: string
  description: string
  severity: 'high' | 'medium' | 'low'
  estimated_impact: string
  estimated_time: string
  status: string
  action_data: Record<string, unknown> | null
  created_at: string
}

const SEV_STYLE: Record<string, string> = {
  high:   'border-l-red-500',
  medium: 'border-l-yellow-500',
  low:    'border-l-slate-500',
}
const SEV_LABEL: Record<string, string> = { high: 'Alta', medium: 'Média', low: 'Baixa' }
const SEV_TEXT:  Record<string, string> = { high: 'text-red-400', medium: 'text-yellow-400', low: 'text-slate-400' }

const TYPE_LABELS: Record<string, string> = {
  schema_markup:    'Schema Markup',
  merchant_feed:    'Merchant Center',
  robots_txt:       'robots.txt',
  llms_txt:         'llms.txt',
  core_web_vitals:  'Core Web Vitals',
  internal_links:   'Links Internos',
}

const TYPE_ROUTES: Record<string, string> = {
  schema_markup:   'technical/schema-audit',
  llms_txt:        'technical/llms-txt',
  merchant_feed:   'merchant-center',
  core_web_vitals: 'technical/history',
}

export default function PendingOptimizationsPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [items,    setItems]    = useState<OptItem[]>([])
  const [total,    setTotal]    = useState(0)
  const [loading,  setLoading]  = useState(true)
  const [filter,   setFilter]   = useState('')
  const [applying, setApplying] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const qs = filter ? `?severity=${filter}` : ''
      const r  = await fetch(`${API}/technical/${clientId}/pending${qs}`)
      const d  = await r.json()
      setItems(d.items || [])
      setTotal(d.total || 0)
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [clientId, filter])

  useEffect(() => { load() }, [load])

  async function markApplied(id: string) {
    if (id.startsWith('schema_') || id.startsWith('merchant_')) return
    setApplying(id)
    try {
      await fetch(`${API}/technical/${clientId}/optimizations/${id}`, {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ status: 'applied' }),
      })
      await load()
    } finally { setApplying(null) }
  }

  const highCount   = items.filter(i => i.severity === 'high').length
  const medCount    = items.filter(i => i.severity === 'medium').length
  const lowCount    = items.filter(i => i.severity === 'low').length

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-bold text-white flex items-center gap-2">
            <Wrench size={20} className="text-indigo-400" />
            Otimizações Pendentes
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">Lista priorizada de melhorias técnicas para aumentar a visibilidade em IA</p>
        </div>
        <button onClick={load} disabled={loading}
          className="h-8 w-8 flex items-center justify-center bg-[#1a1f2e] border border-[#2a2f3e] rounded hover:bg-[#252a3a] transition-colors">
          {loading ? <Loader2 size={13} className="animate-spin text-slate-400" /> : <RefreshCw size={13} className="text-slate-400" />}
        </button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: 'Alta prioridade', value: highCount,  color: 'text-red-400' },
          { label: 'Média prioridade', value: medCount,  color: 'text-yellow-400' },
          { label: 'Baixa prioridade', value: lowCount,  color: 'text-slate-400' },
        ].map(s => (
          <div key={s.label} className="bg-[#1a1f2e] rounded-xl p-4">
            <p className={`text-2xl font-bold ${s.color}`}>{s.value}</p>
            <p className="text-xs text-slate-500 mt-1">{s.label}</p>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2">
        {[
          { value: '',       label: 'Todas' },
          { value: 'high',   label: 'Alta' },
          { value: 'medium', label: 'Média' },
          { value: 'low',    label: 'Baixa' },
        ].map(f => (
          <button key={f.value} onClick={() => setFilter(f.value)}
            className={`text-xs px-3 py-1.5 rounded-md transition-colors ${
              filter === f.value ? 'bg-indigo-600 text-white' : 'bg-[#1a1f2e] text-slate-400 hover:text-white'
            }`}>
            {f.label}
          </button>
        ))}
      </div>

      {/* Items */}
      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 size={24} className="animate-spin text-indigo-400" />
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-16">
          <CheckCircle size={36} className="text-emerald-400 mx-auto mb-3" />
          <p className="text-slate-400 text-sm font-medium">
            {filter ? 'Nenhuma otimização neste filtro' : 'Nenhuma otimização pendente 🎉'}
          </p>
          <p className="text-slate-600 text-xs mt-1">Execute uma auditoria de schema ou verifique o robots.txt para identificar melhorias.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map(item => (
            <div key={item.id}
              className={`bg-[#151b27] border border-[#2a2f3e] border-l-4 ${SEV_STYLE[item.severity]} rounded-xl p-4`}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <span className={`text-[10px] font-semibold ${SEV_TEXT[item.severity]}`}>
                      {SEV_LABEL[item.severity]}
                    </span>
                    <span className="text-[10px] text-slate-600 bg-[#1a1f2e] px-2 py-0.5 rounded">
                      {TYPE_LABELS[item.type] || item.type}
                    </span>
                  </div>
                  <p className="text-sm font-medium text-white leading-snug">{item.title}</p>
                  {item.description && (
                    <p className="text-xs text-slate-400 mt-1 line-clamp-2">{item.description}</p>
                  )}
                  <div className="flex items-center gap-4 mt-2 text-[10px] text-slate-600">
                    {item.estimated_impact && (
                      <span>Impacto: <span className="text-slate-400">{item.estimated_impact}</span></span>
                    )}
                    {item.estimated_time && (
                      <span>Tempo: <span className="text-slate-400">{item.estimated_time}</span></span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {TYPE_ROUTES[item.type] && (
                    <a href={`/clients/${clientId}/${TYPE_ROUTES[item.type]}`}
                      className="h-7 px-2.5 text-xs bg-[#1a1f2e] border border-[#2a2f3e] rounded text-slate-300 hover:text-white flex items-center gap-1 transition-colors">
                      Ver <ChevronRight size={11} />
                    </a>
                  )}
                  {!item.id.startsWith('schema_') && !item.id.startsWith('merchant_') && (
                    <button
                      onClick={() => markApplied(item.id)}
                      disabled={applying === item.id}
                      className="h-7 px-2.5 text-xs bg-emerald-600/20 hover:bg-emerald-600/40 border border-emerald-500/30 rounded text-emerald-400 flex items-center gap-1 transition-colors disabled:opacity-50">
                      {applying === item.id ? <Loader2 size={11} className="animate-spin" /> : <CheckCircle size={11} />}
                      Feito
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
