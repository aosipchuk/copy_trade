import type {
  ClosedTradeItem,
  EquityPoint,
  FillItem,
  Period,
  PositionItem,
  SortKey,
  TraderDetail,
  TraderFilters,
  TraderListResponse,
  TraderSummary,
} from '../types'
import { http } from './http'

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api'

function apiUrl(path: string): URL {
  const base = API_BASE_URL.endsWith('/') ? API_BASE_URL.slice(0, -1) : API_BASE_URL
  return new URL(`${base}${path}`, window.location.origin)
}

interface TraderExportLink {
  path: string
  expires_in: number
}

export async function fetchTraders(params: {
  period: Period
  sort: SortKey
  limit?: number
  cursor?: string | null
  filters?: Partial<TraderFilters>
  address?: string
}): Promise<TraderListResponse> {
  const f = params.filters ?? {}
  const res = await http.get<TraderListResponse>('/traders', {
    params: {
      period: params.period,
      sort: params.sort,
      limit: params.limit ?? 50,
      ...(params.cursor ? { cursor: params.cursor } : {}),
      ...(params.address ? { address: params.address } : {}),
      ...(f.quality ? { quality: true } : {}),
      ...(f.subscribed_only ? { subscribed_only: true } : {}),
      ...(f.min_roi && f.min_roi !== 0 ? { min_roi: f.min_roi } : {}),
      ...(f.min_win_rate && f.min_win_rate > 0 ? { min_win_rate: f.min_win_rate } : {}),
      ...(f.max_drawdown != null && f.max_drawdown < 100 ? { max_drawdown: f.max_drawdown } : {}),
      ...(f.min_days && f.min_days > 0 ? { min_days: f.min_days } : {}),
      ...(f.min_trades && f.min_trades > 0 ? { min_trades: f.min_trades } : {}),
      ...(f.min_composite_score && f.min_composite_score > 0 ? { min_composite_score: f.min_composite_score } : {}),
      ...(f.min_profit_factor && f.min_profit_factor > 0 ? { min_profit_factor: f.min_profit_factor } : {}),
      ...(f.max_losing_streak != null ? { max_losing_streak: f.max_losing_streak } : {}),
      ...(f.min_profitable_days_pct && f.min_profitable_days_pct > 0 ? { min_profitable_days_pct: f.min_profitable_days_pct } : {}),
      ...(f.max_avg_trades_per_day != null ? { max_avg_trades_per_day: f.max_avg_trades_per_day } : {}),
      ...(f.min_calmar && f.min_calmar > 0 ? { min_calmar: f.min_calmar } : {}),
    },
  })
  return res.data
}

export async function fetchTrader(id: number): Promise<TraderDetail> {
  const res = await http.get<TraderDetail>(`/traders/${id}`)
  return res.data
}

export async function fetchEquityCurve(id: number, period: Period): Promise<EquityPoint[]> {
  const res = await http.get<EquityPoint[]>(`/traders/${id}/equity-curve`, {
    params: { period },
  })
  return res.data
}

export async function fetchPositions(id: number): Promise<PositionItem[]> {
  const res = await http.get<PositionItem[]>(`/traders/${id}/positions`)
  return res.data
}

export async function fetchFills(id: number, limit = 50): Promise<FillItem[]> {
  const res = await http.get<FillItem[]>(`/traders/${id}/fills`, { params: { limit } })
  return res.data
}

export async function fetchClosedTrades(id: number, limit = 20): Promise<ClosedTradeItem[]> {
  const res = await http.get<ClosedTradeItem[]>(`/traders/${id}/closed-trades`, { params: { limit } })
  return res.data
}

export async function fetchTraderSummary(id: number): Promise<TraderSummary> {
  const res = await http.get<TraderSummary>(`/traders/${id}/summary`)
  return res.data
}

export async function downloadTraderExport(id: number): Promise<void> {
  const res = await http.post<TraderExportLink>(`/traders/${id}/export-link`)
  const url = apiUrl(res.data.path).toString()
  const telegram = (
    window as Window & {
      Telegram?: {
        WebApp?: {
          openLink?: (url: string, options?: { try_instant_view?: boolean }) => void
        }
      }
    }
  ).Telegram?.WebApp

  if (telegram?.openLink) {
    telegram.openLink(url, { try_instant_view: false })
    return
  }

  window.location.assign(url)
}
