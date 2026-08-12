import { useState } from 'react'
import type { CopyPreflight, ExecutionStatus } from '../types'
import { LoadingSpinner } from './LoadingSpinner'

interface Props {
  status: ExecutionStatus
  reason: string | null
  onPreflight?: () => Promise<CopyPreflight>
  onResume?: () => Promise<{ warning: string }>
  onChanged?: () => void
}

export function ExecutionStatusCard({ status, reason, onPreflight, onResume, onChanged }: Props) {
  const [preflight, setPreflight] = useState<CopyPreflight | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  if (status === 'active') {
    return (
      <div className="rounded-lg border border-green-500/40 bg-green-500/10 px-3 py-2 text-xs text-green-600">
        Copy execution active · existing leader positions remain baseline-only.
      </div>
    )
  }

  const runPreflight = async () => {
    if (!onPreflight) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      setPreflight(await onPreflight())
    } catch (err: unknown) {
      setError((err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Preflight failed')
    } finally {
      setBusy(false)
    }
  }

  const resume = async () => {
    if (!onResume) return
    setBusy(true)
    setError(null)
    try {
      const result = await onResume()
      setNotice(result.warning)
      onChanged?.()
    } catch (err: unknown) {
      setError((err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Resume failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs">
      <div className="font-semibold uppercase text-amber-600">Execution {status}</div>
      <p className="mt-1 text-tg-hint">
        {reason === 'engine_v2_reconciliation_required'
          ? 'Migration safety pause: verify and close positions/open orders, then run preflight and resume manually.'
          : reason?.split('_').join(' ') ?? 'Manual preflight is required.'}
      </p>
      {preflight && (
        <div className="mt-2 space-y-1">
          {preflight.checks.map((check) => (
            <div key={check.code} className={check.ok ? 'text-green-600' : 'text-red-500'}>
              {check.ok ? '✓' : '✕'} {check.message}
            </div>
          ))}
          {preflight.positions.map((position) => (
            <div key={`${position.dex}:${position.coin}`} className="text-red-500">
              Position {position.dex ? `${position.dex}:` : ''}{position.coin} {position.side} {position.size}
            </div>
          ))}
          {preflight.open_orders.map((order) => (
            <div key={order.oid} className="text-red-500">
              Order {order.dex ? `${order.dex}:` : ''}{order.coin} oid {order.oid}{order.cloid ? ` · ${order.cloid}` : ''}
            </div>
          ))}
        </div>
      )}
      {error && <div className="mt-2 text-red-500">{error}</div>}
      {notice && <div className="mt-2 text-green-600">{notice}</div>}
      <div className="mt-3 flex gap-2">
        {onPreflight && (
          <button disabled={busy} onClick={runPreflight} className="flex-1 rounded-lg border border-tg-button px-2 py-1.5 text-tg-button disabled:opacity-50">
            {busy ? <LoadingSpinner size="sm" /> : 'Run preflight'}
          </button>
        )}
        {onResume && (
          <button disabled={busy || !preflight?.ok} onClick={resume} className="flex-1 rounded-lg bg-tg-button px-2 py-1.5 font-semibold text-tg-button-text disabled:opacity-40">
            Resume manually
          </button>
        )}
      </div>
    </div>
  )
}
