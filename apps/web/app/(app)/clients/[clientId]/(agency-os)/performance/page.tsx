import { AlertTriangle, ArrowLeft, CheckCircle2, Clock, Info, Lock, ShieldOff, TrendingUp, XCircle } from 'lucide-react'
import Link from 'next/link'
import {
  getLatestWeeklyReport,
  getWeeklyReport,
  getLatestMonthlyReport,
  getMonthlyReport,
  isMetric,
  ReportError,
  type ReportErrorCode,
  type ChannelMetrics,
  type EcommerceBusiness,
  type LeadGenBusiness,
  type Metric,
  type MetricStatus,
  type ReportContractV1,
} from '@/lib/agency-os'

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatValue(value: number | null, fmt?: string): string {
  if (value === null) return '—'
  if (fmt === 'currency') return `R$ ${value.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  if (fmt === 'percent')  return `${value.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`
  if (fmt === 'roas')     return `${value.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}x`
  return value.toLocaleString('pt-BR', { maximumFractionDigits: 2 })
}


// ── StatusBadge ───────────────────────────────────────────────────────────────

const STATUS_BADGE: Record<MetricStatus, { label: string; className: string; Icon: React.ElementType }> = {
  available:        { label: 'Disponível',          className: 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30', Icon: CheckCircle2 },
  access_missing:   { label: 'Acesso ausente',       className: 'bg-yellow-500/10  text-yellow-400  border border-yellow-500/30',  Icon: Lock         },
  permission_denied:{ label: 'Sem permissão',        className: 'bg-orange-500/10  text-orange-400  border border-orange-500/30',  Icon: ShieldOff    },
  not_contracted:   { label: 'Não contratado',       className: 'bg-slate-500/10   text-slate-500   border border-slate-500/30',   Icon: XCircle      },
  no_data:          { label: 'Sem dados',            className: 'bg-slate-500/10   text-slate-500   border border-slate-500/30',   Icon: Info         },
  unknown:          { label: 'Indisponível',         className: 'bg-slate-500/10   text-slate-500   border border-slate-500/30',   Icon: Info         },
  missing:          { label: 'Dado ausente', className: 'text-yellow-400', Icon: Info },
  stale:            { label: 'Desatualizado', className: 'text-yellow-400', Icon: Clock },
  unresolved_mapping: { label: 'Conversão não reconciliada', className: 'text-yellow-400', Icon: Lock },
}

function StatusBadge({ status }: { status: MetricStatus }) {
  const s = STATUS_BADGE[status] ?? STATUS_BADGE.unknown
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium ${s.className}`}>
      <s.Icon size={11} />
      {s.label}
    </span>
  )
}

// ── MetricCell ────────────────────────────────────────────────────────────────
// Rules: null ≠ 0. Only render value when status=available AND value≠null.
// value=0 with status=available IS a legitimate zero.

interface MetricCellProps {
  metric?: Metric
  fmt?: string
  label: string
}

function MetricCell({ metric, fmt, label }: MetricCellProps) {
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 flex flex-col gap-1">
      <span className="text-xs text-slate-500 uppercase tracking-wide">{label}</span>
      {!metric ? (
        <StatusBadge status="unknown" />
      ) : metric.status !== 'available' || metric.value === null ? (
        <StatusBadge status={metric.status} />
      ) : (
        <span className="text-xl font-semibold text-white">
          {formatValue(metric.value as number, fmt)}
        </span>
      )}
    </div>
  )
}

// ── SectionCard ───────────────────────────────────────────────────────────────

function SectionCard({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="mb-6">
      <div className="mb-3 flex items-baseline gap-3">
        <h2 className="text-sm font-semibold text-white uppercase tracking-wider">{title}</h2>
        {note && <span className="text-xs text-slate-500 italic">{note}</span>}
      </div>
      {children}
    </section>
  )
}

// ── Generic channel section ───────────────────────────────────────────────────
// Renders any object whose leaf values look like Metrics.

const KNOWN_FORMATS: Record<string, string> = {
  spend: 'currency', cost: 'currency', revenue: 'currency', value: 'currency',
  roas: 'roas', mer: 'roas', platform_roas: 'roas',
  platform_attributed_value: 'currency', cpc: 'currency', cpa: 'currency',
  ctr: 'percent', conversion_rate: 'percent',
}

const KNOWN_LABELS: Record<string, string> = {
  spend: 'Investimento', impressions: 'Impressões', clicks: 'Cliques',
  ctr: 'CTR', cpc: 'CPC', conversions: 'Conversões', cpa: 'CPA',
  roas: 'ROAS', revenue: 'Receita', value: 'Receita',
  leads: 'Leads', qualified_leads: 'Leads Qualif.', appointments: 'Agendamentos',
  cost_per_lead: 'CPL', cost_per_business_lead: 'CPL Negócio',
  orders: 'Pedidos', average_order_value: 'Ticket Médio', mer: 'MER',
  total_spend: 'Investimento Total',
  platform_attributed_conversions: 'Conversões atribuídas pela plataforma',
  platform_attributed_leads: 'Leads atribuídos pela plataforma',
  platform_attributed_value: 'Valor atribuído pela plataforma',
  platform_roas: 'ROAS atribuído pela plataforma',
  sessions: 'Sessões', users: 'Usuários', bounce_rate: 'Bounce',
  avg_session_duration: 'Duração Média',
}

function toLabel(key: string) {
  return KNOWN_LABELS[key] ?? key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function ChannelGrid({ data }: { data: ChannelMetrics }) {
  const entries = Object.entries(data).filter((e): e is [string, Metric] => isMetric(e[1]))
  if (entries.length === 0) return <p className="text-xs text-slate-600">Sem métricas disponíveis nesta seção.</p>

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
      {entries.map(([key, m]) => (
        <MetricCell
          key={key}
          label={toLabel(key)}
          metric={m}
          fmt={KNOWN_FORMATS[key]}
        />
      ))}
    </div>
  )
}

// ── Business sections (model-aware) ──────────────────────────────────────────

function EcommerceBusinessSection({ business }: { business: EcommerceBusiness }) {
  return (
    <SectionCard title="Negócio" note="Business Truth">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
        <MetricCell label="Receita"      metric={business.revenue}              fmt="currency" />
        <MetricCell label="Pedidos"      metric={business.orders} />
        <MetricCell label="Ticket Médio" metric={business.average_order_value}  fmt="currency" />
        <MetricCell label="MER"          metric={business.mer}                  fmt="roas" />
      </div>
    </SectionCard>
  )
}

function LeadGenBusinessSection({ business }: { business: LeadGenBusiness }) {
  return (
    <SectionCard title="Negócio" note="Business Truth">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
        <MetricCell label="Leads"         metric={business.leads} />
        <MetricCell label="Leads Qualif." metric={business.qualified_leads} />
        <MetricCell label="Agendamentos"  metric={business.appointments} />
        <MetricCell label="CPL Negócio"   metric={business.cost_per_business_lead} fmt="currency" />
      </div>
    </SectionCard>
  )
}

// ── Recommendations ───────────────────────────────────────────────────────────

function RecommendationsSection({ data }: { data: unknown }) {
  const items: unknown[] = Array.isArray(data) ? data : (data ? [data] : [])
  if (items.length === 0) return null
  return (
    <SectionCard title="Recomendações">
      <ul className="space-y-2">
        {items.map((item, i) => (
          <li key={i} className="bg-[#1a1f2e] rounded-xl p-4 text-sm text-slate-300">
            {typeof item === 'string' ? item : JSON.stringify(item)}
          </li>
        ))}
      </ul>
    </SectionCard>
  )
}

// ── Collapsible sections (no JS needed — native <details>) ───────────────────

function CollapsibleSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <details className="mb-6 group">
      <summary className="cursor-pointer flex items-center gap-2 text-sm font-semibold text-white uppercase tracking-wider mb-3 select-none list-none">
        <TrendingUp size={14} className="text-indigo-400" />
        {title}
        <span className="text-slate-600 text-xs font-normal normal-case tracking-normal ml-1 group-open:hidden">▸ expandir</span>
        <span className="text-slate-600 text-xs font-normal normal-case tracking-normal ml-1 hidden group-open:inline">▾ recolher</span>
      </summary>
      {children}
    </details>
  )
}

// ── Error state ───────────────────────────────────────────────────────────────

const ERROR_COPY: Record<ReportErrorCode, { title: string; body: string }> = {
  not_found:   { title: 'Relatório não encontrado', body: 'Nenhum relatório disponível para este cliente ou período.' },
  unavailable: { title: 'Serviço indisponível',     body: 'Não foi possível recuperar o relatório. Tente novamente em instantes.' },
}

function ErrorState({ code, clientId }: { code: ReportErrorCode; clientId: string }) {
  const copy = ERROR_COPY[code] ?? ERROR_COPY.unavailable
  return (
    <div className="p-8 max-w-2xl">
      <Link href={`/clients/${clientId}/dashboard`} className="inline-flex items-center gap-1.5 text-slate-500 hover:text-white text-xs mb-8 transition-colors">
        <ArrowLeft size={12} /> Voltar ao dashboard
      </Link>
      <nav className="flex gap-4 mb-6 text-sm text-indigo-400" aria-label="Tipo de relatório">
        <Link href={`/clients/${clientId}/performance?type=weekly`}>Semanal</Link>
        <Link href={`/clients/${clientId}/performance?type=monthly`}>Mensal</Link>
      </nav>
      <div className="rounded-xl border border-red-500/20 bg-red-500/5 p-6">
        <div className="flex items-center gap-2 mb-3">
          <AlertTriangle size={16} className="text-red-400" />
          <span className="font-semibold text-red-300">{copy.title}</span>
        </div>
        <p className="text-sm text-slate-400">{copy.body}</p>
      </div>
    </div>
  )
}

// ── Contract view ─────────────────────────────────────────────────────────────

function JsonDump({ data }: { data: unknown }) {
  return (
    <pre className="text-xs text-slate-400 bg-[#0f1117] rounded-xl p-4 overflow-auto max-h-64 whitespace-pre-wrap">
      {typeof data === 'string' ? data : JSON.stringify(data, null, 2)}
    </pre>
  )
}

function ContractView({ contract, clientId }: { contract: ReportContractV1; clientId: string }) {
  const { report, business, paid_media, journey, diagnostics, recommendations, governance } = contract
  const { period, business_model } = report
  const isEcommerce = business_model === 'ecommerce'

  return (
    <div className="p-8 max-w-5xl">
      {/* Header */}
      <div className="mb-8">
        <Link href={`/clients/${clientId}/dashboard`} className="inline-flex items-center gap-1.5 text-slate-500 hover:text-white text-xs mb-4 transition-colors">
          <ArrowLeft size={12} /> Voltar ao dashboard
        </Link>
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-xl font-bold text-white mb-1">Performance {report.type === 'monthly' ? 'Mensal' : 'Semanal'}</h1>
            <div className="flex gap-4 mb-3 text-sm text-indigo-400">
              <Link href={`/clients/${clientId}/performance?type=weekly`}>Semanal</Link>
              <Link href={`/clients/${clientId}/performance?type=monthly`}>Mensal</Link>
            </div>
            {!report.comparison_period && <p className="text-xs text-slate-500 mb-2">Comparação anterior indisponível.</p>}
            <div className="flex items-center gap-3 text-sm text-slate-400">
              <span className="flex items-center gap-1.5"><Clock size={13} />{period.label ?? `${period.start} → ${period.end}`}</span>
              <span className={`px-2 py-0.5 rounded-full text-xs font-medium border ${isEcommerce ? 'bg-indigo-500/10 text-indigo-400 border-indigo-500/30' : 'bg-purple-500/10 text-purple-400 border-purple-500/30'}`}>
                {isEcommerce ? 'E-commerce' : 'Lead Generation'}
              </span>
              <span className="text-xs text-slate-600 font-mono">{contract.schema_version}</span>
            </div>
          </div>
          {/* Period picker — form submission, no JS required */}
          <form method="GET" className="flex items-center gap-2">
            <input type="hidden" name="type" value={report.type} />
            <label className="text-xs text-slate-500">Período</label>
            <input
              name="period"
              defaultValue={`${period.start}_to_${period.end}`}
              placeholder="YYYY-MM-DD_to_YYYY-MM-DD"
              className="text-xs bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-3 py-1.5 text-slate-300 placeholder:text-slate-600 focus:outline-none focus:border-indigo-500 w-52"
            />
            <button type="submit" className="text-xs bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded-lg transition-colors">
              Carregar
            </button>
            <Link href={`/clients/${clientId}/performance?type=${report.type}`} className="text-xs text-slate-500 hover:text-slate-300 px-2 py-1.5">
              Último
            </Link>
          </form>
        </div>
      </div>

      {isEcommerce
        ? <EcommerceBusinessSection business={business as EcommerceBusiness} />
        : <LeadGenBusinessSection   business={business as LeadGenBusiness} />
      }

      {paid_media?.total_spend != null && (
        <SectionCard title="Mídia Paga — Total">
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            <MetricCell label="Investimento Total" metric={paid_media.total_spend} fmt="currency" />
          </div>
        </SectionCard>
      )}

      {paid_media?.google_ads != null && (
        <SectionCard title="Google Ads">
          <ChannelGrid data={paid_media.google_ads} />
        </SectionCard>
      )}

      {paid_media?.meta_ads != null && (
        <SectionCard title="Meta Ads">
          <ChannelGrid data={paid_media.meta_ads} />
        </SectionCard>
      )}

      {journey != null && (
        <CollapsibleSection title="Jornada">
          <JsonDump data={journey} />
        </CollapsibleSection>
      )}

      {recommendations != null && <RecommendationsSection data={recommendations} />}

      {diagnostics != null && (
        <CollapsibleSection title="Diagnóstico">
          <JsonDump data={diagnostics} />
        </CollapsibleSection>
      )}

      {governance != null && (
        <CollapsibleSection title="Governance & Limitações">
          <JsonDump data={governance} />
        </CollapsibleSection>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default async function PerformancePage({
  params,
  searchParams,
}: {
  params:       Promise<{ clientId: string }>
  searchParams: Promise<{ period?: string; type?: string }>
}) {
  const { clientId } = await params
  const { period, type } = await searchParams

  let contract: ReportContractV1 | null = null
  let errorCode: ReportErrorCode | null = null

  try {
    contract = type === 'monthly'
      ? period ? await getMonthlyReport(clientId, period) : await getLatestMonthlyReport(clientId)
      : period ? await getWeeklyReport(clientId, period) : await getLatestWeeklyReport(clientId)
  } catch (err) {
    errorCode = err instanceof ReportError ? err.code : 'unavailable'
  }

  if (errorCode || !contract) {
    return <ErrorState code={errorCode ?? 'unavailable'} clientId={clientId} />
  }

  return <ContractView contract={contract} clientId={clientId} />
}
