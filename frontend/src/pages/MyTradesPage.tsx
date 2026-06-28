import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router-dom'
import { fetchDemoPortfolio } from '../api/demo'
import { deleteSubscription, listSubscriptions, updateSubscription } from '../api/subscriptions'
import { fetchWalletPositions } from '../api/wallet'
import { FullPageSpinner } from '../components/LoadingSpinner'
import { UnsubscribeModal } from '../components/UnsubscribeModal'
import type { DemoOpenPosition, DemoPortfolioResponse, PositionItem, Subscription, SubscriptionUpdate } from '../types'
import { fmt } from '../utils/format'

type Tab = 'live' | 'demo'

function getTabFromState(state: unknown): Tab | null {
  const tab = (state as { tab?: unknown } | null)?.tab
  return tab === 'demo' || tab === 'live' ? tab : null
}

function getTabFromSearch(search: string): Tab | null {
  const tab = new URLSearchParams(search).get('tab')
  return tab === 'demo' || tab === 'live' ? tab : null
}

function getActiveTab(search: string, state: unknown): Tab {
  return getTabFromSearch(search) ?? getTabFromState(state) ?? 'live'
}

export function MyTradesPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const locationTab = getActiveTab(location.search, location.state)
  const [activeTab, setActiveTab] = useState<Tab>(locationTab)

  useEffect(() => {
    setActiveTab(locationTab)
  }, [locationTab])

  const selectTab = (tab: Tab) => {
    setActiveTab(tab)
    const params = new URLSearchParams(location.search)
    params.set('tab', tab)
    const currentState =
      location.state && typeof location.state === 'object'
        ? (location.state as Record<string, unknown>)
        : {}

    navigate(
      { pathname: location.pathname, search: `?${params.toString()}`, hash: location.hash },
      { replace: true, state: { ...currentState, tab } },
    )
  }

  return (
    <div className="pb-20 h-full overflow-y-auto">
      {/* Tab bar */}
      <div className="flex gap-px bg-gray-100 dark:bg-gray-800 mx-4 mt-4 rounded-xl overflow-hidden">
        <TabBtn active={activeTab === 'live'} onClick={() => selectTab('live')} label="Live" />
        <TabBtn active={activeTab === 'demo'} onClick={() => selectTab('demo')} label="Demo" />
      </div>

      {activeTab === 'live' ? <LiveTab /> : <DemoTab />}
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

/* ─────────────────────── Live tab ─────────────────────── */

function LiveTab() {
  const [subs, setSubs] = useState<Subscription[]>([])
  const [positions, setPositions] = useState<PositionItem[]>([])
  const [loading, setLoading] = useState(true)
  const [editId, setEditId] = useState<number | null>(null)
  const [unsubscribeId, setUnsubscribeId] = useState<number | null>(null)
  const navigate = useNavigate()

  const reload = () => {
    setLoading(true)
    Promise.all([
      listSubscriptions(false),
      fetchWalletPositions().catch(() => [] as PositionItem[]),
    ])
      .then(([s, p]) => {
        setSubs(s)
        setPositions(p)
      })
      .finally(() => setLoading(false))
  }

  useEffect(reload, [])

  const handleUnsubscribe = async (closePositions: boolean) => {
    if (unsubscribeId === null) return
    await deleteSubscription(unsubscribeId, closePositions)
    setUnsubscribeId(null)
    reload()
  }

  const handleUpdate = async (id: number, data: SubscriptionUpdate) => {
    await updateSubscription(id, data)
    setEditId(null)
    reload()
  }

  if (loading) return <FullPageSpinner />

  if (subs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 px-6 text-center mt-16">
        <p className="text-tg-hint">No live subscriptions yet</p>
        <button
          className="px-5 py-2.5 rounded-xl text-sm font-semibold text-tg-button-text"
          style={{ background: 'var(--tg-theme-button-color)' }}
          onClick={() => navigate('/')}
        >
          Browse Traders
        </button>
      </div>
    )
  }

  return (
    <div className="px-4 pt-4 space-y-3">
      <LivePortfolioCard subs={subs} positions={positions} />

      {subs.map((sub) => {
        const unrealizedPnl = positions
          .filter((p) => p.subscription_id === sub.id)
          .reduce((acc, p) => acc + p.unrealized_pnl, 0)
        return (
          <SubscriptionCard
            key={sub.id}
            sub={sub}
            unrealizedPnl={unrealizedPnl}
            isEditing={editId === sub.id}
            onEdit={() => setEditId(sub.id)}
            onCancelEdit={() => setEditId(null)}
            onUpdate={(data) => handleUpdate(sub.id, data)}
            onUnsubscribe={() => setUnsubscribeId(sub.id)}
          />
        )
      })}

      {unsubscribeId !== null && (
        <UnsubscribeModal
          onCancel={() => setUnsubscribeId(null)}
          onKeepPositions={() => handleUnsubscribe(false)}
          onClosePositions={() => handleUnsubscribe(true)}
        />
      )}
    </div>
  )
}

/* ─────────────────────── Demo tab ─────────────────────── */

function DemoTab() {
  const [subs, setSubs] = useState<Subscription[]>([])
  const [portfolio, setPortfolio] = useState<DemoPortfolioResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [unsubscribeId, setUnsubscribeId] = useState<number | null>(null)
  const navigate = useNavigate()

  const reload = () => {
    setLoading(true)
    Promise.all([listSubscriptions(true), fetchDemoPortfolio()])
      .then(([s, p]) => {
        setSubs(s)
        setPortfolio(p)
      })
      .finally(() => setLoading(false))
  }

  useEffect(reload, [])

  const handleUnsubscribe = async () => {
    if (unsubscribeId === null) return
    await deleteSubscription(unsubscribeId, false)
    setUnsubscribeId(null)
    reload()
  }

  if (loading) return <FullPageSpinner />

  if (subs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 px-6 text-center mt-16">
        <p className="text-tg-hint">No demo subscriptions yet</p>
        <p className="text-xs text-tg-hint">Go to a trader and tap "Try Demo"</p>
        <button
          className="px-5 py-2.5 rounded-xl text-sm font-semibold text-tg-button-text"
          style={{ background: 'var(--tg-theme-button-color)' }}
          onClick={() => navigate('/')}
        >
          Browse Traders
        </button>
      </div>
    )
  }

  return (
    <div className="px-4 pt-4 space-y-3">
      {portfolio && <DemoPortfolioCard portfolio={portfolio} subCount={subs.length} />}

      {subs.map((sub) => {
        const openPositions = portfolio?.open_positions.filter(
          (p) => p.subscription_id === sub.id,
        ) ?? []
        return (
          <DemoSubscriptionCard
            key={sub.id}
            sub={sub}
            openPositions={openPositions}
            onDetail={() => navigate(`/demo-subscriptions/${sub.id}`, { state: { fromMyTradesTab: 'demo' } })}
            onUnsubscribe={() => setUnsubscribeId(sub.id)}
          />
        )
      })}

      {unsubscribeId !== null && (
        <DemoUnsubscribeModal
          onCancel={() => setUnsubscribeId(null)}
          onConfirm={handleUnsubscribe}
        />
      )}
    </div>
  )
}

function LivePortfolioCard({
  subs,
  positions,
}: {
  subs: Subscription[]
  positions: PositionItem[]
}) {
  const totalRealized = subs.reduce((acc, s) => acc + s.realized_pnl, 0)
  const totalUnrealized = positions.reduce((acc, p) => acc + p.unrealized_pnl, 0)
  const totalPnl = totalRealized + totalUnrealized
  const totalTrades = subs.reduce((acc, s) => acc + s.trade_count, 0)

  return (
    <div
      className="rounded-xl px-4 py-3"
      style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-tg-text">Live Portfolio</span>
        <span
          className="text-xs px-2 py-0.5 rounded-full font-semibold"
          style={{ background: '#16a34a', color: '#fff' }}
        >
          LIVE
        </span>
      </div>
      <div className={`text-xl font-bold mb-2 ${totalPnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
        {fmt.usd(totalPnl)}
      </div>
      <div className="grid grid-cols-4 gap-2 text-center">
        <div>
          <div className="text-[10px] text-tg-hint">Realized</div>
          <div className={`text-xs font-semibold ${totalRealized >= 0 ? 'text-green-500' : 'text-red-500'}`}>
            {fmt.usd(totalRealized)}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-tg-hint">Unrealized</div>
          <div className={`text-xs font-semibold ${totalUnrealized >= 0 ? 'text-green-500' : 'text-red-500'}`}>
            {fmt.usd(totalUnrealized)}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-tg-hint">Trades</div>
          <div className="text-xs font-semibold text-tg-text">{totalTrades}</div>
        </div>
        <div>
          <div className="text-[10px] text-tg-hint">Positions</div>
          <div className="text-xs font-semibold text-tg-text">
            {positions.length} / {subs.length}
          </div>
        </div>
      </div>
    </div>
  )
}

function DemoPortfolioCard({
  portfolio,
  subCount,
}: {
  portfolio: DemoPortfolioResponse
  subCount: number
}) {
  const totalPnl = portfolio.total_realized_pnl + portfolio.total_unrealized_pnl
  return (
    <div
      className="rounded-xl px-4 py-3"
      style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-tg-text">Demo Portfolio</span>
        <span
          className="text-xs px-2 py-0.5 rounded-full font-semibold"
          style={{ background: '#7c3aed', color: '#fff' }}
        >
          DEMO
        </span>
      </div>
      <div className={`text-xl font-bold mb-2 ${totalPnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
        {fmt.usd(totalPnl)}
      </div>
      <div className="grid grid-cols-4 gap-2 text-center">
        <div>
          <div className="text-[10px] text-tg-hint">Realized</div>
          <div className={`text-xs font-semibold ${portfolio.total_realized_pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
            {fmt.usd(portfolio.total_realized_pnl)}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-tg-hint">Unrealized</div>
          <div className={`text-xs font-semibold ${portfolio.total_unrealized_pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
            {fmt.usd(portfolio.total_unrealized_pnl)}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-tg-hint">Win Rate</div>
          <div className="text-xs font-semibold text-tg-text">
            {portfolio.win_rate_pct.toFixed(0)}%
          </div>
        </div>
        <div>
          <div className="text-[10px] text-tg-hint">Positions</div>
          <div className="text-xs font-semibold text-tg-text">
            {portfolio.open_positions.length} / {subCount}
          </div>
        </div>
      </div>
    </div>
  )
}

function DemoSubscriptionCard({
  sub,
  openPositions,
  onDetail,
  onUnsubscribe,
}: {
  sub: Subscription
  openPositions: DemoOpenPosition[]
  onDetail: () => void
  onUnsubscribe: () => void
}) {
  const addr = sub.trader_address
  const shortAddr = addr
    ? `${addr.slice(0, 6)}…${addr.slice(-4)}`
    : sub.trader_name ?? `Trader #${sub.trader_id}`

  const unrealizedPnl = openPositions.reduce((acc, p) => acc + p.unrealized_pnl, 0)
  const totalPnl = sub.realized_pnl + unrealizedPnl

  return (
    <div className="rounded-xl overflow-hidden border border-gray-100 dark:border-gray-800">
      <div className="px-3 py-3" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
        <button className="w-full text-left active:opacity-70 transition-opacity" onClick={onDetail}>
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-tg-text font-mono">{shortAddr}</span>
              <span
                className="text-[10px] px-1.5 py-0.5 rounded-full font-semibold"
                style={{ background: '#7c3aed', color: '#fff' }}
              >
                DEMO
              </span>
            </div>
            <span className={`text-sm font-semibold ${totalPnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
              {fmt.usd(totalPnl)}
            </span>
          </div>
          <div className="flex gap-3 text-xs text-tg-hint mb-1">
            <span>Realized: <span className={sub.realized_pnl >= 0 ? 'text-green-500' : 'text-red-500'}>{fmt.usd(sub.realized_pnl)}</span></span>
            {unrealizedPnl !== 0 && (
              <span>Unrealized: <span className={unrealizedPnl >= 0 ? 'text-green-500' : 'text-red-500'}>{fmt.usd(unrealizedPnl)}</span></span>
            )}
          </div>
          <div className="flex gap-3 text-xs text-tg-hint">
            <span>Virtual ${fmt.compact(sub.max_allocation_usd)}</span>
            <span>{sub.trade_count} trades</span>
            <span>{openPositions.length} open</span>
          </div>
        </button>

        <div className="flex gap-2 mt-2">
          <button
            className="flex-1 py-1.5 rounded-lg text-xs border border-tg-button text-tg-button"
            onClick={onDetail}
          >
            Details
          </button>
          <button
            className="flex-1 py-1.5 rounded-lg text-xs border border-red-400 text-red-400"
            onClick={onUnsubscribe}
          >
            Stop Demo
          </button>
        </div>
      </div>
    </div>
  )
}

function DemoUnsubscribeModal({
  onCancel,
  onConfirm,
}: {
  onCancel: () => void
  onConfirm: () => void
}) {
  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-end" onClick={onCancel}>
      <div
        className="w-full rounded-t-2xl"
        style={{ background: 'var(--tg-theme-bg-color, #fff)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 pt-5 pb-4">
          <h2 className="text-base font-semibold text-tg-text mb-1">Stop Demo</h2>
          <p className="text-sm text-tg-hint">This will deactivate your demo subscription. Simulated trade history is preserved.</p>
        </div>
        <div className="px-5 space-y-2" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 16px)' }}>
          <button
            className="w-full py-3 rounded-xl text-sm font-semibold border border-red-400 text-red-400"
            onClick={onConfirm}
          >
            Stop Demo
          </button>
          <button
            className="w-full py-3 rounded-xl text-sm text-tg-hint"
            onClick={onCancel}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

/* ─────────────────────── Live subscription card ─────────────────────── */

function SubscriptionCard({
  sub,
  unrealizedPnl,
  isEditing,
  onEdit,
  onCancelEdit,
  onUpdate,
  onUnsubscribe,
}: {
  sub: Subscription
  unrealizedPnl: number
  isEditing: boolean
  onEdit: () => void
  onCancelEdit: () => void
  onUpdate: (data: SubscriptionUpdate) => void
  onUnsubscribe: () => void
}) {
  const navigate = useNavigate()
  const [allocation, setAllocation] = useState(sub.max_allocation_usd)
  const [copyRatio, setCopyRatio] = useState(sub.copy_ratio_pct)
  const [stopLoss, setStopLoss] = useState(sub.stop_loss_pct)
  const [maxLeverage, setMaxLeverage] = useState(sub.max_leverage)

  const addr = sub.trader_address
  const shortAddr = addr
    ? `${addr.slice(0, 6)}…${addr.slice(-4)}`
    : sub.trader_name ?? `Trader #${sub.trader_id}`

  const totalPnl = sub.realized_pnl + unrealizedPnl

  return (
    <div className="rounded-xl overflow-hidden border border-gray-100 dark:border-gray-800">
      <div className="px-3 py-3" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
        <button
          className="w-full text-left active:opacity-70 transition-opacity"
          onClick={() => navigate(`/traders/${sub.trader_id}`, { state: { fromMyTradesTab: 'live' } })}
        >
          <div className="flex items-center justify-between mb-1">
            <span className="text-sm font-medium text-tg-text font-mono">{shortAddr}</span>
            <span className={`text-sm font-semibold ${totalPnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
              {fmt.usd(totalPnl)}
            </span>
          </div>
          <div className="flex gap-3 text-xs text-tg-hint mb-1">
            <span>Realized: <span className={sub.realized_pnl >= 0 ? 'text-green-500' : 'text-red-500'}>{fmt.usd(sub.realized_pnl)}</span></span>
            {unrealizedPnl !== 0 && (
              <span>Unrealized: <span className={unrealizedPnl >= 0 ? 'text-green-500' : 'text-red-500'}>{fmt.usd(unrealizedPnl)}</span></span>
            )}
          </div>
          <div className="flex gap-3 text-xs text-tg-hint">
            <span>Alloc {fmt.compact(sub.max_allocation_usd)}</span>
            <span>Copy {sub.copy_ratio_pct}%</span>
            <span>SL {sub.stop_loss_pct}%</span>
            <span>{sub.trade_count} trades</span>
          </div>
        </button>

        {!isEditing ? (
          <div className="flex gap-2 mt-2">
            <button
              className="flex-1 py-1.5 rounded-lg text-xs border border-tg-button text-tg-button"
              onClick={onEdit}
            >
              Edit
            </button>
            <button
              className="flex-1 py-1.5 rounded-lg text-xs border border-red-400 text-red-400"
              onClick={onUnsubscribe}
            >
              Unsubscribe
            </button>
          </div>
        ) : (
          <div className="mt-3 space-y-2">
            <label className="text-xs text-tg-hint">
              Max Allocation: ${allocation}
              <input
                type="range" min={10} max={10000} step={10}
                value={allocation}
                onChange={(e) => setAllocation(Number(e.target.value))}
                className="w-full accent-tg-button mt-1"
              />
            </label>
            <label className="text-xs text-tg-hint">
              Copy Ratio: {copyRatio}%
              <input
                type="range" min={10} max={100} step={5}
                value={copyRatio}
                onChange={(e) => setCopyRatio(Number(e.target.value))}
                className="w-full accent-tg-button mt-1"
              />
            </label>
            <label className="text-xs text-tg-hint">
              Stop-loss: {stopLoss}%
              <input
                type="range" min={5} max={50} step={5}
                value={stopLoss}
                onChange={(e) => setStopLoss(Number(e.target.value))}
                className="w-full accent-tg-button mt-1"
              />
            </label>
            <label className="text-xs text-tg-hint">
              Max Leverage: {maxLeverage}x
              <input
                type="range" min={1} max={40} step={1}
                value={maxLeverage}
                onChange={(e) => setMaxLeverage(Number(e.target.value))}
                className="w-full accent-tg-button mt-1"
              />
            </label>
            <div className="flex gap-2">
              <button
                className="flex-1 py-1.5 rounded-lg text-xs text-tg-button-text"
                style={{ background: 'var(--tg-theme-button-color)' }}
                onClick={() => onUpdate({ max_allocation_usd: allocation, copy_ratio_pct: copyRatio, stop_loss_pct: stopLoss, max_leverage: maxLeverage })}
              >
                Save
              </button>
              <button
                className="flex-1 py-1.5 rounded-lg text-xs border border-tg-hint text-tg-hint"
                onClick={onCancelEdit}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
