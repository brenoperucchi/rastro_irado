"""
Diagnóstico: por que P_up do WIN caiu para ~55%?
Simula o score atual usando os parâmetros do banco.
"""
import sys; sys.path.insert(0, '.')
from backend.db import get_connection
from backend.irai.engine import IRAIEngine
from datetime import date

# 1) Carregar engine
engine = IRAIEngine()
slug = 'win'
m = engine.models.get(slug, {})
print("=== WIN model params (engine) ===")
print(f"  alpha     = {m.get('alpha', 'N/A'):.4f}")
print(f"  intercept = {m.get('intercept', 'N/A'):.4f}")
print(f"  P_up baseline (score=0) = {100 / (1 + __import__('math').exp(-m.get('intercept', 0))):.1f}%")
print()
print("  Weights:")
for k, v in sorted(m.get('weights', {}).items()):
    print(f"    {k:<20} = {v:+.4f}")
print()
print("  Sigmas:")
for k, v in sorted(m.get('sigmas', {}).items()):
    print(f"    {k:<20} = {v:.6f}")

# 2) Computar sessão de hoje
print()
print("=== Últimas barras (hoje) ===")
today = date.today().isoformat()
snaps = engine.compute_from_db(today, target='WIN$N')
if snaps:
    last = snaps[-1]
    print(f"  Barras: {len(snaps)}")
    print(f"  P_up final: {last.p_up:.1f}%  | score: {last.score:.4f}")
    print(f"  WIN: {last.win_open:.0f} -> {last.win_current:.0f} ({last.win_return:+.2f}%)")
    print()
    print("  Fatores (última barra):")
    for label, f in sorted(last.factors.items()):
        print(f"    {label:<8} z={f['z_score']:>+7.3f}  contrib={f['contribution']:>+7.4f}  ret={f['ret']:>+6.3f}%  w={f['weight']:>+.4f}")
else:
    # Tentar último dia disponível
    conn = get_connection()
    row = conn.execute(
        "SELECT DISTINCT substr(timestamp_utc, 1, 10) as d FROM market_bars "
        "WHERE symbol='WIN\$N' AND timeframe='M5' ORDER BY d DESC LIMIT 2"
    ).fetchall()
    conn.close()
    if row and len(row) > 1:
        d = row[1]['d']
        print(f"  Hoje sem dados, usando {d}")
        snaps = engine.compute_from_db(d, target='WIN$N')
        if snaps:
            last = snaps[-1]
            print(f"  P_up final: {last.p_up:.1f}%  | score: {last.score:.4f}")
            for label, f in sorted(last.factors.items()):
                print(f"    {label:<8} z={f['z_score']:>+7.3f}  contrib={f['contribution']:>+7.4f}  ret={f['ret']:>+6.3f}%")
        else:
            print("  Sem dados")
