'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { UserCheck, CheckCircle, Circle, ChevronRight, Loader2 } from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

const VERTICALS = [
  { value: 'sneakers',    label: 'Tênis / Calçados' },
  { value: 'fashion',     label: 'Moda' },
  { value: 'supplements', label: 'Suplementos / Nutrição' },
  { value: 'electronics', label: 'Eletrônicos' },
  { value: 'beauty',      label: 'Beleza' },
  { value: 'home',        label: 'Casa / Decoração' },
]

const STEPS = [
  { id: 'basics',      label: 'Dados básicos',              description: 'Confirme nome, domínio e informações da loja.' },
  { id: 'vertical',    label: 'Vertical e prompts',         description: 'Identifique a vertical e importe os prompts de AI Visibility.' },
  { id: 'rag',         label: 'Base de conhecimento',       description: 'Suba documentos: catálogo, política de trocas, história da marca.' },
  { id: 'integrations', label: 'Shopify & Merchant Center', description: 'Conecte as integrações para sincronização de produtos e feed.' },
  { id: 'competitors', label: 'Competidores',               description: 'Cadastre as marcas que competem no mesmo espaço de busca em IA.' },
  { id: 'audit',       label: 'Auditoria inicial',          description: 'Rode auditoria de schema e verifique robots.txt e llms.txt.' },
]

interface Onboarding {
  current_step: number
  steps_completed: string[]
  vertical: string | null
  completed_at: string | null
}

export default function OnboardingPage() {
  const params   = useParams()
  const router   = useRouter()
  const clientId = params.clientId as string

  const [ob,       setOb]       = useState<Onboarding | null>(null)
  const [loading,  setLoading]  = useState(true)
  const [saving,   setSaving]   = useState(false)
  const [vertical, setVertical] = useState('')
  const [seeding,  setSeeding]  = useState(false)
  const [seedDone, setSeedDone] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${API}/technical/${clientId}/onboarding`)
      const d = await r.json()
      setOb(d)
      if (d.vertical) setVertical(d.vertical)
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [clientId])

  useEffect(() => { load() }, [load])

  async function complete(stepId: string) {
    if (!ob) return
    const steps = [...new Set([...(ob.steps_completed || []), stepId])]
    const nextStep = Math.min(STEPS.findIndex(s => s.id === stepId) + 2, STEPS.length)
    setSaving(true)
    try {
      const r = await fetch(`${API}/technical/${clientId}/onboarding`, {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          steps_completed: steps,
          current_step:    nextStep,
          ...(stepId === 'vertical' && vertical ? { vertical } : {}),
        }),
      })
      setOb(await r.json())
    } finally { setSaving(false) }
  }

  async function seedPrompts() {
    if (!vertical) return
    setSeeding(true)
    try {
      await fetch(`${API}/technical/${clientId}/prompts/seed`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ vertical }),
      })
      setSeedDone(true)
    } finally { setSeeding(false) }
  }

  const completedIds = ob?.steps_completed || []

  if (loading) return (
    <div className="flex items-center justify-center py-24">
      <Loader2 size={28} className="animate-spin text-indigo-400" />
    </div>
  )

  const allDone = STEPS.every(s => completedIds.includes(s.id))

  return (
    <div className="p-6 space-y-6 max-w-3xl">
      <div>
        <h1 className="text-lg font-bold text-white flex items-center gap-2">
          <UserCheck size={20} className="text-indigo-400" />
          Wizard de Onboarding
        </h1>
        <p className="text-xs text-slate-500 mt-0.5">Setup completo do serviço AI Presence para este cliente</p>
      </div>

      {/* Progress */}
      <div className="flex items-center gap-2">
        {STEPS.map((step, i) => {
          const done = completedIds.includes(step.id)
          return (
            <div key={step.id} className="flex items-center gap-2">
              <div className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 ${
                done ? 'bg-emerald-500 text-white' : 'bg-[#1a1f2e] text-slate-500 border border-[#2a2f3e]'
              }`}>
                {done ? '✓' : i + 1}
              </div>
              {i < STEPS.length - 1 && (
                <div className={`h-px w-6 ${done ? 'bg-emerald-500' : 'bg-[#2a2f3e]'}`} />
              )}
            </div>
          )
        })}
        <p className="ml-2 text-xs text-slate-500">{completedIds.length}/{STEPS.length} etapas</p>
      </div>

      {allDone && (
        <div className="flex items-center gap-3 p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-xl text-emerald-300">
          <CheckCircle size={18} className="shrink-0" />
          <div>
            <p className="font-medium text-sm">Onboarding concluído!</p>
            <p className="text-xs mt-0.5 text-emerald-400/70">Todos os componentes estão configurados.</p>
          </div>
        </div>
      )}

      {/* Steps */}
      <div className="space-y-3">
        {STEPS.map((step, i) => {
          const done = completedIds.includes(step.id)
          const active = (ob?.current_step || 1) === i + 1 && !done

          return (
            <div key={step.id}
              className={`border rounded-xl p-4 transition-colors ${
                done   ? 'border-emerald-500/20 bg-emerald-500/5 opacity-70' :
                active ? 'border-indigo-500/40 bg-indigo-500/5' :
                'border-[#2a2f3e] bg-[#151b27]'
              }`}>
              <div className="flex items-start gap-3">
                <div className="mt-0.5">
                  {done
                    ? <CheckCircle size={18} className="text-emerald-400" />
                    : <Circle size={18} className="text-slate-600" />}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-white">{i + 1}. {step.label}</p>
                  <p className="text-xs text-slate-500 mt-0.5">{step.description}</p>

                  {/* Step-specific controls */}
                  {active && step.id === 'vertical' && (
                    <div className="mt-3 space-y-3">
                      <div className="grid grid-cols-3 gap-2">
                        {VERTICALS.map(v => (
                          <button key={v.value} onClick={() => setVertical(v.value)}
                            className={`text-xs px-3 py-2 rounded-lg border transition-colors ${
                              vertical === v.value
                                ? 'border-indigo-500 bg-indigo-500/20 text-white'
                                : 'border-[#2a2f3e] bg-[#1a1f2e] text-slate-400 hover:text-white'
                            }`}>
                            {v.label}
                          </button>
                        ))}
                      </div>
                      {vertical && (
                        <button onClick={seedPrompts} disabled={seeding || seedDone}
                          className="text-xs px-4 py-2 bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg text-slate-300 hover:text-white disabled:opacity-50 flex items-center gap-2 transition-colors">
                          {seeding ? <Loader2 size={11} className="animate-spin" /> :
                           seedDone ? <CheckCircle size={11} className="text-emerald-400" /> : null}
                          {seedDone ? 'Prompts importados!' : 'Importar prompts da vertical'}
                        </button>
                      )}
                    </div>
                  )}

                  {active && step.id === 'audit' && (
                    <div className="mt-3 flex gap-2 flex-wrap">
                      <a href={`/clients/${clientId}/technical/schema-audit`}
                        className="text-xs px-3 py-1.5 bg-[#1a1f2e] border border-[#2a2f3e] rounded text-slate-300 hover:text-white flex items-center gap-1">
                        Schema Audit <ChevronRight size={10} />
                      </a>
                      <a href={`/clients/${clientId}/technical/llms-txt`}
                        className="text-xs px-3 py-1.5 bg-[#1a1f2e] border border-[#2a2f3e] rounded text-slate-300 hover:text-white flex items-center gap-1">
                        llms.txt <ChevronRight size={10} />
                      </a>
                    </div>
                  )}
                </div>
                {!done && (
                  <button onClick={() => complete(step.id)} disabled={saving}
                    className={`shrink-0 h-7 px-3 text-xs rounded transition-colors flex items-center gap-1 disabled:opacity-50 ${
                      active
                        ? 'bg-indigo-600 hover:bg-indigo-500 text-white'
                        : 'bg-[#1a1f2e] border border-[#2a2f3e] text-slate-500 hover:text-white'
                    }`}>
                    {saving ? <Loader2 size={10} className="animate-spin" /> : null}
                    {active ? 'Concluir' : 'Pular'}
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
