'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  ArrowLeft, Loader2, CheckCircle, XCircle, Clock,
  RefreshCw, Send, AlertTriangle, Eye, ExternalLink,
} from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

interface Approval {
  id: string
  piece_id: string
  piece_title: string
  sent_to_email: string
  sent_at: string | null
  deadline: string | null
  status: string
  responded_at: string | null
  feedback: string | null
  auto_approve_on_deadline: boolean
  created_at: string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const STATUS_LABELS: Record<string, string> = {
  pending:           'Aguardando',
  approved:          'Aprovado',
  requested_changes: 'Revisão solicitada',
  cancelled:         'Cancelado',
}

const STATUS_STYLES: Record<string, string> = {
  pending:           'bg-yellow-900/30 text-yellow-300 border-yellow-700/40',
  approved:          'bg-emerald-900/30 text-emerald-300 border-emerald-700/40',
  requested_changes: 'bg-orange-900/30 text-orange-300 border-orange-700/40',
  cancelled:         'bg-slate-800/50 text-slate-500 border-slate-700/40',
}

function StatusIcon({ status }: { status: string }) {
  if (status === 'approved')          return <CheckCircle size={13} className="text-emerald-400" />
  if (status === 'requested_changes') return <AlertTriangle size={13} className="text-orange-400" />
  if (status === 'cancelled')         return <XCircle size={13} className="text-slate-500" />
  return <Clock size={13} className="text-yellow-400" />
}

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('pt-BR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
  })
}

function fmtDeadline(s: string | null) {
  if (!s) return '—'
  const d    = new Date(s)
  const now  = new Date()
  const diff = Math.ceil((d.getTime() - now.getTime()) / 86400000)
  const fmt  = d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
  if (diff < 0) return <span className="text-red-400">{fmt} (expirado)</span>
  if (diff === 0) return <span className="text-red-400">Hoje</span>
  if (diff === 1) return <span className="text-yellow-400">Amanhã</span>
  return <span className="text-slate-400">{fmt} ({diff}d)</span>
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ApprovalsPage() {
  const params   = useParams()
  const router   = useRouter()
  const clientId = params.clientId as string

  const [approvals, setApprovals] = useState<Approval[]>([])
  const [loading, setLoading]     = useState(true)
  const [filter, setFilter]       = useState('')
  const [resending, setResending] = useState<string | null>(null)

  const base = `${API}/content/${clientId}`

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const qs = filter ? `?status=${filter}` : ''
      const d  = await fetch(`${base}/approvals${qs}`).then(r => r.json())
      setApprovals(d.approvals || [])
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [base, filter])

  useEffect(() => { load() }, [load])

  async function resend(pieceId: string, email: string) {
    if (!confirm(`Reenviar aprovação para ${email}?`)) return
    setResending(pieceId)
    try {
      await fetch(`${base}/pieces/${pieceId}/send-for-approval`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ sent_to_email: email }),
      })
      await load()
    } finally { setResending(null) }
  }

  const FILTERS = [
    { value: '',                   label: 'Todas' },
    { value: 'pending',            label: 'Aguardando' },
    { value: 'approved',           label: 'Aprovadas' },
    { value: 'requested_changes',  label: 'Com feedback' },
    { value: 'cancelled',          label: 'Canceladas' },
  ]

  const stats = {
    pending:   approvals.filter(a => a.status === 'pending').length,
    approved:  approvals.filter(a => a.status === 'approved').length,
    changes:   approvals.filter(a => a.status === 'requested_changes').length,
  }

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={() => router.push(`/clients/${clientId}/content`)}
            className="text-slate-500 hover:text-white">
            <ArrowLeft size={18} />
          </button>
          <div>
            <h1 className="text-xl font-bold text-white">Aprovações</h1>
            <p className="text-xs text-slate-500 mt-0.5">Workflow de aprovação de conteúdo com clientes</p>
          </div>
        </div>
        <button onClick={load} className="text-slate-500 hover:text-white">
          <RefreshCw size={15} />
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: 'Aguardando resposta', value: stats.pending,  color: 'text-yellow-400' },
          { label: 'Aprovadas',           value: stats.approved, color: 'text-emerald-400' },
          { label: 'Com feedback',        value: stats.changes,  color: 'text-orange-400' },
        ].map(s => (
          <div key={s.label} className="bg-[#1a1f2e] rounded-xl p-4">
            <p className={`text-2xl font-bold ${s.color}`}>{s.value}</p>
            <p className="text-xs text-slate-500 mt-1">{s.label}</p>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2">
        {FILTERS.map(f => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={`text-xs px-3 py-1.5 rounded-md transition-colors ${
              filter === f.value ? 'bg-indigo-600 text-white' : 'bg-[#1a1f2e] text-slate-400 hover:text-white'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Table */}
      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 size={24} className="animate-spin text-indigo-400" />
        </div>
      ) : approvals.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <Send size={32} className="mx-auto mb-3 opacity-40" />
          <p>Nenhuma aprovação {filter ? 'neste filtro' : 'enviada ainda'}.</p>
          <button onClick={() => router.push(`/clients/${clientId}/content`)}
            className="text-xs text-indigo-400 hover:text-indigo-300 mt-3">
            Ir para peças →
          </button>
        </div>
      ) : (
        <div className="bg-[#1a1f2e] rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#2a2f3e]">
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Peça</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Enviado para</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Status</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Prazo</th>
                <th className="text-right px-4 py-3 text-slate-400 font-medium">Ações</th>
              </tr>
            </thead>
            <tbody>
              {approvals.map(a => (
                <tr key={a.id} className="border-b border-[#1f2433] hover:bg-[#1f2433] transition-colors">
                  <td className="px-4 py-3">
                    <button
                      onClick={() => router.push(`/clients/${clientId}/content/pieces/${a.piece_id}`)}
                      className="text-white hover:text-indigo-300 font-medium text-left transition-colors"
                    >
                      {a.piece_title}
                    </button>
                    <p className="text-xs text-slate-600 mt-0.5">Enviado {fmtDate(a.sent_at)}</p>
                  </td>
                  <td className="px-4 py-3 text-slate-400 text-xs">
                    {a.sent_to_email}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border ${STATUS_STYLES[a.status] || 'bg-slate-700 text-slate-400 border-slate-600'}`}>
                      <StatusIcon status={a.status} />
                      {STATUS_LABELS[a.status] || a.status}
                    </span>
                    {a.status === 'requested_changes' && a.feedback && (
                      <p className="text-xs text-orange-400/70 mt-1 max-w-[220px] truncate italic">
                        "{a.feedback}"
                      </p>
                    )}
                    {a.status === 'approved' && a.responded_at && (
                      <p className="text-xs text-slate-600 mt-0.5">{fmtDate(a.responded_at)}</p>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs">
                    {a.status === 'pending' ? (
                      <span>{fmtDeadline(a.deadline)}</span>
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1.5 justify-end">
                      <button
                        onClick={() => router.push(`/clients/${clientId}/content/pieces/${a.piece_id}`)}
                        className="flex items-center gap-1 text-xs text-slate-400 hover:text-white px-2 py-1 rounded hover:bg-[#2a2f3e] transition-colors"
                        title="Ver peça"
                      >
                        <Eye size={11} />
                      </button>
                      {['pending', 'requested_changes', 'cancelled'].includes(a.status) && (
                        <button
                          onClick={() => resend(a.piece_id, a.sent_to_email)}
                          disabled={resending === a.piece_id}
                          className="flex items-center gap-1 text-xs text-slate-400 hover:text-indigo-300 px-2 py-1 rounded hover:bg-[#2a2f3e] transition-colors disabled:opacity-50"
                          title="Reenviar aprovação"
                        >
                          {resending === a.piece_id
                            ? <Loader2 size={11} className="animate-spin" />
                            : <Send size={11} />
                          }
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Feedback detail */}
      {approvals.some(a => a.status === 'requested_changes' && a.feedback) && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-white">Feedbacks pendentes de ação</h3>
          {approvals
            .filter(a => a.status === 'requested_changes' && a.feedback)
            .map(a => (
              <div key={a.id} className="bg-[#1a1f2e] rounded-xl p-4 border-l-2 border-orange-500/50">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-white mb-1">{a.piece_title}</p>
                    <p className="text-xs text-orange-300/80 italic whitespace-pre-wrap">{a.feedback}</p>
                    <p className="text-xs text-slate-600 mt-2">De: {a.sent_to_email} · {fmtDate(a.responded_at)}</p>
                  </div>
                  <button
                    onClick={() => router.push(`/clients/${clientId}/content/pieces/${a.piece_id}`)}
                    className="shrink-0 flex items-center gap-1.5 text-xs bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 px-3 py-1.5 rounded-md transition-colors"
                  >
                    <ExternalLink size={11} /> Ajustar
                  </button>
                </div>
              </div>
            ))
          }
        </div>
      )}
    </div>
  )
}
