import type { AdminTraderImportResponse } from '../types'
import { http } from './http'

export async function importAdminTrader(
  hlAddress: string,
): Promise<AdminTraderImportResponse> {
  const res = await http.post<AdminTraderImportResponse>('/admin/traders/import', {
    hl_address: hlAddress,
  })
  return res.data
}
