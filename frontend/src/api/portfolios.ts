import type {
  ModelPortfolioDetail,
  ModelPortfolioListItem,
  PortfolioBacktest,
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
