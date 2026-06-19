import type { ActivityItem, AgentStatus, BuilderSetupResponse, PortfolioRisk, PositionItem, WalletBalance, WalletSetupResponse } from '../types'
import { http } from './http'

export async function walletSetup(): Promise<WalletSetupResponse> {
  const res = await http.post<WalletSetupResponse>('/wallet/setup')
  return res.data
}

export async function walletApprove(params: {
  nonce: number
  userAddress: string
  signature: { r: string; s: string; v: number }
}): Promise<void> {
  await http.post('/wallet/approve', {
    nonce: params.nonce,
    user_address: params.userAddress,
    signature: params.signature,
  })
}

export async function fetchWalletBalance(): Promise<WalletBalance> {
  const res = await http.get<WalletBalance>('/wallet/balance')
  return res.data
}

export async function fetchWalletPositions(): Promise<PositionItem[]> {
  const res = await http.get<PositionItem[]>('/wallet/positions')
  return res.data
}

export async function fetchAgentStatus(): Promise<AgentStatus> {
  const res = await http.get<AgentStatus>('/wallet/status')
  return res.data
}

export async function deleteAgent(): Promise<void> {
  await http.delete('/wallet/agent')
}

export async function closeAllPositions(): Promise<{ closed: number; subscriptions_paused: number }> {
  const res = await http.post<{ closed: number; subscriptions_paused: number }>('/wallet/close-all')
  return res.data
}

export async function fetchWalletActivity(limit = 20): Promise<ActivityItem[]> {
  const res = await http.get<ActivityItem[]>(`/wallet/activity?limit=${limit}`)
  return res.data
}

export async function fetchPortfolioRisk(): Promise<PortfolioRisk> {
  const res = await http.get<PortfolioRisk>('/wallet/portfolio-risk')
  return res.data
}

export async function updatePortfolioRisk(portfolio_stop_loss_pct: number | null): Promise<PortfolioRisk> {
  const res = await http.patch<PortfolioRisk>('/wallet/portfolio-risk', { portfolio_stop_loss_pct })
  return res.data
}

export async function walletBuilderSetup(): Promise<BuilderSetupResponse> {
  const res = await http.get<BuilderSetupResponse>('/wallet/builder-setup')
  return res.data
}

export async function walletBuilderApprove(params: {
  nonce: number
  signature: { r: string; s: string; v: number }
}): Promise<void> {
  await http.post('/wallet/builder-approve', {
    nonce: params.nonce,
    signature: params.signature,
  })
}
