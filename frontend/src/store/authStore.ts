import { create } from 'zustand'
import { authenticateWithTelegram } from '../api/auth'

interface AuthState {
  jwt: string | null
  loading: boolean
  error: string | null
  login: (initData: string) => Promise<void>
  setJwt: (jwt: string) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  jwt: localStorage.getItem('jwt'),
  loading: false,
  error: null,

  login: async (initData) => {
    set({ loading: true, error: null })
    try {
      const token = await authenticateWithTelegram(initData)
      localStorage.setItem('jwt', token)
      set({ jwt: token, loading: false })
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Authentication failed'
      set({ error: msg, loading: false })
    }
  },

  setJwt: (jwt) => {
    localStorage.setItem('jwt', jwt)
    set({ jwt })
  },

  logout: () => {
    localStorage.removeItem('jwt')
    set({ jwt: null })
  },
}))
