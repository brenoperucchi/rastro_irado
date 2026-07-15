// Z-Score Dinâmico (Divergência Preço) — reconstruído do componente
// TVPriceDivergeZScoreChart do bundle de produção (fo). Plota
// `price_diverge_z` (z-score de divergência preço vs P(↑)) com as mesmas
// bandas ±2 usadas no chart de pair spread.

import { useRef, useEffect, forwardRef, useImperativeHandle } from 'react'
import { createChart, LineSeries, LineStyle } from 'lightweight-charts'
import { baseChartOptions, buildSeriesData } from './tvShared'

const TVPriceDivergeZScoreChart = forwardRef(function TVPriceDivergeZScoreChart(
  // threshold: price_diverge_threshold real do backend (divergence_config,
  // via /api/irai/targets) — thresholds canônicos. Default 0.5 só como
  // fallback antes do primeiro fetch, igual ao DEFAULT_DIV_THRESHOLD do
  // backend (backend/irai/engine.py); nunca hardcoded ±2 (doc §7.1/7.2).
  { history = [], effectiveDate, hideXAxis = true, threshold = 0.5 },
  ref,
) {
  const containerRef = useRef()
  const chartRef = useRef(null)
  const seriesRef = useRef(null)
  const sellLineRef = useRef(null)
  const buyLineRef = useRef(null)

  useImperativeHandle(ref, () => ({
    getChart: () => chartRef.current,
    getMainSeries: () => seriesRef.current,
  }))

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, baseChartOptions(hideXAxis))
    chartRef.current = chart

    const series = chart.addSeries(LineSeries, {
      color: '#38BDF8',
      lineWidth: 1.5,
      priceLineVisible: false,
      crosshairMarkerVisible: true,
      autoscaleInfoProvider: () => ({ priceRange: { minValue: -4, maxValue: 4 } }),
    })
    sellLineRef.current = series.createPriceLine({ price: threshold, color: '#F87171', lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: 'venda' })
    buyLineRef.current = series.createPriceLine({ price: -threshold, color: '#4ADE80', lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: 'compra' })
    series.createPriceLine({ price: 0, color: '#475569', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false })
    seriesRef.current = series

    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      sellLineRef.current = null
      buyLineRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (chartRef.current) chartRef.current.timeScale().applyOptions({ visible: !hideXAxis })
  }, [hideXAxis])

  useEffect(() => {
    if (!sellLineRef.current || !buyLineRef.current) return
    sellLineRef.current.applyOptions({ price: threshold })
    buyLineRef.current.applyOptions({ price: -threshold })
  }, [threshold])

  useEffect(() => {
    if (!chartRef.current || !seriesRef.current || !history.length) return
    seriesRef.current.setData(buildSeriesData(history, effectiveDate, 'price_diverge_z'))
  }, [history, effectiveDate])

  return (
    <div style={{ background: '#0c1218', borderRadius: hideXAxis ? '0' : '0 0 8px 8px', paddingBottom: 4, position: 'relative' }}>
      <div ref={containerRef} style={{ width: '100%', height: '100px' }} />
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

export default TVPriceDivergeZScoreChart
