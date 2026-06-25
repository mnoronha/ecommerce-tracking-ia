'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  ArrowLeft, Loader2, Sparkles, Eye, CheckCircle,
  Tag, Calendar, Users, Search, AlignLeft, Edit3, Save,
} from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Briefing {
  id: string
  working_title: string
  content_type: string
  status: string
  priority: string
  target_query: string | null
  target_keywords: string[] | null
  target_audience: string | null
  products_to_mention: string[] | null
  competitors_to_cite: string[] | null
  required_length: string
  required_structure: string | null
  tone_override: string | null
  additional_instructions: string | null
  due_date: string | null
  source: string
  created_at: string
  updated_at: string | null
}

interface Piece {
  id: string
  final_title: string | null
  status: string
  current_version: number
  published_at: string | null
  created_at: string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const CONTENT_TYPES: Record<string, string> = {
  comparison:     'Comparativo',
  guide:          'Guia',
  faq:            'FAQ',
  use_case:       'Caso de Uso',
  glossary:       'Glossário',
  pillar_article: 'Artigo Pilar',
}

const LENGTH_LABELS: Record<string, string> = {
  short:  'Curto (~600 palavras)',
  medium: 'Médio (~1200 palavras)',
  long:   'Longo (~2500 palavras)',
}

const STATUS_COLORS: Record<string, string> = {
  briefed:        'text-slate-400',
  generating:     'text-blue-400',
  generated:      'text-indigo-400',
  reviewing:      'text-yellow-400',
  approved:       'text-emerald-400',
  published:      'text-emerald-300',
  cancelled:      'text-red-400',
  draft:          'text-slate-400',
  reviewed:       'text-indigo-400',
  pending_client: 'text-orange-400',
}

const PIECE_STATUS_COLORS: Record<string, string> = {
  draft:          'bg-slate-700/60 text-slate-300',
  generated:      'bg-indigo-900/40 text-indigo-300',
  reviewed:       'bg-blue-900/40 text-blue-300',
  approved:       'bg-emerald-900/40 text-emerald-300',
  published:      'bg-emerald-900/60 text-emerald-200',
  pending_client: 'bg-orange-900/40 text-orange-300',
}

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function BriefingDetailPage() {
  const params      = useParams()
  const router      = useRouter()
  const clientId    = params.clientId  as string
  const briefingId  = params.briefingId as string

  const [briefing, setBriefing]   = useState<Briefing | null>(null)
  const [pieces, setPieces]       = useState<Piece[]>([])
  const [loading, setLoading]     = useState(true)
  const [generating, setGenerating] = useState(false)
  const [editing, setEditing]     = useState(false)
  const [saving, setSaving]       = useState(false)
  const [form, setForm]           = useState<Partial<Briefing>>({})

  const base = `${API}/content/${clientId}`

  const load = useCallback(async () => {
    try {
      const [b, p] = await Promise.all([
        fetch(`${base}/briefings/${briefingId}`).then(r => r.json()),
        fetch(`${base}/pieces?briefing_id=${briefingId}`).then(r => r.json()),
      ])
      setBriefing(b)
      setForm(b)
      setPieces(p.pieces || [])
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [base, briefingId])

  useEffect(() => { load() }, [load])

  async function generate() {
    setGenerating(true)
    try {
      await fetch(`${base}/briefings/${briefingId}/generate`, { method: 'POST' })
      await new Promise(r => setTimeout(r, 12000))
      await load()
    } finally { setGenerating(false) }
  }

  async function saveEdits() {
    setSaving(true)
    try {
      const patch: Record<string, unknown> = {}
      const fields: (keyof Briefing)[] = [
        'working_title', 'content_type', 'target_query', 'target_audience',
        'required_length', 'tone_override', 'additional_instructions', 'priority',
        'required_structure', 'due_date',
      ]
      for (const f of fields) {
        if (form[f] !== briefing?.[f]) patch[f] = form[f]
      }
      if (Object.keys(patch).length === 0) { setEditing(false); return }
      await fetch(`${base}/briefings/${briefingId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      await load()
      setEditing(false)
    } finally { setSaving(false) }
  }

  function setF<K extends keyof Briefing>(k: K, v: Briefing[K]) {
    setForm(f => ({ ...f, [k]: v }))
  }

  if (loading) return (
    <div className="flex items-center justify-center h-96">
      <Loader2 size={24} className="animate-spin text-indigo-400" />
    </div>
  )

  if (!briefing) return <div className="p-6 text-red-400">Briefing não encontrado.</div>

  const latestPiece = pieces[0] ?? null
  const canGenerate = ['briefed', 'cancelled'].includes(briefing.status) || pieces.length === 0

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <button onClick={() => router.push(`/clients/${clientId}/content`)}
            className="text-slate-500 hover:text-white shrink-0">
            <ArrowLeft size={18} />
          </button>
          <div>
            <h1 className="text-xl font-bold text-white">{briefing.working_title}</h1>
            <p className={`text-xs mt-0.5 ${STATUS_COLORS[briefing.status] || 'text-slate-500'}`}>
              {briefing.status} · {CONTENT_TYPES[briefing.content_type] || briefing.content_type}
              {briefing.due_date ? ` · prazo ${fmtDate(briefing.due_date)}` : ''}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!editing && (
            <button onClick={() => setEditing(true)}
              className="flex items-center gap-1.5 text-xs bg-[#1a1f2e] hover:bg-[#2a2f3e] text-slate-300 px-3 py-2 rounded-md transition-colors">
              <Edit3 size={12} /> Editar
            </button>
          )}
          {editing && (
            <>
              <button onClick={() => { setForm(briefing); setEditing(false) }}
                className="text-xs text-slate-400 hover:text-white px-3 py-2">
                Cancelar
              </button>
              <button onClick={saveEdits} disabled={saving}
                className="flex items-center gap-1.5 text-xs bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-2 rounded-md transition-colors disabled:opacity-50">
                {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                Salvar
              </button>
            </>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-5">
        {/* ── Left: Briefing Detail ──────────────────────────────────────── */}
        <div className="col-span-2 space-y-4">
          <div className="bg-[#1a1f2e] rounded-xl p-5 space-y-5">
            {/* Title */}
            <div>
              <label className="text-xs text-slate-500 uppercase tracking-wide mb-1.5 block">Título de trabalho</label>
              {editing ? (
                <input
                  value={form.working_title || ''}
                  onChange={e => setF('working_title', e.target.value)}
                  className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-white text-sm focus:border-indigo-500 focus:outline-none"
                />
              ) : (
                <p className="text-white font-medium">{briefing.working_title}</p>
              )}
            </div>

            {/* Row: type + length + priority */}
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="text-xs text-slate-500 uppercase tracking-wide mb-1.5 block">Tipo</label>
                {editing ? (
                  <select
                    value={form.content_type || ''}
                    onChange={e => setF('content_type', e.target.value)}
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-white text-xs focus:border-indigo-500 focus:outline-none"
                  >
                    {Object.entries(CONTENT_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                ) : (
                  <p className="text-slate-300 text-sm">{CONTENT_TYPES[briefing.content_type] || briefing.content_type}</p>
                )}
              </div>
              <div>
                <label className="text-xs text-slate-500 uppercase tracking-wide mb-1.5 block">Tamanho</label>
                {editing ? (
                  <select
                    value={form.required_length || 'medium'}
                    onChange={e => setF('required_length', e.target.value)}
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-white text-xs focus:border-indigo-500 focus:outline-none"
                  >
                    {Object.entries(LENGTH_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                ) : (
                  <p className="text-slate-300 text-sm">{LENGTH_LABELS[briefing.required_length] || briefing.required_length}</p>
                )}
              </div>
              <div>
                <label className="text-xs text-slate-500 uppercase tracking-wide mb-1.5 block">Prioridade</label>
                {editing ? (
                  <select
                    value={form.priority || 'medium'}
                    onChange={e => setF('priority', e.target.value)}
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-white text-xs focus:border-indigo-500 focus:outline-none"
                  >
                    <option value="low">Baixa</option>
                    <option value="medium">Média</option>
                    <option value="high">Alta</option>
                  </select>
                ) : (
                  <p className="text-slate-300 text-sm capitalize">{briefing.priority}</p>
                )}
              </div>
            </div>

            {/* Target query */}
            <div>
              <label className="flex items-center gap-1.5 text-xs text-slate-500 uppercase tracking-wide mb-1.5">
                <Search size={11} /> Query alvo / intenção de busca
              </label>
              {editing ? (
                <input
                  value={form.target_query || ''}
                  onChange={e => setF('target_query', e.target.value)}
                  placeholder="ex: tênis para academia feminino"
                  className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-white text-sm focus:border-indigo-500 focus:outline-none"
                />
              ) : briefing.target_query ? (
                <p className="text-slate-300 text-sm">{briefing.target_query}</p>
              ) : <p className="text-slate-600 text-sm italic">Não definida</p>}
            </div>

            {/* Keywords */}
            {briefing.target_keywords?.length ? (
              <div>
                <label className="flex items-center gap-1.5 text-xs text-slate-500 uppercase tracking-wide mb-1.5">
                  <Tag size={11} /> Palavras-chave
                </label>
                <div className="flex flex-wrap gap-1.5">
                  {briefing.target_keywords.map((kw, i) => (
                    <span key={i} className="bg-[#0f1117] text-slate-300 text-xs px-2.5 py-1 rounded-full border border-[#2a2f3e]">
                      {kw}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}

            {/* Target audience */}
            <div>
              <label className="flex items-center gap-1.5 text-xs text-slate-500 uppercase tracking-wide mb-1.5">
                <Users size={11} /> Público-alvo
              </label>
              {editing ? (
                <input
                  value={form.target_audience || ''}
                  onChange={e => setF('target_audience', e.target.value)}
                  placeholder="ex: mulheres 25-40 anos, praticantes de esporte"
                  className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-white text-sm focus:border-indigo-500 focus:outline-none"
                />
              ) : briefing.target_audience ? (
                <p className="text-slate-300 text-sm">{briefing.target_audience}</p>
              ) : <p className="text-slate-600 text-sm italic">Não definido</p>}
            </div>

            {/* Tone override */}
            {(briefing.tone_override || editing) && (
              <div>
                <label className="text-xs text-slate-500 uppercase tracking-wide mb-1.5 block">Tom (override)</label>
                {editing ? (
                  <input
                    value={form.tone_override || ''}
                    onChange={e => setF('tone_override', e.target.value)}
                    placeholder="ex: mais técnico, descontraído, formal"
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-white text-sm focus:border-indigo-500 focus:outline-none"
                  />
                ) : <p className="text-slate-300 text-sm">{briefing.tone_override}</p>}
              </div>
            )}

            {/* Products / competitors */}
            {(briefing.products_to_mention?.length || briefing.competitors_to_cite?.length) ? (
              <div className="grid grid-cols-2 gap-4">
                {briefing.products_to_mention?.length ? (
                  <div>
                    <label className="text-xs text-slate-500 uppercase tracking-wide mb-1.5 block">Produtos a mencionar</label>
                    <ul className="space-y-1">
                      {briefing.products_to_mention.map((p, i) => (
                        <li key={i} className="text-slate-300 text-xs">• {p}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {briefing.competitors_to_cite?.length ? (
                  <div>
                    <label className="text-xs text-slate-500 uppercase tracking-wide mb-1.5 block">Concorrentes</label>
                    <ul className="space-y-1">
                      {briefing.competitors_to_cite.map((c, i) => (
                        <li key={i} className="text-slate-300 text-xs">• {c}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            ) : null}

            {/* Additional instructions */}
            <div>
              <label className="flex items-center gap-1.5 text-xs text-slate-500 uppercase tracking-wide mb-1.5">
                <AlignLeft size={11} /> Instruções adicionais
              </label>
              {editing ? (
                <textarea
                  rows={3}
                  value={form.additional_instructions || ''}
                  onChange={e => setF('additional_instructions', e.target.value)}
                  placeholder="Qualquer instrução extra para a IA…"
                  className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-white text-sm focus:border-indigo-500 focus:outline-none resize-none"
                />
              ) : briefing.additional_instructions ? (
                <p className="text-slate-300 text-sm whitespace-pre-wrap">{briefing.additional_instructions}</p>
              ) : <p className="text-slate-600 text-sm italic">Nenhuma</p>}
            </div>

            {/* Required structure */}
            {(briefing.required_structure || editing) && (
              <div>
                <label className="text-xs text-slate-500 uppercase tracking-wide mb-1.5 block">Estrutura exigida</label>
                {editing ? (
                  <textarea
                    rows={2}
                    value={form.required_structure || ''}
                    onChange={e => setF('required_structure', e.target.value)}
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-white text-sm focus:border-indigo-500 focus:outline-none resize-none"
                  />
                ) : <p className="text-slate-300 text-sm whitespace-pre-wrap">{briefing.required_structure}</p>}
              </div>
            )}
          </div>

          {/* Meta */}
          <div className="flex items-center gap-4 px-1">
            <p className="text-xs text-slate-600">
              <span className="text-slate-500">Criado:</span> {fmtDate(briefing.created_at)}
            </p>
            <p className="text-xs text-slate-600">
              <span className="text-slate-500">Fonte:</span> {briefing.source}
            </p>
            {briefing.due_date && (
              <p className="text-xs text-slate-600 flex items-center gap-1">
                <Calendar size={10} className="text-slate-500" />
                <span className="text-slate-500">Prazo:</span> {fmtDate(briefing.due_date)}
              </p>
            )}
          </div>
        </div>

        {/* ── Right: Piece state + actions ──────────────────────────────────── */}
        <div className="space-y-4">
          {/* Current state */}
          <div className="bg-[#1a1f2e] rounded-xl p-4">
            <h3 className="text-sm font-semibold text-white mb-3">Estado da peça</h3>

            {latestPiece ? (
              <div className="space-y-3">
                <div>
                  <p className="text-white font-medium text-sm">{latestPiece.final_title || '(sem título final)'}</p>
                  <span className={`inline-block text-xs px-2 py-0.5 rounded-full mt-1 ${PIECE_STATUS_COLORS[latestPiece.status] || 'bg-slate-700/60 text-slate-400'}`}>
                    {latestPiece.status}
                  </span>
                </div>
                <div className="flex items-center justify-between text-xs text-slate-500">
                  <span>v{latestPiece.current_version}</span>
                  <span>{fmtDate(latestPiece.created_at)}</span>
                </div>
                <div className="flex flex-col gap-1.5 pt-1">
                  <button
                    onClick={() => router.push(`/clients/${clientId}/content/pieces/${latestPiece.id}`)}
                    className="flex items-center justify-center gap-1.5 w-full text-xs bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 px-3 py-2 rounded-md transition-colors"
                  >
                    <Eye size={12} /> Abrir editor
                  </button>
                  <button
                    onClick={generate}
                    disabled={generating}
                    className="flex items-center justify-center gap-1.5 w-full text-xs bg-[#0f1117] hover:bg-[#2a2f3e] text-slate-400 hover:text-white px-3 py-2 rounded-md transition-colors disabled:opacity-50"
                  >
                    {generating ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                    {generating ? 'Gerando nova versão…' : 'Gerar nova versão'}
                  </button>
                </div>
              </div>
            ) : (
              <div className="text-center py-6 space-y-3">
                <p className="text-slate-500 text-xs">Nenhuma peça gerada ainda.</p>
                <button
                  onClick={generate}
                  disabled={generating || !canGenerate}
                  className="flex items-center justify-center gap-1.5 w-full text-xs bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-2 rounded-md transition-colors disabled:opacity-50"
                >
                  {generating ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                  {generating ? 'Gerando…' : 'Gerar com IA'}
                </button>
              </div>
            )}
          </div>

          {/* All pieces from this briefing */}
          {pieces.length > 1 && (
            <div className="bg-[#1a1f2e] rounded-xl p-4">
              <h3 className="text-sm font-semibold text-white mb-3">Todas as versões ({pieces.length})</h3>
              <div className="space-y-1.5">
                {pieces.map(p => (
                  <button
                    key={p.id}
                    onClick={() => router.push(`/clients/${clientId}/content/pieces/${p.id}`)}
                    className="w-full flex items-center justify-between p-2 rounded-lg hover:bg-[#0f1117] text-left transition-colors group"
                  >
                    <div>
                      <p className="text-xs text-slate-300 group-hover:text-white truncate max-w-[160px]">
                        {p.final_title || '(sem título)'}
                      </p>
                      <p className="text-xs text-slate-600 mt-0.5">{fmtDate(p.created_at)}</p>
                    </div>
                    <span className={`text-xs px-1.5 py-0.5 rounded-full ${PIECE_STATUS_COLORS[p.status] || 'bg-slate-700/60 text-slate-400'}`}>
                      {p.status}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Approved check */}
          {latestPiece?.status === 'approved' && (
            <div className="bg-emerald-900/20 border border-emerald-800/40 rounded-xl p-4 flex items-start gap-3">
              <CheckCircle size={16} className="text-emerald-400 shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-emerald-300">Conteúdo aprovado</p>
                <p className="text-xs text-emerald-500/80 mt-0.5">Pronto para publicar ou enviar ao cliente.</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
