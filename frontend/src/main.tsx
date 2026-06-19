import { AppKitProvider } from '@reown/appkit/react'
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { appkitConfig } from './lib/appkit'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppKitProvider {...appkitConfig}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </AppKitProvider>
  </React.StrictMode>,
)
