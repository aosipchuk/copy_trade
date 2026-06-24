export interface TraderStats {
  period: string
  pnl_usd: number
  roi_pct: number
  volume_usd: number
  win_rate_pct: number | null
  max_drawdown_usd: number | null
  max_drawdown_pct: number | null
  trade_count: number | null
  avg_trade_duration_hrs: number | null
  first_trade_at: string | null
  sharpe_ratio: number | null
  sortino_ratio: number | null
  profit_factor: number | null
  avg_pnl_per_trade: number | null
  max_losing_streak: number | null
  profitable_days_pct: number | null
  avg_trades_per_day: number | null
  daily_pnl_std_dev: number | null
  long_ratio_pct: number | null
  avg_position_size_usd: number | null
  fees_paid_usd: number | null
  calmar_ratio: number | null
  composite_score: number | null
  max_drawdown_duration_days: number | null
}

export interface TraderFilters {
  quality: boolean
  subscribed_only: boolean
  min_win_rate: number
  max_drawdown: number
  min_days: number
  min_trades: number
  min_composite_score: number
  min_profit_factor: number
  max_losing_streak: number | null
  min_profitable_days_pct: number
  max_avg_trades_per_day: number | null
  min_calmar: number
}

export interface TraderListItem {
  id: number
  hl_address: string
  display_name: string | null
  stats: TraderStats[]
}

export interface TraderListResponse {
  items: TraderListItem[]
  next_cursor: string | null
}

export interface TraderDetail extends TraderListItem {
  is_active: boolean
  last_seen_at: string | null
}

export interface TraderSummary {
  id: number
  hl_address: string
  display_name: string | null
  stats: Record<string, TraderStats>
  equity_curve_week: EquityPoint[]
  open_positions: PositionItem[]
  recent_trades: ClosedTradeItem[]
}

export interface EquityPoint {
  ts: string
  pnl: number
  roi: number
}

export interface PositionItem {
  coin: string
  side: string
  size: number
  entry_px: number
  unrealized_pnl: number
  leverage: number | null
  subscription_id: number | null
}

export interface ActivityItem {
  action: string
  coin: string | null
  side: string | null
  size: number | null
  pnl: number | null
  ts: string
  subscription_trader: string | null
}

export interface FillItem {
  coin: string
  px: number
  sz: number
  side: string
  dir: string
  time: number
  closed_pnl: number
}

export interface ClosedTradeItem {
  coin: string
  direction: 'long' | 'short'
  size: number
  avg_px: number
  pnl: number
  time: number
  fill_count: number
}

export type SizingMode = 'fixed_ratio' | 'fixed_usd' | 'equity_pct'

export interface Subscription {
  id: number
  trader_id: number
  trader_address: string | null
  trader_name: string | null
  max_allocation_usd: number
  copy_ratio_pct: number
  stop_loss_pct: number
  max_leverage: number
  sizing_mode: SizingMode
  max_per_coin_usd: number | null
  allowed_coins: string[] | null
  is_active: boolean
  is_demo: boolean
  created_at: string
  realized_pnl: number
  unrealized_pnl: number
  trade_count: number
}

export interface SubscriptionCreate {
  trader_id: number
  max_allocation_usd: number
  copy_ratio_pct: number
  stop_loss_pct: number
  max_leverage: number
  sizing_mode: SizingMode
  max_per_coin_usd?: number
  allowed_coins?: string[]
  is_demo?: boolean
}

export interface SubscriptionUpdate {
  max_allocation_usd?: number
  copy_ratio_pct?: number
  stop_loss_pct?: number
  max_leverage?: number
  sizing_mode?: SizingMode
  max_per_coin_usd?: number | null
  allowed_coins?: string[] | null
}

export interface WalletBalance {
  account_value: number
  total_margin_used: number
  available: number
}

export interface WalletSetupResponse {
  agent_address: string
  nonce: number
  eip712_payload: Record<string, unknown>
}

export interface AgentStatus {
  has_agent: boolean
  agent_address: string | null
  is_active: boolean
  approved_at: string | null
  builder_fee_approved: boolean
}

export interface BuilderSetupResponse {
  nonce: number
  eip712_payload: Record<string, unknown>
}

export interface PortfolioRisk {
  portfolio_stop_loss_pct: number | null
}

export type Period = 'day' | 'week' | 'month' | 'allTime'
export type SortKey = 'roi' | 'pnl' | 'volume'

export interface DemoOpenPosition {
  subscription_id: number
  trader_name: string | null
  coin: string
  side: string
  size: number
  entry_price: number
  current_price: number
  unrealized_pnl: number
}

export interface DemoPortfolioResponse {
  total_realized_pnl: number
  total_unrealized_pnl: number
  trade_count: number
  win_count: number
  win_rate_pct: number
  open_positions: DemoOpenPosition[]
}

export interface DemoTradeItem {
  id: number
  coin: string
  side: string
  size: number
  price: number
  trade_type: string
  realized_pnl: number | null
  executed_at: string
}

export interface DemoClosedPositionItem {
  coin: string
  direction: string
  size: number
  entry_price: number
  close_price: number
  realized_pnl: number
  opened_at: string
  closed_at: string
}
