'use client'

import { Period, PERIOD_LABEL, useDateRange } from '@/lib/use-date-range'

const PRESETS: Period[] = ['1d', '7d', '14d', '30d', '90d']

export function DateRangePicker() {
  const { period, setPeriod } = useDateRange()

  return (
    <div className="flex items-center gap-1 bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg p-1">
      {PRESETS.map(p => (
        <button
          key={p}
          onClick={() => setPeriod(p)}
          className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
            period === p
              ? 'bg-indigo-600 text-white'
              : 'text-slate-400 hover:text-white hover:bg-[#252a3a]'
          }`}
        >
          {PERIOD_LABEL[p]}
        </button>
      ))}
    </div>
  )
}
