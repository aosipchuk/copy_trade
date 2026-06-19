import { EthersAdapter } from '@reown/appkit-adapter-ethers'
import { arbitrum, arbitrumSepolia } from '@reown/appkit/networks'
import type { CreateAppKit } from '@reown/appkit/react'

const projectId = import.meta.env.VITE_WALLETCONNECT_PROJECT_ID as string

if (!projectId) {
  console.warn('VITE_WALLETCONNECT_PROJECT_ID is not set — wallet signing will not work')
}

// MetaMask, Trust Wallet, Coinbase Wallet — surfaced first in the connect modal
const FEATURED_WALLET_IDS = [
  'c57ca95b47569778a828d19178114f4db188b89b763c899ba0be274e97267d96', // MetaMask
  '4622a2b2d6af1c9844944291e5e7351a6aa24cd7b23099efac1b2fd875da31a0', // Trust Wallet
  'fd20dc426fb37566d803205b19bbc1d4096b248ac04548e3cfb6b3a38bd033aa', // Coinbase Wallet
]

export const appkitConfig: CreateAppKit = {
  adapters: [new EthersAdapter()],
  networks: [arbitrum, arbitrumSepolia],
  defaultNetwork: arbitrum,
  projectId: projectId ?? '',
  metadata: {
    name: 'Copy Trade',
    description: 'Copy top Hyperliquid traders',
    url: typeof window !== 'undefined' ? window.location.origin : 'https://copytrade.app',
    icons: [],
  },
  features: {
    analytics: false,
    email: false,
    socials: [],
  },
  featuredWalletIds: FEATURED_WALLET_IDS,
}
