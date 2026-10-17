'use client'

/**
 * Configuração do cliente usada pela navegação e pelo dashboard para mostrar
 * e buscar SOMENTE os canais ativos (economiza requisições e deixa a tela
 * mais rápida).
 *
 * Lê apenas colunas não-secretas (IDs de conta), nunca tokens.
 * Cache em memória por pixel_id (5 min), como o hook antigo de plano.
 */

import { useEffect, useState } from 'react'
import { createSupabaseBrowserClient } from './supabase-browser'

export interface ClientChannels {
  meta:           boolean
  googleAds:      boolean
  tiktok:         boolean
  ga4:            boolean
  searchConsole:  boolean
  merchantCenter: boolean
  shopify:        boolean
}

export interface ClientConfig {
  name:     string
  channels: ClientChannels
}

const NONE: ClientChannels = {
  meta: false, googleAds: false, tiktok: false, ga4: false,
  searchConsole: false, merchantCenter: false, shopify: false,
}

// Usado se a leitura falhar: melhor mostrar tudo do que esconder um canal ativo.
const ALL: ClientChannels = {
  meta: true, googleAds: true, tiktok: true, ga4: true,
  searchConsole: true, merchantCenter: true, shopify: true,
}

const _cache = new Map<string, { config: ClientConfig; fetchedAt: number }>()
const CACHE_TTL_MS = 5 * 60 * 1000

const filled = (v: unknown) => typeof v === 'string' ? v.trim().length > 0 : v != null

export function channelsFromRow(row: Record<string, unknown> | null | undefined): ClientChannels {
  if (!row) return NONE
  return {
    meta:           filled(row.meta_ad_account_id),
    googleAds:      filled(row.google_ads_customer_id),
    tiktok:         filled(row.tiktok_advertiser_id),
    ga4:            filled(row.ga4_property_id),
    searchConsole:  filled(row.search_console_site_url),
    merchantCenter: filled(row.merchant_center_id),
    shopify:        row.ecommerce_platform === 'shopify' && filled(row.shopify_domain),
  }
}

/**
 * `ready` = false enquanto a configuração não chegou. Componentes que buscam
 * dados de canais devem esperar `ready` para não disparar requisições à toa.
 */
export function useClientConfig(pixelId?: string): { config: ClientConfig; ready: boolean } {
  const entry   = pixelId ? _cache.get(pixelId) : undefined
  const isFresh = entry ? Date.now() - entry.fetchedAt < CACHE_TTL_MS : false

  const [config, setConfig] = useState<ClientConfig>(
    isFresh ? entry!.config : { name: pixelId ?? '', channels: NONE },
  )
  const [ready, setReady] = useState<boolean>(isFresh)

  useEffect(() => {
    if (!pixelId) { setReady(true); return }
    const e = _cache.get(pixelId)
    if (e && Date.now() - e.fetchedAt < CACHE_TTL_MS) {
      setConfig(e.config); setReady(true); return
    }
    let cancelled = false
    createSupabaseBrowserClient()
      .from('clients')
      .select(
        'name, ecommerce_platform, shopify_domain, meta_ad_account_id, google_ads_customer_id, ' +
        'tiktok_advertiser_id, ga4_property_id, search_console_site_url, merchant_center_id',
      )
      .eq('pixel_id', pixelId)
      .limit(1)
      .maybeSingle()
      .then(({ data, error }) => {
        if (cancelled) return
        const row = (data ?? null) as Record<string, unknown> | null
        const result: ClientConfig = {
          name:     (row?.name as string) || pixelId,
          channels: error ? ALL : channelsFromRow(row),
        }
        if (row) _cache.set(pixelId, { config: result, fetchedAt: Date.now() })
        setConfig(result)
        setReady(true)
      })
    return () => { cancelled = true }
  }, [pixelId])

  return { config, ready }
}
