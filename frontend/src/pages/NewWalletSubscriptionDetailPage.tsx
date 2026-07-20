import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  cancelNewWalletSubscription,
  fetchNewWalletSubscription,
} from '../api/newWallets'
import { FullPageSpinner } from '../components/LoadingSpinner'
import { useBackButton } from '../hooks/useTelegram'
import type { UserNewWalletSubscription } from '../types'

function money(value: number | null | undefined): string {
  if (value == null) return '-'
  const prefix = value < 0 ? '-' : ''
  return `${prefix}$${Math.abs(value).toLocaleString('en-US', {
    maximumFractionDigits: 0,
  })}`
}

function shortAddress(value: string | null | undefined): string {
  if (!value) return '-'
  return `${value.slice(0, 6)}…${value.slice(-4)}`
}

function countdown(value: string): string {
  const ms = new Date(value).getTime() - Date.now()
  if (ms <= 0) return 'expired'
  const hours = Math.ceil(ms / 3_600_000)
  if (hours < 24) return `${hours}h left`
  return `${Math.ceil(hours / 24)}d left`
}

export function NewWalletSubscriptionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [subscription, setSubscription] =
    useState<UserNewWalletSubscription | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const back = useCallback(() => navigate('/new-wallets'), [navigate])
  useBackButton(back)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    fetchNewWalletSubscription(Number(id))
      .then(setSubscription)
      .catch(() => setError('Failed to load subscription'))
      .finally(() => setLoading(false))
  }, [id])

  const cancel = async () => {
    if (!subscription) return
    setBusy(true)
    setError(null)
    try {
      const next = await cancelNewWalletSubscription(subscription.id, true)
      setSubscription(next)
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail
      setError(detail ?? 'Cancel failed')
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <FullPageSpinner />

  if (error && !subscription) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-tg-hint">
        {error}
      </div>
    )
  }

  if (!subscription) return null

  return (
    <div className="h-full overflow-y-auto pb-20">
      <div className="px-4 pb-3 pt-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-tg-text">
              Новые кошельки
            </h1>
            <p className="mt-1 text-xs text-tg-hint">
              {subscription.is_demo ? 'Demo' : 'Live'} · {subscription.status}
            </p>
          </div>
          {subscription.status === 'active' && (
            <button
              onClick={cancel}
              disabled={busy}
              className="rounded-lg border border-red-400 px-3 py-1.5 text-xs font-medium text-red-500 disabled:opacity-50"
            >
              Cancel
            </button>
          )}
        </div>
      </div>

      <div className="mx-4 mb-3 grid grid-cols-3 gap-2 text-center">
        <Metric label="Total" value={money(subscription.total_allocation_usd)} />
        <Metric label="Per wallet" value={money(subscription.max_per_wallet_usd)} />
        <Metric
          label="Active"
          value={String(
            subscription.items.filter((item) => item.status === 'active').length,
          )}
        />
      </div>

      {error && <div className="mx-4 mb-3 text-xs text-red-500">{error}</div>}

      <div className="space-y-3 px-4">
        {subscription.items.length === 0 ? (
          <div className="pt-8 text-center text-sm text-tg-hint">
            No generated subscriptions
          </div>
        ) : (
          subscription.items.map((item) => (
            <div
              key={item.id}
              className="rounded-xl px-4 py-3"
              style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
            >
              <div className="mb-3 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-mono text-sm font-semibold text-tg-text">
                    {shortAddress(item.candidate?.hl_address)}
                  </div>
                  <div className="mt-1 text-xs text-tg-hint">
                    child #{item.subscription_id}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-sm font-semibold text-tg-text">
                    {countdown(item.expires_at)}
                  </div>
                  <div className="text-[10px] uppercase text-tg-hint">
                    {item.status}
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-2 text-xs">
                <Metric
                  label="Chain"
                  value={money(item.candidate?.chain_total_balance_usd)}
                />
                <Metric
                  label="Depth"
                  value={String(item.candidate?.chain_depth ?? '-')}
                />
                <Metric label="PnL" value={money(item.realized_pnl)} />
              </div>

              {item.candidate?.links.length ? (
                <div className="mt-3 space-y-1 border-t border-gray-200 pt-2 text-xs dark:border-gray-700">
                  {item.candidate.links.map((link) => (
                    <div
                      key={link.id}
                      className="flex items-center justify-between gap-2 text-tg-hint"
                    >
                      <span>
                        {link.depth}. {shortAddress(link.funded_by_address)}
                      </span>
                      <span>{money(link.balance_usd)}</span>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ))
        )}
      </div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div
      className="min-w-0 rounded-lg px-2 py-2"
      style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
    >
      <div className="text-[10px] text-tg-hint">{label}</div>
      <div className="truncate text-xs font-semibold text-tg-text">{value}</div>
    </div>
  )
}
