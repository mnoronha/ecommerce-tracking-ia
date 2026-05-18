import { redirect } from 'next/navigation'
import Link from 'next/link'
import { createSupabaseServerClient } from '@/lib/supabase-server'
import { Plus, Settings, BarChart2, ShoppingBag, Users, Zap } from 'lucide-react'
import { PLANS, type PlanId } from '@/lib/plans'

interface Client {
  id: string
  name: string
  pixel_id: string
  ecommerce_platform: string
  is_active: boolean
  meta_pixel_id: string | null
  ga4_measurement_id: string | null
  created_at: string
}

async function getClients(): Promise<Client[]> {
  const supabase = await createSupabaseServerClient()
  const { data, error } = await supabase
    .from('clients')
    .select('id,name,pixel_id,ecommerce_platform,is_active,meta_pixel_id,ga4_measurement_id,created_at')
    .order('created_at', { ascending: false })
  if (error) {
    console.error('[getClients] Error:', error)
    return []
  }
  return data || []
}

const PLATFORM_LABEL: Record<string, string> = {
  shopify:     'Shopify',
  nuvemshop:   'Nuvemshop',
  woocommerce: 'WooCommerce',
}

async function getAgencyPlan(userId: string): Promise<{ plan: PlanId; clientLimit: number; trialDaysLeft: number | null } | null> {
  const supabase = await createSupabaseServerClient()
  const { data } = await supabase
    .from('agency_members')
    .select('agency:agencies(plan, client_limit, trial_ends_at)')
    .eq('user_id', userId)
    .limit(1)
    .single()
  if (!data?.agency) return null
  const ag = (Array.isArray(data.agency) ? data.agency[0] : data.agency) as { plan: string; client_limit: number; trial_ends_at: string | null }
  let trialDaysLeft: number | null = null
  if (ag.trial_ends_at) {
    const diff = new Date(ag.trial_ends_at).getTime() - Date.now()
    if (diff > 0) trialDaysLeft = Math.ceil(diff / 86400_000)
  }
  return { plan: ag.plan as PlanId, clientLimit: ag.client_limit, trialDaysLeft }
}

const PLAN_COLORS: Record<PlanId, string> = {
  rastreador:   'bg-slate-500/15 text-slate-300 border-slate-500/30',
  inteligencia: 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30',
  predicao:     'bg-purple-500/15 text-purple-300 border-purple-500/30',
}

export default async function ClientsPage() {
  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) redirect('/login')

  const [clients, agencyPlan] = await Promise.all([getClients(), getAgencyPlan(user.id)])

  const planInfo = agencyPlan ? PLANS.find(p => p.id === agencyPlan.plan) : null

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Trial banner */}
      {agencyPlan?.trialDaysLeft != null && agencyPlan.trialDaysLeft > 0 && (
        <div className="mb-5 bg-indigo-500/10 border border-indigo-500/20 rounded-xl px-5 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Zap size={14} className="text-indigo-400" />
            <p className="text-sm text-indigo-300 font-medium">
              Trial gratuito: <strong>{agencyPlan.trialDaysLeft} dias restantes</strong>
            </p>
          </div>
          <span className="text-xs text-slate-500">Plano {planInfo?.name}</span>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <div className="flex items-center gap-3 mb-0.5">
            <h1 className="text-xl font-bold text-white">Minhas Lojas</h1>
            {planInfo && (
              <span className={`text-xs px-2.5 py-1 rounded-full border font-medium ${PLAN_COLORS[agencyPlan!.plan]}`}>
                {planInfo.name}
              </span>
            )}
          </div>
          <p className="text-sm text-slate-500">
            {clients.length}{agencyPlan ? `/${agencyPlan.clientLimit === 9999 ? '∞' : agencyPlan.clientLimit}` : ''} loja{clients.length !== 1 ? 's' : ''}
          </p>
        </div>
        <Link
          href="/clients/new"
          className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
        >
          <Plus size={15} />
          Nova loja
        </Link>
      </div>

      {/* Grid */}
      {clients.length === 0 ? (
        <div className="text-center py-20 text-slate-500">
          <Users size={40} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">Nenhum cliente cadastrado ainda.</p>
          <Link href="/clients/new" className="mt-3 inline-block text-indigo-400 text-sm hover:underline">
            Adicionar primeiro cliente →
          </Link>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {clients.map(c => (
            <ClientCard key={c.id} client={c} />
          ))}
        </div>
      )}
    </div>
  )
}

function ClientCard({ client }: { client: Client }) {
  const integrations = [
    client.meta_pixel_id       && 'Meta',
    client.ga4_measurement_id  && 'GA4',
  ].filter(Boolean)

  return (
    <div className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-xl p-4 flex flex-col gap-4">
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${client.is_active ? 'bg-emerald-400' : 'bg-slate-500'}`} />
            <h3 className="text-sm font-semibold text-white">{client.name}</h3>
          </div>
          <p className="text-xs text-slate-500 mt-0.5">{PLATFORM_LABEL[client.ecommerce_platform] || client.ecommerce_platform}</p>
        </div>
        <Link
          href={`/clients/${client.pixel_id}/settings`}
          className="text-slate-500 hover:text-white transition-colors"
          title="Configurações"
        >
          <Settings size={15} />
        </Link>
      </div>

      {integrations.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {integrations.map(i => (
            <span key={i} className="text-xs bg-indigo-600/15 text-indigo-400 border border-indigo-500/20 px-2 py-0.5 rounded-full">
              {i}
            </span>
          ))}
        </div>
      )}

      <div className="flex gap-2 mt-auto">
        <Link
          href={`/clients/${client.pixel_id}/dashboard`}
          className="flex-1 flex items-center justify-center gap-1.5 bg-[#0f1117] hover:bg-[#252b3b] border border-[#2a2f3e] text-slate-300 text-xs font-medium py-2 rounded-lg transition-colors"
        >
          <BarChart2 size={12} />
          Dashboard
        </Link>
        <Link
          href={`/clients/${client.pixel_id}/pedidos`}
          className="flex-1 flex items-center justify-center gap-1.5 bg-[#0f1117] hover:bg-[#252b3b] border border-[#2a2f3e] text-slate-300 text-xs font-medium py-2 rounded-lg transition-colors"
        >
          <ShoppingBag size={12} />
          Pedidos
        </Link>
      </div>
    </div>
  )
}
