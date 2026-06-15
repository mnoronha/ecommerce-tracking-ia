'use client'

import { useEffect, useRef, useState } from 'react'
import { UploadCloud, CheckCircle, XCircle, AlertTriangle, Loader2, RotateCcw, ExternalLink } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Client {
  pixel_id: string
  name:     string
}

interface Preview {
  client_id:    string
  file_name:    string
  file_size:    number
  valid:        boolean
  csv_type:     string | null
  total_rows:   number
  period_start: string | null
  period_end:   string | null
  platforms:    string[]
  errors:       string[]
  warnings:     string[]
  sample_rows:  Record<string, string>[]
}

interface ImportHistory {
  id:             string
  period_start:   string
  period_end:     string
  file_name:      string | null
  rows_processed: number
  rows_skipped:   number
  status:         string
  created_at:     string
  imported_at:    string | null
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' })

const fmtDateTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—'

const STATUS_STYLE: Record<string, string> = {
  imported: 'text-emerald-400',
  failed:   'text-red-400',
  reverted: 'text-slate-500',
  pending:  'text-yellow-400',
}
const STATUS_LABEL: Record<string, string> = {
  imported: 'Importado',
  failed:   'Falhou',
  reverted: 'Revertido',
  pending:  'Pendente',
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AIVisibilityImportPage() {
  const fileInput   = useRef<HTMLInputElement>(null)
  const [clients,   setClients]   = useState<Client[]>([])
  const [pixelId,   setPixelId]   = useState('')
  const [file,      setFile]      = useState<File | null>(null)
  const [preview,   setPreview]   = useState<Preview | null>(null)
  const [history,   setHistory]   = useState<ImportHistory[]>([])
  const [step,      setStep]      = useState<'upload' | 'preview' | 'done'>('upload')
  const [loading,   setLoading]   = useState(false)
  const [result,    setResult]    = useState<any>(null)
  const [dragging,  setDragging]  = useState(false)
  const [histLoading, setHistLoading] = useState(false)

  useEffect(() => {
    fetch(`${API_URL}/setup/clients`)
      .then(r => r.json())
      .then(d => {
        const list: Client[] = Array.isArray(d) ? d : (d.clients || [])
        setClients(list)
        if (list.length === 1) setPixelId(list[0].pixel_id)
      })
      .catch(() => {})
  }, [])

  const loadHistory = async (pid: string) => {
    if (!pid) return
    setHistLoading(true)
    try {
      const d = await fetch(`${API_URL}/ai-visibility/${pid}/imports`).then(r => r.json())
      setHistory(Array.isArray(d) ? d : [])
    } catch { setHistory([]) }
    finally { setHistLoading(false) }
  }

  const handleClientChange = (pid: string) => {
    setPixelId(pid)
    setFile(null)
    setPreview(null)
    setStep('upload')
    loadHistory(pid)
  }

  useEffect(() => { if (pixelId) loadHistory(pixelId) }, [pixelId])

  const handleFile = (f: File) => {
    setFile(f)
    setPreview(null)
    setStep('upload')
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f && f.name.endsWith('.csv')) handleFile(f)
  }

  const handleValidate = async () => {
    if (!file || !pixelId) return
    setLoading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const res = await fetch(`${API_URL}/ai-visibility/${pixelId}/import/preview`, { method: 'POST', body: fd })
      const data = await res.json()
      setPreview(data)
      setStep('preview')
    } catch (e) {
      alert('Erro ao validar CSV')
    } finally {
      setLoading(false)
    }
  }

  const handleConfirm = async () => {
    if (!file || !pixelId) return
    setLoading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const res = await fetch(`${API_URL}/ai-visibility/${pixelId}/import/confirm`, { method: 'POST', body: fd })
      const data = await res.json()
      setResult(data)
      setStep('done')
      loadHistory(pixelId)
    } catch (e) {
      alert('Erro ao importar')
    } finally {
      setLoading(false)
    }
  }

  const handleRevert = async (importId: string) => {
    if (!confirm('Reverter este import? Isso apagará todos os dados importados.')) return
    try {
      await fetch(`${API_URL}/ai-visibility/imports/${importId}/revert`, { method: 'POST' })
      loadHistory(pixelId)
    } catch { alert('Erro ao reverter') }
  }

  const resetFlow = () => {
    setFile(null)
    setPreview(null)
    setResult(null)
    setStep('upload')
    if (fileInput.current) fileInput.current.value = ''
  }

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-lg font-bold text-white flex items-center gap-2">
          <UploadCloud size={20} className="text-indigo-400" />
          Importar AI Visibility
        </h1>
        <p className="text-xs text-slate-500 mt-1">
          Exporte o CSV do Ubersuggest AI Search Visibility e faça o upload aqui.
        </p>
      </div>

      {/* Client selector */}
      <div>
        <label className="block text-xs text-slate-400 mb-1.5">Cliente</label>
        <select
          value={pixelId}
          onChange={e => handleClientChange(e.target.value)}
          className="w-full h-9 px-3 bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg text-sm text-white focus:outline-none focus:border-indigo-500"
        >
          <option value="">Selecionar cliente...</option>
          {clients.map(c => (
            <option key={c.pixel_id} value={c.pixel_id}>{c.name}</option>
          ))}
        </select>
      </div>

      {/* Upload zone */}
      {step === 'upload' && pixelId && (
        <div
          onDragOver={e => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          onClick={() => fileInput.current?.click()}
          className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors ${
            dragging ? 'border-indigo-500 bg-indigo-500/5' : 'border-[#2a2f3e] hover:border-[#3a3f4e]'
          }`}
        >
          <UploadCloud size={32} className="mx-auto text-slate-600 mb-3" />
          {file ? (
            <p className="text-sm text-indigo-400 font-medium">{file.name}</p>
          ) : (
            <>
              <p className="text-sm text-slate-400">Arraste o CSV aqui ou clique para selecionar</p>
              <p className="text-xs text-slate-600 mt-1">Somente arquivos .csv</p>
            </>
          )}
          <input
            ref={fileInput}
            type="file"
            accept=".csv"
            className="hidden"
            onChange={e => e.target.files?.[0] && handleFile(e.target.files[0])}
          />
        </div>
      )}

      {file && step === 'upload' && (
        <button
          onClick={handleValidate}
          disabled={loading}
          className="w-full h-10 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded-lg text-sm text-white font-medium flex items-center justify-center gap-2 transition-colors"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : null}
          Validar e visualizar
        </button>
      )}

      {/* Preview */}
      {step === 'preview' && preview && (
        <div className="space-y-4">
          <div className={`border rounded-xl p-5 ${preview.valid ? 'border-[#2a2f3e]' : 'border-red-500/30 bg-red-500/5'}`}>
            <div className="flex items-center gap-2 mb-4">
              {preview.valid
                ? <CheckCircle size={16} className="text-emerald-400" />
                : <XCircle size={16} className="text-red-400" />}
              <span className="text-sm font-medium text-white">
                {preview.valid ? 'CSV válido — pronto para importar' : 'CSV inválido'}
              </span>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs mb-4">
              <div className="bg-[#0f1117] rounded-lg p-3">
                <p className="text-slate-500 mb-1">Tipo</p>
                <p className="text-white font-medium">{preview.csv_type || '—'}</p>
              </div>
              <div className="bg-[#0f1117] rounded-lg p-3">
                <p className="text-slate-500 mb-1">Total de linhas</p>
                <p className="text-white font-medium tabular-nums">{preview.total_rows.toLocaleString('pt-BR')}</p>
              </div>
              <div className="bg-[#0f1117] rounded-lg p-3">
                <p className="text-slate-500 mb-1">Período</p>
                <p className="text-white font-medium">{preview.period_start ? `${preview.period_start} → ${preview.period_end}` : '—'}</p>
              </div>
              <div className="bg-[#0f1117] rounded-lg p-3">
                <p className="text-slate-500 mb-1">Plataformas</p>
                <p className="text-white font-medium">{preview.platforms.join(', ') || '—'}</p>
              </div>
            </div>

            {preview.errors.length > 0 && (
              <div className="space-y-1 mb-3">
                {preview.errors.map((e, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs text-red-400">
                    <XCircle size={12} className="mt-0.5 shrink-0" />
                    {e}
                  </div>
                ))}
              </div>
            )}

            {preview.warnings.length > 0 && (
              <div className="space-y-1 mb-3">
                {preview.warnings.map((w, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs text-yellow-400">
                    <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                    {w}
                  </div>
                ))}
              </div>
            )}

            {preview.sample_rows.length > 0 && (
              <div className="overflow-x-auto">
                <p className="text-xs text-slate-500 mb-2">Prévia (primeiras {preview.sample_rows.length} linhas)</p>
                <table className="w-full text-[10px] border-collapse">
                  <thead>
                    <tr>
                      {Object.keys(preview.sample_rows[0]).slice(0, 6).map(col => (
                        <th key={col} className="text-left px-2 py-1.5 bg-[#0f1117] text-slate-500 font-medium border border-[#2a2f3e] whitespace-nowrap">
                          {col}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.sample_rows.slice(0, 5).map((row, i) => (
                      <tr key={i}>
                        {Object.keys(preview.sample_rows[0]).slice(0, 6).map(col => (
                          <td key={col} className="px-2 py-1.5 text-slate-400 border border-[#1a1f2e] max-w-[150px] truncate">
                            {String(row[col] ?? '')}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="flex gap-3">
            <button
              onClick={resetFlow}
              className="flex-1 h-10 bg-[#1a1f2e] hover:bg-[#252a3a] border border-[#2a2f3e] rounded-lg text-sm text-slate-300 transition-colors"
            >
              Cancelar
            </button>
            {preview.valid && (
              <button
                onClick={handleConfirm}
                disabled={loading}
                className="flex-1 h-10 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded-lg text-sm text-white font-medium flex items-center justify-center gap-2 transition-colors"
              >
                {loading ? <Loader2 size={15} className="animate-spin" /> : null}
                Importar {preview.total_rows.toLocaleString('pt-BR')} linhas
              </button>
            )}
          </div>
        </div>
      )}

      {/* Done */}
      {step === 'done' && result && (
        <div className="border border-emerald-500/30 bg-emerald-500/5 rounded-xl p-6 text-center space-y-3">
          <CheckCircle size={32} className="text-emerald-400 mx-auto" />
          <p className="text-white font-semibold">Import concluído!</p>
          <p className="text-sm text-slate-400">
            <span className="text-white tabular-nums">{result.rows_processed}</span> registros importados
            {result.rows_skipped > 0 && <> · <span className="text-yellow-400 tabular-nums">{result.rows_skipped}</span> ignorados</>}
            {result.errors_count > 0 && <> · <span className="text-red-400 tabular-nums">{result.errors_count}</span> erros</>}
          </p>
          <div className="flex gap-3 justify-center mt-2">
            <button onClick={resetFlow} className="px-4 py-2 bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg text-sm text-slate-300 hover:bg-[#252a3a] transition-colors">
              Importar outro
            </button>
            <a
              href={`/clients/${pixelId}/ai-visibility`}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 rounded-lg text-sm text-white flex items-center gap-1.5 transition-colors"
            >
              Ver dashboard
              <ExternalLink size={12} />
            </a>
          </div>
        </div>
      )}

      {/* Import history */}
      {pixelId && (
        <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-[#2a2f3e]">
            <h2 className="text-sm font-semibold text-white">Histórico de imports</h2>
          </div>
          {histLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 size={16} className="animate-spin text-slate-500" />
            </div>
          ) : history.length === 0 ? (
            <p className="text-xs text-slate-600 text-center py-8">Nenhum import ainda</p>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[#2a2f3e]">
                  <th className="text-left px-5 py-3 text-slate-500 font-medium">Arquivo</th>
                  <th className="text-left px-3 py-3 text-slate-500 font-medium">Período</th>
                  <th className="text-right px-3 py-3 text-slate-500 font-medium">Linhas</th>
                  <th className="text-right px-3 py-3 text-slate-500 font-medium">Status</th>
                  <th className="text-right px-3 py-3 text-slate-500 font-medium">Data</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody>
                {history.map(h => (
                  <tr key={h.id} className="border-b border-[#1a1f2e]">
                    <td className="px-5 py-3 text-slate-400 max-w-[160px] truncate">{h.file_name || '—'}</td>
                    <td className="px-3 py-3 text-slate-500 whitespace-nowrap">
                      {h.period_start} → {h.period_end}
                    </td>
                    <td className="px-3 py-3 text-right text-slate-400 tabular-nums">{h.rows_processed.toLocaleString('pt-BR')}</td>
                    <td className={`px-3 py-3 text-right font-medium ${STATUS_STYLE[h.status] || 'text-slate-400'}`}>
                      {STATUS_LABEL[h.status] || h.status}
                    </td>
                    <td className="px-3 py-3 text-right text-slate-500 whitespace-nowrap">
                      {fmtDateTime(h.created_at)}
                    </td>
                    <td className="px-5 py-3 text-right">
                      {h.status === 'imported' && (
                        <button
                          onClick={() => handleRevert(h.id)}
                          title="Reverter import"
                          className="text-slate-600 hover:text-red-400 transition-colors"
                        >
                          <RotateCcw size={13} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}
