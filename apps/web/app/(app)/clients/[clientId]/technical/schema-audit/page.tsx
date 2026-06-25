'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'next/navigation'
import {
  ShieldCheck, RefreshCw, Loader2, AlertTriangle, CheckCircle,
  XCircle, Play, Code, ExternalLink, ChevronDown, ChevronUp, Zap,
} from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

interface Audit {
  id: string
  status: string
  pages_audited: number
  schema_health_score: number | null
  issues_found: number
  completed_at: string | null
  created_at: string
  error?: string
  summary: {
    high_issues: number
    medium_issues: number
    low_issues: number
    schema_types_missing: string[]
  } | null
}

interface Issue {
  id: string
  page_url: string
  page_type: string
  issue_type: string
  schema_type: string
  severity: 'high' | 'medium' | 'low'
  details: Record<string, unknown> | null
  status: string
  generated_markup: string | null
}

const SEV_STYLE: Record<string, string> = {
  high:   'text-red-400 bg-red-500/10 border-red-500/30',
  medium: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30',
  low:    'text-slate-400 bg-slate-500/10 border-slate-500/30',
}

const SEV_LABEL: Record<string, string> = { high: 'Alta', medium: 'Média', low: 'Baixa' }

const ISSUE_LABELS: Record<string, string> = {
  missing:    'Ausente',
  malformed:  'Malformado',
  incomplete: 'Incompleto',
}

function ScoreRing({ score }: { score: number }) {
  const color = score >= 80 ? '#10b981' : score >= 50 ? '#f59e0b' : '#ef4444'
  const r = 42, circ = 2 * Math.PI * r
  const dash = (score / 100) * circ
  return (
    <svg width="110" height="110" viewBox="0 0 110 110">
      <circle cx="55" cy="55" r={r} fill="none" stroke="#1a1f2e" strokeWidth="10" />
      <circle cx="55" cy="55" r={r} fill="none" stroke={color} strokeWidth="10"
        strokeDasharray={`${dash} ${circ}`} strokeDashoffset={circ / 4}
        strokeLinecap="round" />
      <text x="55" y="59" textAnchor="middle" fill={color} fontSize="22" fontWeight="bold">{score}</text>
      <text x="55" y="74" textAnchor="middle" fill="#94a3b8" fontSize="10">/ 100</text>
    </svg>
  )
}

function IssueCard({ issue, clientId }: { issue: Issue; clientId: string }) {
  const [open, setOpen]         = useState(false)
  const [generating, setGen]    = useState(false)
  const [applying, setApplying] = useState(false)
  const [markup, setMarkup]     = useState(issue.generated_markup || '')
  const [applied, setApplied]   = useState(issue.status === 'fixed')
  const [instructions, setInstr] = useState('')

  const pixelId = clientId

  async function generate() {
    setGen(true)
    try {
      const r = await fetch(`${API}/technical/${pixelId}/schema/issues/${issue.id}/generate`, { method: 'POST' })
      const d = await r.json()
      setMarkup(d.markup_json || '')
      setOpen(true)
    } finally { setGen(false) }
  }

  async function apply() {
    setApplying(true)
    try {
      const r = await fetch(`${API}/technical/${pixelId}/schema/issues/${issue.id}/apply`, { method: 'POST' })
      const d = await r.json()
      if (d.applied) { setApplied(true) }
      if (d.instructions) { setInstr(d.instructions) }
    } finally { setApplying(false) }
  }

  return (
    <div className={`border rounded-xl p-4 ${applied ? 'border-emerald-500/30 bg-emerald-500/5 opacity-60' : 'border-[#2a2f3e] bg-[#151b27]'}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${SEV_STYLE[issue.severity]}`}>
              {SEV_LABEL[issue.severity]}
            </span>
            <span className="text-xs text-slate-500">{ISSUE_LABELS[issue.issue_type] || issue.issue_type}</span>
            <span className="text-xs font-medium text-indigo-300">{issue.schema_type}</span>
          </div>
          <p className="text-xs text-slate-400 truncate">{issue.page_url}</p>
          <p className="text-[10px] text-slate-600 mt-0.5 capitalize">{issue.page_type}</p>
        </div>
        {applied ? (
          <CheckCircle size={16} className="text-emerald-400 shrink-0" />
        ) : (
          <div className="flex items-center gap-1.5 shrink-0">
            {!markup && (
              <button onClick={generate} disabled={generating}
                className="h-7 px-2.5 text-xs bg-[#1a1f2e] border border-[#2a2f3e] rounded text-slate-300 hover:text-white flex items-center gap-1 transition-colors disabled:opacity-50">
                {generating ? <Loader2 size={11} className="animate-spin" /> : <Code size={11} />}
                Gerar
              </button>
            )}
            {markup && (
              <button onClick={apply} disabled={applying}
                className="h-7 px-2.5 text-xs bg-indigo-600 hover:bg-indigo-500 rounded text-white flex items-center gap-1 transition-colors disabled:opacity-50">
                {applying ? <Loader2 size={11} className="animate-spin" /> : <Zap size={11} />}
                Aplicar
              </button>
            )}
            {markup && (
              <button onClick={() => setOpen(o => !o)}
                className="h-7 w-7 flex items-center justify-center bg-[#1a1f2e] border border-[#2a2f3e] rounded hover:bg-[#252a3a] transition-colors">
                {open ? <ChevronUp size={12} className="text-slate-400" /> : <ChevronDown size={12} className="text-slate-400" />}
              </button>
            )}
          </div>
        )}
      </div>

      {open && markup && (
        <pre className="mt-3 p-3 bg-[#0f1117] rounded-lg text-[10px] text-emerald-300 overflow-x-auto leading-relaxed border border-[#1a1f2e]">
          {markup}
        </pre>
      )}

      {instructions && (
        <div className="mt-3 p-3 bg-amber-500/5 border border-amber-500/20 rounded-lg text-xs text-amber-300 whitespace-pre-wrap">
          {instructions}
        </div>
      )}
    </div>
  )
}

export default function SchemaAuditPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [audit,    setAudit]    = useState<Audit | null>(null)
  const [issues,   setIssues]   = useState<Issue[]>([])
  const [loading,  setLoading]  = useState(true)
  const [starting, setStarting] = useState(false)
  const [filter,   setFilter]   = useState<string>('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${API}/technical/${clientId}/schema/audit/latest`)
      const d = await r.json()
      setAudit(d.audit || null)
      setIssues(d.issues || [])
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [clientId])

  useEffect(() => { load() }, [load])

  async function startAudit() {
    setStarting(true)
    try {
      await fetch(`${API}/technical/${clientId}/schema/audit`, { method: 'POST' })
      setTimeout(load, 3000)
    } finally { setStarting(false) }
  }

  const filtered = filter ? issues.filter(i => i.severity === filter || i.schema_type === filter) : issues
  const schemaTypes = [...new Set(issues.map(i => i.schema_type))]

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-bold text-white flex items-center gap-2">
            <ShieldCheck size={20} className="text-indigo-400" />
            Schema Markup Auditor
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">Verifica presença e qualidade de schema JSON-LD nas páginas do site</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} className="h-8 w-8 flex items-center justify-center bg-[#1a1f2e] border border-[#2a2f3e] rounded hover:bg-[#252a3a] transition-colors">
            {loading ? <Loader2 size={13} className="animate-spin text-slate-400" /> : <RefreshCw size={13} className="text-slate-400" />}
          </button>
          <button onClick={startAudit} disabled={starting || audit?.status === 'running'}
            className="h-8 px-4 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded text-xs text-white flex items-center gap-1.5 transition-colors">
            {starting || audit?.status === 'running'
              ? <><Loader2 size={13} className="animate-spin" /> Auditando...</>
              : <><Play size={13} /> Auditar site</>}
          </button>
        </div>
      </div>

      {/* Score + summary */}
      {audit && audit.status === 'completed' && (
        <div className="grid grid-cols-4 gap-4">
          <div className="col-span-1 bg-[#151b27] border border-[#2a2f3e] rounded-xl p-5 flex flex-col items-center justify-center">
            <ScoreRing score={audit.schema_health_score || 0} />
            <p className="text-xs text-slate-500 mt-2">Schema Health</p>
          </div>
          <div className="col-span-3 grid grid-cols-3 gap-4">
            {[
              { label: 'Páginas auditadas', value: audit.pages_audited, color: 'text-white' },
              { label: 'Problemas encontrados', value: audit.issues_found, color: audit.issues_found > 0 ? 'text-red-400' : 'text-emerald-400' },
              { label: 'Alta prioridade', value: audit.summary?.high_issues || 0, color: 'text-red-400' },
            ].map(s => (
              <div key={s.label} className="bg-[#151b27] border border-[#2a2f3e] rounded-xl p-4">
                <p className={`text-2xl font-bold ${s.color}`}>{s.value}</p>
                <p className="text-xs text-slate-500 mt-1">{s.label}</p>
              </div>
            ))}
            {audit.summary?.schema_types_missing && audit.summary.schema_types_missing.length > 0 && (
              <div className="col-span-3 bg-[#151b27] border border-amber-500/20 rounded-xl p-4">
                <p className="text-xs text-slate-400 mb-2">Schemas ausentes:</p>
                <div className="flex flex-wrap gap-2">
                  {audit.summary.schema_types_missing.map(t => (
                    <span key={t} className="text-xs px-2 py-0.5 bg-amber-500/10 border border-amber-500/20 rounded text-amber-400">{t}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Running state */}
      {audit?.status === 'running' && (
        <div className="flex items-center gap-3 p-4 bg-indigo-500/10 border border-indigo-500/20 rounded-xl text-indigo-300 text-sm">
          <Loader2 size={16} className="animate-spin shrink-0" />
          Auditoria em andamento — as páginas estão sendo analisadas...
        </div>
      )}

      {/* Failed state */}
      {audit?.status === 'failed' && (
        <div className="flex items-center gap-3 p-4 bg-red-500/10 border border-red-500/20 rounded-xl text-red-300 text-sm">
          <XCircle size={16} className="shrink-0" />
          {audit.error || 'Auditoria falhou. Verifique as credenciais Shopify nas configurações.'}
        </div>
      )}

      {/* Empty */}
      {!loading && !audit && (
        <div className="text-center py-16">
          <ShieldCheck size={40} className="text-slate-600 mx-auto mb-4" />
          <p className="text-slate-400 text-sm font-medium">Nenhuma auditoria ainda</p>
          <p className="text-slate-600 text-xs mt-1 max-w-xs mx-auto">
            Clique em "Auditar site" para verificar a presença de schema markup nas páginas do cliente.
          </p>
        </div>
      )}

      {/* Issues list */}
      {issues.length > 0 && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-white">{issues.length} problema{issues.length !== 1 ? 's' : ''} encontrado{issues.length !== 1 ? 's' : ''}</h2>
            <div className="flex items-center gap-2">
              {['', 'high', 'medium', 'low', ...schemaTypes].map(f => (
                <button key={f} onClick={() => setFilter(f)}
                  className={`text-[10px] px-2 py-1 rounded transition-colors ${filter === f ? 'bg-indigo-600 text-white' : 'bg-[#1a1f2e] text-slate-400 hover:text-white'}`}>
                  {f || 'Todos'}
                </button>
              ))}
            </div>
          </div>
          <div className="space-y-3">
            {filtered.map(issue => (
              <IssueCard key={issue.id} issue={issue} clientId={clientId} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
