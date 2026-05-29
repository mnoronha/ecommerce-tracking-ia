'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { supabase } from '@/lib/supabase'
import { ArrowLeft, UserPlus, Trash2, Loader2, Mail, ShieldCheck, Eye } from 'lucide-react'

interface Member {
  id: string
  user_id: string
  role: 'admin' | 'viewer'
  created_at: string
  email?: string
}

const INPUT = 'w-full bg-[#0f1117] border border-[#2a2f3e] rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-600 outline-none transition-colors focus:border-indigo-500'

export default function ClientUsersPage() {
  const params   = useParams()
  const clientId = params.clientId as string

  const [members,   setMembers]   = useState<Member[]>([])
  const [clientUUID, setClientUUID] = useState<string | null>(null)
  const [loading,   setLoading]   = useState(true)
  const [email,     setEmail]     = useState('')
  const [role,      setRole]      = useState<'admin' | 'viewer'>('viewer')
  const [inviting,  setInviting]  = useState(false)
  const [error,     setError]     = useState('')
  const [success,   setSuccess]   = useState('')

  const load = useCallback(async () => {
    // supabase singleton from @/lib/supabase

    const { data: clientData } = await supabase
      .from('clients').select('id').eq('pixel_id', clientId).single()
    if (!clientData) { setLoading(false); return }
    setClientUUID(clientData.id)

    const { data } = await supabase
      .from('client_members')
      .select('id, user_id, role, created_at')
      .eq('client_id', clientData.id)
      .order('created_at')

    setMembers((data || []) as Member[])
    setLoading(false)
  }, [clientId])

  useEffect(() => { load() }, [load])

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault()
    if (!clientUUID) return
    setInviting(true)
    setError('')
    setSuccess('')

    // supabase singleton from @/lib/supabase

    // Check if user exists in auth
    const { data: { user: currentUser } } = await supabase.auth.getUser()
    if (!currentUser) { setError('Sessão expirada.'); setInviting(false); return }

    // Invite via Supabase Admin — uses service role (must be done server-side)
    // Here we call our own API route to avoid exposing service role key
    const res = await fetch('/api/invite-user', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, role, clientId: clientUUID }),
    })

    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      setError(body.error || 'Erro ao convidar usuário.')
    } else {
      setSuccess(`Convite enviado para ${email}`)
      setEmail('')
      load()
    }
    setInviting(false)
  }

  async function handleRemove(memberId: string) {
    if (!confirm('Remover acesso deste usuário?')) return
    // supabase singleton from @/lib/supabase
    await supabase.from('client_members').delete().eq('id', memberId)
    load()
  }

  return (
    <div className="p-6 max-w-2xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <Link href={`/clients/${clientId}/settings`} className="text-slate-500 hover:text-white transition-colors">
          <ArrowLeft size={18} />
        </Link>
        <div>
          <h1 className="text-xl font-bold text-white">Usuários — {clientId}</h1>
          <p className="text-xs text-slate-500 mt-0.5">Gerencie quem tem acesso ao dashboard deste cliente</p>
        </div>
      </div>

      {/* Invite form */}
      <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-5 mb-6">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-4">Convidar usuário</h3>
        <form onSubmit={handleInvite} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5">Email</label>
            <input type="email" required value={email} onChange={e => setEmail(e.target.value)}
              placeholder="usuario@empresa.com" className={INPUT} />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5">Permissão</label>
            <select value={role} onChange={e => setRole(e.target.value as 'admin' | 'viewer')} className={INPUT}>
              <option value="viewer">Visualizador — apenas leitura</option>
              <option value="admin">Admin — pode editar configurações</option>
            </select>
          </div>
          {error   && <p className="text-xs text-red-400">{error}</p>}
          {success && <p className="text-xs text-emerald-400">{success}</p>}
          <button type="submit" disabled={inviting}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors">
            {inviting ? <Loader2 size={14} className="animate-spin" /> : <UserPlus size={14} />}
            {inviting ? 'Enviando...' : 'Enviar convite'}
          </button>
        </form>
      </div>

      {/* Members list */}
      <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-[#2a2f3e]">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
            Usuários com acesso ({members.length})
          </h3>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-10">
            <Loader2 size={18} className="animate-spin text-slate-500" />
          </div>
        ) : members.length === 0 ? (
          <div className="text-center py-10 text-slate-500 text-sm">
            <Mail size={28} className="mx-auto mb-2 opacity-30" />
            Nenhum usuário com acesso ainda.
          </div>
        ) : (
          <ul className="divide-y divide-[#2a2f3e]">
            {members.map(m => (
              <li key={m.id} className="flex items-center justify-between px-5 py-3">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-full bg-indigo-600/20 flex items-center justify-center">
                    {m.role === 'admin' ? <ShieldCheck size={14} className="text-indigo-400" /> : <Eye size={14} className="text-slate-400" />}
                  </div>
                  <div>
                    <p className="text-sm text-white font-medium">{m.email || m.user_id.slice(0, 8) + '...'}</p>
                    <p className="text-xs text-slate-500 capitalize">{m.role === 'admin' ? 'Admin' : 'Visualizador'}</p>
                  </div>
                </div>
                <button onClick={() => handleRemove(m.id)}
                  className="text-slate-600 hover:text-red-400 transition-colors p-1.5 rounded">
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
