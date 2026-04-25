"""
IRAI — Coleta histórica de barras M5 e D1 dos terminais MT5.

Conecta sequencialmente a cada terminal, puxa 1 ano de dados
e grava no SQLite.

Uso: python scripts/collect_history.py [--days 365] [--timeframe M5|D1|BOTH]
"""

import MetaTrader5 as mt5
import sqlite3
import os
import sys
import argparse
import time
from datetime import datetime, timezone, timedelta

# Adicionar raiz do projeto ao path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.db import get_connection, init_db, DB_PATH

os.environ["PYTHONIOENCODING"] = "utf-8"


# ── Configuração dos terminais e símbolos ──────────────────
TERMINALS = [
    {
        "name": "BR (XP)",
        "path": r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe",
        "source": "br",
        "symbols": ["WIN$N", "WDO$N", "DI1$N"],
    },
    {
        "name": "Tickmill",
        "path": r"C:\Program Files\Tickmill MT5 Terminal\terminal64.exe",
        "source": "tickmill",
        "symbols": ["VIX", "DXY", "BRENT", "US500", "BTCUSD"],
    },
]

# Mapeamento timeframe
TF_MAP = {
    "M5": mt5.TIMEFRAME_M5,
    "D1": mt5.TIMEFRAME_D1,
}


def collect_bars(symbol: str, source: str, timeframe_name: str, days: int, conn: sqlite3.Connection) -> int:
    """Coleta barras históricas de um símbolo e grava no banco."""
    tf = TF_MAP[timeframe_name]

    # Calcular quantas barras pedir
    if timeframe_name == "M5":
        bars_per_day = 288  # 24h * 12 barras/hora (mercado pode ter menos)
        max_bars = min(days * bars_per_day, 100000)
    else:  # D1
        max_bars = min(days + 50, 1000)  # margem extra

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, max_bars)

    if rates is None or len(rates) == 0:
        print(f"    {symbol:<12} {timeframe_name}: sem dados")
        return 0

    # Converter e inserir
    inserted = 0
    cursor = conn.cursor()

    for bar in rates:
        ts = datetime.fromtimestamp(bar[0], tz=timezone.utc)
        ts_iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            cursor.execute(
                """INSERT OR IGNORE INTO market_bars
                   (symbol, source, timeframe, timestamp_utc, open, high, low, close, volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (symbol, source, timeframe_name, ts_iso,
                 float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]),
                 float(bar[5]) if bar[5] else 0),
            )
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            pass  # Já existe

    conn.commit()

    # Stats
    oldest = datetime.fromtimestamp(rates[0][0], tz=timezone.utc)
    newest = datetime.fromtimestamp(rates[-1][0], tz=timezone.utc)
    span_days = (newest - oldest).days

    print(f"    {symbol:<12} {timeframe_name}: {len(rates):>6} barras lidas, "
          f"{inserted:>6} inseridas | {oldest.strftime('%Y-%m-%d')} -> {newest.strftime('%Y-%m-%d')} ({span_days}d)")

    return inserted


def main():
    parser = argparse.ArgumentParser(description="IRAI - Coleta historica MT5")
    parser.add_argument("--days", type=int, default=400, help="Dias de historico (default: 400)")
    parser.add_argument("--timeframe", choices=["M5", "D1", "BOTH"], default="BOTH",
                        help="Timeframe a coletar (default: BOTH)")
    parser.add_argument("--db", default=DB_PATH, help="Caminho do banco SQLite")
    args = parser.parse_args()

    timeframes = ["M5", "D1"] if args.timeframe == "BOTH" else [args.timeframe]

    print("=" * 70)
    print("  IRAI — Coleta Historica")
    print(f"  Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Dias: {args.days} | Timeframes: {', '.join(timeframes)}")
    print(f"  DB: {args.db}")
    print("=" * 70)

    # Inicializar banco
    init_db(args.db)
    conn = get_connection(args.db)

    total_inserted = 0

    for terminal in TERMINALS:
        print(f"\n--- {terminal['name']} ---")
        print(f"    Path: {terminal['path']}")

        # Garantir cleanup
        try:
            mt5.shutdown()
        except:
            pass
        time.sleep(1)

        if not mt5.initialize(path=terminal["path"], timeout=30000):
            error = mt5.last_error()
            print(f"    FALHA: {error}")
            continue

        info = mt5.terminal_info()
        if info:
            print(f"    Conectado: {info.company} build {info.build}")

        for symbol in terminal["symbols"]:
            for tf in timeframes:
                count = collect_bars(symbol, terminal["source"], tf, args.days, conn)
                total_inserted += count

        mt5.shutdown()
        time.sleep(1)

    conn.close()

    # Resumo
    print(f"\n{'='*70}")
    print(f"  COLETA CONCLUIDA")
    print(f"  Total de barras inseridas: {total_inserted}")
    print(f"  Banco: {args.db}")
    print(f"{'='*70}")

    # Verificar contagens por símbolo
    conn2 = get_connection(args.db)
    cursor = conn2.execute("""
        SELECT symbol, timeframe, COUNT(*) as cnt,
               MIN(timestamp_utc) as oldest, MAX(timestamp_utc) as newest
        FROM market_bars
        GROUP BY symbol, timeframe
        ORDER BY symbol, timeframe
    """)
    print(f"\n  {'Simbolo':<12} {'TF':<4} {'Barras':>8} {'De':>12} {'Ate':>12}")
    print(f"  {'-'*52}")
    for row in cursor:
        oldest = row["oldest"][:10] if row["oldest"] else "?"
        newest = row["newest"][:10] if row["newest"] else "?"
        print(f"  {row['symbol']:<12} {row['timeframe']:<4} {row['cnt']:>8} {oldest:>12} {newest:>12}")
    conn2.close()


if __name__ == "__main__":
    main()
