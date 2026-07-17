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
    const v = bar.value == null ? (bar.p_up_v1 == null ? bar.p_up : bar.p_up_v1) : bar.value
    if (v != null && Number.isFinite(v)) rows.push({ time: t, value: v })
    else rows.push({ time: t })
  }
  return rows
    .sort((a, b) => a.time - b.time)
    .filter((e, i, arr) => i === 0 || e.time > arr[i - 1].time)
}

const TVProbabilityChart = forwardRef(function TVProbabilityChart(
  { history = [], comparisonSeries, effectiveDate, hideXAxis = true },
  ref,
) {
  const containerRef = useRef()
  const chartRef = useRef(null)
  const seriesRef = useRef(null)
  const seriesByIdRef = useRef(new Map())

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
    const seriesById = seriesByIdRef.current

    const thresholds = chart.addSeries(LineSeries, {
      color: '#0c1218',
      lineWidth: 1,
      priceScaleId: 'right',
      crosshairMarkerVisible: false,
      priceLineVisible: false,
    })
    thresholds.createPriceLine({ price: 60, color: '#4ADE80', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'compra' })
    thresholds.createPriceLine({ price: 40, color: '#F87171', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'venda' })
    thresholds.createPriceLine({ price: 50, color: '#1E293B', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false })

    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      seriesById.clear()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (chartRef.current) chartRef.current.timeScale().applyOptions({ visible: !hideXAxis })
  }, [hideXAxis])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const definitions = comparisonSeries?.length
      ? comparisonSeries
      : [{
          id: 'v2', label: 'P(↑) Dinâmico', color: '#60A5FA',
          lineStyle: 'dashed', lineWidth: 2, visible: true, history,
        }]
    const desiredIds = new Set(definitions.filter(definition => definition.visible).map(definition => definition.id))

    for (const [id, series] of seriesByIdRef.current) {
      if (!desiredIds.has(id)) {
        chart.removeSeries(series)
        seriesByIdRef.current.delete(id)
      }
    }

    for (const definition of definitions) {
      if (!definition.visible) continue
      let series = seriesByIdRef.current.get(definition.id)
      if (!series) {
        series = chart.addSeries(LineSeries, {
          color: definition.color,
          lineWidth: definition.lineWidth,
          lineStyle: definition.lineStyle === 'dashed' ? LineStyle.Dashed : LineStyle.Solid,
          priceScaleId: 'right',
          crosshairMarkerVisible: true,
          priceLineVisible: false,
        })
        seriesByIdRef.current.set(definition.id, series)
      } else {
        series.applyOptions({
          color: definition.color,
          lineWidth: definition.lineWidth,
          lineStyle: definition.lineStyle === 'dashed' ? LineStyle.Dashed : LineStyle.Solid,
        })
      }
      series.setData(buildPUpSeriesData(definition.history || [], effectiveDate))
    }

    seriesRef.current = seriesByIdRef.current.get('v2') || seriesByIdRef.current.values().next().value || null
  }, [comparisonSeries, history, effectiveDate])

  const legend = comparisonSeries?.length
    ? comparisonSeries.filter(definition => definition.visible)
    : [{ id: 'v2', label: 'P(↑) Dinâmico', color: '#60A5FA', lineStyle: 'dashed' }]

  return (
    <div style={{ background: '#0c1218', borderRadius: hideXAxis ? '0' : '0 0 8px 8px', paddingBottom: 4, position: 'relative' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 14, padding: '6px 12px 2px 12px',
        borderTop: '1px solid #1a2530', flexWrap: 'wrap',
      }}>
        <span style={{ fontSize: 9, fontWeight: 'bold', color: '#6f8a9c', letterSpacing: 2 }}>P(↑) DINÂMICO</span>
        {legend.map(definition => (
          <span key={definition.id} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{
              display: 'inline-block', width: 14, height: 2, background: definition.color,
              borderTop: definition.lineStyle === 'dashed' ? `1px dashed ${definition.color}` : 'none',
            }} />
            <span style={{ fontSize: 9, color: definition.color }}>{definition.label}</span>
          </span>
        ))}
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
