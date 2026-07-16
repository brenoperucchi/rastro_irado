#!/usr/bin/env python3
"""Challenger Pair FIXO WIN-WDO (NF-01B / IRAI-4 AC#3, IRAI-21).

Metodologia CONGELADA em docs/plans/2026-07-16-challenger-pair-fixo-win-wdo.md
(escrita ANTES de qualquer resultado). Resumo:

O Pair Signal do dashboard é DINÂMICO (par = fator de maior |β| do Kalman). Este
challenger força o par a ser SEMPRE WIN↔WDO e o computa de forma INDEPENDENTE do
engine/Kalman/calibração — lê os preços dos dois símbolos direto de market_bars,
β por OLS rolling simples (janela 20, sem intercepto). Não sofre de C1-a (como os
baselines momentum/reversão). Reusa a MESMA entrada executável (open da barra
seguinte), custos, MFE/MAE OHLC e os 4 timestamps causais do Pair dinâmico via
`extract_trade_outcomes`, e a MESMA agregação (bootstrap/gate/sensibilidade) via
`run()` — SEM editar measure_pair_signal_value.py (que está em evolução no
IRAI-4): injeta um replay próprio por `patch` local, o mesmo mecanismo que os
testes já usam.

As DUAS únicas diferenças vs. o Pair dinâmico: (a) par FIXO (WDO) em vez do
maior-|β|; (b) β por OLS rolling em vez do Kalman. Todo o resto (janela 20,
threshold 1.5, z centrado sem √t, direção β-agnóstica) é o MESMO código de
backend/irai/zscore.py.

Uso:
  python3 -X utf8 scripts/measure_pair_fixed_value.py --db <path> --target WIN$N
  python3 -X utf8 scripts/measure_pair_fixed_value.py --db <path> --targets WIN$N WDO$N --output-json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.irai.engine import IRAISnapshot
from backend.irai.timezones import brt_to_tickmill_offset_hours
from backend.irai.zscore import (
    PAIR_SIGMA_WINDOW,
    PAIR_THRESHOLD,
    pair_signal,
    pair_zscore,
    pairwise_residual,
)

import scripts.measure_pair_signal_value as psv
from scripts.measure_pair_signal_value import (
    BOOTSTRAP_ITERATIONS,
    COMMON_LIMITATIONS,
    DEFAULT_DB,
    DEFAULT_SESSION_LIMIT,
    DEFAULT_TARGETS,
    RETROSPECTIVE_ONLY_LIMITATION,
    _print_report,
    run,
)
from scripts.measure_d1_inflation import readonly_connection


# Par fixo: cada target usa o OUTRO como fator (nunca escolhido pelo Kalman).
FIXED_FACTOR = {"WIN$N": "WDO$N", "WDO$N": "WIN$N"}


# ── Cálculo do par fixo (independente do engine) ────────────────────────────

def _rolling_ols_beta(ret_target: list[float], ret_factor: list[float]) -> float:
    """β sem intercepto na janela: Σ(t·f) / Σ(f²). <2 pontos ou Σf²≤0 ⇒ 0.0
    (nesse caso pair_signal trata β==0 como neutral)."""
    if len(ret_factor) < 2:
        return 0.0
    den = sum(f * f for f in ret_factor)
    if den <= 0:
        return 0.0
    return sum(t * f for t, f in zip(ret_target, ret_factor)) / den


def _load_session_bars(conn, symbol: str, session_date: str) -> dict:
    """Barras M5 de `symbol` no dia `session_date`, indexadas por timestamp_utc
    cru (B3/BRT). WIN$N e WDO$N vêm da mesma coleta, então seus timestamps
    coincidem por barra — o alinhamento é por igualdade exata."""
    start = f"{session_date}T00:00:00Z"
    end = (datetime.fromisoformat(session_date) + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    rows = conn.execute(
        "SELECT timestamp_utc, open, high, low, close FROM market_bars "
        "WHERE symbol=? AND timeframe='M5' AND timestamp_utc>=? AND timestamp_utc<? "
        "ORDER BY timestamp_utc",
        (symbol, start, end),
    ).fetchall()
    return {r["timestamp_utc"]: r for r in rows}


def build_fixed_pair_snapshots(conn, session_date: str, target: str) -> list:
    """Constrói snapshots sintéticos do TARGET com o marker do par fixo já
    estampado, prontos para `extract_trade_outcomes`. Timestamps deslocados
    +offset sazonal (eixo Tickmill) para casar com `is_b3=True` do run() e
    ficar comparável ao Pair dinâmico (mesma convenção de eixo)."""
    factor = FIXED_FACTOR[target]
    tbars = _load_session_bars(conn, target, session_date)
    fbars = _load_session_bars(conn, factor, session_date)
    common_ts = sorted(set(tbars) & set(fbars))
    if len(common_ts) < 2:
        return []
    t_open = float(tbars[common_ts[0]]["open"])
    f_open = float(fbars[common_ts[0]]["open"])
    if t_open <= 0 or f_open <= 0:
        return []

    offset = brt_to_tickmill_offset_hours(datetime.fromisoformat(f"{session_date}T12:00:00"))
    ret_t_hist: list[float] = []
    ret_f_hist: list[float] = []
    residuals: list[float] = []
    prev_sig: Optional[str] = None
    n = len(common_ts)
    snaps = []
    for bar_idx, ts in enumerate(common_ts):
        tb, fb = tbars[ts], fbars[ts]
        ret_t = (float(tb["close"]) - t_open) / t_open
        ret_f = (float(fb["close"]) - f_open) / f_open
        ret_t_hist.append(ret_t)
        ret_f_hist.append(ret_f)
        beta = _rolling_ols_beta(ret_t_hist[-PAIR_SIGMA_WINDOW:], ret_f_hist[-PAIR_SIGMA_WINDOW:])
        residuals.append(pairwise_residual(ret_t, beta, ret_f))
        z_pair = pair_zscore(residuals, PAIR_SIGMA_WINDOW)
        sig = pair_signal(z_pair, beta, PAIR_THRESHOLD)
        # Marker edge-triggered (só na transição), causal: usa dados até o
        # fechamento da barra bar_idx; a entrada real será no open da seguinte.
        compra = venda = None
        if sig == "buy" and prev_sig != "buy":
            compra = float(tb["close"])
        elif sig == "sell" and prev_sig != "sell":
            venda = float(tb["close"])
        prev_sig = sig

        tick_ts = (datetime.fromisoformat(ts.replace("Z", "")) + timedelta(hours=offset)).isoformat()
        snap = IRAISnapshot(
            timestamp=tick_ts, session_date=session_date, bar_idx=bar_idx,
            t_frac=(bar_idx + 1) / n, p_up=50.0, score=0.0, verdict="", verdict_color="",
        )
        snap.win_current = float(tb["close"])
        snap.win_bar_open = float(tb["open"])
        snap.win_high = float(tb["high"])
        snap.win_low = float(tb["low"])
        snap.is_ghost = False
        snap.pair_fixed_compra = compra
        snap.pair_fixed_venda = venda
        snap.pair_factor = FIXED_FACTOR[target].replace("$N", "").lower()
        snaps.append(snap)
    return snaps


def _pair_fixed_direction(snap) -> Optional[str]:
    if getattr(snap, "pair_fixed_compra", None) is not None:
        return "buy"
    if getattr(snap, "pair_fixed_venda", None) is not None:
        return "sell"
    return None


@contextmanager
def fixed_pair_replay(db_path: str):
    """Replay alternativo (mesmo contrato de chronological_replay: yields
    `(compute, instance)`), mas os snapshots vêm do market_bars com o par
    fixo, não do engine/Kalman. `instance=None` — não há engine a mutar
    (challenger não tem calibração, então pit_schedule nunca é usado)."""
    conn = readonly_connection(db_path)

    def compute(session_date: str, target: str):
        return build_fixed_pair_snapshots(conn, session_date, target)

    try:
        yield compute, None
    finally:
        conn.close()


LIMITATIONS = [
    "Challenger Pair FIXO WIN↔WDO: par sempre WIN-WDO (nunca escolhido pelo "
    "Kalman), computado INDEPENDENTE do engine/calibração (market_bars, β OLS "
    "rolling 20 sem intercepto). Não sofre de C1-a — é uma régua limpa contra "
    "o Pair dinâmico calibrado, no espírito da regra de negócio 8 (regra "
    "simples vs modelo complexo). β OLS é parâmetro de convenção, não "
    "otimizado; não reproduz o encadeamento do Kalman nem o warm-up de σ do "
    "par que o dinâmico tem no início da sessão (intencional).",
] + COMMON_LIMITATIONS + [RETROSPECTIVE_ONLY_LIMITATION]


def run_fixed(db_path: str, targets, limit: int, bootstrap: int) -> dict:
    """Roda o challenger reusando run() do módulo Pair, com o replay fixo
    injetado por patch local (mesmo mecanismo dos testes) — não edita
    measure_pair_signal_value.py."""
    with patch.object(psv, "chronological_replay", fixed_pair_replay):
        report = run(
            db_path, targets, limit, bootstrap, burn_in_sessions=0,
            direction_of=_pair_fixed_direction, limitations=LIMITATIONS,
            emit_events=True,
        )
    report["challenger"] = "pair_fixo_win_wdo"
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--target", choices=DEFAULT_TARGETS, default=None)
    parser.add_argument("--targets", nargs="+", choices=DEFAULT_TARGETS, default=list(DEFAULT_TARGETS))
    parser.add_argument("--limit", type=int, default=DEFAULT_SESSION_LIMIT)
    parser.add_argument("--bootstrap", type=int, default=BOOTSTRAP_ITERATIONS)
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()
    if args.target:
        args.targets = [args.target]
    return args


def main() -> int:
    args = parse_args()
    print(f"Challenger Pair FIXO WIN-WDO (IRAI-21) — banco: {args.db}")
    print(f"Alvos: {args.targets} · limite: {args.limit} · bootstrap: {args.bootstrap} "
          f"· β OLS rolling {PAIR_SIGMA_WINDOW} · threshold {PAIR_THRESHOLD}")
    print("Independente do engine/calibração — ver metodologia congelada em "
          "docs/plans/2026-07-16-challenger-pair-fixo-win-wdo.md")
    report = run_fixed(args.db, args.targets, args.limit, args.bootstrap)
    _print_report(report)
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nRelatório salvo em {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
