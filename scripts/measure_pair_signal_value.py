#!/usr/bin/env python3
"""NF-01 (escopo mínimo) — Pair Signal isolado tem valor OOS líquido de custo?

Contexto (docs/plans/2026-07-13-irai-plano-consolidado.md, NF-01; achado de
risco #1: "Markers ainda não são setup aprovado. Nome e cor podem induzir
ação antes de existir backtest econômico específico do Pair/Z"):

O marker `P COMPRA`/`P VENDA` (transição do Pair Signal, `backend/irai/
zscore.py::pair_signal`) já aparece no gráfico hoje, mas NUNCA foi validado
como algo com valor econômico — ele indica uma DISTORÇÃO (regra de negócio
#4 do plano), não um setup aprovado. Este script mede, evento a evento
(cada transição do sinal), se o retorno forward líquido de custo tem edge
estatístico, seguindo a receita do item 1 de
docs/plans/2026-07-14-divergence-strategy-vs-tactical-layer.md §11:

  "1. Pair Signal isolado (pair_z cruzando o threshold)."

ESCOPO DELIBERADAMENTE MÍNIMO — o que este script NÃO faz (fica para as
próximas fatias do NF-01, itens 2-6 do §11):
  - Não mede a divergência macro-preço (marker Z) isoladamente nem em
    interseção com o Pair.
  - Não condiciona o resultado ao regime de P_up nem à direção/região do
    NWE (itens 4-5 do §11).
  - Não compara contra baselines de momentum/reversão (item 6) — só contra
    a taxa-base (zero de retorno líquido) e win-rate vs. 50%.
  - Não recalibra o modelo point-in-time (achado C1-a, perna de calibração
    in-sample) — usa os pesos calibrados ATUAIS de produção, como estão,
    igual ao walk-forward do macro (VAL-01/§3.7) fez. Isso é uma limitação
    aceita e documentada, EXPLICITAMENTE NÃO NEUTRALIZADA: o Pair Signal em
    si não tem "fit" supervisionado sobre o threshold, mas a ESCOLHA do
    fator ativo do par vem de scripts/calibrate_universal.py, que seleciona
    a cesta por acurácia/R² sobre um split treino/holdout temporal (linhas
    315-404) — o holdout fica de fora da escolha — mas o REFIT FINAL dos
    pesos usados em produção (`model_all`, linha 443) é sobre TODO o
    histórico (`merged_all`, incluindo o próprio holdout). É esse artefato
    final (cesta + pesos refeitos no histórico inteiro) que este script
    aplica retroativamente a cada sessão do replay, inclusive sessões
    anteriores à janela mais recente de calibração — o que pode
    retrospectivamente favorecer um fator cujo resíduo pareça mais
    mean-reverting contra o alvo do que seria observável em tempo real.
    Revisão via /codex-r (jobs relay-mrmo68io-7cg1ij e
    relay-mrmoyhby-243kcx) apontou que a formulação anterior desta nota
    subestimava esse mecanismo e depois corrigiu uma imprecisão factual
    (a seleção da cesta NÃO é sobre "todo o histórico", é o refit final que
    é); um resultado positivo deste script deve ser lido como evidência
    PRELIMINAR, não como confirmação de edge OOS genuíno. Ver seção
    "LIMITAÇÕES" no relatório de saída (impressa e no JSON).

O QUE ESTE SCRIPT FAZ DE NOVO (não existia em nenhum script de medição
anterior — achado C1-b da tri-review, "Kalman frio no replay vs. quente ao
vivo"): encadeia o estado do Kalman CRONOLOGICAMENTE entre sessões
(`chronological_replay` abaixo) em vez de reiniciar frio a cada sessão
como `measure_d1_inflation.py`/`measure_tactical_gate3.py` fazem (lá isso é
aceitável porque o alvo da medição é o VIÉS do D1 em isolamento, não a
trajetória fiel do Pair Signal). Aqui a trajetória de β/z_pair perto da
abertura de cada sessão importa — sem encadeamento, cada sessão recomeça
do prior estático calibrado, o que pode mudar qual fator é o "par ativo" e
deslocar quando |z_pair| cruza o threshold logo na abertura.

Metodologia por evento (transição pair_compra/pair_venda, já causal e só
em barra fechada — reaproveita engine.py inalterado, incluindo o achado
X3 corrigido nesta mesma sessão de trabalho):
  - Entrada = fechamento da barra SEGUINTE à da transição, não da própria
    barra que gerou o marker — o marker só é confirmado quando a barra
    fecha (X3), então o próprio fechamento do sinal já não é mais
    executável no instante em que a decisão pode ser tomada; usá-lo seria
    otimista (achado do /codex-r sobre "primeiro preço negociável"). Sinal
    na última barra da sessão não gera evento (sem barra seguinte pra
    preencher).
  - Cooldown: uma entrada só conta se estiver a >= COOLDOWN_BARS barras da
    entrada contada anterior NA MESMA SESSÃO — evita janelas de medição
    sobrepostas.
  - Retorno forward (h=3,6,10,20 barras) e MFE/MAE (até 20 barras) medidos
    só dentro da MESMA sessão — nunca atravessam a fronteira (A5 do
    plano). Trunca (não mede) o horizonte se a sessão acabar antes.
  - Custo: TARGET_COST_POINTS (mesma fonte que measure_tactical_gate3.py,
    documentada no ADR-002) debitado uma vez do retorno bruto em pontos.
  - IC95%: bootstrap clusterizado por SESSÃO (não por evento), 10k
    iterações — a mesma primitiva de measure_tactical_gate3.py
    (`_bootstrap_sessions`), reimplementada aqui pra não puxar a
    dependência de sklearn/scipy daquele módulo (este script é
    deliberadamente leve — sem modelo nenhum pra treinar).

Uso:
  python3 -X utf8 scripts/measure_pair_signal_value.py --db <path> --target WIN$N
  python3 -X utf8 scripts/measure_pair_signal_value.py --db <path> --targets WIN$N WDO$N --limit 300 --output-json out.json
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import random
import sys
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Iterable, Optional
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.irai import engine as engine_module
from backend.irai.engine import IRAIEngine
from backend.irai.kalman import KalmanFilterWrapper
from backend.db import factor_signature

from scripts.measure_d1_inflation import (
    DEFAULT_DB,
    _percentile,
    _real_snapshots,
    _table,
    candidate_sessions,
    readonly_connection,
)


DEFAULT_TARGETS = ("WIN$N", "WDO$N")
# Mesma fonte que scripts/measure_tactical_gate3.py:61 — não importada de lá
# pra não puxar sklearn/scipy (este script não treina nenhum modelo).
# Origem documentada em docs/adr/ADR-002-minimum-useful-delta-auc.md.
TARGET_COST_POINTS = {"WIN$N": 10.0, "WDO$N": 1.0}
FORWARD_HORIZONS = (3, 6, 10, 20)
MFE_MAE_HORIZON = 20
COOLDOWN_BARS = 20  # == maior horizonte: garante janelas de medição sem overlap
BOOTSTRAP_ITERATIONS = 10_000
DEFAULT_SESSION_LIMIT = 300  # ~14 meses das sessões mais recentes; ajustável via --limit
# Sessões iniciais do replay cujo estado do Kalman encadeado ainda está
# "frio" (achado do /codex-r, 2ª rodada — risco de maior prioridade
# apontado: "estado frio inicial/burn-in"). Essas sessões ainda são
# REPLAYADAS (o estado precisa esquentar), mas seus eventos são excluídos
# da medição — nunca silenciosamente: `sessions_burn_in` sempre aparece no
# relatório.
DEFAULT_BURN_IN_SESSIONS = 5


# ── Replay cronológico com Kalman encadeado (achado C1-b) ──────────────────

def _make_capturing_kalman(base_cls):
    """Fábrica de subclasse: delega tudo à `base_cls` (o KalmanFilterWrapper
    real em produção; um fake leve tipo SpyKalman de tests/test_premarket.py
    em teste, pra não exigir pykalman instalado) — só guarda uma referência
    de si mesma numa variável de classe, porque `kf` é local dentro de
    `IRAIEngine.compute_from_db` e nunca é exposta pro caller."""
    class _Capturing(base_cls):
        last = None

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            _Capturing.last = self

    return _Capturing


@contextmanager
def chronological_replay(db_path: str, *, kalman_cls=KalmanFilterWrapper):
    """Como `readonly_engine` de measure_d1_inflation.py, mas o estado do
    Kalman ENCADEIA entre sessões na ordem em que a função devolvida é
    chamada, em vez de reiniciar frio a cada sessão. Yields uma função
    `compute(session_date, target) -> list[IRAISnapshot]`; chame-a em
    ordem cronológica estritamente crescente por target.

    `kalman_cls` é injetável só pra teste (evita depender do pykalman real,
    ausente no ambiente Linux de dev) — produção sempre usa o default."""
    chained_state: dict[str, dict] = {}  # slug -> saved_state dict (formato de db.py::load_kalman_state)
    capturing_cls = _make_capturing_kalman(kalman_cls)

    def load_chained(_conn, slug):
        return copy.deepcopy(chained_state.get(slug))

    with patch.object(engine_module, "get_connection", readonly_connection), \
         patch.object(engine_module, "load_kalman_state", load_chained), \
         patch.object(engine_module, "KalmanFilterWrapper", capturing_cls):
        instance = IRAIEngine(db_path=db_path)

        def compute(session_date: str, target: str):
            slug = instance.target_slugs.get(target, target.lower())
            capturing_cls.last = None
            snapshots = instance.compute_from_db(
                session_date, target=target, version="v2", persist_state=False,
            )
            real = _real_snapshots(snapshots)
            if capturing_cls.last is not None and real:
                mean, cov = capturing_cls.last.get_state()
                factors = instance.models.get(slug, {}).get("factors", [])
                chained_state[slug] = {
                    "state_mean": mean,
                    "state_covariance": cov,
                    "timestamp_utc": real[-1].timestamp,
                    "factor_signature": factor_signature(factors),
                }
            return snapshots

        yield compute


# ── Extração de eventos + medição forward ───────────────────────────────────

@dataclass(frozen=True)
class TradeOutcome:
    session_date: str
    target: str
    direction: str          # "buy" | "sell"
    hour_brt: int
    pair_factor: Optional[str]
    entry_price: float
    # None = horizonte truncado pela fronteira da sessão (não medido, não é 0.0)
    fwd: dict[int, Optional[float]]   # {horizonte: retorno líquido de custo, em pontos}
    mfe: Optional[float]
    mae: Optional[float]


def _hour_brt(timestamp_utc: str, is_b3: bool) -> int:
    """Hora BRT aproximada só pra quebra de análise (não precisa do offset
    sazonal exato aqui — é agrupamento, não cálculo de sinal)."""
    from datetime import datetime
    ts = datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
    if is_b3:
        # Eixo já vem deslocado +5h/+6h (Tickmill); BRT = Tickmill - offset.
        # Aproximação deliberada (5h) só para agrupar por hora — ver
        # backend/irai/timezones.py se precisão exata for necessária depois.
        ts = ts.replace(hour=(ts.hour - 5) % 24)
    return ts.hour


def extract_trade_outcomes(
    session_date: str, target: str, snapshots, is_b3: bool,
) -> list[TradeOutcome]:
    """Varre as barras reais de UMA sessão, extrai as transições pair_compra/
    pair_venda já causais (achado X3 corrigido: nunca nasce de barra em
    formação), aplica cooldown e mede o resultado forward líquido de custo.
    """
    real = _real_snapshots(snapshots)
    cost = TARGET_COST_POINTS.get(target, 0.0)
    outcomes: list[TradeOutcome] = []
    last_counted_index = -COOLDOWN_BARS - 1

    for i, snap in enumerate(real):
        direction = None
        if getattr(snap, "pair_compra", None) is not None:
            direction = "buy"
        elif getattr(snap, "pair_venda", None) is not None:
            direction = "sell"
        if direction is None:
            continue
        if i - last_counted_index < COOLDOWN_BARS:
            continue  # dentro do cooldown da última entrada contada

        # Entrada = fechamento da barra SEGUINTE à do sinal, não da própria
        # barra que gerou o marker. O marker só é confirmado quando a barra
        # fecha (achado X3), então o preço da própria barra do sinal já não
        # é mais executável no instante em que a decisão pode ser tomada —
        # usá-lo seria otimista (achado do /codex-r sobre "primeiro preço
        # negociável", docs/plans/2026-07-13-irai-plano-consolidado.md).
        entry_index = i + 1
        if entry_index >= len(real):
            continue  # sinal na última barra da sessão — sem preço executável real
        last_counted_index = i
        entry_price = float(real[entry_index].win_current)

        sign = 1.0 if direction == "buy" else -1.0

        fwd: dict[int, Optional[float]] = {}
        for h in FORWARD_HORIZONS:
            if entry_index + h < len(real):
                raw = float(real[entry_index + h].win_current) - entry_price
                fwd[h] = sign * raw - cost
            else:
                fwd[h] = None  # trunca na fronteira da sessão (A5) — não mede

        window_end = min(entry_index + MFE_MAE_HORIZON, len(real) - 1)
        excursions = [
            sign * (float(real[j].win_current) - entry_price)
            for j in range(entry_index + 1, window_end + 1)
        ]
        # Piso em 0 pro MFE e teto em 0 pro MAE: numa trajetória
        # monotonicamente perdedora (ou vencedora), a excursão mais favorável
        # (ou mais adversa) medida não deve trocar de sinal em relação à
        # convenção usual da métrica — achado do /codex-r ("contraria as
        # definições usuais" quando não clampado).
        mfe = max(0.0, max(excursions)) if excursions else None
        mae = min(0.0, min(excursions)) if excursions else None

        outcomes.append(TradeOutcome(
            session_date=session_date,
            target=target,
            direction=direction,
            # Hora da barra de ENTRADA, não da barra do sinal — achado do
            # /codex-r: usar snap.timestamp (a barra do sinal) atribuiria um
            # evento à hora anterior sempre que a barra seguinte cruzasse a
            # fronteira de hora. Só afeta a quebra descritiva by_hour_brt.
            hour_brt=_hour_brt(real[entry_index].timestamp, is_b3),
            pair_factor=getattr(snap, "pair_factor", None),
            entry_price=entry_price,
            fwd=fwd,
            mfe=mfe,
            mae=mae,
        ))
    return outcomes


# ── Bootstrap clusterizado por sessão (mesma primitiva de medida_tactical_gate3.py,
#    reimplementada aqui pra não depender de sklearn/scipy) ─────────────────

@dataclass(frozen=True)
class Estimate:
    value: float
    ci_low: float
    ci_high: float
    n_sessions: int
    n_events: int
    significant: bool  # IC95% não inclui zero
    standard_error: float


def _bootstrap_sessions(by_session: dict[str, list[float]], statistic: Callable[[list[float]], float],
                         iterations: int, seed: int) -> list[float]:
    sessions = sorted(by_session)
    rng = random.Random(seed)
    values = []
    for _ in range(iterations):
        chosen = rng.choices(sessions, k=len(sessions))
        sample = [row for session in chosen for row in by_session[session]]
        if not sample:
            continue
        value = statistic(sample)
        if math.isfinite(value):
            values.append(value)
    return values


def estimate_mean(outcomes: list[TradeOutcome], horizon: int, *,
                   iterations: int = BOOTSTRAP_ITERATIONS, seed: int = 20260714) -> Optional[Estimate]:
    by_session: dict[str, list[float]] = defaultdict(list)
    for o in outcomes:
        v = o.fwd.get(horizon)
        if v is not None:
            by_session[o.session_date].append(v)
    values = [v for vs in by_session.values() for v in vs]
    if not values:
        return None
    mean = sum(values) / len(values)
    samples = _bootstrap_sessions(by_session, lambda s: sum(s) / len(s), iterations, seed)
    if not samples:
        return None
    ci_low, ci_high = _percentile(samples, 0.025), _percentile(samples, 0.975)
    var = sum((x - mean) ** 2 for x in samples) / max(1, len(samples) - 1)
    return Estimate(
        value=mean, ci_low=ci_low, ci_high=ci_high,
        n_sessions=len(by_session), n_events=len(values),
        significant=(ci_low > 0 or ci_high < 0),
        standard_error=math.sqrt(var),
    )


def win_rate(outcomes: list[TradeOutcome], horizon: int) -> tuple[int, int, float]:
    values = [o.fwd[horizon] for o in outcomes if o.fwd.get(horizon) is not None]
    wins = sum(1 for v in values if v > 0)
    total = len(values)
    return wins, total, (100.0 * wins / total if total else float("nan"))


# ── Orquestração ────────────────────────────────────────────────────────────

def run(db_path: str, targets: Iterable[str], limit: int, iterations: int,
        burn_in_sessions: int = DEFAULT_BURN_IN_SESSIONS) -> dict:
    report: dict = {"targets": {}}
    for target in targets:
        candidates = candidate_sessions(db_path, target, limit)
        is_b3 = target in ("WIN$N", "WDO$N")
        outcomes: list[TradeOutcome] = []
        with chronological_replay(db_path) as compute:
            for idx, date in enumerate(candidates.dates):  # já ordenado ascendente
                snapshots = compute(date, target)
                if not snapshots:
                    continue
                if idx < burn_in_sessions:
                    # Sessão ainda replayada (o Kalman precisa esquentar pra
                    # sessão seguinte encadear corretamente), mas seus
                    # eventos não entram na medição — estado ainda frio.
                    continue
                outcomes.extend(extract_trade_outcomes(date, target, snapshots, is_b3))

        by_direction = {
            "buy": [o for o in outcomes if o.direction == "buy"],
            "sell": [o for o in outcomes if o.direction == "sell"],
            "all": outcomes,
        }
        target_report: dict = {
            "sessions_replayed": len(candidates.dates),
            "sessions_discarded": len(candidates.discarded),
            "sessions_burn_in": min(burn_in_sessions, len(candidates.dates)),
            "cost_points": TARGET_COST_POINTS.get(target, 0.0),
            "by_direction": {},
        }
        for label, subset in by_direction.items():
            horizons_report = {}
            for h in FORWARD_HORIZONS:
                est = estimate_mean(subset, h, iterations=iterations)
                wins, total, pct = win_rate(subset, h)
                horizons_report[str(h)] = {
                    "estimate": asdict(est) if est else None,
                    "win_rate_pct": pct, "wins": wins, "total": total,
                }
            mfes = [o.mfe for o in subset if o.mfe is not None]
            maes = [o.mae for o in subset if o.mae is not None]
            target_report["by_direction"][label] = {
                "n_events": len(subset),
                "horizons": horizons_report,
                # Descritivo, não bootstrapado (mesmo espírito de by_hour/
                # by_pair_factor abaixo) — achado do /codex-r: MFE/MAE eram
                # calculados e nunca apareciam no relatório.
                "mfe_mean": round(sum(mfes) / len(mfes), 2) if mfes else None,
                "mae_mean": round(sum(maes) / len(maes), 2) if maes else None,
            }

        # Quebra por hora do dia (BRT aproximado) — só contagem + retorno médio h=6,
        # pra não inflar o relatório com bootstrap por hora (amostra fica pequena).
        by_hour: dict[int, list[float]] = defaultdict(list)
        for o in outcomes:
            v = o.fwd.get(6)
            if v is not None:
                by_hour[o.hour_brt].append(v)
        target_report["by_hour_brt_h6_mean"] = {
            str(h): round(sum(vs) / len(vs), 2) for h, vs in sorted(by_hour.items()) if vs
        }

        # Quebra por identidade do par ativo — idem, só descritivo.
        by_factor: dict[str, list[float]] = defaultdict(list)
        for o in outcomes:
            v = o.fwd.get(6)
            if v is not None and o.pair_factor:
                by_factor[o.pair_factor].append(v)
        target_report["by_pair_factor_h6_mean"] = {
            factor: {"n": len(vs), "mean": round(sum(vs) / len(vs), 2)}
            for factor, vs in sorted(by_factor.items(), key=lambda kv: -len(kv[1]))
        }

        report["targets"][target] = target_report

    report["limitations"] = LIMITATIONS
    return report


# Limitações conhecidas e deliberadamente NÃO resolvidas neste escopo mínimo
# (achados do /codex-r sobre a 1ª versão deste script) — sempre incluídas no
# relatório de saída (JSON e texto) pra nenhuma leitura do número tratar isto
# como confirmação de edge econômico sem essas ressalvas.
LIMITATIONS = [
    "C1-a (calibração in-sample): a cesta de fatores é selecionada por "
    "scripts/calibrate_universal.py num split treino/holdout temporal "
    "(holdout fica fora da escolha), mas os pesos finais usados em "
    "produção são refeitos sobre TODO o histórico (`merged_all`, incluindo "
    "o próprio holdout) — é esse artefato final que este script aplica "
    "retroativamente a cada sessão do replay. Isso pode, "
    "retrospectivamente, favorecer um fator cujo resíduo pareça mais "
    "mean-reverting contra o alvo do que seria observável em tempo real — "
    "um viés otimista que este script NÃO isola nem quantifica. Um "
    "resultado positivo aqui é evidência preliminar, não confirmação de "
    "edge OOS genuíno.",
    "TARGET_COST_POINTS (WIN$N=10, WDO$N=1) nunca foi derivado de P&L "
    "executável real — ver docs/adr/ADR-002-minimum-useful-delta-auc.md. "
    "Assume-se custo único (round-trip) por evento, coerente com o uso do "
    "mesmo valor no plano tático, mas não validado independentemente.",
    "O encadeamento cronológico do Kalman (achado C1-b) pula sessões "
    "descartadas por candidate_sessions() sem processá-las: o estado NÃO é "
    "atualizado nesses dias, diferente do que aconteceria ao vivo (que "
    "processaria qualquer dado parcial disponível). Introduz uma "
    "descontinuidade pequena, porém real, entre este replay e produção.",
    "MFE/MAE usam apenas o fechamento de cada barra de 5 min, não os "
    "extremos intrabarra (H/L) — podem subestimar a excursão real.",
    "As primeiras `sessions_burn_in` sessões de cada alvo são replayadas "
    "(pra o Kalman encadeado esquentar) mas EXCLUÍDAS da medição — estado "
    "inicial frio não reflete o que existiria em produção (achado do "
    "/codex-r, 2ª rodada: risco de maior prioridade apontado).",
]


def _print_report(report: dict) -> None:
    for target, t in report["targets"].items():
        print(f"\n=== {target} — {t['sessions_replayed']} sessões replayadas "
              f"({t['sessions_discarded']} descartadas, {t['sessions_burn_in']} "
              f"de burn-in excluídas da medição), custo={t['cost_points']} pts ===")
        for label, d in t["by_direction"].items():
            if d["n_events"] == 0:
                print(f"  [{label}] nenhum evento")
                continue
            print(f"  [{label}] {d['n_events']} eventos — "
                  f"MFE médio: {d['mfe_mean']}, MAE médio: {d['mae_mean']}")
            rows = []
            for h in FORWARD_HORIZONS:
                hr = d["horizons"][str(h)]
                est = hr["estimate"]
                if est is None:
                    rows.append((f"h={h}", "sem dados", "-", "-", "-"))
                    continue
                sig = "***" if est["significant"] else ""
                rows.append((
                    f"h={h}",
                    f"{est['value']:+.2f} pts {sig}",
                    f"[{est['ci_low']:+.2f}; {est['ci_high']:+.2f}]",
                    f"{hr['win_rate_pct']:.1f}% ({hr['wins']}/{hr['total']})",
                    f"{est['n_sessions']} sessões",
                ))
            print(_table(("horizonte", "média líq. custo", "IC95%", "win-rate", "amostra"), rows))
        print(f"  by_hour_brt (h=6, retorno médio líq.): {t['by_hour_brt_h6_mean']}")
        print(f"  by_pair_factor (h=6, retorno médio líq.): {t['by_pair_factor_h6_mean']}")

    print("\n=== LIMITAÇÕES (leia antes de interpretar os números acima) ===")
    for i, item in enumerate(report.get("limitations", []), 1):
        print(f"  {i}. {item}")


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
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()
    if args.target:
        args.targets = [args.target]
    return args


def main() -> int:
    args = parse_args()
    print(f"Pair Signal isolado (NF-01, escopo mínimo) — banco: {args.db}")
    print(f"Alvos: {args.targets} · limite de sessões: {args.limit} · bootstrap: {args.bootstrap} "
          f"· burn-in: {args.burn_in_sessions} sessões")
    print("Kalman encadeado cronologicamente entre sessões (achado C1-b) — ver docstring do módulo.")
    report = run(args.db, args.targets, args.limit, args.bootstrap, args.burn_in_sessions)
    _print_report(report)
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nRelatório salvo em {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
