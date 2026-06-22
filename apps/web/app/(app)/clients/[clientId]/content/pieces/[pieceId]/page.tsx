'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  ArrowLeft, Loader2, CheckCircle, AlertTriangle,
  XCircle, Clock, RefreshCw, Send, ChevronDown, ChevronUp,
  Copy, ExternalLink, Sparkles, Shield,
} from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ─────────────────────────────────────────────────────────────────────

interface PieceVersion {
  id: string
  version_number: number
  content_markdown: string
  word_count: number | null
  rag_sources_used: number
  generation_model: string
  created_at: string
}

interface FactCheck {
  id: string
  version_id: string
  overall_confidence: number
  facts_to_verify: Array<{ claim: string; source: string | null; confidence: number }>
  issues_found: Array<{ type: string; description: string; severity: string }>
  recommendation: string
  created_at: string
}

interface Piece {
  id: string
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

const STATUS_COLORS: Record<string, string> = {
  draft:           'text-slate-400',
  reviewed:        'text-indigo-400',
  approved:        'text-emerald-400',
  published:       'text-emerald-300',
  pending_client:  'text-orange-400',
}

function ConfidenceBadge({ score }: { score: number }) {
  const color =
    score >= 0.8 ? 'text-emerald-400' :
    score >= 0.6 ? 'text-yellow-400' :
                   'text-red-400'
  return (
    <span className={`text-sm font-bold ${color}`}>
      {Math.round(score * 100)}%
    </span>
  )
}

function SeverityIcon({ severity }: { severity: string }) {
  if (severity === 'high')   return <XCircle size={13} className="text-red-400 shrink-0" />
  if (severity === 'medium') return <AlertTriangle size={13} className="text-yellow-400 shrink-0" />
  return <Clock size={13} className="text-slate-500 shrink-0" />
}

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function PieceEditorPage() {
  const params   = useParams()
  const router   = useRouter()
  const clientId = params.clientId as string
  const pieceId  = params.pieceId  as string

  const [piece, setPiece]           = useState<Piece | null>(null)
  const [loading, setLoading]       = useState(true)
  const [activeVersion, setActiveVersion] = useState<PieceVersion | null>(null)
  const [editingTitle, setEditingTitle]   = useState(false)
  const [title, setTitle]                 = useState('')
  const [saving, setSaving]               = useState(false)
  const [runningFC, setRunningFC]         = useState(false)
  const [sendingApproval, setSendingApproval] = useState(false)
  const [approvalSent, setApprovalSent]       = useState(false)
  const [showFC, setShowFC]               = useState(true)
  const [copied, setCopied]               = useState(false)

  const base = `${API}/content/${clientId}`

  const load = useCallback(async () => {
    try {
      const d: Piece = await fetch(`${base}/pieces/${pieceId}`).then(r => r.json())
      setPiece(d)
      setTitle(d.final_title || '')
      const latest = d.versions.sort((a, b) => b.version_number - a.version_number)[0]
      if (latest) setActiveVersion(latest)
    } catch {}
    finally { setLoading(false) }
  }, [base, pieceId])

  useEffect(() => { load() }, [load])

  async function saveTitle() {
    if (!title.trim()) return
    setSaving(true)
    try {
      await fetch(`${base}/pieces/${pieceId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ final_title: title }),
      })
      setEditingTitle(false)
      await load()
    } finally { setSaving(false) }
  }

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
    if (!activeVersion) return
    setRunningFC(true)
    try {
      await fetch(`${base}/pieces/${pieceId}/factcheck`, { method: 'POST' })
      await new Promise(r => setTimeout(r, 3000))
      await load()
      setShowFC(true)
    } finally { setRunningFC(false) }
  }

  async function regenerate() {
    if (!confirm('Gerar nova versão desta peça?')) return
    setSaving(true)
    try {
      await fetch(`${base}/pieces/${pieceId}/versions`, { method: 'POST' })
      await new Promise(r => setTimeout(r, 4000))
      await load()
    } finally { setSaving(false) }
  }

  async function sendApproval() {
    setSendingApproval(true)
    try {
      await fetch(`${base}/pieces/${pieceId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'pending_client' }),
      })
      setApprovalSent(true)
      await load()
    } finally { setSendingApproval(false) }
  }

  function copyContent() {
    if (!activeVersion) return
    navigator.clipboard.writeText(activeVersion.content_markdown)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  if (loading) return (
    <div className="flex items-center justify-center h-96">
      <Loader2 size={24} className="animate-spin text-indigo-400" />
    </div>
  )

  if (!piece) return (
    <div className="p-6 text-red-400">Peça não encontrada.</div>
  )

  const versions = [...(piece.versions || [])].sort((a, b) => b.version_number - a.version_number)
  const fc = piece.factcheck

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <button
            onClick={() => router.push(`/clients/${clientId}/content`)}
            className="text-slate-500 hover:text-white mt-0.5 shrink-0"
          >
            <ArrowLeft size={18} />
          </button>
          <div>
            {editingTitle ? (
              <div className="flex items-center gap-2">
                <input
                  value={title}
                  onChange={e => setTitle(e.target.value)}
                  autoFocus
                  onKeyDown={e => e.key === 'Enter' && saveTitle()}
                  className="text-xl font-bold bg-transparent border-b border-indigo-500 text-white outline-none w-96"
                />
                <button onClick={saveTitle} disabled={saving} className="text-xs text-indigo-400 hover:text-indigo-300">
                  {saving ? '…' : 'OK'}
                </button>
                <button onClick={() => setEditingTitle(false)} className="text-xs text-slate-500 hover:text-white">
                  Cancelar
                </button>
              </div>
            ) : (
              <h1
                className="text-xl font-bold text-white cursor-pointer hover:text-indigo-300 transition-colors"
                onClick={() => setEditingTitle(true)}
                title="Clique para editar"
              >
                {piece.final_title || 'Sem título — clique para editar'}
              </h1>
            )}
            <p className={`text-xs mt-1 ${STATUS_COLORS[piece.status] || 'text-slate-500'}`}>
              {piece.status.replace(/_/g, ' ')} · v{piece.current_version} · {fmtDate(piece.created_at)}
            </p>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={regenerate}
            disabled={saving}
            className="flex items-center gap-1.5 text-xs bg-[#1a1f2e] hover:bg-[#2a2f3e] text-slate-300 px-3 py-2 rounded-md transition-colors disabled:opacity-50"
          >
            <Sparkles size={12} /> Nova versão
          </button>
          <button
            onClick={runFactcheck}
            disabled={runningFC || !activeVersion}
            className="flex items-center gap-1.5 text-xs bg-[#1a1f2e] hover:bg-[#2a2f3e] text-slate-300 px-3 py-2 rounded-md transition-colors disabled:opacity-50"
          >
            {runningFC ? <Loader2 size={12} className="animate-spin" /> : <Shield size={12} />}
            Factcheck
          </button>
          {piece.status === 'reviewed' && (
            <button
              onClick={sendApproval}
              disabled={sendingApproval}
              className="flex items-center gap-1.5 text-xs bg-orange-600 hover:bg-orange-700 text-white px-3 py-2 rounded-md transition-colors disabled:opacity-50"
            >
              {sendingApproval ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
              {approvalSent ? 'Enviado!' : 'Enviar p/ aprovação'}
            </button>
          )}
          {piece.status === 'approved' && (
            <button
              onClick={() => updateStatus('published')}
              disabled={saving}
              className="flex items-center gap-1.5 text-xs bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-2 rounded-md transition-colors disabled:opacity-50"
            >
              Marcar publicado
            </button>
          )}
          {['draft', 'generated'].includes(piece.status) && (
            <button
              onClick={() => updateStatus('reviewed')}
              disabled={saving}
              className="flex items-center gap-1.5 text-xs bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-2 rounded-md transition-colors disabled:opacity-50"
            >
              <CheckCircle size={12} /> Marcar revisado
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-5">
        {/* ── Content ─────────────────────────────────────────────────────── */}
        <div className="col-span-2 space-y-3">
          {/* Version selector */}
          {versions.length > 1 && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500">Versão:</span>
              <div className="flex gap-1">
                {versions.map(v => (
                  <button
                    key={v.id}
                    onClick={() => setActiveVersion(v)}
                    className={`text-xs px-2.5 py-1 rounded-md transition-colors ${
                      activeVersion?.id === v.id
                        ? 'bg-indigo-600 text-white'
                        : 'bg-[#1a1f2e] text-slate-400 hover:text-white'
                    }`}
                  >
                    v{v.version_number}
                  </button>
                ))}
              </div>
              {activeVersion && (
                <span className="text-xs text-slate-600 ml-2">
                  {activeVersion.word_count ? `${activeVersion.word_count} palavras · ` : ''}
                  {activeVersion.rag_sources_used} fontes RAG · {activeVersion.generation_model}
                </span>
              )}
            </div>
          )}

          {/* Content block */}
          {activeVersion ? (
            <div className="bg-[#1a1f2e] rounded-xl p-5 relative group">
              <button
                onClick={copyContent}
                className="absolute top-4 right-4 text-slate-500 hover:text-white transition-colors opacity-0 group-hover:opacity-100"
                title="Copiar markdown"
              >
                {copied ? <CheckCircle size={15} className="text-emerald-400" /> : <Copy size={15} />}
              </button>

              <article
                className="prose prose-invert prose-sm max-w-none text-slate-200 leading-relaxed whitespace-pre-wrap font-mono text-xs"
                style={{ maxHeight: '72vh', overflowY: 'auto' }}
              >
                {activeVersion.content_markdown}
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
            <h3 className="text-sm font-semibold text-white mb-3">Histórico de versões</h3>
            <div className="space-y-2">
              {versions.map(v => (
                <button
                  key={v.id}
                  onClick={() => setActiveVersion(v)}
                  className={`w-full text-left p-2 rounded-lg text-xs transition-colors ${
                    activeVersion?.id === v.id
                      ? 'bg-indigo-600/20 text-indigo-300'
                      : 'hover:bg-[#0f1117] text-slate-400 hover:text-white'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium">v{v.version_number}</span>
                    {v.word_count && <span className="text-slate-500">{v.word_count}p</span>}
                  </div>
                  <p className="text-slate-600 mt-0.5">{fmtDate(v.created_at)}</p>
                </button>
              ))}
              {versions.length === 0 && (
                <p className="text-slate-500 text-xs text-center py-2">Sem versões</p>
              )}
            </div>
          </div>

          {/* Factcheck */}
          <div className="bg-[#1a1f2e] rounded-xl p-4">
            <button
              onClick={() => setShowFC(v => !v)}
              className="w-full flex items-center justify-between text-sm font-semibold text-white"
            >
              <span className="flex items-center gap-2">
                <Shield size={14} className="text-indigo-400" />
                Factcheck
              </span>
              <div className="flex items-center gap-2">
                {fc && <ConfidenceBadge score={fc.overall_confidence} />}
                {showFC ? <ChevronUp size={14} className="text-slate-500" /> : <ChevronDown size={14} className="text-slate-500" />}
              </div>
            </button>

            {showFC && (
              <div className="mt-3 space-y-3">
                {runningFC && (
                  <div className="flex items-center gap-2 text-xs text-indigo-400">
                    <Loader2 size={12} className="animate-spin" />
                    Verificando fatos…
                  </div>
                )}

                {!fc && !runningFC && (
                  <div className="text-center py-4">
                    <p className="text-xs text-slate-500 mb-3">
                      Nenhum factcheck ainda. Clique em "Factcheck" para verificar a precisão da peça.
                    </p>
                    <button
                      onClick={runFactcheck}
                      className="text-xs bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 px-3 py-1.5 rounded-md transition-colors"
                    >
                      Executar agora
                    </button>
                  </div>
                )}

                {fc && !runningFC && (
                  <>
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-slate-400">Confiança</span>
                      <ConfidenceBadge score={fc.overall_confidence} />
                    </div>

                    {fc.recommendation && (
                      <div className="bg-[#0f1117] rounded-lg p-2">
                        <p className="text-xs text-slate-300 italic">{fc.recommendation}</p>
                      </div>
                    )}

                    {fc.issues_found && fc.issues_found.length > 0 && (
                      <div>
                        <p className="text-xs text-slate-500 mb-1.5">Problemas encontrados</p>
                        <div className="space-y-1.5">
                          {fc.issues_found.map((iss, i) => (
                            <div key={i} className="flex items-start gap-2 text-xs">
                              <SeverityIcon severity={iss.severity} />
                              <span className="text-slate-300">{iss.description}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {fc.facts_to_verify && fc.facts_to_verify.length > 0 && (
                      <div>
                        <p className="text-xs text-slate-500 mb-1.5">Verificar manualmente</p>
                        <div className="space-y-2">
                          {fc.facts_to_verify.map((f, i) => (
                            <div key={i} className="bg-[#0f1117] rounded p-2 text-xs">
                              <p className="text-slate-200 mb-1">"{f.claim}"</p>
                              {f.source && <p className="text-slate-500">Fonte: {f.source}</p>}
                              <div className="flex items-center justify-between mt-1">
                                <span className="text-slate-600">confiança</span>
                                <ConfidenceBadge score={f.confidence} />
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    <p className="text-xs text-slate-600">{fmtDate(fc.created_at)}</p>
                  </>
                )}
              </div>
            )}
          </div>

          {/* Published URL */}
          {piece.published_url && (
            <div className="bg-[#1a1f2e] rounded-xl p-4">
              <h3 className="text-xs font-semibold text-white mb-2">Publicado em</h3>
              <a
                href={piece.published_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1.5 text-xs text-indigo-400 hover:text-indigo-300 break-all"
              >
                <ExternalLink size={11} />
                {piece.published_url}
              </a>
              {piece.published_at && (
                <p className="text-xs text-slate-600 mt-1">{fmtDate(piece.published_at)}</p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
