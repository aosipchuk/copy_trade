import type {
  DemoClosedPositionItem,
  DemoPortfolioResponse,
  DemoResetResponse,
  DemoTradeItem,
} from '../types'
import { http } from './http'

export async function fetchDemoPortfolio(): Promise<DemoPortfolioResponse> {
  const res = await http.get<DemoPortfolioResponse>('/demo/portfolio')
  return res.data
}

export async function resetDemoStats(): Promise<DemoResetResponse> {
  const res = await http.post<DemoResetResponse>('/demo/reset')
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

export async function fetchDemoClosedPositions(
  subscriptionId: number,
  limit = 100,
): Promise<DemoClosedPositionItem[]> {
  const res = await http.get<DemoClosedPositionItem[]>(
    `/demo/subscription/${subscriptionId}/closed-positions`,
    { params: { limit } },
  )
  return res.data
}
