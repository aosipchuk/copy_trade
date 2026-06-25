import type { ClosedTradeItem, EquityPoint, PositionItem, TraderSummary } from '../types'

// Periods rendered in the metrics section, in display order.
const PERIODS = ['day', 'week', 'month', 'allTime'] as const

// Metric columns for the performance section: [CSV header, TraderStats key].
const METRIC_COLUMNS: [string, keyof TraderSummary['stats'][string]][] = [
  ['PnL USD', 'pnl_usd'],
  ['ROI %', 'roi_pct'],
  ['Volume USD', 'volume_usd'],
  ['Win Rate %', 'win_rate_pct'],
  ['Max Drawdown %', 'max_drawdown_pct'],
  ['Max Drawdown USD', 'max_drawdown_usd'],
  ['Trades', 'trade_count'],
  ['Sharpe', 'sharpe_ratio'],
  ['Sortino', 'sortino_ratio'],
  ['Calmar', 'calmar_ratio'],
  ['Profit Factor', 'profit_factor'],
  ['Avg PnL/Trade', 'avg_pnl_per_trade'],
  ['Avg Trade Duration (hrs)', 'avg_trade_duration_hrs'],
  ['Max Losing Streak', 'max_losing_streak'],
  ['Profitable Days %', 'profitable_days_pct'],
  ['Avg Trades/Day', 'avg_trades_per_day'],
  ['Long Ratio %', 'long_ratio_pct'],
  ['Avg Position Size USD', 'avg_position_size_usd'],
  ['Fees Paid USD', 'fees_paid_usd'],
  ['Composite Score', 'composite_score'],
]

/** Escape a single CSV field per RFC 4180 (always quoted for safety). */
function csvField(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '""'
  return `"${String(value).replace(/"/g, '""')}"`
}

function csvRow(values: (string | number | null | undefined)[]): string {
  return values.map(csvField).join(',')
}

function buildCsv(
  summary: TraderSummary,
  positions: PositionItem[],
  trades: ClosedTradeItem[],
  equityCurve: EquityPoint[],
): string {
  const rows: string[] = []

  // Header / metadata
  rows.push(csvRow(['Trader Portfolio Export']))
  rows.push(csvRow(['Trader', summary.display_name ?? summary.hl_address]))
  rows.push(csvRow(['Address', summary.hl_address]))
  rows.push(csvRow(['Exported', new Date().toISOString()]))
  rows.push('')

  // Performance metrics — one row per period
  rows.push(csvRow(['Performance Metrics']))
  rows.push(csvRow(['Period', ...METRIC_COLUMNS.map(([label]) => label)]))
  for (const period of PERIODS) {
    const stat = summary.stats[period]
    if (!stat) continue
    rows.push(csvRow([period, ...METRIC_COLUMNS.map(([, key]) => stat[key])]))
  }
  rows.push('')

  // Open positions
  rows.push(csvRow(['Open Positions']))
  rows.push(csvRow(['Coin', 'Side', 'Size', 'Entry Price', 'Unrealized PnL', 'Leverage']))
  for (const p of positions) {
    rows.push(csvRow([p.coin, p.side, p.size, p.entry_px, p.unrealized_pnl, p.leverage]))
  }
  rows.push('')

  // Closed trades
  rows.push(csvRow(['Closed Trades']))
  rows.push(csvRow(['Coin', 'Direction', 'Size', 'Avg Price', 'PnL', 'Time', 'Fills']))
  for (const t of trades) {
    rows.push(csvRow([t.coin, t.direction, t.size, t.avg_px, t.pnl, new Date(t.time).toISOString(), t.fill_count]))
  }
  rows.push('')

  // Equity curve (week)
  rows.push(csvRow(['Equity Curve (week)']))
  rows.push(csvRow(['Timestamp', 'PnL', 'ROI']))
  for (const pt of equityCurve) {
    rows.push(csvRow([pt.ts, pt.pnl, pt.roi]))
  }

  return rows.join('\r\n')
}

/** Build a portfolio CSV and trigger a client-side download. */
export function exportPortfolioCsv(
  summary: TraderSummary,
  positions: PositionItem[],
  trades: ClosedTradeItem[],
  equityCurve: EquityPoint[],
): void {
  const csv = buildCsv(summary, positions, trades, equityCurve)
  // Prepend UTF-8 BOM so Excel detects encoding correctly.
  const blob = new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)

  const slug = (summary.display_name ?? summary.hl_address).slice(0, 16).replace(/[^a-zA-Z0-9_-]/g, '_')
  const date = new Date().toISOString().slice(0, 10)

  const a = document.createElement('a')
  a.href = url
  a.download = `portfolio_${slug}_${date}.csv`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
