'use client'

import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { useParams, useSearchParams } from 'next/navigation'
import { createSupabaseBrowserClient } from '@/lib/supabase-browser'
import { ArrowLeft, Loader2, Save, CheckCircle } from 'lucide-react'

interface ClientRow {
  id: string
  name: string
  ecommerce_platform: string
  shopify_domain: string | null
  shopify_access_token: string | null
  meta_pixel_id: string | null
  meta_access_token: string | null
  meta_ad_account_id: string | null
  ga4_measurement_id: string | null
  ga4_api_secret: string | null
  google_ads_customer_id: string | null
  google_ads_conversion_action_id: string | null
  google_ads_add_to_cart_action_id: string | null
  google_ads_checkout_action_id: string | null
  google_ads_aw_id: string | null
  google_ads_refresh_token: string | null
  alert_email: string | null
  slack_webhook_url: string | null
  is_active: boolean
}

const INPUT = 'w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-600 outline-none transition-colors focus:border-indigo-500'

export default function ClientSettingsPage() {
  const params       = useParams()
  const searchParams = useSearchParams()
  const clientId     = params.clientId as string
  const justCreated  = searchParams.get('created') === '1'

  const [client,  setClient]  = useState<ClientRow | null>(null)
  const [form,    setForm]    = useState<Partial<ClientRow>>({})
  const [loading, setLoading] = useState(true)
  const [saving,  setSaving]  = useState(false)
  const [saved,   setSaved]   = useState(false)
  const [error,   setError]   = useState('')

  const load = useCallback(async () => {
    const supabase = createSupabaseBrowserClient()
    const { data } = await supabase
      .from('clients')
      .select('*')
      .eq('pixel_id', clientId)
      .single()
    if (data) { setClient(data); setForm(data) }
    setLoading(false)
  }, [clientId])

  useEffect(() => { load() }, [load])

  function set(key: string, value: string | boolean) {
    setForm(f => ({ ...f, [key]: value }))
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError('')
    setSaved(false)

    const supabase = createSupabaseBrowserClient()
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
        alert_email:                     form.alert_email      || null,
        slack_webhook_url:               form.slack_webhook_url || null,
        is_active:                       form.is_active,
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
            </>
          )}
          <Field label="Status">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={!!form.is_active} onChange={e => set('is_active', e.target.checked)}
                className="w-4 h-4 accent-indigo-500" />
              <span className="text-sm text-slate-300">Cliente ativo</span>
            </label>
          </Field>
        </Section>

        <Section title="Meta (Facebook/Instagram)">
          <Field label="Pixel ID">
            <input value={form.meta_pixel_id || ''} onChange={e => set('meta_pixel_id', e.target.value)}
              placeholder="1018779385487104" className={INPUT} />
          </Field>
          <Field label="Access Token (CAPI)">
            <input type="password" value={form.meta_access_token || ''} onChange={e => set('meta_access_token', e.target.value)}
              placeholder="EAAEZBB7cZ..." className={INPUT} />
          </Field>
          <Field label="Ad Account ID">
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
          <Field label="Refresh Token (OAuth)" hint="gerado via Google OAuth Playground">
            <input type="password" value={form.google_ads_refresh_token || ''} onChange={e => set('google_ads_refresh_token', e.target.value)}
              placeholder="1//04-..." className={INPUT} />
          </Field>
        </Section>

        <Section title="Alertas e notificações">
          <Field label="Email para relatórios semanais">
            <input type="email" value={form.alert_email || ''} onChange={e => set('alert_email', e.target.value)}
              placeholder="marketing@empresa.com" className={INPUT} />
          </Field>
          <Field label="Slack Webhook URL">
            <input value={form.slack_webhook_url || ''} onChange={e => set('slack_webhook_url', e.target.value)}
              placeholder="https://hooks.slack.com/services/..." className={INPUT} />
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
