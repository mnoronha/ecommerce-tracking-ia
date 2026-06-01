'use client'

import { useState, useEffect, useCallback } from 'react'
import {
  Mail, MessageSquare, CheckCircle, AlertCircle, Loader2,
  Send, RefreshCw, Info, ExternalLink, Settings,
} from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ─────────────────────────────────────────────────────────────────────

type NotifStatus = {
  email: {
    configured:   boolean
    provider:     'resend' | 'smtp' | 'none'
    from:         string | null
    agency_email: string | null
  }
  whatsapp: {
    configured:   boolean
    instance:     string | null
    agency_phone: string | null
    min_severity: string
    connected:    boolean
    state:        string | null
    error:        string | null
  }
}

// ── Visual helpers ────────────────────────────────────────────────────────────

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span className={`inline-block w-2 h-2 rounded-full ${ok ? 'bg-emerald-400' : 'bg-red-400'}`} />
  )
}

function Badge({ label, variant }: { label: string; variant: 'green' | 'red' | 'yellow' | 'slate' }) {
  const cls = {
    green:  'bg-emerald-500/15 text-emerald-400',
    red:    'bg-red-500/15 text-red-400',
    yellow: 'bg-yellow-500/15 text-yellow-400',
    slate:  'bg-slate-500/15 text-slate-400',
  }[variant]
  return <span className={`text-xs px-2 py-0.5 rounded font-medium ${cls}`}>{label}</span>
}

function EnvRow({ name, description, example }: { name: string; description: string; example: string }) {
  return (
    <div className="flex items-start gap-3 py-2.5 border-b border-[#2a2f3e] last:border-0">
      <code className="text-xs text-indigo-300 bg-indigo-500/10 px-2 py-0.5 rounded shrink-0 mt-0.5">
        {name}
      </code>
      <div className="flex-1 min-w-0">
        <p className="text-xs text-slate-300">{description}</p>
        <p className="text-xs text-slate-600 mt-0.5 font-mono">{example}</p>
      </div>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ConfiguracoesPage() {
  const [status,    setStatus]    = useState<NotifStatus | null>(null)
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState<string | null>(null)

  // Test states
  const [testingEmail, setTestingEmail] = useState(false)
  const [emailResult,  setEmailResult]  = useState<{ ok: boolean; msg: string } | null>(null)
  const [testingWA,    setTestingWA]    = useState(false)
  const [waResult,     setWAResult]     = useState<{ ok: boolean; msg: string } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_URL}/notifications/status`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setStatus(await res.json())
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erro ao carregar status')
    }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  async function testEmail() {
    setTestingEmail(true)
    setEmailResult(null)
    try {
      const res = await fetch(`${API_URL}/notifications/test/email`, { method: 'POST' })
      const data = await res.json()
      setEmailResult(res.ok
        ? { ok: true, msg: `Email enviado para ${data.to}` }
        : { ok: false, msg: data.detail || 'Falha ao enviar' }
      )
    } catch (e: unknown) {
      setEmailResult({ ok: false, msg: e instanceof Error ? e.message : 'Erro de rede' })
    }
    setTestingEmail(false)
  }

  async function testWhatsApp() {
    setTestingWA(true)
    setWAResult(null)
    try {
      const res = await fetch(`${API_URL}/notifications/test/whatsapp`, { method: 'POST' })
      const data = await res.json()
      setWAResult(res.ok
        ? { ok: true, msg: `Mensagem enviada para ${data.to}` }
        : { ok: false, msg: data.detail || 'Falha ao enviar' }
      )
    } catch (e: unknown) {
      setWAResult({ ok: false, msg: e instanceof Error ? e.message : 'Erro de rede' })
    }
    setTestingWA(false)
  }

  return (
    <div className="max-w-2xl mx-auto p-6 space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Settings size={18} className="text-indigo-400" />
            <h1 className="text-xl font-bold text-white">Notificações</h1>
          </div>
          <p className="text-xs text-slate-500 mt-0.5">
            Configurações de email e WhatsApp para alertas e relatórios da agência
          </p>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white transition-colors"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          Atualizar
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/20 text-red-400 text-xs rounded-lg px-4 py-2.5">
          <AlertCircle size={13} /> {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-40">
          <Loader2 size={20} className="animate-spin text-slate-500" />
        </div>
      ) : status && (
        <>
          {/* ── Email ── */}
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-indigo-500/15 flex items-center justify-center">
                  <Mail size={15} className="text-indigo-400" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-white">Email</p>
                  <p className="text-xs text-slate-500">Relatórios semanais, mensais e alertas críticos</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <StatusDot ok={status.email.configured} />
                <Badge
                  label={status.email.configured ? (status.email.provider === 'resend' ? 'Resend' : 'SMTP') : 'Não configurado'}
                  variant={status.email.configured ? 'green' : 'red'}
                />
              </div>
            </div>

            <div className="px-5 py-4 space-y-3">
              {status.email.configured ? (
                <>
                  <div className="grid grid-cols-2 gap-3 text-xs">
                    <div>
                      <p className="text-slate-500 mb-1">Provedor</p>
                      <p className="text-white font-medium capitalize">{status.email.provider}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 mb-1">Remetente</p>
                      <p className="text-white font-medium">{status.email.from || '—'}</p>
                    </div>
                    <div className="col-span-2">
                      <p className="text-slate-500 mb-1">Email da agência (destinatário)</p>
                      <p className="text-white font-medium">{status.email.agency_email || '— não configurado'}</p>
                    </div>
                  </div>

                  {/* Test button */}
                  <div className="pt-2 flex items-center gap-3">
                    <button
                      onClick={testEmail}
                      disabled={testingEmail || !status.email.agency_email}
                      className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors"
                    >
                      {testingEmail ? <Loader2 size={11} className="animate-spin" /> : <Send size={11} />}
                      Enviar teste
                    </button>
                    {emailResult && (
                      <span className={`flex items-center gap-1 text-xs ${emailResult.ok ? 'text-emerald-400' : 'text-red-400'}`}>
                        {emailResult.ok ? <CheckCircle size={12} /> : <AlertCircle size={12} />}
                        {emailResult.msg}
                      </span>
                    )}
                    {!status.email.agency_email && (
                      <span className="text-xs text-slate-600">Configure AGENCY_NOTIFY_EMAIL para testar</span>
                    )}
                  </div>
                </>
              ) : (
                <div className="text-xs text-slate-500 space-y-1">
                  <p>Configure pelo menos um dos provedores abaixo nas variáveis de ambiente.</p>
                </div>
              )}
            </div>
          </div>

          {/* ── WhatsApp ── */}
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-[#2a2f3e] flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-emerald-500/15 flex items-center justify-center">
                  <MessageSquare size={15} className="text-emerald-400" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-white">WhatsApp</p>
                  <p className="text-xs text-slate-500">Alertas críticos em tempo real via Evolution API</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <StatusDot ok={status.whatsapp.configured && status.whatsapp.connected} />
                <Badge
                  label={
                    !status.whatsapp.configured ? 'Não configurado' :
                    status.whatsapp.connected    ? 'Conectado' : 'Desconectado'
                  }
                  variant={
                    !status.whatsapp.configured ? 'slate' :
                    status.whatsapp.connected    ? 'green' : 'red'
                  }
                />
              </div>
            </div>

            <div className="px-5 py-4 space-y-3">
              {status.whatsapp.configured ? (
                <>
                  <div className="grid grid-cols-2 gap-3 text-xs">
                    <div>
                      <p className="text-slate-500 mb-1">Instância</p>
                      <p className="text-white font-medium font-mono">{status.whatsapp.instance || '—'}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 mb-1">Estado</p>
                      <p className={`font-medium ${status.whatsapp.connected ? 'text-emerald-400' : 'text-red-400'}`}>
                        {status.whatsapp.state || (status.whatsapp.error ? 'Erro' : 'Desconhecido')}
                      </p>
                    </div>
                    <div>
                      <p className="text-slate-500 mb-1">WhatsApp da agência</p>
                      <p className="text-white font-medium font-mono">{status.whatsapp.agency_phone || '— não configurado'}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 mb-1">Severidade mínima</p>
                      <p className="text-white font-medium capitalize">{status.whatsapp.min_severity}</p>
                    </div>
                  </div>

                  {status.whatsapp.error && (
                    <div className="flex items-start gap-2 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
                      <AlertCircle size={12} className="text-red-400 mt-0.5 shrink-0" />
                      <p className="text-xs text-red-400">{status.whatsapp.error}</p>
                    </div>
                  )}

                  <div className="pt-2 flex items-center gap-3">
                    <button
                      onClick={testWhatsApp}
                      disabled={testingWA || !status.whatsapp.connected || !status.whatsapp.agency_phone}
                      className="flex items-center gap-1.5 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors"
                    >
                      {testingWA ? <Loader2 size={11} className="animate-spin" /> : <Send size={11} />}
                      Enviar teste
                    </button>
                    {waResult && (
                      <span className={`flex items-center gap-1 text-xs ${waResult.ok ? 'text-emerald-400' : 'text-red-400'}`}>
                        {waResult.ok ? <CheckCircle size={12} /> : <AlertCircle size={12} />}
                        {waResult.msg}
                      </span>
                    )}
                    {!status.whatsapp.connected && status.whatsapp.configured && (
                      <span className="text-xs text-yellow-400/80">Instância não conectada. Verifique o Evolution Manager.</span>
                    )}
                  </div>
                </>
              ) : (
                <p className="text-xs text-slate-500">Configure as variáveis abaixo para ativar alertas via WhatsApp.</p>
              )}
            </div>
          </div>

          {/* ── Env vars reference ── */}
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
            <div className="px-5 py-3.5 border-b border-[#2a2f3e] flex items-center gap-2">
              <Info size={13} className="text-slate-400" />
              <p className="text-sm font-semibold text-white">Variáveis de ambiente</p>
              <span className="text-xs text-slate-600">— configurar no Railway (API service)</span>
            </div>
            <div className="px-5 py-2">
              <p className="text-xs font-medium text-slate-400 pt-2 pb-1">Email (Resend — recomendado)</p>
              <EnvRow name="RESEND_API_KEY"    description="API key do Resend"                  example="re_xxxxxxxxxxxxxxxxxxxxxxxx" />
              <EnvRow name="RESEND_FROM"       description="Endereço remetente verificado"       example="relatorios@noroia.com" />
              <EnvRow name="AGENCY_NOTIFY_EMAIL" description="Email interno da agência (alertas + relatórios retidos)" example="maico@noroia.com" />

              <p className="text-xs font-medium text-slate-400 pt-4 pb-1">WhatsApp (Evolution API)</p>
              <EnvRow name="EVOLUTION_API_URL"      description="URL da sua instância Evolution"              example="https://evolution.noroia.com" />
              <EnvRow name="EVOLUTION_API_KEY"      description="API key global do Evolution (Settings → API Key)" example="seu_global_apikey" />
              <EnvRow name="EVOLUTION_INSTANCE"     description="Nome da instância WhatsApp criada"            example="noroia-principal" />
              <EnvRow name="AGENCY_WHATSAPP"        description="Número da agência para receber alertas"       example="5511999999999" />
              <EnvRow name="EVOLUTION_MIN_SEVERITY" description="Mínima severidade para disparar WA"           example="critical  (ou warning / all)" />
            </div>
            <div className="px-5 py-3 border-t border-[#2a2f3e] flex items-center gap-1.5 text-xs text-slate-600">
              <ExternalLink size={11} />
              <a
                href="https://railway.app"
                target="_blank"
                rel="noopener noreferrer"
                className="hover:text-slate-400 transition-colors"
              >
                Abrir Railway Dashboard para editar variáveis
              </a>
            </div>
          </div>

          {/* ── What triggers notifications ── */}
          <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl px-5 py-4">
            <p className="text-sm font-semibold text-white mb-3">O que dispara notificações</p>
            <div className="space-y-2 text-xs">
              {[
                { canal: 'Email', trigger: 'Relatório semanal (seg 09:30 BRT)', sev: 'info' },
                { canal: 'Email', trigger: 'Relatório mensal (dia 1 do mês)', sev: 'info' },
                { canal: 'Email', trigger: 'Monitor diário de tracking (09:30 BRT)', sev: 'info' },
                { canal: 'Email', trigger: 'Relatório mensal retido para revisão', sev: 'warning' },
                { canal: 'WA + Email', trigger: 'Monitor detecta pedido não enviado ao Meta/Google', sev: 'critical' },
                { canal: 'WA + Email', trigger: 'Snippet com queda de eventos > 50%', sev: 'critical' },
                { canal: 'WA + Email', trigger: 'fbp abaixo de 70%', sev: 'critical' },
              ].map((row, i) => (
                <div key={i} className="flex items-center gap-3">
                  <span className={`shrink-0 w-20 text-center text-xs px-2 py-0.5 rounded font-medium ${
                    row.canal.includes('WA') ? 'bg-emerald-500/15 text-emerald-400' : 'bg-indigo-500/15 text-indigo-400'
                  }`}>
                    {row.canal}
                  </span>
                  <span className="text-slate-400 flex-1">{row.trigger}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
