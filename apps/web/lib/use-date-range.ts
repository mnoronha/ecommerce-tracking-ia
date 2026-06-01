/**
 * useDateRange — hook que persiste a seleção de período no localStorage.
 * Padrão: '7d'. Persiste até o usuário alterar manualmente.
 *
 * Uso:
 *   const { period, setPeriod, days } = useDateRange()
 */

import { useState, useEffect } from 'react'

export type Period = '1d' | '7d' | '14d' | '30d' | '90d'

const STORAGE_KEY = 'dash_period'
const DEFAULT: Period = '7d'

export const PERIOD_DAYS: Record<Period, number> = {
  '1d':  1,
  '7d':  7,
  '14d': 14,
  '30d': 30,
  '90d': 90,
}

export const PERIOD_LABEL: Record<Period, string> = {
  '1d':  'Ontem',
  '7d':  'Últimos 7 dias',
  '14d': 'Últimos 14 dias',
  '30d': 'Últimos 30 dias',
  '90d': 'Últimos 90 dias',
}

function readStorage(): Period {
  if (typeof window === 'undefined') return DEFAULT
  try {
    const v = window.localStorage.getItem(STORAGE_KEY)
    if (v && v in PERIOD_DAYS) return v as Period
  } catch (_) {}
  return DEFAULT
}

export function useDateRange() {
  const [period, setPeriodState] = useState<Period>(DEFAULT)

  // Read from localStorage on mount (client only)
  useEffect(() => {
    setPeriodState(readStorage())
  }, [])

  function setPeriod(p: Period) {
    setPeriodState(p)
    try { window.localStorage.setItem(STORAGE_KEY, p) } catch (_) {}
  }

  return {
    period,
    setPeriod,
    days:  PERIOD_DAYS[period],
    label: PERIOD_LABEL[period],
  }
}
