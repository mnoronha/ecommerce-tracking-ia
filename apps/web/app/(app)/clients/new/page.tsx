'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { createSupabaseBrowserClient } from '@/lib/supabase-browser'
import { ArrowLeft, Loader2, CheckCircle } from 'lucide-react'

const PLATFORMS = [
  { value: 'shopify',     label: 'Shopify' },
  { value: 'nuvemshop',   label: 'Nuvemshop' },
  { value: 'woocommerce', label: 'WooCommerce' },
]

export default function NewClientPage() {
  const router = useRouter()
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')

  const [form, setForm] = useState({
    name:                  '',
    ecommerce_platform:    'shopify',
    shopify_domain:        '',
    shopify_access_token:  '',
    meta_pixel_id:         '',
    meta_access_token:     '',
    meta_ad_account_id:    '',
    ga4_measurement_id:    '',
    ga4_api_secret:        '',
    google_ads_customer_id:'',
    alert_email:           '',
    slack_webhook_url:     '',
  })

  function set(key: string, value: string) {
    setForm(f => ({ ...f, [key]: value }))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      const supabase = createSupabaseBrowserClient()

      // Get agency_id from current user's membership
      const { data: { user } } = await supabase.auth.getUser()
      if (!user) { setError('Sessão expirada. Faça login novamente.'); setLoading(false); return }

      const { data: membership } = await supabase
        .from('agency_members')
        .select('agency_id')
        .eq('user_id', user.id)
        .limit(1)
        .single()

      if (!membership) { setError('Usuário não vinculado a nenhuma agência.'); setLoading(false); return }

      const payload: Record<string, string | boolean> = {
        name:               form.name,
        ecommerce_platform: form.ecommerce_platform,
        agency_id:          membership.agency_id,
        is_active:          true,
      }

      const optionals = [
        'shopify_domain','shopify_access_token',
        'meta_pixel_id','meta_access_token','meta_ad_account_id',
        'ga4_measurement_id','ga4_api_secret',
        'google_ads_customer_id','alert_email','slack_webhook_url',
      ] as const
      for (const k of optionals) {
        if (form[k]) payload[k] = form[k]
      }

      const { data, error: insertError } = await supabase
        .from('clients')
        .insert(payload)
        .select('pixel_id')
        .single()

      if (insertError) throw insertError

      router.push(`/clients/${data.pixel_id}/settings?created=1`)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro ao criar cliente.'
      setError(msg)
      setLoading(false)
    }
  }

  return (
    <div className="p-6 max-w-2xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <Link href="/clients" className="text-slate-500 hover:text-white transition-colors">
          <ArrowLeft size={18} />
        </Link>
        <div>
          <h1 className="text-xl font-bold text-white">Novo cliente</h1>
          <p className="text-xs text-slate-500 mt-0.5">Preencha os dados do cliente e suas credenciais de tracking</p>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Dados básicos */}
        <Section title="Dados básicos">
          <Field label="Nome do cliente *">
            <input required value={form.name} onChange={e => set('name', e.target.value)}
              placeholder="Ex: LK Sneakers" className={INPUT} />
          </Field>
          <Field label="Plataforma *">
            <select required value={form.ecommerce_platform} onChange={e => set('ecommerce_platform', e.target.value)} className={INPUT}>
              {PLATFORMS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
            </select>
          </Field>
          {form.ecommerce_platform === 'shopify' && (
            <>
              <Field label="Domínio Shopify" hint="Ex: minhaloja.myshopify.com">
                <input value={form.shopify_domain} onChange={e => set('shopify_domain', e.target.value)}
                  placeholder="minhaloja.myshopify.com" className={INPUT} />
              </Field>
              <Field label="Shopify Access Token">
                <input type="password" value={form.shopify_access_token} onChange={e => set('shopify_access_token', e.target.value)}
                  placeholder="shpat_..." className={INPUT} />
              </Field>
            </>
          )}
        </Section>

        {/* Meta */}
        <Section title="Meta (Facebook/Instagram)">
          <Field label="Pixel ID">
            <input value={form.meta_pixel_id} onChange={e => set('meta_pixel_id', e.target.value)}
              placeholder="1018779385487104" className={INPUT} />
          </Field>
          <Field label="Access Token (CAPI)">
            <input type="password" value={form.meta_access_token} onChange={e => set('meta_access_token', e.target.value)}
              placeholder="EAAEZBB7cZ..." className={INPUT} />
          </Field>
          <Field label="Ad Account ID" hint="Ex: act_1234567890">
            <input value={form.meta_ad_account_id} onChange={e => set('meta_ad_account_id', e.target.value)}
              placeholder="act_1234567890" className={INPUT} />
          </Field>
        </Section>

        {/* GA4 */}
        <Section title="Google Analytics 4">
          <Field label="Measurement ID">
            <input value={form.ga4_measurement_id} onChange={e => set('ga4_measurement_id', e.target.value)}
              placeholder="G-XXXXXXXXXX" className={INPUT} />
          </Field>
          <Field label="API Secret">
            <input type="password" value={form.ga4_api_secret} onChange={e => set('ga4_api_secret', e.target.value)}
              placeholder="api_secret..." className={INPUT} />
          </Field>
        </Section>

        {/* Google Ads */}
        <Section title="Google Ads">
          <Field label="Customer ID" hint="Ex: 162-897-1213">
            <input value={form.google_ads_customer_id} onChange={e => set('google_ads_customer_id', e.target.value)}
              placeholder="162-897-1213" className={INPUT} />
          </Field>
        </Section>

        {/* Alertas */}
        <Section title="Alertas e notificações">
          <Field label="Email para relatórios semanais">
            <input type="email" value={form.alert_email} onChange={e => set('alert_email', e.target.value)}
              placeholder="marketing@empresa.com" className={INPUT} />
          </Field>
          <Field label="Slack Webhook URL">
            <input value={form.slack_webhook_url} onChange={e => set('slack_webhook_url', e.target.value)}
              placeholder="https://hooks.slack.com/services/..." className={INPUT} />
          </Field>
        </Section>

        {error && (
          <p className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">{error}</p>
        )}

        <div className="flex gap-3">
          <Link href="/clients" className="flex-1 flex items-center justify-center bg-[#1a1f2e] hover:bg-[#252b3b] border border-[#2a2f3e] text-slate-300 text-sm font-medium py-2.5 rounded-lg transition-colors">
            Cancelar
          </Link>
          <button type="submit" disabled={loading}
            className="flex-1 flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium py-2.5 rounded-lg transition-colors">
            {loading ? <><Loader2 size={14} className="animate-spin" />Criando...</> : <><CheckCircle size={14} />Criar cliente</>}
          </button>
        </div>
      </form>
    </div>
  )
}

const INPUT = 'w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-600 outline-none transition-colors focus:border-indigo-500'

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
