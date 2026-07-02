import type {
  ModelPortfolioDetail,
  ModelPortfolioListItem,
  PortfolioBillingCheckoutCreate,
  PortfolioBillingCheckoutResponse,
  PortfolioBillingStatus,
  PortfolioBacktest,
  UserPortfolioActivationResponse,
  UserPortfolioSubscriptionCreate,
  UserPortfolioSubscriptionDetail,
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
