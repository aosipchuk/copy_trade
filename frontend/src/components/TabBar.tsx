import { NavLink } from 'react-router-dom'

const tabs = [
  { to: '/', label: 'Traders', icon: '📊' },
  { to: '/portfolios', label: 'Portfolios', icon: '▦' },
  { to: '/my-trades', label: 'My Trades', icon: '📋' },
  { to: '/wallet', label: 'Wallet', icon: '💼' },
]

export function TabBar() {
  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-50 flex items-center border-t border-gray-200 dark:border-gray-700"
      style={{ background: 'var(--tg-theme-bg-color, #fff)', paddingBottom: 'env(safe-area-inset-bottom)' }}
    >
      {tabs.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end={tab.to === '/'}
          className={({ isActive }) =>
            `flex flex-1 flex-col items-center py-2 text-xs transition-colors ${
              isActive ? 'text-tg-button' : 'text-tg-hint'
            }`
          }
        >
          <span className="text-xl leading-tight">{tab.icon}</span>
          <span className="mt-0.5">{tab.label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
