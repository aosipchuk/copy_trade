export interface TraderStats {
  period: string
  pnl_usd: number | null
  roi_pct: number | null
  volume_usd: number | null
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
  min_roi: number
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
  side: string | null
  size: number
  entry_px: number | null
  unrealized_pnl: number
  leverage: number | null
  subscription_id?: number | null
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
  trader_id: number | null
  trader_address: string | null
  trader_name: string | null
  max_allocation_usd: number
  copy_ratio_pct: number
  stop_loss_pct: number
  max_leverage: number
  sizing_mode: SizingMode
  max_per_coin_usd: number | null
  allowed_coins: string[] | null
  source_type: 'manual' | 'model_portfolio' | 'new_wallet'
  source_id: number | null
  source_version_id: number | null
  managed_by_portfolio: boolean
  is_active: boolean
  is_demo: boolean
  expires_at: string | null
  ended_reason: string | null
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

export interface AuthUser {
  id: number
  telegram_id: number
  username: string | null
  first_name: string | null
  hl_address: string | null
  is_admin: boolean
}

export type AdminTraderImportStatus =
  | 'imported'
  | 'refreshed'
  | 'no_fills'
  | 'no_perp_activity'

export interface AdminTraderImportResponse {
  status: AdminTraderImportStatus
  message: string
  trader: TraderDetail
  has_perp_activity: boolean | null
}

export type RiskProfile = 'conservative' | 'balanced' | 'aggressive'
export type ModelPortfolioStatus = 'draft' | 'active' | 'paused' | 'retired'
export type PortfolioVersionStatus = 'draft' | 'published' | 'retired' | 'rejected'
export type UserPortfolioStatus = 'trialing' | 'active' | 'past_due' | 'paused' | 'canceled'
export type PortfolioItemStatus = 'active' | 'removed' | 'failed' | 'paused'
export type BillingProvider = 'stripe' | 'admin_override'
export type RebalanceEventType = 'scheduled' | 'emergency' | 'manual' | 'user_apply'
export type RebalanceStatus =
  | 'draft'
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'skipped'
export type RebalanceDiffAction =
  | 'add_trader'
  | 'remove_trader'
  | 'change_weight'
  | 'change_risk_settings'
  | 'no_change'
  | 'blocked_by_user_conflict'
  | 'blocked_by_payment'
  | 'blocked_by_wallet'
  | 'failed_risk_check'
export type RebalancePreviewStatus = 'up_to_date' | 'pending' | 'blocked'

export interface PortfolioCurrentVersionSummary {
  id: number
  version_no: number
  status: PortfolioVersionStatus
  valid_from: string | null
  approved_at: string | null
  trader_count: number
  target_weight_sum_pct: number
  summary_json: Record<string, unknown> | null
}

export interface PortfolioBacktestSummary {
  id: number
  portfolio_version_id: number
  period_days: number
  initial_equity_usd: number
  total_return_pct: number | null
  max_drawdown_pct: number | null
  sharpe_ratio: number | null
  sortino_ratio: number | null
  win_rate_pct: number | null
  assumptions_json: Record<string, unknown>
  created_at: string
}

export interface ModelPortfolioListItem {
  id: number
  slug: string
  name: string
  risk_profile: RiskProfile
  status: ModelPortfolioStatus
  description: string | null
  methodology_version: string
  rebalance_cadence: string
  min_equity_usd: number
  monthly_price_usd: number
  trial_days: number
  created_at: string
  updated_at: string
  current_version: PortfolioCurrentVersionSummary | null
  latest_backtest: PortfolioBacktestSummary | null
}

export interface ModelPortfolioAllocation {
  id: number
  version_id: number
  trader_id: number | null
  trader_address: string | null
  trader_display_name: string | null
  target_weight_pct: number
  copy_ratio_pct: number
  max_leverage: number
  stop_loss_pct: number
  sizing_mode: SizingMode
  max_per_coin_usd: number | null
  allowed_coins: string[] | null
  reason_code: string | null
  reason_text: string | null
  portfolio_score: number | null
  source_metrics: Record<string, unknown> | null
  score_snapshot: Record<string, unknown> | null
  constraint_snapshot: Record<string, unknown> | null
  created_at: string
}

export interface ModelPortfolioPublishedVersion {
  id: number
  portfolio_id: number
  version_no: number
  status: PortfolioVersionStatus
  valid_from: string | null
  valid_to: string | null
  created_by: number | null
  approved_by: number | null
  approved_at: string | null
  approval_note: string | null
  selection_started_at: string | null
  selection_finished_at: string | null
  facts_hash: string | null
  summary_json: Record<string, unknown> | null
  created_at: string
  allocations: ModelPortfolioAllocation[]
}

export interface PortfolioBacktest extends PortfolioBacktestSummary {
  turnover_pct: number | null
  fees_usd: number | null
  slippage_usd: number | null
  missed_trade_count: number
  equity_curve_json: {
    source?: string
    points?: Array<Record<string, unknown>>
    [key: string]: unknown
  }
}

export interface ModelPortfolioDetail extends Omit<ModelPortfolioListItem, 'current_version' | 'latest_backtest'> {
  current_version: ModelPortfolioPublishedVersion
  trader_details_visible: boolean
  backtests: PortfolioBacktest[]
}

export interface UserPortfolioSubscriptionCreate {
  portfolio_id: number
  active_version_id: number
  is_demo: boolean
  auto_rebalance: boolean
  total_allocation_usd: number
  close_removed_positions: boolean
  risk_disclosure_accepted?: boolean
}

export interface UserPortfolioSubscription {
  id: number
  user_id: number
  portfolio_id: number
  active_version_id: number
  status: UserPortfolioStatus
  is_demo: boolean
  auto_rebalance: boolean
  total_allocation_usd: number
  close_removed_positions: boolean
  billing_provider: string | null
  billing_customer_id: string | null
  billing_subscription_id: string | null
  current_period_end: string | null
  created_at: string
  updated_at: string
  canceled_at: string | null
}

export interface UserPortfolioItem {
  id: number
  user_portfolio_subscription_id: number
  subscription_id: number
  portfolio_version_id: number
  allocation_id: number
  trader_id: number | null
  target_allocation_usd: number
  target_weight_pct: number
  status: PortfolioItemStatus
  created_at: string
  removed_at: string | null
}

export interface UserPortfolioItemDetail extends UserPortfolioItem {
  subscription: Subscription
  trader_address: string | null
  trader_display_name: string | null
}

export interface UserPortfolioSubscriptionDetail extends UserPortfolioSubscription {
  portfolio_slug: string
  portfolio_name: string
  active_version_no: number
  trader_details_visible: boolean
  items: UserPortfolioItemDetail[]
}

export interface UserPortfolioSubscriptionUpdate {
  auto_rebalance?: boolean | null
  close_removed_positions?: boolean | null
}

export interface PortfolioActivationConflict {
  trader_id: number
  trader_address: string
  trader_display_name: string | null
  subscription_id: number
  is_demo: boolean
}

export interface UserPortfolioActivationResponse extends UserPortfolioSubscriptionDetail {
  created: boolean
  conflicts: PortfolioActivationConflict[]
}

export interface PortfolioRebalanceEvent {
  id: number
  portfolio_id: number
  from_version_id: number | null
  to_version_id: number | null
  user_portfolio_subscription_id: number | null
  event_type: RebalanceEventType
  status: RebalanceStatus
  diff_json: Record<string, unknown> | null
  error_msg: string | null
  idempotency_key: string
  created_at: string
  executed_at: string | null
}

export interface PortfolioRebalanceDiffItem {
  action: RebalanceDiffAction
  trader_id: number | null
  trader_address: string | null
  trader_display_name: string | null
  subscription_id: number | null
  from_allocation_id: number | null
  to_allocation_id: number | null
  from_weight_pct: number | null
  to_weight_pct: number | null
  from_allocation_usd: number | null
  to_allocation_usd: number | null
  changed_fields: string[]
  message: string
  rationale: string | null
  source_facts: Record<string, unknown> | null
}

export interface PortfolioRebalancePreview {
  user_portfolio_subscription_id: number
  portfolio_id: number
  portfolio_slug: string
  portfolio_name: string
  from_version_id: number
  from_version_no: number
  to_version_id: number
  to_version_no: number
  status: RebalancePreviewStatus
  can_apply: boolean
  auto_rebalance: boolean
  close_removed_positions: boolean
  is_demo: boolean
  total_allocation_usd: number
  diff: PortfolioRebalanceDiffItem[]
  blocker: string | null
}

export interface PortfolioRebalanceApplyResponse extends PortfolioRebalancePreview {
  event: PortfolioRebalanceEvent
  portfolio_subscription: UserPortfolioSubscriptionDetail
}

export interface PortfolioBillingCheckoutCreate {
  portfolio_id: number
  active_version_id: number
  total_allocation_usd: number
  success_url?: string | null
  cancel_url?: string | null
}

export interface PortfolioBillingStatus {
  portfolio_id: number
  active_version_id: number
  paid: boolean
  can_activate_live: boolean
  can_rebalance: boolean
  beta_override: boolean
  provider: BillingProvider | null
  status: UserPortfolioStatus | null
  current_period_end: string | null
  portfolio_subscription: UserPortfolioSubscriptionDetail | null
  message: string
}

export interface PortfolioBillingCheckoutResponse {
  provider: BillingProvider
  provider_configured: boolean
  checkout_url: string | null
  portfolio_subscription: UserPortfolioSubscriptionDetail
  billing_status: PortfolioBillingStatus
  message: string
}

export type PortfolioReportGeneratedBy =
  | 'template'
  | 'openai_compatible'
  | 'fallback'

export interface PortfolioAllocationExplanation {
  allocation_id: number
  trader_id: number | null
  trader_address: string | null
  trader_display_name: string | null
  generated_by: PortfolioReportGeneratedBy
  prompt_version: string
  explanation: string
  source_facts: Record<string, unknown>
  used_source_fact_keys: string[]
}

export interface PortfolioExplanation {
  portfolio_id: number
  portfolio_slug: string
  portfolio_name: string
  version_id: number
  version_no: number
  generated_at: string
  generated_by: PortfolioReportGeneratedBy
  prompt_version: string
  trader_details_visible: boolean
  summary: string
  source_facts: Record<string, unknown>
  allocations: PortfolioAllocationExplanation[]
}

export interface PortfolioReportSection {
  title: string
  body: string
}

export interface PortfolioReportAllocationNote {
  allocation_id: number
  trader_id: number | null
  trader_address: string | null
  trader_display_name: string | null
  note: string
}

export interface PortfolioWeeklyReport {
  id: number
  portfolio_id: number
  portfolio_slug: string
  portfolio_name: string
  portfolio_version_id: number
  version_no: number
  report_type: 'weekly'
  period_start: string
  period_end: string
  generated_by: PortfolioReportGeneratedBy
  prompt_version: string
  trader_details_visible: boolean
  source_facts: Record<string, unknown>
  report_json: Record<string, unknown>
  summary: string
  sections: PortfolioReportSection[]
  allocation_notes: PortfolioReportAllocationNote[]
  created_at: string
}

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

export interface DemoResetResponse {
  deleted_trades: number
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

export type NewWalletCandidateStatus =
  | 'pending'
  | 'qualified'
  | 'rejected'
  | 'subscribed'
  | 'expired'
  | 'disabled'
export type UserNewWalletSubscriptionStatus = 'active' | 'paused' | 'canceled'
export type UserNewWalletItemStatus =
  | 'active'
  | 'expired'
  | 'failed'
  | 'removed'

export interface NewWalletFundingLink {
  id: number
  depth: number
  wallet_address: string
  funded_by_address: string | null
  amount_usdc: number | null
  event_time: string | null
  tx_hash: string | null
  balance_usd: number | null
  balance_source: string | null
}

export interface NewWalletCandidate {
  id: number
  trader_id: number | null
  hl_address: string
  status: NewWalletCandidateStatus
  detected_at: string
  funded_at: string | null
  qualified_at: string | null
  last_checked_at: string | null
  chain_depth: number | null
  chain_total_balance_usd: number | null
  threshold_usd_snapshot: number | null
  reject_reason: string | null
  first_seen_tx_hash: string | null
  links: NewWalletFundingLink[]
  user_item_status: UserNewWalletItemStatus | null
  user_child_subscription_id: number | null
  user_child_expires_at: string | null
}

export interface NewWalletCandidateListResponse {
  items: NewWalletCandidate[]
  next_cursor: string | null
}

export interface NewWalletSettingsSnapshot {
  discovery_enabled: boolean
  auto_attach_enabled: boolean
  funding_provider_configured: boolean
  chain_balance_threshold_usd: number
  max_chain_depth: number
  subscription_ttl_days: number
  min_incoming_amount_usd: number
  max_active_per_user: number
  default_max_per_wallet_usd: number
}

export interface UserNewWalletItem {
  id: number
  candidate_id: number
  subscription_id: number
  trader_id: number
  target_allocation_usd: number
  status: UserNewWalletItemStatus
  created_at: string
  expires_at: string
  ended_at: string | null
  error_msg: string | null
  realized_pnl: number
  unrealized_pnl: number
  trade_count: number
  candidate: NewWalletCandidate | null
}

export interface UserNewWalletSubscription {
  id: number
  user_id: number
  status: UserNewWalletSubscriptionStatus
  is_demo: boolean
  total_allocation_usd: number
  max_active_wallets: number
  max_per_wallet_usd: number
  copy_ratio_pct: number
  stop_loss_pct: number
  max_leverage: number
  sizing_mode: SizingMode
  allowed_coins: string[] | null
  close_positions_on_expire: boolean
  created_at: string
  updated_at: string
  canceled_at: string | null
  items: UserNewWalletItem[]
}

export interface NewWalletSummary {
  counts_by_status: Record<string, number>
  active_subscription: UserNewWalletSubscription | null
  settings: NewWalletSettingsSnapshot
}

export interface NewWalletSubscriptionCreate {
  is_demo: boolean
  total_allocation_usd: number
  max_active_wallets: number
  max_per_wallet_usd: number
  copy_ratio_pct: number
  stop_loss_pct: number
  max_leverage: number
  sizing_mode: SizingMode
  allowed_coins?: string[] | null
  close_positions_on_expire: boolean
  risk_disclosure_accepted: boolean
}
