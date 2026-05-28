'use client'

import { useEffect, useState, useCallback } from 'react'
import Link from 'next/link'
import { useParams, useSearchParams, useRouter } from 'next/navigation'
import { createSupabaseBrowserClient } from '@/lib/supabase-browser'
import {
  CheckCircle, Loader2, ArrowRight, Copy,
  AlertCircle, Sparkles, Target,
} from 'lucide-react'

interface ClientRow {
  id:                              string
  name:                            string
  pixel_id:                        string
  agency_id:                       string
  ecommerce_platform:              string
  shopify_domain:                  string | null
  shopify_access_token:            string | null
  webhooks_configured:             boolean | null
  meta_access_token:               string | null
  meta_pixel_id:                   string | null
  meta_ad_account_id:              string | null
  ga4_measurement_id:              string | null
  ga4_api_secret:                  string | null
  google_ads_customer_id:          string | null
  google_ads_refresh_token:        string | null
  google_ads_aw_id:                string | null
  google_ads_conversion_action_id: string | null
  onboarding_completed:            boolean | null
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

function currentMonthDate(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`
}

export default function OnboardingPage() {
  const params       = useParams()
  const searchParams = useSearchParams()
  const router       = useRouter()
  const clientId     = params.clientId as string

  const [client,          setClient]          = useState<ClientRow | null>(null)
  const [loading,         setLoading]         = useState(true)
  const [registeringHooks, setRegisteringHooks] = useState(false)
  const [pixelOk,         setPixelOk]         = useState<boolean | null>(null)
  const [checkingPixel,   setCheckingPixel]   = useState(false)
  const [ga4Form,         setGa4Form]         = useState({ measurement_id: '', api_secret: '' })
  const [savingGa4,       setSavingGa4]       = useState(false)
  const [adsForm,         setAdsForm]         = useState({ customer_id: '', conversion_action_id: '', aw_id: '' })
  const [savingAds,       setSavingAds]       = useState(false)

  // Step 6 — Goals
  const [goalsForm,   setGoalsForm]   = useState({ revenue_goal: '', roas_goal: '', cpa_target: '' })
  const [goalsLoaded, setGoalsLoaded] = useState(false)
  const [savingGoals, setSavingGoals] = useState(false)

  const loadClient = useCallback(async () => {
    const supabase  = createSupabaseBrowserClient()
    const { data }  = await supabase
      .from('clients')
      .select('*')
      .eq('pixel_id', clientId)
      .maybeSingle()
    if (data) {
      setClient(data)
      setGa4Form({
        measurement_id: data.ga4_measurement_id || '',
        api_secret:     data.ga4_api_secret     || '',
      })
      setAdsForm({
        customer_id:          data.google_ads_customer_id          || '',
        conversion_action_id: data.google_ads_conversion_action_id || '',
        aw_id:                data.google_ads_aw_id                || '',
      })

      // Load goals for current month
      const month = currentMonthDate()
      const { data: goal } = await supabase
        .from('goals')
        .select('revenue_goal, roas_goal, cpa_target')
        .eq('client_id', data.id)
        .eq('month', month)
        .maybeSingle()
      if (goal) {
        setGoalsForm({
          revenue_goal: goal.revenue_goal ? String(goal.revenue_goal) : '',
          roas_goal:    goal.roas_goal    ? String(goal.roas_goal)    : '',
          cpa_target:   goal.cpa_target   ? String(goal.cpa_target)   : '',
        })
        setGoalsLoaded(!!(goal.revenue_goal || goal.roas_goal || goal.cpa_target))
      }
    }
    setLoading(false)
  }, [clientId])

  useEffect(() => { loadClient() }, [loadClient])

  useEffect(() => {
    if (searchParams.get('connected')) loadClient()
  }, [searchParams, loadClient])

  const checkPixel = useCallback(async () => {
    if (!client) return
    setCheckingPixel(true)
    const supabase   = createSupabaseBrowserClient()
    const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString()
    const { count }  = await supabase
      .from('tracking_events')
      .select('id', { count: 'exact', head: true })
      .eq('client_id', client.id)
      .gte('created_at', oneHourAgo)
    setPixelOk((count || 0) > 0)
    setCheckingPixel(false)
  }, [client])

  async function registerHooks() {
    setRegisteringHooks(true)
    try {
      await fetch(`${API_URL}/setup/shopify/${clientId}/webhooks`, { method: 'POST' })
      await loadClient()
    } finally {
      setRegisteringHooks(false)
    }
  }

  async function saveGa4() {
    setSavingGa4(true)
    const supabase = createSupabaseBrowserClient()
    await supabase.from('clients').update({
      ga4_measurement_id: ga4Form.measurement_id || null,
      ga4_api_secret:     ga4Form.api_secret     || null,
    }).eq('pixel_id', clientId)
    await loadClient()
    setSavingGa4(false)
  }

  async function saveAds() {
    setSavingAds(true)
    const supabase = createSupabaseBrowserClient()
    await supabase.from('clients').update({
      google_ads_customer_id:          adsForm.customer_id          || null,
      google_ads_conversion_action_id: adsForm.conversion_action_id || null,
      google_ads_aw_id:                adsForm.aw_id                || null,
    }).eq('pixel_id', clientId)
    await loadClient()
    setSavingAds(false)
  }

  async function saveGoals() {
    if (!client) return
    setSavingGoals(true)
    const supabase = createSupabaseBrowserClient()
    const month    = currentMonthDate()
    await supabase.from('goals').upsert({
      client_id:    client.id,
      agency_id:    client.agency_id,
      month,
      revenue_goal: goalsForm.revenue_goal ? Number(goalsForm.revenue_goal) : null,
      roas_goal:    goalsForm.roas_goal    ? Number(goalsForm.roas_goal)    : null,
      cpa_target:   goalsForm.cpa_target   ? Number(goalsForm.cpa_target)   : null,
    }, { onConflict: 'client_id,month' })
    setGoalsLoaded(!!(goalsForm.revenue_goal || goalsForm.roas_goal || goalsForm.cpa_target))
    setSavingGoals(false)
  }

  async function finalize() {
    const supabase = createSupabaseBrowserClient()
    await supabase.from('clients').update({ onboarding_completed: true }).eq('pixel_id', clientId)
    router.push(`/clients/${clientId}/dashboard`)
  }

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <Loader2 size={20} className="animate-spin text-slate-500" />
    </div>
  )

  if (!client) return (
    <div className="p-6 text-slate-400">Cliente não encontrado.</div>
  )

  const stepsBasic  = !!(client.name && client.shopify_domain && client.shopify_access_token)
  const stepsHooks  = !!client.webhooks_configured
  const stepsMeta   = !!(client.meta_access_token && client.meta_pixel_id)
  const stepsGoogle = !!(client.google_ads_refresh_token && client.google_ads_customer_id && client.google_ads_conversion_action_id)
  const stepsGa4    = !!(client.ga4_measurement_id && client.ga4_api_secret)
  const stepsPixel  = pixelOk === true
  const stepsGoals  = goalsLoaded

  const requiredDone  = stepsBasic && stepsHooks && stepsMeta && stepsPixel
  const totalRequired = 4
  const doneRequired  = [stepsBasic, stepsHooks, stepsMeta, stepsPixel].filter(Boolean).length
  const progress      = Math.round((doneRequired / totalRequired) * 100)

  const INPUT_SM = 'bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-xs text-white placeholder-slate-600 outline-none focus:border-indigo-500'

  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200 p-6">
      <div className="max-w-3xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <div className="flex items-center gap-2 text-indigo-400 mb-2">
            <Sparkles size={14} />
            <span className="text-xs font-semibold uppercase tracking-wide">Onboarding</span>
          </div>
          <h1 className="text-2xl font-bold text-white">{client.name}</h1>
          <p className="text-sm text-slate-500 mt-1">
            Conecte os serviços para começar a receber dados de tracking server-side
          </p>
        </div>

        {/* Progress bar */}
        <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5 mb-6">
          <div className="flex items-center justify-between mb-3">
            <p className="text-sm font-semibold text-white">
              {doneRequired} de {totalRequired} etapas obrigatórias
            </p>
            <p className="text-xs text-slate-500">{progress}%</p>
          </div>
          <div className="h-2 bg-[#0f1117] rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-indigo-500 to-purple-500 transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>

        {/* OAuth result banners */}
        {searchParams.get('connected') === 'meta' && (
          <Banner kind="success">Meta conectado! Token válido por 60 dias.</Banner>
        )}
        {searchParams.get('connected') === 'google' && (
          <Banner kind="success">Google Ads conectado!</Banner>
        )}
        {searchParams.get('error') && (
          <Banner kind="error">Erro ao conectar: {searchParams.get('error')}</Banner>
        )}

        {/* Step 1 — Shopify Webhooks */}
        <StepCard
          step={1}
          title="Webhooks Shopify"
          description="Registra automaticamente todos os webhooks (pedidos, carrinhos, refunds) na sua loja Shopify"
          done={stepsHooks}
          required
        >
          {!stepsBasic ? (
            <p className="text-sm text-yellow-400 flex items-center gap-2">
              <AlertCircle size={14} /> Configure o domínio e access token Shopify primeiro em
              <Link href={`/clients/${clientId}/settings`} className="underline">Settings</Link>
            </p>
          ) : stepsHooks ? (
            <p className="text-sm text-emerald-400">9 webhooks registrados.</p>
          ) : (
            <button
              onClick={registerHooks}
              disabled={registeringHooks}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors flex items-center gap-2"
            >
              {registeringHooks ? <><Loader2 size={14} className="animate-spin" /> Registrando…</> : 'Registrar webhooks'}
            </button>
          )}
        </StepCard>

        {/* Step 2 — Meta */}
        <StepCard
          step={2}
          title="Meta Conversions API"
          description="Envia eventos de Purchase, AddToCart e Checkout direto para o Meta Pixel via servidor (bypass iOS/ATT)"
          done={stepsMeta}
          required
        >
          {stepsMeta ? (
            <div className="text-sm text-emerald-400">
              <p>✓ Pixel: <span className="font-mono text-xs text-slate-400">{client.meta_pixel_id}</span></p>
              <p>✓ Ad Account: <span className="font-mono text-xs text-slate-400">{client.meta_ad_account_id}</span></p>
            </div>
          ) : (
            <a
              href={`/api/meta/oauth/start?clientId=${clientId}&next=onboarding`}
              className="inline-flex items-center gap-2 px-4 py-2 bg-[#1877F2] hover:bg-[#0e63ce] text-white text-sm font-medium rounded-lg transition-colors"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/>
              </svg>
              Conectar Meta
            </a>
          )}
        </StepCard>

        {/* Step 3 — Google Ads (optional) */}
        <StepCard
          step={3}
          title="Google Ads Conversion API"
          description="Envia conversões de Compra (e mid-funnel) direto para Google Ads via gclid"
          done={stepsGoogle}
          optional
        >
          <div className="space-y-3">
            {client.google_ads_refresh_token ? (
              <p className="text-sm text-emerald-400">✓ OAuth conectado</p>
            ) : (
              <a
                href={`/api/google-ads/oauth/start?clientId=${clientId}&next=onboarding`}
                className="inline-flex items-center gap-2 px-4 py-2 bg-[#0f1117] border border-[#2a2f3e] hover:border-indigo-500 text-slate-300 hover:text-white text-sm font-medium rounded-lg transition-colors"
              >
                Conectar Google Ads
              </a>
            )}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
              <input
                value={adsForm.customer_id}
                onChange={e => setAdsForm(f => ({ ...f, customer_id: e.target.value }))}
                placeholder="Customer ID (XXX-XXX-XXXX)"
                className={INPUT_SM}
              />
              <input
                value={adsForm.conversion_action_id}
                onChange={e => setAdsForm(f => ({ ...f, conversion_action_id: e.target.value }))}
                placeholder="Conversion ID (Compra)"
                className={INPUT_SM}
              />
              <input
                value={adsForm.aw_id}
                onChange={e => setAdsForm(f => ({ ...f, aw_id: e.target.value }))}
                placeholder="AW-XXXXXXXXXX"
                className={INPUT_SM}
              />
            </div>
            <button
              onClick={saveAds}
              disabled={savingAds}
              className="px-3 py-1.5 bg-[#1a1f2e] hover:bg-[#252b3b] border border-[#2a2f3e] text-slate-300 text-xs rounded-lg transition-colors disabled:opacity-50"
            >
              {savingAds ? 'Salvando…' : 'Salvar IDs'}
            </button>
          </div>
        </StepCard>

        {/* Step 4 — GA4 (optional) */}
        <StepCard
          step={4}
          title="Google Analytics 4"
          description="Envia eventos server-side para GA4 via Measurement Protocol — bypass de bloqueio de cookies"
          done={stepsGa4}
          optional
        >
          <div className="space-y-3">
            <p className="text-xs text-slate-500">
              Pegue em GA4 → Admin → Data Streams → seu stream → Measurement Protocol API secrets
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              <input
                value={ga4Form.measurement_id}
                onChange={e => setGa4Form(f => ({ ...f, measurement_id: e.target.value }))}
                placeholder="G-XXXXXXXXXX"
                className={INPUT_SM}
              />
              <input
                type="password"
                value={ga4Form.api_secret}
                onChange={e => setGa4Form(f => ({ ...f, api_secret: e.target.value }))}
                placeholder="API Secret"
                className={INPUT_SM}
              />
            </div>
            <button
              onClick={saveGa4}
              disabled={savingGa4}
              className="px-3 py-1.5 bg-[#1a1f2e] hover:bg-[#252b3b] border border-[#2a2f3e] text-slate-300 text-xs rounded-lg transition-colors disabled:opacity-50"
            >
              {savingGa4 ? 'Salvando…' : 'Salvar GA4'}
            </button>
          </div>
        </StepCard>

        {/* Step 5 — Pixel installation */}
        <StepCard
          step={5}
          title="Instalar pixel JS"
          description="Cole o snippet em snippets/et-tracker.liquid e renderize em theme.liquid antes de </body>"
          done={stepsPixel}
          required
        >
          <div className="space-y-3">
            <div className="bg-[#0f1117] border border-[#2a2f3e] rounded-lg p-3 text-xs font-mono text-slate-400 overflow-x-auto flex items-center justify-between gap-2">
              <span>{`{% render 'et-tracker' %}`}</span>
              <CopyBtn text={`{% render 'et-tracker' %}`} />
            </div>
            <p className="text-xs text-slate-500">
              Snippet completo em <code className="bg-[#0f1117] px-1 rounded">pixel/shopify-snippet.liquid</code>.
              Trocar <code className="bg-[#0f1117] px-1 rounded">CLIENT_ID = &apos;{clientId}&apos;</code>.
            </p>
            <p className="text-xs text-slate-500">
              Para a Order Status page (thank-you):
              Settings → Checkout → &quot;Additional scripts&quot; → cole <code className="bg-[#0f1117] px-1 rounded">pixel/shopify-order-status.html</code>
            </p>

            <div className="flex items-center gap-2">
              <button
                onClick={checkPixel}
                disabled={checkingPixel}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors flex items-center gap-2"
              >
                {checkingPixel ? <><Loader2 size={14} className="animate-spin" /> Verificando…</> : 'Verificar instalação'}
              </button>
              {pixelOk === true && (
                <span className="text-sm text-emerald-400 flex items-center gap-1.5">
                  <CheckCircle size={14} /> Eventos detectados na última hora
                </span>
              )}
              {pixelOk === false && (
                <span className="text-sm text-red-400 flex items-center gap-1.5">
                  <AlertCircle size={14} /> Nenhum evento ainda — abra a loja para gerar pageview
                </span>
              )}
            </div>
          </div>
        </StepCard>

        {/* Step 6 — Metas do mês (optional) */}
        <StepCard
          step={6}
          title="Metas do mês"
          description="Configure as metas de receita, ROAS e CPA para ativar alertas automáticos e acompanhar o progresso"
          done={stepsGoals}
          optional
        >
          <div className="space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
              <div>
                <label className="block text-xs text-slate-500 mb-1">Receita (R$)</label>
                <input
                  type="number"
                  value={goalsForm.revenue_goal}
                  onChange={e => setGoalsForm(f => ({ ...f, revenue_goal: e.target.value }))}
                  placeholder="50000"
                  className={INPUT_SM + ' w-full'}
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">ROAS mínimo</label>
                <input
                  type="number"
                  step="0.1"
                  value={goalsForm.roas_goal}
                  onChange={e => setGoalsForm(f => ({ ...f, roas_goal: e.target.value }))}
                  placeholder="3.0"
                  className={INPUT_SM + ' w-full'}
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">CPA máximo (R$)</label>
                <input
                  type="number"
                  value={goalsForm.cpa_target}
                  onChange={e => setGoalsForm(f => ({ ...f, cpa_target: e.target.value }))}
                  placeholder="120"
                  className={INPUT_SM + ' w-full'}
                />
              </div>
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={saveGoals}
                disabled={savingGoals}
                className="px-3 py-1.5 bg-[#1a1f2e] hover:bg-[#252b3b] border border-[#2a2f3e] text-slate-300 text-xs rounded-lg transition-colors disabled:opacity-50 flex items-center gap-1.5"
              >
                <Target size={11} />
                {savingGoals ? 'Salvando…' : 'Salvar metas'}
              </button>
              {stepsGoals && (
                <span className="text-xs text-emerald-400 flex items-center gap-1">
                  <CheckCircle size={11} /> Metas definidas
                </span>
              )}
              <Link href={`/clients/${clientId}/metas`} className="text-xs text-slate-500 hover:text-white ml-auto transition-colors">
                Ver página completa de metas →
              </Link>
            </div>
          </div>
        </StepCard>

        {/* Finalize */}
        <div className="mt-8 flex items-center justify-between gap-3">
          <Link
            href={`/clients/${clientId}/settings`}
            className="text-sm text-slate-500 hover:text-white transition-colors"
          >
            Configurações avançadas →
          </Link>
          <button
            onClick={finalize}
            disabled={!requiredDone}
            className="flex items-center gap-2 px-6 py-3 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-all"
          >
            {requiredDone ? 'Ir para o dashboard' : `Faltam ${totalRequired - doneRequired} obrigatórias`}
            <ArrowRight size={14} />
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Helper components ─────────────────────────────────────────────────────────

function CopyBtn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 2000) }}
      className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white transition-colors shrink-0"
    >
      {copied ? <><CheckCircle size={12} className="text-emerald-400" /> Copiado!</> : <><Copy size={12} /> Copiar</>}
    </button>
  )
}

function Banner({ kind, children }: { kind: 'success' | 'error', children: React.ReactNode }) {
  const colors = kind === 'success'
    ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
    : 'bg-red-500/10 border-red-500/20 text-red-400'
  return (
    <div className={`flex items-center gap-2 ${colors} border text-sm rounded-lg px-4 py-3 mb-5`}>
      {kind === 'success' ? <CheckCircle size={15} /> : <AlertCircle size={15} />}
      {children}
    </div>
  )
}

function StepCard({
  step, title, description, done, required, optional, children,
}: {
  step:        number
  title:       string
  description: string
  done:        boolean
  required?:   boolean
  optional?:   boolean
  children:    React.ReactNode
}) {
  return (
    <div className={`bg-[#1a1f2e] border rounded-xl p-5 mb-3 transition-colors ${done ? 'border-emerald-500/30' : 'border-[#2a2f3e]'}`}>
      <div className="flex items-start gap-4">
        <div className={`mt-0.5 shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold ${done ? 'bg-emerald-500/20 text-emerald-400' : 'bg-[#0f1117] text-slate-500 border border-[#2a2f3e]'}`}>
          {done ? <CheckCircle size={14} /> : step}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <h3 className="text-sm font-semibold text-white">{title}</h3>
            {optional && <span className="text-xs text-slate-500">opcional</span>}
            {required && <span className="text-xs text-indigo-400">obrigatório</span>}
          </div>
          <p className="text-xs text-slate-500 mb-3">{description}</p>
          {children}
        </div>
      </div>
    </div>
  )
}
