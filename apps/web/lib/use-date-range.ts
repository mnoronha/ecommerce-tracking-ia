/**
 * Período global do dashboard — fonte única de verdade.
 *
 * A seleção (preset + datas custom) é persistida em localStorage e
 * compartilhada por TODAS as páginas que têm filtro de período. Quando o
 * usuário muda o período numa página, ele passa a valer em todas as demais
 * até ser alterado de novo.
 *
 * Opções idênticas em todo lugar: Ontem · 7d · 30d · 90d · Custom.
 *
 * Uso:
 *   const { period, from, to, setPreset, setCustom } = useDatePeriod()
 *   <PeriodPicker period={period} from={from} to={to}
 *      onPreset={setPreset} onCustom={setCustom} />
 */

import { useState, useEffect, useCallback } from 'react'

export type Period = '1d' | '7d' | '30d' | '90d' | 'custom'

const KEY  = 'dash_period'
const FROM = 'dash_from'
const TO   = 'dash_to'
const DEFAULT: Period = '7d'

/** Ordem dos botões — igual em todas as páginas. */
export const PRESETS: Period[] = ['1d', '7d', '30d', '90d', 'custom']

/** Rótulos curtos exibidos nos botões. */
export const PERIOD_LABEL: Record<Period, string> = {
  '1d':     'Ontem',
  '7d':     '7d',
  '30d':    '30d',
  '90d':    '90d',
  'custom': 'Custom',
}

export const PERIOD_DAYS: Record<Exclude<Period, 'custom'>, number> = {
  '1d': 1, '7d': 7, '30d': 30, '90d': 90,
}

function isPeriod(v: unknown): v is Period {
  return v === '1d' || v === '7d' || v === '30d' || v === '90d' || v === 'custom'
}

/** Dias equivalentes de um preset (custom cai no default de 7). */
function presetDays(period: Period): number {
  return period === 'custom' ? PERIOD_DAYS['7d'] : PERIOD_DAYS[period]
}

function yesterdayStr(): string {
  const d = new Date()
  d.setDate(d.getDate() - 1)
  return d.toISOString().slice(0, 10)
}

export function readPeriod(): Period {
  if (typeof window === 'undefined') return DEFAULT
  try {
    const v = window.localStorage.getItem(KEY)
    if (isPeriod(v)) return v   // legacy '14d' cai no default
  } catch (_) {}
  return DEFAULT
}

export function readCustom(): { from: string; to: string } {
  if (typeof window === 'undefined') return { from: '', to: '' }
  try {
    return {
      from: window.localStorage.getItem(FROM) || '',
      to:   window.localStorage.getItem(TO)   || '',
    }
  } catch (_) {
    return { from: '', to: '' }
  }
}

/**
 * Query string para endpoints que aceitam `days` OU `start`/`end`.
 * - Ontem    → start=Y&end=Y (apenas o dia de ontem)
 * - Custom   → start=from&end=to
 * - 7/30/90d → days=N
 */
export function periodToQuery(period: Period, from: string, to: string): string {
  if (period === '1d') {
    const y = yesterdayStr()
    return `start=${y}&end=${y}`
  }
  if (period === 'custom' && from && to) return `start=${from}&end=${to}`
  return `days=${presetDays(period)}`
}

/** Intervalo concreto (objetos Date locais) para queries diretas no Supabase. */
export function periodToRange(period: Period, from: string, to: string): { start: Date; end: Date; days: number } {
  const now = new Date()
  let start: Date, end: Date
  if (period === 'custom' && from && to) {
    start = new Date(from + 'T00:00:00')
    end   = new Date(to   + 'T23:59:59')
  } else if (period === '1d') {
    start = new Date(now); start.setDate(start.getDate() - 1); start.setHours(0, 0, 0, 0)
    end   = new Date(start); end.setHours(23, 59, 59, 999)
  } else {
    const d = presetDays(period)
    start = new Date(); start.setDate(start.getDate() - d)
    end   = now
  }
  const days = Math.max(1, Math.ceil((end.getTime() - start.getTime()) / 86400000))
  return { start, end, days }
}

/** Rótulo longo para subtítulos ("Últimos 30 dias", "Ontem", "06/01 → 06/02"). */
export function periodLabelLong(period: Period, from: string, to: string): string {
  if (period === '1d') return 'Ontem'
  if (period === 'custom' && from && to) {
    const f = (s: string) => s.slice(5).replace('-', '/')
    return `${f(from)} → ${f(to)}`
  }
  return `Últimos ${presetDays(period)} dias`
}

export function useDatePeriod() {
  // Inicialização síncrona a partir do localStorage — evita um segundo
  // carregamento e mantém o período já na primeira renderização.
  const [period, setPeriodState] = useState<Period>(() => readPeriod())
  const [from, setFrom] = useState<string>(() => readCustom().from)
  const [to,   setTo]   = useState<string>(() => readCustom().to)

  // Sincroniza entre abas / outras instâncias que alterem o período.
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === KEY) setPeriodState(readPeriod())
      if (e.key === FROM || e.key === TO) {
        const c = readCustom(); setFrom(c.from); setTo(c.to)
      }
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const setPreset = useCallback((p: Period) => {
    setPeriodState(p)
    try { window.localStorage.setItem(KEY, p) } catch (_) {}
  }, [])

  const setCustom = useCallback((f: string, t: string) => {
    setFrom(f); setTo(t); setPeriodState('custom')
    try {
      window.localStorage.setItem(FROM, f)
      window.localStorage.setItem(TO, t)
      window.localStorage.setItem(KEY, 'custom')
    } catch (_) {}
  }, [])

  return { period, from, to, setPreset, setCustom }
}
