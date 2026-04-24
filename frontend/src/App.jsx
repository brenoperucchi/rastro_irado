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
  vix:   { label: 'MEDO',  icon: '😰', desc: 'VIX — Volatilidade', invertido: true },
  dxy:   { label: 'USD',   icon: '🌐', desc: 'DXY — Dólar global', invertido: true },
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
  const isNeutral = !isBuy && !isSell

  const signalText = isBuy ? 'COMPRA' : isSell ? 'VENDA' : 'NEUTRO'
  const signalColor = isBuy ? '#4ADE80' : isSell ? '#F87171' : '#94A3B8'
  const signalBg = isBuy ? 'rgba(74,222,128,0.08)' : isSell ? 'rgba(248,113,113,0.08)' : 'rgba(148,163,184,0.05)'

  // Confidence: how far from 50%
  const confidence = Math.abs(pUp - 50) * 2 // 0-100
  const confLabel = confidence > 50 ? 'FORTE' : confidence > 25 ? 'moderado' : 'fraco'

  // Gauge arc angle
  const angle = ((pUp / 100) * 180) - 90 // -90 to +90

  return (
    <div style={{
      background: signalBg,
      border: `1px solid ${signalColor}22`,
      borderRadius: 8, padding: '28px 36px',
      display: 'flex', alignItems: 'center', gap: 40,
    }}>
      {/* SVG Gauge */}
      <div style={{ position: 'relative', width: 160, height: 90, flexShrink: 0 }}>
        <svg viewBox="0 0 160 90" width="160" height="90">
          {/* Background arc */}
          <path d="M 10 85 A 70 70 0 0 1 150 85" fill="none" stroke="#1E293B" strokeWidth="8" strokeLinecap="round" />
          {/* Red zone 0-35% */}
          <path d="M 10 85 A 70 70 0 0 1 30.5 28.5" fill="none" stroke="#F8717133" strokeWidth="8" strokeLinecap="round" />
          {/* Green zone 65-100% */}
          <path d="M 129.5 28.5 A 70 70 0 0 1 150 85" fill="none" stroke="#4ADE8033" strokeWidth="8" strokeLinecap="round" />
          {/* Needle */}
          <line
            x1="80" y1="85"
            x2={80 + 55 * Math.cos(angle * Math.PI / 180)}
            y2={85 - 55 * Math.sin(angle * Math.PI / 180)}
            stroke={signalColor} strokeWidth="2.5" strokeLinecap="round"
          />
          <circle cx="80" cy="85" r="4" fill={signalColor} />
          {/* Labels */}
          <text x="12" y="82" fill="#F87171" fontSize="9" fontFamily="var(--font-mono)">0%</text>
          <text x="74" y="18" fill="#64748B" fontSize="8" fontFamily="var(--font-mono)">50%</text>
          <text x="138" y="82" fill="#4ADE80" fontSize="9" fontFamily="var(--font-mono)">100%</text>
        </svg>
      </div>

      {/* Signal text */}
      <div style={{ flex: 1 }}>
        <div style={{
          fontSize: 11, fontFamily: 'var(--font-mono)', letterSpacing: '0.15em',
          color: '#64748B', textTransform: 'uppercase', marginBottom: 6,
        }}>sinal IRAI</div>
        <div style={{
          fontFamily: 'var(--font-serif)', fontSize: 52, lineHeight: 1,
          color: signalColor, fontWeight: 400,
        }}>{signalText}</div>
        <div style={{
          marginTop: 8, fontFamily: 'var(--font-mono)', fontSize: 13,
          color: '#94A3B8',
        }}>
          {pUp.toFixed(1)}% chance de alta · confiança <span style={{ color: signalColor }}>{confLabel}</span>
        </div>
      </div>

      {/* WIN return + flow */}
      <div style={{ textAlign: 'right', minWidth: 150 }}>
        <div style={{
          fontSize: 10, fontFamily: 'var(--font-mono)', letterSpacing: '0.12em',
          color: '#64748B', textTransform: 'uppercase', marginBottom: 4,
        }}>WIN agora</div>
        <div style={{
          fontFamily: 'var(--font-serif)', fontSize: 36, lineHeight: 1,
          color: winReturn >= 0 ? '#4ADE80' : '#F87171',
        }}>{winReturn >= 0 ? '+' : ''}{winReturn.toFixed(2)}%</div>
        <div style={{
          marginTop: 8, fontFamily: 'var(--font-mono)', fontSize: 11,
          padding: '4px 10px', borderRadius: 4, display: 'inline-block',
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
export default function App() {
  const [dates, setDates] = useState([])
  const [selectedDate, setSelectedDate] = useState(null)
  const [series, setSeries] = useState([])
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch(`${API}/api/irai/dates`)
      .then(r => r.json())
      .then(data => {
        setDates(data.dates || [])
        if (data.dates?.length > 0) setSelectedDate(data.dates[0])
      })
      .catch(e => setError(e.message))
  }, [])

  useEffect(() => {
    if (!selectedDate) return
    setLoading(true)
    fetch(`${API}/api/irai/series?session_date=${selectedDate}`)
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
      })
      .catch(e => { setError(e.message); setLoading(false) })
  }, [selectedDate])

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

            {/* ── MAIN CHART + FACTORS ── */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: 16, marginTop: 16 }}>

              {/* Hero chart: WIN real vs P_up */}
              <div style={{
                background: '#0F172A', border: '1px solid #1E293B',
                borderRadius: 8, padding: '20px 20px 12px',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 14 }}>
                  <div>
                    <div style={{
                      fontFamily: 'var(--font-serif)', fontSize: 20, color: '#E2E8F0',
                    }}>
                      WIN <span style={{ fontStyle: 'italic', color: '#64748B' }}>vs</span> IRAI
                    </div>
                    <div style={{
                      fontFamily: 'var(--font-mono)', fontSize: 9, color: '#475569', marginTop: 2,
                    }}>branco = WIN real · dourado = sinal IRAI (P_up)</div>
                  </div>
                  <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <div style={{ width: 16, height: 2, background: '#E2E8F0' }} />
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: '#64748B' }}>WIN %</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <div style={{ width: 16, height: 2, background: '#D4A84C', borderTop: '1px dashed #D4A84C' }} />
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: '#64748B' }}>P(↑)</span>
                    </div>
                  </div>
                </div>
                <ResponsiveContainer width="100%" height={320}>
                  <ComposedChart data={series} margin={{ top: 10, right: 45, left: 0, bottom: 0 }}>
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
                    <XAxis
                      dataKey="time"
                      tick={{ fill: '#475569', fontSize: 9, fontFamily: 'JetBrains Mono' }}
                      stroke="#1E293B" interval={11}
                    />
                    <YAxis
                      yAxisId="win" orientation="left"
                      tick={{ fill: '#475569', fontSize: 9, fontFamily: 'JetBrains Mono' }}
                      stroke="#1E293B" tickFormatter={v => `${Number(v).toFixed(1)}%`}
                    />
                    <YAxis
                      yAxisId="pup" orientation="right" domain={[0, 100]}
                      ticks={[0, 25, 50, 75, 100]}
                      tick={{ fill: '#475569', fontSize: 9, fontFamily: 'JetBrains Mono' }}
                      stroke="#1E293B" tickFormatter={v => `${v}%`}
                    />
                    {/* Buy zone (>60%) */}
                    <ReferenceArea yAxisId="pup" y1={60} y2={100} fill="url(#buyZone)" />
                    {/* Sell zone (<40%) */}
                    <ReferenceArea yAxisId="pup" y1={0} y2={40} fill="url(#sellZone)" />
                    {/* 50% line */}
                    <ReferenceLine yAxisId="pup" y={50} stroke="#334155" strokeDasharray="4 4" />
                    <ReferenceLine yAxisId="win" y={0} stroke="#334155" strokeDasharray="2 2" />

                    <Tooltip content={<CustomTooltip />} />

                    {/* WIN area */}
                    <Area yAxisId="win" type="monotone" dataKey="win_return" stroke="none" fill="url(#winFill)" isAnimationActive={false} />
                    {/* WIN line */}
                    <Line yAxisId="win" type="monotone" dataKey="win_return" stroke="#E2E8F0" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                    {/* P_up */}
                    <Line yAxisId="pup" type="monotone" dataKey="p_up" stroke="#D4A84C" strokeWidth={2} dot={false} strokeDasharray="6 3" isAnimationActive={false} />
                  </ComposedChart>
                </ResponsiveContainer>

                {/* ── CUMULATIVE DELTA MINI-CHART ── */}
                <div style={{ marginTop: 4, paddingTop: 8, borderTop: '1px solid #1E293B22' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
                    <div style={{
                      fontFamily: 'var(--font-mono)', fontSize: 9, color: '#475569',
                      letterSpacing: '0.1em', textTransform: 'uppercase',
                    }}>
                      📊 cumulative delta · fluxo de ordens
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
                  <ResponsiveContainer width="100%" height={60}>
                    <ComposedChart data={series} margin={{ top: 2, right: 45, left: 0, bottom: 0 }}>
                      <defs>
                        <linearGradient id="deltaPos" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#4ADE80" stopOpacity={0.3} />
                          <stop offset="100%" stopColor="#4ADE80" stopOpacity={0} />
                        </linearGradient>
                        <linearGradient id="deltaNeg" x1="0" y1="1" x2="0" y2="0">
                          <stop offset="0%" stopColor="#F87171" stopOpacity={0.3} />
                          <stop offset="100%" stopColor="#F87171" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <XAxis dataKey="time" hide />
                      <YAxis hide />
                      <ReferenceLine y={0} stroke="#334155" strokeWidth={1} />
                      <Area
                        type="monotone" dataKey="cum_delta" stroke="none"
                        fill="url(#deltaPos)" isAnimationActive={false}
                        baseValue={0}
                      />
                      <Line
                        type="monotone" dataKey="cum_delta" dot={false}
                        stroke={now.cum_delta >= 0 ? '#4ADE80' : '#F87171'}
                        strokeWidth={1.5} isAnimationActive={false}
                      />
                    </ComposedChart>
                  </ResponsiveContainer>
                  <div style={{
                    display: 'flex', justifyContent: 'space-between',
                    fontFamily: 'var(--font-mono)', fontSize: 8, color: '#334155', marginTop: 2,
                  }}>
                    <span>acima da linha = compradores dominam</span>
                    <span>abaixo = vendedores dominam</span>
                  </div>
                </div>
              </div>

              {/* Factor signals */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10, color: '#475569',
                  letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 2, paddingLeft: 4,
                }}>fatores · favorável para IBOV?</div>

                {Object.entries(FACTOR_META).map(([key]) => (
                  <FactorSignal key={key} fkey={key} data={now.factors?.[key]} />
                ))}

                {/* Net score */}
                <div style={{
                  marginTop: 4, padding: '12px 16px',
                  background: now.score > 0 ? 'rgba(74,222,128,0.06)' : now.score < 0 ? 'rgba(248,113,113,0.06)' : 'transparent',
                  border: '1px solid #1E293B', borderRadius: 6,
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                }}>
                  <span style={{
                    fontFamily: 'var(--font-mono)', fontSize: 10, color: '#64748B',
                    letterSpacing: '0.1em', textTransform: 'uppercase',
                  }}>score líquido</span>
                  <span style={{
                    fontFamily: 'var(--font-mono)', fontSize: 20, fontWeight: 600,
                    color: now.score > 0 ? '#4ADE80' : now.score < 0 ? '#F87171' : '#94A3B8',
                  }}>{now.score >= 0 ? '+' : ''}{now.score.toFixed(3)}</span>
                </div>
              </div>
            </div>

            {/* ── FOOTER ── */}
            <div style={{
              marginTop: 20, paddingTop: 14, borderTop: '1px solid #1E293B',
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
