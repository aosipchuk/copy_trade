import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { fetchTraders } from '../api/traders'
import { listSubscriptions } from '../api/subscriptions'
import { TraderCard } from '../components/TraderCard'
import { FullPageSpinner, LoadingSpinner } from '../components/LoadingSpinner'
import { useTradersFilterStore, DEFAULT_FILTERS } from '../store/tradersFilterStore'
import type { Period, SortKey, TraderFilters, TraderListItem } from '../types'

const PERIODS: { key: Period; label: string }[] = [
  { key: 'day', label: 'Day' },
  { key: 'week', label: 'Week' },
  { key: 'month', label: 'Month' },
  { key: 'allTime', label: 'All' },
]

const SORTS: { key: SortKey; label: string }[] = [
  { key: 'roi', label: 'ROI' },
  { key: 'pnl', label: 'PnL' },
  { key: 'volume', label: 'Volume' },
]

const MIN_DAYS_OPTIONS = [
  { value: 0, label: 'Any' },
  { value: 7, label: '7d' },
  { value: 30, label: '30d' },
  { value: 90, label: '90d' },
]

function filtersActive(f: TraderFilters): number {
  return [
    f.quality,
    f.subscribed_only,
    f.min_roi !== 0,
    f.min_win_rate > 0,
    f.max_drawdown < 100,
    f.min_days > 0,
    f.min_trades > 0,
    f.min_composite_score > 0,
    f.min_profit_factor > 0,
    f.max_losing_streak != null,
    f.min_profitable_days_pct > 0,
    f.max_avg_trades_per_day != null,
    f.min_calmar > 0,
  ].filter(Boolean).length
}

export function TradersPage() {
  const { period, sort, filters, setPeriod, setSort, setFilters } = useTradersFilterStore()
  const [draftFilters, setDraftFilters] = useState<TraderFilters>(DEFAULT_FILTERS)
  const [showFilters, setShowFilters] = useState(false)
  const [realSubIds, setRealSubIds] = useState<Set<number>>(new Set())
  const [demoSubIds, setDemoSubIds] = useState<Set<number>>(new Set())
  const [traders, setTraders] = useState<TraderListItem[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(true)
  const [loading, setLoading] = useState(false)
  const [initialLoading, setInitialLoading] = useState(true)
  const [searchInput, setSearchInput] = useState('')
  const [addressQuery, setAddressQuery] = useState('')
  const [loaderNode, setLoaderNode] = useState<HTMLDivElement | null>(null)
  const loadingRef = useRef(false)
  const cursorRef = useRef<string | null>(null)

  useEffect(() => {
    const timer = setTimeout(() => setAddressQuery(searchInput.trim()), 350)
    return () => clearTimeout(timer)
  }, [searchInput])

  useEffect(() => {
    cursorRef.current = cursor
  }, [cursor])

  useEffect(() => {
    Promise.all([listSubscriptions(false), listSubscriptions(true)])
      .then(([real, demo]) => {
        setRealSubIds(new Set(real.map((s) => s.trader_id)))
        setDemoSubIds(new Set(demo.map((s) => s.trader_id)))
      })
      .catch(() => {})
  }, [])

  const load = useCallback(
    async (reset: boolean) => {
      if (loadingRef.current) return
      loadingRef.current = true
      setLoading(true)
      try {
        const res = await fetchTraders({
          period,
          sort,
          cursor: reset ? null : cursorRef.current,
          filters,
          address: addressQuery || undefined,
        })
        setTraders((prev) => (reset ? res.items : [...prev, ...res.items]))
        setCursor(res.next_cursor)
        setHasMore(res.next_cursor !== null)
      } finally {
        loadingRef.current = false
        setLoading(false)
        setInitialLoading(false)
      }
    },
    [period, sort, filters, addressQuery],
  )

  // Reset on period/sort/filters/search change
  useEffect(() => {
    setTraders([])
    setCursor(null)
    cursorRef.current = null
    setHasMore(true)
    setInitialLoading(true)
  }, [period, sort, filters, addressQuery])

  useEffect(() => {
    if (initialLoading) load(true)
  }, [initialLoading, load])

  // Infinite scroll
  useEffect(() => {
    if (!loaderNode) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasMore && !loadingRef.current) load(false)
      },
      { threshold: 0.1 },
    )
    observer.observe(loaderNode)
    return () => observer.disconnect()
  }, [loaderNode, hasMore, load])

  const openFilters = () => {
    setDraftFilters(filters)
    setShowFilters(true)
  }

  const applyFilters = () => {
    setFilters(draftFilters)
    setShowFilters(false)
  }

  const resetFilters = () => {
    setDraftFilters(DEFAULT_FILTERS)
  }

  const activeCount = filtersActive(filters)
  const displayedTraders = traders

  return (
    <div className="flex flex-col h-full">
      {/* Period selector */}
      <div
        className="flex gap-1 px-3 py-2 border-b border-gray-100 dark:border-gray-800"
        style={{ background: 'var(--tg-theme-bg-color)' }}
      >
        {PERIODS.map((p) => (
          <button
            key={p.key}
            onClick={() => setPeriod(p.key)}
            className={`flex-1 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              period === p.key ? 'text-tg-button-text' : 'text-tg-hint'
            }`}
            style={period === p.key ? { background: 'var(--tg-theme-button-color)' } : {}}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Sort + Filter row */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-100 dark:border-gray-800">
        <div className="flex gap-2 flex-1">
          {SORTS.map((s) => (
            <button
              key={s.key}
              onClick={() => setSort(s.key)}
              className={`text-xs px-3 py-1 rounded-full border transition-colors ${
                sort === s.key
                  ? 'border-tg-button text-tg-button'
                  : 'border-transparent text-tg-hint'
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <button
          onClick={openFilters}
          className={`relative text-xs px-3 py-1 rounded-full border transition-colors flex-shrink-0 ${
            activeCount > 0
              ? 'border-tg-button text-tg-button'
              : 'border-transparent text-tg-hint'
          }`}
        >
          Filters
          {activeCount > 0 && (
            <span
              className="absolute -top-1 -right-1 w-4 h-4 rounded-full text-white text-[9px] flex items-center justify-center font-bold"
              style={{ background: 'var(--tg-theme-button-color)' }}
            >
              {activeCount}
            </span>
          )}
        </button>
      </div>

      {/* Wallet address search */}
      <div className="px-3 py-2 border-b border-gray-100 dark:border-gray-800">
        <div className="relative">
          <svg
            className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-tg-hint pointer-events-none"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            viewBox="0 0 24 24"
          >
            <circle cx={11} cy={11} r={8} />
            <path d="m21 21-4.35-4.35" />
          </svg>
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search by wallet address…"
            className="w-full pl-8 pr-8 py-1.5 rounded-lg text-xs text-tg-text placeholder-tg-hint bg-gray-100 dark:bg-gray-800 border-none outline-none"
          />
          {searchInput && (
            <button
              onClick={() => setSearchInput('')}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-tg-hint hover:text-tg-text"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {initialLoading ? (
          <FullPageSpinner />
        ) : (
          <>
            {displayedTraders.map((t) => (
              <TraderCard key={t.id} trader={t} sort={sort} isRealSubscribed={realSubIds.has(t.id)} isDemoSubscribed={demoSubIds.has(t.id)} />
            ))}
            <div ref={setLoaderNode} className="flex justify-center py-4">
              {loading && <LoadingSpinner />}
            </div>
          </>
        )}
      </div>

      {/* Filter bottom sheet — rendered via portal so it overlays the TabBar (z-50) */}
      {showFilters && createPortal(
        <div className="fixed inset-0 z-[100] flex items-end" onClick={() => setShowFilters(false)}>
          <div
            className="w-full rounded-t-2xl flex flex-col max-h-[85vh]"
            style={{ background: 'var(--tg-theme-bg-color)' }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Handle + header — не скроллятся */}
            <div className="flex-shrink-0 px-4 pt-4">
              <div className="w-10 h-1 rounded-full bg-tg-hint mx-auto mb-4 opacity-40" />
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-semibold text-tg-text text-base">Filters</h3>
                <button onClick={resetFilters} className="text-xs text-tg-hint">
                  Reset
                </button>
              </div>
            </div>

            {/* Контролы — скроллятся если не влезают */}
            <div className="overflow-y-auto flex-1 px-4 space-y-5 pb-3">
              {/* Top Rated toggle — composite_score >= 70 */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-tg-text">Top Rated</p>
                  <p className="text-xs text-tg-hint mt-0.5">Composite score ≥ 70 (risk, consistency, returns)</p>
                </div>
                <button
                  onClick={() =>
                    setDraftFilters((f) => ({
                      ...f,
                      min_composite_score: f.min_composite_score > 0 ? 0 : 70,
                    }))
                  }
                  className={`w-11 h-6 rounded-full transition-colors relative flex-shrink-0 ${
                    draftFilters.min_composite_score > 0 ? '' : 'bg-gray-300 dark:bg-gray-600'
                  }`}
                  style={draftFilters.min_composite_score > 0 ? { background: 'var(--tg-theme-button-color)' } : {}}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
                      draftFilters.min_composite_score > 0 ? 'translate-x-5' : 'translate-x-0'
                    }`}
                  />
                </button>
              </div>

              {/* Verified only toggle */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-tg-text">Verified Only</p>
                  <p className="text-xs text-tg-hint mt-0.5">Win rate &gt;40%, 10+ trades, active 30+ days</p>
                </div>
                <button
                  onClick={() => setDraftFilters((f) => ({ ...f, quality: !f.quality }))}
                  className={`w-11 h-6 rounded-full transition-colors relative flex-shrink-0 ${
                    draftFilters.quality ? '' : 'bg-gray-300 dark:bg-gray-600'
                  }`}
                  style={draftFilters.quality ? { background: 'var(--tg-theme-button-color)' } : {}}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
                      draftFilters.quality ? 'translate-x-5' : 'translate-x-0'
                    }`}
                  />
                </button>
              </div>

              {/* Subscribed only toggle */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-tg-text">My Subscriptions</p>
                  <p className="text-xs text-tg-hint mt-0.5">Show only traders you follow</p>
                </div>
                <button
                  onClick={() => setDraftFilters((f) => ({ ...f, subscribed_only: !f.subscribed_only }))}
                  className={`w-11 h-6 rounded-full transition-colors relative flex-shrink-0 ${
                    draftFilters.subscribed_only ? '' : 'bg-gray-300 dark:bg-gray-600'
                  }`}
                  style={draftFilters.subscribed_only ? { background: 'var(--tg-theme-button-color)' } : {}}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
                      draftFilters.subscribed_only ? 'translate-x-5' : 'translate-x-0'
                    }`}
                  />
                </button>
              </div>

              {/* Min ROI */}
              <div>
                <div className="flex justify-between mb-1">
                  <span className="text-sm font-medium text-tg-text">Min ROI</span>
                  <span className="text-sm text-tg-hint">
                    {draftFilters.min_roi !== 0 ? `${draftFilters.min_roi}%` : 'Any'}
                  </span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={50}
                  step={1}
                  value={draftFilters.min_roi}
                  onChange={(e) =>
                    setDraftFilters((f) => ({ ...f, min_roi: Number(e.target.value) }))
                  }
                  className="w-full accent-tg-button"
                />
              </div>

              {/* Min Win Rate */}
              <div>
                <div className="flex justify-between mb-1">
                  <span className="text-sm font-medium text-tg-text">Min Win Rate</span>
                  <span className="text-sm text-tg-hint">
                    {draftFilters.min_win_rate > 0 ? `${draftFilters.min_win_rate}%` : 'Any'}
                  </span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={80}
                  step={5}
                  value={draftFilters.min_win_rate}
                  onChange={(e) =>
                    setDraftFilters((f) => ({ ...f, min_win_rate: Number(e.target.value) }))
                  }
                  className="w-full accent-tg-button"
                />
              </div>

              {/* Max Drawdown */}
              <div>
                <div className="flex justify-between mb-1">
                  <span className="text-sm font-medium text-tg-text">Max Drawdown</span>
                  <span className="text-sm text-tg-hint">
                    {draftFilters.max_drawdown < 100 ? `${draftFilters.max_drawdown}%` : 'Any'}
                  </span>
                </div>
                <input
                  type="range"
                  min={10}
                  max={100}
                  step={5}
                  value={draftFilters.max_drawdown}
                  onChange={(e) =>
                    setDraftFilters((f) => ({ ...f, max_drawdown: Number(e.target.value) }))
                  }
                  className="w-full accent-tg-button"
                />
              </div>

              {/* Min Days Active */}
              <div>
                <p className="text-sm font-medium text-tg-text mb-2">Min Days Active</p>
                <div className="flex gap-2">
                  {MIN_DAYS_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      onClick={() => setDraftFilters((f) => ({ ...f, min_days: opt.value }))}
                      className={`flex-1 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                        draftFilters.min_days === opt.value
                          ? 'border-tg-button text-tg-button'
                          : 'border-gray-200 dark:border-gray-700 text-tg-hint'
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Кнопка — всегда видна, не перекрывается Tab Bar */}
            <div
              className="flex-shrink-0 px-4 pt-2"
              style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 16px)' }}
            >
              <button
                onClick={applyFilters}
                className="w-full py-3 rounded-xl text-sm font-semibold text-tg-button-text"
                style={{ background: 'var(--tg-theme-button-color)' }}
              >
                Apply Filters
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
