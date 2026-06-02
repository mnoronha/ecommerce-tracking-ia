'use client'

/**
 * Seletor de período compartilhado — mesmas opções em TODAS as páginas:
 * Ontem · 7d · 30d · 90d · Custom.
 *
 * É puramente apresentacional; o estado persistido vive em `useDatePeriod()`.
 * Passe os valores e setters do hook:
 *
 *   const { period, from, to, setPreset, setCustom } = useDatePeriod()
 *   <PeriodPicker period={period} from={from} to={to}
 *      onPreset={setPreset} onCustom={setCustom} />
 */

import { useState, useEffect } from 'react'
import { Period, PRESETS, PERIOD_LABEL } from '@/lib/use-date-range'

export function PeriodPicker({
  period,
  from,
  to,
  onPreset,
  onCustom,
  className = '',
}: {
  period: Period
  from: string
  to: string
  onPreset: (p: Period) => void
  onCustom: (from: string, to: string) => void
  className?: string
}) {
  // Buffer local de edição das datas custom (só aplica no clique em "Aplicar").
  const [draftFrom, setDraftFrom] = useState(from)
  const [draftTo,   setDraftTo]   = useState(to)

  useEffect(() => { setDraftFrom(from); setDraftTo(to) }, [from, to])

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div className="flex gap-1 bg-[#1a1f2e] rounded-lg p-1 border border-[#2a2f3e]">
        {PRESETS.map(p => (
          <button
            key={p}
            onClick={() => onPreset(p)}
            className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
              period === p ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-white'
            }`}
          >
            {PERIOD_LABEL[p]}
          </button>
        ))}
      </div>

      {period === 'custom' && (
        <div className="flex items-center gap-1.5">
          <input
            type="date"
            value={draftFrom}
            onChange={e => setDraftFrom(e.target.value)}
            className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-2 py-1 text-xs text-slate-200 outline-none focus:border-indigo-500"
          />
          <span className="text-slate-500 text-xs">–</span>
          <input
            type="date"
            value={draftTo}
            onChange={e => setDraftTo(e.target.value)}
            className="bg-[#1a1f2e] border border-[#2a2f3e] rounded-lg px-2 py-1 text-xs text-slate-200 outline-none focus:border-indigo-500"
          />
          <button
            onClick={() => onCustom(draftFrom, draftTo)}
            disabled={!draftFrom || !draftTo}
            className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 text-white px-3 py-1 rounded-lg text-xs font-medium transition-colors"
          >
            Aplicar
          </button>
        </div>
      )}
    </div>
  )
}
