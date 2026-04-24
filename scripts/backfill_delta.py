"""
Backfill de real_volume e delta para barras históricas WIN$N.

Usa os dados do MT5 para recomputar high, low, real_volume e delta
para todas as barras que já estão no banco mas sem esses campos.
"""
import MetaTrader5 as mt5
import sqlite3
import os
import sys
from datetime import datetime, timezone

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.db import get_connection, DB_PATH, migrate_delta

MT5_PATH = r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"
SYMBOL = "WIN$N"


def compute_bar_delta(open_p, high, low, close, real_volume):
    bar_range = high - low
    if bar_range <= 0 or real_volume <= 0:
        return 0.0
    close_pct = (close - low) / bar_range
    return real_volume * (2 * close_pct - 1)


def main():
    # Garantir migração
    migrate_delta()

    print(f"Conectando ao MT5...")
    if not mt5.initialize(path=MT5_PATH, timeout=15000):
        print(f"ERRO: {mt5.last_error()}")
        return

    # Pegar TODAS as barras M5 do WIN$N (100k = ~1 ano)
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, 0, 100000)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        print("Sem dados do MT5")
        return

    print(f"MT5 retornou {len(rates)} barras M5 do {SYMBOL}")

    conn = get_connection()
    cursor = conn.cursor()

    updated = 0
    for bar in rates:
        ts = datetime.fromtimestamp(bar["time"], tz=timezone.utc)
        ts_iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")

        o = float(bar["open"])
        h = float(bar["high"])
        l = float(bar["low"])
        c = float(bar["close"])
        rv = float(bar["real_volume"]) if bar["real_volume"] else 0
        delta = compute_bar_delta(o, h, l, c, rv)

        cursor.execute("""
            UPDATE market_bars
            SET real_volume = ?, delta = ?
            WHERE symbol = ? AND timeframe = 'M5' AND timestamp_utc = ?
              AND (real_volume IS NULL OR real_volume = 0)
        """, (rv, delta, SYMBOL, ts_iso))

        if cursor.rowcount > 0:
            updated += 1

    conn.commit()
    conn.close()

    print(f"Backfill concluido: {updated} barras atualizadas com real_volume + delta")

    # Verificação
    conn = get_connection()
    stats = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN real_volume > 0 THEN 1 ELSE 0 END) as with_vol,
            SUM(CASE WHEN delta != 0 THEN 1 ELSE 0 END) as with_delta
        FROM market_bars
        WHERE symbol = ? AND timeframe = 'M5'
    """, [SYMBOL]).fetchone()
    conn.close()

    print(f"\nEstado final WIN$N M5:")
    print(f"  Total:      {stats['total']}")
    print(f"  Com volume: {stats['with_vol']}")
    print(f"  Com delta:  {stats['with_delta']}")


if __name__ == "__main__":
    main()
