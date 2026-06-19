import { useAppKit, useAppKitAccount, useAppKitProvider } from '@reown/appkit/react'
import { BrowserProvider, Wallet, type Eip1193Provider } from 'ethers'
import { QRCodeSVG } from 'qrcode.react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { closeAllPositions, deleteAgent, fetchAgentStatus, fetchPortfolioRisk, fetchWalletActivity, fetchWalletBalance, fetchWalletPositions, updatePortfolioRisk, walletApprove, walletBuilderApprove, walletBuilderSetup, walletSetup } from '../api/wallet'
import { FullPageSpinner, LoadingSpinner } from '../components/LoadingSpinner'
import type { ActivityItem, AgentStatus, PortfolioRisk, PositionItem, WalletBalance } from '../types'
import { fmt } from '../utils/format'

type WizardStep = 'setup' | 'sign' | 'builder' | 'new-account' | 'deposit' | 'done'

const ACTION_ICON: Record<string, string> = {
  trade_executed: '🟢',
  position_closed: '🔴',
  position_updated: '🔄',
  trade_failed: '❌',
  trade_cancelled: '⚪',
}

export function WalletPage() {
  const [status, setStatus] = useState<AgentStatus | null>(null)
  const [balance, setBalance] = useState<WalletBalance | null>(null)
  const [positions, setPositions] = useState<PositionItem[]>([])
  const [activity, setActivity] = useState<ActivityItem[]>([])
  const [portfolioRisk, setPortfolioRisk] = useState<PortfolioRisk | null>(null)
  const [pslInput, setPslInput] = useState<string>('')
  const [pslSaving, setPslSaving] = useState(false)
  const [loading, setLoading] = useState(true)
  const [closingAll, setClosingAll] = useState(false)

  const reload = () => {
    setLoading(true)
    fetchAgentStatus()
      .then((s) => {
        setStatus(s)
        if (s.is_active) {
          return Promise.all([
            fetchWalletBalance(),
            fetchWalletPositions(),
            fetchWalletActivity(10),
            fetchPortfolioRisk(),
          ]).then(([b, p, a, pr]) => {
            setBalance(b)
            setPositions(p)
            setActivity(a)
            setPortfolioRisk(pr)
            setPslInput(pr.portfolio_stop_loss_pct != null ? String(pr.portfolio_stop_loss_pct) : '')
          })
        }
      })
      .finally(() => setLoading(false))
  }

  useEffect(reload, [])

  if (loading) return <FullPageSpinner />

  if (!status?.is_active) {
    return <WalletSetupWizard onComplete={reload} />
  }

  if (!status.builder_fee_approved) {
    return <BuilderApprovalGate onComplete={reload} />
  }

  return (
    <div className="pb-20 h-full overflow-y-auto px-4 pt-4 space-y-4">
      <h1 className="text-base font-semibold text-tg-text">Wallet</h1>

      {balance && (
        <div className="rounded-xl p-4" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
          <div className="text-xs text-tg-hint mb-1">Account Value</div>
          <div className="text-2xl font-bold text-tg-text">{fmt.compact(balance.account_value)}</div>
          <div className="flex gap-4 mt-2 text-xs text-tg-hint">
            <span>Margin used: {fmt.compact(balance.total_margin_used)}</span>
            <span>Available: {fmt.compact(balance.available)}</span>
          </div>
        </div>
      )}

      {status.agent_address && (
        <div className="rounded-xl p-3" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
          <div className="text-xs text-tg-hint mb-1">Agent Address</div>
          <div className="text-xs font-mono text-tg-text break-all">{status.agent_address}</div>
        </div>
      )}

      <div>
        <h2 className="text-sm font-semibold text-tg-text mb-2">Your Positions ({positions.length})</h2>
        {positions.length === 0 ? (
          <p className="text-sm text-tg-hint">No open positions</p>
        ) : (
          <div className="rounded-xl overflow-hidden" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
            {positions.map((pos, i) => (
              <div key={i} className="flex items-center justify-between px-3 py-2.5 border-b border-gray-100 dark:border-gray-700 last:border-b-0">
                <div>
                  <span className="text-sm font-medium text-tg-text">{pos.coin}</span>
                  <span className={`ml-1.5 text-xs px-1.5 rounded ${pos.side === 'long' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                    {pos.side.toUpperCase()}
                  </span>
                </div>
                <div className="text-right">
                  <div className="text-xs text-tg-hint">{pos.size}</div>
                  <div className={`text-xs ${pos.unrealized_pnl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                    {fmt.usd(pos.unrealized_pnl)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Activity log */}
      <div>
        <h2 className="text-sm font-semibold text-tg-text mb-2">Recent Agent Activity</h2>
        {activity.length === 0 ? (
          <p className="text-sm text-tg-hint">No activity yet</p>
        ) : (
          <div className="rounded-xl overflow-hidden" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
            {activity.map((item, i) => (
              <div key={i} className="flex items-center justify-between px-3 py-2.5 border-b border-gray-100 dark:border-gray-700 last:border-b-0">
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs">{ACTION_ICON[item.action] ?? '•'}</span>
                    <span className="text-xs font-medium text-tg-text">
                      {item.coin ?? '—'}{item.side ? ` ${item.side.toUpperCase()}` : ''}
                    </span>
                    {item.size != null && (
                      <span className="text-xs text-tg-hint">{item.size}</span>
                    )}
                  </div>
                  {item.subscription_trader && (
                    <div className="text-xs text-tg-hint truncate mt-0.5">{item.subscription_trader}</div>
                  )}
                </div>
                <div className="text-xs text-tg-hint shrink-0 ml-2">
                  {new Date(item.ts).toLocaleDateString([], { month: 'short', day: 'numeric' })}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Portfolio Risk Settings */}
      <div className="rounded-xl p-4" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
        <h2 className="text-sm font-semibold text-tg-text mb-3">Portfolio Stop-Loss</h2>
        <p className="text-xs text-tg-hint mb-3">
          Deactivates all subscriptions when the account drops by this % within 24 hours. Leave empty to disable.
        </p>
        <div className="flex gap-2 items-center">
          <input
            type="number"
            min="1"
            max="99"
            step="1"
            placeholder="e.g. 20"
            value={pslInput}
            onChange={(e) => setPslInput(e.target.value)}
            className="flex-1 rounded-lg px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 bg-transparent text-tg-text"
          />
          <span className="text-sm text-tg-hint">%</span>
          <button
            className="px-4 py-2 rounded-lg text-sm font-semibold text-tg-button-text disabled:opacity-50"
            style={{ background: 'var(--tg-theme-button-color)' }}
            disabled={pslSaving}
            onClick={async () => {
              setPslSaving(true)
              try {
                const val = pslInput.trim() === '' ? null : parseFloat(pslInput)
                const updated = await updatePortfolioRisk(val)
                setPortfolioRisk(updated)
              } finally {
                setPslSaving(false)
              }
            }}
          >
            {pslSaving ? <LoadingSpinner size="sm" /> : 'Save'}
          </button>
        </div>
        {portfolioRisk?.portfolio_stop_loss_pct != null && (
          <p className="text-xs text-tg-hint mt-2">
            Active: stop at <b>{portfolioRisk.portfolio_stop_loss_pct}%</b> daily loss
          </p>
        )}
        {portfolioRisk?.portfolio_stop_loss_pct == null && (
          <p className="text-xs text-tg-hint mt-2">Disabled</p>
        )}
      </div>

      {/* Danger zone */}
      <div className="space-y-2">
        <button
          className="w-full py-2.5 rounded-xl text-sm font-semibold border border-red-500 text-red-500 disabled:opacity-50"
          disabled={closingAll}
          onClick={async () => {
            if (!confirm('Emergency stop: close all positions and pause all subscriptions?')) return
            setClosingAll(true)
            try {
              const result = await closeAllPositions()
              alert(`Closed ${result.closed} positions, paused ${result.subscriptions_paused} subscriptions.`)
            } catch {
              alert('Emergency stop failed. Please try again or revoke agent access.')
            } finally {
              setClosingAll(false)
              reload()
            }
          }}
        >
          {closingAll ? <LoadingSpinner size="sm" /> : 'Emergency Stop — Close All & Pause'}
        </button>
        <button
          className="w-full py-2.5 rounded-xl text-sm border border-gray-300 text-tg-hint"
          onClick={async () => {
            if (!confirm('Revoke agent access? All copy positions will be closed.')) return
            await deleteAgent()
            reload()
          }}
        >
          Revoke Agent Access
        </button>
      </div>
    </div>
  )
}

function WalletSetupWizard({ onComplete }: { onComplete: () => void }) {
  const [step, setStep] = useState<WizardStep>('setup')
  const [agentAddress, setAgentAddress] = useState('')
  const [nonce, setNonce] = useState<number>(0)
  const [eip712Payload, setEip712Payload] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Builder fee state
  const [builderNonce, setBuilderNonce] = useState<number>(0)
  const [builderPayload, setBuilderPayload] = useState<Record<string, unknown> | null>(null)

  // New-account path state
  const [newAddress, setNewAddress] = useState('')
  const [newPrivkey, setNewPrivkey] = useState('')
  const [privkeyCopied, setPrivkeyCopied] = useState(false)

  const { open } = useAppKit()
  const { isConnected, address: connectedAddress } = useAppKitAccount()
  const { walletProvider } = useAppKitProvider<Eip1193Provider>('eip155')
  const pendingSign = useRef(false)

  const splitSig = (rawSig: string) => ({
    r: rawSig.slice(0, 66),
    s: '0x' + rawSig.slice(66, 130),
    v: parseInt(rawSig.slice(130, 132), 16),
  })

  const doSign = useCallback(async (provider: Eip1193Provider, payload: Record<string, unknown>, sigNonce: number) => {
    setLoading(true)
    setError(null)
    try {
      const ethersProvider = new BrowserProvider(provider)
      const signer = await ethersProvider.getSigner()
      const userAddress = await signer.getAddress()

      const { domain, types, message } = payload as {
        domain: Record<string, unknown>
        types: Record<string, { name: string; type: string }[]>
        message: Record<string, unknown>
        primaryType: string
      }
      const { EIP712Domain: _, ...filteredTypes } = types as Record<string, { name: string; type: string }[]>
      const rawSig: string = await signer.signTypedData(domain, filteredTypes, message)

      await walletApprove({ nonce: sigNonce, userAddress, signature: splitSig(rawSig) })
      try {
        const builderRes = await walletBuilderSetup()
        setBuilderNonce(builderRes.nonce)
        setBuilderPayload(builderRes.eip712_payload)
        setStep('builder')
      } catch {
        // builder fee not configured — skip to done
        setStep('done')
        setTimeout(onComplete, 1500)
      }
    } catch (err: unknown) {
      const axiosDetail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(axiosDetail ?? (err instanceof Error ? err.message : 'Signing failed'))
      if (axiosDetail) setStep('setup')
    } finally {
      setLoading(false)
    }
  }, [onComplete])

  const doBuilderSign = useCallback(async (provider: Eip1193Provider) => {
    if (!builderPayload) return
    setLoading(true)
    setError(null)
    try {
      const ethersProvider = new BrowserProvider(provider)
      const signer = await ethersProvider.getSigner()
      const { domain, types, message } = builderPayload as {
        domain: Record<string, unknown>
        types: Record<string, { name: string; type: string }[]>
        message: Record<string, unknown>
        primaryType: string
      }
      const { EIP712Domain: _, ...filteredTypes } = types as Record<string, { name: string; type: string }[]>
      const rawSig: string = await signer.signTypedData(domain, filteredTypes, message)
      await walletBuilderApprove({ nonce: builderNonce, signature: splitSig(rawSig) })
      setStep('deposit')
    } catch (err: unknown) {
      const axiosDetail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(axiosDetail ?? (err instanceof Error ? err.message : 'Builder signing failed'))
    } finally {
      setLoading(false)
    }
  }, [builderPayload, builderNonce])

  // Auto-sign after WalletConnect connects (Variant A)
  useEffect(() => {
    if (pendingSign.current && isConnected && walletProvider && eip712Payload) {
      pendingSign.current = false
      doSign(walletProvider, eip712Payload, nonce)
    }
  }, [isConnected, walletProvider, eip712Payload, nonce, doSign])

  const handleSetup = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await walletSetup()
      setAgentAddress(res.agent_address)
      setNonce(res.nonce)
      setEip712Payload(res.eip712_payload)
      setStep('sign')
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Setup failed')
    } finally {
      setLoading(false)
    }
  }

  const handleSign = () => {
    if (!eip712Payload) return
    if (isConnected && walletProvider) {
      doSign(walletProvider, eip712Payload, nonce)
    } else {
      pendingSign.current = true
      open({ view: 'Connect' })
    }
  }

  // Variant B: generate keypair in browser, sign EIP-712 without MetaMask
  const handleNewAccount = async () => {
    if (!eip712Payload) return
    setLoading(true)
    setError(null)
    try {
      const wallet = Wallet.createRandom()
      const { domain, types, message } = eip712Payload as {
        domain: Record<string, unknown>
        types: Record<string, { name: string; type: string }[]>
        message: Record<string, unknown>
        primaryType: string
      }
      const { EIP712Domain: _, ...filteredTypes } = types as Record<string, { name: string; type: string }[]>
      const rawSig: string = await wallet.signTypedData(domain, filteredTypes, message)

      await walletApprove({ nonce, userAddress: wallet.address, signature: splitSig(rawSig) })

      try {
        const builderRes = await walletBuilderSetup()
        const { domain: bd, types: bt, message: bm } = builderRes.eip712_payload as {
          domain: Record<string, unknown>
          types: Record<string, { name: string; type: string }[]>
          message: Record<string, unknown>
          primaryType: string
        }
        const { EIP712Domain: _bd, ...bFilteredTypes } = bt as Record<string, { name: string; type: string }[]>
        const bRawSig: string = await wallet.signTypedData(bd, bFilteredTypes, bm)
        await walletBuilderApprove({ nonce: builderRes.nonce, signature: splitSig(bRawSig) })
      } catch {
        // builder fee not configured or failed — continue anyway
      }

      setNewAddress(wallet.address)
      setNewPrivkey(wallet.privateKey)
      setStep('new-account')
    } catch (err: unknown) {
      const axiosDetail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(axiosDetail ?? (err instanceof Error ? err.message : 'Failed to create account'))
    } finally {
      setLoading(false)
    }
  }

  const copyPrivkey = () => {
    navigator.clipboard.writeText(newPrivkey).then(() => {
      setPrivkeyCopied(true)
      setTimeout(() => setPrivkeyCopied(false), 2000)
    })
  }

  const STEP_LABELS: WizardStep[] = ['setup', 'sign', 'builder', 'deposit', 'done']
  const stepIndex = (s: WizardStep) => {
    if (s === 'new-account') return 3
    return STEP_LABELS.indexOf(s)
  }

  return (
    <div className="px-4 pt-6 pb-20">
      <h1 className="text-base font-semibold text-tg-text mb-1">Setup Wallet</h1>
      <p className="text-sm text-tg-hint mb-6">Connect your Hyperliquid account to enable copy trading</p>

      {/* Steps indicator — 5 visual steps */}
      <div className="flex items-center gap-2 mb-8">
        {[1, 2, 3, 4, 5].map((n, i) => {
          const active = stepIndex(step) === i || (step === 'done' && i < 5)
          return (
            <div key={n} className="flex items-center gap-2 flex-1 last:flex-none">
              <div
                className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-semibold shrink-0 ${
                  active ? 'text-tg-button-text' : 'text-tg-hint bg-tg-secondary'
                }`}
                style={active ? { background: 'var(--tg-theme-button-color)' } : {}}
              >
                {n}
              </div>
              {i < 4 && <div className="flex-1 h-px bg-gray-200 dark:bg-gray-700" />}
            </div>
          )
        })}
      </div>

      {/* Step: setup */}
      {step === 'setup' && (
        <div className="space-y-4">
          <div className="rounded-xl p-4" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
            <p className="text-sm text-tg-text">
              We'll generate a secure agent wallet that can trade on your behalf.
              The agent can only place orders — it cannot withdraw your funds.
            </p>
          </div>
          {error && <p className="text-sm text-red-500">{error}</p>}
          <button
            className="w-full py-3 rounded-xl font-semibold text-tg-button-text flex items-center justify-center gap-2 disabled:opacity-50"
            style={{ background: 'var(--tg-theme-button-color)' }}
            onClick={handleSetup}
            disabled={loading}
          >
            {loading ? <LoadingSpinner size="sm" /> : 'Generate Agent Wallet'}
          </button>
        </div>
      )}

      {/* Step: sign (agent generated, user chooses path) */}
      {step === 'sign' && (
        <div className="space-y-4">
          <div className="rounded-xl p-4" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
            <div className="text-xs text-tg-hint mb-1">Agent Address</div>
            <div className="text-xs font-mono text-tg-text break-all">{agentAddress}</div>
          </div>

          {connectedAddress && (
            <div className="rounded-xl p-4" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
              <div className="text-xs text-tg-hint mb-1">Connected Wallet</div>
              <div className="text-xs font-mono text-tg-text break-all">{connectedAddress}</div>
            </div>
          )}

          <div className="rounded-xl p-4" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
            <p className="text-sm text-tg-text">
              Sign the authorization message with your Hyperliquid wallet to grant trading access.
            </p>
          </div>

          {error && <p className="text-sm text-red-500">{error}</p>}

          <button
            className="w-full py-3 rounded-xl font-semibold text-tg-button-text flex items-center justify-center gap-2 disabled:opacity-50"
            style={{ background: 'var(--tg-theme-button-color)' }}
            onClick={handleSign}
            disabled={loading}
          >
            {loading ? <LoadingSpinner size="sm" /> : 'Sign with Wallet'}
          </button>

          <div className="text-center">
            <span className="text-xs text-tg-hint">New to Hyperliquid? </span>
            <button
              className="text-xs underline"
              style={{ color: 'var(--tg-theme-button-color)' }}
              onClick={handleNewAccount}
              disabled={loading}
            >
              Create a new account
            </button>
          </div>
        </div>
      )}

      {/* Step: new-account — show private key, then go to deposit */}
      {step === 'new-account' && (
        <div className="space-y-4">
          <div className="rounded-xl p-4 border border-yellow-400" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
            <div className="flex items-start gap-2">
              <span className="text-xl">⚠️</span>
              <div>
                <p className="text-sm font-semibold text-tg-text mb-1">Save your private key now</p>
                <p className="text-xs text-tg-hint">This is the only time it will be shown. Without it you cannot access your funds.</p>
              </div>
            </div>
          </div>

          <div className="rounded-xl p-4" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
            <div className="text-xs text-tg-hint mb-1">Your Hyperliquid Address</div>
            <div className="text-xs font-mono text-tg-text break-all mb-3">{newAddress}</div>
            <div className="text-xs text-tg-hint mb-1">Private Key</div>
            <div className="text-xs font-mono text-tg-text break-all bg-gray-100 dark:bg-gray-800 rounded p-2">{newPrivkey}</div>
          </div>

          <button
            className="w-full py-2.5 rounded-xl text-sm border border-gray-300 text-tg-text"
            onClick={copyPrivkey}
          >
            {privkeyCopied ? '✓ Copied!' : 'Copy Private Key'}
          </button>

          <button
            className="w-full py-3 rounded-xl font-semibold text-tg-button-text"
            style={{ background: 'var(--tg-theme-button-color)' }}
            onClick={() => setStep('deposit')}
          >
            I've saved my key — Continue
          </button>
        </div>
      )}

      {/* Step: builder — authorize platform fee */}
      {step === 'builder' && (
        <div className="space-y-4">
          <div className="rounded-xl p-4" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
            <p className="text-sm font-semibold text-tg-text mb-2">One more signature</p>
            <p className="text-sm text-tg-hint">
              Authorize a 0.05% platform fee on trades. This is how we keep the service running.
              The fee is taken by Hyperliquid on your behalf — we never touch your funds.
            </p>
          </div>
          {error && <p className="text-sm text-red-500">{error}</p>}
          <button
            className="w-full py-3 rounded-xl font-semibold text-tg-button-text flex items-center justify-center gap-2 disabled:opacity-50"
            style={{ background: 'var(--tg-theme-button-color)' }}
            onClick={() => walletProvider ? doBuilderSign(walletProvider) : undefined}
            disabled={loading || !walletProvider}
          >
            {loading ? <LoadingSpinner size="sm" /> : 'Authorize Platform Fee (0.05%)'}
          </button>
        </div>
      )}

      {/* Step: deposit — QR code for funding */}
      {step === 'deposit' && (
        <div className="space-y-4">
          <p className="text-sm text-tg-text text-center">
            Fund your Hyperliquid account to start copy trading
          </p>

          <div className="flex flex-col items-center gap-3 rounded-xl p-6" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
            <QRCodeSVG
              value={newAddress || connectedAddress || ''}
              size={180}
              level="M"
              className="rounded"
            />
            <div className="text-xs font-mono text-tg-text break-all text-center max-w-[240px]">
              {newAddress || connectedAddress}
            </div>
          </div>

          <div className="rounded-xl p-4" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
            <p className="text-xs font-semibold text-tg-text mb-2">How to deposit:</p>
            <ol className="text-xs text-tg-hint space-y-1 list-decimal list-inside">
              <li>Send <strong>USDC</strong> to the address above</li>
              <li>Use <strong>Arbitrum One</strong> network or Hyperliquid Bridge</li>
              <li>Minimum recommended: <strong>$20 USDC</strong></li>
            </ol>
          </div>

          <button
            className="w-full py-3 rounded-xl font-semibold text-tg-button-text"
            style={{ background: 'var(--tg-theme-button-color)' }}
            onClick={() => {
              setStep('done')
              setTimeout(onComplete, 1200)
            }}
          >
            I've funded my account
          </button>
        </div>
      )}

      {/* Step: done */}
      {step === 'done' && (
        <div className="flex flex-col items-center gap-4 pt-8">
          <div className="w-16 h-16 rounded-full bg-green-100 flex items-center justify-center text-3xl">✓</div>
          <p className="text-base font-semibold text-tg-text">Agent authorized!</p>
          <p className="text-sm text-tg-hint text-center">Copy trading is now active. Subscribe to traders to start.</p>
        </div>
      )}
    </div>
  )
}

function BuilderApprovalGate({ onComplete }: { onComplete: () => void }) {
  const [nonce, setNonce] = useState<number>(0)
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notRequired, setNotRequired] = useState(false)

  const { open } = useAppKit()
  const { isConnected } = useAppKitAccount()
  const { walletProvider } = useAppKitProvider<Eip1193Provider>('eip155')
  const pendingSign = useRef(false)

  const splitSig = (rawSig: string) => ({
    r: rawSig.slice(0, 66),
    s: '0x' + rawSig.slice(66, 130),
    v: parseInt(rawSig.slice(130, 132), 16),
  })

  useEffect(() => {
    walletBuilderSetup()
      .then((res) => {
        setNonce(res.nonce)
        setPayload(res.eip712_payload)
      })
      .catch(() => {
        // Builder not configured on server — let user through
        setNotRequired(true)
        onComplete()
      })
  }, [onComplete])

  const doSign = async (provider: Eip1193Provider) => {
    if (!payload) return
    setLoading(true)
    setError(null)
    try {
      const ethersProvider = new BrowserProvider(provider)
      const signer = await ethersProvider.getSigner()
      const { domain, types, message } = payload as {
        domain: Record<string, unknown>
        types: Record<string, { name: string; type: string }[]>
        message: Record<string, unknown>
        primaryType: string
      }
      const { EIP712Domain: _, ...filteredTypes } = types as Record<string, { name: string; type: string }[]>
      const rawSig: string = await signer.signTypedData(domain, filteredTypes, message)
      await walletBuilderApprove({ nonce, signature: splitSig(rawSig) })
      onComplete()
    } catch (err: unknown) {
      const axiosDetail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(axiosDetail ?? (err instanceof Error ? err.message : 'Signing failed'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (pendingSign.current && isConnected && walletProvider && payload) {
      pendingSign.current = false
      doSign(walletProvider)
    }
  }, [isConnected, walletProvider, payload])

  const handleSign = () => {
    if (!payload) return
    if (isConnected && walletProvider) {
      doSign(walletProvider)
    } else {
      pendingSign.current = true
      open({ view: 'Connect' })
    }
  }

  if (notRequired) return null

  return (
    <div className="px-4 pt-12 pb-20 flex flex-col items-center gap-6">
      <div className="w-16 h-16 rounded-full flex items-center justify-center text-3xl" style={{ background: 'var(--tg-theme-secondary-bg-color)' }}>
        📋
      </div>
      <div className="text-center">
        <h1 className="text-base font-semibold text-tg-text mb-2">Platform fee authorization required</h1>
        <p className="text-sm text-tg-hint">
          To use copy trading you need to authorize a one-time 0.05% platform fee.
          This is how we keep the service running — the fee is taken by Hyperliquid on your behalf.
        </p>
      </div>
      {error && <p className="text-sm text-red-500 text-center">{error}</p>}
      <button
        className="w-full py-3 rounded-xl font-semibold text-tg-button-text flex items-center justify-center gap-2 disabled:opacity-50"
        style={{ background: 'var(--tg-theme-button-color)' }}
        onClick={handleSign}
        disabled={loading || !payload}
      >
        {loading ? <LoadingSpinner size="sm" /> : 'Authorize Platform Fee (0.05%)'}
      </button>
    </div>
  )
}
