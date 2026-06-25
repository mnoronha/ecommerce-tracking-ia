'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  ArrowLeft, Loader2, CheckCircle, AlertTriangle, XCircle,
  RefreshCw, Send, ChevronDown, ChevronUp, Copy, Shield, Sparkles,
} from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ─────────────────────────────────────────────────────────────────────

interface PieceVersion {
  id: string
  version_number: number
  version_type: string
  title: string | null
  body_markdown: string
  word_count: number | null
  generation_model: string | null
  generation_cost_usd: number | null
  generation_duration_ms: number | null
  rag_chunks_used: string[] | null
  created_at: string
}

interface FactCheck {
  id: string
  version_id: string
  overall_confidence: 'high' | 'medium' | 'low'
  facts_to_verify: Array<{ claim: string; location_hint?: string; concern?: string; suggested_verification?: string }>
  issues_found: Array<{ type: string; description: string; severity: string; suggested_fix?: string }>
  recommendation: string
  created_at: string
}

interface Piece {
  id: string
  briefing_id: string | null
  final_title: string | null
  status: string
  current_version: number
  published_url: string | null
  published_at: string | null
  created_at: string
  versions: PieceVersion[]
  factcheck: FactCheck | null
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const CONFIDENCE_COLOR: Record<string, string> = {
  high:   'text-emerald-400',
  medium: 'text-yellow-400',
  low:    'text-red-400',
}
const CONFIDENCE_LABEL: Record<string, string> = {
  high: 'Alta', medium: 'Média', low: 'Baixa',
}

const STATUS_COLORS: Record<string, string> = {
  draft:          'text-slate-400',
  reviewed:       'text-indigo-400',
  approved:       'text-emerald-400',
  published:      'text-emerald-300',
  pending_client: 'text-orange-400',
}

function SeverityIcon({ severity }: { severity: string }) {
  if (severity === 'high')   return <XCircle size={13} className="text-red-400 shrink-0" />
  if (severity === 'medium') return <AlertTriangle size={13} className="text-yellow-400 shrink-0" />
  return <AlertTriangle size={13} className="text-slate-500 shrink-0" />
}

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function fmtCost(usd: number | null) {
  if (!usd) return null
  const brl = usd * 5.5
  return `R$ ${brl.toFixed(2)}`
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function PieceEditorPage() {
  const params   = useParams()
  const router   = useRouter()
  const clientId = params.clientId as string
  const pieceId  = params.pieceId  as string

  const [piece, setPiece]               = useState<Piece | null>(null)
  const [loading, setLoading]           = useState(true)
  const [activeVersion, setActiveVersion] = useState<PieceVersion | null>(null)
  const [saving, setSaving]             = useState(false)
  const [runningFC, setRunningFC]       = useState(false)
  const [regenerating, setRegenerating] = useState(false)
  const [showFC, setShowFC]             = useState(true)
  const [copied, setCopied]             = useState(false)

  const base = `${API}/content/${clientId}`

  const load = useCallback(async () => {
    try {
      const d: Piece = await fetch(`${base}/pieces/${pieceId}`).then(r => r.json())
      setPiece(d)
      if (d.versions?.length) {
        setActiveVersion(av => av ? (d.versions.find(v => v.id === av.id) ?? d.versions[0]) : d.versions[0])
      }
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [base, pieceId])

  useEffect(() => { load() }, [load])

  async function updateStatus(status: string) {
    setSaving(true)
    try {
      await fetch(`${base}/pieces/${pieceId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      })
      await load()
    } finally { setSaving(false) }
  }

  async function runFactcheck() {
    setRunningFC(true)
    try {
      await fetch(`${base}/pieces/${pieceId}/factcheck`, { method: 'POST' })
      await new Promise(r => setTimeout(r, 5000))
      await load()
      setShowFC(true)
    } finally { setRunningFC(false) }
  }

  async function regen() {
    if (!confirm('Gerar nova versão com IA? A versão atual não será perdida.')) return
    setRegenerating(true)
    try {
      await fetch(`${base}/pieces/${pieceId}/regen`, { method: 'POST' })
      await new Promise(r => setTimeout(r, 12000))
      await load()
    } finally { setRegenerating(false) }
  }

  function copyContent() {
    if (!activeVersion?.body_markdown) return
    navigator.clipboard.writeText(activeVersion.body_markdown)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  if (loading) return (
    <div className="flex items-center justify-center h-96">
      <Loader2 size={24} className="animate-spin text-indigo-400" />
    </div>
  )

  if (!piece) return <div className="p-6 text-red-400">Peça não encontrada.</div>

  const versions = [...(piece.versions || [])].sort((a, b) => b.version_number - a.version_number)
  const fc = piece.factcheck

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <button onClick={() => router.push(`/clients/${clientId}/content`)}
            className="text-slate-500 hover:text-white mt-1 shrink-0">
            <ArrowLeft size={18} />
          </button>
          <div>
            <h1 className="text-xl font-bold text-white">
              {piece.final_title || 'Sem título'}
            </h1>
            <p className={`text-xs mt-1 ${STATUS_COLORS[piece.status] || 'text-slate-500'}`}>
              {piece.status.replace(/_/g, ' ')} · v{piece.current_version} · {fmtDate(piece.created_at)}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <button onClick={regen} disabled={regenerating || saving}
            className="flex items-center gap-1.5 text-xs bg-[#1a1f2e] hover:bg-[#2a2f3e] text-slate-300 px-3 py-2 rounded-md transition-colors disabled:opacity-50">
            {regenerating ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
            {regenerating ? 'Gerando…' : 'Nova versão IA'}
          </button>
          <button onClick={runFactcheck} disabled={runningFC}
            className="flex items-center gap-1.5 text-xs bg-[#1a1f2e] hover:bg-[#2a2f3e] text-slate-300 px-3 py-2 rounded-md transition-colors disabled:opacity-50">
            {runningFC ? <Loader2 size={12} className="animate-spin" /> : <Shield size={12} />}
            Factcheck
          </button>
          {['draft', 'generated'].includes(piece.status) && (
            <button onClick={() => updateStatus('reviewed')} disabled={saving}
              className="flex items-center gap-1.5 text-xs bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-2 rounded-md transition-colors disabled:opacity-50">
              <CheckCircle size={12} /> Marcar revisado
            </button>
          )}
          {piece.status === 'reviewed' && (
            <button onClick={() => updateStatus('approved')} disabled={saving}
              className="flex items-center gap-1.5 text-xs bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-2 rounded-md transition-colors disabled:opacity-50">
              <CheckCircle size={12} /> Aprovar
            </button>
          )}
          {piece.status === 'approved' && (
            <button onClick={() => updateStatus('published')} disabled={saving}
              className="flex items-center gap-1.5 text-xs bg-emerald-700 hover:bg-emerald-800 text-white px-3 py-2 rounded-md transition-colors disabled:opacity-50">
              <Send size={12} /> Marcar publicado
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-5">
        {/* ── Content ─────────────────────────────────────────────────────── */}
        <div className="col-span-2 space-y-3">
          {versions.length > 1 && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500">Versão:</span>
              <div className="flex gap-1">
                {versions.map(v => (
                  <button key={v.id} onClick={() => setActiveVersion(v)}
                    className={`text-xs px-2.5 py-1 rounded-md transition-colors ${
                      activeVersion?.id === v.id ? 'bg-indigo-600 text-white' : 'bg-[#1a1f2e] text-slate-400 hover:text-white'
                    }`}>
                    v{v.version_number} {v.version_type === 'human_edit' ? '✏' : '✦'}
                  </button>
                ))}
              </div>
              {activeVersion && (
                <span className="text-xs text-slate-600 ml-2">
                  {activeVersion.word_count ? `${activeVersion.word_count} palavras` : ''}
                  {activeVersion.rag_chunks_used?.length ? ` · ${activeVersion.rag_chunks_used.length} chunks RAG` : ''}
                  {activeVersion.generation_cost_usd ? ` · ${fmtCost(activeVersion.generation_cost_usd)}` : ''}
                </span>
              )}
            </div>
          )}

          {activeVersion ? (
            <div className="bg-[#1a1f2e] rounded-xl p-5 relative group">
              <button onClick={copyContent}
                className="absolute top-4 right-4 text-slate-500 hover:text-white transition-colors opacity-0 group-hover:opacity-100"
                title="Copiar markdown">
                {copied ? <CheckCircle size={15} className="text-emerald-400" /> : <Copy size={15} />}
              </button>
              <article
                className="prose prose-invert prose-sm max-w-none text-slate-200 leading-relaxed whitespace-pre-wrap font-mono text-xs"
                style={{ maxHeight: '72vh', overflowY: 'auto' }}
              >
                {activeVersion.body_markdown}
              </article>
            </div>
          ) : (
            <div className="bg-[#1a1f2e] rounded-xl p-8 text-center text-slate-500">
              Nenhuma versão gerada.
            </div>
          )}
        </div>

        {/* ── Sidebar ─────────────────────────────────────────────────────── */}
        <div className="space-y-4">
          {/* Version history */}
          <div className="bg-[#1a1f2e] rounded-xl p-4">
            <h3 className="text-sm font-semibold text-white mb-3">Versões</h3>
            <div className="space-y-1.5">
              {versions.map(v => (
                <button key={v.id} onClick={() => setActiveVersion(v)}
                  className={`w-full text-left p-2 rounded-lg text-xs transition-colors ${
                    activeVersion?.id === v.id ? 'bg-indigo-600/20 text-indigo-300' : 'hover:bg-[#0f1117] text-slate-400 hover:text-white'
                  }`}>
                  <div className="flex items-center justify-between">
                    <span className="font-medium">
                      v{v.version_number} — {v.version_type === 'human_edit' ? 'Edição humana' : 'Gerada por IA'}
                    </span>
                    {v.word_count && <span className="text-slate-500">{v.word_count}p</span>}
                  </div>
                  <p className="text-slate-600 mt-0.5">{fmtDate(v.created_at)}</p>
                  {v.generation_model && <p className="text-slate-700 mt-0.5 truncate">{v.generation_model}</p>}
                </button>
              ))}
              {versions.length === 0 && (
                <p className="text-slate-500 text-xs text-center py-2">Sem versões</p>
              )}
            </div>
          </div>

          {/* Factcheck */}
          <div className="bg-[#1a1f2e] rounded-xl p-4">
            <button onClick={() => setShowFC(v => !v)}
              className="w-full flex items-center justify-between text-sm font-semibold text-white">
              <span className="flex items-center gap-2">
                <Shield size={14} className="text-indigo-400" />
                Factcheck
              </span>
              <div className="flex items-center gap-2">
                {fc && (
                  <span className={`text-xs font-medium ${CONFIDENCE_COLOR[fc.overall_confidence] || 'text-slate-400'}`}>
                    {CONFIDENCE_LABEL[fc.overall_confidence] || fc.overall_confidence}
                  </span>
                )}
                {showFC ? <ChevronUp size={14} className="text-slate-500" /> : <ChevronDown size={14} className="text-slate-500" />}
              </div>
            </button>

            {showFC && (
              <div className="mt-3 space-y-3">
                {runningFC && (
                  <div className="flex items-center gap-2 text-xs text-indigo-400">
                    <Loader2 size={12} className="animate-spin" /> Verificando fatos…
                  </div>
                )}

                {!fc && !runningFC && (
                  <div className="text-center py-4">
                    <p className="text-xs text-slate-500 mb-3">Nenhum factcheck ainda.</p>
                    <button onClick={runFactcheck}
                      className="text-xs bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 px-3 py-1.5 rounded-md">
                      Executar agora
                    </button>
                  </div>
                )}

                {fc && !runningFC && (
                  <>
                    <div className="bg-[#0f1117] rounded-lg p-2">
                      <p className="text-xs text-slate-300 italic">{fc.recommendation}</p>
                    </div>

                    {fc.issues_found?.length > 0 && (
                      <div>
                        <p className="text-xs text-slate-500 mb-1.5">Problemas ({fc.issues_found.length})</p>
                        <div className="space-y-1.5">
                          {fc.issues_found.map((iss, i) => (
                            <div key={i} className="flex items-start gap-2 text-xs bg-[#0f1117] rounded p-2">
                              <SeverityIcon severity={iss.severity} />
                              <div>
                                <p className="text-slate-300">{iss.description}</p>
                                {iss.suggested_fix && <p className="text-slate-500 mt-0.5">→ {iss.suggested_fix}</p>}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {fc.facts_to_verify?.length > 0 && (
                      <div>
                        <p className="text-xs text-slate-500 mb-1.5">Verificar ({fc.facts_to_verify.length})</p>
                        <div className="space-y-2">
                          {fc.facts_to_verify.map((f, i) => (
                            <div key={i} className="bg-[#0f1117] rounded p-2 text-xs">
                              <p className="text-slate-200 mb-1">"{f.claim}"</p>
                              {f.concern && <p className="text-slate-500">{f.concern}</p>}
                              {f.suggested_verification && (
                                <p className="text-indigo-400/70 mt-0.5">→ {f.suggested_verification}</p>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    <div className="flex items-center justify-between">
                      <p className="text-xs text-slate-600">{fmtDate(fc.created_at)}</p>
                      <button onClick={runFactcheck} className="text-xs text-slate-500 hover:text-slate-300">
                        <RefreshCw size={11} />
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
