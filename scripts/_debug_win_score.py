"""
Debug completo do score WIN — mostra o que o engine calcula na última barra.
"""
import sys, math; sys.path.insert(0, '.')
from backend.irai.engine import IRAIEngine
from backend.db import get_connection
from datetime import date

engine = IRAIEngine()
slug = 'win'
m = engine.models[slug]

print("=== WIN model carregado ===")
print(f"  alpha={m['alpha']:.4f}  intercept={m['intercept']:.4f}")
print(f"  fatores no asset_models: {m['factors']}")
print(f"  factor_labels: {m['factor_labels']}")
print(f"  Pesos ativos:")
for k, v in sorted(m['weights'].items()):
    print(f"    {k:<20} = {v:>+.4f}")
print(f"  Sigmas ativos:")
for k, v in sorted(m['sigmas'].items()):
    print(f"    {k:<20} = {v:.6f}")

# Verificar quais símbolos têm dados hoje
conn = get_connection()
today = date.today().isoformat()
print(f"\n=== Dados hoje ({today}) por símbolo ===")
syms_needed = m['factors'] + [m.get('data_proxy') or 'WIN$N']
syms_needed = list(set(syms_needed))

for sym in syms_needed:
    n = conn.execute(
        "SELECT COUNT(*) as c FROM market_bars WHERE symbol=? AND timeframe='M5' AND timestamp_utc LIKE ?",
        (sym, f"{today}%")
    ).fetchone()['c']
    last = conn.execute(
        "SELECT timestamp_utc, open, close FROM market_bars WHERE symbol=? AND timeframe='M5' AND timestamp_utc LIKE ? ORDER BY timestamp_utc DESC LIMIT 1",
        (sym, f"{today}%")
    ).fetchone()
    if last:
        ret = (last['close'] - last['open']) / last['open'] * 100
        print(f"  {sym:<12} {n:>4} barras  last={last['timestamp_utc'][11:16]}  ret={ret:>+.3f}%")
    else:
        print(f"  {sym:<12} SEM DADOS hoje")

conn.close()

# Computar via engine
print(f"\n=== Computando sessao {today} ===")
snaps = engine.compute_from_db(today, target='WIN$N')
if snaps:
    last = snaps[-1]
    print(f"  Barras: {len(snaps)}")
    print(f"  Score: {last.score:.4f}")
    print(f"  P_up: {last.p_up:.1f}%")
    print(f"  t_frac: {last.t_frac:.3f}")
    print(f"  WIN ret: {last.win_return:+.3f}%")
    print()
    print("  Fatores (ultima barra):")
    for label, f in sorted(last.factors.items()):
        w = f.get('weight', 0)
        z = f.get('z_score', 0)
        contrib = f.get('contribution', 0)
        ret = f.get('ret', 0)
        print(f"    {label:<10} w={w:>+.4f}  z={z:>+6.3f}  contrib={contrib:>+7.4f}  ret={ret:>+.4f}%")
    
    # Recompute manual do score para verificar
    manual_score = sum(f.get('contribution', 0) for f in last.factors.values())
    print(f"\n  Score manual (soma contributions): {manual_score:.4f}")
    p_manual = 100 / (1 + math.exp(-(m['alpha'] * manual_score + m['intercept'])))
    print(f"  P_up manual: {p_manual:.1f}%")
else:
    # Pegar última sessão disponível
    conn2 = get_connection()
    last_date = conn2.execute(
        "SELECT DISTINCT substr(timestamp_utc,1,10) as d FROM market_bars "
        "WHERE symbol='WIN\x24N' AND timeframe='M5' ORDER BY d DESC LIMIT 1"
    ).fetchone()
    conn2.close()
    if last_date:
        d = last_date['d']
        print(f"  Sem dados hoje, usando {d}")
        snaps = engine.compute_from_db(d, target='WIN$N')
        if snaps:
            last = snaps[-1]
            print(f"  P_up final: {last.p_up:.1f}%  score: {last.score:.4f}")
            for label, f in sorted(last.factors.items()):
                print(f"    {label:<10} w={f.get('weight',0):>+.4f}  z={f.get('z_score',0):>+6.3f}  contrib={f.get('contribution',0):>+7.4f}")
