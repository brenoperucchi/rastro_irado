"""
IRAI — Backfill histórico M5 (setup WSL, escopo WIN$N/WDO$N).

Baixa o máximo de histórico M5 disponível nos terminais MT5 (BR + Axi) e
grava em market_bars via INSERT OR IGNORE — não sobrescreve barras já
coletadas por collector_wsl.py. Rodar uma vez para popular o banco antes da
calibração; collector_wsl.py assume dali em diante a coleta incremental.

Reaproveita o mapeamento de terminais/símbolos de collector_wsl.py (mesma
tupla ticker-na-broker/nome-canônico/source) para não haver duas fontes de
verdade sobre quais símbolos cada terminal cobre.

Uso: python backend/workers/backfill_history_wsl.py [--n 300000]
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backend.db import get_connection, DB_PATH
from backend.workers.collector_wsl import TERMINALS, compute_bar_delta


def backfill_symbol(mt5_symbol: str, canonical_symbol: str, source: str,
                     conn, n_bars: int):
    rates = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_M5, 0, n_bars)
    if rates is None or len(rates) == 0:
        return 0, None, None

    cursor = conn.cursor()
    inserted = 0
    for bar in rates:
        ts = datetime.fromtimestamp(bar[0], tz=timezone.utc)
        ts_iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")

        o, h, l, c = float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4])
        tick_vol = float(bar[5]) if bar[5] else 0
        real_vol = float(bar[7]) if len(bar) > 7 and bar[7] else 0
        delta = compute_bar_delta(o, h, l, c, real_vol)

        cursor.execute(
            """INSERT OR IGNORE INTO market_bars
               (symbol, source, timeframe, timestamp_utc, open, high, low, close,
                volume, real_volume, delta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (canonical_symbol, source, "M5", ts_iso, o, h, l, c,
             tick_vol, real_vol, delta),
        )
        if cursor.rowcount > 0:
            inserted += 1

    conn.commit()
    first = datetime.fromtimestamp(rates[0][0], tz=timezone.utc)
    last = datetime.fromtimestamp(rates[-1][0], tz=timezone.utc)
    return inserted, first, last


def main():
    parser = argparse.ArgumentParser(description="IRAI - Backfill histórico M5 (WSL)")
    parser.add_argument("--n", type=int, default=300000, help="Teto de barras por símbolo")
    parser.add_argument("--db", default=DB_PATH)
    args = parser.parse_args()

    conn = get_connection(args.db)

    for terminal in TERMINALS:
        try:
            mt5.shutdown()
        except Exception:
            pass
        time.sleep(0.5)

        if not mt5.initialize(path=terminal["path"], portable=True, timeout=15000):
            print(f"### {terminal['name']}: FALHA init -> {mt5.last_error()}")
            continue

        print(f"\n### {terminal['name']}")
        for mt5_sym, canonical_sym, source in terminal["symbols"]:
            mt5.symbol_select(mt5_sym, True)
            inserted, first, last = backfill_symbol(mt5_sym, canonical_sym, source, conn, args.n)
            if first is None:
                print(f"  {canonical_sym:<22} SEM DADOS ({mt5.last_error()})")
                continue
            print(f"  {canonical_sym:<22} +{inserted:<7} barras  {first.date()} -> {last.date()}")

        mt5.shutdown()

    conn.close()
    print("\nBackfill concluído.")


if __name__ == "__main__":
    main()
