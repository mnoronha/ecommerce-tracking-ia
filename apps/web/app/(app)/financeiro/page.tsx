'use client'

import { useEffect, useState, useCallback } from 'react'
import {
  TrendingUp, TrendingDown, AlertCircle, CheckCircle, Clock, Plus,
  RefreshCw, X, ChevronDown, Bell, FileText, CreditCard, Building2,
} from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Summary {
  mrr: number
  active_contracts: number
  receivables: { month_total: number; paid: number; pending: number; overdue: number }
  payables:    { month_total: number; paid: number; pending: number }
  unread_notifications: number
}

interface Contract {
  id: string; client_name: string; description: string | null
  monthly_value: number; due_day: number; start_date: string
  end_date: string | null; status: string; notes: string | null
}

interface Receivable {
  id: string; client_name: string; description: string
  amount: number; due_date: string; status: string
  paid_at: string | null; contract_id: string | null; notes: string | null
}

interface Payable {
  id: string; description: string; supplier: string | null
  amount: number; due_date: string; category: string
  status: string; paid_at: string | null; recurrent: boolean; notes: string | null
}

interface FinNotification {
  id: string; type: string; message: string; created_at: string; read_at: string | null
}

type Tab = 'resumo' | 'contratos' | 'receber' | 'pagar' | 'notificacoes'

// ── Helpers ────────────────────────────────────────────────────────────────────

const fmt = (v: number) =>
  'R$ ' + v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const fmtDate = (s: string) =>
  new Date(s + 'T12:00:00').toLocaleDateString('pt-BR')

const STATUS_LABEL: Record<string, string> = {
  active: 'Ativo', suspended: 'Suspenso', cancelled: 'Cancelado',
  pending: 'Pendente', paid: 'Pago', overdue: 'Atrasado',
}
const STATUS_COLOR: Record<string, string> = {
  active:    'bg-emerald-500/15 text-emerald-400',
  pending:   'bg-yellow-500/15 text-yellow-400',
  paid:      'bg-emerald-500/15 text-emerald-400',
  overdue:   'bg-red-500/15 text-red-400',
  suspended: 'bg-slate-500/15 text-slate-400',
  cancelled: 'bg-slate-500/15 text-slate-400',
}
const CAT_LABEL: Record<string, string> = {
  tool: 'Ferramenta', service: 'Serviço', freelancer: 'Freelancer',
  tax: 'Imposto', salary: 'Salário', other: 'Outro',
}

const thisMonth = () => new Date().toISOString().slice(0, 7)

// ── Modal genérico ─────────────────────────────────────────────────────────────

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="bg-[#1a1f2e] border border-slate-700 rounded-xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-5 border-b border-slate-700">
          <h3 className="font-semibold text-white">{title}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-white"><X size={18} /></button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-slate-400 mb-1">{label}</label>
      {children}
    </div>
  )
}

const inputCls = "w-full bg-[#0f1117] border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500"
const selectCls = inputCls

// ── KPI Card ──────────────────────────────────────────────────────────────────

function KPI({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="bg-[#1a1f2e] rounded-xl border border-slate-700/50 p-5">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className={`text-2xl font-bold ${color || 'text-white'}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function FinanceiroPage() {
  const [tab, setTab]                     = useState<Tab>('resumo')
  const [summary, setSummary]             = useState<Summary | null>(null)
  const [contracts, setContracts]         = useState<Contract[]>([])
  const [receivables, setReceivables]     = useState<Receivable[]>([])
  const [payables, setPayables]           = useState<Payable[]>([])
  const [notifications, setNotifications] = useState<FinNotification[]>([])
  const [loading, setLoading]             = useState(false)
  const [filterMonth, setFilterMonth]     = useState(thisMonth())

  // Modals
  const [showContractModal, setShowContractModal]   = useState(false)
  const [showRecvModal, setShowRecvModal]           = useState(false)
  const [showPayModal, setShowPayModal]             = useState(false)
  const [editContract, setEditContract]             = useState<Contract | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [sumRes, cRes, rRes, pRes, nRes] = await Promise.all([
        fetch(`${API_URL}/finance/summary`),
        fetch(`${API_URL}/finance/contracts`),
        fetch(`${API_URL}/finance/receivables?month=${filterMonth}`),
        fetch(`${API_URL}/finance/payables?month=${filterMonth}`),
        fetch(`${API_URL}/finance/notifications`),
      ])
      if (sumRes.ok) setSummary(await sumRes.json())
      if (cRes.ok)   setContracts(await cRes.json())
      if (rRes.ok)   setReceivables(await rRes.json())
      if (pRes.ok)   setPayables(await pRes.json())
      if (nRes.ok)   setNotifications(await nRes.json())
    } finally {
      setLoading(false)
    }
  }, [filterMonth])

  useEffect(() => { load() }, [load])

  const markRecvPaid = async (id: string) => {
    await fetch(`${API_URL}/finance/receivables/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'paid' }),
    })
    load()
  }

  const markPayPaid = async (id: string) => {
    await fetch(`${API_URL}/finance/payables/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'paid' }),
    })
    load()
  }

  const generateReceivables = async () => {
    await fetch(`${API_URL}/finance/receivables/generate?month=${filterMonth}`, { method: 'POST' })
    load()
  }

  const markAllNotifsRead = async () => {
    await fetch(`${API_URL}/finance/notifications/read-all`, { method: 'POST' })
    setNotifications([])
    setSummary(s => s ? { ...s, unread_notifications: 0 } : s)
  }

  const markNotifRead = async (id: string) => {
    await fetch(`${API_URL}/finance/notifications/${id}/read`, { method: 'POST' })
    setNotifications(n => n.filter(x => x.id !== id))
  }

  const cancelContract = async (id: string) => {
    if (!confirm('Cancelar este contrato?')) return
    await fetch(`${API_URL}/finance/contracts/${id}`, { method: 'DELETE' })
    load()
  }

  const tabs: { key: Tab; label: string; icon: React.ReactNode }[] = [
    { key: 'resumo',       label: 'Resumo',        icon: <TrendingUp size={14} /> },
    { key: 'contratos',    label: 'Contratos',      icon: <Building2 size={14} /> },
    { key: 'receber',      label: 'A Receber',      icon: <TrendingUp size={14} /> },
    { key: 'pagar',        label: 'A Pagar',        icon: <TrendingDown size={14} /> },
    { key: 'notificacoes', label: `Alertas${summary?.unread_notifications ? ` (${summary.unread_notifications})` : ''}`, icon: <Bell size={14} /> },
  ]

  return (
    <div className="min-h-screen bg-[#0f1117] text-white p-6">
      <div className="max-w-6xl mx-auto">

        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-white">Financeiro Norolabs</h1>
            <p className="text-sm text-slate-400 mt-0.5">Contratos, contas a receber e a pagar</p>
          </div>
          <button onClick={load} className="flex items-center gap-2 text-slate-400 hover:text-white text-sm">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Atualizar
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 bg-[#1a1f2e] rounded-xl p-1 mb-6 overflow-x-auto">
          {tabs.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors ${
                tab === t.key ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
              }`}
            >
              {t.icon}{t.label}
            </button>
          ))}
        </div>

        {/* ── Resumo ── */}
        {tab === 'resumo' && summary && (
          <div className="space-y-6">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <KPI label="MRR" value={fmt(summary.mrr)} sub={`${summary.active_contracts} contratos ativos`} color="text-indigo-400" />
              <KPI label="A Receber (mês)" value={fmt(summary.receivables.month_total)} sub={`${fmt(summary.receivables.paid)} já pago`} color="text-emerald-400" />
              <KPI label="A Pagar (mês)" value={fmt(summary.payables.month_total)} sub={`${fmt(summary.payables.paid)} já pago`} color="text-orange-400" />
              <KPI label="Inadimplência" value={fmt(summary.receivables.overdue)} sub="pendentes vencidos" color={summary.receivables.overdue > 0 ? 'text-red-400' : 'text-slate-400'} />
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {/* Recebíveis pendentes */}
              <div className="bg-[#1a1f2e] rounded-xl border border-slate-700/50 p-5">
                <p className="font-semibold text-white mb-3">Recebíveis pendentes</p>
                {receivables.filter(r => r.status === 'pending').length === 0
                  ? <p className="text-sm text-slate-500">Nenhum pendente no mês</p>
                  : receivables.filter(r => r.status === 'pending').map(r => (
                    <div key={r.id} className="flex items-center justify-between py-2 border-b border-slate-700/50 last:border-0">
                      <div>
                        <p className="text-sm text-white font-medium">{r.client_name}</p>
                        <p className="text-xs text-slate-500">Vence {fmtDate(r.due_date)}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-bold text-white">{fmt(r.amount)}</span>
                        <button onClick={() => markRecvPaid(r.id)} className="text-xs bg-emerald-600/20 text-emerald-400 border border-emerald-600/30 px-2 py-1 rounded hover:bg-emerald-600/30">Pago</button>
                      </div>
                    </div>
                  ))}
              </div>
              {/* Contas a pagar pendentes */}
              <div className="bg-[#1a1f2e] rounded-xl border border-slate-700/50 p-5">
                <p className="font-semibold text-white mb-3">Contas a pagar pendentes</p>
                {payables.filter(p => p.status === 'pending').length === 0
                  ? <p className="text-sm text-slate-500">Nenhuma pendente no mês</p>
                  : payables.filter(p => p.status === 'pending').map(p => (
                    <div key={p.id} className="flex items-center justify-between py-2 border-b border-slate-700/50 last:border-0">
                      <div>
                        <p className="text-sm text-white font-medium">{p.description}</p>
                        <p className="text-xs text-slate-500">{CAT_LABEL[p.category]} · Vence {fmtDate(p.due_date)}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-bold text-white">{fmt(p.amount)}</span>
                        <button onClick={() => markPayPaid(p.id)} className="text-xs bg-emerald-600/20 text-emerald-400 border border-emerald-600/30 px-2 py-1 rounded hover:bg-emerald-600/30">Pago</button>
                      </div>
                    </div>
                  ))}
              </div>
            </div>
          </div>
        )}

        {/* ── Contratos ── */}
        {tab === 'contratos' && (
          <div className="space-y-4">
            <div className="flex justify-end">
              <button onClick={() => { setEditContract(null); setShowContractModal(true) }} className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg text-sm font-medium">
                <Plus size={14} /> Novo contrato
              </button>
            </div>
            <div className="bg-[#1a1f2e] rounded-xl border border-slate-700/50 overflow-hidden">
              <table className="w-full">
                <thead><tr className="border-b border-slate-700">
                  <th className="text-left px-4 py-3 text-xs text-slate-400 font-medium">Cliente</th>
                  <th className="text-right px-4 py-3 text-xs text-slate-400 font-medium">Mensalidade</th>
                  <th className="text-center px-4 py-3 text-xs text-slate-400 font-medium">Vence dia</th>
                  <th className="text-center px-4 py-3 text-xs text-slate-400 font-medium">Status</th>
                  <th className="text-right px-4 py-3 text-xs text-slate-400 font-medium">Início</th>
                  <th className="px-4 py-3"></th>
                </tr></thead>
                <tbody>
                  {contracts.length === 0 && (
                    <tr><td colSpan={6} className="text-center py-10 text-slate-500 text-sm">Nenhum contrato cadastrado</td></tr>
                  )}
                  {contracts.map(c => (
                    <tr key={c.id} className="border-b border-slate-700/50 last:border-0 hover:bg-slate-800/30">
                      <td className="px-4 py-3">
                        <p className="text-sm font-medium text-white">{c.client_name}</p>
                        {c.description && <p className="text-xs text-slate-500">{c.description}</p>}
                      </td>
                      <td className="px-4 py-3 text-right text-sm font-bold text-white">{fmt(c.monthly_value)}</td>
                      <td className="px-4 py-3 text-center text-sm text-slate-300">dia {c.due_day}</td>
                      <td className="px-4 py-3 text-center">
                        <span className={`text-xs px-2 py-0.5 rounded font-medium ${STATUS_COLOR[c.status] || ''}`}>{STATUS_LABEL[c.status] || c.status}</span>
                      </td>
                      <td className="px-4 py-3 text-right text-xs text-slate-400">{fmtDate(c.start_date)}{c.end_date ? ` → ${fmtDate(c.end_date)}` : ''}</td>
                      <td className="px-4 py-3">
                        <div className="flex gap-2 justify-end">
                          <button onClick={() => { setEditContract(c); setShowContractModal(true) }} className="text-xs text-indigo-400 hover:text-indigo-300">Editar</button>
                          {c.status === 'active' && (
                            <button onClick={() => cancelContract(c.id)} className="text-xs text-red-400 hover:text-red-300">Cancelar</button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ── A Receber ── */}
        {tab === 'receber' && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <input type="month" value={filterMonth} onChange={e => setFilterMonth(e.target.value)} className={`${inputCls} w-40`} />
              <div className="flex gap-2">
                <button onClick={generateReceivables} className="flex items-center gap-2 bg-slate-700 hover:bg-slate-600 text-white px-3 py-2 rounded-lg text-sm">
                  <RefreshCw size={13} /> Gerar do mês
                </button>
                <button onClick={() => setShowRecvModal(true)} className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg text-sm font-medium">
                  <Plus size={14} /> Novo
                </button>
              </div>
            </div>
            <div className="bg-[#1a1f2e] rounded-xl border border-slate-700/50 overflow-hidden">
              <table className="w-full">
                <thead><tr className="border-b border-slate-700">
                  <th className="text-left px-4 py-3 text-xs text-slate-400 font-medium">Cliente</th>
                  <th className="text-left px-4 py-3 text-xs text-slate-400 font-medium">Descrição</th>
                  <th className="text-right px-4 py-3 text-xs text-slate-400 font-medium">Valor</th>
                  <th className="text-center px-4 py-3 text-xs text-slate-400 font-medium">Vencimento</th>
                  <th className="text-center px-4 py-3 text-xs text-slate-400 font-medium">Status</th>
                  <th className="px-4 py-3"></th>
                </tr></thead>
                <tbody>
                  {receivables.length === 0 && (
                    <tr><td colSpan={6} className="text-center py-10 text-slate-500 text-sm">Nenhum recebível no mês</td></tr>
                  )}
                  {receivables.map(r => (
                    <tr key={r.id} className="border-b border-slate-700/50 last:border-0 hover:bg-slate-800/30">
                      <td className="px-4 py-3 text-sm font-medium text-white">{r.client_name}</td>
                      <td className="px-4 py-3 text-sm text-slate-300">{r.description}</td>
                      <td className="px-4 py-3 text-right text-sm font-bold text-white">{fmt(r.amount)}</td>
                      <td className="px-4 py-3 text-center text-sm text-slate-300">{fmtDate(r.due_date)}</td>
                      <td className="px-4 py-3 text-center">
                        <span className={`text-xs px-2 py-0.5 rounded font-medium ${STATUS_COLOR[r.status] || ''}`}>{STATUS_LABEL[r.status] || r.status}</span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        {r.status === 'pending' || r.status === 'overdue' ? (
                          <button onClick={() => markRecvPaid(r.id)} className="text-xs bg-emerald-600/20 text-emerald-400 border border-emerald-600/30 px-2 py-1 rounded hover:bg-emerald-600/30">Marcar pago</button>
                        ) : (
                          <span className="text-xs text-slate-500">{r.paid_at ? fmtDate(r.paid_at) : ''}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ── A Pagar ── */}
        {tab === 'pagar' && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <input type="month" value={filterMonth} onChange={e => setFilterMonth(e.target.value)} className={`${inputCls} w-40`} />
              <button onClick={() => setShowPayModal(true)} className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg text-sm font-medium">
                <Plus size={14} /> Nova conta
              </button>
            </div>
            <div className="bg-[#1a1f2e] rounded-xl border border-slate-700/50 overflow-hidden">
              <table className="w-full">
                <thead><tr className="border-b border-slate-700">
                  <th className="text-left px-4 py-3 text-xs text-slate-400 font-medium">Descrição</th>
                  <th className="text-left px-4 py-3 text-xs text-slate-400 font-medium">Categoria</th>
                  <th className="text-right px-4 py-3 text-xs text-slate-400 font-medium">Valor</th>
                  <th className="text-center px-4 py-3 text-xs text-slate-400 font-medium">Vencimento</th>
                  <th className="text-center px-4 py-3 text-xs text-slate-400 font-medium">Status</th>
                  <th className="px-4 py-3"></th>
                </tr></thead>
                <tbody>
                  {payables.length === 0 && (
                    <tr><td colSpan={6} className="text-center py-10 text-slate-500 text-sm">Nenhuma conta a pagar no mês</td></tr>
                  )}
                  {payables.map(p => (
                    <tr key={p.id} className="border-b border-slate-700/50 last:border-0 hover:bg-slate-800/30">
                      <td className="px-4 py-3">
                        <p className="text-sm font-medium text-white">{p.description}</p>
                        {p.supplier && <p className="text-xs text-slate-500">{p.supplier}</p>}
                      </td>
                      <td className="px-4 py-3 text-sm text-slate-300">{CAT_LABEL[p.category] || p.category}</td>
                      <td className="px-4 py-3 text-right text-sm font-bold text-white">{fmt(p.amount)}</td>
                      <td className="px-4 py-3 text-center text-sm text-slate-300">{fmtDate(p.due_date)}</td>
                      <td className="px-4 py-3 text-center">
                        <span className={`text-xs px-2 py-0.5 rounded font-medium ${STATUS_COLOR[p.status] || ''}`}>{STATUS_LABEL[p.status] || p.status}</span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        {p.status === 'pending' ? (
                          <button onClick={() => markPayPaid(p.id)} className="text-xs bg-emerald-600/20 text-emerald-400 border border-emerald-600/30 px-2 py-1 rounded hover:bg-emerald-600/30">Marcar pago</button>
                        ) : (
                          <span className="text-xs text-slate-500">{p.paid_at ? fmtDate(p.paid_at) : ''}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ── Notificações ── */}
        {tab === 'notificacoes' && (
          <div className="space-y-3">
            {notifications.length > 0 && (
              <div className="flex justify-end">
                <button onClick={markAllNotifsRead} className="text-sm text-indigo-400 hover:text-indigo-300">Marcar todas como lidas</button>
              </div>
            )}
            {notifications.length === 0 && (
              <div className="bg-[#1a1f2e] rounded-xl border border-slate-700/50 p-10 text-center">
                <Bell size={32} className="text-slate-600 mx-auto mb-2" />
                <p className="text-slate-400">Nenhum alerta pendente</p>
              </div>
            )}
            {notifications.map(n => {
              const isOverdue = n.type === 'overdue'
              const isExpiring = n.type === 'contract_expiring'
              return (
                <div key={n.id} className={`flex items-start gap-3 bg-[#1a1f2e] rounded-xl border p-4 ${
                  isOverdue ? 'border-red-500/30' : isExpiring ? 'border-yellow-500/30' : 'border-slate-700/50'
                }`}>
                  <div className={`mt-0.5 ${isOverdue ? 'text-red-400' : isExpiring ? 'text-yellow-400' : 'text-indigo-400'}`}>
                    {isOverdue ? <AlertCircle size={16} /> : <Clock size={16} />}
                  </div>
                  <div className="flex-1">
                    <p className="text-sm text-white">{n.message}</p>
                    <p className="text-xs text-slate-500 mt-1">{new Date(n.created_at).toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo' })}</p>
                  </div>
                  <button onClick={() => markNotifRead(n.id)} className="text-slate-500 hover:text-slate-300"><X size={14} /></button>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* ── Modal: Contrato ── */}
      {showContractModal && (
        <ContractModal
          initial={editContract}
          onClose={() => { setShowContractModal(false); setEditContract(null) }}
          onSave={load}
        />
      )}

      {/* ── Modal: Recebível ── */}
      {showRecvModal && (
        <ReceivableModal contracts={contracts} onClose={() => setShowRecvModal(false)} onSave={load} />
      )}

      {/* ── Modal: Conta a Pagar ── */}
      {showPayModal && (
        <PayableModal onClose={() => setShowPayModal(false)} onSave={load} />
      )}
    </div>
  )
}

// ── Contract Modal ─────────────────────────────────────────────────────────────

function ContractModal({ initial, onClose, onSave }: { initial: Contract | null; onClose: () => void; onSave: () => void }) {
  const [form, setForm] = useState({
    client_name:   initial?.client_name   || '',
    description:   initial?.description   || '',
    monthly_value: initial?.monthly_value?.toString() || '',
    due_day:       initial?.due_day?.toString()       || '5',
    start_date:    initial?.start_date    || new Date().toISOString().slice(0, 10),
    end_date:      initial?.end_date      || '',
    status:        initial?.status        || 'active',
    notes:         initial?.notes         || '',
  })
  const [saving, setSaving] = useState(false)

  const save = async () => {
    setSaving(true)
    const body = { ...form, monthly_value: parseFloat(form.monthly_value), due_day: parseInt(form.due_day) }
    const url  = initial ? `${API_URL}/finance/contracts/${initial.id}` : `${API_URL}/finance/contracts`
    await fetch(url, { method: initial ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    setSaving(false)
    onSave()
    onClose()
  }

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  return (
    <Modal title={initial ? 'Editar contrato' : 'Novo contrato'} onClose={onClose}>
      <div className="space-y-4">
        <Field label="Cliente *"><input className={inputCls} value={form.client_name} onChange={set('client_name')} placeholder="Nome do cliente" /></Field>
        <Field label="Descrição"><input className={inputCls} value={form.description} onChange={set('description')} placeholder="Ex: Gestão de tráfego" /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Mensalidade (R$) *"><input className={inputCls} type="number" value={form.monthly_value} onChange={set('monthly_value')} placeholder="3500" /></Field>
          <Field label="Dia do vencimento *"><input className={inputCls} type="number" min={1} max={28} value={form.due_day} onChange={set('due_day')} /></Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Início *"><input className={inputCls} type="date" value={form.start_date} onChange={set('start_date')} /></Field>
          <Field label="Fim (opcional)"><input className={inputCls} type="date" value={form.end_date} onChange={set('end_date')} /></Field>
        </div>
        <Field label="Status">
          <select className={selectCls} value={form.status} onChange={set('status')}>
            <option value="active">Ativo</option>
            <option value="suspended">Suspenso</option>
            <option value="cancelled">Cancelado</option>
          </select>
        </Field>
        <Field label="Observações"><textarea className={inputCls} rows={2} value={form.notes} onChange={set('notes')} /></Field>
        <button onClick={save} disabled={saving} className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white py-2.5 rounded-lg font-medium">
          {saving ? 'Salvando...' : 'Salvar'}
        </button>
      </div>
    </Modal>
  )
}

// ── Receivable Modal ───────────────────────────────────────────────────────────

function ReceivableModal({ contracts, onClose, onSave }: { contracts: Contract[]; onClose: () => void; onSave: () => void }) {
  const nextMonth = new Date(); nextMonth.setMonth(nextMonth.getMonth() + 1)
  const [form, setForm] = useState({
    contract_id: '', client_name: '', description: 'Mensalidade',
    amount: '', due_date: '', notes: '',
  })
  const [saving, setSaving] = useState(false)

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  const selectContract = (id: string) => {
    const c = contracts.find(x => x.id === id)
    if (c) setForm(f => ({ ...f, contract_id: id, client_name: c.client_name, amount: c.monthly_value.toString() }))
    else   setForm(f => ({ ...f, contract_id: id }))
  }

  const save = async () => {
    setSaving(true)
    const body = { ...form, amount: parseFloat(form.amount), contract_id: form.contract_id || null }
    await fetch(`${API_URL}/finance/receivables`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    setSaving(false); onSave(); onClose()
  }

  return (
    <Modal title="Novo recebível" onClose={onClose}>
      <div className="space-y-4">
        <Field label="Contrato (opcional)">
          <select className={selectCls} value={form.contract_id} onChange={e => selectContract(e.target.value)}>
            <option value="">— Selecionar contrato —</option>
            {contracts.filter(c => c.status === 'active').map(c => (
              <option key={c.id} value={c.id}>{c.client_name} — {fmt(c.monthly_value)}</option>
            ))}
          </select>
        </Field>
        <Field label="Cliente *"><input className={inputCls} value={form.client_name} onChange={set('client_name')} placeholder="Nome do cliente" /></Field>
        <Field label="Descrição *"><input className={inputCls} value={form.description} onChange={set('description')} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Valor (R$) *"><input className={inputCls} type="number" value={form.amount} onChange={set('amount')} /></Field>
          <Field label="Vencimento *"><input className={inputCls} type="date" value={form.due_date} onChange={set('due_date')} /></Field>
        </div>
        <button onClick={save} disabled={saving} className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white py-2.5 rounded-lg font-medium">
          {saving ? 'Salvando...' : 'Salvar'}
        </button>
      </div>
    </Modal>
  )
}

// ── Payable Modal ──────────────────────────────────────────────────────────────

function PayableModal({ onClose, onSave }: { onClose: () => void; onSave: () => void }) {
  const [form, setForm] = useState({
    description: '', supplier: '', amount: '', due_date: '',
    category: 'tool', recurrent: false, notes: '',
  })
  const [saving, setSaving] = useState(false)

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  const save = async () => {
    setSaving(true)
    const body = { ...form, amount: parseFloat(form.amount) }
    await fetch(`${API_URL}/finance/payables`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    setSaving(false); onSave(); onClose()
  }

  return (
    <Modal title="Nova conta a pagar" onClose={onClose}>
      <div className="space-y-4">
        <Field label="Descrição *"><input className={inputCls} value={form.description} onChange={set('description')} placeholder="Ex: Google Workspace" /></Field>
        <Field label="Fornecedor"><input className={inputCls} value={form.supplier} onChange={set('supplier')} placeholder="Ex: Google" /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Valor (R$) *"><input className={inputCls} type="number" value={form.amount} onChange={set('amount')} /></Field>
          <Field label="Vencimento *"><input className={inputCls} type="date" value={form.due_date} onChange={set('due_date')} /></Field>
        </div>
        <Field label="Categoria">
          <select className={selectCls} value={form.category} onChange={set('category')}>
            {Object.entries(CAT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
        <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
          <input type="checkbox" checked={form.recurrent} onChange={e => setForm(f => ({ ...f, recurrent: e.target.checked }))} className="rounded" />
          Recorrente (mensal)
        </label>
        <button onClick={save} disabled={saving} className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white py-2.5 rounded-lg font-medium">
          {saving ? 'Salvando...' : 'Salvar'}
        </button>
      </div>
    </Modal>
  )
}
