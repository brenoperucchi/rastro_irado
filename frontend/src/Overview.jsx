import { useState, useEffect } from 'react'

const API = 'http://localhost:8888'

function Sparkline({ data, width = 80, height = 24 }) {
  if (!data || data.length < 2) return null
  const min = Math.min(...data)
  const max = Math.max(...data)
  const range = max - min || 1
  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width
    const y = height - ((v - min) / range) * (height - 4) - 2
    return `${x},${y}`
  }).join(' ')

  const last = data[data.length - 1]
  const color = last >= 60 ? '#4ADE80' : last <= 40 ? '#F87171' : '#94A3B8'

  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      <polyline
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={points}
      />
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
        const [tRes, oRes] = await Promise.all([
          fetch(`${API}/api/irai/targets`),
          fetch(`${API}/api/irai/overview`),
        ])
        const tData = await tRes.json()
        const oData = await oRes.json()
        setTargets(tData.targets || [])
        setOverview(oData.targets || [])
      } catch (e) {
        console.error('Overview load error:', e)
      } finally {
        setLoading(false)
      }
    }
    load()
    const interval = setInterval(load, 15000)
    return () => clearInterval(interval)
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
    <div style={{ padding: '24px 32px', maxWidth: 1400, margin: '0 auto' }}>
      {/* Header */}
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        alignItems: 'baseline', marginBottom: 24,
      }}>
        <div>
          <div style={{
            fontFamily: 'var(--font-serif)', fontSize: 28,
            fontWeight: 500, color: '#E2E8F0', lineHeight: 1,
          }}>
            IRAI <span style={{ color: '#475569', fontWeight: 300 }}>Multi-Asset</span>
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10,
            color: '#475569', marginTop: 4, letterSpacing: '0.12em',
          }}>
            INTRADAY RISK APPETITE INDEX · {calibrated.length} MODELOS ATIVOS
          </div>
        </div>
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

      {/* Active Models Grid */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
        gap: 12,
      }}>
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
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
            gap: 8,
          }}>
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
        <span>Auto-refresh 15s</span>
      </div>
    </div>
  )
}


function AssetCard({ card, onClick }) {
  const pUp = card.p_up || 50
  const isBuy = pUp >= 60
  const isSell = pUp <= 40
  const isNeutral = !isBuy && !isSell

  const signalText = isBuy ? 'COMPRA' : isSell ? 'VENDA' : 'NEUTRO'
  const signalColor = isBuy ? '#4ADE80' : isSell ? '#F87171' : '#64748B'
  const bgColor = isBuy ? 'rgba(74,222,128,0.04)' : isSell ? 'rgba(248,113,113,0.04)' : 'rgba(71,85,105,0.03)'
  const borderColor = isBuy ? 'rgba(74,222,128,0.15)' : isSell ? 'rgba(248,113,113,0.15)' : '#1E293B'

  const ret = card.win_return || 0
  const retColor = ret >= 0 ? '#4ADE80' : '#F87171'

  return (
    <div
      onClick={onClick}
      style={{
        background: bgColor,
        border: `1px solid ${borderColor}`,
        borderRadius: 8,
        padding: '16px 18px 14px',
        cursor: 'pointer',
        transition: 'all 0.2s ease',
        position: 'relative',
        overflow: 'hidden',
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
      {/* Top row: icon + name + signal */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 20, lineHeight: 1 }}>{card.icon}</span>
          <div>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 13,
              fontWeight: 700, color: '#E2E8F0', lineHeight: 1,
            }}>{card.display_name}</div>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 8,
              color: '#475569', marginTop: 2,
            }}>{card.target}</div>
          </div>
        </div>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 700,
          color: signalColor, letterSpacing: '0.05em',
          padding: '2px 6px', borderRadius: 3,
          background: isBuy ? 'rgba(74,222,128,0.12)' : isSell ? 'rgba(248,113,113,0.12)' : 'rgba(71,85,105,0.08)',
        }}>
          {signalText}
        </div>
      </div>

      {/* P_up + Return */}
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        alignItems: 'baseline', marginTop: 12,
      }}>
        <div>
          <div style={{
            fontFamily: 'var(--font-serif)', fontSize: 26,
            color: signalColor, fontWeight: 400, lineHeight: 1,
          }}>
            {pUp.toFixed(0)}%
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 8,
            color: '#475569', marginTop: 2,
          }}>P(↑)</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 16,
            color: retColor, fontWeight: 600, lineHeight: 1,
          }}>
            {ret >= 0 ? '+' : ''}{(ret * 100).toFixed(2)}%
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 8,
            color: '#475569', marginTop: 2,
          }}>retorno</div>
        </div>
      </div>

      {/* Sparkline */}
      <div style={{ marginTop: 10 }}>
        <Sparkline data={card.sparkline} width={190} height={20} />
      </div>

      {/* Model accuracy */}
      <div style={{
        marginTop: 8, fontFamily: 'var(--font-mono)', fontSize: 8,
        color: '#334155', display: 'flex', justifyContent: 'space-between',
      }}>
        <span>acc {card.accuracy?.toFixed(0)}%</span>
        <span>{card.bars || 0} barras</span>
      </div>
    </div>
  )
}
