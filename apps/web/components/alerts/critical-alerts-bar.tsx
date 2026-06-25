'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { AlertCircle, X, ChevronDown, ChevronUp, Clock, BellOff, Loader2 } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://ecommerce-tracking-ia-production.up.railway.app'
const POLL_MS = 30_000

type Alert = {
  id: string
  severity: 'critical' | 'warning' | 'info'
  title: string
  message: string
  data: Record<string, unknown>
  created_at: string
  resolved_at: string | null
  silenced_until: string | null
  alert_rule_id: string | null
}

function fmtRelative(iso: string) {
  const diff = Date.now() - new Date(iso).getTime()
  const min  = Math.floor(diff / 60_000)
  if (min < 2)  return 'agora'
  if (min < 60) return `há ${min}min`
  const h = Math.floor(min / 60)
  if (h < 24)   return `há ${h}h`
  return `há ${Math.floor(h / 24)}d`
}

function isSilenced(a: Alert) {
  return !!a.silenced_until && new Date(a.silenced_until) > new Date()
}

// ── Single alert row inside the bar ──────────────────────────────────────────

function AlertRow({
  alert: a,
  onResolve,
  onSilence,
}: {
  alert: Alert
  onResolve: () => Promise<void>
  onSilence: () => Promise<void>
}) {
  const [resolving, setResolving] = useState(false)
  const [silencing, setSilencing] = useState(false)

  async function handleResolve() {
    setResolving(true)
    await onResolve()
    setResolving(false)
  }

  async function handleSilence() {
    setSilencing(true)
    await onSilence()
    setSilencing(false)
  }

  return (
    <div className="flex items-start gap-3 py-2 border-b border-red-500/20 last:border-0">
      <AlertCircle size={14} className="text-red-300 shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-white leading-tight">{a.title}</p>
        <p className="text-xs text-red-200/70 mt-0.5 leading-snug line-clamp-2">{a.message}</p>
        <span className="flex items-center gap-1 text-xs text-red-300/50 mt-1">
          <Clock size={9} />
          {fmtRelative(a.created_at)}
        </span>
      </div>
      <div className="flex items-center gap-1.5 shrink-0">
        <button
          onClick={handleSilence}
          disabled={silencing}
          title="Silenciar por 24h"
          className="flex items-center gap-1 text-xs px-2 py-1 rounded bg-red-500/15 text-red-200 hover:bg-red-500/30 transition-colors disabled:opacity-50"
        >
          {silencing ? <Loader2 size={10} className="animate-spin" /> : <BellOff size={10} />}
          <span className="hidden sm:inline">24h</span>
        </button>
        <button
          onClick={handleResolve}
          disabled={resolving}
          title="Marcar como resolvido"
          className="flex items-center gap-1 text-xs px-2 py-1 rounded bg-red-500/15 text-red-200 hover:bg-emerald-500/25 hover:text-emerald-300 transition-colors disabled:opacity-50"
        >
          {resolving ? <Loader2 size={10} className="animate-spin" /> : <X size={10} />}
          <span className="hidden sm:inline">Resolver</span>
        </button>
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function CriticalAlertsBar({ clientId }: { clientId: string }) {
  const [alerts,    setAlerts]    = useState<Alert[]>([])
  const [expanded,  setExpanded]  = useState(false)
  const [visible,   setVisible]   = useState(false)  // drives fade-in/out
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async (silent = false) => {
    try {
      const res = await fetch(`${API_URL}/alerts/${clientId}?include_resolved=false&limit=50`)
      if (!res.ok) return
      const data = await res.json()
      const critical = ((data.alerts || []) as Alert[]).filter(
        a => a.severity === 'critical' && !a.resolved_at && !isSilenced(a)
      )
      setAlerts(critical)
      if (!silent) setVisible(critical.length > 0)
    } catch (_) {}
  }, [clientId])

  // Initial load
  useEffect(() => { load() }, [load])

  // Fade-in when alerts arrive
  useEffect(() => { setVisible(alerts.length > 0) }, [alerts.length])

  // Polling
  useEffect(() => {
    timerRef.current = setTimeout(() => load(true), POLL_MS)
    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [alerts, load])

  async function resolveAlert(id: string) {
    await fetch(`${API_URL}/alerts/${id}/resolve`, { method: 'POST' })
    setAlerts(prev => prev.filter(a => a.id !== id))
  }

  async function silenceAlert(id: string) {
    await fetch(`${API_URL}/alerts/${id}/silence`, { method: 'POST' })
    setAlerts(prev => prev.filter(a => a.id !== id))
  }

  if (alerts.length === 0) return null

  const shown = expanded ? alerts : alerts.slice(0, 1)
  const extra = alerts.length - 1

  return (
    <div
      className={`sticky top-0 z-50 transition-all duration-300 ${
        visible ? 'opacity-100 translate-y-0' : 'opacity-0 -translate-y-2 pointer-events-none'
      }`}
    >
      {/* Gradient red banner */}
      <div className="bg-gradient-to-r from-red-900/95 via-red-800/95 to-red-900/95 border-b border-red-500/40 backdrop-blur-sm shadow-lg shadow-red-900/30">
        <div className="px-4 py-2">

          {/* Header row */}
          <div className="flex items-center gap-2 mb-1">
            <span className="flex items-center gap-1.5 text-xs font-bold text-red-200 uppercase tracking-wide">
              <AlertCircle size={13} className="animate-pulse" />
              {alerts.length} alerta{alerts.length > 1 ? 's' : ''} crítico{alerts.length > 1 ? 's' : ''}
            </span>

            {extra > 0 && (
              <button
                onClick={() => setExpanded(v => !v)}
                className="flex items-center gap-0.5 text-xs text-red-300/70 hover:text-red-100 transition-colors ml-auto"
              >
                {expanded
                  ? <><ChevronUp size={12} /> Recolher</>
                  : <><ChevronDown size={12} /> +{extra} outro{extra > 1 ? 's' : ''}</>
                }
              </button>
            )}
          </div>

          {/* Alert rows */}
          <div>
            {shown.map(a => (
              <AlertRow
                key={a.id}
                alert={a}
                onResolve={() => resolveAlert(a.id)}
                onSilence={() => silenceAlert(a.id)}
              />
            ))}
          </div>

        </div>
      </div>
    </div>
  )
}
