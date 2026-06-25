'use client'

import Link from 'next/link'
import { usePathname, useParams } from 'next/navigation'
import { useAgencyPlan } from '@/lib/use-agency-plan'
import { PlanLockBadge } from '@/components/plan-gate'
import { CriticalAlertsBar } from '@/components/alerts/critical-alerts-bar'
import { LayoutDashboard, Users, ShoppingBag, Target, Settings, ArrowLeft, BarChart2, TrendingUp, Radio, DollarSign, GitBranch, Sparkles, FileText, UserCog, Bell, Layers, Activity, BrainCircuit, Store, PenLine, Search } from 'lucide-react'

export default function ClientLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const params   = useParams()
  const clientId = params.clientId as string

  const { plan } = useAgencyPlan(clientId)
  const clientName = plan.clientName || clientId

  const NAV = [
    { href: `/clients/${clientId}/dashboard`,        label: 'Dashboard',       icon: LayoutDashboard, gate: null },
    { href: `/clients/${clientId}/shopify-revenue`, label: 'Faturamento',     icon: ShoppingBag,     gate: null },
    { href: `/clients/${clientId}/live`,            label: 'Ao vivo',         icon: Radio,           gate: null },
    { href: `/clients/${clientId}/visitantes`,      label: 'Visitantes',      icon: Users,           gate: null },
    { href: `/clients/${clientId}/pedidos`,         label: 'Pedidos',         icon: ShoppingBag,     gate: null },
    { href: `/clients/${clientId}/audiencias`,  label: 'Audiências',      icon: Layers,          gate: null },
    { href: `/clients/${clientId}/attribution`, label: 'Atribuição',      icon: TrendingUp,      gate: null },
    { href: `/clients/${clientId}/journey`,     label: 'Jornada',         icon: GitBranch,       gate: null },
    { href: `/clients/${clientId}/meta-ads`,      label: 'Meta Ads',        icon: TrendingUp,      gate: null },
    { href: `/clients/${clientId}/google-ads`,   label: 'Google Ads',      icon: TrendingUp,      gate: null },
    { href: `/clients/${clientId}/tiktok-ads`,   label: 'TikTok Ads',      icon: TrendingUp,      gate: null },
    { href: `/clients/${clientId}/pinterest-ads`, label: 'Pinterest Ads',  icon: TrendingUp,      gate: null },
    { href: `/clients/${clientId}/ga4`,             label: 'GA4',             icon: BarChart2,       gate: null },
    { href: `/clients/${clientId}/search-console`,   label: 'Search Console',  icon: Search,          gate: null },
    { href: `/clients/${clientId}/ai-visibility`,    label: 'AI Visibility',   icon: BrainCircuit,    gate: null },
    { href: `/clients/${clientId}/merchant-center`,  label: 'Merchant Center', icon: Store,            gate: null },
    { href: `/clients/${clientId}/content`,          label: 'Conteúdo IA',     icon: PenLine,          gate: null },
    { href: `/clients/${clientId}/creatives`,   label: 'Criativos · IA',  icon: Sparkles,        gate: 'creative_intelligence' },
    { href: `/clients/${clientId}/reports`,     label: 'Relatórios IA',   icon: FileText,        gate: 'ai_insights' },
    { href: `/clients/${clientId}/metas`,       label: 'Metas',           icon: Target,          gate: null },
    { href: `/clients/${clientId}/alertas`,     label: 'Alertas',         icon: Bell,            gate: null },
    { href: `/clients/${clientId}/diagnostics`, label: 'Diagnóstico',     icon: Activity,        gate: null },
    { href: `/clients/${clientId}/cogs`,        label: 'Custos & Margem', icon: DollarSign,      gate: null },
    { href: `/clients/${clientId}/settings`,    label: 'Configurações',   icon: Settings,        gate: null },
    { href: `/clients/${clientId}/users`,       label: 'Usuários',        icon: UserCog,         gate: null },
  ]

  return (
    <div className="flex min-h-screen bg-[#0f1117]">
      <aside className="w-52 shrink-0 border-r border-[#2a2f3e] flex flex-col">
        <div className="px-4 py-4 border-b border-[#2a2f3e]">
          <Link href="/clients" className="flex items-center gap-1.5 text-slate-500 hover:text-white transition-colors text-xs mb-3">
            <ArrowLeft size={12} />
            Todos os clientes
          </Link>
          <div className="flex items-center gap-2">
            <BarChart2 size={16} className="text-indigo-400 shrink-0" />
            <span className="text-sm font-bold text-white truncate" title={clientId}>{clientName}</span>
          </div>
        </div>
        <nav className="flex-1 p-3 space-y-1">
          {NAV.map(item => {
            const active  = pathname === item.href
            const locked  = item.gate ? !(plan.gates[item.gate] ?? true) : false
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                  active
                    ? 'bg-indigo-600/20 text-indigo-400 font-medium'
                    : locked
                      ? 'text-slate-600 hover:text-slate-400 hover:bg-[#1a1f2e]'
                      : 'text-slate-400 hover:text-white hover:bg-[#1a1f2e]'
                }`}
              >
                <item.icon size={15} />
                <span className="flex-1">{item.label}</span>
                <PlanLockBadge show={locked} />
              </Link>
            )
          })}
        </nav>

        {/* Plan badge + footer */}
        <div className="p-3 border-t border-[#2a2f3e] space-y-2">
          <Link
            href="/billing"
            className="flex items-center justify-between px-3 py-2 rounded-lg bg-[#1a1f2e] hover:bg-[#252a3a] transition-colors group"
          >
            <span className="text-xs text-slate-500 group-hover:text-slate-300 transition-colors capitalize">
              Plano {plan.planId}
            </span>
            {plan.isTrialing && (
              <span className="text-xs text-indigo-400 font-medium">Trial</span>
            )}
          </Link>
          <Link href="/privacidade" className="block px-3 text-[11px] text-slate-600 hover:text-slate-400 transition-colors">
            Política de Privacidade
          </Link>
        </div>
      </aside>
      <main className="flex-1 overflow-auto">
        <CriticalAlertsBar clientId={clientId} />
        {children}
      </main>
    </div>
  )
}
