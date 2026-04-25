import sys; sys.path.insert(0, '.')
from backend.db import get_connection
from backend.irai.engine import IRAIEngine

conn = get_connection()

# Checar barras de WIN hoje no banco
print("=== Barras WIN hoje em UTC ===")
rows = conn.execute(
    "SELECT timestamp_utc, open, close FROM market_bars "
    "WHERE symbol='WIN$N' AND timeframe='M5' AND timestamp_utc >= '2026-04-25' "
    "ORDER BY timestamp_utc DESC LIMIT 5"
).fetchall()
for r in rows:
    ret = (r['close'] - r['open']) / r['open'] * 100
    print(f"  {r['timestamp_utc']}  open={r['open']:.0f}  close={r['close']:.0f}  ret={ret:+.3f}%")

print()
# Checar todos os fatores do WIN hoje
fatores = ['WIN$N', 'DOL$N', 'DI1$N', 'USDMXN', 'US30', 'GBPUSD', 'USDCAD', 'USDCHF']
print("=== Contagem de barras HOJE (>= 2026-04-25T09:00:00Z) ===")
for sym in fatores:
    n = conn.execute(
        "SELECT COUNT(*) as c FROM market_bars WHERE symbol=? AND timeframe='M5' AND timestamp_utc >= '2026-04-25T09:00:00Z'",
        (sym,)
    ).fetchone()['c']
    last = conn.execute(
        "SELECT timestamp_utc FROM market_bars WHERE symbol=? AND timeframe='M5' ORDER BY timestamp_utc DESC LIMIT 1",
        (sym,)
    ).fetchone()
    print(f"  {sym:<12} {n:>4} barras hoje | last: {last['timestamp_utc'] if last else 'N/A'}")

conn.close()

# Checar o que engine.compute_from_db retorna com a data de hoje
print()
engine = IRAIEngine()
# Tentar com a data atual como string UTC
import datetime
today_utc = datetime.datetime.utcnow().date().isoformat()
today_brt = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date().isoformat()
print(f"  today_utc = {today_utc}")
print(f"  today_brt = {today_brt}")

for d in [today_brt, today_utc]:
    snaps = engine.compute_from_db(d, target='WIN$N')
    print(f"  compute_from_db('{d}') -> {len(snaps)} snaps")
    if snaps:
        last = snaps[-1]
        print(f"    P_up={last.p_up:.1f}%  score={last.score:.4f}  bars={len(snaps)}")
        break
