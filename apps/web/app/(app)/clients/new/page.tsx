'use client'

import { useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { createSupabaseBrowserClient } from '@/lib/supabase-browser'
import {
  ArrowLeft, ArrowRight, Check, Loader2, Copy, CheckCircle,
  ExternalLink, Sparkles, Zap, ShoppingBag,
} from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

type Step = 1 | 2 | 3 | 4 | 5
type Platform = 'shopify' | 'nuvemshop' | 'woocommerce'
type AdsTab = 'meta' | 'google' | 'tiktok'

const STEP_LABELS = ['Loja', 'Pixel', 'Anúncios', 'Webhooks', 'Pronto!']

const INPUT = 'w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-600 outline-none focus:border-indigo-500 transition-colors'
const LABEL = 'block text-xs font-medium text-slate-400 mb-1.5'

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  function copy() {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }
  return (
    <button onClick={copy} className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white transition-colors">
      {copied ? <><CheckCircle size={12} className="text-emerald-400" /> Copiado!</> : <><Copy size={12} /> Copiar</>}
    </button>
  )
}

export default function NewClientWizard() {
  const router       = useRouter()
  const searchParams = useSearchParams()
  const isFresh      = searchParams.get('fresh') === '1'

  const [step,     setStep]     = useState<Step>(1)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState('')

  // Step 1 state
  const [platform, setPlatform] = useState<Platform>('shopify')
  const [storeName, setStoreName] = useState('')
  const [domain,   setDomain]   = useState('')
  const [apiToken, setApiToken] = useState('')

  // Created client
  const [pixelId,    setPixelId]    = useState('')
  const [clientDbId, setClientDbId] = useState('')

  // Step 2 state
  const [pixelVerified, setPixelVerified] = useState(false)
  const [verifying,     setVerifying]     = useState(false)

  // Step 3 state
  const [adsTab, setAdsTab] = useState<AdsTab>('meta')
  const [metaForm,   setMetaFormState]   = useState({ pixel_id: '', access_token: '', ad_account_id: '' })
  const [googleForm, setGoogleFormState] = useState({ customer_id: '', aw_id: '' })
  const [tiktokForm, setTiktokFormState] = useState({ pixel_id: '', access_token: '' })
  const [savingAds, setSavingAds] = useState(false)
  const [adsSaved,  setAdsSaved]  = useState(false)

  // Step 4 state
  const [registeringHooks, setRegisteringHooks] = useState(false)
  const [hooksResult, setHooksResult] = useState<{ ok: number; fail: number } | null>(null)

  function setMeta(k: string, v: string)   { setMetaFormState(f => ({ ...f, [k]: v })) }
  function setGoogle(k: string, v: string) { setGoogleFormState(f => ({ ...f, [k]: v })) }
  function setTiktok(k: string, v: string) { setTiktokFormState(f => ({ ...f, [k]: v })) }

  // ── Step 1 — create client ────────────────────────────────────────────────
  async function handleCreateClient(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const supabase = createSupabaseBrowserClient()
      const { data: { user } } = await supabase.auth.getUser()
      if (!user) { setError('Sessão expirada.'); return }

      const { data: membership } = await supabase
        .from('agency_members')
        .select('agency_id')
        .eq('user_id', user.id)
        .limit(1)
        .single()
      if (!membership) { setError('Usuário não vinculado a nenhuma agência.'); return }

      const payload: Record<string, string | boolean> = {
        name:               storeName,
        ecommerce_platform: platform,
        agency_id:          membership.agency_id,
        is_active:          true,
      }
      if (domain) {
        if (platform === 'shopify') {
          payload.shopify_domain = domain.replace(/^https?:\/\//, '')
          if (apiToken) payload.shopify_access_token = apiToken
        } else if (platform === 'nuvemshop') {
          payload.nuvemshop_store_id = domain
          if (apiToken) payload.nuvemshop_access_token = apiToken
        } else {
          payload.woo_store_url = domain
          if (apiToken) payload.woo_consumer_key = apiToken
        }
      }

      const { data, error: insertErr } = await supabase
        .from('clients')
        .insert(payload)
        .select('id, pixel_id')
        .single()

      if (insertErr) { setError(insertErr.message); return }
      setPixelId(data.pixel_id)
      setClientDbId(data.id)
      setStep(2)
    } finally {
      setLoading(false)
    }
  }

  // ── Step 2 — verify pixel ─────────────────────────────────────────────────
  async function verifyPixel() {
    setVerifying(true)
    try {
      const supabase = createSupabaseBrowserClient()
      const since = new Date(Date.now() - 300_000).toISOString()
      const { count } = await supabase
        .from('tracking_events')
        .select('id', { count: 'exact', head: true })
        .eq('client_id', clientDbId)
        .gte('created_at', since)
      setPixelVerified((count ?? 0) > 0)
    } finally {
      setVerifying(false)
    }
  }

  // ── Step 3 — save ads ─────────────────────────────────────────────────────
  async function saveAds() {
    setSavingAds(true)
    try {
      const supabase = createSupabaseBrowserClient()
      await supabase.from('clients').update({
        meta_pixel_id:       metaForm.pixel_id || null,
        meta_access_token:   metaForm.access_token || null,
        meta_ad_account_id:  metaForm.ad_account_id || null,
        google_ads_customer_id: googleForm.customer_id || null,
        google_ads_aw_id:    googleForm.aw_id || null,
        tiktok_pixel_id:     tiktokForm.pixel_id || null,
        tiktok_access_token: tiktokForm.access_token || null,
      }).eq('id', clientDbId)
      setAdsSaved(true)
    } finally {
      setSavingAds(false)
    }
  }

  // ── Step 4 — register webhooks (Shopify only) ─────────────────────────────
  async function registerWebhooks() {
    setRegisteringHooks(true)
    try {
      const res  = await fetch(`${API_URL}/setup/shopify/${pixelId}/webhooks`, { method: 'POST' })
      const json = await res.json()
      const s    = json.summary || { succeeded: 0, failed: 0 }
      setHooksResult({ ok: s.succeeded, fail: s.failed })
    } catch {
      setHooksResult({ ok: 0, fail: 1 })
    } finally {
      setRegisteringHooks(false)
    }
  }

  const trackerSnippet = `<script
  src="${API_URL}/pixel/tracker.js"
  data-client-id="${pixelId}"
  async
></script>`

  const liquidSnippet = `{% comment %} Ecommerce Tracking IA {% endcomment %}
<script
  src="${API_URL}/pixel/tracker.js"
  data-client-id="${pixelId}"
  async
></script>`

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
              {isFresh ? 'Bem-vindo! Vamos configurar sua loja' : 'Adicionar nova loja'}
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">
              {isFresh ? 'Conta criada com sucesso · ' : ''}
              {STEP_LABELS.length} etapas rápidas para começar a rastrear
            </p>
          </div>
        </div>
        {step > 1 && (
          <span className="text-xs text-slate-500 bg-[#1a1f2e] border border-[#2a2f3e] px-3 py-1.5 rounded-lg font-mono">
            {pixelId}
          </span>
        )}
      </div>

      {/* Progress stepper */}
      <div className="px-6 py-5 max-w-3xl mx-auto">
        <div className="flex items-center">
          {STEP_LABELS.map((label, i) => {
            const s = (i + 1) as Step
            const done   = step > s
            const active = step === s
            return (
              <div key={s} className="flex items-center flex-1 last:flex-none">
                <div className={`flex items-center gap-2 ${active || done ? 'opacity-100' : 'opacity-35'}`}>
                  <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 transition-colors ${
                    done   ? 'bg-emerald-500 text-white' :
                    active ? 'bg-indigo-600 text-white' :
                             'bg-[#2a2f3e] text-slate-500'
                  }`}>
                    {done ? <Check size={12} /> : s}
                  </div>
                  <span className={`text-xs font-medium hidden sm:block ${active ? 'text-white' : 'text-slate-500'}`}>
                    {label}
                  </span>
                </div>
                {i < STEP_LABELS.length - 1 && (
                  <div className={`flex-1 h-px mx-3 transition-colors ${done ? 'bg-emerald-500/40' : 'bg-[#2a2f3e]'}`} />
                )}
              </div>
            )
          })}
        </div>
      </div>

      <div className="max-w-3xl mx-auto px-6 pb-16 space-y-5">

        {/* ── STEP 1 ── */}
        {step === 1 && (
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-2xl p-8">
            <div className="flex items-center gap-2 mb-6">
              <ShoppingBag size={18} className="text-indigo-400" />
              <h2 className="text-base font-semibold text-white">Dados da loja</h2>
            </div>

            <form onSubmit={handleCreateClient} className="space-y-5">
              <div>
                <label className={LABEL}>Nome da loja</label>
                <input type="text" required value={storeName} onChange={e => setStoreName(e.target.value)}
                  placeholder="LK Sneakers" autoFocus className={INPUT} />
              </div>

              <div>
                <label className={LABEL}>Plataforma</label>
                <div className="grid grid-cols-3 gap-3">
                  {(['shopify', 'nuvemshop', 'woocommerce'] as Platform[]).map(p => (
                    <button key={p} type="button" onClick={() => setPlatform(p)}
                      className={`py-3 rounded-xl border text-sm font-medium transition-colors capitalize ${
                        platform === p
                          ? 'border-indigo-500 bg-indigo-500/10 text-indigo-300'
                          : 'border-[#2a2f3e] text-slate-400 hover:border-slate-500 hover:text-white'
                      }`}>
                      {p === 'nuvemshop' ? 'Nuvemshop' : p === 'woocommerce' ? 'WooCommerce' : 'Shopify'}
                    </button>
                  ))}
                </div>
              </div>

              {platform === 'shopify' && (
                <>
                  <div>
                    <label className={LABEL}>Domínio Shopify</label>
                    <input value={domain} onChange={e => setDomain(e.target.value)}
                      placeholder="lksneakers.myshopify.com" className={INPUT} />
                  </div>
                  <div>
                    <label className={LABEL}>
                      Admin API Access Token{' '}
                      <span className="text-slate-600 font-normal">(para auto-registrar webhooks)</span>
                    </label>
                    <input type="password" value={apiToken} onChange={e => setApiToken(e.target.value)}
                      placeholder="shpat_..." className={INPUT} />
                    <p className="text-xs text-slate-600 mt-1.5">
                      Shopify Admin → Apps → Develop apps → Create app → Admin API → read_orders + write_webhooks
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

              {error && (
                <p className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">{error}</p>
              )}

              <button type="submit" disabled={loading || !storeName}
                className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2 transition-colors">
                {loading
                  ? <><Loader2 size={14} className="animate-spin" /> Criando loja…</>
                  : <><ArrowRight size={14} /> Criar loja e continuar</>}
              </button>
            </form>
          </div>
        )}

        {/* ── STEP 2 ── */}
        {step === 2 && (
          <>
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-2xl p-8">
              <div className="flex items-center gap-2 mb-2">
                <Zap size={18} className="text-yellow-400" />
                <h2 className="text-base font-semibold text-white">Instalar o pixel</h2>
              </div>
              <p className="text-xs text-slate-500 mb-6">
                Cole no <code className="bg-[#252b3b] px-1.5 py-0.5 rounded text-slate-300">&lt;head&gt;</code> de todas as páginas da loja.
              </p>

              <div className="space-y-5">
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-xs font-medium text-slate-400">Snippet universal</p>
                    <CopyButton text={trackerSnippet} />
                  </div>
                  <pre className="bg-[#0f1117] border border-[#2a2f3e] rounded-xl p-4 text-xs text-slate-300 overflow-x-auto leading-relaxed whitespace-pre-wrap">
                    {trackerSnippet}
                  </pre>
                </div>

                {platform === 'shopify' && (
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <p className="text-xs font-medium text-slate-400">theme.liquid (Shopify)</p>
                      <CopyButton text={liquidSnippet} />
                    </div>
                    <pre className="bg-[#0f1117] border border-[#2a2f3e] rounded-xl p-4 text-xs text-slate-300 overflow-x-auto leading-relaxed whitespace-pre-wrap">
                      {liquidSnippet}
                    </pre>
                  </div>
                )}

                <div className="bg-[#0f1117] border border-[#2a2f3e] rounded-xl p-4">
                  <p className="text-xs text-slate-500 mb-1.5">URL do webhook</p>
                  <div className="flex items-center justify-between">
                    <code className="text-xs text-indigo-300">{API_URL}/webhook/{platform}/{pixelId}</code>
                    <CopyButton text={`${API_URL}/webhook/${platform}/${pixelId}`} />
                  </div>
                </div>
              </div>
            </div>

            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5 flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-white">Verificar instalação</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  {pixelVerified ? 'Pixel ativo — eventos sendo recebidos!' : 'Abra a loja no browser após instalar o snippet.'}
                </p>
              </div>
              <div className="flex items-center gap-3">
                {pixelVerified && <CheckCircle size={18} className="text-emerald-400 shrink-0" />}
                <button onClick={verifyPixel} disabled={verifying}
                  className="text-xs bg-[#252b3b] hover:bg-[#2e3448] border border-[#3a4058] text-slate-300 px-4 py-2 rounded-lg disabled:opacity-50 transition-colors">
                  {verifying ? <Loader2 size={12} className="animate-spin inline" /> : 'Verificar'}
                </button>
              </div>
            </div>

            <div className="flex gap-3">
              <button onClick={() => setStep(1)}
                className="flex-1 py-3 border border-[#2a2f3e] text-slate-400 hover:text-white rounded-xl text-sm transition-colors">
                Voltar
              </button>
              <button onClick={() => setStep(3)}
                className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2">
                <ArrowRight size={14} /> Continuar
              </button>
            </div>
          </>
        )}

        {/* ── STEP 3 ── */}
        {step === 3 && (
          <>
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-2xl p-8">
              <div className="flex items-center gap-2 mb-2">
                <Sparkles size={18} className="text-indigo-400" />
                <h2 className="text-base font-semibold text-white">Conectar plataformas de anúncios</h2>
              </div>
              <p className="text-xs text-slate-500 mb-6">Opcional. Configure depois nas Configurações se preferir.</p>

              <div className="flex gap-1 bg-[#0f1117] rounded-lg p-1 border border-[#2a2f3e] mb-6">
                {(['meta', 'google', 'tiktok'] as AdsTab[]).map(t => (
                  <button key={t} onClick={() => setAdsTab(t)}
                    className={`flex-1 py-2 rounded-lg text-xs font-medium transition-colors ${
                      adsTab === t ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
                    }`}>
                    {t === 'meta' ? 'Meta Ads' : t === 'google' ? 'Google Ads' : 'TikTok Ads'}
                  </button>
                ))}
              </div>

              {adsTab === 'meta' && (
                <div className="space-y-4">
                  <div><label className={LABEL}>Pixel ID</label>
                    <input value={metaForm.pixel_id} onChange={e => setMeta('pixel_id', e.target.value)}
                      placeholder="123456789012345" className={INPUT} /></div>
                  <div><label className={LABEL}>System User Access Token</label>
                    <input type="password" value={metaForm.access_token} onChange={e => setMeta('access_token', e.target.value)}
                      placeholder="EAAG..." className={INPUT} />
                    <p className="text-xs text-slate-600 mt-1">Business Manager → System Users → gerar token com ads_management</p>
                  </div>
                  <div><label className={LABEL}>Ad Account ID</label>
                    <input value={metaForm.ad_account_id} onChange={e => setMeta('ad_account_id', e.target.value)}
                      placeholder="act_123456789" className={INPUT} /></div>
                </div>
              )}

              {adsTab === 'google' && (
                <div className="space-y-4">
                  <div><label className={LABEL}>Customer ID</label>
                    <input value={googleForm.customer_id} onChange={e => setGoogle('customer_id', e.target.value)}
                      placeholder="162-897-1213" className={INPUT} /></div>
                  <div><label className={LABEL}>AW-ID <span className="text-slate-600 font-normal">(para o script de remarketing)</span></label>
                    <input value={googleForm.aw_id} onChange={e => setGoogle('aw_id', e.target.value)}
                      placeholder="AW-123456789" className={INPUT} /></div>
                  <p className="text-xs text-slate-600">OAuth Google Ads: configure nas Configurações da loja após criar.</p>
                </div>
              )}

              {adsTab === 'tiktok' && (
                <div className="space-y-4">
                  <div><label className={LABEL}>Pixel Code</label>
                    <input value={tiktokForm.pixel_id} onChange={e => setTiktok('pixel_id', e.target.value)}
                      placeholder="C3XXXXXXXXXXXX" className={INPUT} /></div>
                  <div><label className={LABEL}>Events API Access Token</label>
                    <input type="password" value={tiktokForm.access_token} onChange={e => setTiktok('access_token', e.target.value)}
                      placeholder="token..." className={INPUT} /></div>
                </div>
              )}

              {adsSaved && (
                <div className="flex items-center gap-2 mt-4 text-emerald-400 text-sm">
                  <CheckCircle size={14} /> Configurações salvas!
                </div>
              )}
            </div>

            <div className="flex gap-3">
              <button onClick={() => setStep(2)}
                className="flex-1 py-3 border border-[#2a2f3e] text-slate-400 hover:text-white rounded-xl text-sm transition-colors">
                Voltar
              </button>
              <button onClick={async () => { await saveAds(); setStep(4) }} disabled={savingAds}
                className="flex-1 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2">
                {savingAds ? <><Loader2 size={14} className="animate-spin" /> Salvando…</> : <><ArrowRight size={14} /> Salvar e continuar</>}
              </button>
            </div>
            <button onClick={() => setStep(4)} className="w-full text-xs text-slate-600 hover:text-slate-400 py-2 transition-colors">
              Pular por agora (configurar depois em Configurações)
            </button>
          </>
        )}

        {/* ── STEP 4 ── */}
        {step === 4 && (
          <>
            <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-2xl p-8">
              <h2 className="text-base font-semibold text-white mb-2">Configurar webhooks de pedidos</h2>
              <p className="text-xs text-slate-500 mb-6">
                Os webhooks enviam pedidos em tempo real — necessário para Meta CAPI e atribuição funcionar.
              </p>

              {platform === 'shopify' ? (
                <div className="space-y-5">
                  <div className="bg-[#0f1117] border border-[#2a2f3e] rounded-xl p-4">
                    <p className="text-xs text-slate-500 mb-1.5">URL de destino</p>
                    <div className="flex items-center justify-between">
                      <code className="text-xs text-indigo-300">{API_URL}/webhook/shopify/{pixelId}</code>
                      <CopyButton text={`${API_URL}/webhook/shopify/${pixelId}`} />
                    </div>
                  </div>

                  {hooksResult ? (
                    <div className={`rounded-xl p-4 border ${hooksResult.fail === 0 ? 'bg-emerald-500/10 border-emerald-500/20' : 'bg-yellow-500/10 border-yellow-500/20'}`}>
                      <p className={`text-sm font-semibold ${hooksResult.fail === 0 ? 'text-emerald-300' : 'text-yellow-300'}`}>
                        {hooksResult.fail === 0
                          ? `${hooksResult.ok} webhooks registrados com sucesso!`
                          : `${hooksResult.ok} ok · ${hooksResult.fail} falhou — verifique o Admin Token e tente novamente.`}
                      </p>
                    </div>
                  ) : (
                    <button onClick={registerWebhooks} disabled={registeringHooks}
                      className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2 transition-colors">
                      {registeringHooks
                        ? <><Loader2 size={14} className="animate-spin" /> Registrando…</>
                        : 'Auto-registrar todos os webhooks Shopify'}
                    </button>
                  )}
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="bg-[#0f1117] border border-[#2a2f3e] rounded-xl p-4">
                    <p className="text-xs text-slate-500 mb-1.5">URL do webhook</p>
                    <div className="flex items-center justify-between">
                      <code className="text-xs text-indigo-300">{API_URL}/webhook/{platform}/{pixelId}</code>
                      <CopyButton text={`${API_URL}/webhook/${platform}/${pixelId}`} />
                    </div>
                  </div>
                  <p className="text-xs text-slate-500 leading-relaxed">
                    {platform === 'nuvemshop'
                      ? 'Nuvemshop: Parceiros → App → Notificações (Webhooks) → Adicionar → events: orders/created, orders/paid'
                      : 'WooCommerce: WooCommerce → Settings → Advanced → Webhooks → Add → Topics: Order Created, Order Updated'}
                  </p>
                </div>
              )}
            </div>

            <div className="flex gap-3">
              <button onClick={() => setStep(3)}
                className="flex-1 py-3 border border-[#2a2f3e] text-slate-400 hover:text-white rounded-xl text-sm transition-colors">
                Voltar
              </button>
              <button onClick={() => setStep(5)}
                className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2">
                <ArrowRight size={14} /> Finalizar
              </button>
            </div>
          </>
        )}

        {/* ── STEP 5 ── */}
        {step === 5 && (
          <div className="text-center py-4 space-y-7">
            <div className="w-20 h-20 rounded-2xl bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center mx-auto">
              <CheckCircle size={40} className="text-emerald-400" />
            </div>

            <div>
              <h2 className="text-2xl font-bold text-white mb-2">Tudo pronto!</h2>
              <p className="text-slate-400">
                <span className="text-white font-semibold">{storeName}</span> está configurada e pronta para rastrear conversões.
              </p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-left max-w-md mx-auto">
              {[
                { label: 'Pixel ID', value: pixelId },
                { label: 'Webhook URL', value: `${API_URL.replace('https://', '…')}/webhook/${platform}/${pixelId.slice(0, 8)}…` },
              ].map(({ label, value }) => (
                <div key={label} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-4">
                  <p className="text-xs text-slate-500 mb-1">{label}</p>
                  <code className="text-xs text-indigo-300 break-all">{value}</code>
                </div>
              ))}
            </div>

            <div className="flex flex-col gap-3 max-w-sm mx-auto">
              <button onClick={() => router.push(`/clients/${pixelId}/dashboard`)}
                className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3.5 rounded-xl text-sm flex items-center justify-center gap-2 transition-colors">
                <ArrowRight size={14} /> Abrir dashboard
              </button>
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
