// Movimento do índice + NWE (Nadaraya-Watson Envelope) — reconstruído do
// componente TVNweChart do bundle de produção (`ao`). É o chart de preço
// principal da página de detalhe: uma série de preço (`win_current`, linha ou
// candle), a banda superior/inferior do envelope (`nwe_upper_price`/
// `nwe_lower_price`, tracejadas) e a linha central (`nwe_center_price`) colorida
// ponto-a-ponto pela direção do slope. Trabalha em ESPAÇO DE PREÇO ABSOLUTO
// (não em retorno %), igual à produção.
//
// Diferenças conscientes vs. o chart Recharts antigo que ele substitui:
//  - Sem eixo BRT âmbar secundário: lightweight-charts só tem uma timeScale por
//    chart; os outros dois charts de baixo já migrados também não o desenham, e
//    a produção idem. Regressão visual conhecida e aceita (a data segue alinhada
//    no eixo EEST-como-UTC).
//  - `walls`/showGex/showMidWalls (GEX) e os markers de sinal (pair/z) existem
//    no código fiel à produção, mas só renderizam quando o backend emitir os
//    campos correspondentes (`walls[]`, `pair_compra`, `z_venda_val`, ...).
//    Hoje esses campos não existem → nada renderiza. Feature real fica na task
//    de GEX; não fabricamos os campos a partir dos thresholds dos badges (seriam
//    eventos contínuos/spam, não os eventos discretos por barra da produção).

import { useRef, useEffect, forwardRef, useImperativeHandle } from 'react'
import {
  createChart, LineSeries, CandlestickSeries, LineStyle, createSeriesMarkers,
} from 'lightweight-charts'
import { baseChartOptions, toUnixTime } from './tvShared'

// Ordena por tempo e remove timestamps duplicados, preservando o objeto inteiro
// da linha (cor por ponto, OHLC, shape/text de marker) — buildSeriesData do
// tvShared só mantém {time,value}, então não serve aqui. lightweight-charts v5
// exige tempo estritamente crescente e único por série.
function sortDedupe(rows) {
  return rows
    .sort((a, b) => a.time - b.time)
    .filter((e, i, arr) => i === 0 || e.time > arr[i - 1].time)
}

const TVNweChart = forwardRef(function TVNweChart(
  {
    history = [],
    walls = [],
    showGex = false,
    showMidWalls = false,
    effectiveDate,
    hideXAxis = true,
    chartType = 'line',
  },
  ref,
) {
  const containerRef = useRef()
  const chartRef = useRef(null)
  const seriesRef = useRef({})

  useImperativeHandle(ref, () => ({
    getChart: () => chartRef.current,
    getMainSeries: () => seriesRef.current.winSeries,
  }))

  // Cria o chart e as séries. Recriado quando chartType muda (line <-> candle),
  // igual à produção — hoje chartType é fixo em 'line', então roda uma vez só.
  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      ...baseChartOptions(hideXAxis),
      rightPriceScale: { visible: true, borderColor: '#1a2530', scaleMargins: { top: 0.15, bottom: 0.15 }, minimumWidth: 45 },
    })
    chartRef.current = chart

    // Bandas e centro adicionados ANTES da série de preço → preço desenha por cima.
    const nweUpperSeries = chart.addSeries(LineSeries, {
      color: '#F87171', lineWidth: 1, lineStyle: LineStyle.Dashed,
      crosshairMarkerVisible: false, priceLineVisible: false,
    })
    const nweLowerSeries = chart.addSeries(LineSeries, {
      color: '#4ADE80', lineWidth: 1, lineStyle: LineStyle.Dashed,
      crosshairMarkerVisible: false, priceLineVisible: false,
    })
    const nweCenterSeries = chart.addSeries(LineSeries, {
      color: '#38BDF8', lineWidth: 1.5,
      crosshairMarkerVisible: false, priceLineVisible: false,
    })
    const winSeries = chartType === 'candle'
      ? chart.addSeries(CandlestickSeries, {
          upColor: '#22c55e', downColor: '#ef4444', borderVisible: false,
          wickUpColor: '#22c55e', wickDownColor: '#ef4444',
          crosshairMarkerVisible: true, priceLineVisible: false,
        })
      : chart.addSeries(LineSeries, {
          color: '#E2E8F0', lineWidth: 1.5,
          crosshairMarkerVisible: true, priceLineVisible: false,
        })

    // Handle de markers criado uma vez (v5 removeu series.setMarkers).
    const markersApi = createSeriesMarkers(winSeries, [])
    seriesRef.current = { winSeries, nweUpperSeries, nweLowerSeries, nweCenterSeries, markersApi, dynamicWalls: [] }

    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = {}
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartType])

  useEffect(() => {
    if (chartRef.current) chartRef.current.timeScale().applyOptions({ visible: !hideXAxis })
  }, [hideXAxis])

  // Popula preço + bandas + centro + markers.
  useEffect(() => {
    if (!chartRef.current || !history.length) return
    const { winSeries, nweUpperSeries, nweLowerSeries, nweCenterSeries, markersApi } = seriesRef.current
    if (!winSeries) return

    const winRows = [], upperRows = [], lowerRows = [], centerRows = [], markers = []

    for (const bar of history) {
      if (!bar || !bar.time) continue
      const t = toUnixTime(effectiveDate, bar.time)
      if (t === 0) continue

      // Preço
      if (chartType === 'candle') {
        const close = bar.win_current
        if (close != null && Number.isFinite(close)) {
          const o = bar.win_bar_open == null ? close : bar.win_bar_open
          const h = bar.win_high == null ? close : bar.win_high
          const l = bar.win_low == null ? close : bar.win_low
          winRows.push({ time: t, open: o, high: h, low: l, close })
        }
      } else if (bar.win_current != null && Number.isFinite(bar.win_current)) {
        winRows.push({ time: t, value: bar.win_current })
      } else {
        winRows.push({ time: t })
      }

      // Bandas: desenham flat sobre ghost bars (carry-forward), igual ao Recharts.
      if (bar.nwe_upper_price != null && Number.isFinite(bar.nwe_upper_price)) upperRows.push({ time: t, value: bar.nwe_upper_price })
      else upperRows.push({ time: t })
      if (bar.nwe_lower_price != null && Number.isFinite(bar.nwe_lower_price)) lowerRows.push({ time: t, value: bar.nwe_lower_price })
      else lowerRows.push({ time: t })

      // Centro: gap sobre ghost bars (whitespace) — preserva o comportamento do
      // Recharts (nwe_up/nwe_down eram null p/ ghost) e evita colorir a barra
      // fantasma pelo slope-booleano legado. Cor só em barras reais.
      if (!bar.is_ghost && bar.nwe_center_price != null && Number.isFinite(bar.nwe_center_price)) {
        centerRows.push({ time: t, value: bar.nwe_center_price, color: bar.nwe_slope >= 0 ? '#4ADE80' : '#F87171' })
      } else {
        centerRows.push({ time: t })
      }

      // Markers de sinal (círculo = par, quadrado = z-divergência). Só quando o
      // backend emitir os campos — hoje ausentes → nenhum marker.
      if (bar.pair_compra != null && Number.isFinite(bar.pair_compra)) {
        markers.push({ time: t, position: 'belowBar', shape: 'circle', color: '#4ADE80', text: 'P COMPRA', size: 1 })
      } else if (bar.pair_venda != null && Number.isFinite(bar.pair_venda)) {
        markers.push({ time: t, position: 'aboveBar', shape: 'circle', color: '#F87171', text: 'P VENDA', size: 1 })
      }
      if (bar.z_compra_val != null && Number.isFinite(bar.z_compra_val)) {
        markers.push({ time: t, position: 'belowBar', shape: 'square', color: '#4ADE80', text: 'Z COMPRA', size: 1 })
      } else if (bar.z_venda_val != null && Number.isFinite(bar.z_venda_val)) {
        markers.push({ time: t, position: 'aboveBar', shape: 'square', color: '#F87171', text: 'Z VENDA', size: 1 })
      }
    }

    winSeries.setData(sortDedupe(winRows))
    nweUpperSeries.setData(sortDedupe(upperRows))
    nweLowerSeries.setData(sortDedupe(lowerRows))
    nweCenterSeries.setData(sortDedupe(centerRows))
    // Markers só precisam estar ordenados por tempo; tempos duplicados são
    // permitidos (empilham) — NÃO deduplicar, senão um sinal par + z no mesmo
    // bar perderia o segundo marker. Igual à produção (sort sem dedupe).
    if (markersApi) markersApi.setMarkers(markers.slice().sort((a, b) => a.time - b.time))
  }, [history, effectiveDate, chartType])

  // Walls (GEX) via createPriceLine. Recria as linhas quando walls/toggles mudam;
  // não depende de history/effectiveDate. Hoje showGex=false → nenhuma linha.
  useEffect(() => {
    const { winSeries } = seriesRef.current
    if (!winSeries) return
    if (seriesRef.current.dynamicWalls) seriesRef.current.dynamicWalls.forEach(pl => winSeries.removePriceLine(pl))
    const created = []
    if (showGex && Array.isArray(walls)) {
      walls.forEach(w => {
        if (w.type === 'mid_wall' && !showMidWalls) return
        const style = w.style === 'dashed' ? LineStyle.Dashed : LineStyle.Solid
        const isWallColor = w.type === 'wall' || w.type === 'mid_wall'
        created.push(winSeries.createPriceLine({
          price: w.price, color: isWallColor ? '#FFFFFF' : w.color,
          lineWidth: w.type === 'mid_wall' ? 1 : 2, lineStyle: style,
          axisLabelVisible: false, title: '',
        }))
      })
    }
    seriesRef.current.dynamicWalls = created
  }, [walls, showGex, showMidWalls, chartType])

  return (
    <div style={{ background: '#0c1218', borderRadius: hideXAxis ? '0' : '0 0 8px 8px', paddingBottom: 4, position: 'relative' }}>
      <div ref={containerRef} style={{ width: '100%', height: '320px' }} />
      {(!history || history.length === 0) && (
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: '#0c1218bb', zIndex: 10,
        }}>
          <span style={{ color: '#4a6070', fontSize: 12, letterSpacing: 1 }}>AGUARDANDO ABERTURA DO MERCADO</span>
        </div>
      )}
    </div>
  )
})

export default TVNweChart
