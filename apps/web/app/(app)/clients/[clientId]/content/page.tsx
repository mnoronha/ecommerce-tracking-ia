'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  PenLine, BookOpen, FileText, Layers, DollarSign,
  Plus, Loader2, RefreshCw, CheckCircle, AlertCircle,
  XCircle, Clock, Upload, Trash2, ChevronRight, Sparkles,
  Eye, BarChart2, Edit3, Send, FileUp,
} from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ─────────────────────────────────────────────────────────────────────

interface KnowledgeBase {
  id: string
  brand_voice: string | null
  brand_dos: string[] | null
  brand_donts: string[] | null
  forbidden_terms: string[] | null
  preferred_generation_model: string
  temperature: number
  total_documents: number
  total_chunks: number
  last_reindexed_at: string | null
  doc_count: number
}

interface Document {
  id: string
  title: string
  category: string
  word_count: number | null
  processing_status: string
  uploaded_at: string
}

interface Briefing {
  id: string
  working_title: string
  content_type: string
  status: string
  priority: string
  due_date: string | null
  created_at: string
}

interface Piece {
  id: string
  final_title: string | null
  status: string
  current_version: number
  published_at: string | null
  created_at: string
  briefing_id: string
}

interface CostSummary {
  total_cost_usd: number
  total_calls: number
  by_task: Record<string, number>
  by_model: Record<string, number>
}

// ── Helpers ───────────────────────────────────────────────────────────────────

type Tab = 'kb' | 'briefings' | 'pieces' | 'costs'

const CONTENT_TYPES: Record<string, string> = {
  comparison:    'Comparativo',
  guide:         'Guia',
  faq:           'FAQ',
  use_case:      'Caso de Uso',
  glossary:      'Glossário',
  pillar_article:'Artigo Pilar',
}

const STATUS_COLORS: Record<string, string> = {
  briefed:         'bg-slate-700 text-slate-300',
  generating:      'bg-blue-900/40 text-blue-300',
  generated:       'bg-indigo-900/40 text-indigo-300',
  reviewing:       'bg-yellow-900/40 text-yellow-300',
  pending_client:  'bg-orange-900/40 text-orange-300',
  approved:        'bg-emerald-900/40 text-emerald-300',
  published:       'bg-emerald-900/60 text-emerald-200',
  cancelled:       'bg-red-900/30 text-red-400',
  draft:           'bg-slate-700 text-slate-300',
  reviewed:        'bg-indigo-900/40 text-indigo-300',
}

const PROC_ICON: Record<string, React.ReactNode> = {
  completed: <CheckCircle size={13} className="text-emerald-400" />,
  failed:    <XCircle size={13} className="text-red-400" />,
  embedding: <Loader2 size={13} className="text-blue-400 animate-spin" />,
  chunking:  <Loader2 size={13} className="text-blue-400 animate-spin" />,
  extracting:<Loader2 size={13} className="text-blue-400 animate-spin" />,
  pending:   <Clock size={13} className="text-slate-500" />,
}

const CATEGORY_LABELS: Record<string, string> = {
  brand_identity:  'Identidade',
  products:        'Produtos',
  existing_content:'Conteúdo',
  market_data:     'Mercado',
  operations:      'Operações',
  other:           'Outro',
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_COLORS[status] || 'bg-slate-700 text-slate-300'}`}>
      {status.replace(/_/g, ' ')}
    </span>
  )
}

function fmt(n: number | null | undefined, dec = 0) {
  if (n == null) return '—'
  return n.toLocaleString('pt-BR', { minimumFractionDigits: dec, maximumFractionDigits: dec })
}

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('pt-BR')
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ContentPage() {
  const params   = useParams()
  const router   = useRouter()
  const clientId = params.clientId as string

  const [tab, setTab]     = useState<Tab>('kb')
  const [loading, setLoading] = useState(true)

  // KB
  const [kb, setKb]           = useState<KnowledgeBase | null>(null)
  const [docs, setDocs]       = useState<Document[]>([])
  const [kbEditing, setKbEditing] = useState(false)
  const [kbForm, setKbForm]   = useState({ brand_voice: '', brand_dos: '', brand_donts: '', forbidden_terms: '' })
  const [showDocForm, setShowDocForm] = useState(false)
  const [docForm, setDocForm] = useState({ title: '', category: 'brand_identity', raw_text: '', source_type: 'manual_entry' })
  const [saving, setSaving]   = useState(false)
  const [docMode, setDocMode] = useState<'text' | 'file'>('text')
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadCategory, setUploadCategory] = useState('other')
  const [dragOver, setDragOver] = useState(false)
  const [indexingIds, setIndexingIds] = useState<Set<string>>(new Set())
  const pollingRefs = useRef<Record<string, ReturnType<typeof setInterval>>>({})

  // Briefings
  const [briefings, setBriefings]   = useState<Briefing[]>([])
  const [briefingFilter, setBriefingFilter] = useState('')
  const [showBriefingForm, setShowBriefingForm] = useState(false)
  const [bForm, setBForm] = useState({
    working_title: '', content_type: 'guide', target_query: '',
    target_audience: '', required_length: 'medium', priority: 'medium',
    additional_instructions: '', target_keywords: '',
  })
  const [generating, setGenerating] = useState<Record<string, boolean>>({})

  // Pieces
  const [pieces, setPieces] = useState<Piece[]>([])
  const [pieceFilter, setPieceFilter] = useState('')

  // Costs
  const [costs, setCosts] = useState<CostSummary | null>(null)

  const base = `${API}/content/${clientId}`

  // ── Loaders ──────────────────────────────────────────────────────────────────

  const loadKb = useCallback(async () => {
    try {
      const [kbRes, docsRes] = await Promise.all([
        fetch(`${base}/knowledge-base`).then(r => r.json()),
        fetch(`${base}/documents`).then(r => r.json()),
      ])
      setKb(kbRes)
      const fetchedDocs: Document[] = docsRes.documents || []
      setDocs(fetchedDocs)
      setKbForm({
        brand_voice:      kbRes.brand_voice || '',
        brand_dos:        (kbRes.brand_dos || []).join('\n'),
        brand_donts:      (kbRes.brand_donts || []).join('\n'),
        forbidden_terms:  (kbRes.forbidden_terms || []).join(', '),
      })
      // Auto-poll docs that are currently processing
      const IN_PROGRESS = ['pending', 'extracting', 'chunking', 'embedding']
      fetchedDocs
        .filter(d => IN_PROGRESS.includes(d.processing_status))
        .forEach(d => startPolling(d.id))
    } catch {}
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base])

  const loadBriefings = useCallback(async () => {
    const qs = briefingFilter ? `?status=${briefingFilter}` : ''
    const d  = await fetch(`${base}/briefings${qs}`).then(r => r.json()).catch(() => ({}))
    setBriefings(d.briefings || [])
  }, [base, briefingFilter])

  const loadPieces = useCallback(async () => {
    const qs = pieceFilter ? `?status=${pieceFilter}` : ''
    const d  = await fetch(`${base}/pieces${qs}`).then(r => r.json()).catch(() => ({}))
    setPieces(d.pieces || [])
  }, [base, pieceFilter])

  const loadCosts = useCallback(async () => {
    const d = await fetch(`${API}/content/costs`).then(r => r.json()).catch(() => null)
    setCosts(d)
  }, [])

  useEffect(() => {
    setLoading(true)
    loadKb().finally(() => setLoading(false))
  }, [loadKb])

  useEffect(() => {
    if (tab === 'briefings') loadBriefings()
    if (tab === 'pieces')    loadPieces()
    if (tab === 'costs')     loadCosts()
  }, [tab, loadBriefings, loadPieces, loadCosts])

  useEffect(() => {
    if (tab === 'briefings') loadBriefings()
  }, [briefingFilter])

  useEffect(() => {
    if (tab === 'pieces') loadPieces()
  }, [pieceFilter])

  // ── Actions ──────────────────────────────────────────────────────────────────

  async function saveKb() {
    setSaving(true)
    try {
      await fetch(`${base}/knowledge-base`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          brand_voice:     kbForm.brand_voice || null,
          brand_dos:       kbForm.brand_dos.split('\n').map(s => s.trim()).filter(Boolean),
          brand_donts:     kbForm.brand_donts.split('\n').map(s => s.trim()).filter(Boolean),
          forbidden_terms: kbForm.forbidden_terms.split(',').map(s => s.trim()).filter(Boolean),
        }),
      })
      setKbEditing(false)
      await loadKb()
    } finally {
      setSaving(false)
    }
  }

  async function createDocument() {
    if (!docForm.title.trim() || !docForm.raw_text.trim()) return
    setSaving(true)
    try {
      await fetch(`${base}/documents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(docForm),
      })
      setShowDocForm(false)
      setDocForm({ title: '', category: 'brand_identity', raw_text: '', source_type: 'manual_entry' })
      await loadKb()
    } finally {
      setSaving(false)
    }
  }

  function startPolling(docId: string) {
    if (pollingRefs.current[docId]) return
    setIndexingIds(prev => new Set(prev).add(docId))
    const iv = setInterval(async () => {
      try {
        const d = await fetch(`${base}/documents/${docId}/status`).then(r => r.json())
        if (d.processing_status === 'completed' || d.processing_status === 'failed') {
          clearInterval(pollingRefs.current[docId])
          delete pollingRefs.current[docId]
          setIndexingIds(prev => { const s = new Set(prev); s.delete(docId); return s })
          await loadKb()
        }
      } catch { /* ignore */ }
    }, 3000)
    pollingRefs.current[docId] = iv
  }

  async function uploadFileDoc() {
    if (!uploadFile) return
    setSaving(true)
    try {
      const fd = new FormData()
      fd.append('file', uploadFile)
      const res = await fetch(`${base}/documents/upload?category=${uploadCategory}`, {
        method: 'POST', body: fd,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        alert(err.detail || 'Erro ao fazer upload')
        return
      }
      const data = await res.json()
      setUploadFile(null)
      setShowDocForm(false)
      if (data.document_id) startPolling(data.document_id)
      await loadKb()
    } finally {
      setSaving(false)
    }
  }

  async function reindexDoc(docId: string) {
    await fetch(`${base}/documents/${docId}/index`, { method: 'POST' })
    startPolling(docId)
  }

  async function deleteDoc(docId: string) {
    if (!confirm('Desativar este documento da base de conhecimento?')) return
    await fetch(`${base}/documents/${docId}`, { method: 'DELETE' })
    await loadKb()
  }

  async function createBriefing() {
    if (!bForm.working_title.trim()) return
    setSaving(true)
    try {
      const keywords = bForm.target_keywords.split(',').map(s => s.trim()).filter(Boolean)
      await fetch(`${base}/briefings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...bForm,
          target_keywords: keywords.length ? keywords : undefined,
        }),
      })
      setShowBriefingForm(false)
      setBForm({ working_title: '', content_type: 'guide', target_query: '', target_audience: '', required_length: 'medium', priority: 'medium', additional_instructions: '', target_keywords: '' })
      await loadBriefings()
    } finally {
      setSaving(false)
    }
  }

  async function generatePiece(briefingId: string) {
    setGenerating(g => ({ ...g, [briefingId]: true }))
    try {
      await fetch(`${base}/briefings/${briefingId}/generate`, { method: 'POST' })
      setTimeout(loadBriefings, 3000)
    } finally {
      setGenerating(g => ({ ...g, [briefingId]: false }))
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────────

  if (loading) return (
    <div className="flex items-center justify-center h-96">
      <Loader2 size={24} className="animate-spin text-indigo-400" />
    </div>
  )

  const TABS = [
    { key: 'kb' as Tab,        label: 'Base de Conhecimento', icon: BookOpen   },
    { key: 'briefings' as Tab, label: 'Briefings',            icon: FileText   },
    { key: 'pieces' as Tab,    label: 'Peças',                icon: Layers     },
    { key: 'costs' as Tab,     label: 'Custos IA',            icon: DollarSign },
  ]

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <PenLine size={22} className="text-indigo-400" />
          <h1 className="text-xl font-bold text-white">Conteúdo IA</h1>
        </div>
        {kb && (
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span>{kb.total_documents} doc{kb.total_documents !== 1 ? 's' : ''}</span>
            <span>·</span>
            <span>{fmt(kb.total_chunks)} chunks</span>
            {kb.last_reindexed_at && (
              <>
                <span>·</span>
                <span>Indexado {fmtDate(kb.last_reindexed_at)}</span>
              </>
            )}
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1">
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-2 flex-1 justify-center px-3 py-2 rounded-md text-sm transition-colors ${
              tab === t.key ? 'bg-indigo-600 text-white font-medium' : 'text-slate-400 hover:text-white'
            }`}
          >
            <t.icon size={14} />
            {t.label}
          </button>
        ))}
      </div>

      {/* ── KB Tab ──────────────────────────────────────────────────────────── */}
      {tab === 'kb' && (
        <div className="space-y-5">
          {/* Brand config */}
          <div className="bg-[#1a1f2e] rounded-xl p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-semibold text-white">Configuração da Marca</h2>
              {!kbEditing ? (
                <button onClick={() => setKbEditing(true)} className="text-xs text-indigo-400 hover:text-indigo-300 flex items-center gap-1">
                  <Edit3 size={12} /> Editar
                </button>
              ) : (
                <div className="flex gap-2">
                  <button onClick={() => setKbEditing(false)} className="text-xs text-slate-400 hover:text-white">Cancelar</button>
                  <button onClick={saveKb} disabled={saving} className="text-xs bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1 rounded-md disabled:opacity-50">
                    {saving ? 'Salvando…' : 'Salvar'}
                  </button>
                </div>
              )}
            </div>
            {kbEditing ? (
              <div className="space-y-4">
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Tom de voz</label>
                  <textarea
                    value={kbForm.brand_voice}
                    onChange={e => setKbForm(f => ({ ...f, brand_voice: e.target.value }))}
                    rows={3}
                    placeholder="Descreva o tom de voz da marca…"
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white resize-none"
                  />
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="text-xs text-slate-400 mb-1 block">Sempre faz (um por linha)</label>
                    <textarea
                      value={kbForm.brand_dos}
                      onChange={e => setKbForm(f => ({ ...f, brand_dos: e.target.value }))}
                      rows={4}
                      placeholder="Usa linguagem inclusiva&#10;Cita fontes&#10;…"
                      className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white resize-none"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-slate-400 mb-1 block">Nunca faz (um por linha)</label>
                    <textarea
                      value={kbForm.brand_donts}
                      onChange={e => setKbForm(f => ({ ...f, brand_donts: e.target.value }))}
                      rows={4}
                      placeholder="Usa jargão&#10;Promessas exageradas&#10;…"
                      className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white resize-none"
                    />
                  </div>
                </div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Termos proibidos (separados por vírgula)</label>
                  <input
                    value={kbForm.forbidden_terms}
                    onChange={e => setKbForm(f => ({ ...f, forbidden_terms: e.target.value }))}
                    placeholder="inovador, revolucionário, único no mercado…"
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
                  />
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                {kb?.brand_voice ? (
                  <p className="text-sm text-slate-300">{kb.brand_voice}</p>
                ) : (
                  <p className="text-sm text-slate-500 italic">Tom de voz não configurado. Clique em Editar para configurar.</p>
                )}
                {kb?.brand_dos && kb.brand_dos.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {kb.brand_dos.map((d, i) => (
                      <span key={i} className="text-xs bg-emerald-900/30 text-emerald-300 px-2 py-0.5 rounded-full">✓ {d}</span>
                    ))}
                  </div>
                )}
                {kb?.brand_donts && kb.brand_donts.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {kb.brand_donts.map((d, i) => (
                      <span key={i} className="text-xs bg-red-900/30 text-red-300 px-2 py-0.5 rounded-full">✗ {d}</span>
                    ))}
                  </div>
                )}
                {kb?.forbidden_terms && kb.forbidden_terms.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {kb.forbidden_terms.map((t, i) => (
                      <span key={i} className="text-xs bg-slate-700 text-slate-300 px-2 py-0.5 rounded-full line-through">{t}</span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Documents */}
          <div className="bg-[#1a1f2e] rounded-xl p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-semibold text-white">Documentos ({docs.length})</h2>
              {!showDocForm && (
                <div className="flex gap-2">
                  <button
                    onClick={() => { setDocMode('file'); setShowDocForm(true) }}
                    className="flex items-center gap-1.5 text-xs bg-[#0f1117] hover:bg-[#2a2f3e] border border-[#2a2f3e] text-slate-300 px-3 py-1.5 rounded-md transition-colors"
                  >
                    <FileUp size={13} /> Upload PDF/DOCX
                  </button>
                  <button
                    onClick={() => { setDocMode('text'); setShowDocForm(true) }}
                    className="flex items-center gap-1.5 text-xs bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-md transition-colors"
                  >
                    <Plus size={13} /> Colar texto
                  </button>
                </div>
              )}
            </div>

            {showDocForm && docMode === 'file' && (
              <div className="mb-4 p-4 bg-[#0f1117] rounded-lg border border-[#2a2f3e] space-y-3">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium text-white">Upload de arquivo</p>
                  <span className="text-xs text-slate-500">PDF, DOCX ou TXT · máx 20 MB</span>
                </div>
                {/* Drag-and-drop zone */}
                <div
                  onDragOver={e => { e.preventDefault(); setDragOver(true) }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={e => {
                    e.preventDefault(); setDragOver(false)
                    const f = e.dataTransfer.files[0]
                    if (f) setUploadFile(f)
                  }}
                  className={`relative border-2 border-dashed rounded-xl p-8 text-center transition-colors cursor-pointer ${
                    dragOver ? 'border-indigo-500 bg-indigo-500/10' : 'border-[#2a2f3e] hover:border-indigo-600/50'
                  }`}
                  onClick={() => document.getElementById('file-input')?.click()}
                >
                  <input
                    id="file-input"
                    type="file"
                    accept=".pdf,.docx,.doc,.txt,.md"
                    className="sr-only"
                    onChange={e => { const f = e.target.files?.[0]; if (f) setUploadFile(f) }}
                  />
                  {uploadFile ? (
                    <div className="space-y-1">
                      <FileUp size={24} className="mx-auto text-indigo-400" />
                      <p className="text-sm text-white font-medium">{uploadFile.name}</p>
                      <p className="text-xs text-slate-500">{(uploadFile.size / 1024).toFixed(0)} KB</p>
                    </div>
                  ) : (
                    <div className="space-y-1">
                      <Upload size={24} className="mx-auto text-slate-600" />
                      <p className="text-sm text-slate-400">Arraste o arquivo aqui ou clique para selecionar</p>
                    </div>
                  )}
                </div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Categoria</label>
                  <select
                    value={uploadCategory}
                    onChange={e => setUploadCategory(e.target.value)}
                    className="w-full bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
                  >
                    {Object.entries(CATEGORY_LABELS).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                </div>
                <div className="flex gap-2 justify-end">
                  <button onClick={() => { setShowDocForm(false); setUploadFile(null) }} className="text-xs text-slate-400 hover:text-white px-3 py-1.5">Cancelar</button>
                  <button
                    onClick={uploadFileDoc}
                    disabled={saving || !uploadFile}
                    className="text-xs bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-1.5 rounded-md disabled:opacity-50"
                  >
                    {saving ? <><Loader2 size={11} className="animate-spin inline mr-1" />Enviando…</> : 'Enviar e Indexar'}
                  </button>
                </div>
              </div>
            )}

            {showDocForm && docMode === 'text' && (
              <div className="mb-4 p-4 bg-[#0f1117] rounded-lg border border-[#2a2f3e] space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs text-slate-400 mb-1 block">Título</label>
                    <input
                      value={docForm.title}
                      onChange={e => setDocForm(f => ({ ...f, title: e.target.value }))}
                      placeholder="Ex: Manual da marca 2026"
                      className="w-full bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-slate-400 mb-1 block">Categoria</label>
                    <select
                      value={docForm.category}
                      onChange={e => setDocForm(f => ({ ...f, category: e.target.value }))}
                      className="w-full bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
                    >
                      {Object.entries(CATEGORY_LABELS).map(([k, v]) => (
                        <option key={k} value={k}>{v}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Conteúdo (cole o texto)</label>
                  <textarea
                    value={docForm.raw_text}
                    onChange={e => setDocForm(f => ({ ...f, raw_text: e.target.value }))}
                    rows={8}
                    placeholder="Cole aqui o conteúdo do documento — descrição da marca, specs de produtos, conteúdo existente…"
                    className="w-full bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white resize-none font-mono"
                  />
                </div>
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setShowDocForm(false)} className="text-xs text-slate-400 hover:text-white px-3 py-1.5">Cancelar</button>
                  <button
                    onClick={createDocument}
                    disabled={saving || !docForm.title || !docForm.raw_text}
                    className="text-xs bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-1.5 rounded-md disabled:opacity-50"
                  >
                    {saving ? 'Indexando…' : 'Adicionar e Indexar'}
                  </button>
                </div>
              </div>
            )}

            {docs.length === 0 && !showDocForm ? (
              <div className="text-center py-10 text-slate-500">
                <BookOpen size={28} className="mx-auto mb-2 opacity-40" />
                <p className="text-sm">Nenhum documento. Adicione conteúdo da marca para o RAG funcionar.</p>
              </div>
            ) : (
              <div className="space-y-2">
                {docs.map(doc => {
                  const isPolling = indexingIds.has(doc.id)
                  const statusIcon = isPolling
                    ? <Loader2 size={13} className="text-indigo-400 animate-spin" />
                    : (PROC_ICON[doc.processing_status] || <Clock size={13} className="text-slate-500" />)
                  return (
                    <div key={doc.id} className="flex items-center justify-between py-2 px-3 rounded-lg hover:bg-[#0f1117] transition-colors group">
                      <div className="flex items-center gap-3 min-w-0">
                        <span className="shrink-0">{statusIcon}</span>
                        <div className="min-w-0">
                          <p className="text-sm text-white truncate">{doc.title}</p>
                          <p className="text-xs text-slate-500">
                            {CATEGORY_LABELS[doc.category] || doc.category}
                            {doc.word_count ? ` · ${fmt(doc.word_count)} palavras` : ''}
                            {' · '}{fmtDate(doc.uploaded_at)}
                            {isPolling && <span className="ml-2 text-indigo-400">indexando…</span>}
                            {doc.processing_status === 'failed' && <span className="ml-2 text-red-400">falhou</span>}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                        <button onClick={() => reindexDoc(doc.id)} className="text-slate-400 hover:text-indigo-400" title="Re-indexar">
                          <RefreshCw size={13} />
                        </button>
                        <button onClick={() => deleteDoc(doc.id)} className="text-slate-400 hover:text-red-400" title="Remover">
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Briefings Tab ──────────────────────────────────────────────────── */}
      {tab === 'briefings' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex gap-2">
              {['', 'briefed', 'generating', 'generated', 'reviewing', 'approved', 'published'].map(s => (
                <button
                  key={s}
                  onClick={() => setBriefingFilter(s)}
                  className={`text-xs px-3 py-1.5 rounded-md transition-colors ${
                    briefingFilter === s
                      ? 'bg-indigo-600 text-white'
                      : 'bg-[#1a1f2e] text-slate-400 hover:text-white'
                  }`}
                >
                  {s || 'Todos'}
                </button>
              ))}
            </div>
            <button
              onClick={() => setShowBriefingForm(true)}
              className="flex items-center gap-1.5 text-xs bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-md transition-colors"
            >
              <Plus size={13} /> Novo Briefing
            </button>
          </div>

          {showBriefingForm && (
            <div className="bg-[#1a1f2e] rounded-xl p-5 space-y-4 border border-indigo-600/30">
              <h3 className="font-medium text-white flex items-center gap-2">
                <FileText size={16} className="text-indigo-400" />
                Novo Briefing
              </h3>
              <div className="grid grid-cols-2 gap-4">
                <div className="col-span-2">
                  <label className="text-xs text-slate-400 mb-1 block">Título de trabalho *</label>
                  <input
                    value={bForm.working_title}
                    onChange={e => setBForm(f => ({ ...f, working_title: e.target.value }))}
                    placeholder="Ex: Comparativo Tênis para Corrida 2026"
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Tipo de conteúdo</label>
                  <select
                    value={bForm.content_type}
                    onChange={e => setBForm(f => ({ ...f, content_type: e.target.value }))}
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
                  >
                    {Object.entries(CONTENT_TYPES).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Comprimento</label>
                  <select
                    value={bForm.required_length}
                    onChange={e => setBForm(f => ({ ...f, required_length: e.target.value }))}
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
                  >
                    <option value="short">Curto (~800 palavras)</option>
                    <option value="medium">Médio (~1500 palavras)</option>
                    <option value="long">Longo (~2500 palavras)</option>
                    <option value="pillar">Pilar (~4000 palavras)</option>
                  </select>
                </div>
                <div className="col-span-2">
                  <label className="text-xs text-slate-400 mb-1 block">Pergunta de IA que esta peça responde</label>
                  <input
                    value={bForm.target_query}
                    onChange={e => setBForm(f => ({ ...f, target_query: e.target.value }))}
                    placeholder="Ex: Qual o melhor tênis para corrida em 2026?"
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Público-alvo</label>
                  <input
                    value={bForm.target_audience}
                    onChange={e => setBForm(f => ({ ...f, target_audience: e.target.value }))}
                    placeholder="Ex: Corredores amadores, 25-45 anos"
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Palavras-chave (vírgula)</label>
                  <input
                    value={bForm.target_keywords}
                    onChange={e => setBForm(f => ({ ...f, target_keywords: e.target.value }))}
                    placeholder="tênis corrida, running shoe, amortecimento…"
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Prioridade</label>
                  <select
                    value={bForm.priority}
                    onChange={e => setBForm(f => ({ ...f, priority: e.target.value }))}
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
                  >
                    <option value="high">Alta</option>
                    <option value="medium">Média</option>
                    <option value="low">Baixa</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">Instruções adicionais</label>
                  <input
                    value={bForm.additional_instructions}
                    onChange={e => setBForm(f => ({ ...f, additional_instructions: e.target.value }))}
                    placeholder="Mencionar promoção X, evitar assunto Y…"
                    className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
                  />
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <button onClick={() => setShowBriefingForm(false)} className="text-xs text-slate-400 hover:text-white px-3 py-1.5">Cancelar</button>
                <button
                  onClick={createBriefing}
                  disabled={saving || !bForm.working_title}
                  className="text-xs bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-1.5 rounded-md disabled:opacity-50"
                >
                  {saving ? 'Criando…' : 'Criar Briefing'}
                </button>
              </div>
            </div>
          )}

          {briefings.length === 0 ? (
            <div className="text-center py-16 text-slate-500">
              <FileText size={32} className="mx-auto mb-3 opacity-40" />
              <p>Nenhum briefing encontrado. Crie o primeiro.</p>
            </div>
          ) : (
            <div className="bg-[#1a1f2e] rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#2a2f3e]">
                    <th className="text-left px-4 py-3 text-slate-400 font-medium">Título</th>
                    <th className="text-left px-4 py-3 text-slate-400 font-medium">Tipo</th>
                    <th className="text-left px-4 py-3 text-slate-400 font-medium">Status</th>
                    <th className="text-right px-4 py-3 text-slate-400 font-medium">Ações</th>
                  </tr>
                </thead>
                <tbody>
                  {briefings.map(b => (
                    <tr key={b.id} className="border-b border-[#1f2433] hover:bg-[#1f2433] transition-colors">
                      <td className="px-4 py-3">
                        <p className="text-white font-medium">{b.working_title}</p>
                        <p className="text-xs text-slate-500">{fmtDate(b.created_at)}</p>
                      </td>
                      <td className="px-4 py-3 text-slate-400 text-xs">
                        {CONTENT_TYPES[b.content_type] || b.content_type}
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={b.status} />
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2 justify-end">
                          {b.status === 'briefed' && (
                            <button
                              onClick={() => generatePiece(b.id)}
                              disabled={generating[b.id]}
                              className="flex items-center gap-1 text-xs bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 px-2 py-1 rounded-md disabled:opacity-50 transition-colors"
                            >
                              {generating[b.id]
                                ? <Loader2 size={11} className="animate-spin" />
                                : <Sparkles size={11} />
                              }
                              Gerar
                            </button>
                          )}
                          {['generated', 'reviewing', 'approved', 'published'].includes(b.status) && (
                            <button
                              onClick={() => router.push(`/clients/${clientId}/content/pieces?briefing=${b.id}`)}
                              className="flex items-center gap-1 text-xs text-slate-400 hover:text-white px-2 py-1 rounded-md hover:bg-[#2a2f3e] transition-colors"
                            >
                              <Eye size={11} /> Ver peça
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
        </div>
      )}

      {/* ── Pieces Tab ─────────────────────────────────────────────────────── */}
      {tab === 'pieces' && (
        <div className="space-y-4">
          <div className="flex gap-2">
            {['', 'draft', 'reviewed', 'approved', 'published'].map(s => (
              <button
                key={s}
                onClick={() => setPieceFilter(s)}
                className={`text-xs px-3 py-1.5 rounded-md transition-colors ${
                  pieceFilter === s
                    ? 'bg-indigo-600 text-white'
                    : 'bg-[#1a1f2e] text-slate-400 hover:text-white'
                }`}
              >
                {s || 'Todas'}
              </button>
            ))}
          </div>

          {pieces.length === 0 ? (
            <div className="text-center py-16 text-slate-500">
              <Layers size={32} className="mx-auto mb-3 opacity-40" />
              <p>Nenhuma peça gerada ainda. Crie um briefing e clique em Gerar.</p>
            </div>
          ) : (
            <div className="bg-[#1a1f2e] rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#2a2f3e]">
                    <th className="text-left px-4 py-3 text-slate-400 font-medium">Título</th>
                    <th className="text-left px-4 py-3 text-slate-400 font-medium">Status</th>
                    <th className="text-right px-4 py-3 text-slate-400 font-medium">Versão</th>
                    <th className="text-right px-4 py-3 text-slate-400 font-medium">Ações</th>
                  </tr>
                </thead>
                <tbody>
                  {pieces.map(p => (
                    <tr key={p.id} className="border-b border-[#1f2433] hover:bg-[#1f2433] transition-colors">
                      <td className="px-4 py-3">
                        <p className="text-white font-medium">{p.final_title || 'Sem título'}</p>
                        <p className="text-xs text-slate-500">{fmtDate(p.created_at)}</p>
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={p.status} />
                      </td>
                      <td className="px-4 py-3 text-right text-slate-400 text-xs">
                        v{p.current_version}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2 justify-end">
                          <button
                            onClick={() => router.push(`/clients/${clientId}/content/pieces/${p.id}`)}
                            className="flex items-center gap-1 text-xs text-slate-400 hover:text-white px-2 py-1 rounded-md hover:bg-[#2a2f3e] transition-colors"
                          >
                            <Edit3 size={11} /> Editar
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── Costs Tab ──────────────────────────────────────────────────────── */}
      {tab === 'costs' && (
        <div className="space-y-4">
          {!costs ? (
            <div className="text-center py-16 text-slate-500">
              <Loader2 size={24} className="animate-spin mx-auto mb-3 text-indigo-400" />
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-4">
                <div className="bg-[#1a1f2e] rounded-xl p-5">
                  <p className="text-3xl font-bold text-white">
                    R$ {fmt(costs.total_cost_usd * 5.5, 2)}
                  </p>
                  <p className="text-slate-500 text-sm mt-1">Custo total (30 dias)</p>
                  <p className="text-xs text-slate-600 mt-0.5">≈ US$ {fmt(costs.total_cost_usd, 4)}</p>
                </div>
                <div className="bg-[#1a1f2e] rounded-xl p-5">
                  <p className="text-3xl font-bold text-white">{fmt(costs.total_calls)}</p>
                  <p className="text-slate-500 text-sm mt-1">Chamadas de IA</p>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="bg-[#1a1f2e] rounded-xl p-5">
                  <h3 className="text-sm font-semibold text-white mb-3">Por tarefa</h3>
                  <div className="space-y-2">
                    {Object.entries(costs.by_task).sort(([, a], [, b]) => b - a).map(([task, cost]) => (
                      <div key={task} className="flex items-center justify-between">
                        <span className="text-sm text-slate-300 capitalize">{task.replace(/_/g, ' ')}</span>
                        <span className="text-sm text-slate-400">US$ {cost.toFixed(4)}</span>
                      </div>
                    ))}
                    {Object.keys(costs.by_task).length === 0 && (
                      <p className="text-slate-500 text-sm">Sem dados ainda</p>
                    )}
                  </div>
                </div>

                <div className="bg-[#1a1f2e] rounded-xl p-5">
                  <h3 className="text-sm font-semibold text-white mb-3">Por modelo</h3>
                  <div className="space-y-2">
                    {Object.entries(costs.by_model).sort(([, a], [, b]) => b - a).map(([model, cost]) => (
                      <div key={model} className="flex items-center justify-between">
                        <span className="text-sm text-slate-300 font-mono text-xs">{model}</span>
                        <span className="text-sm text-slate-400">US$ {cost.toFixed(4)}</span>
                      </div>
                    ))}
                    {Object.keys(costs.by_model).length === 0 && (
                      <p className="text-slate-500 text-sm">Sem dados ainda</p>
                    )}
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
