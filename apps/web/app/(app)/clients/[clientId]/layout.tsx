'use client'

import Link from 'next/link'
import { usePathname, useParams } from 'next/navigation'
import { useClientConfig, type ClientChannels } from '@/lib/use-client-config'
import { CriticalAlertsBar } from '@/components/alerts/critical-alerts-bar'
import {
  LayoutDashboard, Users, ShoppingBag, Target, Settings, ArrowLeft, BarChart2,
  TrendingUp, Radio, DollarSign, GitBranch, FileText, UserCog, Bell,
  Layers, Activity, BrainCircuit, Store, PenLine, Search, ShieldCheck, Wrench,
  History, Rocket, UserCheck, Sparkles,
} from 'lucide-react'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Icon = React.ComponentType<any>

/** `channel` = só aparece quando o canal está configurado no cliente. */
interface NavItem { href: string; label: string; icon: Icon; channel?: keyof ClientChannels; match?: string[] }
interface NavGroup { label: string; items: NavItem[] }

export default function ClientLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const params   = useParams()
  const clientId = params.clientId as string

  const { config, ready } = useClientConfig(clientId)
  const clientName = config.name || clientId
  const base = `/clients/${clientId}`

  const NAV_GROUPS: NavGroup[] = [
    {
      label: 'VISÃO GERAL',
      items: [
        { href: `${base}/dashboard`,       label: 'Dashboard',   icon: LayoutDashboard },
        { href: `${base}/shopify-revenue`, label: 'Faturamento', icon: ShoppingBag, channel: 'shopify' },
        { href: `${base}/live`,            label: 'Ao vivo',     icon: Radio },
      ],
    },
    {
      label: 'TRACKING',
      items: [
        { href: `${base}/tracking`,   label: 'Qualidade do Tracking', icon: Target },
        { href: `${base}/visitantes`, label: 'Visitantes',            icon: Users },
        { href: `${base}/audiencias`, label: 'Audiências',            icon: Layers },
      ],
    },
    {
      label: 'VENDAS',
      items: [
        { href: `${base}/pedidos`,     label: 'Pedidos',             icon: ShoppingBag },
        { href: `${base}/attribution`, label: 'Atribuição',          icon: TrendingUp },
        { href: `${base}/journey`,     label: 'Campanha × Produto',  icon: GitBranch },
      ],
    },
    {
      label: 'CANAIS',
      items: [
        { href: `${base}/meta-ads`,        label: 'Meta Ads',        icon: TrendingUp, channel: 'meta' },
        { href: `${base}/google-ads`,      label: 'Google Ads',      icon: TrendingUp, channel: 'googleAds' },
        { href: `${base}/tiktok-ads`,      label: 'TikTok Ads',      icon: TrendingUp, channel: 'tiktok' },
        { href: `${base}/ga4`,             label: 'GA4',             icon: BarChart2,  channel: 'ga4' },
        { href: `${base}/search-console`,  label: 'Search Console',  icon: Search,     channel: 'searchConsole' },
        { href: `${base}/merchant-center`, label: 'Merchant Center', icon: Store,      channel: 'merchantCenter' },
        { href: `${base}/ai-visibility`,   label: 'AI Visibility',   icon: BrainCircuit },
      ],
    },
    {
      label: 'INTELLIGENCE',
      items: [
        { href: `${base}/content`,   label: 'Conteúdo IA',    icon: PenLine },
        { href: `${base}/creatives`, label: 'Criativos · IA', icon: Sparkles },
        { href: `${base}/reports`,   label: 'Relatórios IA',  icon: FileText },
      ],
    },
    {
      label: 'OPERAÇÕES',
      items: [
        {
          href: `${base}/agency-os`, label: 'Agency OS', icon: ShieldCheck,
          match: ['agency-os', 'performance', 'data-health', 'recommendations', 'learning-candidates', 'changes'],
        },
        { href: `${base}/alertas`,     label: 'Alertas',     icon: Bell },
        { href: `${base}/diagnostics`, label: 'Diagnóstico', icon: Activity },
      ],
    },
    {
      label: 'TÉCNICO',
      items: [
        { href: `${base}/ai-presence/pipeline`,            label: 'Pipeline',          icon: Rocket },
        { href: `${base}/technical/schema-audit`,          label: 'Schema Audit',      icon: ShieldCheck },
        { href: `${base}/technical/llms-txt`,              label: 'llms.txt',          icon: FileText },
        { href: `${base}/technical/pending-optimizations`, label: 'Otimizações',       icon: Wrench },
        { href: `${base}/technical/history`,               label: 'Histórico Técnico', icon: History },
        { href: `${base}/technical/onboarding`,            label: 'Onboarding',        icon: UserCheck },
      ],
    },
    {
      label: 'CONFIGURAÇÃO',
      items: [
        { href: `${base}/goals-budget`, label: 'Metas & Orçamento', icon: Target },
        { href: `${base}/cogs`,         label: 'Custos & Margem',   icon: DollarSign },
        { href: `${base}/settings`,     label: 'Configurações',     icon: Settings },
        { href: `${base}/users`,        label: 'Usuários',          icon: UserCog },
      ],
    },
  ]

  // Canais só aparecem quando configurados (enquanto carrega, ficam ocultos).
  const visibleGroups = NAV_GROUPS
    .map(g => ({ ...g, items: g.items.filter(i => !i.channel || (ready && config.channels[i.channel])) }))
    .filter(g => g.items.length > 0)

  const isActive = (item: NavItem) =>
    pathname === item.href ||
    (item.match ?? []).some(seg => pathname === `${base}/${seg}` || pathname.startsWith(`${base}/${seg}/`))

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

        <nav className="flex-1 overflow-y-auto p-3">
          {visibleGroups.map(group => (
            <div key={group.label} className="mb-3">
              <p className="px-3 mb-1 text-[10px] font-semibold text-slate-600 uppercase tracking-wider">
                {group.label}
              </p>
              <div className="space-y-0.5">
                {group.items.map(item => {
                  const active = isActive(item)
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                        active
                          ? 'bg-indigo-600/20 text-indigo-400 font-medium'
                          : 'text-slate-400 hover:text-white hover:bg-[#1a1f2e]'
                      }`}
                    >
                      <item.icon size={15} />
                      <span className="flex-1">{item.label}</span>
                    </Link>
                  )
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* Footer */}
        <div className="p-3 border-t border-[#2a2f3e] space-y-2">
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
