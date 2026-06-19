import { create } from 'zustand'
import type { Period, SortKey, TraderFilters } from '../types'

export const DEFAULT_FILTERS: TraderFilters = {
  quality: false,
  subscribed_only: false,
  min_win_rate: 0,
  max_drawdown: 100,
  min_days: 0,
  min_trades: 0,
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
