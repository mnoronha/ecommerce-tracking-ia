import { redirect } from 'next/navigation'

export default async function ClientIndexPage({ params }: { params: Promise<{ clientId: string }> }) {
  const { clientId } = await params
  redirect(`/clients/${clientId}/dashboard`)
}
