import { create } from 'zustand'
import type { Period, SortKey, TraderFilters } from '../types'

export const DEFAULT_FILTERS: TraderFilters = {
  quality: false,
  subscribed_only: false,
  min_win_rate: 0,
  max_drawdown: 100,
  min_days: 0,
  min_trades: 0,
  min_composite_score: 0,
  min_profit_factor: 0,
  max_losing_streak: null,
  min_profitable_days_pct: 0,
  max_avg_trades_per_day: null,
  min_calmar: 0,
}

interface TradersFilterState {
  period: Period
  sort: SortKey
  filters: TraderFilters
  setPeriod: (period: Period) => void
  setSort: (sort: SortKey) => void
  setFilters: (filters: TraderFilters) => void
}

export const useTradersFilterStore = create<TradersFilterState>((set) => ({
  period: 'week',
  sort: 'roi',
  filters: DEFAULT_FILTERS,
  setPeriod: (period) => set({ period }),
  setSort: (sort) => set({ sort }),
  setFilters: (filters) => set({ filters }),
}))
