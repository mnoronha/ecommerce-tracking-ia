export type PlanId = 'rastreador' | 'inteligencia' | 'predicao'

export interface Plan {
  id: PlanId
  name: string
  tagline: string
  price: number         // monthly BRL
  priceAnnual: number   // monthly BRL when billed annually
  clientLimit: number   // Infinity = unlimited
  ordersLimit: number | null  // null = unlimited
  highlight: boolean
  badge: string | null
  color: string
  features: string[]
  gates: string[]  // features NOT included (shown as locked)
}

export const PLANS: Plan[] = [
  {
    id: 'rastreador',
    name: 'Rastreador',
    tagline: 'Tracking server-side e CAPI para lojas em crescimento',
    price: 297,
    priceAnnual: 247,
    clientLimit: 1,
    ordersLimit: 2000,
    highlight: false,
    badge: null,
    color: 'border-slate-600',
    features: [
      '1 loja conectada',
      'Até 2.000 pedidos/mês',
      'Tracking server-side completo',
      'Meta CAPI + Google Ads + TikTok',
      'Dashboard: KPIs, funil, heatmap',
      'Atribuição multi-touch (5 modelos)',
      'Atribuição unificada (resolve overlap Meta/Google)',
      'Alertas de anomalia via Slack/email',
      'Jornada Campanha × Produto',
    ],
    gates: [
      'Insights IA (Claude) semanais',
      'LTV preditivo + value-based bidding',
      'Survey de atribuição pós-compra',
      'Creative Intelligence (Claude Vision)',
      'CNAME white-label',
    ],
  },
  {
    id: 'inteligencia',
    name: 'Inteligência',
    tagline: 'IA para otimizar budget e prever crescimento',
    price: 697,
    priceAnnual: 581,
    clientLimit: 3,
    ordersLimit: 20000,
    highlight: true,
    badge: 'Mais popular',
    color: 'border-indigo-500',
    features: [
      'Até 3 lojas',
      'Até 20.000 pedidos/mês',
      'Tudo do Rastreador, mais:',
      'Relatórios IA semanais (Claude)',
      'LTV preditivo + value-based bidding',
      'Survey de atribuição pós-compra',
      'Pacing + Forecast de receita mensal',
      'COGS e Margem por campanha',
      'ROAS de Margem vs ROAS de Receita',
    ],
    gates: [
      'Creative Intelligence (Claude Vision)',
      'CNAME white-label',
      'SLA 4h + account manager',
    ],
  },
  {
    id: 'predicao',
    name: 'Predição',
    tagline: 'IA criativa e escala ilimitada para marcas grandes',
    price: 1497,
    priceAnnual: 1247,
    clientLimit: Infinity,
    ordersLimit: null,
    highlight: false,
    badge: 'Completo',
    color: 'border-purple-500',
    features: [
      'Lojas ilimitadas',
      'Pedidos ilimitados',
      'Tudo do Inteligência, mais:',
      'Creative Intelligence (Claude Vision)',
      'Análise visual top/bottom criativos por ROAS',
      'ROAS de Margem avançado por criativo',
      'CNAME white-label próprio',
      'SLA 4h + account manager dedicado',
      'Setup assistido incluído',
    ],
    gates: [],
  },
]

export function getPlan(id: PlanId): Plan {
  return PLANS.find(p => p.id === id) ?? PLANS[0]
}

export function planGates(_planId: PlanId): Record<string, boolean> {
  // All features unlocked during testing phase
  return {
    ai_insights:           true,
    ltv_bidding:           true,
    survey:                true,
    creative_intelligence: true,
    white_label:           true,
    unlimited_clients:     true,
  }
}

export function fmtPrice(n: number) {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }).format(n)
}
