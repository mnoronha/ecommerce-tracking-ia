'use client'

import { useEffect, useState } from 'react'
import {
  Key, Plus, Trash2, RefreshCw, Copy, CheckCircle,
  Loader2, AlertTriangle, Eye, EyeOff, Shield,
} from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ApiKey {
  id: string
  name: string
  key_prefix: string
  permissions: string[]
  scope_type: string
  scope_client_id: string | null
  is_active: boolean
  created_at: string
  last_used_at: string | null
  requests_count: number
}

interface NewKeyResult {
  id: string
  name: string
  key: string  // shown once
  permissions: string[]
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const PERM_LABELS: Record<string, string> = {
  read:         'Somente leitura',
  write:        'Leitura e escrita',
  full_access:  'Acesso total (inclui gestão de keys)',
}

const PERM_COLORS: Record<string, string> = {
  read:         'bg-slate-700 text-slate-300',
  write:        'bg-blue-900/40 text-blue-300',
  full_access:  'bg-indigo-900/40 text-indigo-300',
}

function fmtDate(s: string | null) {
  if (!s) return 'nunca'
  return new Date(s).toLocaleDateString('pt-BR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ApiKeysPage() {
  const [keys, setKeys]         = useState<ApiKey[]>([])
  const [loading, setLoading]   = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [masterKey, setMasterKey] = useState('')

  // New key form
  const [form, setForm] = useState({
    name: '', permissions: ['read'], scope_type: 'all', scope_client_id: '',
  })
  const [saving, setSaving]     = useState(false)
  const [newKey, setNewKey]     = useState<NewKeyResult | null>(null)
  const [copied, setCopied]     = useState(false)
  const [showKey, setShowKey]   = useState(false)

  // For actions that need the master key
  const [actionTarget, setActionTarget] = useState<string | null>(null)
  const [actionType, setActionType]     = useState<'revoke' | 'rotate' | null>(null)
  const [actionDone, setActionDone]     = useState(false)

  function load() {
    if (!masterKey) return
    setLoading(true)
    fetch(`${API}/api/v1/api-keys`, {
      headers: { Authorization: `Bearer ${masterKey}` },
    })
      .then(r => r.json())
      .then(d => setKeys(d.data || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [masterKey])

  async function createKey() {
    if (!form.name || !masterKey) return
    setSaving(true)
    try {
      const res = await fetch(`${API}/api/v1/api-keys`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${masterKey}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name: form.name,
          permissions: form.permissions,
          scope_type: form.scope_type,
          scope_client_id: form.scope_client_id || null,
        }),
      })
      const d = await res.json()
      if (d.data) {
        setNewKey(d.data)
        setShowForm(false)
        setForm({ name: '', permissions: ['read'], scope_type: 'all', scope_client_id: '' })
        load()
      }
    } finally {
      setSaving(false)
    }
  }

  async function revokeKey(keyId: string) {
    if (!masterKey) return
    await fetch(`${API}/api/v1/api-keys/${keyId}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${masterKey}` },
    })
    setActionDone(true)
    setTimeout(() => { setActionTarget(null); setActionDone(false); load() }, 1000)
  }

  async function rotateKey(keyId: string) {
    if (!masterKey) return
    const res = await fetch(`${API}/api/v1/api-keys/${keyId}/rotate`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${masterKey}` },
    })
    const d = await res.json()
    if (d.data) {
      setNewKey(d.data)
      setActionTarget(null)
      load()
    }
  }

  function copyKey(k: string) {
    navigator.clipboard.writeText(k)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  // ── Master key prompt ─────────────────────────────────────────────────────

  if (!masterKey) return (
    <div className="p-8 max-w-lg mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <Key size={22} className="text-indigo-400" />
        <h1 className="text-xl font-bold text-white">Gestão de API Keys</h1>
      </div>
      <div className="bg-[#1a1f2e] rounded-xl p-6 space-y-4">
        <p className="text-sm text-slate-400">
          Para gerenciar as API keys você precisa de uma key com permissão <code className="text-indigo-300">full_access</code>.
        </p>
        <div>
          <label className="text-xs text-slate-400 mb-1 block">Sua API key de admin</label>
          <input
            type="password"
            placeholder="nrp_sk_..."
            className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
            onKeyDown={e => {
              if (e.key === 'Enter') setMasterKey((e.target as HTMLInputElement).value)
            }}
          />
        </div>
        <button
          onClick={() => {
            const val = (document.querySelector('input[type="password"]') as HTMLInputElement)?.value
            if (val) setMasterKey(val)
          }}
          className="w-full bg-indigo-600 hover:bg-indigo-700 text-white py-2 rounded-lg text-sm"
        >
          Entrar
        </button>
      </div>
    </div>
  )

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Key size={22} className="text-indigo-400" />
          <h1 className="text-xl font-bold text-white">API Keys</h1>
        </div>
        <button
          onClick={() => setShowForm(true)}
          className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm transition-colors"
        >
          <Plus size={14} /> Nova API Key
        </button>
      </div>

      {/* New key just created — show full key once */}
      {newKey && (
        <div className="bg-emerald-900/20 border border-emerald-700/50 rounded-xl p-5 space-y-3">
          <div className="flex items-center gap-2">
            <CheckCircle size={16} className="text-emerald-400" />
            <p className="text-emerald-300 font-semibold">Key criada: {newKey.name}</p>
          </div>
          <p className="text-xs text-slate-400">
            Copie agora — essa key não será exibida novamente.
          </p>
          <div className="flex items-center gap-2">
            <div className="flex-1 bg-[#0f1117] rounded-lg px-3 py-2 font-mono text-sm text-emerald-300 overflow-x-auto">
              {showKey ? newKey.key : '•'.repeat(40)}
            </div>
            <button onClick={() => setShowKey(v => !v)} className="text-slate-400 hover:text-white p-2">
              {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
            <button onClick={() => copyKey(newKey.key)} className="text-slate-400 hover:text-emerald-400 p-2">
              {copied ? <CheckCircle size={14} className="text-emerald-400" /> : <Copy size={14} />}
            </button>
          </div>
          <button onClick={() => setNewKey(null)} className="text-xs text-slate-500 hover:text-white">
            Fechar
          </button>
        </div>
      )}

      {/* Create form */}
      {showForm && (
        <div className="bg-[#1a1f2e] rounded-xl p-5 border border-indigo-600/30 space-y-4">
          <h3 className="font-medium text-white">Nova API Key</h3>
          <div className="grid grid-cols-2 gap-4">
            <div className="col-span-2">
              <label className="text-xs text-slate-400 mb-1 block">Nome *</label>
              <input
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="Ex: OpenClaw Production"
                className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
              />
            </div>
            <div>
              <label className="text-xs text-slate-400 mb-1 block">Permissões</label>
              <select
                value={form.permissions[0]}
                onChange={e => setForm(f => ({ ...f, permissions: [e.target.value] }))}
                className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
              >
                <option value="read">Somente leitura</option>
                <option value="write">Leitura e escrita</option>
                <option value="full_access">Acesso total</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-slate-400 mb-1 block">Escopo</label>
              <select
                value={form.scope_type}
                onChange={e => setForm(f => ({ ...f, scope_type: e.target.value }))}
                className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white"
              >
                <option value="all">Todos os clientes</option>
                <option value="client">Cliente específico</option>
              </select>
            </div>
            {form.scope_type === 'client' && (
              <div className="col-span-2">
                <label className="text-xs text-slate-400 mb-1 block">UUID do cliente</label>
                <input
                  value={form.scope_client_id}
                  onChange={e => setForm(f => ({ ...f, scope_client_id: e.target.value }))}
                  placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                  className="w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2 text-sm text-white font-mono"
                />
              </div>
            )}
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={() => setShowForm(false)} className="text-xs text-slate-400 hover:text-white px-3 py-1.5">
              Cancelar
            </button>
            <button
              onClick={createKey}
              disabled={saving || !form.name}
              className="text-xs bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-1.5 rounded-md disabled:opacity-50"
            >
              {saving ? 'Criando…' : 'Criar Key'}
            </button>
          </div>
        </div>
      )}

      {/* Keys list */}
      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 size={24} className="animate-spin text-indigo-400" />
        </div>
      ) : keys.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <Key size={32} className="mx-auto mb-3 opacity-40" />
          <p>Nenhuma API key criada ainda.</p>
        </div>
      ) : (
        <div className="bg-[#1a1f2e] rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#2a2f3e]">
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Nome</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Permissão</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Prefix</th>
                <th className="text-right px-4 py-3 text-slate-400 font-medium">Último uso</th>
                <th className="text-right px-4 py-3 text-slate-400 font-medium">Requests</th>
                <th className="text-right px-4 py-3 text-slate-400 font-medium">Ações</th>
              </tr>
            </thead>
            <tbody>
              {keys.map(k => (
                <tr key={k.id} className="border-b border-[#1f2433] hover:bg-[#1f2433] transition-colors">
                  <td className="px-4 py-3">
                    <p className="text-white font-medium">{k.name}</p>
                    <p className="text-xs text-slate-500">
                      {k.scope_type === 'client' ? `Cliente específico` : 'Todos os clientes'}
                      {' · '}Criada {fmtDate(k.created_at)}
                    </p>
                  </td>
                  <td className="px-4 py-3">
                    {k.permissions.map(p => (
                      <span key={p} className={`text-xs px-2 py-0.5 rounded-full mr-1 ${PERM_COLORS[p] || 'bg-slate-700 text-slate-300'}`}>
                        {p}
                      </span>
                    ))}
                  </td>
                  <td className="px-4 py-3">
                    <code className="text-xs text-slate-400 bg-[#0f1117] px-2 py-0.5 rounded">
                      {k.key_prefix}…
                    </code>
                  </td>
                  <td className="px-4 py-3 text-right text-xs text-slate-400">
                    {fmtDate(k.last_used_at)}
                  </td>
                  <td className="px-4 py-3 text-right text-xs text-slate-400">
                    {(k.requests_count || 0).toLocaleString('pt-BR')}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1 justify-end">
                      <button
                        onClick={() => { setActionTarget(k.id); setActionType('rotate') }}
                        className="flex items-center gap-1 text-xs text-slate-400 hover:text-indigo-400 px-2 py-1 rounded hover:bg-[#2a2f3e] transition-colors"
                        title="Rotacionar"
                      >
                        <RefreshCw size={12} />
                      </button>
                      <button
                        onClick={() => { setActionTarget(k.id); setActionType('revoke') }}
                        className="flex items-center gap-1 text-xs text-slate-400 hover:text-red-400 px-2 py-1 rounded hover:bg-[#2a2f3e] transition-colors"
                        title="Revogar"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Confirm action modal */}
      {actionTarget && actionType && !actionDone && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-[#1a1f2e] rounded-xl p-6 max-w-sm w-full mx-4 space-y-4">
            {actionType === 'revoke' ? (
              <>
                <div className="flex items-center gap-3">
                  <AlertTriangle size={20} className="text-red-400" />
                  <h3 className="font-semibold text-white">Revogar key?</h3>
                </div>
                <p className="text-sm text-slate-400">
                  A key será desativada imediatamente. Integrações usando ela deixarão de funcionar.
                </p>
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setActionTarget(null)} className="text-sm text-slate-400 hover:text-white px-3 py-1.5">
                    Cancelar
                  </button>
                  <button
                    onClick={() => revokeKey(actionTarget)}
                    className="text-sm bg-red-600 hover:bg-red-700 text-white px-4 py-1.5 rounded-md"
                  >
                    Revogar
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="flex items-center gap-3">
                  <RefreshCw size={20} className="text-indigo-400" />
                  <h3 className="font-semibold text-white">Rotacionar key?</h3>
                </div>
                <p className="text-sm text-slate-400">
                  Uma nova key será criada com as mesmas permissões. A key antiga será revogada.
                </p>
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setActionTarget(null)} className="text-sm text-slate-400 hover:text-white px-3 py-1.5">
                    Cancelar
                  </button>
                  <button
                    onClick={() => rotateKey(actionTarget)}
                    className="text-sm bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-1.5 rounded-md"
                  >
                    Rotacionar
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* API reference quick card */}
      <div className="bg-[#1a1f2e] rounded-xl p-5">
        <div className="flex items-center gap-2 mb-3">
          <Shield size={14} className="text-slate-400" />
          <h3 className="text-sm font-semibold text-white">Como usar</h3>
        </div>
        <div className="space-y-2 text-xs font-mono">
          <div className="bg-[#0f1117] rounded-lg p-3 text-slate-300">
            curl {API}/api/v1/me \<br />
            &nbsp;&nbsp;-H "Authorization: Bearer nrp_sk_..."
          </div>
          <div className="bg-[#0f1117] rounded-lg p-3 text-slate-300">
            GET /api/v1/clients &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;→ Lista clientes<br />
            GET /api/v1/clients/:id/performance/daily<br />
            GET /api/v1/alerts<br />
            <a
              href={`${API}/api/v1/docs`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-indigo-400 hover:underline mt-1 block not-italic normal-case"
            >
              Ver docs completas → /api/v1/docs
            </a>
          </div>
        </div>
      </div>
    </div>
  )
}
