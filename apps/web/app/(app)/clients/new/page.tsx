'use client'

import { useState, Suspense } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { createSupabaseBrowserClient } from '@/lib/supabase-browser'
import {
  ArrowLeft, ArrowRight, Check, Loader2, Copy, CheckCircle,
  Sparkles, Zap, ShoppingBag, AlertTriangle, Bell,
} from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

type Step     = 1 | 2 | 3 | 4 | 5
type Platform = 'shopify' | 'nuvemshop' | 'woocommerce' | 'vnda' | 'wordpress'
type AdsTab   = 'meta' | 'google' | 'tiktok' | 'pinterest' | 'ga4'

type ProbeResult = { status: string; error?: string | null } | null

function slugify(name: string): string {
  const base = name
    .toLowerCase()
    .normalize('NFD')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40)
  if (!base) return ''
  const suffix = Math.random().toString(36).slice(2, 6)
  return `${base}-${suffix}`
}

const STEP_LABELS: Record<Step, string> = {
  1: 'Loja',
  2: 'Anúncios',
  3: 'Instalação',
  4: 'Notificações',
  5: 'Pronto!',
}

const INPUT = 'w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-600 outline-none focus:border-indigo-500 transition-colors'
const LABEL = 'block text-xs font-medium text-slate-400 mb-1.5'

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

interface InstallResult {
  webhooks: { succeeded: number; failed: number; total: number }
  script_tag: { status: 'created' | 'exists' | 'failed'; id?: number; src?: string; error?: string }
  tracker_src: string
}

function NewClientWizard() {
  const router       = useRouter()
  const searchParams = useSearchParams()
  const isFresh      = searchParams.get('fresh') === '1'

  const [step,    setStep]    = useState<Step>(1)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')

  // Step 1
  const [platform,   setPlatform]   = useState<Platform>('shopify')
  const [storeName,  setStoreName]  = useState('')
  const [clientType, setClientType] = useState<'ecommerce' | 'leads'>('ecommerce')
  const [domain,     setDomain]     = useState('')
  const [apiToken,   setApiToken]   = useState('')
  const [apiSecret,  setApiSecret]  = useState('')

  // Created client
  const [pixelId,    setPixelId]    = useState('')
  const [clientDbId, setClientDbId] = useState('')

  // Step 2
  const [adsTab,        setAdsTab]        = useState<AdsTab>('meta')
  const [metaForm,      setMetaForm]      = useState({ pixel_id: '', access_token: '', ad_account_id: '' })
  const [googleForm,    setGoogleForm]    = useState({ customer_id: '', aw_id: '', conversion_action_id: '', login_customer_id: '' })
  const [tiktokForm,    setTiktokForm]    = useState({ pixel_id: '', access_token: '' })
  const [pinterestForm, setPinterestForm] = useState({ ad_account_id: '', access_token: '', tag_id: '' })
  const [ga4Form,       setGa4Form]       = useState({ measurement_id: '', api_secret: '' })
  const [savingAds,     setSavingAds]     = useState(false)
  const [probes,        setProbes]        = useState<Record<string, ProbeResult>>({})
  const [probing,       setProbing]       = useState<string | null>(null)

  // Step 3
  const [installing,    setInstalling]    = useState(false)
  const [installResult, setInstallResult] = useState<InstallResult | null>(null)
  const [syncOnly,      setSyncOnly]      = useState(false)
  const [enablingSyncOnly, setEnablingSyncOnly] = useState(false)

  // Step 4
  const [alertEmail,     setAlertEmail]     = useState('')
  const [slackWebhook,   setSlackWebhook]   = useState('')
  const [reportsEnabled, setReportsEnabled] = useState(false)
  const [savingNotif,    setSavingNotif]    = useState(false)

  function setM(k: string, v: string)  { setMetaForm(f      => ({ ...f, [k]: v })) }
  function setG(k: string, v: string)  { setGoogleForm(f    => ({ ...f, [k]: v })) }
  function setT(k: string, v: string)  { setTiktokForm(f    => ({ ...f, [k]: v })) }
  function setP(k: string, v: string)  { setPinterestForm(f => ({ ...f, [k]: v })) }
  function setGA(k: string, v: string) { setGa4Form(f       => ({ ...f, [k]: v })) }

  async function handleTestConnection(platform: 'meta' | 'google_ads' | 'tiktok' | 'pinterest' | 'ga4') {
    if (!pixelId) return
    setProbing(platform)
    setProbes(p => ({ ...p, [platform]: null }))
    try {
      await handleSaveAds(false)
      const res  = await fetch(`${API_URL}/integrations/${pixelId}/test/${platform}`, { method: 'POST' })
      const data = await res.json()
      setProbes(p => ({ ...p, [platform]: { status: data.status, error: data.error } }))
    } catch (err: unknown) {
      setProbes(p => ({ ...p, [platform]: { status: 'invalid', error: (err as Error)?.message || 'falha de rede' } }))
    } finally {
      setProbing(null)
    }
  }

  // ── Step 1: create client ─────────────────────────────────────────────────
  async function handleCreateClient(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const sb = createSupabaseBrowserClient()
      const { data: { user } } = await sb.auth.getUser()
      if (!user) { setError('Sessão expirada.'); return }

      const { data: mem } = await sb
        .from('agency_members').select('agency_id').eq('user_id', user.id).limit(1).single()
      if (!mem) { setError('Usuário não vinculado a nenhuma agência.'); return }

      const slug = slugify(storeName)
      const payload: Record<string, string | boolean> = {
        name: storeName, ecommerce_platform: platform, agency_id: mem.agency_id, is_active: true,
        client_type: clientType,
      }
      if (slug) payload.pixel_id = slug
      if (domain) {
        if (platform === 'shopify') {
          payload.shopify_domain = domain.replace(/^https?:\/\//, '')
          if (apiToken)  payload.shopify_access_token   = apiToken
          if (apiSecret) payload.shopify_webhook_secret = apiSecret
        } else if (platform === 'nuvemshop') {
          payload.nuvemshop_store_id = domain
          if (apiToken) payload.nuvemshop_access_token = apiToken
        } else {
          payload.woo_store_url = domain
          if (apiToken) payload.woo_consumer_key = apiToken
        }
      }

      const { data, error: insertErr } = await sb.from('clients').insert(payload).select('id, pixel_id').single()
      if (insertErr) { setError(insertErr.message); return }
      setPixelId(data.pixel_id)
      setClientDbId(data.id)
      setStep(2)
    } finally {
      setLoading(false)
    }
  }

  // ── Step 2: save ads (optional) ───────────────────────────────────────────
  async function handleSaveAds(andContinue = true) {
    setSavingAds(true)
    try {
      const sb = createSupabaseBrowserClient()
      await sb.from('clients').update({
        meta_pixel_id:       metaForm.pixel_id      || null,
        meta_access_token:   metaForm.access_token  || null,
        meta_ad_account_id:  metaForm.ad_account_id || null,
        google_ads_customer_id:          googleForm.customer_id          || null,
        google_ads_aw_id:                googleForm.aw_id                || null,
        google_ads_conversion_action_id: googleForm.conversion_action_id || null,
        google_ads_login_customer_id:    googleForm.login_customer_id    || null,
        tiktok_pixel_id:     tiktokForm.pixel_id     || null,
        tiktok_access_token: tiktokForm.access_token || null,
        pinterest_ad_account_id: pinterestForm.ad_account_id || null,
        pinterest_access_token:  pinterestForm.access_token  || null,
        pinterest_tag_id:        pinterestForm.tag_id        || null,
        ga4_measurement_id:  ga4Form.measurement_id || null,
        ga4_api_secret:      ga4Form.api_secret     || null,
      }).eq('id', clientDbId)
    } finally {
      setSavingAds(false)
      if (andContinue) setStep(3)
    }
  }

  // ── Step 3: one-shot install (webhooks + ScriptTag) ───────────────────────
  async function handleInstall() {
    setInstalling(true)
    setInstallResult(null)
    try {
      const res  = await fetch(`${API_URL}/setup/shopify/${pixelId}/install`, { method: 'POST' })
      const json: InstallResult = await res.json()
      setInstallResult(json)
    } catch {
      setInstallResult({
        webhooks:    { succeeded: 0, failed: 1, total: 9 },
        script_tag:  { status: 'failed', error: 'Erro de rede — tente novamente.' },
        tracker_src: `${API_URL}/static/tracker.js?client_id=${pixelId}`,
      })
    } finally {
      setInstalling(false)
    }
  }

  // ── Step 3 alt: skip pixel/webhooks, use API sync only ───────────────────
  async function handleEnableSyncOnly() {
    setEnablingSyncOnly(true)
    try {
      await fetch(`${API_URL}/sync/shopify/${pixelId}/enable?enabled=true`, { method: 'PATCH' })
      await fetch(`${API_URL}/sync/shopify/${pixelId}/backfill`, { method: 'POST' })
      setSyncOnly(true)
      setStep(4)
    } catch {
      // backfill can be triggered later; still advance
      setSyncOnly(true)
      setStep(4)
    } finally {
      setEnablingSyncOnly(false)
    }
  }

  // ── Step 4: save notifications config (optional) ─────────────────────────
  async function handleSaveNotifications(andContinue = true) {
    setSavingNotif(true)
    try {
      const sb = createSupabaseBrowserClient()
      await sb.from('clients').update({
        alert_email:       alertEmail   || null,
        slack_webhook_url: slackWebhook || null,
        reports_enabled:   reportsEnabled,
      }).eq('id', clientDbId)
    } finally {
      setSavingNotif(false)
      if (andContinue) setStep(5)
    }
  }

  const manualSnippet = `<script
  src="${API_URL}/static/tracker.js?client_id=${pixelId}"
  async
></script>`

  const pixelOk    = installResult?.script_tag?.status !== 'failed'
  const webhooksOk = (installResult?.webhooks?.failed ?? 1) === 0
  const installOk  = pixelOk && webhooksOk

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-200">
      {/* Header */}
      <div className="border-b border-[#2a2f3e] px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link href="/clients" className="text-slate-500 hover:text-white">
            <ArrowLeft size={18} />
          </Link>
          <div>
            <h1 className="text-lg font-bold text-white">
              {isFresh ? 'Bem-vindo! Configure sua loja' : 'Adicionar nova loja'}
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">
              {STEP_LABELS[step]} · etapa {step} de 5
            </p>
          </div>
        </div>
        {pixelId && (
          <span className="text-xs text-slate-600 bg-[#1a1f2e] border border-[#2a2f3e] px-3 py-1.5 rounded-lg font-mono">
            {pixelId}
          </span>
        )}
      </div>

      {/* Progress */}
      <div className="px-6 py-5 max-w-2xl mx-auto">
        <div className="flex items-center">
          {([1, 2, 3, 4, 5] as Step[]).map((s, i) => {
            const done   = step > s
            const active = step === s
            return (
              <div key={s} className="flex items-center flex-1 last:flex-none">
                <div className={`flex items-center gap-2 ${active || done ? 'opacity-100' : 'opacity-35'}`}>
                  <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 transition-all ${
                    done   ? 'bg-emerald-500 text-white' :
                    active ? 'bg-indigo-600 text-white' :
                             'bg-[#2a2f3e] text-slate-500'
                  }`}>
                    {done ? <Check size={12} /> : s}
                  </div>
                  <span className={`text-xs font-medium hidden sm:block ${active ? 'text-white' : 'text-slate-500'}`}>
                    {STEP_LABELS[s]}
                  </span>
                </div>
                {i < 4 && (
                  <div className={`flex-1 h-px mx-3 transition-colors ${done ? 'bg-emerald-500/40' : 'bg-[#2a2f3e]'}`} />
                )}
              </div>
            )
          })}
        </div>
      </div>

      <div className="max-w-2xl mx-auto px-6 pb-16 space-y-4">

        {/* ──────────────────────────────────────── STEP 1: Loja */}
        {step === 1 && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-2xl p-8">
            <div className="flex items-center gap-2 mb-6">
              <ShoppingBag size={18} className="text-indigo-400" />
              <h2 className="text-base font-semibold text-white">Dados da loja</h2>
            </div>

            <form onSubmit={handleCreateClient} className="space-y-5">
              <div>
                <label className={LABEL}>Nome da loja</label>
                <input autoFocus required value={storeName} onChange={e => setStoreName(e.target.value)}
                  placeholder="LK Sneakers" className={INPUT} />
              </div>

              <div>
                <label className={LABEL}>Tipo de cliente</label>
                <div className="grid grid-cols-2 gap-3">
                  {([
                    { v: 'ecommerce', label: 'E-commerce', hint: 'Faturamento, ROAS, pedidos' },
                    { v: 'leads',     label: 'Leads',      hint: 'Leads, CPL, agendamentos' },
                  ] as const).map(opt => (
                    <button key={opt.v} type="button" onClick={() => setClientType(opt.v)}
                      className={`py-3 px-3 rounded-xl border text-sm font-medium transition-colors text-left ${
                        clientType === opt.v
                          ? 'border-indigo-500 bg-indigo-500/10 text-indigo-300'
                          : 'border-[#2a2f3e] text-slate-400 hover:border-slate-500 hover:text-white'
                      }`}>
                      <span className="block">{opt.label}</span>
                      <span className="block text-[11px] font-normal text-slate-500 mt-0.5">{opt.hint}</span>
                    </button>
                  ))}
                </div>
                <p className="mt-1.5 text-xs text-slate-600">Define quais métricas aparecem nos relatórios deste cliente.</p>
              </div>

              <div>
                <label className={LABEL}>Plataforma</label>
                <div className="grid grid-cols-3 gap-3">
                  {([
                    { value: 'shopify',     label: 'Shopify' },
                    { value: 'nuvemshop',   label: 'Nuvemshop' },
                    { value: 'woocommerce', label: 'WooCommerce' },
                    { value: 'vnda',        label: 'Vnda' },
                    { value: 'wordpress',   label: 'WordPress' },
                  ] as { value: Platform; label: string }[]).map(({ value, label }) => (
                    <button key={value} type="button" onClick={() => setPlatform(value)}
                      className={`py-3 rounded-xl border text-sm font-medium transition-colors ${
                        platform === value
                          ? 'border-indigo-500 bg-indigo-500/10 text-indigo-300'
                          : 'border-[#2a2f3e] text-slate-400 hover:border-slate-500 hover:text-white'
                      }`}>
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {platform === 'shopify' && (
                <>
                  <div>
                    <label className={LABEL}>Domínio</label>
                    <input value={domain} onChange={e => setDomain(e.target.value)}
                      placeholder="lksneakers.myshopify.com" className={INPUT} />
                  </div>
                  <div>
                    <label className={LABEL}>
                      Admin API Access Token
                      <span className="text-slate-600 font-normal ml-1">(necessário para auto-instalar tudo)</span>
                    </label>
                    <input type="password" value={apiToken} onChange={e => setApiToken(e.target.value)}
                      placeholder="shpat_..." className={INPUT} />
                    <div className="mt-2 bg-[#0f1117] border border-[#2a2f3e] rounded-lg p-3 text-xs text-slate-500 space-y-1">
                      <p className="text-slate-400 font-medium">Como gerar:</p>
                      <p>Shopify Admin → Apps → <strong className="text-slate-300">Develop apps</strong> → Create app</p>
                      <p>Admin API scopes necessários:</p>
                      <code className="text-indigo-300 block">
                        read_orders · write_webhooks · read_script_tags · write_script_tags
                      </code>
                    </div>
                  </div>
                  <div>
                    <label className={LABEL}>
                      API Secret Key
                      <span className="text-slate-600 font-normal ml-1">(assina os webhooks — sem ela, o rastreamento falha)</span>
                    </label>
                    <input type="password" value={apiSecret} onChange={e => setApiSecret(e.target.value)}
                      placeholder="shpss_... ou hash de 32 caracteres" className={INPUT} />
                    <p className="mt-1.5 text-xs text-slate-600">
                      No mesmo app: <strong className="text-slate-400">API credentials</strong> → <strong className="text-slate-400">API secret key</strong>. É o que valida a assinatura HMAC dos webhooks deste cliente.
                    </p>
                  </div>
                </>
              )}

              {platform === 'nuvemshop' && (
                <div>
                  <label className={LABEL}>ID da Loja</label>
                  <input value={domain} onChange={e => setDomain(e.target.value)} placeholder="123456" className={INPUT} />
                </div>
              )}

              {platform === 'woocommerce' && (
                <div>
                  <label className={LABEL}>URL da loja</label>
                  <input type="url" value={domain} onChange={e => setDomain(e.target.value)}
                    placeholder="https://minhaloja.com.br" className={INPUT} />
                </div>
              )}

              {(platform === 'vnda' || platform === 'wordpress') && (
                <div>
                  <label className={LABEL}>URL da loja</label>
                  <input type="url" value={domain} onChange={e => setDomain(e.target.value)}
                    placeholder="https://minhaloja.com.br" className={INPUT} />
                  <p className="mt-1.5 text-xs text-slate-600">
                    Somente dashboard e relatórios — sem pixel nem webhooks por enquanto.
                  </p>
                </div>
              )}

              {error && (
                <p className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">{error}</p>
              )}

              <button type="submit" disabled={loading || !storeName}
                className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2 transition-colors">
                {loading
                  ? <><Loader2 size={14} className="animate-spin" /> Criando…</>
                  : <><ArrowRight size={14} /> Criar loja e continuar</>}
              </button>
            </form>
          </div>
        )}

        {/* ──────────────────────────────────────── STEP 2: Anúncios */}
        {step === 2 && (
          <>
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-2xl p-8">
              <div className="flex items-center gap-2 mb-2">
                <Sparkles size={18} className="text-indigo-400" />
                <h2 className="text-base font-semibold text-white">Conectar plataformas de anúncios</h2>
              </div>
              <p className="text-xs text-slate-500 mb-6">Opcional — configure depois nas Configurações se preferir.</p>

              <div className="flex gap-1 bg-[#0f1117] rounded-lg p-1 border border-[#2a2f3e] mb-6 overflow-x-auto">
                {(['meta', 'google', 'tiktok', 'pinterest', 'ga4'] as AdsTab[]).map(t => (
                  <button key={t} onClick={() => setAdsTab(t)}
                    className={`flex-1 min-w-[84px] py-2 rounded-lg text-xs font-medium transition-colors ${
                      adsTab === t ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
                    }`}>
                    {t === 'meta' ? 'Meta Ads' : t === 'google' ? 'Google Ads' : t === 'tiktok' ? 'TikTok Ads' : t === 'pinterest' ? 'Pinterest' : 'GA4'}
                  </button>
                ))}
              </div>

              {adsTab === 'meta' && (
                <div className="space-y-4">
                  <div><label className={LABEL}>Pixel ID</label>
                    <input value={metaForm.pixel_id} onChange={e => setM('pixel_id', e.target.value)}
                      placeholder="123456789012345" className={INPUT} /></div>
                  <div><label className={LABEL}>System User Access Token</label>
                    <input type="password" value={metaForm.access_token} onChange={e => setM('access_token', e.target.value)}
                      placeholder="EAAG..." className={INPUT} />
                    <p className="text-xs text-slate-600 mt-1">Business Manager → System Users → token com ads_management</p>
                  </div>
                  <div><label className={LABEL}>Ad Account ID</label>
                    <input value={metaForm.ad_account_id} onChange={e => setM('ad_account_id', e.target.value)}
                      placeholder="act_123456789" className={INPUT} /></div>
                </div>
              )}

              {adsTab === 'google' && (
                <div className="space-y-4">
                  <div><label className={LABEL}>Customer ID</label>
                    <input value={googleForm.customer_id} onChange={e => setG('customer_id', e.target.value)}
                      placeholder="162-897-1213" className={INPUT} /></div>
                  <div><label className={LABEL}>Conversion Action ID <span className="text-slate-600 font-normal">(necessário p/ enviar conversões)</span></label>
                    <input value={googleForm.conversion_action_id} onChange={e => setG('conversion_action_id', e.target.value)}
                      placeholder="1500331104" className={INPUT} />
                    <p className="mt-1 text-xs text-slate-600">Google Ads → Goals → Conversions → sua ação → o número no fim da URL.</p>
                  </div>
                  <div><label className={LABEL}>MCC / Manager ID <span className="text-slate-600 font-normal">(opcional)</span></label>
                    <input value={googleForm.login_customer_id} onChange={e => setG('login_customer_id', e.target.value)}
                      placeholder="985-863-8484" className={INPUT} /></div>
                  <div><label className={LABEL}>AW-ID <span className="text-slate-600 font-normal">(remarketing, opcional)</span></label>
                    <input value={googleForm.aw_id} onChange={e => setG('aw_id', e.target.value)}
                      placeholder="AW-123456789" className={INPUT} /></div>
                  <p className="text-xs text-slate-600">O OAuth (refresh token) é conectado nas Configurações após criar a loja.</p>
                </div>
              )}

              {adsTab === 'tiktok' && (
                <div className="space-y-4">
                  <div><label className={LABEL}>Pixel Code</label>
                    <input value={tiktokForm.pixel_id} onChange={e => setT('pixel_id', e.target.value)}
                      placeholder="C3XXXXXXXXXXXX" className={INPUT} /></div>
                  <div><label className={LABEL}>Events API Access Token</label>
                    <input type="password" value={tiktokForm.access_token} onChange={e => setT('access_token', e.target.value)}
                      placeholder="token..." className={INPUT} /></div>
                </div>
              )}

              {adsTab === 'pinterest' && (
                <div className="space-y-4">
                  <div><label className={LABEL}>Ad Account ID</label>
                    <input value={pinterestForm.ad_account_id} onChange={e => setP('ad_account_id', e.target.value)}
                      placeholder="549XXXXXXXXXX" className={INPUT} /></div>
                  <div><label className={LABEL}>Conversions API Access Token</label>
                    <input type="password" value={pinterestForm.access_token} onChange={e => setP('access_token', e.target.value)}
                      placeholder="pinit_AAAA..." className={INPUT} /></div>
                  <div><label className={LABEL}>Tag ID <span className="text-slate-600 font-normal">(antigo "pixel")</span></label>
                    <input value={pinterestForm.tag_id} onChange={e => setP('tag_id', e.target.value)}
                      placeholder="2613XXXXXXXXX" className={INPUT} /></div>
                  <p className="text-xs text-slate-600">Ads → Conversions → Conversions API → gere o token e copie o Tag ID.</p>
                </div>
              )}

              {adsTab === 'ga4' && (
                <div className="space-y-4">
                  <div><label className={LABEL}>Measurement ID</label>
                    <input value={ga4Form.measurement_id} onChange={e => setGA('measurement_id', e.target.value)}
                      placeholder="G-XXXXXXXXXX" className={INPUT} /></div>
                  <div><label className={LABEL}>API Secret</label>
                    <input type="password" value={ga4Form.api_secret} onChange={e => setGA('api_secret', e.target.value)}
                      placeholder="xxxxxxxx" className={INPUT} />
                    <p className="mt-1 text-xs text-slate-600">GA4 → Admin → Data Streams → seu stream → Measurement Protocol API secrets → criar.</p>
                  </div>
                </div>
              )}

              {/* Testar conexão */}
              <div className="mt-5 pt-5 border-t border-[#2a2f3e]">
                {(() => {
                  const plat = adsTab === 'meta' ? 'meta'
                             : adsTab === 'google' ? 'google_ads'
                             : adsTab === 'tiktok' ? 'tiktok'
                             : adsTab === 'ga4' ? 'ga4'
                             : 'pinterest'
                  const probe     = probes[plat]
                  const isProbing = probing === plat
                  return (
                    <div className="flex items-center justify-between gap-3">
                      <button
                        onClick={() => handleTestConnection(plat as 'meta' | 'google_ads' | 'tiktok' | 'pinterest' | 'ga4')}
                        disabled={isProbing || !pixelId}
                        className="px-4 py-2 rounded-lg bg-[#0f1117] hover:bg-[#252a3a] border border-[#2a2f3e] text-xs text-slate-300 flex items-center gap-2 disabled:opacity-50"
                      >
                        {isProbing
                          ? <><Loader2 size={12} className="animate-spin" /> Testando…</>
                          : <><Zap size={12} /> Salvar e testar conexão</>}
                      </button>
                      {probe && (
                        <div className={`flex items-center gap-1.5 text-xs px-2 py-1 rounded ${
                          probe.status === 'healthy'
                            ? 'bg-emerald-500/10 text-emerald-400'
                            : 'bg-red-500/10 text-red-400'
                        }`}>
                          {probe.status === 'healthy'
                            ? <><CheckCircle size={12} /> Conectado</>
                            : <><AlertTriangle size={12} /> {probe.error?.slice(0, 80) || probe.status}</>}
                        </div>
                      )}
                    </div>
                  )
                })()}
              </div>
            </div>

            <div className="flex gap-3">
              <button onClick={() => setStep(1)}
                className="flex-1 py-3 border border-[#2a2f3e] text-slate-400 hover:text-white rounded-xl text-sm transition-colors">
                Voltar
              </button>
              <button onClick={() => handleSaveAds(true)} disabled={savingAds}
                className="flex-1 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2">
                {savingAds ? <><Loader2 size={14} className="animate-spin" /> Salvando…</> : <><ArrowRight size={14} /> Salvar e continuar</>}
              </button>
            </div>
            <button onClick={() => setStep(3)} className="w-full text-xs text-slate-600 hover:text-slate-400 py-1.5 transition-colors">
              Pular — configurar depois
            </button>
          </>
        )}

        {/* ──────────────────────────────────────── STEP 3: Instalação automática */}
        {step === 3 && (
          <>
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-2xl p-8">
              <div className="flex items-center gap-2 mb-2">
                <Zap size={18} className="text-yellow-400" />
                <h2 className="text-base font-semibold text-white">Instalação automática</h2>
              </div>

              {!installResult ? (
                <>
                  <p className="text-sm text-slate-400 mb-6 leading-relaxed">
                    Um clique instala tudo:
                  </p>
                  <ul className="space-y-2 mb-8">
                    {[
                      { icon: '⚡', text: 'Pixel de rastreamento em todas as páginas (ScriptTag API — sem tocar no tema)' },
                      { icon: '🔗', text: '9 webhooks de pedidos, carrinho, clientes e reembolsos' },
                    ].map(({ icon, text }) => (
                      <li key={text} className="flex items-start gap-3 text-sm text-slate-300">
                        <span className="shrink-0">{icon}</span>
                        <span>{text}</span>
                      </li>
                    ))}
                  </ul>

                  {(platform === 'vnda' || platform === 'wordpress') ? (
                    <div className="space-y-3">
                      <div className="bg-indigo-500/10 border border-indigo-500/20 rounded-xl p-4 text-sm text-indigo-300">
                        Pixel e webhooks não disponíveis para {platform === 'vnda' ? 'Vnda' : 'WordPress'} ainda.
                        O dashboard já está ativo com dados de anúncios.
                      </div>
                      <button onClick={() => setStep(4)}
                        className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2">
                        <ArrowRight size={14} /> Continuar
                      </button>
                    </div>
                  ) : platform === 'shopify' ? (
                    <div className="space-y-3">
                      <button onClick={handleInstall} disabled={installing || enablingSyncOnly}
                        className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-bold py-4 rounded-xl text-sm flex items-center justify-center gap-2 transition-colors">
                        {installing
                          ? <><Loader2 size={16} className="animate-spin" /> Instalando…</>
                          : <><Zap size={16} /> Instalar tudo automaticamente</>}
                      </button>
                      <button
                        onClick={handleEnableSyncOnly}
                        disabled={installing || enablingSyncOnly}
                        className="w-full py-3 border border-[#2a2f3e] text-slate-400 hover:text-white hover:border-slate-500 rounded-xl text-sm flex items-center justify-center gap-2 transition-colors disabled:opacity-50"
                      >
                        {enablingSyncOnly
                          ? <><Loader2 size={14} className="animate-spin" /> Ativando sync…</>
                          : 'Pular — usar só sincronização de pedidos via API'}
                      </button>
                      <p className="text-xs text-slate-600 text-center">
                        Sem pixel nem webhooks. Pedidos importados a cada hora via Admin API.
                      </p>
                    </div>
                  ) : (
                    <div className="space-y-4">
                      <div className="bg-[#0f1117] border border-[#2a2f3e] rounded-xl p-4">
                        <div className="flex items-center justify-between mb-1.5">
                          <p className="text-xs text-slate-500">Snippet do pixel</p>
                          <CopyBtn text={manualSnippet} />
                        </div>
                        <pre className="text-xs text-slate-300 whitespace-pre-wrap leading-relaxed">{manualSnippet}</pre>
                      </div>
                      <div className="bg-[#0f1117] border border-[#2a2f3e] rounded-xl p-4">
                        <div className="flex items-center justify-between mb-1.5">
                          <p className="text-xs text-slate-500">URL do webhook</p>
                          <CopyBtn text={`${API_URL}/webhook/${platform}/${pixelId}`} />
                        </div>
                        <code className="text-xs text-indigo-300">{API_URL}/webhook/{platform}/{pixelId}</code>
                      </div>
                      <button onClick={() => setStep(4)}
                        className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2">
                        <ArrowRight size={14} /> Já configurei — continuar
                      </button>
                    </div>
                  )}
                </>
              ) : (
                <div className="space-y-4">
                  <div className={`rounded-xl p-4 border flex items-start gap-3 ${
                    webhooksOk ? 'bg-emerald-500/8 border-emerald-500/25' : 'bg-yellow-500/8 border-yellow-500/25'
                  }`}>
                    {webhooksOk
                      ? <CheckCircle size={16} className="text-emerald-400 shrink-0 mt-0.5" />
                      : <AlertTriangle size={16} className="text-yellow-400 shrink-0 mt-0.5" />}
                    <div>
                      <p className={`text-sm font-semibold ${webhooksOk ? 'text-emerald-300' : 'text-yellow-300'}`}>
                        Webhooks: {installResult.webhooks.succeeded}/{installResult.webhooks.total} registrados
                      </p>
                      {!webhooksOk && (
                        <p className="text-xs text-slate-500 mt-0.5">
                          {installResult.webhooks.failed} falhou — verifique o Admin Token e os escopos
                        </p>
                      )}
                    </div>
                  </div>

                  {installResult.script_tag.status !== 'failed' ? (
                    <div className="rounded-xl p-4 border bg-emerald-500/8 border-emerald-500/25 flex items-start gap-3">
                      <CheckCircle size={16} className="text-emerald-400 shrink-0 mt-0.5" />
                      <div>
                        <p className="text-sm font-semibold text-emerald-300">
                          Pixel instalado em todas as páginas automaticamente
                        </p>
                        <p className="text-xs text-slate-500 mt-0.5">
                          ScriptTag ID #{installResult.script_tag.id} · sem editar o tema
                        </p>
                      </div>
                    </div>
                  ) : (
                    <div className="rounded-xl p-4 border bg-yellow-500/8 border-yellow-500/25">
                      <div className="flex items-start gap-3 mb-3">
                        <AlertTriangle size={16} className="text-yellow-400 shrink-0 mt-0.5" />
                        <div>
                          <p className="text-sm font-semibold text-yellow-300">
                            Pixel: instale manualmente (escopos read/write_script_tags ausentes)
                          </p>
                          <p className="text-xs text-slate-500 mt-0.5">
                            Cole o snippet no &lt;head&gt; ou no theme.liquid da loja:
                          </p>
                        </div>
                      </div>
                      <div className="bg-[#0f1117] border border-[#2a2f3e] rounded-lg p-3">
                        <div className="flex items-center justify-between mb-2">
                          <p className="text-xs text-slate-500">Snippet</p>
                          <CopyBtn text={manualSnippet} />
                        </div>
                        <pre className="text-xs text-slate-300 whitespace-pre-wrap leading-relaxed">{manualSnippet}</pre>
                      </div>
                    </div>
                  )}

                  <div className="flex gap-3 pt-2">
                    <button onClick={handleInstall} disabled={installing}
                      className="flex-1 py-2.5 border border-[#2a2f3e] text-slate-400 hover:text-white rounded-xl text-xs transition-colors">
                      {installing ? <Loader2 size={12} className="animate-spin inline" /> : 'Tentar novamente'}
                    </button>
                    <button onClick={() => setStep(4)}
                      className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-2.5 rounded-xl text-sm flex items-center justify-center gap-2">
                      <ArrowRight size={14} /> {installOk ? 'Continuar' : 'Continuar assim mesmo'}
                    </button>
                  </div>
                </div>
              )}
            </div>

            {!installResult && (
              <div className="flex gap-3">
                <button onClick={() => setStep(2)}
                  className="flex-1 py-3 border border-[#2a2f3e] text-slate-400 hover:text-white rounded-xl text-sm transition-colors">
                  Voltar
                </button>
              </div>
            )}
          </>
        )}

        {/* ──────────────────────────────────────── STEP 4: Notificações */}
        {step === 4 && (
          <>
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-2xl p-8">
              <div className="flex items-center gap-2 mb-2">
                <Bell size={18} className="text-indigo-400" />
                <h2 className="text-base font-semibold text-white">Alertas & Notificações</h2>
              </div>
              <p className="text-xs text-slate-500 mb-6">
                Opcional — você será avisado quando o ROAS cair, o tracking parar ou o CPA superar a meta.
              </p>

              <div className="space-y-5">
                <div>
                  <label className={LABEL}>E-mail para alertas</label>
                  <input
                    type="email"
                    value={alertEmail}
                    onChange={e => setAlertEmail(e.target.value)}
                    placeholder="gestor@agencia.com.br"
                    className={INPUT}
                  />
                  <p className="text-xs text-slate-600 mt-1.5">
                    Recebe relatório semanal IA e alertas de anomalias críticas.
                  </p>
                </div>

                <div className="bg-[#0f1117] border border-[#2a2f3e] rounded-xl p-4">
                  <label className="flex items-start gap-3 cursor-pointer">
                    <input type="checkbox" checked={reportsEnabled} onChange={e => setReportsEnabled(e.target.checked)}
                      className="w-4 h-4 mt-0.5 accent-indigo-500 shrink-0" />
                    <span>
                      <span className="block text-sm text-slate-200 font-medium">Ativar relatórios de tráfego pago</span>
                      <span className="block text-xs text-slate-500 mt-0.5">
                        Gera os relatórios semanal e mensal ({clientType === 'leads' ? 'perfil Leads' : 'perfil E-commerce'}) para este cliente.
                      </span>
                    </span>
                  </label>
                </div>

                <div>
                  <label className={LABEL}>
                    Slack Webhook URL
                    <span className="text-slate-600 font-normal ml-1">(opcional)</span>
                  </label>
                  <input
                    value={slackWebhook}
                    onChange={e => setSlackWebhook(e.target.value)}
                    placeholder="https://hooks.slack.com/services/..."
                    className={INPUT}
                  />
                  <p className="text-xs text-slate-600 mt-1.5">
                    Slack → Apps → Incoming Webhooks → Add to Slack → copie a URL.
                  </p>
                </div>

                <div className="bg-[#0f1117] border border-[#2a2f3e] rounded-xl p-4">
                  <p className="text-xs font-medium text-slate-400 mb-3">Alertas configurados automaticamente:</p>
                  <ul className="space-y-1.5 text-xs text-slate-500">
                    {[
                      { color: 'bg-yellow-400',  text: 'ROAS abaixo da meta configurada' },
                      { color: 'bg-red-400',     text: 'Tracking parado por mais de 24h' },
                      { color: 'bg-orange-400',  text: 'CPA acima da meta (+ margem de 20%)' },
                      { color: 'bg-red-500',     text: 'Token Meta expirando em menos de 7 dias' },
                    ].map(({ color, text }) => (
                      <li key={text} className="flex items-center gap-2">
                        <span className={`w-1.5 h-1.5 rounded-full ${color} shrink-0`} />
                        {text}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>

            <div className="flex gap-3">
              <button onClick={() => setStep(3)}
                className="flex-1 py-3 border border-[#2a2f3e] text-slate-400 hover:text-white rounded-xl text-sm transition-colors">
                Voltar
              </button>
              <button
                onClick={() => handleSaveNotifications(true)}
                disabled={savingNotif}
                className="flex-1 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2"
              >
                {savingNotif
                  ? <><Loader2 size={14} className="animate-spin" /> Salvando…</>
                  : <><ArrowRight size={14} /> Salvar e continuar</>}
              </button>
            </div>
            <button onClick={() => setStep(5)} className="w-full text-xs text-slate-600 hover:text-slate-400 py-1.5 transition-colors">
              Pular — configurar depois
            </button>
          </>
        )}

        {/* ──────────────────────────────────────── STEP 5: Pronto! */}
        {step === 5 && (
          <div className="text-center py-4 space-y-7">
            <div className="w-20 h-20 rounded-2xl bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center mx-auto">
              <CheckCircle size={40} className="text-emerald-400" />
            </div>

            <div>
              <h2 className="text-2xl font-bold text-white mb-2">Tudo pronto!</h2>
              <p className="text-slate-400 text-sm max-w-sm mx-auto">
                <span className="text-white font-semibold">{storeName}</span> está configurada.
                {syncOnly
                  ? ' Pedidos serão sincronizados automaticamente via Shopify Admin API a cada hora.'
                  : installResult?.script_tag?.status !== 'failed'
                    ? ' O pixel está instalado em todas as páginas da loja automaticamente.'
                    : ' Lembre de colar o snippet do pixel no tema da loja.'}
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3 max-w-sm mx-auto text-left">
              <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-4">
                <p className="text-xs text-slate-500 mb-1">Pixel ID</p>
                <div className="flex items-center justify-between gap-2">
                  <code className="text-xs text-indigo-300 truncate">{pixelId}</code>
                  <CopyBtn text={pixelId} />
                </div>
              </div>
              <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-4">
                <p className="text-xs text-slate-500 mb-1">Plataforma</p>
                <p className="text-sm font-medium text-white capitalize">{platform}</p>
              </div>
            </div>

            <div className="flex flex-col gap-3 max-w-sm mx-auto">
              <button onClick={() => router.push(`/clients/${pixelId}/dashboard`)}
                className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3.5 rounded-xl text-sm flex items-center justify-center gap-2 transition-colors">
                <ArrowRight size={14} /> Abrir dashboard
              </button>
              <Link href={`/clients/${pixelId}/onboarding`}
                className="w-full bg-[#1a1f2e] hover:bg-[#252b3b] border border-indigo-500/30 text-indigo-300 hover:text-white py-3 rounded-xl text-sm flex items-center justify-center gap-2 transition-colors">
                <CheckCircle size={14} /> Ver checklist de integração
              </Link>
              <div className="grid grid-cols-2 gap-3">
                <Link href={`/clients/${pixelId}/settings`}
                  className="text-sm text-slate-400 hover:text-white bg-[#1a1f2e] border border-[#2a2f3e] py-2.5 rounded-xl text-center transition-colors">
                  Configurações
                </Link>
                <Link href="/clients"
                  className="text-sm text-slate-400 hover:text-white bg-[#1a1f2e] border border-[#2a2f3e] py-2.5 rounded-xl text-center transition-colors">
                  Todas as lojas
                </Link>
              </div>
            </div>
          </div>
        )}

      </div>
    </div>
  )
}

export default function NewClientPage() {
  return (
    <Suspense>
      <NewClientWizard />
    </Suspense>
  )
}
