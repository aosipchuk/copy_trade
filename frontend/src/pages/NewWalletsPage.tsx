import { FormEvent, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  activateNewWalletSubscription,
  fetchNewWalletCandidates,
  fetchNewWalletSummary,
} from '../api/newWallets'
import { FullPageSpinner } from '../components/LoadingSpinner'
import type {
  NewWalletCandidate,
  NewWalletSummary,
  NewWalletSubscriptionCreate,
} from '../types'

function money(value: number | null | undefined): string {
  if (value == null) return '-'
  return `$${value.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
}

function shortAddress(value: string): string {
  return `${value.slice(0, 6)}…${value.slice(-4)}`
}

function dateText(value: string | null | undefined): string {
  if (!value) return '-'
  return new Date(value).toLocaleDateString('ru-RU', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function ttlText(value: string | null): string {
  if (!value) return ''
  const ms = new Date(value).getTime() - Date.now()
  if (ms <= 0) return 'expired'
  const hours = Math.ceil(ms / 3_600_000)
  if (hours < 24) return `${hours}h`
  return `${Math.ceil(hours / 24)}d`
}

export function NewWalletsPage() {
  const [summary, setSummary] = useState<NewWalletSummary | null>(null)
  const [items, setItems] = useState<NewWalletCandidate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showActivate, setShowActivate] = useState(false)

  const load = () => {
    setLoading(true)
    setError(null)
    Promise.all([
      fetchNewWalletSummary(),
      fetchNewWalletCandidates({ limit: 100 }),
    ])
      .then(([nextSummary, candidates]) => {
        setSummary(nextSummary)
        setItems(
          candidates.items.filter((item) =>
            ['qualified', 'subscribed'].includes(item.status),
          ),
        )
      })
      .catch(() => setError('Failed to load new wallets'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const active = summary?.active_subscription ?? null
  const qualifiedCount = summary?.counts_by_status.qualified ?? 0
  const subscribedCount = summary?.counts_by_status.subscribed ?? 0

  if (loading) return <FullPageSpinner />

  if (error) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-tg-hint">
        {error}
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto pb-20">
      <div className="px-4 pb-3 pt-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-tg-text">
              Новые кошельки
            </h1>
            <p className="mt-1 text-xs text-tg-hint">
              {qualifiedCount + subscribedCount} ready · threshold{' '}
              {money(summary?.settings.chain_balance_threshold_usd)}
            </p>
          </div>
          {active ? (
            <Link
              to={`/new-wallet-subscriptions/${active.id}`}
              className="rounded-lg border border-tg-button px-3 py-1.5 text-xs font-medium text-tg-button"
            >
              Active
            </Link>
          ) : (
            <button
              onClick={() => setShowActivate(true)}
              className="rounded-lg bg-tg-button px-3 py-1.5 text-xs font-medium text-tg-button-text"
            >
              Activate
            </button>
          )}
        </div>
      </div>

      {!summary?.settings.discovery_enabled && (
        <div className="mx-4 mb-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          Discovery disabled
        </div>
      )}

      {!summary?.settings.funding_provider_configured && (
        <div className="mx-4 mb-3 rounded-lg border border-gray-200 px-3 py-2 text-xs text-tg-hint dark:border-gray-700">
          Funding provider not configured
        </div>
      )}

      {summary?.settings.discovery_enabled &&
        !summary.settings.auto_attach_enabled && (
          <div className="mx-4 mb-3 rounded-lg border border-gray-200 px-3 py-2 text-xs text-tg-hint dark:border-gray-700">
            Auto-attach disabled
          </div>
        )}

      {items.length === 0 ? (
        <div className="px-6 pt-12 text-center text-sm text-tg-hint">
          No qualified wallets
        </div>
      ) : (
        <div className="space-y-3 px-4">
          {items.map((candidate) => (
            <CandidateCard key={candidate.id} candidate={candidate} />
          ))}
        </div>
      )}

      {showActivate && summary && (
        <ActivationModal
          summary={summary}
          onClose={() => setShowActivate(false)}
          onActivated={(next) => {
            setSummary((prev) =>
              prev ? { ...prev, active_subscription: next } : prev,
            )
            setShowActivate(false)
            load()
          }}
        />
      )}
    </div>
  )
}

function CandidateCard({ candidate }: { candidate: NewWalletCandidate }) {
  const copied = candidate.user_item_status === 'active'
  const firstLink = candidate.links[0]

  return (
    <div
      className="rounded-xl px-4 py-3"
      style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-mono text-sm font-semibold text-tg-text">
            {shortAddress(candidate.hl_address)}
          </div>
          <div className="mt-1 text-xs text-tg-hint">
            funded {dateText(candidate.funded_at)}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-sm font-semibold text-tg-text">
            {money(candidate.chain_total_balance_usd)}
          </div>
          <div className="text-[10px] uppercase text-tg-hint">
            depth {candidate.chain_depth ?? '-'}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-xs">
        <Metric
          label="Source"
          value={
            firstLink?.funded_by_address
              ? shortAddress(firstLink.funded_by_address)
              : '-'
          }
        />
        <Metric label="Amount" value={money(firstLink?.amount_usdc)} />
        <Metric
          label={copied ? 'Expires' : 'Status'}
          value={copied ? ttlText(candidate.user_child_expires_at) : candidate.status}
        />
      </div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] text-tg-hint">{label}</div>
      <div className="truncate font-medium text-tg-text">{value}</div>
    </div>
  )
}

function ActivationModal({
  summary,
  onClose,
  onActivated,
}: {
  summary: NewWalletSummary
  onClose: () => void
  onActivated: (value: NewWalletSummary['active_subscription']) => void
}) {
  const [mode, setMode] = useState<'demo' | 'live'>('demo')
  const [form, setForm] = useState({
    totalAllocationUsd: 500,
    maxActiveWallets: 5,
    maxPerWalletUsd: summary.settings.default_max_per_wallet_usd,
    copyRatioPct: 100,
    stopLossPct: 20,
    maxLeverage: 10,
    riskDisclosureAccepted: false,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const estimatedPerWallet = useMemo(
    () =>
      Math.min(
        form.maxPerWalletUsd,
        form.totalAllocationUsd / Math.max(1, form.maxActiveWallets),
      ),
    [form.maxActiveWallets, form.maxPerWalletUsd, form.totalAllocationUsd],
  )

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const body: NewWalletSubscriptionCreate = {
      is_demo: mode === 'demo',
      total_allocation_usd: form.totalAllocationUsd,
      max_active_wallets: form.maxActiveWallets,
      max_per_wallet_usd: form.maxPerWalletUsd,
      copy_ratio_pct: form.copyRatioPct,
      stop_loss_pct: form.stopLossPct,
      max_leverage: form.maxLeverage,
      sizing_mode: 'fixed_ratio',
      close_positions_on_expire: true,
      risk_disclosure_accepted:
        mode === 'demo' ? false : form.riskDisclosureAccepted,
    }
    try {
      const result = await activateNewWalletSubscription(body)
      onActivated(result)
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail
      setError(detail ?? 'Activation failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-end bg-black/30">
      <form
        onSubmit={submit}
        className="max-h-[88vh] w-full overflow-y-auto rounded-t-2xl px-4 pb-6 pt-4"
        style={{ background: 'var(--tg-theme-bg-color)' }}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-tg-text">Activate</h2>
          <button type="button" onClick={onClose} className="text-sm text-tg-hint">
            Close
          </button>
        </div>

        <div className="mb-4 grid grid-cols-2 gap-2 rounded-lg bg-gray-100 p-1 dark:bg-gray-800">
          {(['demo', 'live'] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setMode(value)}
              className={`rounded-md py-2 text-sm font-medium ${
                mode === value ? 'bg-tg-button text-tg-button-text' : 'text-tg-hint'
              }`}
            >
              {value === 'demo' ? 'Demo' : 'Live'}
            </button>
          ))}
        </div>

        <div className="space-y-3">
          <NumberField
            label="Total allocation"
            value={form.totalAllocationUsd}
            onChange={(v) =>
              setForm((f) => ({ ...f, totalAllocationUsd: v }))
            }
          />
          <NumberField
            label="Max wallets"
            value={form.maxActiveWallets}
            onChange={(v) =>
              setForm((f) => ({
                ...f,
                maxActiveWallets: Math.min(
                  summary.settings.max_active_per_user,
                  Math.max(1, Math.round(v)),
                ),
              }))
            }
          />
          <NumberField
            label="Max per wallet"
            value={form.maxPerWalletUsd}
            onChange={(v) => setForm((f) => ({ ...f, maxPerWalletUsd: v }))}
          />
          <NumberField
            label="Copy ratio %"
            value={form.copyRatioPct}
            onChange={(v) => setForm((f) => ({ ...f, copyRatioPct: v }))}
          />
          <NumberField
            label="Stop loss %"
            value={form.stopLossPct}
            onChange={(v) => setForm((f) => ({ ...f, stopLossPct: v }))}
          />
          <NumberField
            label="Max leverage"
            value={form.maxLeverage}
            onChange={(v) => setForm((f) => ({ ...f, maxLeverage: v }))}
          />
        </div>

        <div className="mt-4 rounded-lg border border-gray-200 px-3 py-2 text-xs text-tg-hint dark:border-gray-700">
          {summary.settings.subscription_ttl_days} days per wallet · estimated{' '}
          {money(estimatedPerWallet)} each · open copied positions close on expiry.
        </div>

        {mode === 'live' && (
          <label className="mt-4 flex items-start gap-3 text-xs text-tg-hint">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={form.riskDisclosureAccepted}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  riskDisclosureAccepted: e.target.checked,
                }))
              }
            />
            <span>
              Live strategy opens real HyperLiquid orders. Each generated wallet
              subscription expires after 5 days and copied positions are closed.
            </span>
          </label>
        )}

        {error && <div className="mt-3 text-xs text-red-500">{error}</div>}

        <button
          type="submit"
          disabled={busy || (mode === 'live' && !form.riskDisclosureAccepted)}
          className="mt-5 w-full rounded-lg bg-tg-button py-3 text-sm font-semibold text-tg-button-text disabled:opacity-50"
        >
          {busy ? 'Activating…' : 'Activate'}
        </button>
      </form>
    </div>
  )
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string
  value: number
  onChange: (value: number) => void
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-tg-hint">{label}</span>
      <input
        type="number"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-tg-text outline-none dark:border-gray-700"
      />
    </label>
  )
}
