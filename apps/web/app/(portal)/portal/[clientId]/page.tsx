'use client'

import { useParams, redirect } from 'next/navigation'
import { useEffect } from 'react'
import { useRouter } from 'next/navigation'

export default function PortalRoot() {
  const params = useParams()
  const router = useRouter()

  useEffect(() => {
    const clientId = params?.clientId as string
    if (clientId) router.replace(`/portal/${clientId}/dashboard`)
  }, [params, router])

  return null
}
