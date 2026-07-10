// Pesos Dinâmicos (Kalman Filter) — reconstruído do componente
// TVKalmanWeightsChart do bundle de produção (co). Uma line series por
// fator ativo na cesta (`weight_<fator>`), recriadas sempre que o conjunto
// de fatores (`factorKeys`) muda — mesmo comportamento da produção.

import { useRef, useEffect, forwardRef, useImperativeHandle } from 'react'
import { createChart, LineSeries, LineStyle } from 'lightweight-charts'
import { baseChartOptions, toUnixTime } from './tvShared'

const FACTOR_COLORS = [
  '#4ADE80', '#60A5FA', '#FBBF24', '#C084FC',
  '#F87171', '#2DD4BF', '#F472B6', '#A3E635',
  '#FCD34D', '#A78BFA', '#34D399', '#FB923C',
]

const TVKalmanWeightsChart = forwardRef(function TVKalmanWeightsChart(
  { history = [], factorKeys = [], effectiveDate, hideXAxis = true },
  ref,
) {
  const containerRef = useRef()
  const chartRef = useRef(null)
  const seriesByFactorRef = useRef({})

  useImperativeHandle(ref, () => ({
    getChart: () => chartRef.current,
    getMainSeries: () => {
      const keys = Object.keys(seriesByFactorRef.current)
      return keys.length > 0 ? seriesByFactorRef.current[keys[0]] : null
    },
  }))

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, baseChartOptions(hideXAxis))
    chartRef.current = chart

    const seriesByFactor = {}
    factorKeys.forEach((key, idx) => {
      seriesByFactor[key] = chart.addSeries(LineSeries, {
        color: FACTOR_COLORS[idx % FACTOR_COLORS.length],
        lineWidth: 1.5,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      })
    })
    chart.addSeries(LineSeries, { visible: false })
      .createPriceLine({ price: 0, color: '#475569', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false })
    seriesByFactorRef.current = seriesByFactor

    return () => {
      chart.remove()
      chartRef.current = null
      seriesByFactorRef.current = {}
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [factorKeys])

  useEffect(() => {
    if (chartRef.current) chartRef.current.timeScale().applyOptions({ visible: !hideXAxis })
  }, [hideXAxis])

  useEffect(() => {
    if (!chartRef.current || !history.length || !factorKeys.length) return
    const seriesByFactor = seriesByFactorRef.current
    const rowsByFactor = {}
    factorKeys.forEach(key => { rowsByFactor[key] = [] })

    for (const bar of history) {
      if (!bar || !bar.time) continue
      const t = toUnixTime(effectiveDate, bar.time)
      if (t === 0) continue
      factorKeys.forEach(key => {
        const v = bar[`weight_${key}`]
        if (v != null && Number.isFinite(v)) rowsByFactor[key].push({ time: t, value: v })
        else rowsByFactor[key].push({ time: t })
      })
    }

    const sortDedupe = rows => rows
      .sort((a, b) => a.time - b.time)
      .filter((e, i, arr) => i === 0 || e.time > arr[i - 1].time)

    factorKeys.forEach(key => {
      if (seriesByFactor[key]) seriesByFactor[key].setData(sortDedupe(rowsByFactor[key]))
    })
  }, [history, factorKeys, effectiveDate])

  return (
    <div style={{ background: '#0c1218', borderRadius: hideXAxis ? '0' : '0 0 8px 8px', paddingBottom: 4, position: 'relative' }}>
      <div ref={containerRef} style={{ width: '100%', height: '140px' }} />
      {(!history || history.length === 0) && (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: '#0c1218bb', zIndex: 10,
        }}>
          <span style={{ color: '#4a6070', fontSize: 12, letterSpacing: 1 }}>AGUARDANDO ABERTURA DO MERCADO</span>
        </div>
      )}
    </div>
  )
})

export default TVKalmanWeightsChart
