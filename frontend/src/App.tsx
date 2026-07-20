import WebApp from '@twa-dev/sdk'
import { useEffect, useState } from 'react'
import { Route, Routes } from 'react-router-dom'
import { TabBar } from './components/TabBar'
import { FullPageSpinner } from './components/LoadingSpinner'
import { useAuthStore } from './store/authStore'
import { OnboardingPage } from './pages/OnboardingPage'
import { TradersPage } from './pages/TradersPage'
import { TraderDetailPage } from './pages/TraderDetailPage'
import { DemoSubscriptionDetailPage } from './pages/DemoSubscriptionDetailPage'
import { MyTradesPage } from './pages/MyTradesPage'
import { WalletPage } from './pages/WalletPage'
import { PortfoliosPage } from './pages/PortfoliosPage'
import { PortfolioDetailPage } from './pages/PortfolioDetailPage'
import { NewWalletsPage } from './pages/NewWalletsPage'
import { NewWalletSubscriptionDetailPage } from './pages/NewWalletSubscriptionDetailPage'

const ONBOARDING_KEY = 'onboarding_done'

export default function App() {
  const { jwt, login, loadCurrentUser, loading, error } = useAuthStore()
  const [onboardingDone, setOnboardingDone] = useState(
    () => localStorage.getItem(ONBOARDING_KEY) === '1',
  )

  // Apply Telegram theme params as CSS variables
  useEffect(() => {
    WebApp.ready()
    WebApp.expand()
    const tp = WebApp.themeParams
    const root = document.documentElement
    if (tp.bg_color) root.style.setProperty('--tg-theme-bg-color', tp.bg_color)
    if (tp.text_color) root.style.setProperty('--tg-theme-text-color', tp.text_color)
    if (tp.hint_color) root.style.setProperty('--tg-theme-hint-color', tp.hint_color)
    if (tp.link_color) root.style.setProperty('--tg-theme-link-color', tp.link_color)
    if (tp.button_color) root.style.setProperty('--tg-theme-button-color', tp.button_color)
    if (tp.button_text_color) root.style.setProperty('--tg-theme-button-text-color', tp.button_text_color)
    if (tp.secondary_bg_color) root.style.setProperty('--tg-theme-secondary-bg-color', tp.secondary_bg_color)
    if (WebApp.colorScheme === 'dark') root.classList.add('dark')
  }, [])

  // Authenticate on mount
  useEffect(() => {
    if (!jwt) {
      const initData = WebApp.initData
      if (initData) {
        login(initData)
      } else if (import.meta.env.DEV) {
        // In dev mode without Telegram, allow manual JWT in localStorage
        const devJwt = import.meta.env.VITE_DEV_JWT as string | undefined
        if (devJwt) useAuthStore.getState().setJwt(devJwt)
      }
    }
  }, [jwt, login])

  useEffect(() => {
    if (jwt) {
      void loadCurrentUser()
    }
  }, [jwt, loadCurrentUser])

  if (loading) return <FullPageSpinner />

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 px-6 text-center">
        <p className="text-red-500 text-sm">{error}</p>
        <p className="text-tg-hint text-xs">Open this app through Telegram</p>
      </div>
    )
  }

  if (!jwt) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-tg-hint text-sm">Authenticating…</p>
      </div>
    )
  }

  if (!onboardingDone) {
    return (
      <OnboardingPage
        onDone={() => {
          localStorage.setItem(ONBOARDING_KEY, '1')
          setOnboardingDone(true)
        }}
      />
    )
  }

  return (
    <div className="flex flex-col h-full">
      <main className="flex-1 overflow-hidden" style={{ paddingBottom: 56 }}>
        <Routes>
          <Route path="/" element={<TradersPage />} />
          <Route path="/traders/:id" element={<TraderDetailPage />} />
          <Route path="/portfolios" element={<PortfoliosPage />} />
          <Route path="/portfolios/:slug" element={<PortfolioDetailPage />} />
          <Route path="/new-wallets" element={<NewWalletsPage />} />
          <Route path="/new-wallet-subscriptions/:id" element={<NewWalletSubscriptionDetailPage />} />
          <Route path="/my-trades" element={<MyTradesPage />} />
          <Route path="/demo-subscriptions/:id" element={<DemoSubscriptionDetailPage />} />
          <Route path="/wallet" element={<WalletPage />} />
        </Routes>
      </main>
      <TabBar />
    </div>
  )
}
