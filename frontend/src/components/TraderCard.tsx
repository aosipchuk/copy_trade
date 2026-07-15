import { useNavigate } from 'react-router-dom'
import type { SortKey, TraderListItem, TraderStats } from '../types'
import { fmt } from '../utils/format'

function VerifiedBadge() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      {/* Rounded 8-pointed rosette (Telegram-style verification seal) */}
      <path
        d="M8 1L9.72 3.84L12.95 3.05L12.16 6.28L15 8L12.16 9.72L12.95 12.95L9.72 12.16L8 15L6.28 12.16L3.05 12.95L3.84 9.72L1 8L3.84 6.28L3.05 3.05L6.28 3.84Z"
        fill="currentColor"
      />
      <path
        d="M5 8.5L7 10.5L11 6"
        stroke="white"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

interface Props {
  trader: TraderListItem
  sort: SortKey
  isRealSubscribed?: boolean
  isDemoSubscribed?: boolean
}

function shortenAddress(addr: string) {
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`
}

function primaryMetric(stat: TraderStats, sort: SortKey): { label: string; value: string; positive?: boolean } {
  if (sort === 'pnl') {
    return {
      label: 'PnL',
      value: fmt.usd(stat.pnl_usd),
      positive: stat.pnl_usd == null ? undefined : stat.pnl_usd >= 0,
    }
  }
  if (sort === 'volume') {
    return {
      label: 'Vol',
      value: fmt.compact(stat.volume_usd),
      positive: stat.volume_usd == null ? undefined : true,
    }
  }
  return {
    label: 'ROI',
    value: fmt.pct(stat.roi_pct),
    positive: stat.roi_pct == null ? undefined : stat.roi_pct >= 0,
  }
}

function secondaryMetrics(stat: TraderStats, sort: SortKey): string[] {
  if (sort === 'pnl') return [`ROI ${fmt.pct(stat.roi_pct)}`, `Vol ${fmt.compact(stat.volume_usd)}`]
  if (sort === 'volume') return [`ROI ${fmt.pct(stat.roi_pct)}`, `PnL ${fmt.usd(stat.pnl_usd)}`]
  return [`PnL ${fmt.usd(stat.pnl_usd)}`, `Vol ${fmt.compact(stat.volume_usd)}`]
}

function isVerified(stat: TraderStats): boolean {
  return (
    stat.win_rate_pct != null &&
    stat.trade_count != null &&
    stat.win_rate_pct > 50 &&
    stat.trade_count > 20
  )
}

function qualityLine(stat: TraderStats): string | null {
  if (stat.win_rate_pct == null && stat.trade_count == null && stat.max_drawdown_pct == null) {
    return null
  }
  const parts: string[] = []
  if (stat.win_rate_pct != null) parts.push(`Win ${stat.win_rate_pct.toFixed(0)}%`)
  if (stat.max_drawdown_pct != null) parts.push(`DD -${stat.max_drawdown_pct.toFixed(0)}%`)
  if (stat.trade_count != null) parts.push(`${stat.trade_count} trades`)
  return parts.join(' · ')
}

export function TraderCard({ trader, sort, isRealSubscribed = false, isDemoSubscribed = false }: Props) {
  const navigate = useNavigate()
  const stat = trader.stats[0]
  const verified = stat ? isVerified(stat) : false
  const quality = stat ? qualityLine(stat) : null

  return (
    <button
      className="w-full text-left px-4 py-3 flex items-center gap-3 border-b border-gray-100 dark:border-gray-800 active:bg-tg-secondary transition-colors"
      onClick={() => navigate(`/traders/${trader.id}`)}
    >
      {/* Avatar with optional subscribed indicator */}
      <div className="relative flex-shrink-0">
        <div className="w-10 h-10 rounded-full bg-tg-secondary flex items-center justify-center text-sm font-bold text-tg-hint">
          {(trader.display_name ?? trader.hl_address).slice(0, 2).toUpperCase()}
        </div>
        {isDemoSubscribed && (
          <span
            className="absolute bottom-0 left-0 w-3 h-3 rounded-full border-2 border-white dark:border-gray-900"
            style={{ background: '#7c3aed' }}
          />
        )}
        {isRealSubscribed && (
          <span
            className="absolute bottom-0 right-0 w-3 h-3 rounded-full border-2 border-white dark:border-gray-900"
            style={{ background: 'var(--tg-theme-button-color)' }}
          />
        )}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="font-medium text-tg-text text-sm truncate">
              {trader.display_name ?? shortenAddress(trader.hl_address)}
            </span>
            {verified && (
              <span
                className="flex-shrink-0"
                style={{ color: 'var(--tg-theme-button-color)' }}
              >
                <VerifiedBadge />
              </span>
            )}
          </div>
          {stat && (() => {
            const primary = primaryMetric(stat, sort)
            return (
              <span
                className={`text-sm font-semibold ml-2 flex-shrink-0 ${
                  primary.positive === undefined
                    ? 'text-tg-text'
                    : primary.positive
                      ? 'text-green-500'
                      : 'text-red-500'
                }`}
              >
                {primary.value}
              </span>
            )
          })()}
        </div>
        {stat && (
          <div className="flex gap-3 mt-0.5 text-xs text-tg-hint">
            {secondaryMetrics(stat, sort).map((m) => (
              <span key={m}>{m}</span>
            ))}
          </div>
        )}
        {quality && (
          <div className="mt-0.5 text-xs text-tg-hint opacity-75">
            {quality}
          </div>
        )}
      </div>

      <span className="text-tg-hint text-sm flex-shrink-0">›</span>
    </button>
  )
}
