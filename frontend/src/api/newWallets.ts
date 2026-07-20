import type {
  NewWalletCandidateListResponse,
  NewWalletCandidateStatus,
  NewWalletSubscriptionCreate,
  NewWalletSummary,
  UserNewWalletSubscription,
} from '../types'
import { http } from './http'

export async function fetchNewWalletSummary(): Promise<NewWalletSummary> {
  const res = await http.get<NewWalletSummary>('/new-wallets/summary')
  return res.data
}

export async function fetchNewWalletCandidates(params?: {
  status?: NewWalletCandidateStatus
  cursor?: string | null
  limit?: number
}): Promise<NewWalletCandidateListResponse> {
  const res = await http.get<NewWalletCandidateListResponse>(
    '/new-wallets/candidates',
    { params },
  )
  return res.data
}

export async function activateNewWalletSubscription(
  body: NewWalletSubscriptionCreate,
): Promise<UserNewWalletSubscription> {
  const res = await http.post<UserNewWalletSubscription>(
    '/new-wallet-subscriptions',
    body,
  )
  return res.data
}

export async function fetchNewWalletSubscriptions(): Promise<
  UserNewWalletSubscription[]
> {
  const res = await http.get<UserNewWalletSubscription[]>(
    '/new-wallet-subscriptions',
  )
  return res.data
}

export async function fetchNewWalletSubscription(
  id: number,
): Promise<UserNewWalletSubscription> {
  const res = await http.get<UserNewWalletSubscription>(
    `/new-wallet-subscriptions/${id}`,
  )
  return res.data
}

export async function cancelNewWalletSubscription(
  id: number,
  closePositions = true,
): Promise<UserNewWalletSubscription> {
  const res = await http.delete<UserNewWalletSubscription>(
    `/new-wallet-subscriptions/${id}`,
    { params: { close_positions: closePositions } },
  )
  return res.data
}
