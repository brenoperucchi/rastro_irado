import { useState, useEffect } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceArea, ReferenceLine, Area, ComposedChart,
  ResponsiveContainer
} from 'recharts'

const API = 'http://localhost:8888'

const FACTOR_META = {
  dol:   { label: 'DÓLAR', icon: '💵', desc: 'Câmbio BRL/USD', invertido: true },
  di:    { label: 'JUROS', icon: '📈', desc: 'DI Futuro BR', invertido: true },
  vix:   { label: 'VIX',   icon: '😰', desc: 'VIX — Volatilidade', invertido: true },
  dxy:   { label: 'DXY',   icon: '🌐', desc: 'DXY — Dólar global', invertido: true },
  brent: { label: 'PETRÓLEO', icon: '🛢️', desc: 'Brent Crude', invertido: false },
}

function barToTime(barIdx) {
  const totalMinutes = 10 * 60 + barIdx * 5
  const h = Math.floor(totalMinutes / 60)
  const m = totalMinutes % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

/* ── Big Gauge ────────────────────────────────────── */
function SignalGauge({ pUp, verdict, score, winReturn, flowConfirms, cumDeltaNorm }) {
  const isBuy = pUp >= 60
  const isSell = pUp <= 40

  const signalText = isBuy ? 'COMPRA' : isSell ? 'VENDA' : 'NEUTRO'
  const signalColor = isBuy ? '#4ADE80' : isSell ? '#F87171' : '#94A3B8'
  const signalBg = isBuy ? 'rgba(74,222,128,0.08)' : isSell ? 'rgba(248,113,113,0.08)' : 'rgba(148,163,184,0.05)'

  const confidence = Math.abs(pUp - 50) * 2
  const confLabel = confidence > 50 ? 'FORTE' : confidence > 25 ? 'moderado' : 'fraco'

  // Gauge: arc goes left(180°) → top(90°) → right(0°) in math coords
  // 0% = left, 50% = top, 100% = right
  const angleRad = Math.PI * (1 - pUp / 100)

  return (
    <div style={{
      background: signalBg,
      border: `1px solid ${signalColor}22`,
      borderRadius: 8, padding: '14px 28px',
      display: 'flex', alignItems: 'center', gap: 28,
    }}>
      {/* SVG Gauge — compact */}
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
          <text x="6" y="56" fill="#F87171" fontSize="7" fontFamily="var(--font-mono)">0%</text>
          <text x="51" y="10" fill="#64748B" fontSize="7" fontFamily="var(--font-mono)">50%</text>
          <text x="93" y="56" fill="#4ADE80" fontSize="7" fontFamily="var(--font-mono)">100</text>
        </svg>
      </div>

      {/* Signal text */}
      <div style={{ flex: 1 }}>
        <div style={{
          fontSize: 9, fontFamily: 'var(--font-mono)', letterSpacing: '0.15em',
          color: '#64748B', textTransform: 'uppercase', marginBottom: 4,
        }}>sinal IRAI</div>
        <div style={{
          fontFamily: 'var(--font-serif)', fontSize: 38, lineHeight: 1,
          color: signalColor, fontWeight: 400,
        }}>{signalText}</div>
        <div style={{
          marginTop: 5, fontFamily: 'var(--font-mono)', fontSize: 11,
          color: '#94A3B8',
        }}>
          {pUp.toFixed(1)}% chance de alta · confiança <span style={{ color: signalColor }}>{confLabel}</span>
        </div>
      </div>

      {/* WIN return + flow */}
      <div style={{ textAlign: 'right', minWidth: 140 }}>
        <div style={{
          fontSize: 9, fontFamily: 'var(--font-mono)', letterSpacing: '0.12em',
          color: '#64748B', textTransform: 'uppercase', marginBottom: 3,
        }}>WIN agora</div>
        <div style={{
          fontFamily: 'var(--font-serif)', fontSize: 28, lineHeight: 1,
          color: winReturn >= 0 ? '#4ADE80' : '#F87171',
        }}>{winReturn >= 0 ? '+' : ''}{winReturn.toFixed(2)}%</div>
        <div style={{
          marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 10,
          padding: '3px 8px', borderRadius: 4, display: 'inline-block',
          background: flowConfirms === true ? 'rgba(74,222,128,0.12)'
                    : flowConfirms === false ? 'rgba(251,191,36,0.12)'
                    : 'rgba(148,163,184,0.08)',
          color: flowConfirms === true ? '#4ADE80'
               : flowConfirms === false ? '#FBBF24'
               : '#64748B',
          border: `1px solid ${flowConfirms === true ? 'rgba(74,222,128,0.2)'
                             : flowConfirms === false ? 'rgba(251,191,36,0.2)'
                             : 'rgba(148,163,184,0.1)'}`,
        }}>
          {flowConfirms === true ? '✓ FLUXO CONFIRMA'
           : flowConfirms === false ? '⚠ FLUXO DIVERGE'
           : '— fluxo neutro'}
        </div>
      </div>
    </div>
  )
}

/* ── Factor signal card ──────────────────────────── */
function FactorSignal({ fkey, data }) {
  const meta = FACTOR_META[fkey]
  if (!meta || !data) return null

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

/* ── Custom tooltip ──────────────────────────────── */
function CustomTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null
  const pUp = payload.find(p => p.dataKey === 'p_up')?.value
  const winRet = payload.find(p => p.dataKey === 'win_return')?.value
  const isBuy = pUp >= 60
  const isSell = pUp <= 40
  return (
    <div style={{
      background: '#0F172A', border: '1px solid #1E293B', borderRadius: 4,
      padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 11,
    }}>
      <div style={{ color: '#94A3B8', marginBottom: 6 }}>{label}</div>
      <div style={{ color: isBuy ? '#4ADE80' : isSell ? '#F87171' : '#CBD5E1', fontWeight: 600 }}>
        P(↑) = {pUp?.toFixed(1)}%
        <span style={{ marginLeft: 8, fontSize: 10, fontWeight: 400 }}>
          {isBuy ? '▲ COMPRA' : isSell ? '▼ VENDA' : '— NEUTRO'}
        </span>
      </div>
      {winRet != null && (
        <div style={{ color: winRet >= 0 ? '#4ADE80' : '#F87171', marginTop: 2 }}>
          WIN: {winRet >= 0 ? '+' : ''}{winRet.toFixed(3)}%
        </div>
      )}
    </div>
  )
}

/* ── Main App ────────────────────────────────────── */
const REFRESH_INTERVAL = 30_000 // 30 seconds

export default function App() {
  const [dates, setDates] = useState([])
  const [selectedDate, setSelectedDate] = useState(null)
  const [series, setSeries] = useState([])
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [lastUpdate, setLastUpdate] = useState(null)
  const [nextRefresh, setNextRefresh] = useState(REFRESH_INTERVAL / 1000)

  // Fetch dates once
  useEffect(() => {
    fetch(`${API}/api/irai/dates`)
      .then(r => r.json())
      .then(data => {
        setDates(data.dates || [])
        if (data.dates?.length > 0) setSelectedDate(data.dates[0])
      })
      .catch(e => setError(e.message))
  }, [])

  // Fetch series data (silent = no loading spinner on auto-refresh)
  const fetchSeries = (date, silent = false) => {
    if (!date) return
    if (!silent) setLoading(true)
    fetch(`${API}/api/irai/series?session_date=${date}`)
      .then(r => r.json())
      .then(data => {
        if (data.error) { setError(data.error); setLoading(false); return }
        const processed = (data.series || []).map(s => ({
          ...s,
          time: barToTime(s.bar_idx),
        }))
        setSeries(processed)
        setSummary(data.summary)
        setLoading(false)
        setLastUpdate(new Date())
        setError(null)
      })
      .catch(e => { setError(e.message); setLoading(false) })
  }

  // Initial load on date change
  useEffect(() => {
    fetchSeries(selectedDate, false)
  }, [selectedDate])

  // Auto-refresh polling
  useEffect(() => {
    if (!selectedDate) return
    const interval = setInterval(() => {
      fetchSeries(selectedDate, true)
    }, REFRESH_INTERVAL)
    return () => clearInterval(interval)
  }, [selectedDate])

  // Countdown timer
  useEffect(() => {
    const tick = setInterval(() => {
      if (lastUpdate) {
        const elapsed = (Date.now() - lastUpdate.getTime()) / 1000
        setNextRefresh(Math.max(0, Math.round(REFRESH_INTERVAL / 1000 - elapsed)))
      }
    }, 1000)
    return () => clearInterval(tick)
  }, [lastUpdate])

  const now = series.length > 0 ? series[series.length - 1] : null

  if (error && !series.length) {
    return (
      <div style={{
        minHeight: '100vh', background: '#0F172A', color: '#E2E8F0',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: 'JetBrains Mono, monospace',
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>📡</div>
          <div style={{ fontSize: 16, color: '#F87171', marginBottom: 8 }}>Backend offline</div>
          <div style={{ fontSize: 12, color: '#64748B' }}>Inicie o servidor: python -m uvicorn backend.api.main:app --port 8888</div>
        </div>
      </div>
    )
  }

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Instrument+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap');
        :root {
          --font-serif: 'Instrument Serif', Georgia, serif;
          --font-sans: 'Instrument Sans', -apple-system, sans-serif;
          --font-mono: 'JetBrains Mono', ui-monospace, monospace;
        }
        * { box-sizing: border-box; }
        body { margin: 0; background: #0F172A; }
        select { background: #1E293B; color: #CBD5E1; border: 1px solid #334155;
                 padding: 6px 12px; font-family: var(--font-mono); font-size: 11px;
                 border-radius: 4px; cursor: pointer; }
        select:hover { border-color: #475569; }
      `}</style>

      <div style={{
        minHeight: '100vh', background: '#0F172A', color: '#E2E8F0',
        fontFamily: 'var(--font-sans)', padding: '24px 32px',
        maxWidth: 1400, margin: '0 auto',
      }}>
        {/* Header */}
        <header style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          marginBottom: 24,
        }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 16 }}>
            <h1 style={{
              fontFamily: 'var(--font-serif)', fontSize: 32, fontWeight: 400,
              margin: 0, color: '#F1F5F9',
            }}>
              IRAI
            </h1>
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, color: '#475569',
              letterSpacing: '0.1em', textTransform: 'uppercase',
            }}>Intraday Risk Appetite Index · B3</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {now && (
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 12, color: '#64748B',
              }}>{now.time} · barra {series.length}/{96}</span>
            )}
            {/* Live pulse + countdown */}
            <div style={{
              display: 'flex', alignItems: 'center', gap: 6,
              fontFamily: 'var(--font-mono)', fontSize: 10, color: '#475569',
            }}>
              <div style={{
                width: 6, height: 6, borderRadius: '50%',
                background: nextRefresh <= 3 ? '#4ADE80' : '#334155',
                boxShadow: nextRefresh <= 3 ? '0 0 6px #4ADE80' : 'none',
                transition: 'all 0.3s ease',
              }} />
              <span>{nextRefresh}s</span>
            </div>
            <select value={selectedDate || ''} onChange={e => setSelectedDate(e.target.value)}>
              {dates.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
        </header>

        {loading && (
          <div style={{ textAlign: 'center', padding: 60, color: '#64748B', fontFamily: 'var(--font-mono)', fontSize: 13 }}>
            carregando sessão...
          </div>
        )}

        {now && !loading && (
          <>
            {/* ── SIGNAL GAUGE ── */}
            <SignalGauge
              pUp={now.p_up}
              verdict={now.verdict}
              score={now.score}
              winReturn={now.win_return}
              flowConfirms={now.flow_confirms}
              cumDeltaNorm={now.cum_delta_norm}
            />

            {/* ── STACKED CHARTS: same X axis ── */}
            <div style={{
              background: '#0F172A', border: '1px solid #1E293B',
              borderRadius: 8, padding: '16px 16px 8px', marginTop: 16,
            }}>
              {/* TOP: WIN vs IRAI */}
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <div>
                  <div style={{
                    fontFamily: 'var(--font-serif)', fontSize: 18, color: '#E2E8F0',
                  }}>
                    WIN <span style={{ fontStyle: 'italic', color: '#64748B' }}>vs</span> IRAI
                  </div>
                  <div style={{
                    fontFamily: 'var(--font-mono)', fontSize: 8, color: '#475569', marginTop: 2,
                  }}>rastro macro · fatores externos</div>
                </div>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <div style={{ width: 12, height: 2, background: '#E2E8F0' }} />
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8, color: '#64748B' }}>WIN</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <div style={{ width: 12, height: 2, background: '#D4A84C', borderTop: '1px dashed #D4A84C' }} />
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8, color: '#64748B' }}>P(↑)</span>
                  </div>
                </div>
              </div>
              <ResponsiveContainer width="100%" height={260}>
                <ComposedChart data={series} margin={{ top: 10, right: 45, left: 5, bottom: 0 }}>
                  <defs>
                    <linearGradient id="winFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#E2E8F0" stopOpacity={0.08} />
                      <stop offset="100%" stopColor="#E2E8F0" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="buyZone" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#4ADE80" stopOpacity={0.06} />
                      <stop offset="100%" stopColor="#4ADE80" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="sellZone" x1="0" y1="1" x2="0" y2="0">
                      <stop offset="0%" stopColor="#F87171" stopOpacity={0.06} />
                      <stop offset="100%" stopColor="#F87171" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="#1E293B" vertical={false} />
                  <XAxis dataKey="time" tick={false} stroke="#1E293B" />
                  <YAxis
                    yAxisId="win" orientation="left" width={45}
                    tick={{ fill: '#475569', fontSize: 8, fontFamily: 'JetBrains Mono' }}
                    stroke="#1E293B" tickFormatter={v => `${Number(v).toFixed(1)}%`}
                  />
                  <YAxis
                    yAxisId="pup" orientation="right" width={35} domain={[0, 100]}
                    ticks={[0, 25, 50, 75, 100]}
                    tick={{ fill: '#475569', fontSize: 8, fontFamily: 'JetBrains Mono' }}
                    stroke="#1E293B" tickFormatter={v => `${v}%`}
                  />
                  <ReferenceArea yAxisId="pup" y1={60} y2={100} fill="url(#buyZone)" />
                  <ReferenceArea yAxisId="pup" y1={0} y2={40} fill="url(#sellZone)" />
                  <ReferenceLine yAxisId="pup" y={50} stroke="#334155" strokeDasharray="4 4" />
                  <ReferenceLine yAxisId="win" y={0} stroke="#334155" strokeDasharray="2 2" />
                  <Tooltip content={<CustomTooltip />} />
                  <Area yAxisId="win" type="monotone" dataKey="win_return" stroke="none" fill="url(#winFill)" isAnimationActive={false} />
                  <Line yAxisId="win" type="monotone" dataKey="win_return" stroke="#E2E8F0" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                  <Line yAxisId="pup" type="monotone" dataKey="p_up" stroke="#D4A84C" strokeWidth={2} dot={false} strokeDasharray="6 3" isAnimationActive={false} />
                </ComposedChart>
              </ResponsiveContainer>

              {/* BOTTOM: Cumulative Delta — same X axis alignment */}
              <div style={{ marginTop: 2, borderTop: '1px solid #1E293B33' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0 4px' }}>
                  <div style={{
                    fontFamily: 'var(--font-mono)', fontSize: 9, color: '#475569',
                    letterSpacing: '0.08em', textTransform: 'uppercase',
                  }}>
                    fluxo delta · pressão local
                  </div>
                  <div style={{
                    fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 600,
                    color: now.cum_delta >= 0 ? '#4ADE80' : '#F87171',
                  }}>
                    {now.cum_delta >= 0 ? '▲' : '▼'} {(now.cum_delta / 1000).toFixed(0)}k
                    <span style={{ fontSize: 9, color: '#475569', marginLeft: 6, fontWeight: 400 }}>
                      norm {now.cum_delta_norm >= 0 ? '+' : ''}{now.cum_delta_norm?.toFixed(2)}
                    </span>
                  </div>
                </div>
                <ResponsiveContainer width="100%" height={120}>
                  <ComposedChart data={series} margin={{ top: 4, right: 45, left: 5, bottom: 0 }}>
                    <defs>
                      <linearGradient id="deltaPos" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#4ADE80" stopOpacity={0.25} />
                        <stop offset="100%" stopColor="#4ADE80" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="#1E293B" vertical={false} />
                    <XAxis
                      dataKey="time"
                      tick={{ fill: '#475569', fontSize: 8, fontFamily: 'JetBrains Mono' }}
                      stroke="#1E293B" interval={11}
                    />
                    <YAxis
                      yAxisId="delta" orientation="left" width={45}
                      tick={{ fill: '#475569', fontSize: 8, fontFamily: 'JetBrains Mono' }}
                      stroke="#1E293B"
                      tickFormatter={v => `${(v / 1000).toFixed(0)}k`}
                    />
                    <YAxis yAxisId="spacer" orientation="right" width={35} tick={false} stroke="transparent" />
                    <ReferenceLine yAxisId="delta" y={0} stroke="#475569" strokeWidth={1} strokeDasharray="4 4" />
                    <Tooltip
                      formatter={(v) => [`${(v / 1000).toFixed(1)}k`, 'Delta']}
                      contentStyle={{ background: '#0F172A', border: '1px solid #1E293B', borderRadius: 4, fontFamily: 'JetBrains Mono', fontSize: 11 }}
                      labelStyle={{ color: '#94A3B8' }}
                    />
                    <Area
                      yAxisId="delta" type="monotone" dataKey="cum_delta" stroke="none"
                      fill="url(#deltaPos)" isAnimationActive={false}
                      baseValue={0}
                    />
                    <Line
                      yAxisId="delta" type="monotone" dataKey="cum_delta" dot={false}
                      stroke={now.cum_delta >= 0 ? '#4ADE80' : '#F87171'}
                      strokeWidth={1.5} isAnimationActive={false}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* ── COMPACT FACTOR ROW ── */}
            <div style={{
              display: 'grid', gridTemplateColumns: 'repeat(5, 1fr) auto',
              gap: 8, marginTop: 12, alignItems: 'center',
            }}>
              {Object.entries(FACTOR_META).map(([key]) => {
                const data = now.factors?.[key]
                if (!data) return null
                const meta = FACTOR_META[key]
                const contrib = data.contribution || 0
                const isFavorBuy = contrib > 0.02
                const isFavorSell = contrib < -0.02
                const color = isFavorBuy ? '#4ADE80' : isFavorSell ? '#F87171' : '#475569'
                const label = isFavorBuy ? 'COMPRA' : isFavorSell ? 'VENDA' : '—'
                const ret = data.ret || 0
                const intensity = Math.min(Math.abs(contrib) / 0.5, 1) * 100

                return (
                  <div key={key} style={{
                    background: isFavorBuy ? 'rgba(74,222,128,0.05)' : isFavorSell ? 'rgba(248,113,113,0.05)' : 'rgba(71,85,105,0.03)',
                    border: `1px solid ${isFavorBuy ? 'rgba(74,222,128,0.12)' : isFavorSell ? 'rgba(248,113,113,0.12)' : 'rgba(71,85,105,0.08)'}`,
                    borderRadius: 6, padding: '10px 12px',
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
                background: now.score > 0 ? 'rgba(74,222,128,0.05)' : now.score < 0 ? 'rgba(248,113,113,0.05)' : 'transparent',
                border: '1px solid #1E293B', borderRadius: 6,
                padding: '10px 14px', textAlign: 'center',
              }}>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 8, color: '#64748B',
                  letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 4,
                }}>score</div>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 600,
                  color: now.score > 0 ? '#4ADE80' : now.score < 0 ? '#F87171' : '#94A3B8',
                }}>{now.score >= 0 ? '+' : ''}{now.score.toFixed(2)}</div>
              </div>
            </div>

            {/* ── FOOTER ── */}
            <div style={{
              marginTop: 16, paddingTop: 12, borderTop: '1px solid #1E293B',
              fontFamily: 'var(--font-mono)', fontSize: 10, color: '#334155',
              display: 'flex', justifyContent: 'space-between',
            }}>
              <span>R²=0.46 · α=1.42 · 67.5% acurácia direcional · 5 fatores cross-asset</span>
              <span>
                sessão {selectedDate} ·
                WIN {now.win_open?.toFixed(0)} → {now.win_current?.toFixed(0)}
              </span>
            </div>
          </>
        )}
      </div>
    </>
  )
}
