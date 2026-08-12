import type { CopyAccountState, CopyPreflight } from '../types'
import { http } from './http'

export async function fetchCopyAccount(): Promise<CopyAccountState> {
  const response = await http.get<CopyAccountState>('/copy-execution/account')
  return response.data
}

export async function selectCopyAccount(
  accountAddress: string,
): Promise<CopyAccountState> {
  const response = await http.put<CopyAccountState>('/copy-execution/account', {
    account_address: accountAddress,
    dedicated_account_acknowledged: true,
  })
  return response.data
}

export async function runCopyPreflight(): Promise<CopyPreflight> {
  const response = await http.post<CopyPreflight>('/copy-execution/preflight')
  return response.data
}
