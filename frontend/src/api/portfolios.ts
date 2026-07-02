import type {
  ModelPortfolioDetail,
  ModelPortfolioListItem,
  PortfolioBillingCheckoutCreate,
  PortfolioBillingCheckoutResponse,
  PortfolioBillingStatus,
  PortfolioBacktest,
  PortfolioExplanation,
  PortfolioRebalanceApplyResponse,
  PortfolioRebalanceEvent,
  PortfolioRebalancePreview,
  PortfolioWeeklyReport,
  UserPortfolioActivationResponse,
  UserPortfolioSubscriptionCreate,
  UserPortfolioSubscriptionDetail,
  UserPortfolioSubscriptionUpdate,
} from '../types'
import { http } from './http'

export async function fetchPortfolios(): Promise<ModelPortfolioListItem[]> {
  const res = await http.get<ModelPortfolioListItem[]>('/portfolios')
  return res.data
}

export async function fetchPortfolio(slug: string): Promise<ModelPortfolioDetail> {
  const res = await http.get<ModelPortfolioDetail>(`/portfolios/${slug}`)
  return res.data
}

export async function fetchPortfolioBacktests(slug: string): Promise<PortfolioBacktest[]> {
  const res = await http.get<PortfolioBacktest[]>(`/portfolios/${slug}/backtests`)
  return res.data
}

export async function fetchPortfolioExplanations(
  slug: string,
): Promise<PortfolioExplanation> {
  const res = await http.get<PortfolioExplanation>(
    `/portfolios/${slug}/explanations`,
  )
  return res.data
}

export async function fetchPortfolioWeeklyReport(
  slug: string,
): Promise<PortfolioWeeklyReport | null> {
  const res = await http.get<PortfolioWeeklyReport | null>(
    `/portfolios/${slug}/weekly-report`,
  )
  return res.data
}

export async function generatePortfolioWeeklyReport(
  slug: string,
): Promise<PortfolioWeeklyReport> {
  const res = await http.post<PortfolioWeeklyReport>(
    `/portfolios/${slug}/weekly-report`,
  )
  return res.data
}

export async function fetchPortfolioSubscriptions(params?: {
  is_demo?: boolean
  portfolio_id?: number
  active_only?: boolean
}): Promise<UserPortfolioSubscriptionDetail[]> {
  const res = await http.get<UserPortfolioSubscriptionDetail[]>(
    '/portfolio-subscriptions',
    { params },
  )
  return res.data
}

export async function fetchPortfolioSubscription(
  id: number,
): Promise<UserPortfolioSubscriptionDetail> {
  const res = await http.get<UserPortfolioSubscriptionDetail>(
    `/portfolio-subscriptions/${id}`,
  )
  return res.data
}

export async function fetchPortfolioBillingStatus(params: {
  portfolio_id: number
  active_version_id: number
}): Promise<PortfolioBillingStatus> {
  const res = await http.get<PortfolioBillingStatus>(
    '/portfolio-subscriptions/billing/status',
    { params },
  )
  return res.data
}

export async function createPortfolioBillingCheckout(
  body: PortfolioBillingCheckoutCreate,
): Promise<PortfolioBillingCheckoutResponse> {
  const res = await http.post<PortfolioBillingCheckoutResponse>(
    '/portfolio-subscriptions/billing/checkout',
    body,
  )
  return res.data
}

export async function activateDemoPortfolio(
  body: UserPortfolioSubscriptionCreate,
): Promise<UserPortfolioActivationResponse> {
  const res = await http.post<UserPortfolioActivationResponse>(
    '/portfolio-subscriptions',
    body,
  )
  return res.data
}

export async function activateLivePortfolio(
  body: UserPortfolioSubscriptionCreate,
): Promise<UserPortfolioActivationResponse> {
  const res = await http.post<UserPortfolioActivationResponse>(
    '/portfolio-subscriptions',
    body,
  )
  return res.data
}

export async function cancelPortfolioSubscription(
  id: number,
): Promise<UserPortfolioSubscriptionDetail> {
  const res = await http.delete<UserPortfolioSubscriptionDetail>(
    `/portfolio-subscriptions/${id}`,
  )
  return res.data
}

export async function updatePortfolioSubscription(
  id: number,
  body: UserPortfolioSubscriptionUpdate,
): Promise<UserPortfolioSubscriptionDetail> {
  const res = await http.patch<UserPortfolioSubscriptionDetail>(
    `/portfolio-subscriptions/${id}`,
    body,
  )
  return res.data
}

export async function previewPortfolioRebalance(
  id: number,
): Promise<PortfolioRebalancePreview> {
  const res = await http.post<PortfolioRebalancePreview>(
    `/portfolio-subscriptions/${id}/preview-rebalance`,
  )
  return res.data
}

export async function applyPortfolioRebalance(
  id: number,
): Promise<PortfolioRebalanceApplyResponse> {
  const res = await http.post<PortfolioRebalanceApplyResponse>(
    `/portfolio-subscriptions/${id}/apply-rebalance`,
  )
  return res.data
}

export async function fetchPortfolioRebalanceHistory(
  id: number,
): Promise<PortfolioRebalanceEvent[]> {
  const res = await http.get<PortfolioRebalanceEvent[]>(
    `/portfolio-subscriptions/${id}/rebalance-history`,
  )
  return res.data
}
