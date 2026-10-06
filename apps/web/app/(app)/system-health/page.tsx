import { redirect } from 'next/navigation'
import Link from 'next/link'
import {
  CheckCircle2, XCircle, AlertTriangle, Clock, RefreshCw,
  Server, Activity, Cpu,
} from 'lucide-react'
import { createSupabaseServerClient } from '@/lib/supabase-server'
import { agencyApiFetch, AgencyApiError } from '@/lib/agency-api-client'

// ── Types ─────────────────────────────────────────────────────────────────────

interface SchedulerJobStatus {
  job_id:           string
  trigger:          string
  next_run_time?:   string
  last_run_status?: string
  last_run_at?:     string
}

interface JobRow {
  id:            string
  job_type:      string
  client_id?:    string
  status:        string
  started_at?:   string
  finished_at?:  string
  error?:        string
  duration_ms?:  number
}

interface SystemHealthOut {
  api_status:                     string
  database_status:                string
  scheduler_status:               string
  running_jobs:                   number
  queued_jobs:                    number
  failed_jobs_last_24h:           number
  stuck_jobs:                     JobRow[]
  scheduled_jobs:                 SchedulerJobStatus[]
  last_successful_pipeline_run?:  string
  last_pipeline_error?:           string
  checked_at:                     string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso?: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
    timeZone: 'America/Sao_Paulo',
  })
}

function fmtRelative(iso?: string | null): string {
  if (!iso) return '—'
  const ms  = Date.now() - new Date(iso).getTime()
  const min = Math.floor(ms / 60000)
  if (min < 60) return `${min}min atrás`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h}h atrás`
  return `${Math.floor(h / 24)}d atrás`
}

const SERVICE_ICON: Record<string, React.ElementType> = {
  UP:   CheckCircle2,
  DOWN: XCircle,
}
const SERVICE_CLASS: Record<string, string> = {
  UP:   'text-emerald-400',
  DOWN: 'text-red-400',
}

const JOB_STATUS_BADGE: Record<string, string> = {
  COMPLETED: 'bg-emerald-500/15 text-emerald-400',
  FAILED:    'bg-red-500/15 text-red-400',
  RUNNING:   'bg-yellow-500/15 text-yellow-400',
  QUEUED:    'bg-indigo-500/15 text-indigo-400',
  SKIPPED:   'bg-slate-500/15 text-slate-500',
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default async function SystemHealthPage() {
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) redirect('/login')

  let health: SystemHealthOut | null = null
  let errorMsg: string | null = null

  try {
    health = await agencyApiFetch<SystemHealthOut>('system/health')
  } catch (err) {
    errorMsg = err instanceof AgencyApiError
      ? `Agency API: ${err.message}`
      : 'Não foi possível carregar a saúde do sistema.'
  }

  const services = health ? [
    { label: 'API',        status: health.api_status,       icon: Server },
    { label: 'Database',   status: health.database_status,  icon: Activity },
    { label: 'Scheduler',  status: health.scheduler_status, icon: Cpu },
  ] : []

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3">
            <Activity size={16} className="text-indigo-400" />
            <h1 className="text-xl font-bold text-white">Saúde do Sistema</h1>
          </div>
          <p className="text-xs text-slate-500 mt-0.5">
            Scheduler · jobs · pipeline · fonte: Agency API
          </p>
        </div>
        <Link href="/dashboard" className="text-xs text-slate-500 hover:text-white transition-colors">
          ← Dashboard
        </Link>
      </div>

      {errorMsg && (
        <div className="rounded-xl border border-yellow-500/20 bg-yellow-500/5 p-4 text-sm text-yellow-300">
          {errorMsg}
        </div>
      )}

      {health && (
        <>
          {/* Service status */}
          <section>
            <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Serviços</h2>
            <div className="grid grid-cols-3 gap-3">
              {services.map(svc => {
                const StatusIcon = SERVICE_ICON[svc.status] || AlertTriangle
                const svcCls     = SERVICE_CLASS[svc.status] || 'text-yellow-400'
                return (
                  <div key={svc.label} className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-4 flex items-center gap-3">
                    <svc.icon size={16} className="text-slate-400 shrink-0" />
                    <div>
                      <p className="text-xs text-slate-500">{svc.label}</p>
                      <div className={`flex items-center gap-1 ${svcCls}`}>
                        <StatusIcon size={12} />
                        <span className="text-sm font-semibold">{svc.status}</span>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </section>

          {/* Job metrics */}
          <section>
            <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Jobs (últimas 24h)</h2>
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-[#1a1f2e] rounded-xl p-4">
                <p className="text-xs text-slate-500 mb-1">Executando</p>
                <p className="text-2xl font-bold text-white">{health.running_jobs}</p>
              </div>
              <div className="bg-[#1a1f2e] rounded-xl p-4">
                <p className="text-xs text-slate-500 mb-1">Na fila</p>
                <p className="text-2xl font-bold text-white">{health.queued_jobs}</p>
              </div>
              <div className={`rounded-xl p-4 ${health.failed_jobs_last_24h > 0 ? 'bg-red-500/10 border border-red-500/20' : 'bg-[#1a1f2e]'}`}>
                <p className="text-xs text-slate-500 mb-1">Falhas (24h)</p>
                <p className={`text-2xl font-bold ${health.failed_jobs_last_24h > 0 ? 'text-red-400' : 'text-white'}`}>
                  {health.failed_jobs_last_24h}
                </p>
              </div>
            </div>
            {health.last_successful_pipeline_run && (
              <p className="text-xs text-slate-500 mt-3 flex items-center gap-1">
                <CheckCircle2 size={11} className="text-emerald-400" />
                Último pipeline OK: {fmtRelative(health.last_successful_pipeline_run)}
              </p>
            )}
            {health.last_pipeline_error && (
              <p className="text-xs text-red-400 mt-1">{health.last_pipeline_error}</p>
            )}
          </section>

          {/* Stuck jobs */}
          {health.stuck_jobs.length > 0 && (
            <section>
              <h2 className="text-xs font-semibold text-red-400 uppercase tracking-wider mb-3">Jobs Travados</h2>
              <div className="bg-[#1a1f2e] border border-red-500/20 rounded-xl overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-[#2a2f3e]">
                      <th className="text-left px-4 py-2.5 text-xs text-slate-500 font-medium">Tipo</th>
                      <th className="text-left px-4 py-2.5 text-xs text-slate-500 font-medium">Cliente</th>
                      <th className="text-left px-4 py-2.5 text-xs text-slate-500 font-medium">Status</th>
                      <th className="text-left px-4 py-2.5 text-xs text-slate-500 font-medium">Iniciado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {health.stuck_jobs.map(j => (
                      <tr key={j.id} className="border-b border-[#2a2f3e] last:border-0">
                        <td className="px-4 py-2.5 text-xs text-slate-300">{j.job_type}</td>
                        <td className="px-4 py-2.5 text-xs text-slate-500">{j.client_id || '—'}</td>
                        <td className="px-4 py-2.5">
                          <span className={`text-xs px-1.5 py-0.5 rounded ${JOB_STATUS_BADGE[j.status] || 'bg-slate-500/15 text-slate-400'}`}>
                            {j.status}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-xs text-slate-500">{fmtDate(j.started_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {/* Scheduled jobs */}
          {health.scheduled_jobs.length > 0 && (
            <section>
              <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Scheduled Jobs</h2>
              <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-[#2a2f3e]">
                      <th className="text-left px-4 py-2.5 text-xs text-slate-500 font-medium">Job</th>
                      <th className="text-left px-4 py-2.5 text-xs text-slate-500 font-medium">Trigger</th>
                      <th className="text-left px-4 py-2.5 text-xs text-slate-500 font-medium">Último status</th>
                      <th className="text-left px-4 py-2.5 text-xs text-slate-500 font-medium">Próxima execução</th>
                    </tr>
                  </thead>
                  <tbody>
                    {health.scheduled_jobs.map(j => (
                      <tr key={j.job_id} className="border-b border-[#2a2f3e] last:border-0">
                        <td className="px-4 py-2.5 text-xs text-slate-300 font-mono">{j.job_id}</td>
                        <td className="px-4 py-2.5 text-xs text-slate-500 font-mono">{j.trigger}</td>
                        <td className="px-4 py-2.5">
                          {j.last_run_status ? (
                            <span className={`text-xs px-1.5 py-0.5 rounded ${JOB_STATUS_BADGE[j.last_run_status] || 'bg-slate-500/15 text-slate-400'}`}>
                              {j.last_run_status}
                            </span>
                          ) : <span className="text-xs text-slate-600">—</span>}
                        </td>
                        <td className="px-4 py-2.5 text-xs text-slate-500 flex items-center gap-1">
                          <Clock size={10} />
                          {fmtDate(j.next_run_time)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-slate-600 mt-2 flex items-center gap-1">
                <RefreshCw size={9} />
                verificado às {fmtDate(health.checked_at)}
              </p>
            </section>
          )}
        </>
      )}
    </div>
  )
}
