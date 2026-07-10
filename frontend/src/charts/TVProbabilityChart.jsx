// P(↑) Dinâmico — reconstruído do componente TVProbabilityChart do bundle de
// produção (ro). Plota a probabilidade direcional (p_up_v1 se presente, senão
// p_up) com limiares de compra/venda em 60/40 e linha neutra em 50.

import { useRef, useEffect, forwardRef, useImperativeHandle } from 'react'
import { createChart, LineSeries, LineStyle } from 'lightweight-charts'
import { baseChartOptions, toUnixTime } from './tvShared'

function buildPUpSeriesData(history, effectiveDate) {
  const rows = []
  for (const bar of history) {
    if (!bar || !bar.time) continue
    const t = toUnixTime(effectiveDate, bar.time)
    if (t === 0) continue
    const v = bar.p_up_v1 == null ? bar.p_up : bar.p_up_v1
    if (v != null && Number.isFinite(v)) rows.push({ time: t, value: v })
    else rows.push({ time: t })
  }
  return rows
    .sort((a, b) => a.time - b.time)
    .filter((e, i, arr) => i === 0 || e.time > arr[i - 1].time)
}

const TVProbabilityChart = forwardRef(function TVProbabilityChart(
  { history = [], effectiveDate, hideXAxis = true },
  ref,
) {
  const containerRef = useRef()
  const chartRef = useRef(null)
  const seriesRef = useRef(null)

  useImperativeHandle(ref, () => ({
    getChart: () => chartRef.current,
    getMainSeries: () => seriesRef.current,
  }))

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      ...baseChartOptions(hideXAxis),
      rightPriceScale: { visible: true, borderColor: '#1a2530', scaleMargins: { top: 0.1, bottom: 0.1 }, minimumWidth: 45 },
    })
    chartRef.current = chart

    const series = chart.addSeries(LineSeries, {
      color: '#60A5FA',
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      priceScaleId: 'right',
      crosshairMarkerVisible: true,
      priceLineVisible: false,
    })
    series.createPriceLine({ price: 60, color: '#4ADE80', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'compra' })
    series.createPriceLine({ price: 40, color: '#F87171', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'venda' })
    series.createPriceLine({ price: 50, color: '#1E293B', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false })
    seriesRef.current = series

    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (chartRef.current) chartRef.current.timeScale().applyOptions({ visible: !hideXAxis })
  }, [hideXAxis])

  useEffect(() => {
    if (!chartRef.current || !seriesRef.current || !history.length) return
    seriesRef.current.setData(buildPUpSeriesData(history, effectiveDate))
  }, [history, effectiveDate])

  return (
    <div style={{ background: '#0c1218', borderRadius: hideXAxis ? '0' : '0 0 8px 8px', paddingBottom: 4, position: 'relative' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 14, padding: '6px 12px 2px 12px',
        borderTop: '1px solid #1a2530', flexWrap: 'wrap',
      }}>
        <span style={{ fontSize: 9, fontWeight: 'bold', color: '#6f8a9c', letterSpacing: 2 }}>P(↑) DINÂMICO</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{ display: 'inline-block', width: 14, height: 2, background: '#60A5FA', borderTop: '1px dashed #60A5FA', borderRadius: 1 }} />
          <span style={{ fontSize: 9, color: '#60A5FA' }}>P(↑) Dinâmico</span>
        </span>
      </div>
      <div ref={containerRef} style={{ width: '100%', height: '260px' }} />
      {(!history || history.length === 0) && (
        <div style={{
          position: 'absolute', top: 25, left: 0, right: 0, bottom: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: '#0c1218bb', zIndex: 10,
        }}>
          <span style={{ color: '#4a6070', fontSize: 12, letterSpacing: 1 }}>AGUARDANDO ABERTURA DO MERCADO</span>
        </div>
      )}
    </div>
  )
})

export default TVProbabilityChart
