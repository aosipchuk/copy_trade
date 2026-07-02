import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  applyPortfolioRebalance,
  activateDemoPortfolio,
  activateLivePortfolio,
  cancelPortfolioSubscription,
  createPortfolioBillingCheckout,
  fetchPortfolio,
  fetchPortfolioBillingStatus,
  fetchPortfolioRebalanceHistory,
  fetchPortfolioSubscriptions,
  previewPortfolioRebalance,
  updatePortfolioSubscription,
} from '../api/portfolios'
import { fetchAgentStatus } from '../api/wallet'
import { FullPageSpinner } from '../components/LoadingSpinner'
import { useBackButton } from '../hooks/useTelegram'
import type {
  AgentStatus,
  ModelPortfolioAllocation,
  ModelPortfolioDetail,
  PortfolioBillingStatus,
  PortfolioActivationConflict,
  PortfolioBacktest,
  PortfolioRebalanceEvent,
  PortfolioRebalancePreview,
  UserPortfolioSubscriptionDetail,
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
  const [portfolioSubscription, setPortfolioSubscription] =
    useState<UserPortfolioSubscriptionDetail | null>(null)
  const [livePortfolioSubscription, setLivePortfolioSubscription] =
    useState<UserPortfolioSubscriptionDetail | null>(null)
  const [billingStatus, setBillingStatus] = useState<PortfolioBillingStatus | null>(
    null,
  )
  const [walletStatus, setWalletStatus] = useState<AgentStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activationBusy, setActivationBusy] = useState(false)
  const [activationError, setActivationError] = useState<string | null>(null)
  const [activationNotice, setActivationNotice] = useState<string | null>(null)
  const [liveActivationBusy, setLiveActivationBusy] = useState(false)
  const [liveActivationError, setLiveActivationError] = useState<string | null>(null)
  const [liveActivationNotice, setLiveActivationNotice] = useState<string | null>(null)
  const [billingBusy, setBillingBusy] = useState(false)
  const [billingError, setBillingError] = useState<string | null>(null)
  const [billingNotice, setBillingNotice] = useState<string | null>(null)
  const [conflicts, setConflicts] = useState<PortfolioActivationConflict[]>([])

  const navigateBack = useCallback(() => navigate('/portfolios'), [navigate])
  useBackButton(navigateBack)

  useEffect(() => {
    if (!slug) return
    let canceled = false
    setLoading(true)
    setError(null)
    setPortfolio(null)
    setPortfolioSubscription(null)
    setLivePortfolioSubscription(null)
    setBillingStatus(null)
    setWalletStatus(null)
    setActivationError(null)
    setActivationNotice(null)
    setLiveActivationError(null)
    setLiveActivationNotice(null)
    setBillingError(null)
    setBillingNotice(null)
    setConflicts([])

    fetchPortfolio(slug)
      .then(async (nextPortfolio) => {
        if (canceled) return
        setPortfolio(nextPortfolio)
        const [
          subscriptionsResult,
          liveSubscriptionsResult,
          billingResult,
          walletResult,
        ] = await Promise.allSettled([
          fetchPortfolioSubscriptions({
            is_demo: true,
            portfolio_id: nextPortfolio.id,
            active_only: true,
          }),
          fetchPortfolioSubscriptions({
            is_demo: false,
            portfolio_id: nextPortfolio.id,
            active_only: true,
          }),
          fetchPortfolioBillingStatus({
            portfolio_id: nextPortfolio.id,
            active_version_id: nextPortfolio.current_version.id,
          }),
          fetchAgentStatus(),
        ])
        if (canceled) return

        if (subscriptionsResult.status === 'fulfilled') {
          setPortfolioSubscription(subscriptionsResult.value[0] ?? null)
        } else {
          setActivationError('Failed to load demo status')
        }
        if (liveSubscriptionsResult.status === 'fulfilled') {
          setLivePortfolioSubscription(
            liveSubscriptionsResult.value.find((item) => item.items.length > 0) ??
              null,
          )
        } else {
          setLiveActivationError('Failed to load live activation status')
        }
        if (billingResult.status === 'fulfilled') {
          setBillingStatus(billingResult.value)
        } else {
          setBillingError('Failed to load billing status')
        }
        if (walletResult.status === 'fulfilled') {
          setWalletStatus(walletResult.value)
        }
      })
      .catch((err: unknown) => {
        const status = (err as { response?: { status?: number } })?.response?.status
        setError(status === 404 ? 'not_found' : 'error')
      })
      .finally(() => {
        if (!canceled) setLoading(false)
      })

    return () => {
      canceled = true
    }
  }, [slug])

  const primaryBacktest = useMemo(
    () => portfolio?.backtests[0] ?? null,
    [portfolio],
  )

  const handleActivateDemo = useCallback(
    async (totalAllocationUsd: number) => {
      if (!portfolio) return
      setActivationBusy(true)
      setActivationError(null)
      setActivationNotice(null)
      setConflicts([])

      try {
        const result = await activateDemoPortfolio({
          portfolio_id: portfolio.id,
          active_version_id: portfolio.current_version.id,
          is_demo: true,
          auto_rebalance: false,
          total_allocation_usd: totalAllocationUsd,
          close_removed_positions: false,
        })
        setPortfolioSubscription(result)
        setConflicts(result.conflicts)
        setActivationNotice(result.created ? 'Demo activated' : 'Demo already active')
      } catch (err: unknown) {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response
          ?.data?.detail
        setActivationError(detail ?? 'Failed to activate demo')
      } finally {
        setActivationBusy(false)
      }
    },
    [portfolio],
  )

  const handleCancelDemo = useCallback(async () => {
    if (!portfolioSubscription) return
    setActivationBusy(true)
    setActivationError(null)
    setActivationNotice(null)

    try {
      await cancelPortfolioSubscription(portfolioSubscription.id)
      setPortfolioSubscription(null)
      setActivationNotice('Demo canceled')
      setConflicts([])
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response
        ?.data?.detail
      setActivationError(detail ?? 'Failed to cancel demo')
    } finally {
      setActivationBusy(false)
    }
  }, [portfolioSubscription])

  const handleActivateLive = useCallback(
    async (totalAllocationUsd: number, riskDisclosureAccepted: boolean) => {
      if (!portfolio) return
      setLiveActivationBusy(true)
      setLiveActivationError(null)
      setLiveActivationNotice(null)

      try {
        const result = await activateLivePortfolio({
          portfolio_id: portfolio.id,
          active_version_id: portfolio.current_version.id,
          is_demo: false,
          auto_rebalance: false,
          total_allocation_usd: totalAllocationUsd,
          close_removed_positions: false,
          risk_disclosure_accepted: riskDisclosureAccepted,
        })
        setLivePortfolioSubscription(result)
        setLiveActivationNotice(
          result.created ? 'Live portfolio activated' : 'Live portfolio already active',
        )
      } catch (err: unknown) {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response
          ?.data?.detail
        setLiveActivationError(detail ?? 'Failed to activate live portfolio')
      } finally {
        setLiveActivationBusy(false)
      }
    },
    [portfolio],
  )

  const handleCreateCheckout = useCallback(async () => {
    if (!portfolio) return
    setBillingBusy(true)
    setBillingError(null)
    setBillingNotice(null)

    try {
      const result = await createPortfolioBillingCheckout({
        portfolio_id: portfolio.id,
        active_version_id: portfolio.current_version.id,
        total_allocation_usd: Math.max(100, Math.round(portfolio.min_equity_usd)),
      })
      setBillingStatus(result.billing_status)
      setBillingNotice(result.message)
      if (result.checkout_url) {
        window.open(result.checkout_url, '_blank', 'noopener,noreferrer')
      }
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response
        ?.data?.detail
      setBillingError(detail ?? 'Failed to start payment')
    } finally {
      setBillingBusy(false)
    }
  }, [portfolio])

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
        <BillingPanel
          portfolio={portfolio}
          billingStatus={billingStatus}
          busy={billingBusy}
          error={billingError}
          notice={billingNotice}
          onCheckout={handleCreateCheckout}
        />
        <LiveActivationPanel
          portfolio={portfolio}
          portfolioSubscription={livePortfolioSubscription}
          billingStatus={billingStatus}
          walletStatus={walletStatus}
          busy={liveActivationBusy}
          error={liveActivationError}
          notice={liveActivationNotice}
          onActivate={handleActivateLive}
          onWalletSetup={() => navigate('/wallet')}
        />
        {livePortfolioSubscription && livePortfolioSubscription.status !== 'canceled' && (
          <RebalancePanel
            label="Live rebalance"
            portfolioSubscription={livePortfolioSubscription}
            onSubscriptionChange={setLivePortfolioSubscription}
          />
        )}
        <DemoActivationPanel
          portfolio={portfolio}
          portfolioSubscription={portfolioSubscription}
          conflicts={conflicts}
          busy={activationBusy}
          error={activationError}
          notice={activationNotice}
          onActivate={handleActivateDemo}
          onCancel={handleCancelDemo}
        />
        {portfolioSubscription && portfolioSubscription.status !== 'canceled' && (
          <RebalancePanel
            label="Demo rebalance"
            portfolioSubscription={portfolioSubscription}
            onSubscriptionChange={setPortfolioSubscription}
          />
        )}
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

function BillingPanel({
  portfolio,
  billingStatus,
  busy,
  error,
  notice,
  onCheckout,
}: {
  portfolio: ModelPortfolioDetail
  billingStatus: PortfolioBillingStatus | null
  busy: boolean
  error: string | null
  notice: string | null
  onCheckout: () => Promise<void>
}) {
  const status = billingStatus?.status ?? 'not_started'
  const paid = billingStatus?.paid ?? false
  const blocked = status === 'past_due' || status === 'canceled'
  const periodEnd = billingStatus?.current_period_end
  const ctaLabel =
    status === 'past_due'
      ? 'Update payment'
      : status === 'canceled'
        ? 'Restart payment'
        : 'Start payment'

  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-tg-text">Live billing</h2>
        <span
          className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase ${
            paid
              ? 'bg-green-500/10 text-green-500'
              : blocked
                ? 'bg-red-500/10 text-red-500'
                : 'bg-amber-500/10 text-amber-600'
          }`}
        >
          {paid ? 'paid' : status.replace('_', ' ')}
        </span>
      </div>

      <div
        className="rounded-lg px-3 py-3"
        style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-tg-text">
              {money(portfolio.monthly_price_usd)}/mo
            </div>
            <div className="mt-0.5 text-xs text-tg-hint">
              {portfolio.trial_days} day trial · live access gate
            </div>
          </div>
          {!paid && (
            <button
              className="shrink-0 rounded-md bg-tg-button px-3 py-2 text-xs font-semibold text-tg-button-text disabled:opacity-50"
              disabled={busy}
              onClick={() => {
                void onCheckout()
              }}
            >
              {busy ? 'Opening' : ctaLabel}
            </button>
          )}
        </div>

        <div className="mt-3 space-y-1 border-t border-gray-200 pt-3 text-xs text-tg-hint dark:border-gray-700">
          <Assumption
            label="Provider"
            value={billingStatus?.provider?.replace('_', ' ') ?? 'stripe'}
          />
          <Assumption label="Status" value={status.replace('_', ' ')} />
          <Assumption label="Period end" value={dateText(periodEnd)} />
          {billingStatus?.beta_override && (
            <Assumption label="Override" value="beta active" />
          )}
        </div>

        {blocked && (
          <div className="mt-3 rounded-md border border-red-300 px-2 py-2 text-xs leading-snug text-red-500">
            Billing must be active before live activation or rebalance.
          </div>
        )}
        {notice && <p className="mt-3 text-xs text-green-500">{notice}</p>}
        {error && <p className="mt-3 text-xs text-red-500">{error}</p>}
      </div>
    </section>
  )
}

function LiveActivationPanel({
  portfolio,
  portfolioSubscription,
  billingStatus,
  walletStatus,
  busy,
  error,
  notice,
  onActivate,
  onWalletSetup,
}: {
  portfolio: ModelPortfolioDetail
  portfolioSubscription: UserPortfolioSubscriptionDetail | null
  billingStatus: PortfolioBillingStatus | null
  walletStatus: AgentStatus | null
  busy: boolean
  error: string | null
  notice: string | null
  onActivate: (
    totalAllocationUsd: number,
    riskDisclosureAccepted: boolean,
  ) => Promise<void>
  onWalletSetup: () => void
}) {
  const defaultAllocation = Math.max(100, Math.round(portfolio.min_equity_usd))
  const [allocationInput, setAllocationInput] = useState(String(defaultAllocation))
  const [riskAccepted, setRiskAccepted] = useState(false)

  useEffect(() => {
    setAllocationInput(String(defaultAllocation))
    setRiskAccepted(false)
  }, [defaultAllocation, portfolio.id])

  const allocationUsd = Number(allocationInput)
  const allocationInvalid =
    !Number.isFinite(allocationUsd) || allocationUsd <= 10 || allocationUsd > 1_000_000
  const paid = billingStatus?.can_activate_live ?? false
  const walletReady = walletStatus?.is_active ?? false
  const canSubmit = paid && walletReady && riskAccepted && !allocationInvalid && !busy

  const previewRows = useMemo(
    () =>
      portfolio.current_version.allocations.map((allocation) => ({
        id: allocation.id,
        name: allocation.trader_display_name ?? allocation.trader_address.slice(0, 10),
        address: allocation.trader_address,
        weight: allocation.target_weight_pct,
        allocationUsd: allocationInvalid
          ? 0
          : (allocationUsd * allocation.target_weight_pct) / 100,
      })),
    [allocationInvalid, allocationUsd, portfolio.current_version.allocations],
  )

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (canSubmit) {
      void onActivate(allocationUsd, riskAccepted)
    }
  }

  if (portfolioSubscription && portfolioSubscription.status !== 'canceled') {
    return (
      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-tg-text">Live activation</h2>
          <span className="text-xs text-tg-hint">
            v{portfolioSubscription.active_version_no}
          </span>
        </div>

        <div
          className="rounded-lg px-3 py-3"
          style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
        >
          <div className="mb-3 flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-tg-text">
                {portfolioSubscription.status}
              </div>
              <div className="mt-0.5 text-xs text-tg-hint">
                {money(portfolioSubscription.total_allocation_usd)} live allocation
              </div>
            </div>
            <span className="shrink-0 rounded bg-green-500/10 px-2 py-1 text-[10px] font-semibold uppercase text-green-500">
              live
            </span>
          </div>

          <div className="divide-y divide-gray-100 dark:divide-gray-800">
            {portfolioSubscription.items.map((item) => (
              <GeneratedSubscriptionLine
                key={item.id}
                name={item.trader_display_name ?? item.trader_address ?? 'Trader'}
                address={item.trader_address}
                allocationUsd={item.target_allocation_usd}
                weight={item.target_weight_pct}
                status={item.subscription.managed_by_portfolio ? 'Managed' : 'Manual'}
              />
            ))}
          </div>

          {notice && <p className="mt-3 text-xs text-green-500">{notice}</p>}
          {error && <p className="mt-3 text-xs text-red-500">{error}</p>}
        </div>
      </section>
    )
  }

  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-tg-text">Live activation</h2>
        <span className="text-xs text-tg-hint">
          {portfolio.current_version.allocations.length} subscriptions
        </span>
      </div>

      <form
        className="rounded-lg px-3 py-3"
        style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
        onSubmit={handleSubmit}
      >
        <div className="mb-3 space-y-1 border-b border-gray-200 pb-3 text-xs text-tg-hint dark:border-gray-700">
          <Assumption label="Payment" value={paid ? 'active' : 'required'} />
          <Assumption label="Agent" value={walletReady ? 'ready' : 'setup required'} />
        </div>

        {!walletReady && (
          <button
            className="mb-3 w-full rounded-md border border-tg-button px-3 py-2 text-xs font-semibold text-tg-button"
            type="button"
            onClick={onWalletSetup}
          >
            Open wallet setup
          </button>
        )}

        <label className="block text-xs text-tg-hint" htmlFor="live-allocation">
          Live allocation
        </label>
        <div className="mt-1 flex items-center gap-2">
          <input
            id="live-allocation"
            className="min-w-0 flex-1 rounded-md border border-gray-200 bg-transparent px-3 py-2 text-sm font-semibold text-tg-text outline-none focus:border-tg-button dark:border-gray-700"
            inputMode="decimal"
            min={11}
            max={1_000_000}
            step={1}
            type="number"
            value={allocationInput}
            onChange={(event) => setAllocationInput(event.target.value)}
          />
          <button
            className="shrink-0 rounded-md bg-tg-button px-3 py-2 text-sm font-semibold text-tg-button-text disabled:opacity-50"
            disabled={!canSubmit}
            type="submit"
          >
            {busy ? 'Starting' : 'Start live'}
          </button>
        </div>

        <label className="mt-3 flex items-start gap-2 text-xs leading-snug text-tg-hint">
          <input
            className="mt-0.5"
            checked={riskAccepted}
            type="checkbox"
            onChange={(event) => setRiskAccepted(event.target.checked)}
          />
          <span>
            I understand copy trading can lose funds; historical results and
            backtests do not guarantee future returns, and execution can differ
            because of fees, slippage, liquidity, minimum order size, and latency.
          </span>
        </label>

        <div className="mt-3 divide-y divide-gray-100 dark:divide-gray-800">
          {previewRows.map((row) => (
            <GeneratedSubscriptionLine
              key={row.id}
              name={row.name}
              address={row.address}
              allocationUsd={row.allocationUsd}
              weight={row.weight}
              status="Preview"
            />
          ))}
        </div>

        {!paid && (
          <p className="mt-3 text-xs text-amber-600">
            Payment must be active before live activation.
          </p>
        )}
        {notice && <p className="mt-3 text-xs text-green-500">{notice}</p>}
        {error && <p className="mt-3 text-xs text-red-500">{error}</p>}
      </form>
    </section>
  )
}

function DemoActivationPanel({
  portfolio,
  portfolioSubscription,
  conflicts,
  busy,
  error,
  notice,
  onActivate,
  onCancel,
}: {
  portfolio: ModelPortfolioDetail
  portfolioSubscription: UserPortfolioSubscriptionDetail | null
  conflicts: PortfolioActivationConflict[]
  busy: boolean
  error: string | null
  notice: string | null
  onActivate: (totalAllocationUsd: number) => Promise<void>
  onCancel: () => Promise<void>
}) {
  const defaultAllocation = Math.max(100, Math.round(portfolio.min_equity_usd))
  const [allocationInput, setAllocationInput] = useState(String(defaultAllocation))

  useEffect(() => {
    setAllocationInput(String(defaultAllocation))
  }, [defaultAllocation, portfolio.id])

  const allocationUsd = Number(allocationInput)
  const allocationInvalid =
    !Number.isFinite(allocationUsd) || allocationUsd <= 10 || allocationUsd > 1_000_000

  const previewRows = useMemo(
    () =>
      portfolio.current_version.allocations.map((allocation) => ({
        id: allocation.id,
        name: allocation.trader_display_name ?? allocation.trader_address.slice(0, 10),
        address: allocation.trader_address,
        weight: allocation.target_weight_pct,
        allocationUsd: allocationInvalid
          ? 0
          : (allocationUsd * allocation.target_weight_pct) / 100,
      })),
    [allocationInvalid, allocationUsd, portfolio.current_version.allocations],
  )

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!allocationInvalid && !busy) {
      void onActivate(allocationUsd)
    }
  }

  if (portfolioSubscription && portfolioSubscription.status !== 'canceled') {
    return (
      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-tg-text">Demo activation</h2>
          <span className="text-xs text-tg-hint">
            v{portfolioSubscription.active_version_no}
          </span>
        </div>

        <div
          className="rounded-lg px-3 py-3"
          style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
        >
          <div className="mb-3 flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-tg-text">
                {portfolioSubscription.status}
              </div>
              <div className="mt-0.5 text-xs text-tg-hint">
                {money(portfolioSubscription.total_allocation_usd)} allocation
              </div>
            </div>
            <button
              className="shrink-0 rounded-md border border-red-400 px-3 py-1.5 text-xs font-semibold text-red-500 disabled:opacity-50"
              disabled={busy}
              onClick={() => {
                void onCancel()
              }}
            >
              Cancel
            </button>
          </div>

          <div className="divide-y divide-gray-100 dark:divide-gray-800">
            {portfolioSubscription.items.map((item) => (
              <GeneratedSubscriptionLine
                key={item.id}
                name={item.trader_display_name ?? item.trader_address ?? 'Trader'}
                address={item.trader_address}
                allocationUsd={item.target_allocation_usd}
                weight={item.target_weight_pct}
                status={item.subscription.managed_by_portfolio ? 'Managed' : 'Manual'}
              />
            ))}
          </div>

          <ConflictNotice conflicts={conflicts} />
          {notice && <p className="mt-3 text-xs text-green-500">{notice}</p>}
          {error && <p className="mt-3 text-xs text-red-500">{error}</p>}
        </div>
      </section>
    )
  }

  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-tg-text">Demo activation</h2>
        <span className="text-xs text-tg-hint">
          {portfolio.current_version.allocations.length} subscriptions
        </span>
      </div>

      <form
        className="rounded-lg px-3 py-3"
        style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
        onSubmit={handleSubmit}
      >
        <label className="block text-xs text-tg-hint" htmlFor="demo-allocation">
          Demo allocation
        </label>
        <div className="mt-1 flex items-center gap-2">
          <input
            id="demo-allocation"
            className="min-w-0 flex-1 rounded-md border border-gray-200 bg-transparent px-3 py-2 text-sm font-semibold text-tg-text outline-none focus:border-tg-button dark:border-gray-700"
            inputMode="decimal"
            min={11}
            max={1_000_000}
            step={1}
            type="number"
            value={allocationInput}
            onChange={(event) => setAllocationInput(event.target.value)}
          />
          <button
            className="shrink-0 rounded-md bg-tg-button px-3 py-2 text-sm font-semibold text-tg-button-text disabled:opacity-50"
            disabled={busy || allocationInvalid}
            type="submit"
          >
            {busy ? 'Starting' : 'Start demo'}
          </button>
        </div>

        <div className="mt-3 divide-y divide-gray-100 dark:divide-gray-800">
          {previewRows.map((row) => (
            <GeneratedSubscriptionLine
              key={row.id}
              name={row.name}
              address={row.address}
              allocationUsd={row.allocationUsd}
              weight={row.weight}
              status="Preview"
            />
          ))}
        </div>

        <ConflictNotice conflicts={conflicts} />
        {notice && <p className="mt-3 text-xs text-green-500">{notice}</p>}
        {error && <p className="mt-3 text-xs text-red-500">{error}</p>}
      </form>
    </section>
  )
}

function ConflictNotice({
  conflicts,
}: {
  conflicts: PortfolioActivationConflict[]
}) {
  if (conflicts.length === 0) return null

  return (
    <div className="mt-3 rounded-md border border-amber-300 px-2 py-2 text-xs leading-snug text-amber-600">
      Manual live overlap:{' '}
      {conflicts
        .map((item) => item.trader_display_name ?? item.trader_address.slice(0, 10))
        .join(', ')}
    </div>
  )
}

function rebalanceActionLabel(action: string): string {
  return action
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function RebalancePanel({
  label,
  portfolioSubscription,
  onSubscriptionChange,
}: {
  label: string
  portfolioSubscription: UserPortfolioSubscriptionDetail
  onSubscriptionChange: (next: UserPortfolioSubscriptionDetail) => void
}) {
  const [preview, setPreview] = useState<PortfolioRebalancePreview | null>(null)
  const [history, setHistory] = useState<PortfolioRebalanceEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [settingsBusy, setSettingsBusy] = useState<string | null>(null)
  const [applyBusy, setApplyBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const subscriptionId = portfolioSubscription.id

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    const [previewResult, historyResult] = await Promise.allSettled([
      previewPortfolioRebalance(subscriptionId),
      fetchPortfolioRebalanceHistory(subscriptionId),
    ])

    if (previewResult.status === 'fulfilled') {
      setPreview(previewResult.value)
    } else {
      const detail = (
        previewResult.reason as { response?: { data?: { detail?: string } } }
      )?.response?.data?.detail
      setError(detail ?? 'Failed to load rebalance preview')
    }

    if (historyResult.status === 'fulfilled') {
      setHistory(historyResult.value)
    }
    setLoading(false)
  }, [subscriptionId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const handleSettingChange = useCallback(
    async (
      field: 'auto_rebalance' | 'close_removed_positions',
      value: boolean,
    ) => {
      setSettingsBusy(field)
      setError(null)
      setNotice(null)
      try {
        const updated = await updatePortfolioSubscription(subscriptionId, {
          [field]: value,
        })
        onSubscriptionChange(updated)
        setPreview((current) =>
          current == null ? current : { ...current, [field]: value },
        )
        setNotice('Settings saved')
      } catch (err: unknown) {
        const detail = (err as { response?: { data?: { detail?: string } } })
          ?.response?.data?.detail
        setError(detail ?? 'Failed to update rebalance settings')
      } finally {
        setSettingsBusy(null)
      }
    },
    [onSubscriptionChange, subscriptionId],
  )

  const handleApply = useCallback(async () => {
    setApplyBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await applyPortfolioRebalance(subscriptionId)
      onSubscriptionChange(result.portfolio_subscription)
      setPreview(result)
      const nextHistory = await fetchPortfolioRebalanceHistory(subscriptionId)
      setHistory(nextHistory)
      setNotice(
        result.event.status === 'completed'
          ? 'Rebalance applied'
          : `Rebalance ${result.event.status}`,
      )
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail
      setError(detail ?? 'Failed to apply rebalance')
    } finally {
      setApplyBusy(false)
    }
  }, [onSubscriptionChange, subscriptionId])

  const status = preview?.status ?? 'pending'
  const canApply = preview?.can_apply ?? false
  const autoRebalance =
    preview?.auto_rebalance ?? portfolioSubscription.auto_rebalance
  const closeRemovedPositions =
    preview?.close_removed_positions ??
    portfolioSubscription.close_removed_positions
  const diff = preview?.diff ?? []

  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-tg-text">{label}</h2>
        <span
          className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase ${
            status === 'blocked'
              ? 'bg-red-500/10 text-red-500'
              : status === 'pending'
                ? 'bg-amber-500/10 text-amber-600'
                : 'bg-green-500/10 text-green-500'
          }`}
        >
          {status.replace('_', ' ')}
        </span>
      </div>

      <div
        className="rounded-lg px-3 py-3"
        style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
      >
        {loading ? (
          <p className="text-xs text-tg-hint">Loading rebalance</p>
        ) : (
          <>
            {preview && (
              <div className="mb-3 grid grid-cols-2 gap-px overflow-hidden rounded-lg bg-gray-100 dark:bg-gray-800">
                <StatCell
                  label="Current"
                  value={`v${preview.from_version_no}`}
                />
                <StatCell label="Target" value={`v${preview.to_version_no}`} />
              </div>
            )}

            <div className="mb-3 space-y-2 border-b border-gray-200 pb-3 dark:border-gray-700">
              <label className="flex items-center justify-between gap-3 text-xs text-tg-text">
                <span>Auto rebalance</span>
                <input
                  checked={autoRebalance}
                  disabled={settingsBusy != null}
                  type="checkbox"
                  onChange={(event) => {
                    void handleSettingChange(
                      'auto_rebalance',
                      event.target.checked,
                    )
                  }}
                />
              </label>
              <label className="flex items-center justify-between gap-3 text-xs text-tg-text">
                <span>Close removed positions</span>
                <input
                  checked={closeRemovedPositions}
                  disabled={settingsBusy != null}
                  type="checkbox"
                  onChange={(event) => {
                    void handleSettingChange(
                      'close_removed_positions',
                      event.target.checked,
                    )
                  }}
                />
              </label>
            </div>

            <div className="divide-y divide-gray-100 dark:divide-gray-800">
              {diff.map((item, index) => (
                <RebalanceDiffLine item={item} key={`${item.action}-${index}`} />
              ))}
            </div>

            {preview?.blocker && (
              <div className="mt-3 rounded-md border border-red-300 px-2 py-2 text-xs leading-snug text-red-500">
                {preview.blocker}
              </div>
            )}

            <button
              className="mt-3 w-full rounded-md bg-tg-button px-3 py-2 text-xs font-semibold text-tg-button-text disabled:opacity-50"
              disabled={!canApply || applyBusy}
              onClick={() => {
                void handleApply()
              }}
            >
              {applyBusy ? 'Applying' : 'Apply rebalance'}
            </button>

            {history.length > 0 && (
              <div className="mt-3 border-t border-gray-200 pt-3 dark:border-gray-700">
                <div className="mb-1 text-[10px] font-semibold uppercase text-tg-hint">
                  History
                </div>
                <div className="space-y-1">
                  {history.slice(0, 4).map((event) => (
                    <div
                      className="flex items-center justify-between gap-3 text-xs"
                      key={event.id}
                    >
                      <span className="min-w-0 truncate text-tg-text">
                        {event.event_type.replace('_', ' ')} · {event.status}
                      </span>
                      <span className="shrink-0 text-tg-hint">
                        {dateText(event.executed_at ?? event.created_at)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {notice && <p className="mt-3 text-xs text-green-500">{notice}</p>}
        {error && <p className="mt-3 text-xs text-red-500">{error}</p>}
      </div>
    </section>
  )
}

function RebalanceDiffLine({
  item,
}: {
  item: PortfolioRebalancePreview['diff'][number]
}) {
  const name =
    item.trader_display_name ??
    (item.trader_address ? item.trader_address.slice(0, 10) : 'Portfolio')
  const target =
    item.to_weight_pct == null
      ? null
      : `${item.to_weight_pct.toFixed(3)}% · ${money(item.to_allocation_usd)}`
  const source =
    item.from_weight_pct == null
      ? null
      : `${item.from_weight_pct.toFixed(3)}% · ${money(item.from_allocation_usd)}`

  return (
    <div className="py-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-xs font-semibold text-tg-text">{name}</div>
          <div className="text-[10px] font-semibold uppercase text-tg-hint">
            {rebalanceActionLabel(item.action)}
          </div>
        </div>
        <div className="shrink-0 text-right text-[10px] text-tg-hint">
          {source && <div>{source}</div>}
          {target && <div className="text-tg-text">{target}</div>}
        </div>
      </div>
      <p className="mt-1 text-xs leading-snug text-tg-hint">{item.message}</p>
      {item.changed_fields.length > 0 && (
        <div className="mt-1 text-[10px] text-tg-hint">
          {item.changed_fields.join(', ')}
        </div>
      )}
    </div>
  )
}

function GeneratedSubscriptionLine({
  name,
  address,
  allocationUsd,
  weight,
  status,
}: {
  name: string
  address: string | null
  allocationUsd: number
  weight: number
  status: string
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-2">
      <div className="min-w-0">
        <div className="truncate text-xs font-semibold text-tg-text">{name}</div>
        {address && (
          <div className="truncate font-mono text-[10px] text-tg-hint">
            {address}
          </div>
        )}
      </div>
      <div className="shrink-0 text-right">
        <div className="text-xs font-semibold text-tg-text">
          {money(allocationUsd)}
        </div>
        <div className="text-[10px] text-tg-hint">
          {weight.toFixed(3)}% · {status}
        </div>
      </div>
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
