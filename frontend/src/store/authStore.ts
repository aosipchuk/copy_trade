import { create } from 'zustand'
import { authenticateWithTelegram, fetchCurrentUser } from '../api/auth'
import type { AuthUser } from '../types'

interface AuthState {
  jwt: string | null
  user: AuthUser | null
  loading: boolean
  error: string | null
  login: (initData: string) => Promise<void>
  loadCurrentUser: () => Promise<void>
  setJwt: (jwt: string) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  jwt: localStorage.getItem('jwt'),
  user: null,
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

  loadCurrentUser: async () => {
    if (!localStorage.getItem('jwt')) return
    try {
      const user = await fetchCurrentUser()
      set({ user })
    } catch {
      set({ user: null })
    }
  },

  setJwt: (jwt) => {
    localStorage.setItem('jwt', jwt)
    set({ jwt, user: null })
  },

  logout: () => {
    localStorage.removeItem('jwt')
    set({ jwt: null, user: null })
  },
}))
