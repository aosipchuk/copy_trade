import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

const STEPS = [
  {
    icon: '📊',
    title: 'Follow top traders',
    desc: 'Browse Hyperliquid leaderboard with real-time PnL, ROI and open positions.',
  },
  {
    icon: '🔒',
    title: 'How your money is protected',
    desc: null,
    security: true,
  },
  {
    icon: '💼',
    title: 'Connect your wallet',
    desc: 'Authorize a secure agent key. It can only place orders — never withdraw funds.',
  },
  {
    icon: '⚡',
    title: 'Auto copy trades',
    desc: 'Trades are mirrored instantly. Set allocation limits and stop-loss to stay in control.',
  },
]

interface Props {
  onDone: () => void
}

export function OnboardingPage({ onDone }: Props) {
  const [step, setStep] = useState(0)
  const navigate = useNavigate()

  const isLast = step === STEPS.length - 1
  const s = STEPS[step]

  return (
    <div className="flex flex-col h-full px-6 pt-16 pb-10">
      <div className="flex-1 flex flex-col items-center justify-center text-center gap-6">
        <div className="text-6xl">{s.icon}</div>
        <div className="w-full">
          <h1 className="text-xl font-bold text-tg-text mb-2">{s.title}</h1>
          {s.security ? (
            <ul className="text-left space-y-2.5 mt-4">
              {[
                'Agent can only TRADE on your account',
                'Agent CANNOT withdraw your funds',
                'You can revoke access anytime',
                'Your private key never touches our servers',
                'All trades are visible in your history',
              ].map((item) => (
                <li key={item} className="flex items-start gap-2 text-sm text-tg-text">
                  <span className="text-green-500 shrink-0">✓</span>
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-tg-hint leading-relaxed">{s.desc}</p>
          )}
        </div>
        {/* Dots */}
        <div className="flex gap-2">
          {STEPS.map((_, i) => (
            <div
              key={i}
              className={`h-1.5 rounded-full transition-all ${i === step ? 'w-6' : 'w-1.5 bg-tg-hint opacity-40'}`}
              style={i === step ? { background: 'var(--tg-theme-button-color)', width: 24 } : {}}
            />
          ))}
        </div>
      </div>

      <div className="space-y-2">
        <button
          className="w-full py-3 rounded-xl font-semibold text-tg-button-text"
          style={{ background: 'var(--tg-theme-button-color)' }}
          onClick={() => {
            if (isLast) {
              onDone()
              navigate('/wallet')
            } else {
              setStep((s) => s + 1)
            }
          }}
        >
          {isLast ? 'Get started' : 'Next'}
        </button>
        {!isLast && (
          <button className="w-full py-2 text-sm text-tg-hint" onClick={onDone}>
            Skip
          </button>
        )}
      </div>
    </div>
  )
}
