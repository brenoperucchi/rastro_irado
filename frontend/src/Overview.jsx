import { useState, useEffect } from 'react'

const OVERVIEW_STYLES = `
  @keyframes borderGlowGreen {
    0%, 100% { box-shadow: 0 0 5px rgba(74,222,128,0.2), inset 0 0 5px rgba(74,222,128,0.05); border-color: rgba(74,222,128,0.4); }
    50% { box-shadow: 0 0 15px rgba(74,222,128,0.6), inset 0 0 10px rgba(74,222,128,0.1); border-color: rgba(74,222,128,0.8); }
  }
  @keyframes borderGlowRed {
    0%, 100% { box-shadow: 0 0 5px rgba(248,113,113,0.2), inset 0 0 5px rgba(248,113,113,0.05); border-color: rgba(248,113,113,0.4); }
    50% { box-shadow: 0 0 15px rgba(248,113,113,0.6), inset 0 0 10px rgba(248,113,113,0.1); border-color: rgba(248,113,113,0.8); }
  }
  .card-alert-green { animation: borderGlowGreen 2s infinite ease-in-out; }
  .card-alert-red { animation: borderGlowRed 2s infinite ease-in-out; }

  @keyframes badgeBlink {
    0% { opacity: 1; }
    50% { opacity: 0.3; }
    100% { opacity: 1; }
  }
  .badge-blink { animation: badgeBlink 1.5s infinite ease-in-out; }

  /* Mobile Responsive Overrides */
  .overview-container { padding: 24px 32px; max-width: 1400px; margin: 0 auto; font-family: var(--font-sans); color: #C8C8D4; }
  .overview-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 24px; }
  .overview-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
  .overview-grid-pending { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 8px; }
  .asset-card { padding: 14px 16px 12px; transition: all 0.2s ease; cursor: pointer; position: relative; overflow: hidden; }
  .sparkline-wrapper { margin-top: 10px; width: 100%; }
  
  @media (max-width: 768px) {
    .overview-container { padding: 16px 12px !important; }
    .overview-header { flex-direction: column; align-items: flex-start !important; gap: 16px; }
    .overview-grid { grid-template-columns: 1fr !important; }
    .overview-grid-pending { grid-template-columns: 1fr !important; }
    .asset-card { padding: 12px !important; }
    .sparkline-wrapper { margin-top: 16px !important; }
  }
`
const FIREBASE_URL = import.meta.env.VITE_FIREBASE_URL
// window.location.hostname (não 'localhost' fixo) para funcionar tanto local
// quanto acessado remotamente via IP da LAN (ex: dashboard aberto de outra máquina).
const API = FIREBASE_URL ? null : `http://${window.location.hostname}:8888`

function Sparkline({ dataV1, dataV2, width = '100%', height = 24 }) {
  const allData = [...(dataV1 || []), ...(dataV2 || [])]
  if (allData.length < 2) return null
  const min = Math.min(...allData)
  const max = Math.max(...allData)
  const range = max - min || 1
  const viewBoxWidth = 185

  const makePoints = (data) => {
    if (!data || data.length < 2) return ''
    return data.map((v, i) => {
      const x = (i / (data.length - 1)) * viewBoxWidth
      const y = height - ((v - min) / range) * (height - 4) - 2
      return `${x},${y}`
    }).join(' ')
  }

  const pointsV1 = makePoints(dataV1)
  const pointsV2 = makePoints(dataV2)

  return (
    <svg width={width} height={height} viewBox={`0 0 ${viewBoxWidth} ${height}`} preserveAspectRatio="none" style={{ display: 'block' }}>
      {pointsV1 && (
        <polyline
          fill="none"
          stroke="#D4A84C"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeDasharray="4 2"
          points={pointsV1}
        />
      )}
      {pointsV2 && (
        <polyline
          fill="none"
          stroke="#60A5FA"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          points={pointsV2}
        />
      )}
    </svg>
  )
}

export default function Overview({ onSelectTarget }) {
  const [targets, setTargets] = useState([])
  const [overview, setOverview] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function load() {
      try {
        if (FIREBASE_URL) {
          const url = FIREBASE_URL.endsWith('.json') ? FIREBASE_URL : `${FIREBASE_URL.replace(/\/$/, '')}/db.json`
          const res = await fetch(url)
          const db = await res.json()
          setTargets(db?.targets?.targets || [])
          setOverview(db?.overview?.targets || [])
        } else {
          const [tRes, oRes] = await Promise.all([
            fetch(`${API}/api/irai/targets`),
            fetch(`${API}/api/irai/overview?version=v2`),
          ])
          const tData = await tRes.json()
          const oData = await oRes.json()
          setTargets(tData.targets || [])
          setOverview(oData.targets || [])
        }
      } catch (e) {
        console.error('Overview load error:', e)
      } finally {
        setLoading(false)
      }
    }
    load()
    
    let mounted = true
    let pollTimer = null
    
    if (FIREBASE_URL) {
      // Firebase: poll every 60s (SSE unreliable for large payloads)
      pollTimer = setInterval(async () => {
        if (!mounted) return
        try {
          const url = FIREBASE_URL.endsWith('.json') ? FIREBASE_URL : `${FIREBASE_URL.replace(/\/$/, '')}/db.json`
          const res = await fetch(url)
          const db = await res.json()
          setTargets(db?.targets?.targets || [])
          setOverview(db?.overview?.targets || [])
        } catch (e) { console.error('Overview poll error:', e) }
      }, 60_000)
    } else {
      // Local: simple HTTP polling every 60s
      pollTimer = setInterval(async () => {
        if (!mounted) return
        try {
          const [oRes] = await Promise.all([
            fetch(`${API}/api/irai/overview?version=v2`),
          ])
          const oData = await oRes.json()
          setOverview(oData.targets || [])
        } catch (e) { console.error('Overview poll error:', e) }
      }, 60_000)
    }
    
    return () => {
      mounted = false
      if (pollTimer) clearInterval(pollTimer)
    }
  }, [])

  // Merge targets + overview data
  const cards = targets.map(t => {
    const live = overview.find(o => o.target === t.target) || {}
    return { ...t, ...live }
  })

  const calibrated = cards.filter(c => c.calibrated)
  const pending = cards.filter(c => !c.calibrated)

  if (loading) {
    return (
      <div style={{
        height: '100vh', display: 'flex', alignItems: 'center',
        justifyContent: 'center', color: '#64748B',
        fontFamily: 'var(--font-mono)', fontSize: 14,
      }}>
        Carregando modelos...
      </div>
    )
  }

  return (
    <div className="overview-container">
      <style>{OVERVIEW_STYLES}</style>
      {/* Header */}
      <header className="overview-header">
        <div>
          <div style={{
            fontFamily: 'var(--font-serif)', fontSize: 28,
            fontWeight: 500, color: '#C9A227', lineHeight: 1,
            letterSpacing: '0.04em',
          }}>
            IRAI <span style={{ color: '#3A3A4A', fontWeight: 300 }}>Multi-Asset</span>
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10,
            color: '#3A3A4A', marginTop: 4, letterSpacing: '0.12em',
          }}>
            INTRADAY RISK APPETITE INDEX · {calibrated.length} MODELOS ATIVOS
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10,
            color: '#4ADE80', display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <div style={{
              width: 6, height: 6, borderRadius: '50%', background: '#4ADE80',
              animation: 'pulse 2s infinite',
            }} />
            LIVE
          </div>
        </div>
      </header>

      {/* Active Models Grid */}
      <div className="overview-grid">
        {calibrated.map(card => (
          <AssetCard key={card.target} card={card} onClick={() => onSelectTarget?.(card.target)} />
        ))}
      </div>

      {/* Pending Models */}
      {pending.length > 0 && (
        <>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 9,
            color: '#334155', marginTop: 24, marginBottom: 8,
            letterSpacing: '0.15em', textTransform: 'uppercase',
          }}>
            Aguardando dados · {pending.length} ativos
          </div>
          <div className="overview-grid-pending">
            {pending.map(card => (
              <div key={card.target} style={{
                background: 'rgba(15,23,42,0.5)',
                border: '1px solid #1E293B',
                borderRadius: 6, padding: '10px 14px',
                opacity: 0.5,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 16 }}>{card.icon}</span>
                  <div>
                    <div style={{
                      fontFamily: 'var(--font-mono)', fontSize: 11,
                      color: '#64748B', fontWeight: 600,
                    }}>{card.display_name}</div>
                    <div style={{
                      fontFamily: 'var(--font-mono)', fontSize: 8,
                      color: '#334155',
                    }}>{card.session_hours}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Footer */}
      <div style={{
        marginTop: 24, paddingTop: 12, borderTop: '1px solid #1E293B',
        fontFamily: 'var(--font-mono)', fontSize: 9, color: '#1E293B',
        display: 'flex', justifyContent: 'space-between',
      }}>
        <span>IRAI Multi-Asset v2.0 · Cross-asset macro factor models</span>
        <span>Push em tempo real ativo</span>
      </div>
    </div>
  )
}


function AssetCard({ card, onClick }) {
  const pUp = card.p_up_v1 != null ? card.p_up_v1 : (card.p_up || 50)
  const accuracy = card.accuracy || 80
  const isBuy = pUp >= 60
  const isSell = pUp <= 40

  const directionText = isBuy ? 'ALTA' : isSell ? 'BAIXA' : 'NEUTRO'
  const signalColor = isBuy ? '#4ADE80' : isSell ? '#F87171' : '#64748B'
  const bgColor = isBuy ? 'rgba(74,222,128,0.04)' : isSell ? 'rgba(248,113,113,0.04)' : 'rgba(71,85,105,0.03)'
  const borderColor = isBuy ? 'rgba(74,222,128,0.15)' : isSell ? 'rgba(248,113,113,0.15)' : '#1C1C22'

  const nweUp = !card.nwe_available ? undefined
    : card.nwe_direction === 'up' ? true
    : card.nwe_direction === 'down' ? false
    : undefined; // 'flat' — sem direção, não conta como divergência
  const isNweDivergentBuy = isBuy && nweUp === false;
  const isNweDivergentSell = isSell && nweUp === true;

  const isReturnDivergentBuy = isBuy && card.win_return < 0;
  const isReturnDivergentSell = isSell && card.win_return > 0;

  const isNweExhaustionDown = card.nwe_available && card.nwe_upper != null && card.win_return > card.nwe_upper;
  const isNweExhaustionUp = card.nwe_available && card.nwe_lower != null && card.win_return < card.nwe_lower;

  const hasAlert = isReturnDivergentBuy || isReturnDivergentSell;
  const alertClass = hasAlert ? (isBuy ? 'card-alert-green' : 'card-alert-red') : '';

  // Convicção calibrada por Shrinkage (mesmo algoritmo do SignalGauge)
  const acc = Math.min(Math.max(accuracy, 50), 100)
  const shrinkFactor = (2 * acc / 100) - 1
  
  // V1 Conviction
  const pUpV1 = card.p_up_v1 != null ? card.p_up_v1 : pUp
  const pShrunkV1 = 50 + (pUpV1 - 50) * shrinkFactor
  const convictionV1 = Math.round(Math.abs(pShrunkV1 - 50) * 2)
  const maxConviction = Math.round((acc - 50) * 2)
  const convRatioV1 = maxConviction > 0 ? convictionV1 / maxConviction : 0
  const convLabelV1 = card.is_preview ? 'pré-mercado' : (convRatioV1 >= 0.55 ? 'forte' : convRatioV1 >= 0.25 ? 'moderada' : 'fraca')
  
  // V2 Conviction
  const hasV2 = card.p_up_v2 != null
  const pUpV2 = card.p_up_v2
  const pShrunkV2 = hasV2 ? 50 + (pUpV2 - 50) * shrinkFactor : 50
  const convictionV2 = hasV2 ? Math.round(Math.abs(pShrunkV2 - 50) * 2) : 0
  const convRatioV2 = maxConviction > 0 ? convictionV2 / maxConviction : 0
  const convLabelV2 = card.is_preview ? 'pré-mercado' : (convRatioV2 >= 0.55 ? 'forte' : convRatioV2 >= 0.25 ? 'moderada' : 'fraca')

  const convColor = card.is_preview ? '#EAB308' : (convRatioV1 >= 0.55 ? signalColor : convRatioV1 >= 0.25 ? '#C9A227' : '#334155')

  const ret = card.win_return || 0

  return (
    <div
      onClick={onClick}
      className={`asset-card ${alertClass}`}
      style={{
        background: bgColor,
        border: `1px solid ${borderColor}`,
        borderRadius: 8,
      }}
      onMouseEnter={e => {
        e.currentTarget.style.transform = 'translateY(-2px)'
        e.currentTarget.style.borderColor = signalColor
        e.currentTarget.style.boxShadow = `0 4px 20px ${signalColor}15`
      }}
      onMouseLeave={e => {
        e.currentTarget.style.transform = 'translateY(0)'
        e.currentTarget.style.borderColor = borderColor
        e.currentTarget.style.boxShadow = 'none'
      }}
    >
      {/* Top row: icon + name + signal badge */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 18, lineHeight: 1, fontFamily: 'var(--font-mono)', fontWeight: 700 }}>{card.icon}</span>
          <div>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 12,
              fontWeight: 700, color: '#C8C8D4', lineHeight: 1,
            }}>{card.display_name}</div>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 7,
              color: '#2A2A36', marginTop: 1,
            }}>{card.target}</div>
          </div>
        </div>
        {/* Conviction badge top-right */}
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 8, fontWeight: 700,
          color: convColor, letterSpacing: '0.04em',
          padding: '2px 6px', borderRadius: 3,
          background: `${convColor}18`,
          border: `1px solid ${convColor}33`,
        }}>
          {convLabelV1}
        </div>
      </div>

      {/* Direction + conviction (main body) */}
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        alignItems: 'flex-end', marginTop: 10,
      }}>
        <div>
          {/* Direction label — large */}
          <div style={{
            fontFamily: 'var(--font-serif)', fontSize: 24,
            color: signalColor, fontWeight: 400, lineHeight: 1,
          }}>{directionText}</div>
          {/* Conviction % */}
          <div style={{ display: 'flex', gap: 12 }}>
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: convColor, marginTop: 4, fontWeight: 600 }}>
                {card.version || 'v2'} conv. {convictionV1}%
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 8, color: '#2A2A36', marginTop: 2 }}>
                P(↑) {pUpV1.toFixed(0)}%
              </div>
            </div>
            {hasV2 && (
              <div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: '#60A5FA', marginTop: 4, fontWeight: 600 }}>
                  v2 conv. {convictionV2}%
                </div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 8, color: '#2A2A36', marginTop: 2 }}>
                  P(↑) {pUpV2.toFixed(0)}%
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Indicators Column (D P Z E) */}
        <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
          {/* D: Divergência de Retorno (Cor fixa, sem blink local) */}
          <div title="Divergência de Retorno %" style={{
            width: 16, height: 16, display: 'flex', alignItems: 'center', justifyContent: 'center',
            borderRadius: 4, fontSize: 9, fontFamily: 'var(--font-mono)', fontWeight: 600,
            background: (isReturnDivergentBuy || isReturnDivergentSell) ? (isReturnDivergentBuy ? 'rgba(74,222,128,0.2)' : 'rgba(248,113,113,0.2)') : 'rgba(148,163,184,0.05)',
            color: (isReturnDivergentBuy || isReturnDivergentSell) ? (isReturnDivergentBuy ? '#4ADE80' : '#F87171') : '#475569',
            border: `1px solid ${(isReturnDivergentBuy || isReturnDivergentSell) ? (isReturnDivergentBuy ? 'rgba(74,222,128,0.4)' : 'rgba(248,113,113,0.4)') : 'rgba(148,163,184,0.1)'}`
          }}>D</div>

          {/* P: Pullback / NWE Divergence (Blink) */}
          <div title="Pullback / Divergência NWE" className={(isNweDivergentBuy || isNweDivergentSell) ? 'badge-blink' : ''} style={{
            width: 16, height: 16, display: 'flex', alignItems: 'center', justifyContent: 'center',
            borderRadius: 4, fontSize: 9, fontFamily: 'var(--font-mono)', fontWeight: 600,
            background: (isNweDivergentBuy || isNweDivergentSell) ? (isNweDivergentBuy ? 'rgba(74,222,128,0.2)' : 'rgba(248,113,113,0.2)') : 'rgba(148,163,184,0.05)',
            color: (isNweDivergentBuy || isNweDivergentSell) ? (isNweDivergentBuy ? '#4ADE80' : '#F87171') : '#475569',
            border: `1px solid ${(isNweDivergentBuy || isNweDivergentSell) ? (isNweDivergentBuy ? 'rgba(74,222,128,0.4)' : 'rgba(248,113,113,0.4)') : 'rgba(148,163,184,0.1)'}`
          }}>P</div>

          {/* Z: Z-Score (Blink) */}
          <div title="Z-Score" className={card.price_diverges ? 'badge-blink' : ''} style={{
            width: 16, height: 16, display: 'flex', alignItems: 'center', justifyContent: 'center',
            borderRadius: 4, fontSize: 9, fontFamily: 'var(--font-mono)', fontWeight: 600,
            background: card.price_diverges ? (isBuy ? 'rgba(74,222,128,0.2)' : 'rgba(248,113,113,0.2)') : 'rgba(148,163,184,0.05)',
            color: card.price_diverges ? (isBuy ? '#4ADE80' : '#F87171') : '#475569',
            border: `1px solid ${card.price_diverges ? (isBuy ? 'rgba(74,222,128,0.4)' : 'rgba(248,113,113,0.4)') : 'rgba(148,163,184,0.1)'}`
          }}>Z</div>

          {/* E: Exaustão NWE (Blink) */}
          <div title="Exaustão NWE" className={(isNweExhaustionUp || isNweExhaustionDown) ? 'badge-blink' : ''} style={{
            width: 16, height: 16, display: 'flex', alignItems: 'center', justifyContent: 'center',
            borderRadius: 4, fontSize: 9, fontFamily: 'var(--font-mono)', fontWeight: 600,
            background: (isNweExhaustionUp || isNweExhaustionDown) ? (isNweExhaustionUp ? 'rgba(74,222,128,0.2)' : 'rgba(248,113,113,0.2)') : 'rgba(148,163,184,0.05)',
            color: (isNweExhaustionUp || isNweExhaustionDown) ? (isNweExhaustionUp ? '#4ADE80' : '#F87171') : '#475569',
            border: `1px solid ${(isNweExhaustionUp || isNweExhaustionDown) ? (isNweExhaustionUp ? 'rgba(74,222,128,0.4)' : 'rgba(248,113,113,0.4)') : 'rgba(148,163,184,0.1)'}`
          }}>E</div>
        </div>
      </div>



      {/* Footer: acc + bars */}
      <div style={{
        marginTop: 8, fontFamily: 'var(--font-mono)', fontSize: 7,
        color: '#1E1E28', display: 'flex', justifyContent: 'space-between',
      }}>
        <span>acc {accuracy.toFixed(0)}%</span>
        <span>{card.bars || 0} barras</span>
      </div>
    </div>
  )
}

