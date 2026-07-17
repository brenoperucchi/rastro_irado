import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useSwipeable } from 'react-swipeable'
import Overview from './Overview'
import TVPairwiseZScoreChart from './charts/TVPairwiseZScoreChart'
import TVProbabilityChart from './charts/TVProbabilityChart'
import TVPriceDivergeZScoreChart from './charts/TVPriceDivergeZScoreChart'
import TVKalmanWeightsChart from './charts/TVKalmanWeightsChart'
import TVNweChart from './charts/TVNweChart'

const FIREBASE_URL = import.meta.env.VITE_FIREBASE_URL
// window.location.hostname (não 'localhost' fixo) para funcionar tanto local
// quanto acessado remotamente via IP da LAN (ex: dashboard aberto de outra máquina).
const API = FIREBASE_URL ? null : `http://${window.location.hostname}:8888`
const WS_URL = FIREBASE_URL ? null : `ws://${window.location.hostname}:8888/ws/irai`

// Mapa cosmético de labels para fatores conhecidos
const FACTOR_DISPLAY = {
  win: { label: 'WIN', icon: 'BR' }, dol: { label: 'DÓLAR', icon: 'US' },
  di1: { label: 'JUROS', icon: 'BR' }, dxy: { label: 'DXY', icon: 'DX' },
  brent: { label: 'PETRÓLEO', icon: 'PT' }, china50: { label: 'CHINA50', icon: 'CN' },
  usdmxn: { label: 'USDMXN', icon: 'MX' }, vix: { label: 'VIX', icon: 'VX' },
  btcusd: { label: 'BITCOIN', icon: '₿' }, us500: { label: 'S&P 500', icon: 'US' },
  us30: { label: 'DOW 30', icon: 'DJ' }, ustec: { label: 'NASDAQ', icon: 'NQ' },
  xauusd: { label: 'OURO', icon: 'GL' }, eurusd: { label: 'EUR/USD', icon: 'EU' },
  gbpusd: { label: 'GBP/USD', icon: 'GB' }, usdjpy: { label: 'USD/JPY', icon: 'JP' },
  audusd: { label: 'AUD/USD', icon: 'AU' }, usdcad: { label: 'USD/CAD', icon: 'CA' },
  usdchf: { label: 'USD/CHF', icon: 'CH' }, nzdusd: { label: 'NZD/USD', icon: 'NZ' },
  eurgbp: { label: 'EUR/GBP', icon: 'EG' }, eurchf: { label: 'EUR/CHF', icon: 'EC' },
  eurjpy: { label: 'EUR/JPY', icon: 'EJ' }, gbpjpy: { label: 'GBP/JPY', icon: 'GJ' },
  euraud: { label: 'EUR/AUD', icon: 'EA' },
}

// Gera meta de fator dinamicamente a partir da key
function getFactorMeta(fkey) {
  const known = FACTOR_DISPLAY[fkey]
  if (known) return { label: known.label, icon: known.icon, desc: known.label }
  return { label: fkey.toUpperCase(), icon: '📊', desc: fkey }
}

/* ── Big Gauge ────────────────────────────────────── */
function SignalGauge({ title, pUp = 50, verdict, score = 0, winReturn, flowConfirms, cumDeltaNorm, targetLabel, hasFlow = true, accuracy = 80, recentPUp = [], priceDiverges, nweUp, nweUpper, nweLower, nweAvailable, isPreview }) {
  const isBuy = pUp >= 60
  const isSell = pUp <= 40

  const signalText = isBuy ? 'ALTA' : isSell ? 'BAIXA' : 'NEUTRO'
  const signalColor = isBuy ? '#4ADE80' : isSell ? '#F87171' : '#94A3B8'

  // ── Convicção calibrada por Shrinkage ──────────────────────────────
  // P_shrunk = 50 + (P_raw - 50) × (2×acc - 1)
  // Convicção = distância do ponto neutro após calibração, normalizada 0–100%
  const acc = Math.min(Math.max(accuracy, 50), 100) // garante 50–100%
  const shrinkFactor = (2 * acc / 100) - 1           // 0 (acc=50%) … 1 (acc=100%)
  const pShrunk = 50 + (pUp - 50) * shrinkFactor
  const conviction = Math.round(Math.abs(pShrunk - 50) * 2) // 0–max(acc)%

  // Teto de convicção para este modelo: acc=80% → max=60%, acc=91% → max=82%
  // Labels são relativos ao teto — 3 faixas apenas
  //   forte:   convRatio ≥ 55%  →  P(↑) ≥ ~78% num modelo de acc=80%
  //   moderada: convRatio ≥ 25%  →  P(↑) ≥ ~62% num modelo de acc=80%
  //   fraca:   qualquer sinal acima do neutro
  const maxConviction = Math.round((acc - 50) * 2)
  const convRatio = maxConviction > 0 ? conviction / maxConviction : 0

  const convLabel = isPreview ? 'pré-mercado' :
    convRatio >= 0.55 ? 'forte' :
    convRatio >= 0.25 ? 'moderada' : 'fraca'
  const convColor = isPreview ? '#EAB308' :
    convRatio >= 0.55 ? signalColor :
    convRatio >= 0.25 ? '#C9A227' :
    '#475569'

  // ── Estabilidade (últimas 8 barras) ───────────────────────────────
  // Mede quanto o P(↑) variou e se cruzou o neutro (50)
  let stability = 'sem dados'
  let stabilityIcon = '○'
  let stabilityColor = '#334155'
  let stabilityTip = ''

  if (recentPUp.length >= 4) {
    const vals = recentPUp.slice(-8)
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length
    const std = Math.sqrt(vals.reduce((s, v) => s + (v - mean) ** 2, 0) / vals.length)

    // Cruzamento de neutro: alguma barra acima e alguma abaixo de 50
    const hasCross = vals.some(v => v > 50) && vals.some(v => v < 50)

    // Trend: P(↑) nas últimas 4 barras vs. 4 barras anteriores
    const half = Math.floor(vals.length / 2)
    const early = vals.slice(0, half).reduce((a, b) => a + b, 0) / half
    const late  = vals.slice(half).reduce((a, b) => a + b, 0) / (vals.length - half)
    const trending = Math.abs(late - early) > 6  // deslocamento > 6pp entre metades

    if (hasCross || std > 18) {
      stability = 'oscilando'
      stabilityIcon = '⟆'
      stabilityColor = '#FBBF24'
      stabilityTip = `Sinal instável — P(↑) cruzou o neutro nas últimas ${vals.length} barras (σ=${std.toFixed(1)}pp). Aguarde confirmação.`
    } else if (trending && std > 6) {
      stability = 'formando'
      stabilityIcon = '◈'
      stabilityColor = '#60A5FA'
      stabilityTip = `Sinal em formação — ${late > early ? 'ganhando' : 'perdendo'} convicção nas últimas ${vals.length} barras (σ=${std.toFixed(1)}pp).`
    } else if (std <= 6) {
      stability = 'estável'
      stabilityIcon = '▬'
      stabilityColor = '#4ADE80'
      stabilityTip = `Sinal estável — baixa variação nas últimas ${vals.length} barras (σ=${std.toFixed(1)}pp).`
    } else {
      stability = 'variando'
      stabilityIcon = '~'
      stabilityColor = '#94A3B8'
      stabilityTip = `Variação moderada (σ=${std.toFixed(1)}pp).`
    }
  }

  // ── Gauge needle ──────────────────────────────────────────────────
  const angleRad = Math.PI * (1 - pUp / 100)

  const isReturnDivergentBuy = isBuy && winReturn < 0;
  const isReturnDivergentSell = isSell && winReturn > 0;
  const isNweExhaustionDown = nweAvailable && nweUpper != null && winReturn > nweUpper;
  const isNweExhaustionUp = nweAvailable && nweLower != null && winReturn < nweLower;
  const isNweDivergentBuy = isBuy && nweUp === false;
  const isNweDivergentSell = isSell && nweUp === true;

  const hasAlert = isReturnDivergentBuy || isReturnDivergentSell;

  const alertClass = hasAlert ? (isBuy ? 'card-alert-green' : 'card-alert-red') : '';

  return (
    <div className={`gauge-container ${alertClass}`} style={{
      flex: '1 1 350px',
      display: 'flex', gap: 20, padding: 24, borderRadius: 12,
      background: 'linear-gradient(180deg, #111116 0%, #09090B 100%)',
      border: `1px solid ${signalColor}33`,
      boxShadow: `0 4px 20px -10px ${signalColor}20`,
      alignItems: 'center',
    }}>
      {/* SVG Gauge */}
      <div style={{ position: 'relative', width: 110, height: 62, flexShrink: 0 }}>
        <svg viewBox="0 0 110 62" width="110" height="62">
          <path d="M 8 58 A 48 48 0 0 1 102 58" fill="none" stroke="#1E293B" strokeWidth="6" strokeLinecap="round" />
          <path d="M 8 58 A 48 48 0 0 1 22 20" fill="none" stroke="#F8717133" strokeWidth="6" strokeLinecap="round" />
          <path d="M 88 20 A 48 48 0 0 1 102 58" fill="none" stroke="#4ADE8033" strokeWidth="6" strokeLinecap="round" />
          <line
            x1="55" y1="58"
            x2={55 + 38 * Math.cos(angleRad)}
            y2={58 - 38 * Math.sin(angleRad)}
            stroke={signalColor} strokeWidth="2" strokeLinecap="round"
          />
          <circle cx="55" cy="58" r="3" fill={signalColor} />
          <text x="6" y="56" fill="#F87171" fontSize="7" fontFamily="var(--font-mono)">↓</text>
          <text x="49" y="10" fill="#64748B" fontSize="7" fontFamily="var(--font-mono)">50%</text>
          <text x="96" y="56" fill="#4ADE80" fontSize="7" fontFamily="var(--font-mono)">↑</text>
        </svg>
      </div>

      {/* Signal text */}
      <div className="gauge-left">
        {title && <div style={{
          fontSize: 10, fontFamily: 'var(--font-serif)', color: '#C9A227', marginBottom: 6, fontStyle: 'italic'
        }}>{title}</div>}
        <div style={{
          fontSize: 9, fontFamily: 'var(--font-mono)', letterSpacing: '0.15em',
          color: '#64748B', textTransform: 'uppercase', marginBottom: 4,
        }}>sinal IRAI</div>
        <div style={{
          fontFamily: 'var(--font-serif)', fontSize: 38, lineHeight: 1,
          color: signalColor, fontWeight: 400,
        }}>{signalText}</div>

        {/* Conviction + stability row */}
        <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {/* Conviction */}
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 11, color: '#64748B',
          }}>
            convicção{' '}
            <span style={{ color: convColor, fontWeight: 600 }}>{conviction}%</span>
            <span style={{ color: '#334155', fontSize: 9 }}> ({convLabel})</span>
          </span>

          {/* Divider */}
          <span style={{ color: '#1E293B', fontSize: 11 }}>·</span>

          {/* Stability badge */}
          <span
            title={stabilityTip}
            style={{
              fontFamily: 'var(--font-mono)', fontSize: 10,
              color: stabilityColor,
              background: `${stabilityColor}18`,
              border: `1px solid ${stabilityColor}33`,
              borderRadius: 4, padding: '1px 7px',
              cursor: 'help',
              letterSpacing: '0.06em',
            }}
          >
            {stabilityIcon} {stability}
          </span>
        </div>

        {/* Raw p_up — secondary, small */}
        <div style={{ marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 9, color: '#334155' }}>
          P(↑) bruto: {pUp.toFixed(1)}% · acc modelo: {accuracy.toFixed(0)}%
        </div>
      </div>

      {/* Target return + flow */}
      <div className="gauge-right">
        <div style={{
          fontSize: 9, fontFamily: 'var(--font-mono)', letterSpacing: '0.12em',
          color: '#64748B', textTransform: 'uppercase', marginBottom: 3,
        }}>{targetLabel || 'WIN'} agora</div>
        <div style={{
          fontFamily: 'var(--font-serif)', fontSize: 28, lineHeight: 1,
          color: winReturn >= 0 ? '#4ADE80' : '#F87171',
        }}>{winReturn >= 0 ? '+' : ''}{winReturn.toFixed(2)}%</div>
        {/* D: DIVERGÊNCIA DE RETORNO */}
        <div style={{
          marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 9,
          padding: '2px 6px', borderRadius: 4, display: 'inline-block', clear: 'both', float: 'left',
          background: (isReturnDivergentBuy || isReturnDivergentSell) ? (isReturnDivergentBuy ? 'rgba(74,222,128,0.12)' : 'rgba(248,113,113,0.12)') : 'rgba(148,163,184,0.08)',
          color: (isReturnDivergentBuy || isReturnDivergentSell) ? (isReturnDivergentBuy ? '#4ADE80' : '#F87171') : '#64748B',
          border: `1px solid ${(isReturnDivergentBuy || isReturnDivergentSell) ? (isReturnDivergentBuy ? 'rgba(74,222,128,0.2)' : 'rgba(248,113,113,0.2)') : 'rgba(148,163,184,0.1)'}`,
        }}>
          {(isReturnDivergentBuy || isReturnDivergentSell) ? (isReturnDivergentBuy ? '🟢 DIVERGÊNCIA %' : '🔴 DIVERGÊNCIA %') : '✓ DIVERGÊNCIA %'}
        </div>

        {/* P: PULLBACK / NWE DIVERGENCE */}
        <div className={(isNweDivergentBuy || isNweDivergentSell) ? 'badge-blink' : ''} style={{
          marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 9,
          padding: '2px 6px', borderRadius: 4, display: 'inline-block', clear: 'both', float: 'left',
          background: (isNweDivergentBuy || isNweDivergentSell) ? (isNweDivergentBuy ? 'rgba(74,222,128,0.12)' : 'rgba(248,113,113,0.12)') : 'rgba(148,163,184,0.08)',
          color: (isNweDivergentBuy || isNweDivergentSell) ? (isNweDivergentBuy ? '#4ADE80' : '#F87171') : '#64748B',
          border: `1px solid ${(isNweDivergentBuy || isNweDivergentSell) ? (isNweDivergentBuy ? 'rgba(74,222,128,0.2)' : 'rgba(248,113,113,0.2)') : 'rgba(148,163,184,0.1)'}`,
        }}>
          {(isNweDivergentBuy || isNweDivergentSell) ? (isNweDivergentBuy ? '🟢 PULLBACK NWE' : '🔴 PULLBACK NWE') : '✓ PULLBACK NWE'}
        </div>

        {/* Z: Z-SCORE SIGNAL */}
        <div className={priceDiverges ? 'badge-blink' : ''} style={{
          marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 9,
          padding: '2px 6px', borderRadius: 4, display: 'inline-block', clear: 'both', float: 'left',
          background: priceDiverges ? (isBuy ? 'rgba(74,222,128,0.12)' : 'rgba(248,113,113,0.12)') : 'rgba(148,163,184,0.08)',
          color: priceDiverges ? (isBuy ? '#4ADE80' : '#F87171') : '#64748B',
          border: `1px solid ${priceDiverges ? (isBuy ? 'rgba(74,222,128,0.2)' : 'rgba(248,113,113,0.2)') : 'rgba(148,163,184,0.1)'}`,
        }}>
          {priceDiverges ? (isBuy ? '🟢 Z-SCORE COMPRA' : '🔴 Z-SCORE VENDA') : '✓ Z-SCORE'}
        </div>

        {/* E: EXAUSTÃO NWE */}
        <div className={(isNweExhaustionUp || isNweExhaustionDown) ? 'badge-blink' : ''} style={{
          marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 9,
          padding: '2px 6px', borderRadius: 4, display: 'inline-block', clear: 'both', float: 'left',
          background: (isNweExhaustionUp || isNweExhaustionDown) ? (isNweExhaustionUp ? 'rgba(74,222,128,0.12)' : 'rgba(248,113,113,0.12)') : 'rgba(148,163,184,0.08)',
          color: (isNweExhaustionUp || isNweExhaustionDown) ? (isNweExhaustionUp ? '#4ADE80' : '#F87171') : '#64748B',
          border: `1px solid ${(isNweExhaustionUp || isNweExhaustionDown) ? (isNweExhaustionUp ? 'rgba(74,222,128,0.4)' : 'rgba(248,113,113,0.4)') : 'rgba(148,163,184,0.1)'}`,
        }}>
          {(isNweExhaustionUp || isNweExhaustionDown) ? (isNweExhaustionUp ? '🟢 EXAUSTÃO NWE' : '🔴 EXAUSTÃO NWE') : '✓ EXAUSTÃO NWE'}
        </div>
      </div>
    </div>
  )
}

/* ── Factor signal card ──────────────────────────── */
function FactorSignal({ fkey, data }) {
  const meta = getFactorMeta(fkey)
  if (!data) return null

  const z = data.z_score || 0
  const ret = data.ret || 0

  // Para fatores invertidos: z positivo = ruim para IBOV (fator subiu mas é negativo para IBOV)
  // A contribuição já tem o sinal correto
  const contrib = data.contribution || 0
  const isFavorBuy = contrib > 0.02
  const isFavorSell = contrib < -0.02
  const isNeutral = !isFavorBuy && !isFavorSell

  const label = isFavorBuy ? 'COMPRA' : isFavorSell ? 'VENDA' : '—'
  const color = isFavorBuy ? '#4ADE80' : isFavorSell ? '#F87171' : '#475569'
  const bgColor = isFavorBuy ? 'rgba(74,222,128,0.06)' : isFavorSell ? 'rgba(248,113,113,0.06)' : 'rgba(71,85,105,0.04)'
  const borderColor = isFavorBuy ? 'rgba(74,222,128,0.15)' : isFavorSell ? 'rgba(248,113,113,0.15)' : 'rgba(71,85,105,0.1)'

  // Intensity bar width (0–100%)
  const intensity = Math.min(Math.abs(contrib) / 0.5, 1) * 100

  return (
    <div style={{
      background: bgColor, border: `1px solid ${borderColor}`,
      borderRadius: 6, padding: '14px 16px',
      display: 'flex', alignItems: 'center', gap: 12,
      transition: 'all 0.3s ease',
    }}>
      {/* Icon */}
      <div style={{ fontSize: 22, lineHeight: 1, width: 28, textAlign: 'center' }}>{meta.icon}</div>

      {/* Info */}
      <div style={{ flex: 1 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 600,
            color: '#CBD5E1',
          }}>{meta.label}</span>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 600,
            color: color, letterSpacing: '0.05em',
          }}>{label}</span>
        </div>

        {/* Intensity bar */}
        <div style={{
          marginTop: 6, height: 3, background: '#1E293B', borderRadius: 2,
          position: 'relative', overflow: 'hidden',
        }}>
          <div style={{
            position: 'absolute', top: 0, bottom: 0, left: 0,
            width: `${intensity}%`, background: color,
            borderRadius: 2, transition: 'width 0.5s ease',
          }} />
        </div>

        <div style={{
          marginTop: 4, display: 'flex', justifyContent: 'space-between',
          fontFamily: 'var(--font-mono)', fontSize: 9, color: '#475569',
        }}>
          <span>{meta.desc}</span>
          <span>{ret >= 0 ? '+' : ''}{ret.toFixed(2)}%</span>
        </div>
      </div>
    </div>
  )
}

/* ── NWE (Nadaraya-Watson Envelope) ──────────────── */
// O envelope é calculado de forma 100% causal no backend (backend/irai/nwe.py)
// e chega pronto por barra via /api/irai/series (campos nwe_*). NÃO recalcular
// no cliente — NWE_BW sobrevive apenas como label textual do badge.
const NWE_BW = 8;    // bandwidth (kernel width) — label apenas

/* ── Main App ────────────────────────────────────── */
const REFRESH_INTERVAL = 30_000 // 30 seconds (fallback polling)

// Helper: compute local time from UTC time string + offset in hours
function toLocalTime(utcTimeStr, offsetH) {
  // `!offsetH` descartaria um offset 0 legítimo (ativo sem deslocamento) — só a
  // ausência do valor deve anular.
  if (!utcTimeStr || offsetH == null) return null;
  const [hStr, mStr] = utcTimeStr.split(':');
  let h = (parseInt(hStr, 10) + offsetH + 24) % 24;
  return `${h.toString().padStart(2, '0')}:${mStr}`;
}

const padSeriesToFullDay = (series, isB3 = false, brtOffsetH = 6) => {
  if (!series || series.length === 0) return series;
  
  const lastTimeStr = series[series.length - 1].time; // ex: "18:05"
  if (!lastTimeStr) return series;

  const [hStr, mStr] = lastTimeStr.split(":");
  let currentH = parseInt(hStr, 10);
  let currentM = parseInt(mStr, 10);
  
  const padded = [...series];
  
  while (currentH < 23 || (currentH === 23 && currentM < 55)) {
    currentM += 5;
    if (currentM >= 60) {
      currentM = 0;
      currentH += 1;
    }
    if (currentH > 23) break;
    
    const h = currentH.toString().padStart(2, '0');
    const m = currentM.toString().padStart(2, '0');
    const timeTickmill = `${h}:${m}`;
    
    padded.push({
      time: timeTickmill,
      time_local: isB3 ? toLocalTime(timeTickmill, -brtOffsetH) : null
    });
  }
  return padded;
};

// URL <-> estado de navegação: ?target=WIN$N&date=2026-07-15
// Sem date -> LIVE. Sem target -> overview. Sem router (projeto não usa
// react-router-dom); só URLSearchParams + history, lido uma vez no mount e
// sincronizado por um efeito depois (ver useEffect de sync mais abaixo).
function readUrlState() {
  const params = new URLSearchParams(window.location.search)
  const target = params.get('target')
  const date = params.get('date')
  return {
    page: target ? 'detail' : 'overview',
    selectedTarget: target || 'WIN$N',
    selectedDate: date || null,
    liveMode: !date,
  }
}

export default function App() {
  const [page, setPage] = useState(() => readUrlState().page)
  const [dates, setDates] = useState([])
  const [selectedDate, setSelectedDate] = useState(() => readUrlState().selectedDate)
  const [liveMode, setLiveMode] = useState(() => readUrlState().liveMode)
  const [selectedTarget, setSelectedTarget] = useState(() => readUrlState().selectedTarget)
  const [targetsMeta, setTargetsMeta] = useState([]) // From /api/irai/targets
  const [seriesInfo, setSeriesInfo] = useState({}) // display_name, icon from series response
  const [gex, setGex] = useState(null)       // níveis de gamma walls (gex_worker, EOD D-1)
  const [showGex, setShowGex] = useState(false) // default OFF: opt-in do operador
  const [showMid, setShowMid] = useState(false) // midwalls: toggle separado (GEX/MID)
  const [series, setSeries] = useState([])
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [lastUpdate, setLastUpdate] = useState(null)
  const [rastroView, setRastroView] = useState('both') // 'v1', 'v2', 'both'

  // The effective date: LIVE = today (from backend), or manually selected
  const effectiveDate = liveMode ? (dates.length > 0 ? dates[0] : selectedDate) : selectedDate

  // Thresholds canônicos do target selecionado (divergence_config, via
  // /api/irai/targets — ver backend/api/main.py::_target_thresholds). Os
  // charts de z-score usam isto pra desenhar a linha que o sinal de verdade
  // usa, em vez de ±2 hardcoded (doc de divergência §7.1/7.2). Fallback só
  // pra antes do 1º fetch de targetsMeta completar.
  const currentTargetMeta = targetsMeta.find(t => t.target === selectedTarget)
  const pairThreshold = currentTargetMeta?.pair_threshold ?? 1.5
  const priceDivergeThreshold = currentTargetMeta?.price_diverge_threshold ?? 0.5

  // Espelha page/selectedTarget/selectedDate/liveMode na URL. pushState só na
  // transição overview<->detail (dá um "voltar" útil no navegador); demais
  // mudanças (trocar ativo, trocar data) usam replaceState pra não empilhar
  // uma entrada de histórico por swipe/seleção.
  const prevPageRef = useRef(page)
  useEffect(() => {
    const params = new URLSearchParams()
    if (page === 'detail') {
      params.set('target', selectedTarget)
      if (!liveMode && selectedDate) params.set('date', selectedDate)
    }
    const qs = params.toString()
    const newUrl = qs ? `${window.location.pathname}?${qs}` : window.location.pathname
    const currentUrl = window.location.pathname + window.location.search
    if (newUrl !== currentUrl) {
      if (page !== prevPageRef.current) {
        window.history.pushState(null, '', newUrl)
      } else {
        window.history.replaceState(null, '', newUrl)
      }
    }
    prevPageRef.current = page
  }, [page, selectedTarget, selectedDate, liveMode])

  // Botão voltar/avançar do navegador -> reaplica o estado lido da URL.
  useEffect(() => {
    function onPopState() {
      const s = readUrlState()
      setPage(s.page)
      setSelectedTarget(s.selectedTarget)
      setLiveMode(s.liveMode)
      if (s.selectedDate) setSelectedDate(s.selectedDate)
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  // Swipe Handlers
  const handleNextTarget = useCallback(() => {
    if (targetsMeta.length === 0) return;
    const currentIndex = targetsMeta.findIndex(t => t.target === selectedTarget);
    if (currentIndex < targetsMeta.length - 1) {
      setSelectedTarget(targetsMeta[currentIndex + 1].target);
    } else {
      setSelectedTarget(targetsMeta[0].target);
    }
  }, [targetsMeta, selectedTarget]);

  const handlePrevTarget = useCallback(() => {
    if (targetsMeta.length === 0) return;
    const currentIndex = targetsMeta.findIndex(t => t.target === selectedTarget);
    if (currentIndex > 0) {
      setSelectedTarget(targetsMeta[currentIndex - 1].target);
    } else {
      setSelectedTarget(targetsMeta[targetsMeta.length - 1].target);
    }
  }, [targetsMeta, selectedTarget]);

  const targetSwipeHandlers = useSwipeable({
    onSwipedLeft: handleNextTarget,
    onSwipedRight: handlePrevTarget,
    trackMouse: false
  });

  // Fetch dates + targets list once (and poll every 60s in live mode to detect new sessions)
  useEffect(() => {
    function loadDates() {
      if (FIREBASE_URL) {
        const url = FIREBASE_URL.endsWith('.json') ? FIREBASE_URL : `${FIREBASE_URL.replace(/\/$/, '')}/db.json`
        fetch(url)
          .then(r => r.json())
          .then(data => {
            const d = data?.dates?.dates || []
            setDates(d)
            if (d.length > 0) setSelectedDate(prev => prev || d[0])
          })
          .catch(e => setError(e.message))
      } else {
        fetch(`${API}/api/irai/dates`)
          .then(r => r.json())
          .then(data => {
            const d = data.dates || []
            setDates(d)
            if (d.length > 0) setSelectedDate(prev => prev || d[0])
          })
          .catch(e => setError(e.message))
      }
    }
    loadDates()
    const poll = setInterval(loadDates, 60_000)
    return () => clearInterval(poll)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    // Poll (não só mount): os thresholds canônicos (pair_threshold,
    // price_diverge_threshold) vêm daqui. Se o backend recalibrar e
    // reiniciar enquanto a aba já está aberta, um fetch só-no-mount deixaria
    // as linhas desenhadas nos charts presas no valor antigo indefinidamente
    // — mesmo cadência de `loadDates` acima (60s), o suficiente pra um valor
    // que só muda por recalibração manual (achado de revisão /codex-r do
    // commit de thresholds canônicos).
    function loadTargets() {
      if (FIREBASE_URL) {
        const url = FIREBASE_URL.endsWith('.json') ? FIREBASE_URL : `${FIREBASE_URL.replace(/\/$/, '')}/db.json`
        fetch(url)
          .then(r => r.json())
          .then(data => setTargetsMeta((data?.targets?.targets || []).filter(t => t.calibrated)))
          .catch(() => {})
      } else {
        fetch(`${API}/api/irai/targets`)
          .then(r => r.json())
          .then(data => setTargetsMeta((data.targets || []).filter(t => t.calibrated)))
          .catch(() => {})
      }
    }
    loadTargets()
    const poll = setInterval(loadTargets, 60_000)
    return () => clearInterval(poll)
  }, [])

  // Fetch series data (silent = no loading spinner on auto-refresh)
  const fetchSeriesReqRef = useRef(0)
  const fetchSeries = useCallback((date, target, silent = false) => {
    if (!date) return
    // Descarta respostas fora de ordem: se o usuário trocar de target (ex.
    // WIN$N -> WDO$N) antes da requisição anterior voltar, a resposta antiga
    // não pode sobrescrever `series`/`seriesInfo` -- senão as walls de GEX do
    // novo target (já corretamente filtradas por gex.target===selectedTarget)
    // acabam desenhadas sobre a série de preço do target anterior.
    const reqId = ++fetchSeriesReqRef.current
    if (!silent) {
      setLoading(true)
      // Clear previous data so we don't show ghost data from a previously selected target
      setSeries([])
      setSummary(null)
      setSeriesInfo({})
      setError(null)
    }
    
    if (FIREBASE_URL) {
      const url = FIREBASE_URL.endsWith('.json') ? FIREBASE_URL : `${FIREBASE_URL.replace(/\/$/, '')}/db.json`
      fetch(url)
        .then(r => r.json())
        .then(data => {
          if (reqId !== fetchSeriesReqRef.current) return
          if (data.error) { setError(data.error); setLoading(false); return }
          const safeTarget = target.replace('$', '_').replace('.', '_')
          const s = data?.series?.[safeTarget] || []
          const sum = data?.summaries?.[safeTarget] || {}
          const tMeta = data?.targets?.targets?.find(t => t.target === target) || {}
          const processed = s.map(x => ({
            ...x,
            time: x.timestamp ? x.timestamp.substring(11, 16) : '00:00',
          }))
          setSeries(padSeriesToFullDay(processed))
          setSummary(sum)
          const history_closes = data?.history?.[safeTarget] || []
          setSeriesInfo({ display_name: tMeta.display_name, icon: tMeta.icon, history_closes, accuracy: sum.accuracy })
          setLoading(false)
          setLastUpdate(new Date(data?.last_update ? data.last_update * 1000 : Date.now()))
          setError(null)
        })
        .catch(e => { if (reqId !== fetchSeriesReqRef.current) return; setError(e.message); setLoading(false) })
    } else {
      fetch(`${API}/api/irai/series?session_date=${date}&target=${encodeURIComponent(target)}&version=v2`)   // v2 = engine dinâmico (Kalman); antes pedia `both`, que o engine resolvia como V1 estático
        .then(r => r.json())
        .then(data => {
          if (reqId !== fetchSeriesReqRef.current) return
          if (data.error) { setError(data.error); setLoading(false); return }
          const isB3 = data.is_b3 || false;
          // O engine desloca a B3 para o eixo do servidor somando `brt_offset_h`,
          // que VARIA com o horário de verão (6h ou 5h) — não assuma -6h fixo aqui,
          // senão o eixo BRT erra 1h fora do DST americano (próxima virada: 01/11/2026).
          const brtOffsetH = data.brt_offset_h ?? 6;
          const processed = (data.series || []).map(s => {
            const timeDb = s.timestamp ? s.timestamp.substring(11, 16) : '00:00';
            // timeDb já é o eixo primário (relógio do servidor).
            const timeTickmill = timeDb;
            const timeBrt = isB3 ? toLocalTime(timeDb, -brtOffsetH) : null;
            return {
              ...s,
              time: timeTickmill,
              time_local: timeBrt,
            };
          })
          setSeries(padSeriesToFullDay(processed, isB3, brtOffsetH))
          setSummary(data.summary)
          setSeriesInfo({ display_name: data.display_name, icon: data.icon, history_closes: data.history_closes || [], tz_offset: isB3 ? -3 : 0, tz_label: 'BRT', accuracy: data.summary?.accuracy })
          setLoading(false)
          setLastUpdate(new Date())
          setError(null)
        })
        .catch(e => { if (reqId !== fetchSeriesReqRef.current) return; setError(e.message); setLoading(false) })
    }
  }, [])

  // GEX (gamma walls) do target — EOD D-1, gerado pelo gex_worker. Só no modo
  // local (o payload Firebase ainda não carrega GEX).
  useEffect(() => {
    if (!API) return  // modo Firebase: gex permanece null (estado inicial)
    let mounted = true
    const refreshGex = () => {
      const gexParams = new URLSearchParams({ target: selectedTarget })
      if (!liveMode && effectiveDate) gexParams.set('date', effectiveDate)
      fetch(`${API}/api/irai/gex?${gexParams.toString()}`)
        .then(r => r.json())
        .then(d => {
          if (!mounted) return
          setGex(d && d.walls && d.walls.length ? d : null)  // d.target vem do endpoint
          if (!d?.active) {
            setShowGex(false)
            setShowMid(false)
          }
        })
        .catch(() => { if (mounted) setGex(null) })
    }
    refreshGex()
    if (!liveMode) {
      return () => { mounted = false }
    }
    const timer = setInterval(refreshGex, 60_000)
    return () => {
      mounted = false
      clearInterval(timer)
    }
  }, [effectiveDate, liveMode, selectedTarget])

  // Initial load on date/target/liveMode change + polling
  useEffect(() => {
    fetchSeries(effectiveDate, selectedTarget, false)
    
    if (!liveMode) return

    let mounted = true
    let pollTimer = null
    
    // Poll every 60s for updates (both Firebase and local)
    pollTimer = setInterval(() => {
      if (mounted) fetchSeries(effectiveDate, selectedTarget, true)
    }, 60_000)
    
    return () => {
      mounted = false
      if (pollTimer) clearInterval(pollTimer)
    }
  }, [effectiveDate, selectedTarget, liveMode, fetchSeries])

  const validSeries = useMemo(() => series.filter(s => s.win_current !== undefined), [series]);
  const now = validSeries.length > 0 ? validSeries[validSeries.length - 1] : null
  const hasFlow = now && 'flow_confirms' in now

  const isOffline = error && (error.includes('Failed to fetch') || error.includes('NetworkError') || error.includes('Load failed'));

  if (error && !series.length && isOffline && page === 'overview' && !FIREBASE_URL) {
    return (
      <div style={{
        minHeight: '100vh', background: '#0F172A', color: '#E2E8F0',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: 'JetBrains Mono, monospace',
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>📡</div>
          <div style={{ fontSize: 16, color: '#F87171', marginBottom: 8 }}>
            Backend offline
          </div>
          <div style={{ fontSize: 12, color: '#64748B' }}>
            Inicie o servidor: python -m uvicorn backend.api.main:app --port 8888
          </div>
        </div>
      </div>
    )
  }

  // Os campos NWE (nwe_center_price, nwe_upper_price, nwe_lower_price, nwe_center,
  // nwe_upper, nwe_lower, nwe_slope_price, nwe_direction, nwe_available) já vêm
  // prontos e causais por barra de /api/irai/series (backend/irai/nwe.py). Aqui só
  // achatamos os pesos em weight_<fator> (consumido pelo TVKalmanWeightsChart).
  const seriesWithNWE = useMemo(() => {
    return series.map(entry => {
      const mappedEntry = { ...entry };

      const factors = entry.factors;
      if (factors) {
        Object.keys(factors).forEach(label => {
          mappedEntry[`weight_${label}`] = factors[label].weight;
        });
      }

      return mappedEntry;
    });
  }, [series]);
  const validSeriesWithNWE = useMemo(() => seriesWithNWE.filter(s => s.win_current !== undefined), [seriesWithNWE]);
  const nweNow = validSeriesWithNWE.length > 0 ? validSeriesWithNWE[validSeriesWithNWE.length - 1] : null;

  return (
    <>
      <style>{`
        @keyframes borderGlowGreen {
          0% { box-shadow: 0 0 5px rgba(74,222,128,0.2); border-color: rgba(74,222,128,0.3); }
          50% { box-shadow: 0 0 15px rgba(74,222,128,0.6), inset 0 0 10px rgba(74,222,128,0.1); border-color: rgba(74,222,128,0.8); }
          100% { box-shadow: 0 0 5px rgba(74,222,128,0.2); border-color: rgba(74,222,128,0.3); }
        }
        @keyframes borderGlowRed {
          0% { box-shadow: 0 0 5px rgba(248,113,113,0.2); border-color: rgba(248,113,113,0.3); }
          50% { box-shadow: 0 0 15px rgba(248,113,113,0.6), inset 0 0 10px rgba(248,113,113,0.1); border-color: rgba(248,113,113,0.8); }
          100% { box-shadow: 0 0 5px rgba(248,113,113,0.2); border-color: rgba(248,113,113,0.3); }
        }
        .card-alert-green { animation: borderGlowGreen 2s infinite ease-in-out; }
        .card-alert-red { animation: borderGlowRed 2s infinite ease-in-out; }

        @import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Instrument+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap');
        :root {
          --font-serif: 'Instrument Serif', Georgia, serif;
          --font-sans: 'Instrument Sans', -apple-system, sans-serif;
          --font-mono: 'JetBrains Mono', ui-monospace, monospace;
          --amber: #C9A227;
          --amber-dim: #8A6E1A;
          --bg: #09090B;
          --bg-card: #0E0E11;
          --bg-card2: #111116;
          --border: #1C1C22;
          --border-dim: #141418;
          --grid: #13131A;
        }
        * { box-sizing: border-box; }
        body { margin: 0; background: var(--bg); }
        select { background: #0E0E11; color: #A0A0B0; border: 1px solid #1C1C22;
                 padding: 6px 12px; font-family: var(--font-mono); font-size: 11px;
                 border-radius: 4px; cursor: pointer; }
        select:hover { border-color: #2C2C38; }
        @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.4; } }
        /* Mobile Layout */
        .app-container { min-height: 100vh; background: var(--bg); color: #C8C8D4; font-family: var(--font-sans); padding: 24px 32px; max-width: 1400px; margin: 0 auto; }
        .app-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
        .app-header-controls { display: flex; align-items: center; gap: 10px; }
        .gauge-container { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 24px 32px; display: flex; justify-content: space-between; align-items: center; }
        .gauge-left { flex: 1; }
        .gauge-right { text-align: right; min-width: 140px; }
        .chart-container { background: var(--bg-card); border: 1px solid var(--border); border-radius: 6px; padding: 16px 16px 8px; margin-top: 16px; }
        .factors-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; align-items: stretch; }
        
        @media (max-width: 768px) {
          .app-container { padding: 16px 12px !important; }
          .app-header { flex-direction: column; align-items: flex-start !important; gap: 16px; }
          .app-header-controls { flex-wrap: wrap; }
          .gauge-container { flex-direction: column; align-items: flex-start !important; padding: 16px !important; gap: 16px; }
          .gauge-right { text-align: left !important; min-width: 0 !important; width: 100%; display: flex; flex-direction: column; gap: 4px; }
          .chart-container { padding: 12px 8px 8px !important; }
          .factors-grid { grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 6px; }
        }
      `}</style>
      {page === 'overview' ? (
        <Overview 
          onSelectTarget={(target) => {
            setSelectedTarget(target)
            setPage('detail')
          }} 
        />
      ) : (
      <div className="app-container" {...targetSwipeHandlers}>
        {/* Header */}
        <header className="app-header">
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
            <button
              onClick={() => setPage('overview')}
              style={{
                background: 'none', border: '1px solid #334155',
                borderRadius: 6, padding: '4px 10px', cursor: 'pointer',
                fontFamily: 'var(--font-mono)', fontSize: 10, color: '#94A3B8',
                transition: 'all 0.2s',
              }}
              onMouseEnter={e => e.currentTarget.style.borderColor = '#64748B'}
              onMouseLeave={e => e.currentTarget.style.borderColor = '#334155'}
            >← PAINEL</button>
            <h1 style={{
              fontFamily: 'var(--font-serif)', fontSize: 32, fontWeight: 400,
              margin: 0, color: 'var(--amber)',
              letterSpacing: '0.04em',
            }}>
              IRAI
            </h1>
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, color: '#3A3A4A',
              letterSpacing: '0.12em', textTransform: 'uppercase',
            }}>Intraday Risk Appetite Index</span>
          </div>
          <div className="app-header-controls">
            {now && (
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 12, color: '#64748B',
              }}>{now.time} · barra {validSeries.length}</span>
            )}
            {/* WS connection dot */}
            {liveMode && (
              <div style={{
                display: 'flex', alignItems: 'center', gap: 5,
                fontFamily: 'var(--font-mono)', fontSize: 9, color: '#4ADE80',
              }}>
                <div style={{
                  width: 6, height: 6, borderRadius: '50%',
                  background: '#4ADE80',
                  boxShadow: '0 0 6px #4ADE80',
                  animation: 'pulse 2s infinite',
                }} />
                LIVE (60s)
              </div>
            )}
            <select
              value={selectedTarget}
              onChange={e => setSelectedTarget(e.target.value)}
            >
              {targetsMeta.map(t => (
                <option key={t.target} value={t.target}>
                  {t.icon} {t.display_name}
                </option>
              ))}
            </select>
            {/* LIVE button + date dropdown */}
            {!FIREBASE_URL ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
                <button
                  onClick={() => setLiveMode(true)}
                  style={{
                    fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 700,
                    letterSpacing: '0.1em', padding: '6px 11px',
                    borderRadius: '4px 0 0 4px',
                    border: `1px solid ${liveMode ? '#4ADE80' : '#334155'}`,
                    borderRight: 'none',
                    background: liveMode ? 'rgba(74,222,128,0.12)' : '#1E293B',
                    color: liveMode ? '#4ADE80' : '#475569',
                    cursor: 'pointer',
                    transition: 'all 0.2s',
                    display: 'flex', alignItems: 'center', gap: 5,
                  }}
                >
                  {liveMode && (
                    <span style={{
                      display: 'inline-block', width: 6, height: 6, borderRadius: '50%',
                      background: '#4ADE80',
                      boxShadow: '0 0 5px #4ADE80',
                      animation: 'pulse 2s infinite',
                    }} />
                  )}
                  LIVE
                </button>
                <select
                  value={liveMode ? (dates[0] || '') : (selectedDate || '')}
                  onChange={e => {
                    setLiveMode(false)
                    setSelectedDate(e.target.value)
                  }}
                  style={{
                    borderRadius: '0 4px 4px 0',
                    borderLeft: `1px solid ${liveMode ? '#4ADE8033' : '#334155'}`,
                    opacity: liveMode ? 0.5 : 1,
                  }}
                >
                  {dates.map(d => <option key={d} value={d}>{d}</option>)}
                </select>
              </div>
            ) : (
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 700,
                letterSpacing: '0.1em', padding: '6px 11px',
                borderRadius: 4,
                border: '1px solid #4ADE80',
                background: 'rgba(74,222,128,0.12)',
                color: '#4ADE80',
                display: 'flex', alignItems: 'center', gap: 5,
              }}>
                <span style={{
                  display: 'inline-block', width: 6, height: 6, borderRadius: '50%',
                  background: '#4ADE80', boxShadow: '0 0 5px #4ADE80',
                  animation: 'pulse 2s infinite',
                }} />
                LIVE
              </div>
            )}
          </div>
        </header>

        <main>
        {loading && (
          <div style={{ textAlign: 'center', padding: 60, color: '#64748B', fontFamily: 'var(--font-mono)', fontSize: 13 }}>
            carregando sessão...
          </div>
        )}

        {error && !seriesWithNWE.length && !loading && (
          <div style={{ textAlign: 'center', padding: 100, color: '#64748B', fontFamily: 'var(--font-mono)' }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>⏳</div>
            <div style={{ fontSize: 16, color: '#D4A84C', marginBottom: 8 }}>
              {isOffline ? 'Backend offline' : error}
            </div>
            <div style={{ fontSize: 12, color: '#64748B', marginTop: 12 }}>
              {liveMode 
                ? "Dica: O pregão pode estar fechado (fim de semana/feriado). Desative o LIVE 🟢 para visualizar dias anteriores."
                : "Aguardando o início do pregão ou os primeiros dados da sessão..."}
            </div>
          </div>
        )}

        {now && !loading && (
          <>
            {/* ── SIGNAL GAUGES ── */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, marginBottom: 16 }}>
              {now.p_up != null && (
                <SignalGauge
                  title="DINÂMICO (KALMAN)"
                  pUp={now.p_up}
                  verdict={now.verdict}
                  score={now.score}
                  winReturn={now.win_return}
                  flowConfirms={now.flow_confirms}
                  cumDeltaNorm={now.cum_delta_norm}
                  targetLabel={seriesInfo.display_name || selectedTarget}
                  hasFlow={hasFlow}
                  accuracy={seriesInfo.accuracy ?? 80}
                  recentPUp={series.slice(-8).map(b => b.p_up).filter(v => v != null)}
                  priceDiverges={now.price_diverges}
                  nweUp={!nweNow?.nwe_available ? undefined
                    : nweNow.nwe_direction === 'up' ? true
                    : nweNow.nwe_direction === 'down' ? false
                    : undefined /* 'flat' — sem direção, não conta como divergência */}
                  nweUpper={nweNow?.nwe_upper}
                  nweLower={nweNow?.nwe_lower}
                  nweAvailable={nweNow?.nwe_available}
                  isPreview={now.is_preview}
                />
              )}
            </div>

            {/* ── STACKED CHARTS: same X axis ── */}
            <div className="chart-container">
              {/* TOP: identificação do ativo + PAR ATIVO */}
              <div style={{ marginBottom: 4 }}>
                <div style={{
                  fontFamily: 'var(--font-serif)', fontSize: 18, color: '#D0D0DC',
                }}>
                  {seriesInfo.display_name || selectedTarget} <span style={{ fontStyle: 'italic', color: '#3A3A4A' }}>vs</span> IRAI
                </div>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 8, color: 'var(--amber-dim)', marginTop: 2,
                  letterSpacing: '0.1em', textTransform: 'uppercase',
                }}>rastro macro · fatores externos</div>
                {/* PAR ATIVO: fator de maior |β| no Kalman naquele bar + z-score do resíduo */}
                {now.pair_factor && (
                  <div style={{
                    display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 6,
                    padding: '2px 8px', borderRadius: 4, background: '#0E0E11', border: '1px solid #1E293B',
                  }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8, color: '#64748B' }}>PAR ATIVO:</span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: '#C9A227', fontWeight: 600 }}>
                      {getFactorMeta(now.pair_factor).label}
                    </span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8, color: '#64748B' }}>β:</span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: now.pair_beta > 0 ? '#4ADE80' : '#F87171' }}>
                      {now.pair_beta > 0 ? '+' : ''}{now.pair_beta?.toFixed(3)}
                    </span>
                    {now.pair_z != null && (
                      <>
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8, color: '#64748B', marginLeft: 2 }}>Z:</span>
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: now.pair_z >= 0 ? '#4ADE80' : '#F87171' }}>
                          {now.pair_z >= 0 ? '+' : ''}{now.pair_z.toFixed(2)}
                        </span>
                      </>
                    )}
                  </div>
                )}
              </div>
              <TVProbabilityChart history={seriesWithNWE} effectiveDate={effectiveDate} hideXAxis={true} />

              {/* MOVIMENTO DO ÍNDICE — NWE (Nadaraya-Watson Envelope) */}
              <div style={{ marginTop: 2, borderTop: '1px solid var(--border-dim)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0 4px' }}>
                  <div style={{
                    fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--amber-dim)',
                    letterSpacing: '0.1em', textTransform: 'uppercase',
                    display: 'flex', alignItems: 'center', gap: 8,
                  }}>
                    movimento {seriesInfo.display_name || selectedTarget}
                    {/* Toggle GEX: só aparece quando há walls; `active` = válido e
                        fresco (D-1). Envelhecido fica desabilitado com o as-of. */}
                    {gex && gex.target === selectedTarget && (
                      <button
                        disabled={!gex.active}
                        onClick={() => gex.active && setShowGex(v => !v)}
                        title={gex.active
                          ? `gamma walls do fechamento de ${gex.as_of}`
                          : `GEX de ${gex.as_of} (envelhecido/inválido — não plotável)`}
                        style={{
                          fontFamily: 'var(--font-mono)', fontSize: 8, letterSpacing: '0.08em',
                          padding: '1px 7px', borderRadius: 4, cursor: gex.active ? 'pointer' : 'not-allowed',
                          background: showGex && gex.active ? '#EAB30822' : '#0E0E11',
                          border: `1px solid ${showGex && gex.active ? '#EAB308' : '#1E293B'}`,
                          color: gex.active ? (showGex ? '#EAB308' : '#64748B') : '#3A4553',
                        }}>
                        GEX {gex.as_of?.slice(5)}
                      </button>
                    )}
                    {gex && gex.target === selectedTarget && gex.active && showGex && (
                      <button
                        onClick={() => setShowMid(v => !v)}
                        title="mid-walls (pontos médios entre strikes)"
                        style={{
                          fontFamily: 'var(--font-mono)', fontSize: 8, letterSpacing: '0.08em',
                          padding: '1px 7px', borderRadius: 4, cursor: 'pointer',
                          background: showMid ? '#9CA3AF22' : '#0E0E11',
                          border: `1px solid ${showMid ? '#9CA3AF' : '#1E293B'}`,
                          color: showMid ? '#9CA3AF' : '#64748B',
                        }}>
                        MID
                      </button>
                    )}
                    {/* F1: Flip fora da grade das 17 walls (centrada no spot,
                        não no Flip) -- sinal só informativo, direção/distância
                        AO SPOT; não recolore walls nem recalcula Gamma/Flip. */}
                    {gex && gex.target === selectedTarget && gex.active && showGex
                      && gex.flip_grid_signal?.outside_grid && (
                      <span
                        title={`Flip fora da grade de walls do fechamento de ${gex.as_of} — distância (${Math.round(gex.flip_grid_signal.distance_to_spot)} pts) medida ao spot desse fechamento, não ao preço atual`}
                        style={{
                          fontFamily: 'var(--font-mono)', fontSize: 8, letterSpacing: '0.08em',
                          padding: '1px 7px', borderRadius: 4,
                          background: '#EAB30822', border: '1px solid #EAB308', color: '#EAB308',
                        }}>
                        {gex.flip_grid_signal.direction === 'above' ? '▲' : '▼'} FLIP FORA DA GRADE
                        <span style={{ fontSize: 8, color: '#EAB308AA', marginLeft: 4, fontWeight: 400 }}>
                          ({gex.flip_grid_signal.direction === 'above' ? '+' : '-'}
                          {Math.round(gex.flip_grid_signal.distance_to_spot)})
                        </span>
                      </span>
                    )}
                  </div>
                  <div style={{
                    fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 600,
                    color: !nweNow?.nwe_available ? '#64748B'
                      : nweNow.nwe_direction === 'up' ? '#4ADE80'
                      : nweNow.nwe_direction === 'down' ? '#F87171'
                      : '#9CA3AF' /* 'flat' */,
                  }}>
                    {!nweNow?.nwe_available ? '◌ NWE —'
                      : nweNow.nwe_direction === 'up' ? '▲ NWE ALTA'
                      : nweNow.nwe_direction === 'down' ? '▼ NWE BAIXA'
                      : '● NWE FLAT'}
                    <span style={{ fontSize: 9, color: '#475569', marginLeft: 6, fontWeight: 400 }}>
                      {`bw=${NWE_BW}`} · {nweNow?.nwe_center?.toFixed(3)}%
                    </span>
                  </div>
                </div>
                <TVNweChart
                  history={seriesWithNWE}
                  effectiveDate={effectiveDate}
                  hideXAxis={false}
                  walls={(gex?.target === selectedTarget && gex?.walls) || []}
                  showGex={showGex && !!gex?.active && gex?.target === selectedTarget}
                  showMidWalls={showGex && showMid && !!gex?.active && gex?.target === selectedTarget}
                />
              </div>

              {/* BOTTOM: Divergence Z-Score */}
              <div style={{ marginTop: 2, borderTop: '1px solid var(--border-dim)', display: 'flex', flexDirection: 'column', gap: 16 }}>
                {/* Z-SCORE DINÂMICO (PAIR SPREAD) — primeiro chart migrado p/ lightweight-charts */}
                {seriesWithNWE.some(b => b.pair_z != null) && (
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0 4px' }}>
                      <div style={{
                        fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--amber-dim)',
                        letterSpacing: '0.1em', textTransform: 'uppercase',
                      }}>
                        z-score dinâmico (pair spread)
                      </div>
                      {now.pair_z != null && (
                        /* Consome `pair_signal` do backend (uma fonte da verdade).
                           Antes esta regra era re-derivada aqui de pair_z+pair_beta,
                           com o mesmo bug de inversão em β>0 e o threshold 1.5
                           hardcoded — que ignorava o pair_threshold do divergence_config. */
                        <div style={{
                          fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 600,
                          color: now.pair_signal === 'buy' ? '#4ADE80' : now.pair_signal === 'sell' ? '#F87171' : '#64748B',
                        }}>
                          {now.pair_signal === 'buy' ? '🟢 COMPRA' : now.pair_signal === 'sell' ? '🔴 VENDA' : '✓ NEUTRO'}
                          <span style={{ fontSize: 9, color: '#475569', marginLeft: 6, fontWeight: 400 }}>
                            z={now.pair_z >= 0 ? '+' : ''}{now.pair_z.toFixed(2)}
                          </span>
                        </div>
                      )}
                    </div>
                    <TVPairwiseZScoreChart history={seriesWithNWE} effectiveDate={effectiveDate} hideXAxis={false} threshold={pairThreshold} />
                  </div>
                )}
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0 4px' }}>
                    <div style={{
                      fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--amber-dim)',
                      letterSpacing: '0.1em', textTransform: 'uppercase',
                    }}>
                      z-score dinâmico (divergência preço)
                    </div>
                    <div style={{
                      fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 600,
                      /* Consome price_diverge_dir do backend (thresholds canônicos) —
                         antes re-derivava a direção aqui via `p_up > 55`, um 2º
                         literal do mesmo threshold que o engine já decide. */
                      color: now.price_diverge_dir === 'buy' ? '#4ADE80' : now.price_diverge_dir === 'sell' ? '#F87171' : '#64748B',
                    }}>
                      {now.price_diverge_dir === 'buy' ? '🟢 COMPRA' : now.price_diverge_dir === 'sell' ? '🔴 VENDA' : '✓ ALINHADO'}
                      {/* != null (não >=0, que coage null->0 e mostraria "z=+" sem
                          número — achado de revisão /codex-r): price_diverge_z fica
                          null em modo preview/pré-mercado (engine.py retorna antes
                          do loop principal), não é "zero calculado". */}
                      {now.price_diverge_z != null && (
                        <span style={{ fontSize: 9, color: '#475569', marginLeft: 6, fontWeight: 400 }}>
                          z={now.price_diverge_z >= 0 ? '+' : ''}{now.price_diverge_z.toFixed(2)}
                        </span>
                      )}
                    </div>
                  </div>
                  <TVPriceDivergeZScoreChart history={seriesWithNWE} effectiveDate={effectiveDate} hideXAxis={false} threshold={priceDivergeThreshold} />
                </div>

                {/* BOTTOM: Dynamic Weights (Kalman) */}
                {now.factors && Object.keys(now.factors).length > 0 && (
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0 4px' }}>
                      <div style={{
                        fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--amber-dim)',
                        letterSpacing: '0.1em', textTransform: 'uppercase',
                      }}>
                        pesos dinâmicos (kalman filter)
                      </div>
                    </div>
                    <TVKalmanWeightsChart
                      history={seriesWithNWE}
                      factorKeys={Object.keys(now.factors_v2 || now.factors || {})}
                      effectiveDate={effectiveDate}
                      hideXAxis={false}
                    />
                  </div>
                )}
              </div>
            </div>

            {/* ── COMPACT FACTOR ROW ── */}
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 8, color: 'var(--amber-dim)', letterSpacing: '0.1em', textTransform: 'uppercase', marginTop: 16, marginBottom: 6 }}>fatores</div>
            <div className="factors-grid">
              {Object.entries(now.factors_v2 || now.factors_v1 || now.factors || {}).map(([key, data]) => {
                if (!data) return null
                const meta = getFactorMeta(key)
                const contrib = data.contribution || 0
                const isFavorBuy = contrib > 0.02
                const isFavorSell = contrib < -0.02
                const color = isFavorBuy ? '#4ADE80' : isFavorSell ? '#F87171' : '#475569'
                const label = isFavorBuy ? 'COMPRA' : isFavorSell ? 'VENDA' : '—'
                const ret = data.ret || 0
                const intensity = Math.min(Math.abs(contrib) / 0.5, 1) * 100

                return (
                  <div key={key} style={{
                    background: isFavorBuy ? 'rgba(74,222,128,0.04)' : isFavorSell ? 'rgba(248,113,113,0.04)' : '#0E0E11',
                    border: `1px solid ${isFavorBuy ? 'rgba(74,222,128,0.14)' : isFavorSell ? 'rgba(248,113,113,0.14)' : '#1C1C22'}`,
                    borderRadius: 4, padding: '8px 10px',
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                      <span style={{ fontSize: 16 }}>{meta.icon}</span>
                      <span style={{
                        fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600, color,
                      }}>{label}</span>
                    </div>
                    <div style={{
                      fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600, color: '#CBD5E1',
                    }}>{meta.label}</div>
                    <div style={{
                      marginTop: 4, height: 2, background: '#1E293B', borderRadius: 1,
                      position: 'relative', overflow: 'hidden',
                    }}>
                      <div style={{
                        position: 'absolute', top: 0, bottom: 0, left: 0,
                        width: `${intensity}%`, background: color,
                        borderRadius: 1, transition: 'width 0.5s ease',
                      }} />
                    </div>
                    <div style={{
                      fontFamily: 'var(--font-mono)', fontSize: 8, color: '#475569', marginTop: 3,
                    }}>{ret >= 0 ? '+' : ''}{ret.toFixed(2)}%</div>
                  </div>
                )
              })}
              <div style={{
                background: now.score > 0 ? 'rgba(74,222,128,0.05)' : now.score < 0 ? 'rgba(248,113,113,0.05)' : '#0E0E11',
                border: `1px solid ${now.score > 0 ? 'rgba(74,222,128,0.15)' : now.score < 0 ? 'rgba(248,113,113,0.15)' : '#1C1C22'}`,
                borderRadius: 4, padding: '8px 14px', textAlign: 'center',
              }}>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 8, color: '#64748B',
                  letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 4,
                }}>score</div>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 600,
                  color: (now.score_v1 || now.score || 0) > 0 ? '#4ADE80' : (now.score_v1 || now.score || 0) < 0 ? '#F87171' : '#94A3B8',
                }}>{(now.score_v1 || now.score || 0) >= 0 ? '+' : ''}{(now.score_v1 || now.score || 0).toFixed(2)}</div>
              </div>
            </div>

            {/* ── FOOTER ── */}
            <div style={{
              marginTop: 16, paddingTop: 12, borderTop: '1px solid #141418',
              fontFamily: 'var(--font-mono)', fontSize: 10, color: '#2A2A36',
              display: 'flex', justifyContent: 'space-between',
            }}>
              <span>IRAI · {Object.keys(now.factors_v2 || now.factors_v1 || now.factors || {}).length} fatores cross-asset</span>
              <span>
                sessão {effectiveDate} ·
                {seriesInfo.display_name || selectedTarget} {now.win_open?.toFixed(0)} → {now.win_current?.toFixed(0)}
              </span>
            </div>
          </>
        )}
        </main>
      </div>
      )}
    </>
  )
}
