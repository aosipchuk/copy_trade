import { createChart, ColorType, TickMarkType, type UTCTimestamp } from 'lightweight-charts'
import { useEffect, useRef } from 'react'
import type { EquityPoint, Period } from '../types'

interface Props {
  data: EquityPoint[]
  period: Period
  height?: number
}

const PERIOD_SECONDS: Record<Period, number | null> = {
  day: 86_400,
  week: 7 * 86_400,
  month: 30 * 86_400,
  allTime: null,
}

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const DAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function makeTickFormatter(period: Period) {
  return (time: UTCTimestamp, type: TickMarkType): string => {
    const d = new Date((time as number) * 1000)

    if (period === 'day') {
      return `${d.getUTCHours().toString().padStart(2, '0')}:00`
    }

    if (period === 'week') {
      const h = d.getUTCHours()
      return h === 0
        ? DAY[d.getUTCDay()]
        : `${DAY[d.getUTCDay()]} ${h.toString().padStart(2, '0')}:00`
    }

    if (period === 'month') {
      return `${MON[d.getUTCMonth()]} ${d.getUTCDate()}`
    }

    // allTime — show year at year boundaries, "Mon 'YY" otherwise
    return type === TickMarkType.Year
      ? String(d.getUTCFullYear())
      : `${MON[d.getUTCMonth()]} '${String(d.getUTCFullYear()).slice(2)}`
  }
}

export function EquityChart({ data, period, height = 180 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container || data.length === 0) return

    const isDark = document.documentElement.classList.contains('dark')
    const style = getComputedStyle(document.documentElement)
    const textColor = style.getPropertyValue('--tg-theme-hint-color').trim() || '#999'
    const lineColor = style.getPropertyValue('--tg-theme-button-color').trim() || '#2481cc'
    const bgColor = style.getPropertyValue('--tg-theme-bg-color').trim() || (isDark ? '#212121' : '#ffffff')

    // clientWidth may be 0 if container hasn't been laid out yet; fall back to bounding rect
    const width = container.clientWidth || container.getBoundingClientRect().width || 300

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: bgColor },
        textColor,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: isDark ? '#333' : '#f0f0f0' },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: {
        borderVisible: false,
        // Show intra-day times in labels for day/week; dates suffice for month/allTime
        timeVisible: period === 'day' || period === 'week',
        secondsVisible: false,
        tickMarkFormatter: makeTickFormatter(period),
      },
      crosshair: { horzLine: { visible: false } },
      width,
      height,
    })

    const series = chart.addLineSeries({
      color: lineColor,
      lineWidth: 2,
      priceLineVisible: false,
    })

    // Backend sends naive UTC datetimes (no 'Z'). Appending 'Z' forces correct UTC
    // parsing — without it, browsers treat the string as local time.
    // Deduplicate same-second timestamps (keep last cumulative value) then sort ascending,
    // as lightweight-charts requires strictly increasing time values.
    const deduped = new Map<number, number>()
    for (const p of data) {
      const t = Math.floor(new Date(p.ts + 'Z').getTime() / 1000)
      deduped.set(t, p.pnl)
    }
    const chartData = Array.from(deduped.entries())
      .sort(([a], [b]) => a - b)
      .map(([time, value]) => ({ time: time as UTCTimestamp, value }))

    series.setData(chartData)

    // Pin the visible range to [now - period, now] so the X axis always reflects the
    // chosen timeframe and tick marks are spaced correctly for that granularity.
    // allTime uses fitContent() so all data is always visible.
    const periodSec = PERIOD_SECONDS[period]
    if (periodSec !== null && chartData.length > 0) {
      const nowSec = Math.floor(Date.now() / 1000) as UTCTimestamp
      chart.timeScale().setVisibleRange({ from: (nowSec - periodSec) as UTCTimestamp, to: nowSec })
    } else {
      chart.timeScale().fitContent()
    }

    // ResizeObserver tracks container width changes (window.resize misses flex reflows)
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w && w > 0) chart.applyOptions({ width: w })
    })
    ro.observe(container)

    return () => {
      ro.disconnect()
      chart.remove()
    }
  }, [data, period, height])

  return <div ref={containerRef} style={{ height }} />
}
