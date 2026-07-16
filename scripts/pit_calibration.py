#!/usr/bin/env python3
"""Calibração point-in-time (achado C1-a) para o backtest NF-01.

Contexto (docs/plans/2026-07-14-divergence-strategy-vs-tactical-layer.md §11.3/11.4):
os 3 scripts de backtest (measure_pair_signal_value.py, measure_price_divergence_
value.py, measure_intersection_value.py) usam, por padrão, os pesos/cesta ATUAIS
de produção aplicados retroativamente a todo o histórico do replay — a limitação
C1-a, documentada mas nunca resolvida em nenhuma medição anterior.

Este módulo fecha essa lacuna reaproveitando um mecanismo que já existe no
projeto para o walk-forward do MACRO layer (scripts/run_walkforward_macro.sh):
`scripts/calibrate_universal.py::calibrate_target(..., as_of=cutoff, dry_run)`
já recalibra usando só sessões <= cutoff, sem gravar no banco;
`scripts/measure_tactical_gate3.py::apply_calibration()` já injeta o resultado
num IRAIEngine em memória. Este módulo NÃO importa measure_tactical_gate3.py
(que puxa sklearn/scipy no topo do arquivo — os 3 scripts de marker evitam
essa dependência deliberadamente) — reimplementa apply_calibration aqui,
~10 linhas, e adiciona a injeção de `divergence_config.sigma` (achado C1-a,
lado preço) que apply_calibration original não cobre.

DECISÃO DE DESIGN — cesta FIXA entre folds, não rebuscada (revisado via 3
pareceres independentes: deep-reasoner, fable-reasoner e codex; os 2
primeiros convergiram nisso, codex discordou — ver docs/plans/2026-07-14-
divergence-strategy-vs-tactical-layer.md §11.5 para o registro completo):
`engine.py:685` só reaproveita o estado do Kalman encadeado (achado C1-b)
quando `factor_signature(cesta_atual) == factor_signature(cesta_salva)` —
uma cesta que muda de fold para fold quebraria silenciosamente o
encadeamento a cada fronteira. Por isso `build_schedule()` FORÇA a mesma
cesta (`FIXED_BASKETS`, história longa, sem iShares — mesma cesta de
scripts/run_walkforward_macro.sh) em todos os cutoffs e valida com
`assert` que a assinatura da cesta não varia entre eles.

LIMITAÇÃO ACEITA E DOCUMENTADA (não escondida): a cesta fixa é diferente da
cesta de produção real em cada momento histórico — os números point-in-time
medem os markers sobre essa cesta SUBSTITUTA, não a cesta exata que
apareceu no dashboard em cada dia. O braço "retrospectivo" (cesta atual de
produção, já medido e registrado em §11.3/11.4) continua disponível como
comparação — não é substituído por este módulo, roda em paralelo.

O QUE REALMENTE MUDA NAS FRONTEIRAS DE CUTOFF (achado do /codex-r, job
relay-mrmxnu54-iqj8x5): `_apply_calibration_local` troca `weights`/`sigmas`/
`alpha`/`intercept`/`factors` em `engine.models[slug]` — mas quando a
assinatura da cesta bate (sempre bate aqui, cesta fixa), `engine.py:685`
REAPROVEITA o estado do Kalman ENCADEADO (achado C1-b) em vez de usar o
`initial_state_mean` recém-montado a partir dos novos pesos. Ou seja: o
beta rastreado pelo Kalman (a estimativa dinâmica do hedge ratio, que É o
objeto medido pelo Pair Signal) segue evoluindo CONTINUAMENTE através das
trocas de calibração, sem reset — só os parâmetros ESTRUTURAIS (sigma do
z-score, alpha/intercept da calibração logística de `p_up`, a própria
cesta) são atualizados a cada cutoff. Isso é INTENCIONAL, não um efeito
colateral: resetar o Kalman a cada recalibração destruiria o propósito do
próprio achado C1-b (encadeamento contínuo).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db import factor_signature
from scripts import calibrate_universal as calibrator
from scripts.measure_d1_inflation import readonly_connection


# Mesma cesta de scripts/run_walkforward_macro.sh:37-38 — fatores com
# história longa (>=1000 sessões), sem iShares (existem só desde 2025) e sem
# USDCAD/USDCHF (começam em 2022-07, encurtariam a pista). Cada target usa o
# OUTRO índice BR como fator (cross-market), mantendo a separação doméstico/
# internacional documentada no CLAUDE.md do projeto.
FIXED_BASKETS = {
    "WIN$N": ["WDO$N", "DI1$N", "DE40", "US500", "VIX", "USTEC", "XAUUSD"],
    "WDO$N": ["WIN$N", "DI1$N", "DE40", "US500", "VIX", "USTEC", "XAUUSD"],
}

DEFAULT_HOLDOUT_SESSIONS = 50

# Cadência trimestral. Primeiro cutoff (2022-12-30) fica ~1 ano antes do
# início do walk-forward macro (2023-10-25): a cesta fixa tem interseção
# desde 2022-04-14, e calibrate_target exige >= 100 sessões de treino após
# o holdout (>= 150 sessões totais) — a ~21 sessões/mês, ~150 sessões dá
# ~2022-11; 2022-12-30 dá margem confortável. Cutoffs finais alinhados com
# os do walk-forward macro (2023-10-25 em diante) para permitir comparação
# direta entre os dois estudos. Datas de calendário, não precisam ser dia
# de pregão — truncate_daily_as_of só filtra "<=", não exige match exato.
DEFAULT_CUTOFFS = (
    "2022-12-30",
    "2023-03-31",
    "2023-06-30",
    "2023-09-29",
    "2023-10-25",  # alinhado ao 1º fold do walk-forward macro
    "2024-02-29",
    "2024-06-28",
    "2024-10-31",
    "2025-02-28",
    "2025-06-30",
    "2025-10-31",
    "2026-02-27",
)


def _std(values: list[float]) -> float:
    """Desvio-padrão populacional (ddof=0) — mesma convenção de np.std
    default, usada por scripts/calc_sigmas.py. Reimplementado sem numpy
    pra manter este módulo tão leve quanto os scripts de marker (que já
    evitam sklearn/scipy deliberadamente)."""
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return math.sqrt(var)


def div_sigma_as_of(conn, data_proxy: str, cutoff: str) -> float:
    """Réplica exata de scripts/calc_sigmas.py (first open/last close por
    sessão, desvio-padrão dos retornos diários), restrita a sessões
    <= cutoff — fecha o 3º mecanismo de contaminação C1-a (o único que o
    apply_calibration original, de measure_tactical_gate3.py, não cobre:
    ele nunca toca em divergence_config). Retorna o mesmo default 0.005
    de backend/irai/engine.py quando não há dados suficientes."""
    rows = conn.execute(
        "SELECT substr(timestamp_utc,1,10) AS session_date, open, close "
        "FROM market_bars WHERE symbol=? AND timeframe='M5' "
        "AND substr(timestamp_utc,1,10) <= ? ORDER BY timestamp_utc",
        (data_proxy, cutoff),
    ).fetchall()
    by_session: dict[str, list] = defaultdict(list)
    for row in rows:
        by_session[row["session_date"]].append((row["open"], row["close"]))
    daily_returns = []
    for bars in by_session.values():
        first_open = bars[0][0]
        last_close = bars[-1][1]
        if first_open and first_open > 0:
            daily_returns.append((last_close - first_open) / first_open)
    if len(daily_returns) < 5:
        return 0.005
    # 4 casas — mesmo arredondamento de scripts/calc_sigmas.py:48 (achado
    # do /codex-r: a versão anterior usava 6 casas, divergindo do valor
    # real que calc_sigmas.py grava em produção).
    return round(_std(daily_returns), 4)


def _apply_calibration_local(engine, target: str, result: dict) -> None:
    """Reimplementação de measure_tactical_gate3.py::apply_calibration —
    NÃO importada de lá (esse módulo puxa sklearn/scipy no topo do arquivo,
    os scripts de marker evitam essa dependência deliberadamente). Muta
    engine.models[slug] em memória; NÃO grava no banco (persist_state=False
    já garante isso no compute_from_db, mas esta função nem chega perto do
    banco)."""
    slug = engine.target_slugs[target]
    model = engine.models[slug]
    model["factors"] = list(result["factors"])
    model["factor_labels"] = dict(result["factor_labels"])
    model["weights"] = {f"w_{label}": value for label, value in result["weights"].items()}
    model["sigmas"] = dict(result["sigmas"])
    model["alpha"] = float(result["alpha"])
    model["intercept"] = float(result["intercept"])


@dataclass(frozen=True)
class PitEntry:
    cutoff: str
    calibration: dict
    div_sigma: float


class PitSchedule:
    """Calibração point-in-time por target, aplicada em memória dentro de um
    replay cronológico já em andamento (nunca reconstrói o IRAIEngine — só
    assim o encadeamento do Kalman entre sessões, achado C1-b, sobrevive à
    troca de calibração)."""

    def __init__(self, entries_by_target: dict[str, list[PitEntry]]):
        # Cada lista já vem ordenada ascendente por cutoff (build_schedule).
        self._entries = entries_by_target
        self._last_applied: dict[str, str] = {}

    def _active_entry(self, target: str, session_date: str) -> Optional[PitEntry]:
        active = None
        for entry in self._entries.get(target, []):
            if entry.cutoff < session_date:
                active = entry
            else:
                break
        return active

    def apply_for_session(self, engine, target: str, session_date: str) -> bool:
        """Retorna False se `session_date` é anterior ao 1º cutoff (sem
        calibração point-in-time OOS-válida ainda — sessão não mensurável).

        Mesmo nesse caso, a cesta FIXA já é aplicada usando a calibração do
        1º cutoff disponível (não a cesta default do banco) — achado do
        /codex-r: sem isso, as sessões pré-cutoff aqueceriam o Kalman numa
        cesta DIFERENTE da cesta fixa (a do banco), e a troca de cesta no
        exato instante em que o schedule vira "válido" dispararia um
        cold-restart silencioso do Kalman bem no início da janela medida
        (engine.py:685, assinatura da cesta mudando). Aplicar a calibração
        do 1º cutoff retroativamente às sessões de aquecimento é seguro
        porque elas NUNCA são medidas de qualquer forma — só o Kalman
        precisa estar "quente" na cesta certa quando a medição começa.

        Reaplica a calibração só quando o cutoff efetivamente usado muda
        (evita reatribuir os mesmos dicts a cada sessão)."""
        entries = self._entries.get(target, [])
        if not entries:
            return False
        active = self._active_entry(target, session_date)
        valid = active is not None
        entry = active if valid else entries[0]
        if self._last_applied.get(target) != entry.cutoff:
            _apply_calibration_local(engine, target, entry.calibration)
            slug = engine.target_slugs[target]
            engine.models[slug]["divergence_config"]["sigma"] = entry.div_sigma
            self._last_applied[target] = entry.cutoff
        return valid

    def cutoffs_used(self, target: str) -> list[str]:
        return [e.cutoff for e in self._entries.get(target, [])]


def build_schedule(
    db_path: str,
    targets: Iterable[str],
    cutoffs: Iterable[str] = DEFAULT_CUTOFFS,
    *,
    forced_baskets: dict[str, list[str]] = FIXED_BASKETS,
    holdout_sessions: int = DEFAULT_HOLDOUT_SESSIONS,
    calibrate_fn: Optional[Callable] = None,
) -> PitSchedule:
    """Pré-computa 1 artefato de calibração por (target, cutoff) ANTES do
    replay — calibrate_target() é caro o bastante (mesmo com cesta forçada,
    sem busca por força bruta) pra não valer a pena recalcular por sessão.

    `calibrate_fn` é injetável só pra teste (evita depender do sklearn real,
    ausente no ambiente Linux de dev — sklearn só é importado DENTRO de
    calibrate_target, lazy, não no topo do módulo calibrate_universal.py,
    mas ainda assim não está instalado aqui). Produção sempre usa o default
    (`calibrator.calibrate_target`).
    """
    calibrate_fn = calibrate_fn or calibrator.calibrate_target
    cutoffs = sorted(cutoffs)
    entries_by_target: dict[str, list[PitEntry]] = {}

    conn = readonly_connection(db_path)
    try:
        for target in targets:
            row = conn.execute(
                "SELECT session_start_h, session_end_h, data_proxy FROM asset_models WHERE target=?",
                (target,),
            ).fetchone()
            s_start = (row["session_start_h"] if row else None) or 0
            s_end = (row["session_end_h"] if row else None) or 24
            proxy = row["data_proxy"] if row else None
            data_sym = proxy or target
            forced = forced_baskets[target]

            # Carrega os retornos diários UMA VEZ (todo o histórico
            # disponível) e reusa via daily_override em cada cutoff —
            # evita N consultas redundantes ao banco (calibrate_target
            # normalmente re-carrega do zero a cada chamada).
            full_daily = calibrator.load_daily_returns(conn, s_start, s_end, data_sym)

            entries: list[PitEntry] = []
            signatures: set[str] = set()
            for cutoff in cutoffs:
                result = calibrate_fn(
                    conn, target, s_start, s_end, proxy,
                    min_factors=len(forced), max_factors=len(forced),
                    forced_factors=forced, holdout_sessions=holdout_sessions,
                    as_of=cutoff, daily_override=full_daily,
                )
                if result is None:
                    continue  # dados insuficientes ainda pra este cutoff — pulado, não é erro
                # Verifica não só consistência ENTRE cutoffs, mas que a
                # cesta devolvida é EXATAMENTE a forçada (achado do
                # /codex-r: o assert original só pegaria uma cesta
                # errada-mas-consistente entre folds, não uma cesta
                # sistematicamente errada em todos eles).
                assert result["factors"] == forced, (
                    f"calibrate_fn devolveu uma cesta diferente da forçada pra {target} @ "
                    f"{cutoff}: {result['factors']} != {forced}. forced_factors deveria "
                    "bypassar qualquer busca e devolver a cesta exata, na mesma ordem.")
                sig = factor_signature(result["factors"])
                signatures.add(sig)
                sigma = div_sigma_as_of(conn, data_sym, cutoff)
                entries.append(PitEntry(cutoff=cutoff, calibration=result, div_sigma=sigma))

            assert len(signatures) <= 1, (
                f"cesta forçada variou entre cutoffs pra {target} (esperado: sempre a mesma "
                f"ordem/composição — {len(signatures)} assinaturas distintas encontradas). "
                "Isso quebraria o encadeamento do Kalman (achado C1-b) nas fronteiras de fold "
                "silenciosamente — não prossiga sem investigar.")
            entries_by_target[target] = entries
    finally:
        conn.close()

    return PitSchedule(entries_by_target)
