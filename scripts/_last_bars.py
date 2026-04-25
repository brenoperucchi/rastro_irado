import sys; sys.path.insert(0, '.')
from backend.db import get_connection
conn = get_connection()

print("=== Ultima barra por simbolo (M5) ===")
rows = conn.execute(
    "SELECT symbol, MAX(timestamp_utc) as last FROM market_bars "
    "WHERE timeframe='M5' GROUP BY symbol ORDER BY last DESC"
).fetchall()
for r in rows:
    flag = " << HOJE" if r['last'] >= '2026-04-25' else "  [ONTEM]"
    print(f"  {r['symbol']:<14}  {r['last']}{flag}")

conn.close()
