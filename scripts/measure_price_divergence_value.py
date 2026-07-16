#!/usr/bin/env python3
"""NF-01 item 2 — Divergência macro-preço (marker `Z`) isolada tem valor OOS
líquido de custo?

Contexto (docs/plans/2026-07-14-divergence-strategy-vs-tactical-layer.md
§11, item 2: "Divergência macro-preço isolada"; item 1, Pair Signal
isolado, foi medido em scripts/measure_pair_signal_value.py — commit
496f739 — e mostrou resultado neutro em WIN$N e edge NEGATIVO
estatisticamente significante em WDO$N).

O marker `Z COMPRA`/`Z VENDA` (transição discreta e causal de
`price_diverge_dir`, `backend/irai/engine.py`, campos `z_compra_val`/
`z_venda_val`) aparece no gráfico quando `P_up` está num extremo (>
p_up_gate_hi ou < p_up_gate_lo) E o retorno do próprio target não acompanhou
esse extremo (`price_diverge_z < -threshold` ou `> threshold`, comparação
estrita — engine.py:948-953). Como o Pair Signal, é tratado hoje como
observação de distorção, não setup aprovado — este script mede se, isolado,
tem edge econômico.

METODOLOGIA IDÊNTICA à de scripts/measure_pair_signal_value.py (reusa
`extract_trade_outcomes`, `run` e `_print_report` de lá inteiros, só
trocando QUAL marker dispara o evento via `direction_of=
_divergence_direction`) — não duplicada aqui de propósito: a lógica de
entrada na barra seguinte ao sinal, cooldown, clamp de MFE/MAE, horizontes
truncados na fronteira da sessão, bootstrap clusterizado por sessão e
Kalman encadeado cronologicamente (achado C1-b) já passou por 2 rodadas de
/codex-r naquele script (jobs relay-mrmo68io-7cg1ij e
relay-mrmoyhby-243kcx); reescrever equivaleria a reintroduzir o mesmo risco
sem o mesmo escrutínio.

C1-a (calibração in-sample) tem um caminho de contaminação DIFERENTE e mais
direto aqui do que no Pair Signal — não necessariamente "mais forte" em
magnitude (isso não foi quantificado), mas mais direto e com uma fonte a
mais. Revisão via /codex-r (job relay-mrmta8qe-g59z0c) corrigiu uma
formulação anterior imprecisa desta nota: `price_diverge_z` em si NÃO
depende de `p_up` (é só o z-score do retorno do target contra
`target_div_sigma`); quem depende de `p_up` é a direção discreta
`price_diverge_dir` (e portanto os markers `z_compra_val`/`z_venda_val`
medidos aqui), que exige `p_up` cruzando `p_up_gate_hi`/`lo` — e `p_up`, no
v2, tem os pesos atualizados causalmente pelo Kalman a partir de uma cesta
selecionada por acurácia/R² e pesos/sigmas/calibração logística iniciais
todos refeitos sobre o histórico completo (`calibrate_universal.py`, sem
corte point-in-time). Além disso — mecanismo que o Pair Signal NÃO tem —
`target_div_sigma` (o denominador de `price_diverge_z`) vem de
`scripts/calc_sigmas.py`, calculado sobre TODO o histórico disponível em
`market_bars` (sem filtro de data) e aplicado retroativamente a cada
sessão do replay. Ver LIMITATIONS abaixo.

Uso:
  python3 -X utf8 scripts/measure_price_divergence_value.py --db <path> --target WIN$N
  python3 -X utf8 scripts/measure_price_divergence_value.py --db <path> --targets WIN$N WDO$N --output-json out.json
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
    POINT_IN_TIME_LIMITATIONS,
    RETROSPECTIVE_ONLY_LIMITATION,
    run,
    _print_report,
)


def _divergence_direction(snap) -> Optional[str]:
    """Direção da transição causal da divergência macro-preço (marker `Z`)
    numa barra. Ver `backend/irai/engine.py` (campos `z_compra_val`/
    `z_venda_val`, já gated pelo achado X3 — nunca nasce de barra em
    formação, mesmo bloco de código que gera pair_compra/pair_venda)."""
    if getattr(snap, "z_compra_val", None) is not None:
        return "buy"
    if getattr(snap, "z_venda_val", None) is not None:
        return "sell"
    return None


# EXTRA_LIMITATIONS não depende do modo (retrospectivo vs. point-in-time) —
# é sobre a semântica de um campo do relatório, não sobre calibração.
EXTRA_LIMITATIONS = [
    "by_pair_factor no relatório reflete qual fator do PAIR SIGNAL estava "
    "ativo no momento do evento de divergência Z — é metadado descritivo "
    "(o Pair e o Z podem estar ativos ao mesmo tempo sem relação causal "
    "entre si), não o fator que disparou este evento.",
]

# 2 itens de C1-a próprios do marker Z (mecanismo de contaminação diferente
# do Pair Signal, ver docstring do módulo, corrigida via /codex-r job
# relay-mrmta8qe-g59z0c) — só relevantes no modo RETROSPECTIVO (default);
# no modo --point-in-time são substituídos por POINT_IN_TIME_LIMITATIONS
# (ver main()).
C1A_LIMITATIONS = [
    "C1-a (calibração in-sample) pro marker Z, lado P_up: quem depende de "
    "`p_up` é a direção discreta `price_diverge_dir` (não `price_diverge_z` "
    "em si, que é só o z-score do retorno contra `target_div_sigma`) — mas "
    "é exatamente essa direção que dispara os eventos medidos aqui. `p_up` "
    "(v2) tem os pesos atualizados causalmente pelo Kalman a partir de uma "
    "cesta selecionada por acurácia/R² e pesos/sigmas/calibração logística "
    "iniciais refeitos sobre TODO o histórico (`scripts/"
    "calibrate_universal.py`, sem corte point-in-time).",
    "C1-a pro marker Z, lado preço (mecanismo que o Pair Signal NÃO tem): "
    "`target_div_sigma`, denominador de `price_diverge_z`, vem de "
    "`scripts/calc_sigmas.py`, calculado sobre TODO o histórico disponível "
    "em `market_bars` (sem filtro de data) e aplicado retroativamente a "
    "cada sessão do replay — uma segunda fonte de contaminação in-sample "
    "independente da de `p_up`.",
]

# Sempre incluídas no relatório de saída (JSON e texto), nunca uma leitura
# do número deve tratar isto como confirmação de edge econômico sem essas
# ressalvas.
LIMITATIONS = (
    C1A_LIMITATIONS + COMMON_LIMITATIONS + [RETROSPECTIVE_ONLY_LIMITATION] + EXTRA_LIMITATIONS
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--target", choices=DEFAULT_TARGETS, default=None,
                         help="Um único target (atalho pra --targets X).")
    parser.add_argument("--targets", nargs="+", choices=DEFAULT_TARGETS, default=list(DEFAULT_TARGETS))
    parser.add_argument("--limit", type=int, default=DEFAULT_SESSION_LIMIT,
                         help="Nº de sessões mais recentes a replayar (default: %(default)s).")
    parser.add_argument("--bootstrap", type=int, default=BOOTSTRAP_ITERATIONS)
    parser.add_argument("--burn-in-sessions", type=int, default=DEFAULT_BURN_IN_SESSIONS,
                         help="Nº de sessões iniciais replayadas p/ esquentar o Kalman "
                              "encadeado, mas excluídas da medição (default: %(default)s).")
    parser.add_argument("--point-in-time", action="store_true",
                         help="Calibração point-in-time (achado C1-a) em vez dos pesos/cesta "
                              "atuais de produção — ver scripts/pit_calibration.py.")
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()
    if args.target:
        args.targets = [args.target]
    return args


def main() -> int:
    args = parse_args()
    print(f"Divergência macro-preço isolada, marker Z (NF-01 item 2) — banco: {args.db}")
    print(f"Alvos: {args.targets} · limite de sessões: {args.limit} · bootstrap: {args.bootstrap} "
          f"· burn-in: {args.burn_in_sessions} sessões")
    print("Kalman encadeado cronologicamente entre sessões (achado C1-b) — mesma metodologia "
          "de scripts/measure_pair_signal_value.py, ver docstring deste módulo.")
    pit_schedule = None
    limitations = LIMITATIONS
    if args.point_in_time:
        import scripts.pit_calibration as pit_calibration
        print("Modo POINT-IN-TIME ativo (achado C1-a) — construindo schedule de calibração...")
        pit_schedule = pit_calibration.build_schedule(args.db, args.targets)
        limitations = POINT_IN_TIME_LIMITATIONS + COMMON_LIMITATIONS + EXTRA_LIMITATIONS
    report = run(args.db, args.targets, args.limit, args.bootstrap, args.burn_in_sessions,
                 direction_of=_divergence_direction, limitations=limitations,
                 pit_schedule=pit_schedule)
    _print_report(report)
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nRelatório salvo em {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
