import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { fetchDemoPortfolio, fetchDemoSubscriptionTrades } from '../api/demo'
import { deleteSubscription, listSubscriptions } from '../api/subscriptions'
import { FullPageSpinner } from '../components/LoadingSpinner'
import { useBackButton } from '../hooks/useTelegram'
import type { DemoOpenPosition, DemoTradeItem, Subscription } from '../types'
import { fmt } from '../utils/format'

export function DemoSubscriptionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const subscriptionId = Number(id)
  const navigate = useNavigate()

  const [sub, setSub] = useState<Subscription | null>(null)
  const [openPositions, setOpenPositions] = useState<DemoOpenPosition[]>([])
  const [trades, setTrades] = useState<DemoTradeItem[]>([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<'positions' | 'trades'>('positions')
  const [stopping, setStopping] = useState(false)

  useBackButton(useCallback(() => navigate(-1), [navigate]))

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([
      listSubscriptions(true),
      fetchDemoPortfolio(),
      fetchDemoSubscriptionTrades(subscriptionId),
    ])
      .then(([subs, portfolio, tradeHistory]) => {
        const found = subs.find((s) => s.id === subscriptionId) ?? null
        setSub(found)
        setOpenPositions(portfolio.open_positions.filter((p) => p.subscription_id === subscriptionId))
        setTrades(tradeHistory)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [subscriptionId])

  useEffect(load, [load])

  const handleStop = async () => {
    setStopping(true)
    try {
      await deleteSubscription(subscriptionId, false)
      navigate(-1)
    } finally {
      setStopping(false)
    }
  }

  if (loading) return <FullPageSpinner />

  if (!sub) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-tg-hint">
        <p className="text-sm">Demo subscription not found</p>
        <button className="text-sm text-tg-button underline" onClick={() => navigate(-1)}>
          Go back
        </button>
      </div>
    )
  }

  const addr = sub.trader_address
  const traderLabel = addr
    ? `${addr.slice(0, 6)}…${addr.slice(-4)}`
    : sub.trader_name ?? `Trader #${sub.trader_id}`

  const totalPnl = sub.realized_pnl + sub.unrealized_pnl
  const unrealizedPnl = openPositions.reduce((acc, p) => acc + p.unrealized_pnl, 0)
  const closedCount = trades.filter((t) => t.trade_type === 'close').length
  const winCount = trades.filter((t) => t.trade_type === 'close' && (t.realized_pnl ?? 0) > 0).length
  const winRate = closedCount > 0 ? (winCount / closedCount) * 100 : 0

  return (
    <div className="pb-20 h-full overflow-y-auto">
      {/* Header */}
      <div className="px-4 pt-4 pb-3 border-b border-gray-100 dark:border-gray-800">
        <div className="flex items-center gap-2">
          <h1 className="text-base font-semibold text-tg-text">{traderLabel}</h1>
          <span
            className="text-[10px] px-1.5 py-0.5 rounded-full font-semibold"
            style={{ background: '#7c3aed', color: '#fff' }}
          >
            DEMO
          </span>
        </div>
        <p className="text-xs text-tg-hint mt-0.5">Demo subscription</p>
      </div>

      {/* Summary card */}
      <div
        className="mx-4 mt-4 rounded-xl"
        style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
      >
        <div className="grid grid-cols-2 gap-px bg-gray-100 dark:bg-gray-800 rounded-xl overflow-hidden">
          <SummaryCell
            label="Total P&L"
            value={fmt.usd(totalPnl)}
            positive={totalPnl >= 0}
          />
          <SummaryCell
            label="Realized"
            value={fmt.usd(sub.realized_pnl)}
            positive={sub.realized_pnl >= 0}
          />
          <SummaryCell
            label="Unrealized"
            value={fmt.usd(unrealizedPnl)}
            positive={unrealizedPnl >= 0}
          />
          <SummaryCell
            label="Win Rate"
            value={closedCount > 0 ? `${winRate.toFixed(0)}%` : '—'}
          />
        </div>
        <div className="px-4 py-3 flex gap-4 text-xs text-tg-hint">
          <span>Virtual ${fmt.compact(sub.max_allocation_usd)}</span>
          <span>{sub.trade_count} trades</span>
          <span>{openPositions.length} open</span>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-px bg-gray-100 dark:bg-gray-800 mx-4 mt-4 rounded-xl overflow-hidden">
        <TabBtn
          active={activeTab === 'positions'}
          onClick={() => setActiveTab('positions')}
          label={`Open (${openPositions.length})`}
        />
        <TabBtn
          active={activeTab === 'trades'}
          onClick={() => setActiveTab('trades')}
          label={`History (${closedCount})`}
        />
      </div>

      {activeTab === 'positions' && (
        <div className="mt-2 px-4 pb-4">
          {openPositions.length === 0 ? (
            <p className="text-sm text-tg-hint py-3 px-1">No open demo positions</p>
          ) : (
            <div
              className="rounded-xl overflow-hidden"
              style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
            >
              {openPositions.map((pos, i) => (
                <div
                  key={i}
                  className="px-3 py-2.5 border-b border-gray-100 dark:border-gray-700 last:border-b-0"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-medium text-tg-text">{pos.coin}</span>
                      <span
                        className={`text-xs px-1.5 py-0.5 rounded ${
                          pos.side === 'long'
                            ? 'bg-green-100 text-green-700'
                            : 'bg-red-100 text-red-700'
                        }`}
                      >
                        {pos.side.toUpperCase()}
                      </span>
                    </div>
                    <div
                      className={`text-sm font-semibold ${
                        pos.unrealized_pnl >= 0 ? 'text-green-500' : 'text-red-500'
                      }`}
                    >
                      {fmt.usd(pos.unrealized_pnl)}
                    </div>
                  </div>
                  <div className="flex gap-3 text-xs text-tg-hint mt-0.5">
                    <span>Size {pos.size}</span>
                    <span>Entry {fmt.price(pos.entry_price)}</span>
                    <span>Current {fmt.price(pos.current_price)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === 'trades' && (
        <div className="mt-2 px-4 pb-4">
          {trades.length === 0 ? (
            <p className="text-sm text-tg-hint py-3 px-1">No completed trades yet</p>
          ) : (
            <div
              className="rounded-xl overflow-hidden"
              style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
            >
              <div
                className="grid px-3 py-1.5 border-b border-gray-200 dark:border-gray-700"
                style={{ gridTemplateColumns: '4rem 3rem 1fr 3rem 4rem' }}
              >
                <span className="text-xs text-tg-hint">Asset</span>
                <span className="text-xs text-tg-hint">Side</span>
                <span className="text-xs text-tg-hint text-right">Size · Price</span>
                <span className="text-xs text-tg-hint text-right">Type</span>
                <span className="text-xs text-tg-hint text-right">PnL</span>
              </div>
              {trades.map((t) => (
                <TradeRow key={t.id} trade={t} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Stop Demo button */}
      <div className="px-4 mt-4">
        <button
          className="w-full py-2.5 rounded-xl text-sm font-semibold border border-red-400 text-red-400 disabled:opacity-50"
          onClick={handleStop}
          disabled={stopping}
        >
          {stopping ? 'Stopping…' : 'Stop Demo'}
        </button>
      </div>
    </div>
  )
}

function SummaryCell({
  label,
  value,
  positive,
}: {
  label: string
  value: string
  positive?: boolean
}) {
  return (
    <div className="px-3 py-2.5" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
      <div className="text-xs text-tg-hint">{label}</div>
      <div
        className={`text-sm font-semibold mt-0.5 ${
          positive === undefined ? 'text-tg-text' : positive ? 'text-green-500' : 'text-red-500'
        }`}
      >
        {value}
      </div>
    </div>
  )
}

function TabBtn({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`flex-1 py-2 text-xs font-medium transition-colors ${
        active ? 'text-tg-button-text' : 'text-tg-hint'
      }`}
      style={
        active
          ? { background: 'var(--tg-theme-button-color)' }
          : { background: 'var(--tg-theme-secondary-bg-color)' }
      }
    >
      {label}
    </button>
  )
}

function TradeRow({ trade }: { trade: DemoTradeItem }) {
  return (
    <div
      className="grid items-center px-3 py-2.5 border-b border-gray-100 dark:border-gray-700 last:border-b-0"
      style={{ gridTemplateColumns: '4rem 3rem 1fr 3rem 4rem' }}
    >
      <div>
        <div className="text-xs font-medium text-tg-text">{trade.coin}</div>
        <div className="text-[10px] text-tg-hint">
          {new Date(trade.executed_at).toLocaleDateString()}
        </div>
      </div>
      <span
        className={`text-xs font-semibold px-1 py-0.5 rounded self-start mt-0.5 ${
          trade.side === 'long' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
        }`}
      >
        {trade.side.slice(0, 1).toUpperCase()}
      </span>
      <div className="text-right">
        <div className="text-xs text-tg-text">{fmt.qty(trade.size)}</div>
        <div className="text-xs text-tg-hint">@ ${fmt.price(trade.price)}</div>
      </div>
      <div className="text-right text-xs text-tg-hint">
        {trade.trade_type === 'close' ? 'Close' : 'Open'}
      </div>
      <div
        className={`text-xs font-semibold text-right ${
          trade.realized_pnl == null
            ? 'text-tg-hint'
            : trade.realized_pnl >= 0
              ? 'text-green-500'
              : 'text-red-500'
        }`}
      >
        {trade.realized_pnl != null ? fmt.usd(trade.realized_pnl) : '—'}
      </div>
    </div>
  )
}
