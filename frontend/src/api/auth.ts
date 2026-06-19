import { http } from './http'

interface AuthResponse {
  access_token: string
  token_type: string
}

export async function authenticateWithTelegram(initData: string): Promise<string> {
  const res = await http.post<AuthResponse>('/auth/telegram', { init_data: initData })
  return res.data.access_token
}
