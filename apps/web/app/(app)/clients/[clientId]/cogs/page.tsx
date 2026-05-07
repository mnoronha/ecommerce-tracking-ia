'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import { Loader2, Upload, RefreshCw, AlertCircle, CheckCircle, Trash2 } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

interface CogsRow {
  id:                  string
  sku:                 string | null
  platform_product_id: string | null
  product_name:        string | null
  cost_price:          number
  currency:            string
  updated_at:          string
}

interface Coverage {
  days:                number
  total_orders:        number
  orders_with_margin:  number
  orders_pct:          number
  revenue_total:       number
  revenue_with_cogs:   number
  revenue_pct:         number
}

const fmt = (n: number) =>
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n)

export default function CogsPage() {
  const params  = useParams()
  const pixelId = params.clientId as string

  const [rows,     setRows]     = useState<CogsRow[]>([])
  const [coverage, setCoverage] = useState<Coverage | null>(null)
  const [loading,  setLoading]  = useState(true)
  const [csvText,  setCsvText]  = useState('')
  const [importing, setImporting] = useState(false)
  const [recomputing, setRecomputing] = useState(false)
  const [msg,      setMsg]      = useState<{ ok: boolean; text: string } | null>(null)
  const [search,   setSearch]   = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [r, c] = await Promise.all([
        fetch(`${API_URL}/cogs/${pixelId}`),
        fetch(`${API_URL}/cogs/${pixelId}/coverage?days=30`),
      ])
      if (r.ok) {
        const data = await r.json()
        setRows(data.rows || [])
      }
      if (c.ok) setCoverage(await c.json())
    } finally {
      setLoading(false)
    }
  }, [pixelId])

  useEffect(() => { load() }, [load])

  async function handleImport() {
    if (!csvText.trim()) return
    setImporting(true); setMsg(null)
    try {
      const res = await fetch(`${API_URL}/cogs/${pixelId}/import`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ csv: csvText }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Falha na importação')
      }
      const data = await res.json()
      setMsg({ ok: true, text: `${data.inserted} produtos importados, ${data.skipped} ignorados` })
      setCsvText('')
      await load()
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    } finally {
      setImporting(false)
    }
  }

  async function handleRecompute() {
    setRecomputing(true); setMsg(null)
    try {
      const res = await fetch(`${API_URL}/cogs/${pixelId}/recompute?days=365`, { method: 'POST' })
      if (!res.ok) throw new Error('Falha ao recalcular')
      setMsg({ ok: true, text: 'Recálculo iniciado em background. Atualize em ~30s.' })
      // Reload coverage after 5s
      setTimeout(load, 5000)
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    } finally {
      setRecomputing(false)
    }
  }

  async function handleDelete(id: string) {
    if (!confirm('Remover este custo?')) return
    await fetch(`${API_URL}/cogs/${pixelId}/${id}`, { method: 'DELETE' })
    await load()
  }

  const filteredRows = search
    ? rows.filter(r =>
        (r.sku?.toLowerCase().includes(search.toLowerCase())) ||
        (r.product_name?.toLowerCase().includes(search.toLowerCase())) ||
        (r.platform_product_id?.includes(search))
      )
    : rows

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      <div className="border-b border-[#2a2f3e] px-6 py-4">
        <h1 className="text-lg font-bold text-white">Custos & Margem</h1>
        <p className="text-xs text-slate-500 mt-1">
          Importe o custo (COGS) por SKU para ver margem real e ROAS de margem nos relatórios
        </p>
      </div>

      <div className="p-6 space-y-6 max-w-5xl">

        {/* Coverage */}
        {coverage && (
          <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] p-5">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">Cobertura — últimos 30 dias</h2>
            <div className="grid grid-cols-2 gap-6">
              <div>
                <div className="flex items-end justify-between mb-2">
                  <span className="text-xs text-slate-500">Pedidos com margem</span>
                  <span className={`text-sm font-bold ${coverage.orders_pct >= 80 ? 'text-emerald-400' : coverage.orders_pct >= 50 ? 'text-yellow-400' : 'text-red-400'}`}>
                    {coverage.orders_pct.toFixed(0)}%
                  </span>
                </div>
                <div className="h-2 bg-[#0f1117] rounded-full overflow-hidden">
                  <div className="h-full bg-emerald-500 transition-all duration-700"
                       style={{ width: `${Math.min(coverage.orders_pct, 100)}%` }} />
                </div>
                <p className="text-xs text-slate-500 mt-1">
                  {coverage.orders_with_margin} de {coverage.total_orders} pedidos
                </p>
              </div>
              <div>
                <div className="flex items-end justify-between mb-2">
                  <span className="text-xs text-slate-500">Receita coberta</span>
                  <span className={`text-sm font-bold ${coverage.revenue_pct >= 80 ? 'text-emerald-400' : coverage.revenue_pct >= 50 ? 'text-yellow-400' : 'text-red-400'}`}>
                    {coverage.revenue_pct.toFixed(0)}%
                  </span>
                </div>
                <div className="h-2 bg-[#0f1117] rounded-full overflow-hidden">
                  <div className="h-full bg-indigo-500 transition-all duration-700"
                       style={{ width: `${Math.min(coverage.revenue_pct, 100)}%` }} />
                </div>
                <p className="text-xs text-slate-500 mt-1">
                  {fmt(coverage.revenue_with_cogs)} de {fmt(coverage.revenue_total)}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Importer */}
        <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] p-5">
          <div className="flex items-start justify-between mb-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-300">Importar CSV</h2>
              <p className="text-xs text-slate-500 mt-1">
                Cabeçalho: <code className="bg-[#0f1117] px-1.5 py-0.5 rounded">sku,product_name,cost_price</code>
                {' '}ou <code className="bg-[#0f1117] px-1.5 py-0.5 rounded">platform_product_id,cost_price</code>
              </p>
            </div>
            <button
              onClick={handleRecompute}
              disabled={recomputing}
              className="flex items-center gap-2 text-xs bg-[#0f1117] hover:bg-[#252a3a] border border-[#2a2f3e] text-slate-300 px-3 py-1.5 rounded-lg transition-colors"
            >
              {recomputing
                ? <><Loader2 size={12} className="animate-spin" />Recalculando...</>
                : <><RefreshCw size={12} />Recalcular margens (365d)</>}
            </button>
          </div>
          <textarea
            value={csvText}
            onChange={e => setCsvText(e.target.value)}
            placeholder={'sku,product_name,cost_price\nAJ1-CHI-42,Air Jordan Chicago 42,599.90\nAJ1-RED-43,Air Jordan Red 43,650.00'}
            className="w-full h-40 bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2.5 text-xs font-mono text-slate-300 placeholder-slate-600 outline-none focus:border-indigo-500"
          />
          <div className="flex items-center justify-between mt-3">
            {msg && (
              <span className={`flex items-center gap-1.5 text-xs ${msg.ok ? 'text-emerald-400' : 'text-red-400'}`}>
                {msg.ok ? <CheckCircle size={13} /> : <AlertCircle size={13} />}
                {msg.text}
              </span>
            )}
            <button
              onClick={handleImport}
              disabled={importing || !csvText.trim()}
              className="ml-auto flex items-center gap-2 text-xs bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-4 py-1.5 rounded-lg transition-colors font-medium"
            >
              {importing
                ? <><Loader2 size={12} className="animate-spin" />Importando...</>
                : <><Upload size={12} />Importar</>}
            </button>
          </div>
        </div>

        {/* Existing rows */}
        <div className="bg-[#1a1f2e] rounded-xl border border-[#2a2f3e] overflow-hidden">
          <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-300">
              Custos cadastrados ({rows.length})
            </h2>
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Buscar SKU ou nome..."
              className="bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-1.5 text-xs text-white placeholder-slate-600 outline-none focus:border-indigo-500 w-56"
            />
          </div>
          {loading ? (
            <div className="p-8 text-center text-slate-500">
              <Loader2 size={20} className="animate-spin mx-auto" />
            </div>
          ) : filteredRows.length === 0 ? (
            <p className="p-8 text-center text-slate-500 text-sm">
              {rows.length === 0 ? 'Nenhum custo cadastrado ainda' : 'Nenhum resultado'}
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#2a2f3e]">
                  {['SKU', 'Produto', 'Custo', 'Atualizado', ''].map(h => (
                    <th key={h} className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredRows.slice(0, 200).map(row => (
                  <tr key={row.id} className="border-b border-[#2a2f3e] last:border-0 hover:bg-[#252a3a]">
                    <td className="px-4 py-3 font-mono text-xs text-slate-300">
                      {row.sku || row.platform_product_id || '—'}
                    </td>
                    <td className="px-4 py-3 text-slate-200 text-xs max-w-md truncate">{row.product_name || '—'}</td>
                    <td className="px-4 py-3 text-emerald-400 font-medium whitespace-nowrap">{fmt(row.cost_price)}</td>
                    <td className="px-4 py-3 text-slate-500 text-xs whitespace-nowrap">
                      {new Date(row.updated_at).toLocaleDateString('pt-BR')}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => handleDelete(row.id)}
                        className="text-slate-500 hover:text-red-400 transition-colors"
                      >
                        <Trash2 size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {filteredRows.length > 200 && (
            <p className="px-5 py-3 text-xs text-slate-500">
              Mostrando 200 de {filteredRows.length}. Use a busca para filtrar.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
