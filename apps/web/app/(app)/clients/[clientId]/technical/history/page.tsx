'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { History, Loader2, RefreshCw, CheckCircle } from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

interface HistoryItem {
  id: string
  type: string
  title: string
  description: string | null
  before_value: string | null
  after_value: string | null
  applied_at: string
}

const TYPE_COLORS: Record<string, string> = {
  schema_markup:   'bg-indigo-500/20 text-indigo-400',
  llms_txt:        'bg-purple-500/20 text-purple-400',
  robots_txt:      'bg-amber-500/20 text-amber-400',
  merchant_feed:   'bg-blue-500/20 text-blue-400',
  core_web_vitals: 'bg-emerald-500/20 text-emerald-400',
}

const TYPE_LABELS: Record<string, string> = {
  schema_markup:   'Schema',
  llms_txt:        'llms.txt',
  robots_txt:      'robots.txt',
  merchant_feed:   'Merchant',
  core_web_vitals: 'Web Vitals',
}

function fmtDate(s: string) {
  return new Date(s).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function groupByMonth(items: HistoryItem[]) {
  const groups: Record<string, HistoryItem[]> = {}
  for (const item of items) {
    const key = new Date(item.applied_at).toLocaleDateString('pt-BR', { month: 'long', year: 'numeric' })
    groups[key] = groups[key] || []
    groups[key].push(item)
  }
  return groups
}

export default function OptimizationHistoryPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [history, setHistory] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${API}/technical/${clientId}/history?limit=100`)
      setHistory(await r.json())
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [clientId])

  useEffect(() => { load() }, [load])

  const groups = groupByMonth(history)

  return (
    <div className="p-6 space-y-6 max-w-4xl">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-bold text-white flex items-center gap-2">
            <History size={20} className="text-indigo-400" />
            Histórico de Otimizações
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">Tudo que foi implementado tecnicamente pelo cliente</p>
        </div>
        <button onClick={load} disabled={loading}
          className="h-8 w-8 flex items-center justify-center bg-[#1a1f2e] border border-[#2a2f3e] rounded hover:bg-[#252a3a] transition-colors">
          {loading ? <Loader2 size={13} className="animate-spin text-slate-400" /> : <RefreshCw size={13} className="text-slate-400" />}
        </button>
      </div>

      {/* Stats */}
      <div className="bg-[#1a1f2e] rounded-xl p-4">
        <p className="text-2xl font-bold text-white">{history.length}</p>
        <p className="text-xs text-slate-500">otimizações aplicadas no total</p>
      </div>

      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 size={24} className="animate-spin text-indigo-400" />
        </div>
      ) : history.length === 0 ? (
        <div className="text-center py-16">
          <History size={36} className="text-slate-600 mx-auto mb-3" />
          <p className="text-slate-400 text-sm">Nenhuma otimização registrada ainda.</p>
        </div>
      ) : (
        <div className="space-y-8">
          {Object.entries(groups).map(([month, items]) => (
            <div key={month}>
              <div className="flex items-center gap-3 mb-4">
                <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider capitalize">{month}</h2>
                <span className="text-[10px] text-slate-600 bg-[#1a1f2e] px-2 py-0.5 rounded">{items.length} ação{items.length !== 1 ? 'ões' : ''}</span>
              </div>
              <div className="space-y-3 relative before:absolute before:left-[17px] before:top-2 before:bottom-2 before:w-px before:bg-[#2a2f3e]">
                {items.map(item => (
                  <div key={item.id} className="flex items-start gap-4 pl-2">
                    <div className="w-8 h-8 rounded-full bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center shrink-0 z-10">
                      <CheckCircle size={13} className="text-emerald-400" />
                    </div>
                    <div className="flex-1 bg-[#151b27] border border-[#2a2f3e] rounded-xl p-3">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <div className="flex items-center gap-2 mb-1">
                            <span className={`text-[10px] px-2 py-0.5 rounded ${TYPE_COLORS[item.type] || 'bg-slate-500/20 text-slate-400'}`}>
                              {TYPE_LABELS[item.type] || item.type}
                            </span>
                          </div>
                          <p className="text-sm font-medium text-white">{item.title}</p>
                          {item.description && (
                            <p className="text-xs text-slate-400 mt-0.5">{item.description}</p>
                          )}
                        </div>
                        <span className="text-[10px] text-slate-600 whitespace-nowrap">{fmtDate(item.applied_at)}</span>
                      </div>
                      {item.before_value && item.after_value && (
                        <div className="mt-2 grid grid-cols-2 gap-2 text-[10px]">
                          <div className="bg-red-500/5 border border-red-500/20 rounded p-2">
                            <p className="text-red-400 font-medium mb-1">Antes</p>
                            <p className="text-slate-400 line-clamp-2">{item.before_value}</p>
                          </div>
                          <div className="bg-emerald-500/5 border border-emerald-500/20 rounded p-2">
                            <p className="text-emerald-400 font-medium mb-1">Depois</p>
                            <p className="text-slate-400 line-clamp-2">{item.after_value}</p>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
