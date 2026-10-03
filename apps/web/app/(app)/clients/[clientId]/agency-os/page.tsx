import Link from 'next/link'
import { getLatestOperations, operationLabel, type OperationalAlert, type OperationsContract } from '@/lib/agency-operations'
import { ReportError } from '@/lib/agency-os'

const capabilityNames: Record<string, string> = {
  collection_automation_readonly: 'Coleta automática em leitura', media_analysis: 'Análise de mídia',
  journey_analysis: 'Análise de jornada', conversion_analysis: 'Análise de conversões oficiais',
  business_analysis: 'Análise de vendas ou CRM', decision_automation: 'Decisões automáticas', execution: 'Execução automática',
}
const date = (value?: string) => value && !Number.isNaN(new Date(value).getTime()) ? new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short', timeZone: 'America/Sao_Paulo' }).format(new Date(value)) : 'Não informado'
const panel = 'rounded-xl border border-slate-700 bg-slate-900/60 p-5 space-y-3'

function AlertCard({ bundle }: { bundle: OperationalAlert }) {
  const { alert, diagnosis, recommendation } = bundle
  return <article className={panel}>
    <div className="flex flex-wrap items-center gap-2">
      <span className={`rounded px-2 py-1 text-xs font-semibold ${alert.severity === 'P0' || alert.severity === 'P1' ? 'bg-red-950 text-red-300' : 'bg-amber-950 text-amber-300'}`}>{alert.severity}</span>
      <h3 className="font-semibold text-slate-100">{operationLabel(alert.type)}</h3>
      <span className="text-xs text-slate-400">{operationLabel(alert.state)}</span>
    </div>
    <p className="text-xs text-slate-400">Fonte: {operationLabel(alert.source || 'Não informada')} · Último registro: {date(alert.last_seen_at || alert.created_at)}</p>
    <p className="text-sm text-slate-300"><strong>Diagnóstico: </strong>{diagnosis.diagnosis}</p>
    <p className="text-sm text-indigo-200"><strong>Ação recomendada: </strong>{recommendation.proposed_action}</p>
    <details className="text-sm text-slate-400">
      <summary className="cursor-pointer text-slate-300">Evidências e revisão</summary>
      <div className="mt-3 space-y-2">
        <p>O diagnóstico reflete o registro operacional. A causa precisa ser verificada na fonte.</p>
        <pre className="overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-3 text-xs">{JSON.stringify(alert.details || {}, null, 2)}</pre>
        <p><strong>Risco: </strong>{recommendation.risk}</p>
        <p><strong>Como preservar o estado anterior: </strong>{recommendation.rollback}</p>
        <p><strong>Reavaliar: </strong>{recommendation.reevaluation_window}</p>
      </div>
    </details>
    {alert.resolution && <div className="rounded bg-emerald-950/40 p-3 text-sm text-emerald-200">
      <p><strong>Resolução: </strong>{alert.resolution.reason}</p>
      <p><strong>Evidência: </strong>{alert.resolution.evidence}</p>
      <p><strong>Revisado por: </strong>{alert.resolution.resolved_by}</p>
    </div>}
    <p className="break-all text-xs text-slate-500">Referência: {alert.alert_id}</p>
  </article>
}

function OperationsView({ contract, clientId }: { contract: OperationsContract; clientId: string }) {
  return <div className="space-y-6 p-6 text-slate-200">
    <header className="space-y-2">
      <h1 className="text-2xl font-bold text-white">Agency OS</h1>
      <p className="text-sm text-slate-400">Estado operacional recebido do Hermes · Atualizado em {date(contract.generated_at)} · Horário de Brasília</p>
      <p className="text-sm text-indigo-200">Revisão humana obrigatória. Decisões e execução automáticas estão bloqueadas.</p>
      <Link href={`/clients/${clientId}/performance?type=monthly`} className="inline-block text-sm text-indigo-400 underline">Ver relatório mensal</Link>
    </header>

    <section className={panel}>
      <h2 className="text-lg font-semibold">Fontes e capacidades</h2>
      <div className="flex flex-wrap gap-3">{Object.entries(contract.source_status).map(([key, value]) => <p key={key} className="rounded bg-slate-800 px-3 py-2 text-sm">{operationLabel(key)}: <strong>{operationLabel(value)}</strong></p>)}</div>
      <div className="grid gap-2 md:grid-cols-2">{Object.entries(capabilityNames).map(([key, label]) => <p key={key} className="flex items-center justify-between gap-3 text-sm"><span>{label}</span><span className={contract.capabilities[key] ? 'text-emerald-300' : 'text-amber-300'}>{contract.capabilities[key] ? 'Disponível' : 'Bloqueada'}</span></p>)}</div>
      <p className="text-sm text-slate-400">Metas cadastradas: {contract.target_truth.period || 'Não informado'} · Período atual: {contract.current_period}</p>
    </section>

    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Pendências humanas</h2>
      {contract.human_dependencies.length === 0 ? <p className="text-sm text-slate-400">Nenhuma pendência registrada nesta atualização.</p> : <div className="grid gap-3 md:grid-cols-2">{contract.human_dependencies.map(row => <article key={row.dependency_id} className={panel}>
        <h3 className="font-semibold">{operationLabel(row.type)}</h3>
        <p className="text-xs text-amber-300">{operationLabel(row.status)} · {operationLabel(row.blocking_level)}</p>
        <p className="text-sm text-slate-300">{row.requested_action}</p>
        <p className="break-all text-xs text-slate-500">Referência: {row.dependency_id}</p>
      </article>)}</div>}
    </section>

    <section className="grid gap-4 md:grid-cols-2">
      <article className={panel}><h2 className="text-lg font-semibold">Creative Engine</h2><p className="text-sm text-amber-300">{operationLabel(contract.workflows.creative.state)} · Serviço: {operationLabel(contract.workflows.creative.service_status)}</p><ul className="list-disc space-y-2 pl-5 text-sm">{contract.workflows.creative.blockers.map(reason => <li key={reason}>{operationLabel(reason)}</li>)}</ul><p className="text-sm text-slate-400">Briefing → conceito e copy → produção → validação → aprovação humana → material pronto para campanha.</p></article>
      <article className={panel}><h2 className="text-lg font-semibold">Campaign Builder</h2><p className="text-sm text-amber-300">{operationLabel(contract.workflows.campaign.state)}</p><ul className="list-disc space-y-2 pl-5 text-sm">{contract.workflows.campaign.blockers.map(reason => <li key={reason}>{operationLabel(reason)}</li>)}</ul><p className="text-sm text-slate-400">Propostas passam por validação e aprovação humana. Novas campanhas devem nascer pausadas.</p></article>
    </section>

    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Alertas, diagnóstico e recomendações</h2>
      <p className="text-sm text-slate-400">Alertas de performance aguardam conversões e resultados de negócio certificados. A resolução humana é registrada no Hermes e aparece após a próxima atualização.</p>
      {contract.alerts.length === 0 ? <p className="text-sm text-slate-400">Nenhuma ocorrência registrada nesta atualização.</p> : contract.alerts.map(bundle => <AlertCard key={bundle.alert.alert_id} bundle={bundle} />)}
    </section>
  </div>
}

export default async function Page({ params }: { params: Promise<{ clientId: string }> }) {
  const { clientId } = await params
  let contract: OperationsContract
  try { contract = await getLatestOperations(clientId) }
  catch (error) {
    return <div className="space-y-3 p-6"><h1 className="text-2xl font-bold text-white">Agency OS</h1><p className="text-slate-400">{error instanceof ReportError ? error.message : 'Não foi possível carregar o estado operacional.'}</p><p className="text-sm text-amber-300">Ausência de atualização não confirma que as fontes estão saudáveis.</p></div>
  }
  return <OperationsView contract={contract} clientId={clientId} />
}
