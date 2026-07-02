import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { fetchPortfolio } from '../api/portfolios'
import { FullPageSpinner } from '../components/LoadingSpinner'
import { useBackButton } from '../hooks/useTelegram'
import type {
  ModelPortfolioAllocation,
  ModelPortfolioDetail,
  PortfolioBacktest,
} from '../types'

function money(value: number | null | undefined): string {
  if (value == null) return 'n/a'
  return `$${value.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
}

function pct(value: number | null | undefined, signed = true): string {
  if (value == null) return 'n/a'
  const sign = signed && value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

function ratio(value: number | null | undefined): string {
  return value == null ? 'n/a' : value.toFixed(2)
}

function dateText(value: string | null | undefined): string {
  if (!value) return 'n/a'
  return new Date(value).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function sourceText(value: unknown): string {
  if (value === 'daily_snapshot') return 'Daily snapshot'
  if (value === 'aggregate_metric_proxy') return 'Limited data proxy'
  return 'Unknown'
}

function numberMetric(
  metrics: Record<string, unknown> | null,
  key: string,
): number | null {
  const value = metrics?.[key]
  return typeof value === 'number' ? value : null
}

export function PortfolioDetailPage() {
  const { slug } = useParams<{ slug: string }>()
  const navigate = useNavigate()
  const [portfolio, setPortfolio] = useState<ModelPortfolioDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const navigateBack = useCallback(() => navigate('/portfolios'), [navigate])
  useBackButton(navigateBack)

  useEffect(() => {
    if (!slug) return
    setLoading(true)
    setError(null)
    fetchPortfolio(slug)
      .then(setPortfolio)
      .catch((err: unknown) => {
        const status = (err as { response?: { status?: number } })?.response?.status
        setError(status === 404 ? 'not_found' : 'error')
      })
      .finally(() => setLoading(false))
  }, [slug])

  const primaryBacktest = useMemo(
    () => portfolio?.backtests[0] ?? null,
    [portfolio],
  )

  if (loading) return <FullPageSpinner />

  if (!portfolio) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center text-tg-hint">
        <p className="text-sm">
          {error === 'not_found' ? 'Portfolio not found' : 'Failed to load portfolio'}
        </p>
        <button className="text-sm text-tg-button underline" onClick={navigateBack}>
          Go back
        </button>
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto pb-24">
      <div className="border-b border-gray-100 px-4 pb-3 pt-4 dark:border-gray-800">
        <button className="mb-3 text-xs text-tg-hint" onClick={navigateBack}>
          Back
        </button>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold text-tg-text">
              {portfolio.name}
            </h1>
            <p className="mt-1 text-xs text-tg-hint">
              v{portfolio.current_version.version_no} ·{' '}
              {portfolio.current_version.allocations.length} traders ·{' '}
              {portfolio.rebalance_cadence}
            </p>
          </div>
          <div className="shrink-0 rounded-md bg-tg-button px-2 py-1 text-[10px] font-semibold uppercase text-tg-button-text">
            {portfolio.risk_profile}
          </div>
        </div>
      </div>

      <div className="space-y-4 px-4 pt-4">
        <VersionStats portfolio={portfolio} backtest={primaryBacktest} />
        <AllocationsList allocations={portfolio.current_version.allocations} />
        <BacktestPanel backtest={primaryBacktest} />
      </div>
    </div>
  )
}

function VersionStats({
  portfolio,
  backtest,
}: {
  portfolio: ModelPortfolioDetail
  backtest: PortfolioBacktest | null
}) {
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl bg-gray-100 dark:bg-gray-800">
      <StatCell label="Min equity" value={money(portfolio.min_equity_usd)} />
      <StatCell label="Price" value={`${money(portfolio.monthly_price_usd)}/mo`} />
      <StatCell label="Return" value={pct(backtest?.total_return_pct)} />
      <StatCell label="Drawdown" value={pct(backtest?.max_drawdown_pct, false)} />
      <StatCell label="Sharpe" value={ratio(backtest?.sharpe_ratio)} />
      <StatCell label="Weight sum" value={`${portfolio.current_version.allocations.reduce((acc, item) => acc + item.target_weight_pct, 0).toFixed(3)}%`} />
    </div>
  )
}

function StatCell({ label, value }: { label: string; value: string }) {
  return (
    <div
      className="min-w-0 px-3 py-2"
      style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
    >
      <div className="truncate text-[10px] text-tg-hint">{label}</div>
      <div className="truncate text-sm font-semibold text-tg-text">{value}</div>
    </div>
  )
}

function AllocationsList({
  allocations,
}: {
  allocations: ModelPortfolioAllocation[]
}) {
  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-tg-text">Allocation</h2>
        <span className="text-xs text-tg-hint">{allocations.length} traders</span>
      </div>
      <div className="space-y-2">
        {allocations.map((allocation) => (
          <AllocationRow key={allocation.id} allocation={allocation} />
        ))}
      </div>
    </section>
  )
}

function AllocationRow({
  allocation,
}: {
  allocation: ModelPortfolioAllocation
}) {
  const metrics = allocation.source_metrics
  const drawdown = numberMetric(metrics, 'max_drawdown_pct')
  const leverage = numberMetric(metrics, 'avg_leverage')
  const score = allocation.portfolio_score

  return (
    <div
      className="rounded-xl px-3 py-3"
      style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
    >
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-tg-text">
            {allocation.trader_display_name ?? allocation.trader_address.slice(0, 10)}
          </div>
          <div className="truncate font-mono text-[10px] text-tg-hint">
            {allocation.trader_address}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-sm font-semibold text-tg-text">
            {allocation.target_weight_pct.toFixed(3)}%
          </div>
          <div className="text-[10px] text-tg-hint">
            Copy {allocation.copy_ratio_pct.toFixed(0)}%
          </div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-center">
        <MiniMetric label="Score" value={ratio(score)} />
        <MiniMetric label="Drawdown" value={pct(drawdown, false)} />
        <MiniMetric label="Leverage" value={leverage == null ? 'n/a' : `${leverage.toFixed(2)}x`} />
      </div>

      {allocation.reason_text && (
        <p className="mt-2 text-xs leading-snug text-tg-hint">
          {allocation.reason_text}
        </p>
      )}
    </div>
  )
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] text-tg-hint">{label}</div>
      <div className="truncate text-xs font-semibold text-tg-text">{value}</div>
    </div>
  )
}

function BacktestPanel({ backtest }: { backtest: PortfolioBacktest | null }) {
  if (!backtest) {
    return (
      <section>
        <h2 className="mb-2 text-sm font-semibold text-tg-text">Backtest</h2>
        <div
          className="rounded-xl px-3 py-3 text-sm text-tg-hint"
          style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
        >
          No backtest saved for this version
        </div>
      </section>
    )
  }

  const assumptions = backtest.assumptions_json
  const limitations = Array.isArray(assumptions.limitations)
    ? assumptions.limitations.filter((item): item is string => typeof item === 'string')
    : []

  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-tg-text">Backtest</h2>
        <span className="text-xs text-tg-hint">
          {backtest.period_days}d · {money(backtest.initial_equity_usd)}
        </span>
      </div>

      <div
        className="rounded-xl px-3 py-3"
        style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
      >
        <div className="grid grid-cols-3 gap-2 text-center">
          <MiniMetric label="Return" value={pct(backtest.total_return_pct)} />
          <MiniMetric label="Win rate" value={pct(backtest.win_rate_pct, false)} />
          <MiniMetric label="Sortino" value={ratio(backtest.sortino_ratio)} />
          <MiniMetric label="Fees" value={money(backtest.fees_usd)} />
          <MiniMetric label="Slippage" value={money(backtest.slippage_usd)} />
          <MiniMetric label="Missed" value={String(backtest.missed_trade_count)} />
        </div>

        <div className="mt-3 space-y-1 border-t border-gray-200 pt-3 text-xs text-tg-hint dark:border-gray-700">
          <Assumption label="Source" value={sourceText(assumptions.data_source)} />
          <Assumption label="Created" value={dateText(backtest.created_at)} />
          <Assumption label="Fees" value={`${assumptions.fees_bps ?? 'n/a'} bps`} />
          <Assumption label="Slippage" value={`${assumptions.slippage_bps ?? 'n/a'} bps`} />
          <Assumption label="Minimum order" value={money(Number(assumptions.minimum_order_size_usd ?? 0))} />
        </div>

        {limitations.length > 0 && (
          <div className="mt-3 space-y-1 text-xs leading-snug text-tg-hint">
            {limitations.map((item) => (
              <p key={item}>{item}</p>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

function Assumption({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="shrink-0">{label}</span>
      <span className="min-w-0 truncate text-right text-tg-text">{value}</span>
    </div>
  )
}
