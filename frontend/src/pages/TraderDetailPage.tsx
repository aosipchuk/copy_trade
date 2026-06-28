import { useCallback, useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { deleteSubscription, listSubscriptions, updateSubscription } from '../api/subscriptions'
import {
  downloadTraderExport,
  fetchClosedTrades,
  fetchEquityCurve,
  fetchTraderSummary,
} from '../api/traders'
import { EquityChart } from '../components/EquityChart'
import { FullPageSpinner } from '../components/LoadingSpinner'
import { SubscribeModal } from '../components/SubscribeModal'
import { UnsubscribeModal } from '../components/UnsubscribeModal'
import { useBackButton } from '../hooks/useTelegram'
import { useTraderPositionsWS } from '../hooks/useWebSocket'
import type {
  ClosedTradeItem,
  EquityPoint,
  Period,
  PositionItem,
  Subscription,
  TraderSummary,
} from '../types'
import { fmt } from '../utils/format'

type MyTradesTab = 'live' | 'demo'

function getMyTradesReturnTab(state: unknown): MyTradesTab | null {
  const tab = (state as { fromMyTradesTab?: unknown } | null)?.fromMyTradesTab
  return tab === 'demo' || tab === 'live' ? tab : null
}

export function TraderDetailPage() {
  const { id } = useParams<{ id: string }>()
  const traderId = Number(id)
  const navigate = useNavigate()
  const location = useLocation()
  const myTradesReturnTab = getMyTradesReturnTab(location.state)

  const [summary, setSummary] = useState<TraderSummary | null>(null)
  const [existingSub, setExistingSub] = useState<Subscription | null>(null)
  const [equityCurve, setEquityCurve] = useState<EquityPoint[]>([])
  const [chartLoading, setChartLoading] = useState(false)
  const [period, setPeriod] = useState<Period>('week')
  const [activeTab, setActiveTab] = useState<'positions' | 'trades'>('positions')
  const [closedTrades, setClosedTrades] = useState<ClosedTradeItem[] | null>(null)
  const [tradesLoading, setTradesLoading] = useState(false)
  const [tradesError, setTradesError] = useState(false)
  const [tradesReload, setTradesReload] = useState(0)
  const [tradesLimit, setTradesLimit] = useState(50)
  const [existingDemoSub, setExistingDemoSub] = useState<Subscription | null>(null)
  const [showSubscribe, setShowSubscribe] = useState(false)
  const [showDemoSubscribe, setShowDemoSubscribe] = useState(false)
  const [showUnsubscribe, setShowUnsubscribe] = useState(false)
  const [editingSub, setEditingSub] = useState(false)
  const [subAllocation, setSubAllocation] = useState(100)
  const [subCopyRatio, setSubCopyRatio] = useState(100)
  const [subStopLoss, setSubStopLoss] = useState(20)
  const [subMaxLeverage, setSubMaxLeverage] = useState(10)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)

  const navigateBack = useCallback(() => {
    if (myTradesReturnTab) {
      navigate(`/my-trades?tab=${myTradesReturnTab}`, {
        replace: true,
        state: { tab: myTradesReturnTab },
      })
      return
    }
    navigate(-1)
  }, [myTradesReturnTab, navigate])

  useBackButton(navigateBack)

  const wsPositions = useTraderPositionsWS<PositionItem[]>(traderId)
  const livePositions = wsPositions ?? summary?.open_positions ?? []

  // Initial load via summary endpoint — replaces 4 separate requests
  const reload = useCallback(() => {
    setLoading(true)
    setLoadError(null)
    fetchTraderSummary(traderId)
      .then((s) => {
        setSummary(s)
        setEquityCurve(s.equity_curve_week)
        return Promise.all([listSubscriptions(false), listSubscriptions(true)])
      })
      .then(([liveSubs, demoSubs]) => {
        const sub = liveSubs.find((s) => s.trader_id === traderId && s.is_active) ?? null
        setExistingSub(sub)
        if (sub) {
          setSubAllocation(sub.max_allocation_usd)
          setSubCopyRatio(sub.copy_ratio_pct)
          setSubStopLoss(sub.stop_loss_pct)
          setSubMaxLeverage(sub.max_leverage)
        }
        const demoSub = demoSubs.find((s) => s.trader_id === traderId && s.is_active) ?? null
        setExistingDemoSub(demoSub)
      })
      .catch((err: unknown) => {
        const status = (err as { response?: { status?: number } })?.response?.status
        setLoadError(status === 404 ? 'not_found' : 'error')
      })
      .finally(() => setLoading(false))
  }, [traderId])

  useEffect(reload, [reload])

  // Load closed trades when tab opens, limit increases, or a retry is requested.
  // On failure (e.g. Hyperliquid rate-limiting the live fills call) we flag an
  // error instead of setting an empty list — otherwise the UI would render
  // "No recent trades" and masquerade a transient failure as zero trades.
  useEffect(() => {
    if (activeTab !== 'trades') return
    setTradesLoading(true)
    setTradesError(false)
    fetchClosedTrades(traderId, tradesLimit)
      .then((t) => {
        setClosedTrades(t)
        setTradesError(false)
      })
      .catch(() => setTradesError(true))
      .finally(() => setTradesLoading(false))
  }, [activeTab, traderId, tradesLimit, tradesReload])

  // When period changes, restore from summary (week) or fetch separately.
  // Cancellation flag prevents a stale fetch from overwriting a newer period's data
  // if the user switches periods faster than requests complete.
  useEffect(() => {
    if (!summary) return
    if (period === 'week') {
      setChartLoading(false)
      setEquityCurve(summary.equity_curve_week)
      return
    }
    let cancelled = false
    setChartLoading(true)
    setEquityCurve([])
    fetchEquityCurve(traderId, period)
      .then((pts) => { if (!cancelled) setEquityCurve(pts) })
      .catch(() => { if (!cancelled) setEquityCurve([]) })
      .finally(() => { if (!cancelled) setChartLoading(false) })
    return () => { cancelled = true }
  }, [traderId, period]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleUnsubscribe = async (closePositions: boolean) => {
    await deleteSubscription(existingSub!.id, closePositions)
    setShowUnsubscribe(false)
    setExistingSub(null)
    setEditingSub(false)
  }

  const handleExport = async () => {
    if (!summary || exporting) return
    setExporting(true)
    try {
      await downloadTraderExport(traderId)
    } finally {
      setExporting(false)
    }
  }

  const handleSaveEdit = async () => {
    if (!existingSub) return
    await updateSubscription(existingSub.id, {
      max_allocation_usd: subAllocation,
      copy_ratio_pct: subCopyRatio,
      stop_loss_pct: subStopLoss,
      max_leverage: subMaxLeverage,
    })
    setEditingSub(false)
    reload()
  }

  if (loading) return <FullPageSpinner />
  if (!summary) return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-tg-hint">
      <span className="text-4xl">⚠️</span>
      <p className="text-sm">
        {loadError === 'not_found' ? 'Trader not found' : 'Failed to load trader'}
      </p>
      <button className="text-sm text-tg-button underline" onClick={navigateBack}>Go back</button>
    </div>
  )

  const stat = summary.stats[period] ?? Object.values(summary.stats)[0]
  const qualityStat = summary.stats['allTime'] ?? Object.values(summary.stats)[0]

  return (
    <div className="pb-48 h-full overflow-y-auto">
      {/* Header */}
      <div className="px-4 pt-4 pb-3 border-b border-gray-100 dark:border-gray-800 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-base font-semibold text-tg-text truncate">
            {summary.display_name ?? `${summary.hl_address.slice(0, 8)}…`}
          </h1>
          <p className="text-xs text-tg-hint mt-0.5 font-mono truncate">{summary.hl_address}</p>
        </div>
        <button
          type="button"
          className="shrink-0 h-9 w-9 rounded-lg flex items-center justify-center text-tg-hint transition-colors active:bg-tg-secondary disabled:opacity-60 disabled:cursor-wait"
          style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
          onClick={handleExport}
          disabled={exporting}
          aria-label={exporting ? 'Preparing trader export' : 'Export trader workbook'}
          title={exporting ? 'Preparing export' : 'Export workbook'}
        >
          {exporting ? <SpinnerIcon /> : <DownloadIcon />}
        </button>
      </div>

      {/* Period stats grid */}
      {stat && (
        <div className="grid grid-cols-2 gap-px bg-gray-100 dark:bg-gray-800 mx-4 mt-4 rounded-xl overflow-hidden">
          <StatCell label="ROI" value={fmt.pct(stat.roi_pct)} positive={stat.roi_pct >= 0} />
          <StatCell label="PnL" value={fmt.usd(stat.pnl_usd)} positive={stat.pnl_usd >= 0} />
          <StatCell label="Volume" value={fmt.compact(stat.volume_usd)} />
          <StatCell label="Positions" value={String(livePositions.length)} />
        </div>
      )}

      {/* Subscription panel */}
      {existingSub && (
        <div
          className="mx-4 mt-4 rounded-xl overflow-hidden"
          style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
        >
          <div className="px-3 py-3">
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm font-semibold text-tg-text">Your Subscription</span>
              <span className={`text-sm font-semibold ${existingSub.realized_pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                {fmt.usd(existingSub.realized_pnl)}
              </span>
            </div>
            <div className="flex gap-3 text-xs text-tg-hint mb-3">
              <span>Alloc ${existingSub.max_allocation_usd}</span>
              <span>Copy {existingSub.copy_ratio_pct}%</span>
              <span>SL {existingSub.stop_loss_pct}%</span>
              <span>{existingSub.trade_count} trades</span>
            </div>

            {!editingSub ? (
              <div className="flex gap-2">
                <button
                  className="flex-1 py-1.5 rounded-lg text-xs border border-tg-button text-tg-button"
                  onClick={() => setEditingSub(true)}
                >
                  Edit
                </button>
                <button
                  className="flex-1 py-1.5 rounded-lg text-xs border border-red-400 text-red-400"
                  onClick={() => setShowUnsubscribe(true)}
                >
                  Unsubscribe
                </button>
              </div>
            ) : (
              <div className="space-y-2">
                <label className="text-xs text-tg-hint block">
                  Max Allocation: ${subAllocation}
                  <input
                    type="range" min={10} max={10000} step={10}
                    value={subAllocation}
                    onChange={(e) => setSubAllocation(Number(e.target.value))}
                    className="w-full accent-tg-button mt-1"
                  />
                </label>
                <label className="text-xs text-tg-hint block">
                  Copy Ratio: {subCopyRatio}%
                  <input
                    type="range" min={10} max={100} step={5}
                    value={subCopyRatio}
                    onChange={(e) => setSubCopyRatio(Number(e.target.value))}
                    className="w-full accent-tg-button mt-1"
                  />
                </label>
                <label className="text-xs text-tg-hint block">
                  Stop-loss: {subStopLoss}%
                  <input
                    type="range" min={5} max={50} step={5}
                    value={subStopLoss}
                    onChange={(e) => setSubStopLoss(Number(e.target.value))}
                    className="w-full accent-tg-button mt-1"
                  />
                </label>
                <label className="text-xs text-tg-hint block">
                  Max Leverage: {subMaxLeverage}x
                  <input
                    type="range" min={1} max={40} step={1}
                    value={subMaxLeverage}
                    onChange={(e) => setSubMaxLeverage(Number(e.target.value))}
                    className="w-full accent-tg-button mt-1"
                  />
                </label>
                <div className="flex gap-2">
                  <button
                    className="flex-1 py-1.5 rounded-lg text-xs text-tg-button-text"
                    style={{ background: 'var(--tg-theme-button-color)' }}
                    onClick={handleSaveEdit}
                  >
                    Save
                  </button>
                  <button
                    className="flex-1 py-1.5 rounded-lg text-xs border border-tg-hint text-tg-hint"
                    onClick={() => setEditingSub(false)}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Demo subscription panel / Try Demo CTA */}
      {existingDemoSub ? (
        <div
          className="mx-4 mt-3 rounded-xl overflow-hidden"
          style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
        >
          <div className="px-3 py-3">
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-tg-text">Demo Subscription</span>
                <span
                  className="text-[10px] px-1.5 py-0.5 rounded-full font-semibold"
                  style={{ background: '#7c3aed', color: '#fff' }}
                >
                  DEMO
                </span>
              </div>
              <span className={`text-sm font-semibold ${existingDemoSub.realized_pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                {fmt.usd(existingDemoSub.realized_pnl + existingDemoSub.unrealized_pnl)}
              </span>
            </div>
            <div className="flex gap-3 text-xs text-tg-hint mb-2">
              <span>Virtual ${fmt.compact(existingDemoSub.max_allocation_usd)}</span>
              <span>{existingDemoSub.trade_count} trades</span>
            </div>
            <button
              className="w-full py-1.5 rounded-lg text-xs border border-tg-button text-tg-button"
              onClick={() => navigate('/my-trades?tab=demo', { state: { tab: 'demo' } })}
            >
              View Demo Portfolio
            </button>
          </div>
        </div>
      ) : null}

      {/* Period tabs */}
      <div className="flex gap-1 px-4 mt-4">
        {(['day', 'week', 'month', 'allTime'] as Period[]).map((p) => (
          <button
            key={p}
            onClick={() => setPeriod(p)}
            className={`flex-1 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              period === p ? 'text-tg-button-text' : 'text-tg-hint'
            }`}
            style={period === p ? { background: 'var(--tg-theme-button-color)' } : {}}
          >
            {p === 'allTime' ? 'All' : p.charAt(0).toUpperCase() + p.slice(1)}
          </button>
        ))}
      </div>

      {/* Equity chart */}
      <div className="mx-4 mt-3 rounded-xl overflow-hidden" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
        {chartLoading ? (
          <div className="h-[180px] flex items-center justify-center text-tg-hint text-sm">
            Loading…
          </div>
        ) : equityCurve.length > 0 ? (
          <EquityChart data={equityCurve} period={period} height={180} />
        ) : (
          <div className="h-[180px] flex items-center justify-center text-tg-hint text-sm">
            No equity data
          </div>
        )}
      </div>

      {/* Quality stats grid */}
      {qualityStat && (
        <div className="grid grid-cols-4 gap-px bg-gray-100 dark:bg-gray-800 mx-4 mt-3 rounded-xl overflow-hidden">
          <QualityCell
            label="Win Rate"
            value={qualityStat.win_rate_pct != null ? `${qualityStat.win_rate_pct.toFixed(0)}%` : '—'}
            color={winRateColor(qualityStat.win_rate_pct)}
          />
          <QualityCell
            label="Max DD"
            value={qualityStat.max_drawdown_pct != null ? `-${Math.floor(qualityStat.max_drawdown_pct)}%` : '—'}
            color={drawdownColor(qualityStat.max_drawdown_pct)}
          />
          <QualityCell
            label="Trades"
            value={qualityStat.trade_count != null ? String(qualityStat.trade_count) : '—'}
            color="neutral"
          />
          <QualityCell
            label="Sharpe"
            value={qualityStat.sharpe_ratio != null ? qualityStat.sharpe_ratio.toFixed(2) : '—'}
            color={sharpeColor(qualityStat.sharpe_ratio)}
          />
        </div>
      )}

      {/* Positions / Trades tab */}
      <div className="flex gap-px bg-gray-100 dark:bg-gray-800 mx-4 mt-4 rounded-xl overflow-hidden">
        <TabBtn
          active={activeTab === 'positions'}
          onClick={() => setActiveTab('positions')}
          label={`Positions (${livePositions.length})`}
        />
        <TabBtn
          active={activeTab === 'trades'}
          onClick={() => setActiveTab('trades')}
          label={`Trades (${qualityStat?.trade_count ?? closedTrades?.length ?? summary.recent_trades.length})`}
        />
      </div>

      {/* Positions list */}
      {activeTab === 'positions' && (
        <div className="mt-2 px-4 pb-4">
          {livePositions.length === 0 ? (
            <p className="text-sm text-tg-hint px-1 py-3">No open positions</p>
          ) : (
            <div className="rounded-xl overflow-hidden" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
              {livePositions.map((pos, i) => (
                <div key={i} className="flex items-center justify-between px-3 py-2.5 border-b border-gray-100 dark:border-gray-700 last:border-b-0">
                  <div>
                    <span className="text-sm font-medium text-tg-text">{pos.coin}</span>
                    <span className={`ml-1.5 text-xs px-1.5 py-0.5 rounded ${pos.side === 'long' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                      {pos.side.toUpperCase()}
                    </span>
                  </div>
                  <div className="text-right">
                    <div className="text-xs text-tg-hint">Size {pos.size}</div>
                    <div className={`text-xs ${pos.unrealized_pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                      {fmt.usd(pos.unrealized_pnl)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Recent closed trades list */}
      {activeTab === 'trades' && (
        <div className="mt-2 px-4 pb-4">
          {tradesLoading && closedTrades === null ? (
            <p className="text-sm text-tg-hint px-1 py-3">Loading trades…</p>
          ) : tradesError && (closedTrades ?? summary.recent_trades).length === 0 ? (
            <div className="flex flex-col items-center gap-2 px-1 py-6 text-center">
              <p className="text-sm text-tg-hint">Couldn’t load trades. Please try again.</p>
              <button
                className="py-1.5 px-4 rounded-lg text-xs border border-tg-button text-tg-button disabled:opacity-50"
                disabled={tradesLoading}
                onClick={() => setTradesReload((n) => n + 1)}
              >
                {tradesLoading ? 'Retrying…' : 'Retry'}
              </button>
            </div>
          ) : (closedTrades ?? summary.recent_trades).length === 0 ? (
            <p className="text-sm text-tg-hint px-1 py-3">No recent trades</p>
          ) : (
            <>
              <div className="rounded-xl overflow-hidden" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
                <div className="grid px-3 py-1.5 border-b border-gray-200 dark:border-gray-700" style={{ gridTemplateColumns: '4rem 3.5rem 1fr 4rem' }}>
                  <span className="text-xs text-tg-hint">Asset</span>
                  <span className="text-xs text-tg-hint">Side</span>
                  <span className="text-xs text-tg-hint text-right">Size · Avg px</span>
                  <span className="text-xs text-tg-hint text-right">PnL</span>
                </div>
                {(closedTrades ?? summary.recent_trades).map((t, i) => (
                  <TradeRow key={i} trade={t} />
                ))}
              </div>
              {closedTrades !== null && closedTrades.length < (qualityStat?.trade_count ?? 0) && tradesLimit < 500 && (
                <button
                  className="w-full mt-3 py-2.5 rounded-xl text-sm text-tg-button border border-tg-button disabled:opacity-50"
                  disabled={tradesLoading}
                  onClick={() => setTradesLimit((prev) => Math.min(prev + 50, 500))}
                >
                  {tradesLoading ? 'Loading…' : `Load more (${closedTrades.length} of ${qualityStat?.trade_count})`}
                </button>
              )}
            </>
          )}
        </div>
      )}

      {/* Fixed bottom action bar */}
      {!loading && (!existingSub || !existingDemoSub) && (
        <div
          className="fixed left-0 right-0 px-4 pb-3 pt-2 flex flex-row gap-2"
          style={{
            bottom: 'calc(62px + env(safe-area-inset-bottom, 0px))',
            background: 'var(--tg-theme-bg-color)',
            borderTop: '1px solid var(--tg-theme-secondary-bg-color)',
          }}
        >
          {!existingDemoSub && (
            <button
              className="flex-1 py-3 rounded-xl text-sm font-semibold text-white"
              style={{ background: '#7c3aed' }}
              onClick={() => setShowDemoSubscribe(true)}
            >
              Try Demo
            </button>
          )}
          {!existingSub && (
            <button
              className="flex-1 py-3 rounded-xl text-sm font-semibold"
              style={{ background: 'var(--tg-theme-button-color)', color: 'var(--tg-theme-button-text-color)' }}
              onClick={() => setShowSubscribe(true)}
            >
              Subscribe
            </button>
          )}
        </div>
      )}

      {showSubscribe && (
        <SubscribeModal
          traderId={traderId}
          onClose={() => setShowSubscribe(false)}
          onSuccess={() => {
            setShowSubscribe(false)
            reload()
          }}
        />
      )}

      {showDemoSubscribe && (
        <SubscribeModal
          traderId={traderId}
          isDemo
          onClose={() => setShowDemoSubscribe(false)}
          onSuccess={() => {
            setShowDemoSubscribe(false)
            reload()
          }}
        />
      )}

      {showUnsubscribe && (
        <UnsubscribeModal
          onCancel={() => setShowUnsubscribe(false)}
          onKeepPositions={() => handleUnsubscribe(false)}
          onClosePositions={() => handleUnsubscribe(true)}
        />
      )}
    </div>
  )
}

function StatCell({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  return (
    <div className="px-3 py-2.5" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
      <div className="text-xs text-tg-hint">{label}</div>
      <div className={`text-sm font-semibold mt-0.5 ${positive === undefined ? 'text-tg-text' : positive ? 'text-green-500' : 'text-red-500'}`}>
        {value}
      </div>
    </div>
  )
}

type SignalColor = 'green' | 'yellow' | 'red' | 'neutral'

function winRateColor(v: number | null): SignalColor {
  if (v == null) return 'neutral'
  if (v > 55) return 'green'
  if (v >= 40) return 'yellow'
  return 'red'
}

function drawdownColor(v: number | null): SignalColor {
  if (v == null) return 'neutral'
  if (v < 20) return 'green'
  if (v <= 50) return 'yellow'
  return 'red'
}

function sharpeColor(v: number | null): SignalColor {
  if (v == null) return 'neutral'
  if (v > 1.5) return 'green'
  if (v >= 0.5) return 'yellow'
  return 'red'
}

const COLOR_CLASSES: Record<SignalColor, string> = {
  green: 'text-green-500',
  yellow: 'text-yellow-500',
  red: 'text-red-500',
  neutral: 'text-tg-text',
}

function QualityCell({ label, value, color }: { label: string; value: string; color: SignalColor }) {
  return (
    <div className="px-2 py-2" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
      <div className="text-[10px] text-tg-hint leading-tight">{label}</div>
      <div className={`text-xs font-semibold mt-0.5 ${COLOR_CLASSES[color]}`}>{value}</div>
    </div>
  )
}

function TabBtn({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`flex-1 py-2 text-xs font-medium transition-colors ${active ? 'text-tg-button-text' : 'text-tg-hint'}`}
      style={active ? { background: 'var(--tg-theme-button-color)' } : { background: 'var(--tg-theme-secondary-bg-color)' }}
    >
      {label}
    </button>
  )
}

function DownloadIcon() {
  return (
    <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v10m0 0 4-4m-4 4-4-4" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M5 17v1a3 3 0 0 0 3 3h8a3 3 0 0 0 3-3v-1" />
    </svg>
  )
}

function SpinnerIcon() {
  return (
    <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24" aria-hidden="true">
      <circle className="opacity-25" cx="12" cy="12" r="9" stroke="currentColor" strokeWidth={3} />
      <path className="opacity-75" fill="currentColor" d="M21 12a9 9 0 0 0-9-9v3a6 6 0 0 1 6 6h3Z" />
    </svg>
  )
}

function TradeRow({ trade }: { trade: ClosedTradeItem }) {
  return (
    <div className="grid items-center px-3 py-2.5 border-b border-gray-100 dark:border-gray-700 last:border-b-0" style={{ gridTemplateColumns: '4rem 3.5rem 1fr 4rem' }}>
      <div>
        <div className="text-sm font-medium text-tg-text">{trade.coin}</div>
        <div className="text-xs text-tg-hint">
          {new Date(trade.time).toLocaleDateString()}{' '}
          {new Date(trade.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
        </div>
      </div>
      <span className={`text-xs font-semibold px-1.5 py-0.5 rounded self-start mt-0.5 ${trade.direction === 'long' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
        {trade.direction.toUpperCase()}
      </span>
      <div className="text-right">
        <div className="text-xs text-tg-text">{fmt.qty(trade.size)}</div>
        <div className="text-xs text-tg-hint">@ ${fmt.price(trade.avg_px)}</div>
      </div>
      <div className={`text-xs font-semibold text-right ${trade.pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
        {fmt.usd(trade.pnl)}
      </div>
    </div>
  )
}
