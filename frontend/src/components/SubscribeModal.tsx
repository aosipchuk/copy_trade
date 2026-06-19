import { useState } from 'react'
import { createPortal } from 'react-dom'
import { createSubscription } from '../api/subscriptions'
import type { SizingMode, SubscriptionCreate } from '../types'
import { LoadingSpinner } from './LoadingSpinner'

interface Props {
  traderId: number
  onClose: () => void
  onSuccess: () => void
  isDemo?: boolean
}

const TOP_COINS = [
  'BTC', 'ETH', 'SOL', 'ARB', 'AVAX', 'DOGE', 'LINK', 'BNB',
  'OP', 'SUI', 'INJ', 'APT', 'ATOM', 'MATIC', 'LTC', 'NEAR',
  'FIL', 'ADA', 'XRP', 'TON',
]

const SIZING_MODE_LABELS: Record<SizingMode, string> = {
  fixed_ratio: 'Proportional (% of trader)',
  fixed_usd: 'Fixed amount per trade',
  equity_pct: '% of my balance',
}

export function SubscribeModal({ traderId, onClose, onSuccess, isDemo = false }: Props) {
  const [allocation, setAllocation] = useState(100)
  const [copyRatio, setCopyRatio] = useState(100)
  const [stopLoss, setStopLoss] = useState(20)
  const [maxLeverage, setMaxLeverage] = useState(10)
  const [sizingMode, setSizingMode] = useState<SizingMode>('fixed_ratio')
  const [maxPerCoin, setMaxPerCoin] = useState<string>('')
  const [allowedCoins, setAllowedCoins] = useState<string[]>([])
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const toggleCoin = (coin: string) => {
    setAllowedCoins(prev =>
      prev.includes(coin) ? prev.filter(c => c !== coin) : [...prev, coin]
    )
  }

  const handleSubmit = async () => {
    setLoading(true)
    setError(null)
    try {
      const body: SubscriptionCreate = {
        trader_id: traderId,
        max_allocation_usd: allocation,
        copy_ratio_pct: copyRatio,
        stop_loss_pct: stopLoss,
        max_leverage: maxLeverage,
        sizing_mode: sizingMode,
        is_demo: isDemo,
      }
      if (maxPerCoin !== '') {
        const parsed = parseFloat(maxPerCoin)
        if (!isNaN(parsed) && parsed > 0) {
          body.max_per_coin_usd = parsed
        }
      }
      if (allowedCoins.length > 0) {
        body.allowed_coins = allowedCoins
      }
      await createSubscription(body)
      onSuccess()
    } catch (err: unknown) {
      const apiDetail = (err as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail
      const msg = typeof apiDetail === 'string'
        ? apiDetail
        : err instanceof Error ? err.message : 'Failed to subscribe'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-end" onClick={onClose}>
      <div
        className="w-full rounded-t-2xl flex flex-col max-h-[85vh]"
        style={{ background: 'var(--tg-theme-bg-color, #fff)' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 pt-5 pb-3 flex-shrink-0">
          <div>
            <h2 className="text-base font-semibold text-tg-text">
              {isDemo ? 'Try Demo' : 'Subscribe'}
            </h2>
            {isDemo && (
              <p className="text-xs text-tg-hint mt-0.5">
                Simulated trades only — no real money at risk
              </p>
            )}
          </div>
          <button className="text-tg-hint text-xl" onClick={onClose}>✕</button>
        </div>

        {/* Scrollable content */}
        <div className="overflow-y-auto flex-1 px-5 space-y-4 pb-3">

          {/* Sizing Mode */}
          <div>
            <p className="text-sm font-medium text-tg-text mb-2">Position Sizing</p>
            <div className="space-y-2">
              {(Object.keys(SIZING_MODE_LABELS) as SizingMode[]).map(mode => (
                <label key={mode} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="sizingMode"
                    value={mode}
                    checked={sizingMode === mode}
                    onChange={() => setSizingMode(mode)}
                    className="accent-tg-button"
                  />
                  <span className="text-sm text-tg-text">{SIZING_MODE_LABELS[mode]}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Max Allocation — always shown, meaning differs by mode */}
          <SliderField
            label={
              isDemo
                ? `Virtual allocation: $${allocation}`
                : sizingMode === 'fixed_usd'
                  ? `Amount per trade: $${allocation}`
                  : `Max Allocation: $${allocation}`
            }
            value={allocation}
            min={10} max={10000} step={10}
            onChange={setAllocation}
          />

          {/* Copy Ratio — hidden for fixed_usd */}
          {sizingMode !== 'fixed_usd' && (
            <SliderField
              label={
                sizingMode === 'fixed_ratio'
                  ? `Copy Ratio: ${copyRatio}% of trader's position`
                  : `Copy Ratio: ${copyRatio}% of my balance`
              }
              value={copyRatio}
              min={10} max={100} step={5}
              onChange={setCopyRatio}
            />
          )}

          <SliderField
            label={`Stop-loss: ${stopLoss}%`}
            value={stopLoss}
            min={5} max={50} step={5}
            onChange={setStopLoss}
          />
          <SliderField
            label={`Max Leverage: ${maxLeverage}x`}
            value={maxLeverage}
            min={1} max={40} step={1}
            onChange={setMaxLeverage}
          />

          {/* Advanced */}
          <div>
            <button
              className="flex items-center gap-1 text-sm text-tg-hint"
              onClick={() => setShowAdvanced(v => !v)}
            >
              <span>{showAdvanced ? '▼' : '▶'}</span>
              <span>Advanced</span>
            </button>

            {showAdvanced && (
              <div className="mt-3 space-y-4">
                {/* Max per coin */}
                <div>
                  <label className="text-sm text-tg-text block mb-1">
                    Max per coin (USDC, optional)
                  </label>
                  <input
                    type="number"
                    min="0"
                    step="10"
                    placeholder="e.g. 500"
                    value={maxPerCoin}
                    onChange={(e) => setMaxPerCoin(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg border text-sm"
                    style={{
                      background: 'var(--tg-theme-secondary-bg-color, #f0f0f0)',
                      borderColor: 'var(--tg-theme-hint-color, #ccc)',
                      color: 'var(--tg-theme-text-color, #000)',
                    }}
                  />
                </div>

                {/* Allowed coins */}
                <div>
                  <p className="text-sm text-tg-text mb-2">
                    Allowed coins{' '}
                    <span className="text-tg-hint">
                      ({allowedCoins.length === 0 ? 'all' : allowedCoins.length + ' selected'})
                    </span>
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {TOP_COINS.map(coin => (
                      <button
                        key={coin}
                        onClick={() => toggleCoin(coin)}
                        className="px-2 py-1 rounded-md text-xs font-medium transition-colors"
                        style={{
                          background: allowedCoins.includes(coin)
                            ? 'var(--tg-theme-button-color, #2481cc)'
                            : 'var(--tg-theme-secondary-bg-color, #f0f0f0)',
                          color: allowedCoins.includes(coin)
                            ? 'var(--tg-theme-button-text-color, #fff)'
                            : 'var(--tg-theme-text-color, #000)',
                        }}
                      >
                        {coin}
                      </button>
                    ))}
                  </div>
                  {allowedCoins.length > 0 && (
                    <button
                      className="mt-2 text-xs text-tg-hint underline"
                      onClick={() => setAllowedCoins([])}
                    >
                      Clear selection (allow all)
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>

          {error && <p className="text-red-500 text-sm">{error}</p>}
        </div>

        {/* Submit button */}
        <div
          className="flex-shrink-0 px-5 pt-2"
          style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 16px)' }}
        >
          <button
            className="w-full py-3 rounded-xl font-semibold text-tg-button-text disabled:opacity-50"
            style={{ background: 'var(--tg-theme-button-color, #2481cc)' }}
            onClick={handleSubmit}
            disabled={loading}
          >
            {loading ? <LoadingSpinner size="sm" /> : isDemo ? 'Start Demo' : 'Confirm Subscribe'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

function SliderField({
  label, value, min, max, step, onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  onChange: (v: number) => void
}) {
  return (
    <div>
      <div className="flex justify-between text-sm text-tg-text mb-1">
        <span>{label}</span>
      </div>
      <input
        type="range"
        min={min} max={max} step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-tg-button"
      />
    </div>
  )
}
