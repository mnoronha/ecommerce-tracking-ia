'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  BrainCircuit, Settings, Save, Loader2, CheckCircle2, AlertTriangle,
  ToggleLeft, ToggleRight, DollarSign, ArrowLeft, Plus, Trash2,
  UploadCloud, RefreshCw, Info,
} from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

const LLM_OPTIONS = [
  { id: 'chatgpt',    label: 'ChatGPT',    color: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' },
  { id: 'gemini',     label: 'Gemini',     color: 'bg-blue-500/20 text-blue-300 border-blue-500/30' },
  { id: 'perplexity', label: 'Perplexity', color: 'bg-orange-500/20 text-orange-300 border-orange-500/30' },
  { id: 'claude',     label: 'Claude',     color: 'bg-purple-500/20 text-purple-300 border-purple-500/30' },
]

const FREQ_OPTIONS = [
  { value: 'weekly',   label: 'Semanal (segunda-feira)' },
  { value: 'biweekly', label: 'Quinzenal' },
  { value: 'monthly',  label: 'Mensal (1º do mês)' },
]

interface Config {
  id?:                     string
  is_enabled:              boolean
  llms_to_monitor:         string[]
  collection_frequency:    string
  location_code:           number
  language_code:           string
  budget_monthly_usd:      number
  budget_used_this_month:  number
  last_collection_at:      string | null
  last_collection_status:  string | null
  notes:                   string | null
  configured:              boolean
}

interface Prompt {
  prompt_id:   string
  prompt_text: string
  category:    string | null
  intent:      string | null
  total_runs:  number
}

interface Brand {
  id:          string
  brand_name:  string
  website_url: string | null
  is_own_brand: boolean
}

const DEFAULT_CONFIG: Config = {
  is_enabled:             false,
  llms_to_monitor:        ['chatgpt', 'gemini', 'perplexity'],
  collection_frequency:   'weekly',
  location_code:          2076,
  language_code:          'pt',
  budget_monthly_usd:     50,
  budget_used_this_month: 0,
  last_collection_at:     null,
  last_collection_status: null,
  notes:                  null,
  configured:             false,
}

export default function AIVisibilitySettingsPage() {
  const params   = useParams()
  const router   = useRouter()
  const clientId = params.clientId as string

  const [config,   setConfig]   = useState<Config>(DEFAULT_CONFIG)
  const [prompts,  setPrompts]  = useState<Prompt[]>([])
  const [brands,   setBrands]   = useState<Brand[]>([])
  const [loading,  setLoading]  = useState(true)
  const [saving,   setSaving]   = useState(false)
  const [saved,    setSaved]    = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const [collecting, setCollecting] = useState(false)

  // New brand form
  const [newBrandName,    setNewBrandName]    = useState('')
  const [newBrandUrl,     setNewBrandUrl]     = useState('')
  const [newBrandIsOwn,   setNewBrandIsOwn]   = useState(false)
  const [addingBrand,     setAddingBrand]     = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [cfg, pr, br] = await Promise.all([
        fetch(`${API_URL}/ai-visibility/${clientId}/config`).then(r => r.json()),
        fetch(`${API_URL}/ai-visibility/${clientId}/prompts?start=2020-01-01&end=2099-12-31`).then(r => r.json()),
        fetch(`${API_URL}/ai-visibility/${clientId}/brands`).then(r => r.json()),
      ])
      if (cfg && !cfg.detail) setConfig({ ...DEFAULT_CONFIG, ...cfg })
      setPrompts(Array.isArray(pr) ? pr : [])
      setBrands(Array.isArray(br) ? br : [])
    } catch {
      setError('Erro ao carregar configurações')
    } finally {
      setLoading(false)
    }
  }, [clientId])

  useEffect(() => { load() }, [load])

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const res = await fetch(`${API_URL}/ai-visibility/${clientId}/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          is_enabled:           config.is_enabled,
          llms_to_monitor:      config.llms_to_monitor,
          collection_frequency: config.collection_frequency,
          location_code:        config.location_code,
          language_code:        config.language_code,
          budget_monthly_usd:   config.budget_monthly_usd,
          notes:                config.notes,
        }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
      load()
    } catch (e) {
      setError('Erro ao salvar configurações')
    } finally {
      setSaving(false)
    }
  }

  const triggerCollect = async () => {
    setCollecting(true)
    try {
      await fetch(`${API_URL}/ai-visibility/${clientId}/collect?force=true`, { method: 'POST' })
      setTimeout(load, 60000)
    } catch { /* ignore */ }
    finally { setCollecting(false) }
  }

  const addBrand = async () => {
    if (!newBrandName.trim()) return
    setAddingBrand(true)
    try {
      const res = await fetch(`${API_URL}/ai-visibility/${clientId}/brands`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          brand_name:  newBrandName.trim(),
          is_own_brand: newBrandIsOwn,
          website_url: newBrandUrl.trim() || null,
        }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setNewBrandName('')
      setNewBrandUrl('')
      setNewBrandIsOwn(false)
      load()
    } catch {
      setError('Erro ao adicionar marca')
    } finally {
      setAddingBrand(false)
    }
  }

  const toggleLlm = (id: string) => {
    setConfig(c => ({
      ...c,
      llms_to_monitor: c.llms_to_monitor.includes(id)
        ? c.llms_to_monitor.filter(l => l !== id)
        : [...c.llms_to_monitor, id],
    }))
  }

  const estimatedCost = (prompts.length * config.llms_to_monitor.length * 0.001).toFixed(3)

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 size={20} className="animate-spin text-slate-500" />
      </div>
    )
  }

  return (
    <div className="p-6 max-w-2xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push(`/clients/${clientId}/ai-visibility`)}
            className="text-slate-500 hover:text-slate-300 transition-colors"
          >
            <ArrowLeft size={16} />
          </button>
          <div>
            <h1 className="text-lg font-bold text-white flex items-center gap-2">
              <Settings size={18} className="text-indigo-400" />
              AI Visibility — Configurações
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">Coleta automática via DataForSEO API</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {config.is_enabled && (
            <button
              onClick={triggerCollect}
              disabled={collecting}
              className="h-8 px-3 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded text-xs text-white flex items-center gap-1.5 transition-colors"
            >
              {collecting ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
              Coletar agora
            </button>
          )}
          <button
            onClick={save}
            disabled={saving}
            className="h-8 px-4 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded text-xs text-white flex items-center gap-1.5 transition-colors"
          >
            {saving ? <Loader2 size={11} className="animate-spin" /> : saved ? <CheckCircle2 size={11} /> : <Save size={11} />}
            {saved ? 'Salvo!' : 'Salvar'}
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-xs">
          <AlertTriangle size={13} />
          {error}
        </div>
      )}

      {/* Enable toggle */}
      <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl p-5 flex items-center justify-between gap-4">
        <div>
          <p className="text-sm font-semibold text-white">Coleta automática</p>
          <p className="text-xs text-slate-500 mt-0.5">
            Coleta dados de presença da marca nas IAs automaticamente via DataForSEO
          </p>
        </div>
        <button
          onClick={() => setConfig(c => ({ ...c, is_enabled: !c.is_enabled }))}
          className="flex-shrink-0"
        >
          {config.is_enabled
            ? <ToggleRight size={32} className="text-indigo-400" />
            : <ToggleLeft size={32} className="text-slate-600" />
          }
        </button>
      </div>

      {/* LLMs to monitor */}
      <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl p-5 space-y-3">
        <p className="text-sm font-semibold text-white">IAs monitoradas</p>
        <div className="flex flex-wrap gap-2">
          {LLM_OPTIONS.map(llm => (
            <button
              key={llm.id}
              onClick={() => toggleLlm(llm.id)}
              className={`h-8 px-3 rounded-lg border text-xs transition-all ${
                config.llms_to_monitor.includes(llm.id)
                  ? llm.color + ' ring-1 ring-inset ring-current'
                  : 'bg-[#0f1117] border-[#2a2f3e] text-slate-500'
              }`}
            >
              {llm.label}
            </button>
          ))}
        </div>
      </div>

      {/* Frequency + budget */}
      <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl p-5 space-y-4">
        <p className="text-sm font-semibold text-white">Frequência e budget</p>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1.5">Frequência de coleta</label>
            <select
              value={config.collection_frequency}
              onChange={e => setConfig(c => ({ ...c, collection_frequency: e.target.value }))}
              className="w-full h-9 px-3 bg-[#0f1117] border border-[#2a2f3e] rounded-lg text-sm text-white focus:outline-none focus:border-indigo-500"
            >
              {FREQ_OPTIONS.map(f => (
                <option key={f.value} value={f.value}>{f.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1.5">Budget mensal (USD)</label>
            <input
              type="number"
              min="1"
              max="500"
              step="5"
              value={config.budget_monthly_usd}
              onChange={e => setConfig(c => ({ ...c, budget_monthly_usd: Number(e.target.value) }))}
              className="w-full h-9 px-3 bg-[#0f1117] border border-[#2a2f3e] rounded-lg text-sm text-white focus:outline-none focus:border-indigo-500"
            />
          </div>
        </div>
        <div className="flex items-start gap-2 p-3 bg-[#0f1117] rounded-lg text-xs text-slate-500">
          <DollarSign size={12} className="mt-0.5 shrink-0 text-slate-600" />
          <span>
            Estimativa desta semana: <span className="text-white">${estimatedCost}</span>{' '}
            ({prompts.length} prompts × {config.llms_to_monitor.length} IAs × $0,001/consulta).{' '}
            Usado este mês: <span className="text-white">${(config.budget_used_this_month || 0).toFixed(3)}</span>
            {' '}/ ${config.budget_monthly_usd}
          </span>
        </div>
      </div>

      {/* Last collection status */}
      {config.last_collection_at && (
        <div className={`flex items-center gap-3 p-4 rounded-xl border text-xs ${
          config.last_collection_status === 'ok'
            ? 'bg-emerald-500/5 border-emerald-500/20'
            : config.last_collection_status === 'budget_exceeded'
            ? 'bg-yellow-500/5 border-yellow-500/20'
            : 'bg-red-500/5 border-red-500/20'
        }`}>
          {config.last_collection_status === 'ok'
            ? <CheckCircle2 size={13} className="text-emerald-400" />
            : <AlertTriangle size={13} className="text-yellow-400" />
          }
          <span className="text-slate-400">
            Última coleta:{' '}
            <span className="text-white">
              {new Date(config.last_collection_at).toLocaleString('pt-BR', {
                day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'
              })}
            </span>
            {' '}· Status: <span className={config.last_collection_status === 'ok' ? 'text-emerald-400' : 'text-yellow-400'}>
              {config.last_collection_status === 'ok' ? 'OK'
                : config.last_collection_status === 'budget_exceeded' ? 'Budget excedido'
                : config.last_collection_status || '—'}
            </span>
          </span>
        </div>
      )}

      {/* Brands */}
      <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold text-white">Marcas monitoradas</p>
            <p className="text-xs text-slate-500 mt-0.5">Própria marca + competidores detectados pelas IAs</p>
          </div>
        </div>
        {brands.length === 0 ? (
          <p className="text-xs text-slate-600 text-center py-6">Nenhuma marca cadastrada ainda</p>
        ) : (
          <table className="w-full text-xs">
            <tbody>
              {brands.map(b => (
                <tr key={b.id} className="border-b border-[#1a1f2e]">
                  <td className="px-5 py-3 text-slate-300 font-medium">{b.brand_name}</td>
                  <td className="px-3 py-3 text-slate-500">{b.website_url || '—'}</td>
                  <td className="px-5 py-3 text-right">
                    {b.is_own_brand
                      ? <span className="px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300 text-[10px]">Própria</span>
                      : <span className="px-2 py-0.5 rounded bg-slate-500/20 text-slate-400 text-[10px]">Competidor</span>
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {/* Add brand */}
        <div className="px-5 py-4 border-t border-[#2a2f3e] space-y-2">
          <p className="text-xs text-slate-500 font-medium">Adicionar marca</p>
          <div className="flex gap-2">
            <input
              placeholder="Nome da marca"
              value={newBrandName}
              onChange={e => setNewBrandName(e.target.value)}
              className="flex-1 h-8 px-3 bg-[#0f1117] border border-[#2a2f3e] rounded text-xs text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500"
            />
            <input
              placeholder="URL (opcional)"
              value={newBrandUrl}
              onChange={e => setNewBrandUrl(e.target.value)}
              className="w-44 h-8 px-3 bg-[#0f1117] border border-[#2a2f3e] rounded text-xs text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500"
            />
            <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={newBrandIsOwn}
                onChange={e => setNewBrandIsOwn(e.target.checked)}
                className="rounded"
              />
              Própria
            </label>
            <button
              onClick={addBrand}
              disabled={addingBrand || !newBrandName.trim()}
              className="h-8 px-3 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded text-xs text-white flex items-center gap-1 transition-colors"
            >
              {addingBrand ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />}
              Adicionar
            </button>
          </div>
        </div>
      </div>

      {/* Prompts */}
      <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-[#2a2f3e]">
          <p className="text-sm font-semibold text-white">Prompts monitorados</p>
          <p className="text-xs text-slate-500 mt-0.5">
            {prompts.length} prompt{prompts.length !== 1 ? 's' : ''} — enviados para cada IA a cada coleta
          </p>
        </div>
        {prompts.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-8 text-center">
            <p className="text-xs text-slate-600">Nenhum prompt ainda</p>
            <p className="text-xs text-slate-600 max-w-xs">
              Prompts são criados automaticamente ao importar um CSV do Ubersuggest, ou pode adicionar via API.
            </p>
            <button
              onClick={() => router.push('/ai-visibility/import')}
              className="flex items-center gap-1.5 px-3 py-2 bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg text-xs text-slate-300 hover:bg-[#252a3a] transition-colors"
            >
              <UploadCloud size={12} />
              Importar CSV para criar prompts
            </button>
          </div>
        ) : (
          <div className="max-h-64 overflow-y-auto">
            <table className="w-full text-xs">
              <tbody>
                {prompts.map(p => (
                  <tr key={p.prompt_id} className="border-b border-[#1a1f2e]">
                    <td className="px-5 py-2.5 text-slate-400 max-w-[340px]">
                      <span className="line-clamp-1">{p.prompt_text}</span>
                    </td>
                    <td className="px-3 py-2.5 text-right text-slate-600">
                      {p.category || '—'}
                    </td>
                    <td className="px-5 py-2.5 text-right text-slate-600 tabular-nums">
                      {p.total_runs > 0 ? `${p.total_runs}×` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Info box */}
      <div className="flex items-start gap-3 p-4 bg-[#151b27] border border-[#2a2f3e] rounded-xl text-xs text-slate-500">
        <Info size={13} className="text-slate-600 shrink-0 mt-0.5" />
        <div className="space-y-1 leading-relaxed">
          <p>Adicione <strong className="text-slate-300">DATAFORSEO_LOGIN</strong> e <strong className="text-slate-300">DATAFORSEO_PASSWORD</strong> como variáveis de ambiente no Railway para ativar a coleta automática.</p>
          <p>A coleta roda toda segunda-feira às 00:00 BRT e dispara a análise Claude automaticamente.</p>
        </div>
      </div>
    </div>
  )
}
