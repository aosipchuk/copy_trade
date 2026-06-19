export const fmt = {
  usd(v: number): string {
    const abs = Math.abs(v)
    const sign = v < 0 ? '-' : '+'
    if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`
    if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(1)}K`
    return `${sign}$${abs.toFixed(2)}`
  },

  compact(v: number): string {
    if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
    if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`
    return `$${v.toFixed(0)}`
  },

  qty(v: number): string {
    if (v >= 1_000) return v.toLocaleString('en-US', { maximumFractionDigits: 0 })
    if (v >= 1) return v.toPrecision(4).replace(/\.?0+$/, '')
    return v.toPrecision(3)
  },

  pct(v: number): string {
    return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
  },

  price(v: number): string {
    if (v >= 1_000) return v.toLocaleString('en-US', { maximumFractionDigits: 1 })
    if (v >= 1) return v.toFixed(4)
    return v.toFixed(6)
  },

  date(iso: string): string {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  },
}
