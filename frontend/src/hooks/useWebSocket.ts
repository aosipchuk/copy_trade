import { useEffect, useRef, useState } from 'react'

const WS_BASE = import.meta.env.VITE_WS_URL || 'ws://localhost:8000'

export function useTraderPositionsWS<T>(traderId: number | null) {
  const [data, setData] = useState<T | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!traderId) return
    const token = localStorage.getItem('jwt')
    if (!token) return

    const url = `${WS_BASE}/ws/traders/${traderId}/positions?token=${token}`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onmessage = (evt) => {
      try {
        setData(JSON.parse(evt.data) as T)
      } catch {
        // ignore parse errors
      }
    }

    ws.onerror = () => ws.close()

    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [traderId])

  return data
}
