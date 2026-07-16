#!/usr/bin/env python3
"""NF-01 item 3 — Interseção Pair Signal + divergência macro-preço (marker
`Z`) tem valor OOS líquido de custo?

Contexto (docs/plans/2026-07-14-divergence-strategy-vs-tactical-layer.md
§11, item 3: "Interseção Pair + divergência macro"; itens 1 e 2 medidos em
scripts/measure_pair_signal_value.py e scripts/measure_price_divergence_
value.py, commits 496f739/784d2f6, registrados em §11.1/§11.2).

DEFINIÇÃO DA INTERSEÇÃO — CONGELADA antes de rodar ou olhar qualquer
resultado, por instrução explícita do usuário (evitar overfitting da
definição aos dados): primeira barra fechada em que `pair_signal ==
price_diverge_dir` E ambos não-`None` (mesma direção) — NÃO exige que os
markers discretos `pair_compra`/`pair_venda`/`z_compra_val`/`z_venda_val`
transicionem na MESMA barra, só que os dois ESTADOS contínuos estejam
alinhados na mesma direção. Ver `_mark_intersection` abaixo.

METODOLOGIA IDÊNTICA à dos itens 1/2 (entrada na barra seguinte ao sinal,
cooldown, MFE/MAE clampado, horizontes truncados na sessão, bootstrap
clusterizado por sessão, Kalman encadeado — achado C1-b): reusa `run()`/
`_print_report()`/`COMMON_LIMITATIONS` de measure_pair_signal_value.py via
`direction_of=_intersection_direction` e `preprocess=_mark_intersection`,
não duplicada.

GATE DE AMOSTRA MÍNIMA (docs/plans/2026-07-13-irai-tactical-layer-win-wdo.md
§7.3, "pelo menos 100 eventos confirmados para o gate econômico" — ver
`MIN_EVENTS_FOR_GATE` importado de measure_pair_signal_value.py): a
interseção de dois markers já individualmente raros (62 eventos em WIN$N e
41 em WDO$N pro marker Z sozinho, numa janela de 300 sessões — §11.2) é
necessariamente MAIS rara ainda. Se `n_events < 100` (agregado "all") pra
um alvo, `run()` já rotula esse alvo como `gate_verdict = "INCONCLUSIVO"`
no relatório — não descartado, só não interpretado como edge/no-edge.

JANELA DE REPLAY: por instrução explícita do usuário, este script deve ser
rodado com `--limit` grande o bastante pra cobrir TODA a base elegível
(não só as sessões mais recentes) ANTES de qualquer inspeção de resultado
— nenhuma janela deve ser escolhida ou ajustada depois de ver os números,
sob risco de overfitting da janela aos dados. Os itens 1 e 2 também devem
ser re-rodados na MESMA janela expandida pra permitir comparação de
estabilidade por período (ver `by_year_h6_mean` no relatório).

C1-a (calibração in-sample) aqui HERDA os dois mecanismos já documentados
nos itens 1 e 2 (contaminação via `p_up`/calibração da cesta E via
`target_div_sigma`), porque a interseção exige ambos os markers alinhados
— nenhum mecanismo novo, mas os dois já existentes se somam.

CAUSALIDADE: `pair_signal` e `price_diverge_dir` (os campos contínuos que
`_mark_intersection` lê) são computados pelo engine em TODA barra,
inclusive potencialmente a última de uma sessão ainda "em formação" ao
vivo (achado X3) — diferente dos markers discretos `pair_compra`/
`z_compra_val`, que já são gated por `not bar_may_be_forming`. Este script
NÃO adiciona esse gate explicitamente porque há DUAS garantias
independentes de que nunca é necessário aqui (revisado via /codex-r, job
relay-mrmv6awy-phl3u0): (1) `candidate_sessions()` (scripts/
measure_d1_inflation.py:203-236) descarta incondicionalmente a sessão mais
recente ("potencialmente parcial") antes de qualquer sessão chegar em
`run()`/`_mark_intersection`; (2) mesmo que uma sessão histórica chegasse
aqui, `bar_may_be_forming` (backend/irai/engine.py:769) exige idade da
barra `< BAR_FORMING_MAX_AGE` (10min) em relação ao relógio ATUAL — uma
barra de 2021-2025 nunca passa nesse teste, independente de ser "a última"
processada. Se este script for reusado num contexto que NÃO filtra a
sessão corrente via candidate_sessions, a garantia (2) sozinha ainda
protege — mas revisite este raciocínio se `BAR_FORMING_MAX_AGE` ou a
lógica de `bar_may_be_forming` mudarem.

Uso:
  python3 -X utf8 scripts/measure_intersection_value.py --db <path> --target WIN$N
  python3 -X utf8 scripts/measure_intersection_value.py --db <path> --targets WIN$N WDO$N --limit 5000 --output-json out.json
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
    FORWARD_HORIZONS,
    MIN_EVENTS_FOR_GATE,
    _real_snapshots,
    run,
    _print_report,
)


def _mark_intersection(snapshots) -> None:
    """Pré-processa a lista de snapshots de UMA sessão (mesma filtragem de
    barras reais que extract_trade_outcomes usa internamente) e ESTAMPA
    `snap.intersect_compra`/`snap.intersect_venda` na PRIMEIRA barra em que
    `pair_signal == price_diverge_dir` (mesma direção, ambos não-`None`) —
    mimetiza o padrão prev_pair_sig/prev_div_dir de backend/irai/engine.py
    (linhas ~964-975) pra manter o mesmo formato de transição discreta que
    _pair_direction/_divergence_direction já sabem ler, sem exigir que os
    dois markers originais transicionem na mesma barra.

    Roda ANTES de qualquer burn-in/extração (`run()` chama isto via
    `preprocess=`), então estampa em TODAS as sessões replayadas, inclusive
    as de burn-in — inofensivo, só desperdiça um cálculo pequeno."""
    real = _real_snapshots(snapshots)
    prev_aligned = None
    for snap in real:
        pair_dir = getattr(snap, "pair_signal", None)
        z_dir = getattr(snap, "price_diverge_dir", None)
        aligned = pair_dir if (pair_dir is not None and pair_dir == z_dir) else None
        snap.intersect_compra = (
            float(snap.win_current) if (aligned == "buy" and prev_aligned != "buy") else None
        )
        snap.intersect_venda = (
            float(snap.win_current) if (aligned == "sell" and prev_aligned != "sell") else None
        )
        prev_aligned = aligned


def _intersection_direction(snap) -> Optional[str]:
    """Lê os campos estampados por `_mark_intersection`. Só retorna algo
    diferente de `None` se `_mark_intersection` já rodou pra esta sessão —
    `run()` sempre chama `preprocess` antes de `extract_trade_outcomes`,
    então isso vale pra qualquer uso via `run(..., preprocess=
    _mark_intersection, direction_of=_intersection_direction)`."""
    if getattr(snap, "intersect_compra", None) is not None:
        return "buy"
    if getattr(snap, "intersect_venda", None) is not None:
        return "sell"
    return None


LIMITATIONS = [
    "C1-a (calibração in-sample) na interseção: herda os dois mecanismos "
    "já documentados nos itens 1 e 2 — contaminação via `p_up` (cesta "
    "selecionada por acurácia/R², pesos/sigmas/calibração logística "
    "refeitos sobre TODO o histórico em `scripts/calibrate_universal.py`) "
    "E via `target_div_sigma` (`scripts/calc_sigmas.py`, também sobre todo "
    "o histórico disponível). A interseção exige AMBOS os markers "
    "alinhados, então os dois vieses se somam, não se cancelam.",
    "A definição de interseção usada aqui (`pair_signal == price_diverge_dir` "
    "na primeira barra alinhada) foi CONGELADA antes de rodar ou olhar "
    "qualquer resultado — não houve otimização de janela/definição "
    "após ver os dados.",
    "Causalidade: `pair_signal`/`price_diverge_dir` (campos contínuos que "
    "`_mark_intersection` lê) não são gated por `bar_may_be_forming` no "
    "engine, diferente dos markers discretos `pair_compra`/`z_compra_val`. "
    "Este script depende de `candidate_sessions()` já excluir "
    "incondicionalmente a sessão mais recente/potencialmente parcial antes "
    "de qualquer sessão chegar aqui — ver docstring do módulo.",
] + COMMON_LIMITATIONS + [
    "by_pair_factor no relatório reflete qual fator do Pair Signal estava "
    "ativo no momento do evento de interseção — aqui SIM é o fator "
    "relevante (a interseção exige o Pair Signal alinhado), diferente do "
    "caso do marker Z isolado (item 2), onde era só metadado incidental.",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--target", choices=DEFAULT_TARGETS, default=None,
                         help="Um único target (atalho pra --targets X).")
    parser.add_argument("--targets", nargs="+", choices=DEFAULT_TARGETS, default=list(DEFAULT_TARGETS))
    parser.add_argument("--limit", type=int, default=DEFAULT_SESSION_LIMIT,
                         help="Nº de sessões mais recentes a replayar (default: %(default)s). "
                              "Use um valor grande (ex: 5000) pra cobrir toda a base elegível.")
    parser.add_argument("--bootstrap", type=int, default=BOOTSTRAP_ITERATIONS)
    parser.add_argument("--burn-in-sessions", type=int, default=DEFAULT_BURN_IN_SESSIONS,
                         help="Nº de sessões iniciais replayadas p/ esquentar o Kalman "
                              "encadeado, mas excluídas da medição (default: %(default)s).")
    parser.add_argument("--min-events-for-gate", type=int, default=MIN_EVENTS_FOR_GATE,
                         help="Amostra mínima (agregado 'all') pra não rotular o alvo "
                              "INCONCLUSIVO (default: %(default)s, docs/plans/"
                              "2026-07-13-irai-tactical-layer-win-wdo.md §7.3).")
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()
    if args.target:
        args.targets = [args.target]
    return args


def main() -> int:
    args = parse_args()
    print(f"Interseção Pair + Z (NF-01 item 3) — banco: {args.db}")
    print(f"Alvos: {args.targets} · limite de sessões: {args.limit} · bootstrap: {args.bootstrap} "
          f"· burn-in: {args.burn_in_sessions} sessões · mínimo p/ gate: {args.min_events_for_gate} eventos")
    print("Definição de interseção CONGELADA antes de olhar resultados — ver docstring deste módulo.")
    report = run(args.db, args.targets, args.limit, args.bootstrap, args.burn_in_sessions,
                 direction_of=_intersection_direction, limitations=LIMITATIONS,
                 preprocess=_mark_intersection, min_events_for_gate=args.min_events_for_gate)
    _print_report(report)
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nRelatório salvo em {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
