'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { Loader2, CheckCircle, XCircle, ThumbsUp, ThumbsDown, MessageSquare } from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

interface ApprovalData {
  piece_id: string
  final_title: string | null
  content_markdown: string
  word_count: number | null
  client_name: string
  expires_at: string | null
  already_decided: boolean
  decision: string | null
}

export default function ApprovalPage() {
  const params = useParams()
  const token  = params.token as string

  const [data, setData]         = useState<ApprovalData | null>(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState('')
  const [comment, setComment]   = useState('')
  const [submitting, setSub]    = useState(false)
  const [done, setDone]         = useState<'approved' | 'rejected' | null>(null)

  useEffect(() => {
    fetch(`${API}/content/approve/${token}`)
      .then(r => {
        if (!r.ok) throw new Error('Link inválido ou expirado')
        return r.json()
      })
      .then(d => setData(d))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [token])

  async function decide(decision: 'approved' | 'rejected') {
    setSub(true)
    try {
      const r = await fetch(`${API}/content/approve/${token}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, comment: comment.trim() || undefined }),
      })
      if (!r.ok) throw new Error('Erro ao enviar resposta')
      setDone(decision)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setSub(false)
    }
  }

  // ── Loading ───────────────────────────────────────────────────────────────

  if (loading) return (
    <div className="min-h-screen bg-[#0f1117] flex items-center justify-center">
      <Loader2 size={24} className="animate-spin text-indigo-400" />
    </div>
  )

  // ── Error ─────────────────────────────────────────────────────────────────

  if (error && !done) return (
    <div className="min-h-screen bg-[#0f1117] flex items-center justify-center px-4">
      <div className="max-w-md w-full text-center">
        <XCircle size={40} className="text-red-400 mx-auto mb-4" />
        <h1 className="text-white text-xl font-bold mb-2">Link inválido</h1>
        <p className="text-slate-400 text-sm">{error}</p>
      </div>
    </div>
  )

  // ── Done ──────────────────────────────────────────────────────────────────

  if (done) return (
    <div className="min-h-screen bg-[#0f1117] flex items-center justify-center px-4">
      <div className="max-w-md w-full text-center">
        {done === 'approved' ? (
          <>
            <CheckCircle size={48} className="text-emerald-400 mx-auto mb-4" />
            <h1 className="text-white text-2xl font-bold mb-2">Conteúdo aprovado!</h1>
            <p className="text-slate-400">Obrigado. Nossa equipe foi notificada e o conteúdo será publicado em breve.</p>
          </>
        ) : (
          <>
            <XCircle size={48} className="text-red-400 mx-auto mb-4" />
            <h1 className="text-white text-2xl font-bold mb-2">Feedback enviado</h1>
            <p className="text-slate-400">Recebemos sua revisão. Nossa equipe irá ajustar e enviar uma nova versão.</p>
          </>
        )}
      </div>
    </div>
  )

  // ── Already decided ───────────────────────────────────────────────────────

  if (data?.already_decided) return (
    <div className="min-h-screen bg-[#0f1117] flex items-center justify-center px-4">
      <div className="max-w-md w-full text-center">
        <CheckCircle size={40} className="text-slate-500 mx-auto mb-4" />
        <h1 className="text-white text-xl font-bold mb-2">Resposta já registrada</h1>
        <p className="text-slate-400 text-sm capitalize">
          Decisão: <span className={data.decision === 'approved' ? 'text-emerald-400' : 'text-red-400'}>
            {data.decision === 'approved' ? 'aprovado' : 'solicitada revisão'}
          </span>
        </p>
      </div>
    </div>
  )

  // ── Main approval view ────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-[#0f1117] py-10 px-4">
      <div className="max-w-3xl mx-auto">
        {/* Header */}
        <div className="text-center mb-8">
          <p className="text-slate-500 text-sm mb-1">Aprovação de conteúdo</p>
          <h1 className="text-2xl font-bold text-white">
            {data?.final_title || 'Conteúdo para revisão'}
          </h1>
          {data?.word_count && (
            <p className="text-slate-500 text-sm mt-1">{data.word_count.toLocaleString('pt-BR')} palavras</p>
          )}
        </div>

        {/* Content */}
        <div className="bg-[#1a1f2e] rounded-2xl p-8 mb-6">
          <article className="prose prose-invert prose-sm max-w-none text-slate-200 leading-relaxed whitespace-pre-wrap">
            {data?.content_markdown}
          </article>
        </div>

        {/* Comment */}
        <div className="bg-[#1a1f2e] rounded-xl p-5 mb-4">
          <label className="flex items-center gap-2 text-sm font-medium text-white mb-2">
            <MessageSquare size={14} className="text-slate-400" />
            Comentário (opcional)
          </label>
          <textarea
            value={comment}
            onChange={e => setComment(e.target.value)}
            rows={3}
            placeholder="Deixe um comentário para a equipe — ajustes desejados, dúvidas, observações…"
            className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white resize-none"
          />
        </div>

        {/* Buttons */}
        <div className="grid grid-cols-2 gap-4">
          <button
            onClick={() => decide('rejected')}
            disabled={submitting}
            className="flex items-center justify-center gap-2 py-4 rounded-xl bg-red-900/30 hover:bg-red-900/50 border border-red-800/50 text-red-300 font-medium transition-colors disabled:opacity-50"
          >
            {submitting ? (
              <Loader2 size={18} className="animate-spin" />
            ) : (
              <ThumbsDown size={18} />
            )}
            Solicitar revisão
          </button>

          <button
            onClick={() => decide('approved')}
            disabled={submitting}
            className="flex items-center justify-center gap-2 py-4 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white font-medium transition-colors disabled:opacity-50"
          >
            {submitting ? (
              <Loader2 size={18} className="animate-spin" />
            ) : (
              <ThumbsUp size={18} />
            )}
            Aprovar conteúdo
          </button>
        </div>

        {data?.expires_at && (
          <p className="text-center text-xs text-slate-600 mt-4">
            Link válido até {new Date(data.expires_at).toLocaleDateString('pt-BR')}
          </p>
        )}
      </div>
    </div>
  )
}
