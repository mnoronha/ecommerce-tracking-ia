import { redirect } from 'next/navigation'
import { createSupabaseServerClient } from '@/lib/supabase-server'
import { agencyApiFetch } from '@/lib/agency-api-client'
import { GoalsBudgetClient } from './_components/goals-budget-client'
import type {
  PacingContract,
  ClientBalanceOut,
  BudgetConfigListOut,
  TargetTruthReadOut,
} from './_components/goals-budget-client'

export default async function GoalsBudgetPage({
  params,
}: {
  params: Promise<{ clientId: string }>
}) {
  const { clientId } = await params

  const supabase = await createSupabaseServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) redirect('/login')

  const [pacingResult, balanceResult, budgetResult, targetsResult] =
    await Promise.allSettled([
      agencyApiFetch<PacingContract>(`clients/${clientId}/pacing`),
      agencyApiFetch<ClientBalanceOut>(`clients/${clientId}/balance`),
      agencyApiFetch<BudgetConfigListOut>(`clients/${clientId}/budget/config`),
      agencyApiFetch<TargetTruthReadOut>(`clients/${clientId}/truth/targets`),
    ])

  return (
    <GoalsBudgetClient
      clientId={clientId}
      pacing={pacingResult.status === 'fulfilled' ? pacingResult.value : null}
      balance={balanceResult.status === 'fulfilled' ? balanceResult.value : null}
      budgetConfig={budgetResult.status === 'fulfilled' ? budgetResult.value : null}
      targets={targetsResult.status === 'fulfilled' ? targetsResult.value : null}
    />
  )
}
