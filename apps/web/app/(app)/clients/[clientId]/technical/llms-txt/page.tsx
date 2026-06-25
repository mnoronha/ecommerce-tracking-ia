'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { FileText, Loader2, RefreshCw, CheckCircle, ExternalLink, AlertCircle } from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

export default function LlmsTxtPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [content,   setContent]   = useState('')
  const [loading,   setLoading]   = useState(true)
  const [applying,  setApplying]  = useState(false)
  const [applied,   setApplied]   = useState(false)
  const [appliedUrl, setAppliedUrl] = useState('')
  const [error,     setError]     = useState('')
  const [robots,    setRobots]    = useState<any>(null)
  const [robLoading, setRobLoading] = useState(true)

  async function loadPreview() {
    setLoading(true)
    setError('')
    try {
      const r = await fetch(`${API}/technical/${clientId}/llms-txt`)
      if (!r.ok) throw new Error((await r.json()).detail || 'Erro ao gerar preview')
      const d = await r.json()
      setContent(d.content || '')
    } catch (e: any) {
      setError(e.message)
    } finally { setLoading(false) }
  }

  async function loadRobots() {
    setRobLoading(true)
    try {
      const r = await fetch(`${API}/technical/${clientId}/robots`)
      if (r.ok) setRobots(await r.json())
    } catch { /* ignore */ }
    finally { setRobLoading(false) }
  }

  useEffect(() => {
    loadPreview()
    loadRobots()
  }, [clientId])

  async function applyToSite() {
    setApplying(true)
    try {
      const r = await fetch(`${API}/technical/${clientId}/llms-txt/apply`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ content }),
      })
      const d = await r.json()
      if (d.ok) {
        setApplied(true)
        setAppliedUrl(d.url || '')
      }
    } catch { /* ignore */ }
    finally { setApplying(false) }
  }

  return (
    <div className="p-6 space-y-6 max-w-4xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-bold text-white flex items-center gap-2">
            <FileText size={20} className="text-indigo-400" />
            llms.txt Generator
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Arquivo que indica a sistemas de IA como usar e citar o conteúdo da marca
          </p>
        </div>
        <button onClick={loadPreview} disabled={loading}
          className="h-8 w-8 flex items-center justify-center bg-[#1a1f2e] border border-[#2a2f3e] rounded hover:bg-[#252a3a] transition-colors">
          {loading ? <Loader2 size={13} className="animate-spin text-slate-400" /> : <RefreshCw size={13} className="text-slate-400" />}
        </button>
      </div>

      {/* robots.txt status */}
      {!robLoading && robots && (
        <div className={`p-4 rounded-xl border text-sm ${robots.overall_status === 'ok'
          ? 'border-emerald-500/20 bg-emerald-500/5 text-emerald-300'
          : 'border-amber-500/20 bg-amber-500/5 text-amber-300'}`}>
          <div className="flex items-center gap-2 font-medium mb-2">
            {robots.overall_status === 'ok'
              ? <CheckCircle size={14} />
              : <AlertCircle size={14} />}
            robots.txt — {robots.blocked_count > 0
              ? `${robots.blocked_count} bot(s) de IA bloqueados`
              : 'Bots de IA permitidos'}
          </div>
          <div className="flex flex-wrap gap-2 mt-1">
            {(robots.bots || []).map((b: any) => (
              <span key={b.name} className={`text-[10px] px-2 py-0.5 rounded-full ${
                b.blocked ? 'bg-red-500/20 text-red-400' :
                b.mentioned ? 'bg-emerald-500/20 text-emerald-400' :
                'bg-slate-500/20 text-slate-400'}`}>
                {b.name} {b.blocked ? '✗' : b.mentioned ? '✓' : '—'}
              </span>
            ))}
          </div>
          {robots.suggestions?.length > 0 && (
            <ul className="mt-2 text-xs text-slate-400 space-y-1">
              {robots.suggestions.slice(0, 3).map((s: string, i: number) => (
                <li key={i} className="flex items-start gap-1.5">
                  <span className="text-amber-400 shrink-0">•</span> {s}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-xs">
          <AlertCircle size={14} /> {error}
        </div>
      )}

      {/* Preview + editor */}
      {!loading && content && (
        <>
          <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-[#2a2f3e]">
              <span className="text-xs text-slate-400 font-mono">llms.txt — preview</span>
              <span className="text-[10px] text-slate-600">{content.split('\n').length} linhas</span>
            </div>
            <textarea
              value={content}
              onChange={e => setContent(e.target.value)}
              rows={24}
              className="w-full bg-[#0f1117] text-emerald-300 text-xs font-mono p-4 resize-none focus:outline-none leading-relaxed"
            />
          </div>

          {applied ? (
            <div className="flex items-center gap-3 p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-xl">
              <CheckCircle size={18} className="text-emerald-400 shrink-0" />
              <div>
                <p className="text-sm font-medium text-white">llms.txt publicado com sucesso!</p>
                {appliedUrl && (
                  <a href={appliedUrl} target="_blank" rel="noopener"
                    className="text-xs text-indigo-400 hover:text-indigo-300 flex items-center gap-1 mt-1">
                    <ExternalLink size={11} /> {appliedUrl}
                  </a>
                )}
              </div>
            </div>
          ) : (
            <button onClick={applyToSite} disabled={applying}
              className="w-full h-11 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded-xl text-sm text-white font-medium flex items-center justify-center gap-2 transition-colors">
              {applying ? <Loader2 size={16} className="animate-spin" /> : <FileText size={16} />}
              {applying ? 'Publicando...' : 'Publicar llms.txt no site'}
            </button>
          )}
        </>
      )}

      {/* What is llms.txt */}
      <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl p-5 text-xs text-slate-400 space-y-2">
        <p className="text-white font-medium text-sm">O que é llms.txt?</p>
        <p>Assim como robots.txt instrui crawlers, llms.txt é um arquivo emergente que orienta sistemas de IA sobre como usar e citar o conteúdo do site.</p>
        <p>Ao publicar um llms.txt bem estruturado, você facilita que ChatGPT, Gemini, Perplexity e Claude entendam a marca, seus produtos e suas políticas — aumentando a qualidade e frequência das menções.</p>
        <a href="https://llmstxt.org" target="_blank" rel="noopener"
          className="text-indigo-400 hover:text-indigo-300 inline-flex items-center gap-1">
          Saiba mais em llmstxt.org <ExternalLink size={10} />
        </a>
      </div>
    </div>
  )
}
