import { useEffect, useState } from 'react'
import { fetchCopyAccount, runCopyPreflight, selectCopyAccount } from '../api/copyExecution'
import type { CopyAccountState, CopyPreflight } from '../types'
import { LoadingSpinner } from './LoadingSpinner'

export function CopyAccountPreflight() {
  const [account, setAccount] = useState<CopyAccountState | null>(null)
  const [selected, setSelected] = useState('')
  const [preflight, setPreflight] = useState<CopyPreflight | null>(null)
  const [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchCopyAccount()
      .then((next) => {
        setAccount(next)
        setSelected(next.account_address ?? next.options[0]?.address ?? '')
        setAck(Boolean(next.dedicated_confirmed_at))
      })
      .catch(() => setError('Failed to load copy-account settings'))
  }, [])

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const next = await selectCopyAccount(selected)
      setAccount(next)
      setPreflight(null)
    } catch (err: unknown) {
      setError((err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Account selection failed')
    } finally {
      setBusy(false)
    }
  }

  const check = async () => {
    setBusy(true)
    setError(null)
    try {
      setPreflight(await runCopyPreflight())
    } catch (err: unknown) {
      setError((err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Preflight failed')
    } finally {
      setBusy(false)
    }
  }

  if (!account && !error) return <div className="p-3"><LoadingSpinner size="sm" /></div>

  return (
    <div className="rounded-xl p-4" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
      <h2 className="text-sm font-semibold text-tg-text">Dedicated copy account</h2>
      <p className="mt-1 text-xs text-tg-hint">Manual trades, positions and open orders are forbidden on this account. Only server-verified owned subaccounts are listed.</p>
      {account && (
        <>
          <select value={selected} onChange={(event) => setSelected(event.target.value)} className="mt-3 w-full rounded-lg border border-gray-300 bg-transparent px-3 py-2 text-xs text-tg-text dark:border-gray-600">
            {account.options.map((option) => (
              <option key={option.address} value={option.address}>{option.name} · {option.address.slice(0, 8)}…</option>
            ))}
          </select>
          <label className="mt-3 flex items-start gap-2 text-xs text-tg-hint">
            <input type="checkbox" checked={ack} onChange={(event) => setAck(event.target.checked)} className="mt-0.5 accent-tg-button" />
            I confirm this account is dedicated exclusively to CopyTrade.
          </label>
          <div className="mt-3 flex gap-2">
            <button disabled={busy || !ack || !selected} onClick={save} className="flex-1 rounded-lg border border-tg-button px-2 py-2 text-xs text-tg-button disabled:opacity-40">Save account</button>
            <button disabled={busy || !account.account_address} onClick={check} className="flex-1 rounded-lg bg-tg-button px-2 py-2 text-xs font-semibold text-tg-button-text disabled:opacity-40">Run preflight</button>
          </div>
        </>
      )}
      {preflight && <div className="mt-3 space-y-1">{preflight.checks.map((item) => <div key={item.code} className={`text-xs ${item.ok ? 'text-green-600' : 'text-red-500'}`}>{item.ok ? '✓' : '✕'} {item.message}</div>)}</div>}
      {error && <p className="mt-2 text-xs text-red-500">{error}</p>}
    </div>
  )
}
