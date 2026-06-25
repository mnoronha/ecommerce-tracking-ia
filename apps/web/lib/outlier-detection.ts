export type OutlierResult = {
  isOutlier: boolean
  direction: 'positive' | 'negative' | null
  magnitude: 'extreme' | 'high' | null
  percentile: number
}

export function detectOutlier(value: number, allValues: number[]): OutlierResult {
  const valid = allValues.filter(v => Number.isFinite(v))
  if (valid.length < 3) {
    return { isOutlier: false, direction: null, magnitude: null, percentile: 0.5 }
  }
  const sorted = [...valid].sort((a, b) => a - b)
  const n    = sorted.length
  const below = sorted.filter(v => v < value).length
  const percentile = below / n
  const mean = valid.reduce((s, v) => s + v, 0) / n

  if (percentile >= 0.99 || (mean > 0 && value >= 3 * mean)) {
    return { isOutlier: true, direction: 'positive', magnitude: 'extreme', percentile }
  }
  if (percentile >= 0.90 || (mean > 0 && value >= 2 * mean)) {
    return { isOutlier: true, direction: 'positive', magnitude: 'high', percentile }
  }
  if (percentile <= 0.01 || (mean > 0 && value > 0 && value <= 0.3 * mean)) {
    return { isOutlier: true, direction: 'negative', magnitude: 'extreme', percentile }
  }
  if (percentile <= 0.10 || (mean > 0 && value > 0 && value <= 0.5 * mean)) {
    return { isOutlier: true, direction: 'negative', magnitude: 'high', percentile }
  }
  return { isOutlier: false, direction: null, magnitude: null, percentile }
}
