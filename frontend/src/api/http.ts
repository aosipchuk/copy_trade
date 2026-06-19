import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL || '/api'

const isNgrok = BASE_URL.includes('ngrok')

export const http = axios.create({
  baseURL: BASE_URL,
  timeout: 15_000,
  headers: isNgrok ? { 'ngrok-skip-browser-warning': 'true' } : {},
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('jwt')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

let reloading = false

http.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401 && !reloading) {
      reloading = true
      localStorage.removeItem('jwt')
      window.location.reload()
    }
    return Promise.reject(err)
  },
)
