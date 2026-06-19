import type { DemoPortfolioResponse, DemoTradeItem } from '../types'
import { http } from './http'

export async function fetchDemoPortfolio(): Promise<DemoPortfolioResponse> {
  const res = await http.get<DemoPortfolioResponse>('/demo/portfolio')
  return res.data
}

export async function fetchDemoSubscriptionTrades(
  subscriptionId: number,
  limit = 100,
): Promise<DemoTradeItem[]> {
  const res = await http.get<DemoTradeItem[]>(
    `/demo/subscription/${subscriptionId}/trades`,
    { params: { limit } },
  )
  return res.data
}
