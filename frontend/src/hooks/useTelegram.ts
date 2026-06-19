import WebApp from '@twa-dev/sdk'
import { useEffect } from 'react'

export function useTelegram() {
  useEffect(() => {
    WebApp.ready()
    WebApp.expand()
  }, [])

  return {
    WebApp,
    initData: WebApp.initData,
    colorScheme: WebApp.colorScheme,
    themeParams: WebApp.themeParams,
  }
}

export function useMainButton(text: string, onClick: () => void, visible = true) {
  useEffect(() => {
    if (!visible) {
      WebApp.MainButton.hide()
      return
    }
    WebApp.MainButton.setText(text)
    WebApp.MainButton.show()
    WebApp.MainButton.onClick(onClick)
    return () => {
      WebApp.MainButton.offClick(onClick)
      WebApp.MainButton.hide()
    }
  }, [text, onClick, visible])
}

export function useBackButton(onClick: () => void) {
  useEffect(() => {
    WebApp.BackButton.show()
    WebApp.BackButton.onClick(onClick)
    return () => {
      WebApp.BackButton.offClick(onClick)
      WebApp.BackButton.hide()
    }
  }, [onClick])
}
