import { createPortal } from 'react-dom'

interface Props {
  onCancel: () => void
  onKeepPositions: () => void
  onClosePositions: () => void
}

export function UnsubscribeModal({ onCancel, onKeepPositions, onClosePositions }: Props) {
  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-end" onClick={onCancel}>
      <div
        className="w-full rounded-t-2xl"
        style={{ background: 'var(--tg-theme-bg-color, #fff)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 pt-5 pb-4">
          <h2 className="text-base font-semibold text-tg-text mb-1">Unsubscribe</h2>
          <p className="text-sm text-tg-hint">What should happen to your open copy positions?</p>
        </div>

        <div
          className="px-5 space-y-2"
          style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 16px)' }}
        >
          <button
            className="w-full py-3 rounded-xl text-sm font-semibold border border-red-400 text-red-400"
            onClick={onClosePositions}
          >
            Close All Positions
          </button>
          <button
            className="w-full py-3 rounded-xl text-sm font-semibold border border-gray-300 dark:border-gray-600 text-tg-text"
            onClick={onKeepPositions}
          >
            Keep Positions Open
          </button>
          <button
            className="w-full py-3 rounded-xl text-sm text-tg-hint"
            onClick={onCancel}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
