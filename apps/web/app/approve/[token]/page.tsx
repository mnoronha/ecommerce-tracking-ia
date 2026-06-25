'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import {
  Loader2, CheckCircle, XCircle, ThumbsUp, ThumbsDown,
  MessageSquare, Clock, FileText,
} from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

interface ApprovalData {
  status:          string
  already_decided: boolean
  decision:        string | null
  client_name:     string
  title:           string | null
  final_title:     string | null
  body_markdown:   string | null
  word_count:      number | null
  expires_at:      string | null
  deadline:        string | null
}

function fmtDeadline(s: string | null) {
  if (!s) return null
  const d = new Date(s)
  const now = new Date()
  const diff = Math.ceil((d.getTime() - now.getTime()) / (1000 * 60 * 60 * 24))
  const fmt  = d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' })
  if (diff <= 0) return `Prazo encerrado em ${fmt}`
  if (diff === 1) return `Prazo amanhã (${fmt})`
  return `Prazo em ${diff} dias — ${fmt}`
}

function renderMarkdown(md: string): string {
  let html = md
  // Headings
  html = html.replace(/^### (.+)$/gm, '<h3 class="text-lg font-semibold mt-6 mb-2">$1</h3>')
  html = html.replace(/^## (.+)$/gm, '<h2 class="text-xl font-bold mt-8 mb-3">$1</h2>')
  html = html.replace(/^# (.+)$/gm, '<h1 class="text-2xl font-bold mt-8 mb-4">$1</h1>')
  // Bold / italic
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>')
  // Unordered list items
  html = html.replace(/^[-*] (.+)$/gm, '<li class="ml-4 list-disc">$1</li>')
  // Paragraphs
  html = html.replace(/^(?!<[hliu]).+$/gm, '<p class="mb-3 text-slate-700 leading-relaxed">$&</p>')
  return html
}

export default function ApprovalPage() {
  const params = useParams()
  const token  = params.token as string

  const [data, setData]       = useState<ApprovalData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState('')
  const [feedback, setFeedback] = useState('')
  const [submitting, setSub]  = useState(false)
  const [done, setDone]       = useState<'approved' | 'requested_changes' | null>(null)

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

  async function decide(decision: 'approved' | 'requested_changes') {
    setSub(true)
    try {
      const r = await fetch(`${API}/content/approve/${token}`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ decision, feedback: feedback.trim() || undefined }),
      })
      if (!r.ok) throw new Error('Erro ao enviar resposta')
      setDone(decision)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erro desconhecido')
    } finally {
      setSub(false)
    }
  }

  // Loading
  if (loading) return (
    <div className="min-h-screen bg-white flex items-center justify-center">
      <Loader2 size={28} className="animate-spin text-indigo-500" />
    </div>
  )

  // Error
  if (error && !done) return (
    <div className="min-h-screen bg-white flex items-center justify-center px-4">
      <div className="max-w-md w-full text-center">
        <XCircle size={40} className="text-red-400 mx-auto mb-4" />
        <h1 className="text-gray-900 text-xl font-bold mb-2">Link inválido</h1>
        <p className="text-gray-500 text-sm">{error}</p>
      </div>
    </div>
  )

  // Done
  if (done) return (
    <div className="min-h-screen bg-white flex items-center justify-center px-4">
      <div className="max-w-md w-full text-center">
        {done === 'approved' ? (
          <>
            <CheckCircle size={52} className="text-emerald-500 mx-auto mb-5" />
            <h1 className="text-gray-900 text-2xl font-bold mb-3">Conteúdo aprovado!</h1>
            <p className="text-gray-500 text-base">
              Obrigado! Nossa equipe foi notificada e o conteúdo será publicado em breve.
            </p>
          </>
        ) : (
          <>
            <div className="w-14 h-14 rounded-full bg-amber-100 flex items-center justify-center mx-auto mb-5">
              <MessageSquare size={28} className="text-amber-600" />
            </div>
            <h1 className="text-gray-900 text-2xl font-bold mb-3">Feedback enviado</h1>
            <p className="text-gray-500 text-base">
              Recebemos sua revisão. Nossa equipe irá ajustar e enviar uma nova versão.
            </p>
          </>
        )}
      </div>
    </div>
  )

  // Already decided
  if (data?.already_decided) return (
    <div className="min-h-screen bg-white flex items-center justify-center px-4">
      <div className="max-w-md w-full text-center">
        <CheckCircle size={40} className="text-gray-300 mx-auto mb-4" />
        <h1 className="text-gray-900 text-xl font-bold mb-2">Resposta já registrada</h1>
        <p className="text-gray-500 text-sm">
          Decisão: <span className={data.decision === 'approved' ? 'text-emerald-600 font-medium' : 'text-amber-600 font-medium'}>
            {data.decision === 'approved' ? 'aprovado' : 'revisão solicitada'}
          </span>
        </p>
      </div>
    </div>
  )

  // Main approval view
  const title       = data?.title || data?.final_title || 'Conteúdo para revisão'
  const deadlineStr = fmtDeadline(data?.expires_at || data?.deadline || null)

  return (
    <div className="min-h-screen bg-gray-50 py-10 px-4">
      <div className="max-w-3xl mx-auto">

        {/* Agency header */}
        <div className="text-center mb-10">
          <p className="text-sm text-gray-400 uppercase tracking-widest mb-1 font-medium">
            Pareto Plus
          </p>
          <h1 className="text-3xl font-bold text-gray-900 mb-1">
            Aprovação de conteúdo
          </h1>
          {data?.client_name && (
            <p className="text-gray-500 text-sm">para {data.client_name}</p>
          )}
        </div>

        {/* Piece header */}
        <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6 mb-6">
          <div className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-xl bg-indigo-50 flex items-center justify-center shrink-0">
              <FileText size={18} className="text-indigo-600" />
            </div>
            <div className="min-w-0">
              <h2 className="text-xl font-bold text-gray-900 mb-1">{title}</h2>
              <div className="flex items-center gap-3 text-sm text-gray-400">
                {data?.word_count && (
                  <span>{data.word_count.toLocaleString('pt-BR')} palavras</span>
                )}
                {deadlineStr && (
                  <span className="flex items-center gap-1">
                    <Clock size={13} /> {deadlineStr}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Content */}
        <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-8 mb-6">
          {data?.body_markdown ? (
            <article
              className="text-gray-800 text-base leading-relaxed prose prose-slate max-w-none"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(data.body_markdown) }}
            />
          ) : (
            <p className="text-gray-400 text-center py-8">Conteúdo não disponível.</p>
          )}
        </div>

        {/* Feedback textarea */}
        <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6 mb-5">
          <label className="flex items-center gap-2 text-sm font-semibold text-gray-700 mb-3">
            <MessageSquare size={15} className="text-gray-400" />
            Comentário (opcional)
          </label>
          <textarea
            value={feedback}
            onChange={e => setFeedback(e.target.value)}
            rows={3}
            placeholder="Deixe um comentário — ajustes desejados, dúvidas, observações…"
            className="w-full border border-gray-200 rounded-xl px-4 py-3 text-sm text-gray-800 resize-none focus:outline-none focus:ring-2 focus:ring-indigo-300"
          />
        </div>

        {/* Action buttons */}
        <div className="grid grid-cols-2 gap-4 mb-6">
          <button
            onClick={() => decide('requested_changes')}
            disabled={submitting}
            className="flex items-center justify-center gap-2 py-4 rounded-2xl border-2 border-amber-200 bg-amber-50 hover:bg-amber-100 text-amber-700 font-semibold text-base transition-colors disabled:opacity-50"
          >
            {submitting ? <Loader2 size={18} className="animate-spin" /> : <ThumbsDown size={18} />}
            Solicitar revisão
          </button>
          <button
            onClick={() => decide('approved')}
            disabled={submitting}
            className="flex items-center justify-center gap-2 py-4 rounded-2xl bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-base transition-colors disabled:opacity-50 shadow-md"
          >
            {submitting ? <Loader2 size={18} className="animate-spin" /> : <ThumbsUp size={18} />}
            Aprovar conteúdo
          </button>
        </div>

        {deadlineStr && (
          <p className="text-center text-xs text-gray-400">{deadlineStr}</p>
        )}
      </div>
    </div>
  )
}
