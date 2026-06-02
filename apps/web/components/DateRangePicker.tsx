'use client'

/**
 * @deprecated Use `PeriodPicker` + `useDatePeriod()` diretamente.
 * Mantido como wrapper self-contained para imports legados.
 */

import { useDatePeriod } from '@/lib/use-date-range'
import { PeriodPicker } from './PeriodPicker'

export function DateRangePicker() {
  const { period, from, to, setPreset, setCustom } = useDatePeriod()
  return (
    <PeriodPicker period={period} from={from} to={to} onPreset={setPreset} onCustom={setCustom} />
  )
}
