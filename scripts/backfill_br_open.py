"""
Backfill pontual das barras M5 do terminal BR (WIN$N/WDO$N/DI1$N).

O collector normal só puxa as 5 barras mais recentes por ciclo, então quando o
BR começa gated (sem --force) as barras da abertura (09:00–09:35) ficam de fora
e o engine ancora o win_open no preço errado. Este one-off puxa N barras M5
(cobrindo a sessão inteira) e reusa collect_recent_bars — que faz INSERT OR
IGNORE nas barras fechadas (só preenche buracos, não sobrescreve) e INSERT OR
REPLACE só na barra atual.

Rodar com o collector PARADO (MT5 aceita 1 conexão por terminal/processo).
Uso: py.exe -3.12 -X utf8 scripts/backfill_br_open.py [N_BARS]
"""

import os
import sys
import time
import MetaTrader5 as mt5

# Bootstrap do sys.path via __file__ (o py.exe do Windows ignora PYTHONPATH em
# path WSL) — mesmo padrão do collector_wsl.py. Script está em scripts/, então
# a raiz do projeto é 2 níveis acima.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db import get_connection, DB_PATH
from backend.workers.collector_wsl import TERMINALS, collect_recent_bars

N_BARS = int(sys.argv[1]) if len(sys.argv) > 1 else 90  # ~7.5h de M5

def main():
    br = next(t for t in TERMINALS if t.get("is_br"))
    conn = get_connection(DB_PATH)
    print(f"DB: {DB_PATH} | terminal: {br['name']} | n_bars={N_BARS}")

    try:
        mt5.shutdown()
    except Exception:
        pass
    time.sleep(0.5)

    if not mt5.initialize(path=br["path"], portable=True, timeout=15000):
        print("FALHA ao inicializar MT5 BR:", mt5.last_error())
        return 1

    total = 0
    for mt5_sym, canonical, source in br["symbols"]:
        mt5.symbol_select(mt5_sym, True)
        inserted = collect_recent_bars(mt5_sym, canonical, source, conn, n_bars=N_BARS)
        print(f"  {canonical:<8} +{inserted} barras")
        total += inserted

    mt5.shutdown()
    print(f"TOTAL backfill: {total} barras")
    return 0

if __name__ == "__main__":
    sys.exit(main())
