import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchPortfolios } from '../api/portfolios'
import { FullPageSpinner } from '../components/LoadingSpinner'
import type { ModelPortfolioListItem, PortfolioBacktestSummary } from '../types'

function money(value: number): string {
  return `$${value.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
}

function pct(value: number | null): string {
  if (value == null) return 'n/a'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function ratio(value: number | null): string {
  return value == null ? 'n/a' : value.toFixed(2)
}

function sourceLabel(backtest: PortfolioBacktestSummary | null): string {
  const source = backtest?.assumptions_json?.data_source
  if (source === 'daily_snapshot') return 'Daily snapshot'
  if (source === 'aggregate_metric_proxy') return 'Limited data'
  return 'No backtest'
}

export function PortfoliosPage() {
  const [portfolios, setPortfolios] = useState<ModelPortfolioListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    setLoading(true)
    setError(false)
    fetchPortfolios()
      .then(setPortfolios)
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <FullPageSpinner />

  if (error) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-tg-hint">
        Failed to load portfolios
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto pb-20">
      <div className="px-4 pb-3 pt-4">
        <h1 className="text-lg font-semibold text-tg-text">Portfolios</h1>
      </div>

      {portfolios.length === 0 ? (
        <div className="px-6 pt-12 text-center text-sm text-tg-hint">
          No model portfolios available
        </div>
      ) : (
        <div className="space-y-3 px-4">
          {portfolios.map((portfolio) => (
            <PortfolioCard key={portfolio.id} portfolio={portfolio} />
          ))}
        </div>
      )}
    </div>
  )
}

function PortfolioCard({ portfolio }: { portfolio: ModelPortfolioListItem }) {
  const version = portfolio.current_version
  const backtest = portfolio.latest_backtest
  const disabled = version == null

  return (
    <Link
      to={disabled ? '/portfolios' : `/portfolios/${portfolio.slug}`}
      onClick={(event) => {
        if (disabled) event.preventDefault()
      }}
      className={`block rounded-xl px-4 py-3 transition-colors ${
        disabled ? 'opacity-70' : 'active:opacity-80'
      }`}
      style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-base font-semibold text-tg-text">
              {portfolio.name}
            </h2>
            <span className="shrink-0 rounded-md bg-tg-button px-1.5 py-0.5 text-[10px] font-semibold uppercase text-tg-button-text">
              {portfolio.risk_profile}
            </span>
          </div>
          <p className="mt-1 text-xs text-tg-hint">
            {version
              ? `v${version.version_no} · ${version.trader_count} traders · ${portfolio.rebalance_cadence}`
              : 'Awaiting published version'}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-xs font-medium text-tg-text">
            {money(portfolio.monthly_price_usd)}/mo
          </div>
          <div className="mt-0.5 text-[10px] text-tg-hint">
            min {money(portfolio.min_equity_usd)}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-2 text-center">
        <Metric label="Return" value={pct(backtest?.total_return_pct ?? null)} />
        <Metric label="Drawdown" value={pct(backtest?.max_drawdown_pct ?? null)} />
        <Metric label="Sharpe" value={ratio(backtest?.sharpe_ratio ?? null)} />
        <Metric label="Source" value={sourceLabel(backtest)} compact />
      </div>
    </Link>
  )
}

function Metric({
  label,
  value,
  compact = false,
}: {
  label: string
  value: string
  compact?: boolean
}) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] text-tg-hint">{label}</div>
      <div
        className={`truncate font-semibold text-tg-text ${
          compact ? 'text-[11px]' : 'text-xs'
        }`}
      >
        {value}
      </div>
    </div>
  )
}
