"""
IRAI — Worker de coleta em tempo real.

Conecta sequencialmente aos terminais MT5 (XP e Tickmill) a cada
intervalo configurado, coleta barras M5 mais recentes e insere no SQLite.

Projetado para rodar como serviço persistente durante o pregão B3.

Uso: python backend/workers/collector.py [--interval 60] [--once]
"""

import MetaTrader5 as mt5
import sqlite3
import os
import sys
import time
import argparse
import logging
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backend.db import get_connection, DB_PATH

os.environ["PYTHONIOENCODING"] = "utf-8"

# ── Configuração ──────────────────────────────────────────
TERMINALS = [
    {
        "name": "BR (XP)",
        "path": r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe",
        "source": "br",
        "symbols": ["WIN$N", "DOL$N", "DI1$N"],
    },
    {
        "name": "Tickmill",
        "path": r"C:\Program Files\Tickmill MT5 Terminal\terminal64.exe",
        "source": "tickmill",
        "symbols": ["VIX", "DXY", "BRENT"],
    },
]

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("collector")


def compute_bar_delta(open_p, high, low, close, real_volume):
    """Aproximação de delta por posição de close na barra."""
    bar_range = high - low
    if bar_range <= 0 or real_volume <= 0:
        return 0.0
    close_pct = (close - low) / bar_range  # 0..1
    return real_volume * (2 * close_pct - 1)


def collect_recent_bars(symbol: str, source: str, conn: sqlite3.Connection, n_bars: int = 5) -> int:
    """Coleta as N barras M5 mais recentes de um símbolo."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n_bars)

    if rates is None or len(rates) == 0:
        return 0

    inserted = 0
    cursor = conn.cursor()

    for bar in rates:
        ts = datetime.fromtimestamp(bar[0], tz=timezone.utc)
        ts_iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")

        o, h, l, c = float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4])
        tick_vol = float(bar[5]) if bar[5] else 0
        real_vol = float(bar[7]) if len(bar) > 7 and bar[7] else 0
        delta = compute_bar_delta(o, h, l, c, real_vol)

        try:
            cursor.execute(
                """INSERT OR IGNORE INTO market_bars
                   (symbol, source, timeframe, timestamp_utc, open, high, low, close,
                    volume, real_volume, delta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (symbol, source, "M5", ts_iso, o, h, l, c,
                 tick_vol, real_vol, delta),
            )
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    return inserted


def is_b3_session() -> bool:
    """Verifica se está dentro do horário do pregão B3 (09:55–18:10 BRT, margem)."""
    now = datetime.now()
    h, m = now.hour, now.minute
    # Pregão: 10:00-17:55 BRT, com margem de 5 min antes e 15 min depois
    if h < 9 or (h == 9 and m < 55):
        return False
    if h > 18 or (h == 18 and m > 10):
        return False
    return True


def run_collection_cycle(conn: sqlite3.Connection) -> dict:
    """Executa um ciclo de coleta em todos os terminais."""
    results = {}

    for terminal in TERMINALS:
        try:
            mt5.shutdown()
        except:
            pass
        time.sleep(0.5)

        if not mt5.initialize(path=terminal["path"], timeout=15000):
            error = mt5.last_error()
            log.warning(f"{terminal['name']}: falha na conexao: {error}")
            for sym in terminal["symbols"]:
                results[sym] = {"status": "error", "error": str(error)}
            continue

        for sym in terminal["symbols"]:
            inserted = collect_recent_bars(sym, terminal["source"], conn)
            tick = mt5.symbol_info_tick(sym)
            bid = tick.bid if tick and tick.bid > 0 else 0
            results[sym] = {
                "status": "ok",
                "inserted": inserted,
                "bid": bid,
                "source": terminal["source"],
            }

        mt5.shutdown()

    return results


def main():
    parser = argparse.ArgumentParser(description="IRAI - Collector worker")
    parser.add_argument("--interval", type=int, default=60, help="Intervalo em segundos (default: 60)")
    parser.add_argument("--once", action="store_true", help="Executa apenas um ciclo")
    parser.add_argument("--force", action="store_true", help="Ignora verificacao de horario")
    parser.add_argument("--db", default=DB_PATH, help="Caminho do banco SQLite")
    args = parser.parse_args()

    log.info("=" * 50)
    log.info("IRAI Collector v1.0")
    log.info(f"Intervalo: {args.interval}s | DB: {args.db}")
    log.info("=" * 50)

    conn = get_connection(args.db)
    cycle = 0

    while True:
        cycle += 1

        if not args.force and not is_b3_session():
            if args.once:
                log.info("Fora do horario de pregao. Use --force para forcar.")
                break
            log.debug(f"Fora do horario. Aguardando...")
            time.sleep(30)
            continue

        log.info(f"--- Ciclo {cycle} ---")
        results = run_collection_cycle(conn)

        for sym, r in results.items():
            if r["status"] == "ok":
                log.info(f"  {sym:<12} bid={r['bid']:<12.2f} +{r['inserted']} barras")
            else:
                log.warning(f"  {sym:<12} ERRO: {r.get('error', '?')}")

        if args.once:
            break

        log.info(f"  Proximo ciclo em {args.interval}s...")
        time.sleep(args.interval)

    conn.close()
    log.info("Collector encerrado.")


if __name__ == "__main__":
    main()
