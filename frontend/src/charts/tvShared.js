// Utilidades compartilhadas dos gráficos TradingView lightweight-charts (v5).
//
// A migração Recharts → lightweight-charts é incremental (strangler-fig): cada
// chart vira um componente próprio em src/charts/, e todos reusam a base daqui
// para manter tema/eixo/tempo consistentes. Ver docs/plans/ (benchmark prod).
//
// Eixo de tempo: o backend normaliza tudo no eixo EEST (Tickmill) e cada barra
// carrega `time` = "HH:MM" (substring do timestamp). Convertemos "HH:MM" + a
// data da sessão para um unix timestamp UTC e formatamos os labels com
// getUTC* — ou seja, tratamos o horário EEST como se fosse UTC, exatamente como
// a produção faz. O eixo BRT âmbar (assets B3) é responsabilidade do chart que
// desenha o eixo secundário, não deste helper.

import { CrosshairMode, LineStyle } from 'lightweight-charts'

/** "HH:MM" + data da sessão → unix seconds (UTC). Retorna 0 se inválido. */
export function toUnixTime(effectiveDate, timeStr) {
  if (!timeStr) return 0
  let y = 2026, mo = 6, d = 26
  if (effectiveDate && effectiveDate.includes('-')) {
    const parts = effectiveDate.split('-').map(Number)
    if (parts.length === 3 && !parts.some(Number.isNaN)) [y, mo, d] = parts
  }
  const [h, mi] = String(timeStr).split(':').map(Number)
  if (Number.isNaN(h) || Number.isNaN(mi)) return 0
  return Math.floor(Date.UTC(y, mo - 1, d, h, mi) / 1000)
}

/** Formata um unix timestamp (s) como "HH:MM" no relógio EEST (via getUTC*). */
export function fmtHHMM(t) {
  const d = new Date(t * 1000)
  return String(d.getUTCHours()).padStart(2, '0') + ':' + String(d.getUTCMinutes()).padStart(2, '0')
}

/** Opções base do createChart — tema escuro do dashboard, sem grid, crosshair âmbar. */
export function baseChartOptions(hideXAxis = true) {
  return {
    layout: {
      background: { type: 'solid', color: '#0c1218' },
      textColor: '#4a6070',
      fontSize: 9,
      attributionLogo: false,
    },
    localization: { timeFormatter: fmtHHMM },
    grid: { vertLines: { visible: false }, horzLines: { visible: false } },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { color: '#c8a444', width: 1, style: LineStyle.Solid, labelBackgroundColor: '#c8a444' },
      horzLine: { visible: false, labelVisible: true, labelBackgroundColor: '#c8a444' },
    },
    timeScale: {
      visible: !hideXAxis,
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 15,
      tickMarkFormatter: fmtHHMM,
    },
    rightPriceScale: { visible: true, borderColor: '#1a2530', autoScale: true, minimumWidth: 45 },
    leftPriceScale: { visible: false, minimumWidth: 55 },
    autoSize: true,
  }
}

/**
 * Converte a série processada em pontos {time, value} para uma line series.
 * Barras sem valor viram "whitespace" (só {time}) → a linha abre um gap em vez
 * de interpolar. lightweight-charts exige tempo estritamente crescente e único,
 * então ordenamos e removemos timestamps duplicados (senão a lib lança erro).
 */
export function buildSeriesData(history, effectiveDate, valueKey) {
  const rows = []
  for (const bar of history) {
    if (!bar || !bar.time) continue
    const t = toUnixTime(effectiveDate, bar.time)
    if (t === 0) continue
    const v = bar[valueKey]
    if (v != null && Number.isFinite(v)) rows.push({ time: t, value: v })
    else rows.push({ time: t })
  }
  return rows
    .sort((a, b) => a.time - b.time)
    .filter((e, i, arr) => i === 0 || e.time > arr[i - 1].time)
}
