import { redirect } from 'next/navigation'
import { createSupabaseServerClient } from '@/lib/supabase-server'
import { ActionCenterView } from '@/components/action-center/action-center-view'

export default async function ClientActionCenterPage({
  params,
}: {
  params: Promise<{ clientId: string }>
}) {
  const supabase = await createSupabaseServerClient()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!user) redirect('/login')

  const { clientId } = await params
  return <ActionCenterView clientId={clientId} />
}
