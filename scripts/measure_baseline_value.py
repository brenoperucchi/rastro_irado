#!/usr/bin/env python3
"""NF-01 — baselines simples (momentum e reversão) como eventos reproduzíveis.

Contexto (docs/plans/2026-07-14-divergence-strategy-vs-tactical-layer.md §11,
item 6: "Pair + NWE + VWAP/ATR versus baselines simples de momentum e
reversão"; backlog IRAI-2 AC #3: "Pair, Z e baselines previstos geram eventos
reproduzíveis por sessão"):

Os markers Pair/Z do dashboard são essencialmente sinais de REVERSÃO À MÉDIA
(distorção -> convergência). Pra saber se eles agregam algo além do trivial, é
preciso compará-los contra baselines que NÃO usam nenhum modelo calibrado — só
o preço do próprio WIN. Este script GERA esses eventos de baseline de forma
reproduzível; a AVALIAÇÃO ECONÔMICA comparativa (custos, frequência comparável
e sensibilidade) pertence ao IRAI-4/VAL-04, NÃO a este módulo. A primitiva
compartilhada já usa o open M5 seguinte como proxy do primeiro preço executável.

DEFINIÇÃO DOS BASELINES — CONGELADA, não otimizada (senão deixaria de ser
baseline honesto e viraria data-snooping):
  Cruzamento de médias móveis simples (SMA) sobre `win_current`, edge-
  triggered (dispara só no cruzamento, como os markers Pair/Z):
    fast = SMA(BASELINE_FAST barras), slow = SMA(BASELINE_SLOW barras)
    - MOMENTUM: fast cruza ACIMA de slow -> "buy" (segue a tendência que
      inicia); cruza abaixo -> "sell".
    - REVERSÃO: o inverso (fast cruza acima -> "sell", apostando que já subiu
      demais; cruza abaixo -> "buy").
  BASELINE_FAST=6 (30min) e BASELINE_SLOW=20 (100min, == janela do Pair) são
  valores redondos de convenção, NÃO ajustados a nenhum resultado.

INVARIÂNCIA AO MODO DE CALIBRAÇÃO (por que serve de baseline): os baselines
usam SÓ `win_current` (preço do próprio target), que não depende de p_up/
Pair/Z nem de nenhum parâmetro calibrado. Logo rodar com ou sem
`--point-in-time` produz o MESMO resultado de baseline — é exatamente essa
imunidade a C1-a que os torna uma régua limpa contra os markers calibrados.
A flag `--point-in-time` é aceita só por simetria de interface (e porque o
Kalman ainda encadeia no replay), mas não muda os eventos de baseline.

REUSA a metodologia inteira de scripts/measure_pair_signal_value.py (entrada
na barra seguinte, cooldown, MFE/MAE clampado, horizontes truncados na
sessão, bootstrap por sessão, os 4 timestamps causais) via
`preprocess=_mark_*` + `direction_of=_*_direction` — não duplica nada.

Uso:
  python3 -X utf8 scripts/measure_baseline_value.py --db <path> --baseline momentum
  python3 -X utf8 scripts/measure_baseline_value.py --db <path> --baseline reversao --targets WIN$N WDO$N --output-json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.measure_pair_signal_value import (
    DEFAULT_BURN_IN_SESSIONS,
    DEFAULT_DB,
    DEFAULT_SESSION_LIMIT,
    DEFAULT_TARGETS,
    BOOTSTRAP_ITERATIONS,
    COMMON_LIMITATIONS,
    POINT_IN_TIME_LIMITATIONS,
    RETROSPECTIVE_ONLY_LIMITATION,
    _real_snapshots,
    run,
    _print_report,
)

BASELINE_FAST = 6    # 30 min — janela curta da SMA
BASELINE_SLOW = 20   # 100 min — janela longa (== PAIR_SIGMA_WINDOW), por convenção


def _sma(values: list[float], k: int) -> Optional[float]:
    if len(values) < k:
        return None
    return sum(values[-k:]) / k


def _mark_crossover(snapshots, *, momentum: bool) -> None:
    """Estampa `baseline_compra`/`baseline_venda` na barra em que a SMA rápida
    CRUZA a lenta (edge-triggered). `momentum=True` segue o cruzamento;
    `momentum=False` (reversão) inverte a direção. Causal: a SMA na barra i
    usa closes até a barra i inclusive (disponíveis no fechamento da barra i),
    e a entrada acontece na barra seguinte (extract_trade_outcomes)."""
    real = _real_snapshots(snapshots)
    closes: list[float] = []
    prev_state = None  # "above" | "below" | None
    for snap in real:
        closes.append(float(snap.win_current))
        snap.baseline_compra = None
        snap.baseline_venda = None
        fast = _sma(closes, BASELINE_FAST)
        slow = _sma(closes, BASELINE_SLOW)
        if fast is None or slow is None:
            continue
        state = "above" if fast > slow else "below"
        crossed_up = state == "above" and prev_state == "below"
        crossed_down = state == "below" and prev_state == "above"
        # momentum: cruzou pra cima -> buy; reversão: cruzou pra cima -> sell.
        if crossed_up:
            if momentum:
                snap.baseline_compra = float(snap.win_current)
            else:
                snap.baseline_venda = float(snap.win_current)
        elif crossed_down:
            if momentum:
                snap.baseline_venda = float(snap.win_current)
            else:
                snap.baseline_compra = float(snap.win_current)
        prev_state = state


def _mark_momentum(snapshots) -> None:
    _mark_crossover(snapshots, momentum=True)


def _mark_reversao(snapshots) -> None:
    _mark_crossover(snapshots, momentum=False)


def _baseline_direction(snap) -> Optional[str]:
    """Lê os campos estampados por `_mark_momentum`/`_mark_reversao`."""
    if getattr(snap, "baseline_compra", None) is not None:
        return "buy"
    if getattr(snap, "baseline_venda", None) is not None:
        return "sell"
    return None


BASELINES = {"momentum": _mark_momentum, "reversao": _mark_reversao}


def _limitations(baseline: str, point_in_time: bool) -> list:
    head = [
        f"Baseline '{baseline}': cruzamento de SMA({BASELINE_FAST}) x SMA("
        f"{BASELINE_SLOW}) sobre o preço do WIN, edge-triggered. É uma RÉGUA "
        "reproduzível pra comparar contra os markers Pair/Z — NÃO um setup "
        "proposto. Os parâmetros (6/20) são convenção redonda, não otimizados.",
        "Este script só GERA eventos reproduzíveis do baseline. A comparação "
        "econômica (líquida de custo completo, frequência comparável e "
        "sensibilidade a parâmetros) contra os markers pertence ao "
        "IRAI-4/VAL-04 — não é feita aqui.",
        "O baseline usa só `win_current` (preço do target), sem nenhum "
        "parâmetro calibrado — logo é INVARIANTE ao modo point-in-time vs "
        "retrospectivo (C1-a não o afeta). É essa imunidade que o torna uma "
        "régua limpa contra os markers calibrados.",
    ]
    tail = ([RETROSPECTIVE_ONLY_LIMITATION] if not point_in_time
            else POINT_IN_TIME_LIMITATIONS)
    return head + COMMON_LIMITATIONS + tail


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--baseline", choices=sorted(BASELINES), default="momentum",
                         help="Qual baseline gerar (default: momentum).")
    parser.add_argument("--target", choices=DEFAULT_TARGETS, default=None,
                         help="Um único target (atalho pra --targets X).")
    parser.add_argument("--targets", nargs="+", choices=DEFAULT_TARGETS, default=list(DEFAULT_TARGETS))
    parser.add_argument("--limit", type=int, default=DEFAULT_SESSION_LIMIT,
                         help="Nº de sessões mais recentes a replayar (default: %(default)s).")
    parser.add_argument("--bootstrap", type=int, default=BOOTSTRAP_ITERATIONS)
    parser.add_argument("--burn-in-sessions", type=int, default=DEFAULT_BURN_IN_SESSIONS)
    parser.add_argument("--point-in-time", action="store_true",
                         help="Aceito por simetria de interface; NÃO muda os eventos de "
                              "baseline (invariante à calibração — ver docstring).")
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()
    if args.target:
        args.targets = [args.target]
    return args


def main() -> int:
    args = parse_args()
    print(f"Baseline '{args.baseline}' (NF-01, AC #3) — banco: {args.db}")
    print(f"Alvos: {args.targets} · limite de sessões: {args.limit} · bootstrap: {args.bootstrap} "
          f"· SMA {BASELINE_FAST}x{BASELINE_SLOW}")
    print("Só GERA eventos reproduzíveis — avaliação econômica comparativa é IRAI-4/VAL-04.")
    pit_schedule = None
    if args.point_in_time:
        import scripts.pit_calibration as pit_calibration
        print("Modo point-in-time aceito, mas o baseline é invariante à calibração "
              "(usa só o preço do WIN) — resultado idêntico ao retrospectivo.")
        pit_schedule = pit_calibration.build_schedule(args.db, args.targets)
    report = run(args.db, args.targets, args.limit, args.bootstrap, args.burn_in_sessions,
                 direction_of=_baseline_direction,
                 limitations=_limitations(args.baseline, args.point_in_time),
                 preprocess=BASELINES[args.baseline], pit_schedule=pit_schedule)
    report["baseline"] = args.baseline
    _print_report(report)
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nRelatório salvo em {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
