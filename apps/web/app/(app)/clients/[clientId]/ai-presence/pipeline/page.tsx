'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  Rocket, Loader2, RefreshCw, CheckCircle, AlertTriangle, XCircle,
  BookOpen, BrainCircuit, ShieldCheck, Store, FileText, BarChart2, ChevronRight,
} from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

interface PipelineData {
  rag:       { configured: boolean; documents: number; status: string }
  prompts:   { configured: boolean; count: number; imports: number; status: string }
  schema:    { audited: boolean; score: number | null; issues: number | null; status: string }
  merchant:  { configured: boolean; score: number | null; status: string }
  content:   { published: number; in_progress: number; status: string }
  onboarding: { completed: boolean; step: number; vertical: string | null }
}

function StatusIcon({ status }: { status: string }) {
  if (status === 'ok')      return <CheckCircle size={16} className="text-emerald-400" />
  if (status === 'warning' || status === 'partial') return <AlertTriangle size={16} className="text-yellow-400" />
  if (status === 'missing' || status === 'pending') return <XCircle size={16} className="text-slate-500" />
  return <AlertTriangle size={16} className="text-orange-400" />
}

function PipelineCard({
  icon: Icon, title, status, detail, href,
}: {
  icon: React.ElementType; title: string; status: string; detail: string; href?: string
}) {
  const borderColor = status === 'ok' ? 'border-emerald-500/30'
    : status === 'warning' || status === 'partial' ? 'border-yellow-500/30'
    : 'border-[#2a2f3e]'

  const content = (
    <div className={`bg-[#151b27] border ${borderColor} rounded-xl p-4 flex items-center gap-4 transition-colors ${href ? 'hover:bg-[#1a1f2e] cursor-pointer' : ''}`}>
      <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
        status === 'ok' ? 'bg-emerald-500/10' : status === 'warning' ? 'bg-yellow-500/10' : 'bg-slate-500/10'
      }`}>
        <Icon size={18} className={
          status === 'ok' ? 'text-emerald-400' : status === 'warning' ? 'text-yellow-400' : 'text-slate-500'
        } />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-white">{title}</p>
        <p className="text-xs text-slate-500 mt-0.5">{detail}</p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <StatusIcon status={status} />
        {href && <ChevronRight size={14} className="text-slate-600" />}
      </div>
    </div>
  )

  if (href) return <a href={href}>{content}</a>
  return content
}

export default function PipelinePage() {
  const params   = useParams()
  const router   = useRouter()
  const clientId = params.clientId as string

  const [data,    setData]    = useState<PipelineData | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${API}/technical/${clientId}/pipeline`)
      setData(await r.json())
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [clientId])

  useEffect(() => { load() }, [load])

  if (loading) return (
    <div className="flex items-center justify-center py-24">
      <Loader2 size={28} className="animate-spin text-indigo-400" />
    </div>
  )

  const stages = data ? [
    {
      icon:   BookOpen,
      title:  'Base de Conhecimento (RAG)',
      status: data.rag.status,
      detail: data.rag.documents > 0 ? `${data.rag.documents} documentos indexados` : 'Nenhum documento indexado',
      href:   `/clients/${clientId}/content`,
    },
    {
      icon:   BrainCircuit,
      title:  'Prompts Monitorados',
      status: data.prompts.status,
      detail: data.prompts.count > 0
        ? `${data.prompts.count} prompts · ${data.prompts.imports} imports`
        : 'Nenhum prompt configurado',
      href:   `/clients/${clientId}/ai-visibility`,
    },
    {
      icon:   ShieldCheck,
      title:  'Schema Markup',
      status: data.schema.status,
      detail: data.schema.score !== null
        ? `Health score: ${data.schema.score}/100 · ${data.schema.issues} problemas`
        : 'Auditoria não realizada',
      href:   `/clients/${clientId}/technical/schema-audit`,
    },
    {
      icon:   Store,
      title:  'Feed Merchant Center',
      status: data.merchant.status,
      detail: data.merchant.score !== null
        ? `Feed health: ${data.merchant.score}/100`
        : 'Merchant Center não configurado',
      href:   `/clients/${clientId}/merchant-center`,
    },
    {
      icon:   FileText,
      title:  'Conteúdo IA',
      status: data.content.status,
      detail: `${data.content.published} publicadas · ${data.content.in_progress} em progresso`,
      href:   `/clients/${clientId}/content`,
    },
    {
      icon:   BarChart2,
      title:  'Relatórios',
      status: 'info',
      detail: 'Relatório semanal ativo',
      href:   `/clients/${clientId}/reports`,
    },
  ] : []

  const doneCount = stages.filter(s => s.status === 'ok').length
  const totalStages = stages.length
  const pct = Math.round((doneCount / totalStages) * 100)

  return (
    <div className="p-6 space-y-6 max-w-3xl">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-bold text-white flex items-center gap-2">
            <Rocket size={20} className="text-indigo-400" />
            Pipeline AI Presence
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">Estado de cada componente do serviço de AI Presence</p>
        </div>
        <button onClick={load}
          className="h-8 w-8 flex items-center justify-center bg-[#1a1f2e] border border-[#2a2f3e] rounded hover:bg-[#252a3a] transition-colors">
          <RefreshCw size={13} className="text-slate-400" />
        </button>
      </div>

      {/* Progress bar */}
      <div className="bg-[#151b27] border border-[#2a2f3e] rounded-xl p-5">
        <div className="flex items-center justify-between mb-3">
          <p className="text-sm font-medium text-white">Progresso geral</p>
          <p className="text-sm font-bold text-white">{doneCount}/{totalStages}</p>
        </div>
        <div className="h-2 bg-[#0f1117] rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${pct === 100 ? 'bg-emerald-500' : 'bg-indigo-500'}`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <p className="text-xs text-slate-500 mt-2">
          {pct === 100
            ? '🎉 Todos os componentes ativos!'
            : `${totalStages - doneCount} componente${totalStages - doneCount !== 1 ? 's' : ''} pendente${totalStages - doneCount !== 1 ? 's' : ''}`}
        </p>
      </div>

      {/* Pipeline cards */}
      <div className="space-y-3">
        {stages.map(stage => (
          <PipelineCard key={stage.title} {...stage} />
        ))}
      </div>

      {/* Onboarding link if not complete */}
      {data && !data.onboarding.completed && (
        <a href={`/clients/${clientId}/technical/onboarding`}
          className="flex items-center justify-between p-4 bg-indigo-600/10 border border-indigo-500/20 rounded-xl hover:bg-indigo-600/20 transition-colors">
          <div>
            <p className="text-sm font-medium text-white">Wizard de onboarding incompleto</p>
            <p className="text-xs text-slate-500 mt-0.5">Conclua o setup para ativar todos os recursos — etapa {data.onboarding.step}/6</p>
          </div>
          <ChevronRight size={16} className="text-indigo-400 shrink-0" />
        </a>
      )}
    </div>
  )
}
