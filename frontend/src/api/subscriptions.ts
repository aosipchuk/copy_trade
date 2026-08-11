import type { Subscription, SubscriptionCreate, SubscriptionUpdate } from '../types'
import { http } from './http'

export async function createSubscription(data: SubscriptionCreate): Promise<Subscription> {
  const res = await http.post<Subscription>('/subscriptions', data)
  return res.data
}

export async function listSubscriptions(
  isDemo = false,
  includeInactive = false,
): Promise<Subscription[]> {
  const res = await http.get<Subscription[]>('/subscriptions', {
    params: {
      is_demo: isDemo,
      ...(includeInactive ? { include_inactive: true } : {}),
    },
  })
  return res.data
}

export async function fetchSubscription(id: number): Promise<Subscription> {
  const res = await http.get<Subscription>(`/subscriptions/${id}`)
  return res.data
}

export async function updateSubscription(id: number, data: SubscriptionUpdate): Promise<Subscription> {
  const res = await http.patch<Subscription>(`/subscriptions/${id}`, data)
  return res.data
}

export async function deleteSubscription(id: number, closePositions: boolean): Promise<void> {
  await http.delete(`/subscriptions/${id}`, { params: { close_positions: closePositions } })
}
