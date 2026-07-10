"""
IRAI — Worker de coleta em tempo real (setup WSL, escopo WIN$N/WDO$N).

Variante de collector.py rodando em 2 terminais MT5 dedicados no host WSL
(ssh brenoperucchi@192.168.0.240), em vez dos 3 terminais originais
(BR/XP + Tickmill + Axi) na máquina Windows de produção. Objetivo: coletar
apenas o necessário para os modelos WIN$N/WDO$N, pausando os outros 18
ativos do projeto original.

Terminais:
  - irai            (XP/B3)         → WIN$N, WDO$N, DI1$N
  - irai_forex_axi  (Axi-US51-Live) → TODOS os fatores internacionais,
                                       incluindo os iShares de bond

O terminal Axi cobre 100% do basket internacional de WIN$N/WDO$N — inclusive
os 2 iShares que só existiam no Axi de produção (iSharesTreasury1-3+ / SHY e
iSharesCurrencyBond+ / LEMB). Por isso NÃO há gap e NÃO é preciso recalibrar:
os pesos já calibrados (FACTOR_MAP.md) continuam válidos. (Verificado em
2026-07-09: os 9 fatores retornam barras M5 com preço vivo.)

A Axi usa sufixos de broker nos tickers de índice/FX/metal (".sa" para cash,
".fs" para futuro), então cada símbolo carrega uma tupla
(ticker_na_axi, nome_canônico, source). Os iShares têm nome idêntico ao
canônico. `source` preserva a proveniência original do schema
(iShares → "axi"; índices/FX/gold/VIX → "tickmill") — engine.py não filtra
por source, é apenas metadado, mas mantê-lo fiel evita surpresa em queries
de debug.

Uso: python backend/workers/collector_wsl.py [--interval 60] [--once]
"""

import MetaTrader5 as mt5
import sqlite3
import os
import sys
import time
import argparse
import logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backend.db import get_connection, DB_PATH

os.environ["PYTHONIOENCODING"] = "utf-8"

# ── Configuração ──────────────────────────────────────────
# Cada símbolo é uma tupla (ticker_na_broker, nome_canônico, source).
# nome_canônico = como grava em market_bars.symbol (o que engine.py espera).
# source ∈ ('br','tickmill','axi') — só metadado de proveniência (CHECK do schema).
TERMINALS = [
    {
        "name": "irai (BR/XP)",
        "path": r"E:\MetaTradersWSL\wdowin\irai\terminal64.exe",
        "is_br": True,
        "symbols": [
            ("WIN$N", "WIN$N", "br"),
            ("WDO$N", "WDO$N", "br"),
            ("DI1$N", "DI1$N", "br"),
        ],
    },
    {
        "name": "irai_forex_axi (Axi)",
        "path": r"E:\MetaTradersWSL\wdowin\irai_forex_axi\terminal64.exe",
        "is_br": False,
        "symbols": [
            # Índices / FX / metal / VIX — sufixo .sa (cash) ou .fs (futuro)
            ("US500.sa", "US500", "tickmill"),
            ("USTECH.sa", "USTEC", "tickmill"),
            ("GER40.sa", "DE40", "tickmill"),
            ("XAUUSD.sa", "XAUUSD", "tickmill"),
            ("USDCAD.sa", "USDCAD", "tickmill"),
            ("USDCHF.sa", "USDCHF", "tickmill"),
            ("VIX.fs", "VIX", "tickmill"),
            # iShares de bond — nome idêntico ao canônico
            ("iSharesTreasury1-3+", "iSharesTreasury1-3+", "axi"),
            ("iSharesCurrencyBond+", "iSharesCurrencyBond+", "axi"),
            # Fatores da cesta de produção do WIN ainda ausentes localmente
            # (nome canônico = symbol do feed de produção → slug = lower()).
            # Necessários p/ recalibrar o WIN com a cesta que a produção usa.
            ("BRENT.fs", "BRENT", "tickmill"),
            ("BTCUSD.sa", "BTCUSD", "tickmill"),
            ("CADCHF.sa", "CADCHF", "tickmill"),
            ("US30.sa", "US30", "tickmill"),
            ("USDMXN.sa", "USDMXN", "tickmill"),
        ],
    },
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("collector_wsl")


def compute_bar_delta(open_p, high, low, close, real_volume):
    """Aproximação de delta por posição de close na barra."""
    bar_range = high - low
    if bar_range <= 0 or real_volume <= 0:
        return 0.0
    close_pct = (close - low) / bar_range
    return real_volume * (2 * close_pct - 1)


def collect_recent_bars(
    mt5_symbol: str, canonical_symbol: str, source: str, conn: sqlite3.Connection, n_bars: int = 5
) -> int:
    """Coleta as N barras M5 mais recentes de mt5_symbol e grava sob canonical_symbol."""
    rates = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_M5, 0, n_bars)

    if rates is None or len(rates) == 0:
        return 0

    inserted = 0
    cursor = conn.cursor()

    for i, bar in enumerate(rates):
        ts = datetime.fromtimestamp(bar[0], tz=timezone.utc)
        ts_iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")

        o, h, l, c = float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4])
        tick_vol = float(bar[5]) if bar[5] else 0
        real_vol = float(bar[7]) if len(bar) > 7 and bar[7] else 0
        delta = compute_bar_delta(o, h, l, c, real_vol)

        is_current_bar = (i == len(rates) - 1)
        verb = "INSERT OR REPLACE" if is_current_bar else "INSERT OR IGNORE"

        try:
            cursor.execute(
                f"""{verb} INTO market_bars
                   (symbol, source, timeframe, timestamp_utc, open, high, low, close,
                    volume, real_volume, delta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (canonical_symbol, source, "M5", ts_iso, o, h, l, c,
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
    if h < 9 or (h == 9 and m < 55):
        return False
    if h > 18 or (h == 18 and m > 10):
        return False
    return True


def run_collection_cycle(conn: sqlite3.Connection, skip_br: bool = False) -> dict:
    """Executa um ciclo de coleta em todos os terminais."""
    results = {}

    for terminal in TERMINALS:
        if skip_br and terminal.get("is_br"):
            for _, canonical_sym, _ in terminal["symbols"]:
                results[canonical_sym] = {"status": "skipped", "error": "B3 fechada"}
            continue

        try:
            mt5.shutdown()
        except Exception:
            pass
        time.sleep(0.5)

        if not mt5.initialize(path=terminal["path"], portable=True, timeout=15000):
            error = mt5.last_error()
            log.warning(f"{terminal['name']}: falha na conexao: {error}")
            for _, canonical_sym, _ in terminal["symbols"]:
                results[canonical_sym] = {"status": "error", "error": str(error)}
            continue

        for mt5_sym, canonical_sym, source in terminal["symbols"]:
            # Garante que o símbolo está no Market Watch para a barra em
            # formação atualizar a cada ciclo (muitos vêm visible=False).
            mt5.symbol_select(mt5_sym, True)
            inserted = collect_recent_bars(mt5_sym, canonical_sym, source, conn)
            tick = mt5.symbol_info_tick(mt5_sym)
            bid = tick.bid if tick and tick.bid > 0 else 0
            results[canonical_sym] = {
                "status": "ok",
                "inserted": inserted,
                "bid": bid,
                "source": source,
                "mt5_symbol": mt5_sym,
            }

        mt5.shutdown()

    return results


def main():
    parser = argparse.ArgumentParser(description="IRAI - Collector worker (WSL, WIN$N/WDO$N)")
    parser.add_argument("--interval", type=int, default=60, help="Intervalo em segundos (default: 60)")
    parser.add_argument("--once", action="store_true", help="Executa apenas um ciclo")
    parser.add_argument("--force", action="store_true", help="Ignora verificacao de horario")
    parser.add_argument("--db", default=DB_PATH, help="Caminho do banco SQLite")
    args = parser.parse_args()

    log.info("=" * 50)
    log.info("IRAI Collector (WSL) v1.0 — escopo WIN$N/WDO$N")
    log.info(f"Intervalo: {args.interval}s | DB: {args.db}")
    log.info("=" * 50)

    cycle = 0

    while True:
        cycle += 1
        conn = get_connection(args.db)

        b3_open = args.force or is_b3_session()

        log.info(f"--- Ciclo {cycle} {'(B3 aberta)' if b3_open else '(apenas internacional)'} ---")
        results = run_collection_cycle(conn, skip_br=not b3_open)

        for sym, r in results.items():
            if r.get("status") == "skipped":
                continue
            if r["status"] == "ok":
                log.info(f"  {sym:<10} (mt5={r['mt5_symbol']:<8}) bid={r['bid']:<12.2f} +{r['inserted']} barras")
            else:
                log.warning(f"  {sym:<10} ERRO: {r.get('error', '?')}")

        if args.once:
            break

        try:
            import requests
            requests.post("http://127.0.0.1:8888/api/internal/notify_update", timeout=1.0)
        except Exception as e:
            log.debug(f"Falha ao notificar API local: {e}")

        conn.close()

        log.info(f"  Proximo ciclo em {args.interval}s...")
        time.sleep(args.interval)

    log.info("Collector encerrado.")


if __name__ == "__main__":
    main()
