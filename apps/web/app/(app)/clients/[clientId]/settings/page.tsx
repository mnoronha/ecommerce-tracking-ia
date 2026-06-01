'use client'

import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { useParams, useSearchParams } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import { ArrowLeft, Loader2, Save, CheckCircle, AlertCircle, Send, Zap, Plus, Trash2, MessageSquare } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

interface ClientRow {
  id: string
  name: string
  ecommerce_platform: string
  shopify_domain: string | null
  shopify_access_token: string | null
  meta_pixel_id: string | null
  meta_access_token: string | null
  meta_ad_account_id: string | null
  meta_token_expires_at: string | null
  meta_token_health: string | null
  ga4_measurement_id: string | null
  ga4_api_secret: string | null
  google_ads_customer_id: string | null
  google_ads_conversion_action_id: string | null
  google_ads_add_to_cart_action_id: string | null
  google_ads_checkout_action_id: string | null
  google_ads_aw_id: string | null
  google_ads_refresh_token: string | null
  tiktok_pixel_id: string | null
  tiktok_access_token: string | null
  tiktok_advertiser_id: string | null
  alert_email: string | null
  alert_emails: string[]
  whatsapp_group_jid: string | null
  webhook_secret: string | null
  slack_webhook_url: string | null
  is_active: boolean
  client_type: string | null
  reports_enabled: boolean | null
  tracking_cname: string | null
  tracking_cname_verified: boolean | null
  tracking_cname_secret: string | null
  meta_prepaid: boolean
  google_prepaid: boolean
  meta_balance_threshold: number | null
  google_balance_threshold: number | null
}

const INPUT = 'w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-600 outline-none transition-colors focus:border-indigo-500'

export default function ClientSettingsPage() {
  const params       = useParams()
  const searchParams = useSearchParams()
  const clientId     = params.clientId as string
  const justCreated       = searchParams.get('created')   === '1'
  const connected         = searchParams.get('connected')
  const justConnectedGA   = connected === 'google'
  const justConnectedMeta = connected === 'meta'
  const oauthError        = searchParams.get('error')

  const [client,  setClient]  = useState<ClientRow | null>(null)
  const [form,    setForm]    = useState<Partial<ClientRow>>({})
  const [loading, setLoading] = useState(true)
  const [saving,  setSaving]  = useState(false)
  const [saved,   setSaved]   = useState(false)
  const [error,   setError]   = useState('')
  const [registeringHooks, setRegisteringHooks] = useState(false)
  const [hooksResult, setHooksResult] = useState<{ ok: number; fail: number } | null>(null)
  const [cnameInput, setCnameInput] = useState('')
  const [cnameSecret, setCnameSecret] = useState<string | null>(null)
  const [cnameVerifying, setCnameVerifying] = useState(false)
  const [cnameMsg, setCnameMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [testingSlack, setTestingSlack] = useState(false)
  const [slackTestMsg, setSlackTestMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [testingEmail, setTestingEmail] = useState(false)
  const [emailTestMsg, setEmailTestMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [waGroupInput,  setWaGroupInput]  = useState('')
  const [resolvingWA,   setResolvingWA]   = useState(false)
  const [waResolveMsg,  setWaResolveMsg]  = useState<{ ok: boolean; text: string } | null>(null)

  const load = useCallback(async () => {
    // supabase singleton
    const { data } = await supabase
      .from('clients')
      .select('*')
      .eq('pixel_id', clientId)
      .single()
    if (data) { setClient(data); setForm(data) }
    setLoading(false)
  }, [clientId])

  useEffect(() => { load() }, [load])

  function set(key: string, value: string | boolean | string[]) {
    setForm(f => ({ ...f, [key]: value }))
  }

  // alert_emails helpers
  const emailList: string[] = (form.alert_emails as string[] | undefined) || []

  function addEmail() {
    set('alert_emails', [...emailList, ''])
  }
  function updateEmail(i: number, val: string) {
    const next = [...emailList]; next[i] = val; set('alert_emails', next)
  }
  function removeEmail(i: number) {
    set('alert_emails', emailList.filter((_, idx) => idx !== i))
  }

  // WhatsApp group resolve
  async function resolveWAGroup() {
    if (!waGroupInput.trim()) return
    setResolvingWA(true); setWaResolveMsg(null)
    try {
      const res = await fetch(
        `${API_URL}/notifications/whatsapp/resolve-invite?invite=${encodeURIComponent(waGroupInput.trim())}`,
        { method: 'POST' }
      )
      const data = await res.json()
      if (res.ok) {
        set('whatsapp_group_jid', data.jid)
        setWaGroupInput('')
        setWaResolveMsg({ ok: true, text: `JID resolvido: ${data.subject || data.jid}` })
      } else {
        setWaResolveMsg({ ok: false, text: data.detail || 'Não foi possível resolver' })
      }
    } catch {
      setWaResolveMsg({ ok: false, text: 'Erro de conexão' })
    }
    setResolvingWA(false)
  }

  async function handleDisconnectGoogle() {
    // supabase singleton
    await supabase
      .from('clients')
      .update({ google_ads_refresh_token: null })
      .eq('pixel_id', clientId)
    setForm(f => ({ ...f, google_ads_refresh_token: null }))
  }

  async function initCname() {
    setCnameMsg(null)
    if (!cnameInput || !/^[a-zA-Z0-9.-]+$/.test(cnameInput)) {
      setCnameMsg({ ok: false, text: 'Hostname inválido' })
      return
    }
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'
    const res = await fetch(`${apiUrl}/setup/cname/${clientId}/init`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cname: cnameInput }),
    })
    if (!res.ok) {
      setCnameMsg({ ok: false, text: 'Falha ao inicializar' })
      return
    }
    const data = await res.json()
    setCnameSecret(data.secret)
    setCnameMsg({ ok: true, text: 'CNAME registrado. Configure DNS e clique em Verificar.' })
    load()
  }

  async function verifyCname() {
    setCnameVerifying(true)
    setCnameMsg(null)
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'
      const res = await fetch(`${apiUrl}/setup/cname/${clientId}/verify`, { method: 'POST' })
      const data = await res.json()
      if (data.verified) {
        setCnameMsg({ ok: true, text: '✓ CNAME verificado. Pixel agora usa first-party tracking.' })
        load()
      } else {
        setCnameMsg({ ok: false, text: data.hint || data.error || 'Verificação falhou' })
      }
    } finally {
      setCnameVerifying(false)
    }
  }

  async function handleRegisterHooks() {
    setRegisteringHooks(true)
    setHooksResult(null)
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'
      const res = await fetch(`${apiUrl}/setup/shopify/${clientId}/webhooks`, { method: 'POST' })
      if (!res.ok) {
        setHooksResult({ ok: 0, fail: 1 })
        return
      }
      const data = await res.json()
      setHooksResult({ ok: data.summary?.succeeded || 0, fail: data.summary?.failed || 0 })
    } catch {
      setHooksResult({ ok: 0, fail: 1 })
    } finally {
      setRegisteringHooks(false)
    }
  }

  async function handleDisconnectMeta() {
    // supabase singleton
    await supabase
      .from('clients')
      .update({
        meta_access_token:     null,
        meta_token_expires_at: null,
        meta_token_health:     'unknown',
      })
      .eq('pixel_id', clientId)
    setForm(f => ({
      ...f,
      meta_access_token:     null,
      meta_token_expires_at: null,
      meta_token_health:     'unknown',
    }))
  }

  async function testSlack() {
    setTestingSlack(true)
    setSlackTestMsg(null)
    try {
      const res  = await fetch(`${API_URL}/insights/${clientId}/test-alert`, { method: 'POST' })
      const json = await res.json()
      setSlackTestMsg(json.status === 'ok'
        ? { ok: true,  text: 'Mensagem enviada! Verifique o canal Slack.' }
        : { ok: false, text: 'Falha ao enviar. Verifique a URL do webhook.' })
    } catch {
      setSlackTestMsg({ ok: false, text: 'Erro de conexão.' })
    } finally {
      setTestingSlack(false)
    }
  }

  async function testEmail() {
    setTestingEmail(true)
    setEmailTestMsg(null)
    try {
      const res  = await fetch(`${API_URL}/insights/${clientId}/report`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: form.alert_email }),
      })
      const json = await res.json()
      if (!res.ok) {
        setEmailTestMsg({ ok: false, text: json.detail || `Erro ${res.status}` })
      } else {
        setEmailTestMsg({ ok: true, text: `Relatório enviado para ${json.email}` })
      }
    } catch {
      setEmailTestMsg({ ok: false, text: 'Erro de conexão.' })
    } finally {
      setTestingEmail(false)
    }
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError('')
    setSaved(false)

    // supabase singleton
    const { error: updateError } = await supabase
      .from('clients')
      .update({
        name:                            form.name,
        shopify_domain:                  form.shopify_domain   || null,
        shopify_access_token:            form.shopify_access_token || null,
        meta_pixel_id:                   form.meta_pixel_id    || null,
        meta_access_token:               form.meta_access_token || null,
        meta_ad_account_id:              form.meta_ad_account_id || null,
        ga4_measurement_id:              form.ga4_measurement_id || null,
        ga4_api_secret:                  form.ga4_api_secret   || null,
        google_ads_customer_id:           form.google_ads_customer_id || null,
        google_ads_conversion_action_id:  form.google_ads_conversion_action_id || null,
        google_ads_add_to_cart_action_id: form.google_ads_add_to_cart_action_id || null,
        google_ads_checkout_action_id:    form.google_ads_checkout_action_id || null,
        google_ads_aw_id:                 form.google_ads_aw_id || null,
        google_ads_refresh_token:         form.google_ads_refresh_token || null,
        tiktok_pixel_id:                 form.tiktok_pixel_id      || null,
        tiktok_access_token:             form.tiktok_access_token  || null,
        tiktok_advertiser_id:            form.tiktok_advertiser_id || null,
        alert_email:                     (form.alert_emails as string[] | undefined)?.[0] || form.alert_email || null,
        alert_emails:                    (form.alert_emails as string[] | undefined)?.filter(e => e.trim()) || [],
        whatsapp_group_jid:              form.whatsapp_group_jid    || null,
        slack_webhook_url:               form.slack_webhook_url     || null,
        webhook_secret:                  form.webhook_secret        || null,
        is_active:                       form.is_active,
        client_type:                     form.client_type           || 'ecommerce',
        reports_enabled:                 form.reports_enabled       ?? false,
        meta_prepaid:                    form.meta_prepaid          ?? false,
        google_prepaid:                  form.google_prepaid        ?? false,
        meta_balance_threshold:          form.meta_balance_threshold ?? 200,
        google_balance_threshold:        form.google_balance_threshold ?? 200,
      })
      .eq('pixel_id', clientId)

    if (updateError) setError(updateError.message)
    else setSaved(true)
    setSaving(false)
  }

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <Loader2 size={20} className="animate-spin text-slate-500" />
    </div>
  )

  return (
    <div className="p-6 max-w-2xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <Link href="/clients" className="text-slate-500 hover:text-white transition-colors">
          <ArrowLeft size={18} />
        </Link>
        <div>
          <h1 className="text-xl font-bold text-white">{client?.name || clientId}</h1>
          <p className="text-xs text-slate-500 mt-0.5">Configurações e credenciais</p>
        </div>
      </div>

      {justCreated && (
        <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-sm rounded-lg px-4 py-3 mb-5">
          <CheckCircle size={15} />
          Cliente criado com sucesso! Configure as credenciais abaixo.
        </div>
      )}

      {justConnectedGA && (
        <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-sm rounded-lg px-4 py-3 mb-5">
          <CheckCircle size={15} />
          Google Ads conectado com sucesso!
        </div>
      )}

      {justConnectedMeta && (
        <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-sm rounded-lg px-4 py-3 mb-5">
          <CheckCircle size={15} />
          Meta conectado com sucesso! Token válido por 60 dias.
        </div>
      )}

      {oauthError && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/20 text-red-400 text-sm rounded-lg px-4 py-3 mb-5">
          <AlertCircle size={15} />
          {oauthError.startsWith('google_oauth_') && (
            <>
              {oauthError === 'google_oauth_denied'     && 'Autorização Google negada pelo usuário.'}
              {oauthError === 'google_oauth_csrf'       && 'Sessão expirada. Tente conectar novamente.'}
              {oauthError === 'google_oauth_no_refresh' && 'Token Google não retornado. Clique em "Conectar" novamente para re-autorizar.'}
              {oauthError === 'google_oauth_token'      && 'Falha ao trocar código Google por token. Verifique as credenciais OAuth no servidor.'}
              {oauthError === 'google_oauth_db'         && 'Token Google obtido, mas falhou ao salvar no banco. Tente novamente.'}
            </>
          )}
          {oauthError.startsWith('meta_oauth_') && (
            <>
              {oauthError === 'meta_oauth_denied'        && 'Autorização Meta negada pelo usuário.'}
              {oauthError === 'meta_oauth_csrf'          && 'Sessão expirada. Tente conectar novamente.'}
              {oauthError === 'meta_oauth_token'         && 'Falha ao trocar código Meta por token. Verifique as credenciais do app no servidor.'}
              {oauthError === 'meta_oauth_no_token'      && 'Token Meta não retornado. Tente novamente.'}
              {oauthError === 'meta_oauth_long_token'    && 'Falha ao gerar token de longa duração Meta. Tente novamente.'}
              {oauthError === 'meta_oauth_no_long_token' && 'Token de longa duração Meta não retornado. Tente novamente.'}
              {oauthError === 'meta_oauth_db'            && 'Token Meta obtido, mas falhou ao salvar no banco. Tente novamente.'}
            </>
          )}
          {!oauthError.startsWith('google_oauth_') && !oauthError.startsWith('meta_oauth_') && `Erro: ${oauthError}`}
        </div>
      )}

      {/* Integration health summary */}
      <IntegrationHealth form={form} />

      <form onSubmit={handleSave} className="space-y-6">
        <Section title="Dados básicos">
          <Field label="Nome do cliente">
            <input value={form.name || ''} onChange={e => set('name', e.target.value)} className={INPUT} />
          </Field>
          {form.ecommerce_platform === 'shopify' && (
            <>
              <Field label="Domínio Shopify">
                <input value={form.shopify_domain || ''} onChange={e => set('shopify_domain', e.target.value)}
                  placeholder="minhaloja.myshopify.com" className={INPUT} />
              </Field>
              <Field label="Shopify Access Token">
                <input type="password" value={form.shopify_access_token || ''} onChange={e => set('shopify_access_token', e.target.value)}
                  placeholder="shpat_..." className={INPUT} />
              </Field>
              <Field label="Webhooks" hint="cria automaticamente todos os webhooks necessários na Shopify">
                <div className="space-y-2">
                  <button
                    type="button"
                    onClick={handleRegisterHooks}
                    disabled={registeringHooks || !form.shopify_domain || !form.shopify_access_token}
                    className="flex items-center justify-center gap-2 w-full bg-[#0f1117] border border-[#2a2f3e] hover:border-indigo-500 disabled:opacity-50 text-slate-300 hover:text-white text-sm font-medium py-2.5 rounded-lg transition-colors"
                  >
                    {registeringHooks ? (
                      <><Loader2 size={14} className="animate-spin" /> Registrando…</>
                    ) : (
                      'Registrar webhooks Shopify'
                    )}
                  </button>
                  {hooksResult && (
                    <p className={`text-xs ${hooksResult.fail === 0 ? 'text-emerald-400' : 'text-yellow-400'}`}>
                      {hooksResult.fail === 0
                        ? `✓ ${hooksResult.ok} webhooks configurados`
                        : `${hooksResult.ok} ok · ${hooksResult.fail} falharam — verifique access token e domínio`}
                    </p>
                  )}
                </div>
              </Field>
            </>
          )}
          <Field label="Tipo de cliente" hint="define as métricas dos relatórios: e-commerce ou geração de leads">
            <div className="grid grid-cols-2 gap-3">
              {([
                { v: 'ecommerce', label: 'E-commerce', hint: 'Faturamento, ROAS, pedidos' },
                { v: 'leads',     label: 'Leads',      hint: 'Leads, CPL, agendamentos' },
              ] as const).map(opt => (
                <button key={opt.v} type="button" onClick={() => set('client_type', opt.v)}
                  className={`py-3 px-3 rounded-xl border text-sm font-medium transition-colors text-left ${
                    (form.client_type || 'ecommerce') === opt.v
                      ? 'border-indigo-500 bg-indigo-500/10 text-indigo-300'
                      : 'border-[#2a2f3e] text-slate-400 hover:border-slate-500 hover:text-white'
                  }`}>
                  <span className="block">{opt.label}</span>
                  <span className="block text-[11px] font-normal text-slate-500 mt-0.5">{opt.hint}</span>
                </button>
              ))}
            </div>
          </Field>
          <Field label="Status">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={!!form.is_active} onChange={e => set('is_active', e.target.checked)}
                className="w-4 h-4 accent-indigo-500" />
              <span className="text-sm text-slate-300">Cliente ativo</span>
            </label>
          </Field>
        </Section>

        <Section title="Tracking first-party (CNAME)">
          <Field label="Subdomínio do cliente" hint="ex: track.lojadocliente.com.br — bypass de ITP/iOS, +30% match rate">
            {form.tracking_cname_verified ? (
              <div className="flex items-center justify-between px-3 py-2.5 bg-emerald-500/10 border border-emerald-500/20 rounded-lg">
                <div className="flex items-center gap-2 text-emerald-400 text-sm">
                  <CheckCircle size={14} />
                  <span className="font-mono text-xs">{form.tracking_cname}</span>
                  <span className="text-xs text-slate-500 ml-2">first-party ativo</span>
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                <div className="flex gap-2">
                  <input
                    value={cnameInput || form.tracking_cname || ''}
                    onChange={e => setCnameInput(e.target.value)}
                    placeholder="track.lojadocliente.com.br"
                    className={INPUT}
                  />
                  <button
                    type="button"
                    onClick={form.tracking_cname ? verifyCname : initCname}
                    disabled={cnameVerifying}
                    className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors whitespace-nowrap"
                  >
                    {cnameVerifying ? 'Verificando…' : (form.tracking_cname ? 'Verificar' : 'Salvar')}
                  </button>
                </div>
                {form.tracking_cname && !form.tracking_cname_verified && (
                  <p className="text-xs text-slate-500">
                    Configure CNAME <code className="bg-[#0f1117] px-1 rounded">{form.tracking_cname} → tracking.pareto.plus</code> e clique em Verificar.
                  </p>
                )}
                {cnameMsg && (
                  <p className={`text-xs ${cnameMsg.ok ? 'text-emerald-400' : 'text-yellow-400'}`}>
                    {cnameMsg.text}
                  </p>
                )}
              </div>
            )}
          </Field>
        </Section>

        <Section title="Meta (Facebook/Instagram)">
          <Field label="Autenticação OAuth">
            {form.meta_access_token ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between px-3 py-2.5 bg-emerald-500/10 border border-emerald-500/20 rounded-lg">
                  <div className="flex items-center gap-2 text-emerald-400 text-sm">
                    <CheckCircle size={14} />
                    Conta Meta vinculada
                    {form.meta_token_expires_at && (
                      <span className="text-xs text-slate-500 ml-2">
                        · expira {new Date(form.meta_token_expires_at).toLocaleDateString('pt-BR')}
                      </span>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={handleDisconnectMeta}
                    className="text-xs text-slate-500 hover:text-red-400 transition-colors"
                  >
                    Desconectar
                  </button>
                </div>
                {form.meta_token_health === 'expiring_soon' && (
                  <p className="text-xs text-yellow-400 flex items-center gap-1.5">
                    <AlertCircle size={12} /> Token expira em breve — clique em &quot;Conectar Meta&quot; para renovar
                  </p>
                )}
                {form.meta_token_health === 'expired' && (
                  <p className="text-xs text-red-400 flex items-center gap-1.5">
                    <AlertCircle size={12} /> Token expirado — reconecte para continuar enviando eventos
                  </p>
                )}
              </div>
            ) : (
              <a
                href={`/api/meta/oauth/start?clientId=${clientId}`}
                className="flex items-center justify-center gap-2 w-full bg-[#0f1117] border border-[#2a2f3e] hover:border-indigo-500 text-slate-300 hover:text-white text-sm font-medium py-2.5 rounded-lg transition-colors"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="#1877F2">
                  <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/>
                </svg>
                Conectar Meta
              </a>
            )}
          </Field>
          <Field label="Access Token" hint="System User token (permanente) — cole aqui ou use OAuth acima">
            <input
              type="password"
              value={form.meta_access_token || ''}
              onChange={e => set('meta_access_token', e.target.value)}
              placeholder={form.meta_access_token ? '••••••••••••••••' : 'EAAxxxxxxx...'}
              className={INPUT}
            />
          </Field>
          <Field label="Pixel ID" hint="auto-detectado após conectar OAuth, ou preencha manualmente">
            <input value={form.meta_pixel_id || ''} onChange={e => set('meta_pixel_id', e.target.value)}
              placeholder="1018779385487104" className={INPUT} />
          </Field>
          <Field label="Ad Account ID" hint="auto-detectado após conectar OAuth">
            <input value={form.meta_ad_account_id || ''} onChange={e => set('meta_ad_account_id', e.target.value)}
              placeholder="act_1234567890" className={INPUT} />
          </Field>
        </Section>

        <Section title="Google Analytics 4">
          <Field label="Measurement ID">
            <input value={form.ga4_measurement_id || ''} onChange={e => set('ga4_measurement_id', e.target.value)}
              placeholder="G-XXXXXXXXXX" className={INPUT} />
          </Field>
          <Field label="API Secret">
            <input type="password" value={form.ga4_api_secret || ''} onChange={e => set('ga4_api_secret', e.target.value)}
              placeholder="api_secret..." className={INPUT} />
          </Field>
        </Section>

        <Section title="Google Ads">
          <Field label="Customer ID" hint="ex: 162-897-1213">
            <input value={form.google_ads_customer_id || ''} onChange={e => set('google_ads_customer_id', e.target.value)}
              placeholder="162-897-1213" className={INPUT} />
          </Field>
          <Field label="AW-ID (snippet Shopify)" hint="AW-XXXXXXXXXX — para o script de remarketing">
            <input value={form.google_ads_aw_id || ''} onChange={e => set('google_ads_aw_id', e.target.value)}
              placeholder="AW-123456789" className={INPUT} />
          </Field>
          <Field label="Conversion Action ID — Compra">
            <input value={form.google_ads_conversion_action_id || ''} onChange={e => set('google_ads_conversion_action_id', e.target.value)}
              placeholder="11392887484" className={INPUT} />
          </Field>
          <Field label="Conversion Action ID — Add to Cart">
            <input value={form.google_ads_add_to_cart_action_id || ''} onChange={e => set('google_ads_add_to_cart_action_id', e.target.value)}
              placeholder="11392887485" className={INPUT} />
          </Field>
          <Field label="Conversion Action ID — Checkout Iniciado">
            <input value={form.google_ads_checkout_action_id || ''} onChange={e => set('google_ads_checkout_action_id', e.target.value)}
              placeholder="11392887486" className={INPUT} />
          </Field>
          <Field label="Autenticação OAuth">
            {form.google_ads_refresh_token ? (
              <div className="flex items-center justify-between px-3 py-2.5 bg-emerald-500/10 border border-emerald-500/20 rounded-lg">
                <div className="flex items-center gap-2 text-emerald-400 text-sm">
                  <CheckCircle size={14} />
                  Conta Google Ads vinculada
                </div>
                <button
                  type="button"
                  onClick={handleDisconnectGoogle}
                  className="text-xs text-slate-500 hover:text-red-400 transition-colors"
                >
                  Desconectar
                </button>
              </div>
            ) : (
              <a
                href={`/api/google-ads/oauth/start?clientId=${clientId}`}
                className="flex items-center justify-center gap-2 w-full bg-[#0f1117] border border-[#2a2f3e] hover:border-indigo-500 text-slate-300 hover:text-white text-sm font-medium py-2.5 rounded-lg transition-colors"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" className="text-slate-400">
                  <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                  <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                  <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                  <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
                </svg>
                Conectar Google Ads
              </a>
            )}
          </Field>
        </Section>

        <Section title="TikTok Ads">
          <Field label="Pixel Code" hint="TikTok Pixel Code (ex: C3XXXXXXXXXXXX)">
            <input value={form.tiktok_pixel_id || ''} onChange={e => set('tiktok_pixel_id', e.target.value)}
              placeholder="C3XXXXXXXXXXXX" className={INPUT} />
          </Field>
          <Field label="Events API Access Token">
            <input type="password" value={form.tiktok_access_token || ''} onChange={e => set('tiktok_access_token', e.target.value)}
              placeholder="TikTok Events API token..." className={INPUT} />
          </Field>
          <Field label="Advertiser ID" hint="ID da conta no TikTok Ads Manager — necessário para sincronizar gasto diário">
            <input value={form.tiktok_advertiser_id || ''} onChange={e => set('tiktok_advertiser_id', e.target.value)}
              placeholder="7012345678901234567" className={INPUT} />
          </Field>
        </Section>

        <Section title="Alertas e notificações">
          {/* Multi-email list */}
          <Field label="Emails para relatórios e alertas" hint="todos os endereços receberão relatórios semanais, mensais e alertas críticos">
            <div className="space-y-2">
              {emailList.map((addr, i) => (
                <div key={i} className="flex items-center gap-2">
                  <input
                    type="email"
                    value={addr}
                    onChange={e => updateEmail(i, e.target.value)}
                    placeholder="marketing@empresa.com"
                    className={INPUT + ' flex-1'}
                  />
                  <button
                    type="button"
                    onClick={() => removeEmail(i)}
                    className="shrink-0 p-2 text-slate-600 hover:text-red-400 transition-colors"
                    title="Remover email"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
              <div className="flex items-center gap-3 pt-1">
                <button
                  type="button"
                  onClick={addEmail}
                  className="flex items-center gap-1.5 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
                >
                  <Plus size={12} /> Adicionar email
                </button>
                {emailList.some(e => e.trim()) && (
                  <>
                    <span className="text-slate-700">·</span>
                    <button
                      type="button"
                      onClick={testEmail}
                      disabled={testingEmail}
                      className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white bg-[#0f1117] border border-[#2a2f3e] hover:border-slate-600 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                    >
                      {testingEmail ? <Loader2 size={11} className="animate-spin" /> : <Send size={11} />}
                      Enviar teste
                    </button>
                    {emailTestMsg && (
                      <span className={`text-xs ${emailTestMsg.ok ? 'text-emerald-400' : 'text-red-400'}`}>
                        {emailTestMsg.ok ? '✓' : '✗'} {emailTestMsg.text}
                      </span>
                    )}
                  </>
                )}
              </div>
              {emailList.length === 0 && (
                <p className="text-xs text-slate-600">Nenhum email cadastrado. Clique em "Adicionar email" para começar.</p>
              )}
            </div>
          </Field>

          {/* WhatsApp group */}
          <Field label="Grupo WhatsApp" hint="Resumo semanal/mensal + alertas críticos enviados para o grupo da empresa">
            <div className="space-y-2">
              {form.whatsapp_group_jid ? (
                <div className="flex items-center gap-2">
                  <div className="flex-1 flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-3 py-2">
                    <MessageSquare size={13} className="text-emerald-400 shrink-0" />
                    <span className="text-xs text-emerald-300 font-mono truncate">{form.whatsapp_group_jid}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => { set('whatsapp_group_jid', ''); setWaResolveMsg(null) }}
                    className="p-2 text-slate-600 hover:text-red-400 transition-colors shrink-0"
                    title="Remover grupo"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              ) : (
                <div className="space-y-2">
                  <div className="flex gap-2">
                    <input
                      value={waGroupInput}
                      onChange={e => setWaGroupInput(e.target.value)}
                      placeholder="https://chat.whatsapp.com/... ou JID (120363xxx@g.us)"
                      className={INPUT + ' flex-1'}
                    />
                    <button
                      type="button"
                      onClick={resolveWAGroup}
                      disabled={resolvingWA || !waGroupInput.trim()}
                      className="flex items-center gap-1.5 text-xs bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white px-3 py-2 rounded-lg transition-colors shrink-0"
                    >
                      {resolvingWA ? <Loader2 size={11} className="animate-spin" /> : <MessageSquare size={11} />}
                      Resolver
                    </button>
                  </div>
                  {waResolveMsg && (
                    <p className={`text-xs ${waResolveMsg.ok ? 'text-emerald-400' : 'text-red-400'}`}>
                      {waResolveMsg.ok ? '✓' : '✗'} {waResolveMsg.text}
                    </p>
                  )}
                  <p className="text-xs text-slate-600">Cole o link de convite do grupo ou o JID direto. Salve após resolver.</p>
                </div>
              )}
            </div>
          </Field>
          <Field label="Relatórios de tráfego pago" hint="gera os relatórios semanal e mensal para este cliente">
            <label className="flex items-start gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={form.reports_enabled ?? false}
                onChange={e => set('reports_enabled', e.target.checked)}
                className="w-4 h-4 mt-0.5 accent-indigo-500 shrink-0"
              />
              <span>
                <span className="block text-sm text-slate-300">Ativar relatórios automáticos</span>
                <span className="block text-xs text-slate-500 mt-0.5">
                  Perfil {(form.client_type || 'ecommerce') === 'leads' ? 'Leads' : 'E-commerce'} — ajuste o tipo de cliente em Dados básicos.
                </span>
              </span>
            </label>
          </Field>
          {/* Pre-paid balance alerts */}
          <Field label="Conta Pré-Paga Meta Ads"
            hint="Ativa alerta de saldo baixo quando o crédito da conta Meta estiver abaixo do limite">
            <div className="space-y-3">
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.meta_prepaid ?? false}
                  onChange={e => set('meta_prepaid', e.target.checked)}
                  className="w-4 h-4 rounded border-slate-600 bg-[#0f1117] text-indigo-600 focus:ring-indigo-500"
                />
                <span className="text-sm text-slate-300">Cliente usa conta pré-paga Meta Ads</span>
              </label>
              {form.meta_prepaid && (
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Alerta quando saldo &lt; R$</label>
                  <input
                    type="number" min="0" step="50"
                    value={form.meta_balance_threshold ?? 200}
                    onChange={e => set('meta_balance_threshold', e.target.value)}
                    className={INPUT + ' w-40'}
                  />
                </div>
              )}
            </div>
          </Field>
          <Field label="Conta Pré-Paga Google Ads"
            hint="Ativa alerta quando o orçamento mensal restante for inferior ao burn rate de X dias">
            <div className="space-y-3">
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.google_prepaid ?? false}
                  onChange={e => set('google_prepaid', e.target.checked)}
                  className="w-4 h-4 rounded border-slate-600 bg-[#0f1117] text-indigo-600 focus:ring-indigo-500"
                />
                <span className="text-sm text-slate-300">Cliente usa conta pré-paga Google Ads</span>
              </label>
              {form.google_prepaid && (
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Alerta quando restar menos de R$</label>
                  <input
                    type="number" min="0" step="50"
                    value={form.google_balance_threshold ?? 200}
                    onChange={e => set('google_balance_threshold', e.target.value)}
                    className={INPUT + ' w-40'}
                  />
                </div>
              )}
            </div>
          </Field>

          <Field label="Slack Webhook URL">
            <div className="space-y-2">
              <input value={form.slack_webhook_url || ''} onChange={e => set('slack_webhook_url', e.target.value)}
                placeholder="https://hooks.slack.com/services/..." className={INPUT} />
              {form.slack_webhook_url && (
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={testSlack}
                    disabled={testingSlack}
                    className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white bg-[#0f1117] border border-[#2a2f3e] hover:border-slate-600 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                  >
                    {testingSlack ? <Loader2 size={11} className="animate-spin" /> : <Zap size={11} />}
                    Testar webhook
                  </button>
                  {slackTestMsg && (
                    <span className={`text-xs ${slackTestMsg.ok ? 'text-emerald-400' : 'text-red-400'}`}>
                      {slackTestMsg.ok ? '✓' : '✗'} {slackTestMsg.text}
                    </span>
                  )}
                </div>
              )}
            </div>
          </Field>
          <Field label="Webhook Secret" hint="segredo compartilhado para autenticar webhooks de Klaviyo, etc.">
            <input
              type="password"
              value={form.webhook_secret || ''}
              onChange={e => set('webhook_secret', e.target.value)}
              placeholder={form.webhook_secret ? '••••••••••••••••' : 'meu-segredo-secreto'}
              className={INPUT}
            />
            {form.webhook_secret && (
              <p className="text-xs text-slate-500 mt-1.5">
                URL Klaviyo: <code className="bg-[#0f1117] px-1 rounded text-slate-400">
                  {process.env.NEXT_PUBLIC_API_URL || 'https://api.noroia.com'}/webhook/klaviyo/{clientId}
                </code>
              </p>
            )}
          </Field>
        </Section>

        {error && (
          <p className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">{error}</p>
        )}
        {saved && (
          <div className="flex items-center gap-2 text-emerald-400 text-sm bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-4 py-3">
            <CheckCircle size={14} />
            Configurações salvas com sucesso.
          </div>
        )}

        <div className="flex gap-3">
          <Link href={`/clients/${clientId}/dashboard`}
            className="flex-1 flex items-center justify-center bg-[#1a1f2e] hover:bg-[#252b3b] border border-[#2a2f3e] text-slate-300 text-sm font-medium py-2.5 rounded-lg transition-colors">
            Ver dashboard
          </Link>
          <button type="submit" disabled={saving}
            className="flex-1 flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium py-2.5 rounded-lg transition-colors">
            {saving ? <><Loader2 size={14} className="animate-spin" />Salvando...</> : <><Save size={14} />Salvar</>}
          </button>
        </div>
      </form>
    </div>
  )
}

type HealthStatus = 'ok' | 'warning' | 'error' | 'inactive'

function healthDot(status: HealthStatus) {
  const colors: Record<HealthStatus, string> = {
    ok:       'bg-emerald-400',
    warning:  'bg-yellow-400',
    error:    'bg-red-400',
    inactive: 'bg-slate-600',
  }
  return <span className={`w-2 h-2 rounded-full shrink-0 ${colors[status]}`} />
}

function IntegrationHealth({ form }: { form: Partial<ClientRow> }) {
  const daysUntilExpiry = form.meta_token_expires_at
    ? Math.ceil((new Date(form.meta_token_expires_at).getTime() - Date.now()) / 86_400_000)
    : null

  const metaStatus: HealthStatus = !form.meta_access_token ? 'inactive'
    : form.meta_token_health === 'expired' ? 'error'
    : form.meta_token_health === 'expiring_soon' || (daysUntilExpiry != null && daysUntilExpiry <= 7) ? 'warning'
    : 'ok'

  const metaLabel = !form.meta_access_token ? 'não conectado'
    : metaStatus === 'error' ? 'token expirado'
    : daysUntilExpiry != null && daysUntilExpiry <= 30 ? `expira em ${daysUntilExpiry}d`
    : 'conectado'

  const items: { label: string; status: HealthStatus; detail: string }[] = [
    {
      label:  'Meta CAPI',
      status: metaStatus,
      detail: metaLabel,
    },
    {
      label:  'Google Ads',
      status: form.google_ads_refresh_token ? 'ok' : 'inactive',
      detail: form.google_ads_refresh_token ? 'conectado via OAuth' : 'não conectado',
    },
    {
      label:  'GA4',
      status: (form.ga4_measurement_id && form.ga4_api_secret) ? 'ok' : 'inactive',
      detail: form.ga4_measurement_id ? form.ga4_measurement_id : 'não configurado',
    },
    {
      label:  'TikTok',
      status: (form.tiktok_pixel_id && form.tiktok_access_token) ? 'ok' : 'inactive',
      detail: form.tiktok_pixel_id ? form.tiktok_pixel_id : 'não configurado',
    },
    {
      label:  'CNAME',
      status: form.tracking_cname_verified ? 'ok' : form.tracking_cname ? 'warning' : 'inactive',
      detail: form.tracking_cname_verified ? form.tracking_cname || 'verificado'
        : form.tracking_cname ? 'aguardando verificação'
        : 'não configurado',
    },
  ]

  const hasIssue = items.some(i => i.status === 'error' || i.status === 'warning')

  return (
    <div className={`bg-[#1a1f2e] border rounded-xl p-4 mb-2 ${hasIssue ? 'border-yellow-500/30' : 'border-[#2a2f3e]'}`}>
      <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3">Saúde das integrações</p>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        {items.map(({ label, status, detail }) => (
          <div key={label} className="flex items-start gap-2">
            <div className="mt-1">{healthDot(status)}</div>
            <div className="min-w-0">
              <p className="text-xs font-medium text-slate-300">{label}</p>
              <p className={`text-xs truncate ${
                status === 'error'   ? 'text-red-400'
                : status === 'warning' ? 'text-yellow-400'
                : status === 'ok'    ? 'text-emerald-400'
                : 'text-slate-600'
              }`}>{detail}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5">
      <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-4">{title}</h3>
      <div className="space-y-4">{children}</div>
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-300 mb-1.5">
        {label}
        {hint && <span className="text-slate-500 font-normal ml-1">— {hint}</span>}
      </label>
      {children}
    </div>
  )
}
