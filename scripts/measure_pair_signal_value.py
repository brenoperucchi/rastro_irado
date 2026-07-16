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

from datetime import datetime, timedelta

from backend.irai import engine as engine_module
from backend.irai.engine import IRAIEngine
from backend.irai.kalman import KalmanFilterWrapper
from backend.irai.timezones import brt_to_tickmill_offset_hours
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
BAR_DURATION_MIN = 5  # M5 — usado só p/ derivar o FIM de cada barra a partir do
                      # timestamp (que é o INÍCIO da barra M5, convenção MT5)
BOOTSTRAP_ITERATIONS = 10_000
DEFAULT_SESSION_LIMIT = 300  # ~14 meses das sessões mais recentes; ajustável via --limit
# Sessões iniciais do replay cujo estado do Kalman encadeado ainda está
# "frio" (achado do /codex-r, 2ª rodada — risco de maior prioridade
# apontado: "estado frio inicial/burn-in"). Essas sessões ainda são
# REPLAYADAS (o estado precisa esquentar), mas seus eventos são excluídos
# da medição — nunca silenciosamente: `sessions_burn_in` sempre aparece no
# relatório.
DEFAULT_BURN_IN_SESSIONS = 5
# Amostra mínima pro "gate de aprovação" econômico — não um limiar inventado
# aqui: docs/plans/2026-07-13-irai-tactical-layer-win-wdo.md:281 ("pelo
# menos 100 eventos confirmados para o gate econômico"), §7.3. Abaixo disso
# o alvo é rotulado INCONCLUSIVO no relatório, nunca silenciosamente tratado
# como "sem edge" ou "com edge" — pedido explícito do usuário ao expandir o
# escopo pro item 3 (interseção Pair+Z, cuja amostra é necessariamente menor
# que a de qualquer um dos dois markers isolados).
MIN_EVENTS_FOR_GATE = 100


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
    chamada, em vez de reiniciar frio a cada sessão. Yields `(compute,
    instance)`: `compute(session_date, target) -> list[IRAISnapshot]`
    (chame-a em ordem cronológica estritamente crescente por target) e
    `instance`, o IRAIEngine subjacente — exposto pra permitir injetar
    calibração point-in-time em memória (scripts/pit_calibration.py,
    achado C1-a) ANTES de cada `compute()`, sem nunca reconstruir o
    engine (o que reiniciaria o encadeamento do Kalman do zero).

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

        yield compute, instance


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
    # ── Contrato temporal causal (4 timestamps, eixo Tickmill, ISO) ──────────
    # Prova, evento a evento, que nenhum dado do futuro entrou na decisão.
    # Modelo: barra M5, timestamp = INÍCIO da barra; a barra fecha +5min.
    #   observation_bar_end  = fim da barra que gerou o marker (barra i). A
    #     distorção só é "vista" quando essa barra fecha (achado X3: o marker
    #     nunca nasce de barra em formação).
    #   confirmation_bar_end = fim da barra que CONFIRMA o sinal. Na política
    #     atual, observação e confirmação coincidem (o próprio fechamento da
    #     barra i confirma, X3), então == observation_bar_end. O campo separado
    #     deixa o contrato pronto p/ uma barra de confirmação adicional futura
    #     (VAL-04/tactical), sem quebrar o schema.
    #   signal_available_at  = instante em que o sinal fica ACIONÁVEL =
    #     confirmation_bar_end (existe assim que a barra de confirmação fecha).
    #   entry_at             = instante do FILL. Política atual: close da barra
    #     SEGUINTE (i+1), que ocorre em fim_da_barra(i+1). Portanto
    #     signal_available_at <= entry_at SEMPRE (o sinal existe antes da
    #     entrada — causal, e conservador: há 1 barra M5 de defasagem).
    observation_bar_end: str
    confirmation_bar_end: str
    signal_available_at: str
    entry_at: str


def _parse_axis_ts(timestamp_iso: str) -> datetime:
    """Parseia o timestamp do snapshot (eixo do servidor Tickmill, string ISO
    naive — ver backend/irai/engine.py). Tolera o sufixo 'Z' por robustez,
    mas os snapshots do engine são naive."""
    return datetime.fromisoformat(timestamp_iso.replace("Z", "").replace("+00:00", ""))


def _bar_end_iso(timestamp_iso: str) -> str:
    """FIM da barra M5 a partir do seu timestamp de INÍCIO (convenção MT5:
    o timestamp da barra é o instante de abertura; a barra fecha +5min)."""
    return (_parse_axis_ts(timestamp_iso) + timedelta(minutes=BAR_DURATION_MIN)).isoformat()


def _hour_brt(timestamp_iso: str, is_b3: bool) -> int:
    """Hora BRT da barra, usando o offset SAZONAL oficial
    (brt_to_tickmill_offset_hours: 5h fora do DST americano, 6h dentro) em
    vez do -5h aproximado anterior. O timestamp vem no eixo Tickmill; a data
    Tickmill coincide com a data BRT para o pregão diurno da B3 (09:00-18:00
    BRT -> ~14:00-24:00 no eixo Tickmill, mesma data), então usar a data do
    próprio timestamp p/ resolver o offset é seguro aqui — corrige o achado
    do /codex-r (comentário #3 do IRAI-2)."""
    ts = _parse_axis_ts(timestamp_iso)
    if is_b3:
        offset = brt_to_tickmill_offset_hours(ts)
        ts = ts - timedelta(hours=offset)
    return ts.hour


def _pair_direction(snap) -> Optional[str]:
    """Direção da transição causal do Pair Signal (marker `P`) numa barra —
    default de `extract_trade_outcomes`. Ver `backend/irai/engine.py`
    (campos `pair_compra`/`pair_venda`, já gated pelo achado X3)."""
    if getattr(snap, "pair_compra", None) is not None:
        return "buy"
    if getattr(snap, "pair_venda", None) is not None:
        return "sell"
    return None


def extract_trade_outcomes(
    session_date: str, target: str, snapshots, is_b3: bool,
    *, direction_of: Optional[Callable] = None,
) -> list[TradeOutcome]:
    """Varre as barras reais de UMA sessão, extrai as transições causais já
    gated pelo achado X3 (nunca nasce de barra em formação), aplica cooldown
    e mede o resultado forward líquido de custo.

    `direction_of(snap) -> "buy" | "sell" | None` decide QUAL marker dispara
    o evento — default `_pair_direction` (marker `P`, pair_compra/pair_venda).
    scripts/measure_price_divergence_value.py reusa esta função inteira
    passando `direction_of=_divergence_direction` (marker `Z`,
    z_compra_val/z_venda_val) em vez de duplicar a metodologia de entrada/
    cooldown/MFE-MAE, que já passou por 2 rodadas de /codex-r.
    """
    direction_of = direction_of or _pair_direction
    real = _real_snapshots(snapshots)
    cost = TARGET_COST_POINTS.get(target, 0.0)
    outcomes: list[TradeOutcome] = []
    last_counted_index = -COOLDOWN_BARS - 1

    for i, snap in enumerate(real):
        direction = direction_of(snap)
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

        # Contrato temporal causal (ver docstring de TradeOutcome). A barra do
        # sinal é `i`; a barra de entrada é `entry_index` (i+1). observação e
        # confirmação coincidem na política atual (marker X3 confirmado no
        # fechamento da barra i).
        observation_bar_end = _bar_end_iso(snap.timestamp)
        confirmation_bar_end = observation_bar_end
        signal_available_at = confirmation_bar_end
        entry_at = _bar_end_iso(real[entry_index].timestamp)

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
            observation_bar_end=observation_bar_end,
            confirmation_bar_end=confirmation_bar_end,
            signal_available_at=signal_available_at,
            entry_at=entry_at,
        ))
    return outcomes


def outcome_to_dict(o: TradeOutcome) -> dict:
    """Serializa um TradeOutcome pra JSON (artefato NF-01 versionado). `fwd`
    vira dict com chaves-string (horizontes) pra sobreviver a round-trip JSON."""
    return {
        "session_date": o.session_date,
        "target": o.target,
        "direction": o.direction,
        "hour_brt": o.hour_brt,
        "pair_factor": o.pair_factor,
        "entry_price": o.entry_price,
        "fwd": {str(h): v for h, v in o.fwd.items()},
        "mfe": o.mfe,
        "mae": o.mae,
        "observation_bar_end": o.observation_bar_end,
        "confirmation_bar_end": o.confirmation_bar_end,
        "signal_available_at": o.signal_available_at,
        "entry_at": o.entry_at,
    }


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
        burn_in_sessions: int = DEFAULT_BURN_IN_SESSIONS,
        *, direction_of: Optional[Callable] = None,
        limitations: Optional[list] = None,
        preprocess: Optional[Callable] = None,
        min_events_for_gate: int = MIN_EVENTS_FOR_GATE,
        pit_schedule=None,
        emit_events: bool = False) -> dict:
    """`direction_of`/`limitations` default ao Pair Signal (marker `P`) —
    scripts/measure_price_divergence_value.py e
    scripts/measure_intersection_value.py reusam esta função passando as
    suas próprias (markers `Z` e interseção Pair∩Z) em vez de duplicar
    orquestração/relatório. `preprocess(snapshots)`, se dado, roda logo
    após `compute()` de cada sessão (antes da extração de eventos) — usado
    por measure_intersection_value.py pra estampar os campos sintéticos que
    seu `direction_of` lê (não há campo pronto no engine pra "interseção",
    diferente de pair_compra/z_compra_val).

    `pit_schedule` (scripts/pit_calibration.py::PitSchedule), se dado,
    injeta calibração point-in-time em memória ANTES de cada `compute()`
    (achado C1-a) — sessões anteriores ao 1º cutoff do schedule ainda são
    replayadas (aquecem o Kalman, igual ao burn-in) mas ficam de fora da
    medição, contadas separadamente em `sessions_before_first_pit_cutoff`."""
    direction_of = direction_of or _pair_direction
    report: dict = {"targets": {}}
    for target in targets:
        candidates = candidate_sessions(db_path, target, limit)
        is_b3 = target in ("WIN$N", "WDO$N")
        outcomes: list[TradeOutcome] = []
        sessions_before_first_pit_cutoff = 0
        with chronological_replay(db_path) as (compute, instance):
            for idx, date in enumerate(candidates.dates):  # já ordenado ascendente
                pit_valid = True
                if pit_schedule is not None:
                    pit_valid = pit_schedule.apply_for_session(instance, target, date)
                snapshots = compute(date, target)
                if not snapshots:
                    continue
                if preprocess is not None:
                    preprocess(snapshots)
                if not pit_valid:
                    sessions_before_first_pit_cutoff += 1
                if idx < burn_in_sessions or not pit_valid:
                    # Sessão ainda replayada (o Kalman precisa esquentar pra
                    # sessão seguinte encadear corretamente), mas seus
                    # eventos não entram na medição — estado ainda frio, ou
                    # ainda sem calibração point-in-time válida.
                    continue
                outcomes.extend(extract_trade_outcomes(
                    date, target, snapshots, is_b3, direction_of=direction_of))

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
            "pit_mode": pit_schedule is not None,
            "sessions_before_first_pit_cutoff": sessions_before_first_pit_cutoff,
            "pit_cutoffs_used": pit_schedule.cutoffs_used(target) if pit_schedule is not None else None,
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
                # Sessões DISTINTAS que contribuíram pelo menos 1 evento —
                # diferente de sessions_replayed (total processado). Pedido
                # explícito do usuário ao expandir o escopo pro item 3:
                # "sessões independentes" precisa ser visível pra julgar se
                # um horizonte com poucas sessões é confiável.
                "n_sessions_with_events": len({o.session_date for o in subset}),
                "horizons": horizons_report,
                # Descritivo, não bootstrapado (mesmo espírito de by_hour/
                # by_pair_factor abaixo) — achado do /codex-r: MFE/MAE eram
                # calculados e nunca apareciam no relatório.
                "mfe_mean": round(sum(mfes) / len(mfes), 2) if mfes else None,
                "mae_mean": round(sum(maes) / len(maes), 2) if maes else None,
            }

        # Gate de amostra mínima — docs/plans/2026-07-13-irai-tactical-layer-
        # win-wdo.md §7.3 ("pelo menos 100 eventos confirmados"). Abaixo do
        # mínimo, o alvo é rotulado INCONCLUSIVO em vez de silenciosamente
        # reportar médias/IC de uma amostra fina como se fossem confiáveis.
        n_events_all = target_report["by_direction"]["all"]["n_events"]
        target_report["min_events_for_gate"] = min_events_for_gate
        target_report["gate_verdict"] = (
            "INCONCLUSIVO (amostra abaixo do mínimo)"
            if n_events_all < min_events_for_gate
            else "AMOSTRA_SUFICIENTE_PARA_GATE"
        )

        # Quebra por ano — pedido explícito do usuário ao expandir a janela de
        # replay pra vários anos: "verificar estabilidade por período"
        # (mesmo espírito de "estabilidade mínima por fold" do gate de
        # aprovação, docs/plans/2026-07-13-irai-tactical-layer-win-wdo.md
        # §7.3). Descritivo, não bootstrapado, mesmo padrão de by_pair_factor.
        by_year: dict[str, list[float]] = defaultdict(list)
        for o in outcomes:
            v = o.fwd.get(6)
            if v is not None:
                by_year[o.session_date[:4]].append(v)
        target_report["by_year_h6_mean"] = {
            year: {"n": len(vs), "mean": round(sum(vs) / len(vs), 2)}
            for year, vs in sorted(by_year.items())
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

        # Eventos individuais serializados (com os 4 timestamps causais) — só
        # quando `emit_events`, pra alimentar o artefato NF-01 versionado sem
        # inflar o relatório de rotina. Ordenados por (data, signal_available_at).
        if emit_events:
            target_report["events"] = [
                outcome_to_dict(o)
                for o in sorted(outcomes, key=lambda o: (o.session_date, o.signal_available_at))
            ]

        report["targets"][target] = target_report

    report["limitations"] = limitations if limitations is not None else LIMITATIONS
    return report


# Limitações COMPARTILHADAS entre os 3 scripts NF-01 (Pair, Z, interseção) —
# nenhuma depende de qual marker dispara o evento. measure_price_divergence_
# value.py e measure_intersection_value.py importam e estendem esta lista com
# suas próprias ressalvas de C1-a (mecanismo de contaminação difere por
# marker) em vez de duplicá-la. Sempre incluídas no relatório de saída (JSON
# e texto) pra nenhuma leitura do número tratar isto como confirmação de edge
# econômico sem essas ressalvas.
COMMON_LIMITATIONS = [
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
    "Cada execução reporta múltiplos horizontes (h=3/6/10/20) × até 3 "
    "agrupamentos de direção (buy/sell/all) por alvo — até 24 comparações "
    "simultâneas. Um `***` isolado NÃO deve ser lido como confirmatório sem "
    "correção pra comparações múltiplas (ex: Bonferroni implícito); é mais "
    "informativo olhar CONSISTÊNCIA do sinal ao longo de vários horizontes/ "
    "direções do que qualquer `***` isolado.",
    "candidate_sessions() (scripts/measure_d1_inflation.py) só valida que a "
    "ÚLTIMA barra de uma sessão histórica bate com o horário de fechamento "
    "esperado — não valida contagem mínima de barras nem gaps internos. "
    "Numa janela de replay que cubra vários anos, uma sessão esparsa (dados "
    "faltando no meio, mas presente no fechamento) passa como 'completa'; "
    "nesse caso, 'h=6' significa 6 barras REAIS observadas, que podem "
    "cobrir mais de 30 minutos de relógio (achado do /codex-r, job "
    "relay-mrmv6awy-phl3u0). Auditar contagem de barras/gaps por sessão "
    "antes de confiar em quebras por ano/período de janelas expandidas.",
]

# Só se aplica ao modo RETROSPECTIVO (default) — no modo point-in-time
# (--point-in-time) esta ressalva seria falsa, por isso fica fora de
# COMMON_LIMITATIONS e é montada separadamente em LIMITATIONS/main().
RETROSPECTIVE_ONLY_LIMITATION = (
    "Sem calibração point-in-time (ver ressalva C1-a específica de cada "
    "marker acima), isto é sempre um REPLAY RETROSPECTIVO com os parâmetros "
    "ATUAIS de produção aplicados a todo o histórico — não um teste "
    "out-of-sample no sentido estrito, mesmo quando a janela de replay "
    "cobre anos anteriores à calibração mais recente."
)

# Ressalvas do modo --point-in-time (achado C1-a, fechado via
# scripts/pit_calibration.py) — substituem C1A_LIMITATIONS +
# RETROSPECTIVE_ONLY_LIMITATION quando pit_schedule está ativo. Ver
# docstring de pit_calibration.py pro desenho completo (revisado por 3
# pareceres independentes: deep-reasoner, fable-reasoner, codex).
POINT_IN_TIME_LIMITATIONS = [
    "Modo point-in-time (achado C1-a): a cesta de fatores é FIXA e forçada "
    "(scripts/pit_calibration.py::FIXED_BASKETS — história longa, sem "
    "iShares, mesma cesta de scripts/run_walkforward_macro.sh) em vez da "
    "cesta real de produção em cada momento histórico. Necessário pro "
    "encadeamento do Kalman (achado C1-b) sobreviver às trocas de "
    "calibração — engine.py:685 só reaproveita o estado quando a "
    "assinatura da cesta não muda entre sessões. Isso remove o viés de "
    "seleção retrospectiva da cesta e dos pesos/sigmas/calibração "
    "logística (recalibrados a cada cutoff, só com dados <= cutoff), mas "
    "mede o marker sobre uma cesta SUBSTITUTA — não é a cesta exata que "
    "apareceu no dashboard historicamente. Contagens de eventos, "
    "identidade do fator ativo e by_pair_factor mudam de universo em "
    "relação ao braço retrospectivo (ver §11.3/11.4 do plano).",
    "Sessões anteriores ao 1º cutoff do schedule point-in-time (ver "
    "`sessions_before_first_pit_cutoff` no relatório) não têm calibração "
    "válida ainda — são replayadas (aquecem o Kalman) mas excluídas da "
    "medição, além do burn-in padrão.",
    "Mesmo point-in-time por DATA DE MERCADO, isto não reconstrói "
    "perfeitamente 'a informação disponível naquele dia': dados "
    "posteriormente corrigidos/completados no banco (backfill) podem "
    "aparecer em cutoffs antigos como se sempre tivessem estado lá.",
]

C1A_LIMITATIONS = [
    "C1-a (calibração in-sample): a cesta de fatores é selecionada por "
    "scripts/calibrate_universal.py num split treino/holdout temporal "
    "(holdout fica fora da escolha), mas os pesos finais usados em "
    "produção são refeitos sobre TODO o histórico (`merged_all`, incluindo "
    "o próprio holdout) — é esse artefato final que este script aplica "
    "retroativamente a cada sessão do replay. Isso pode, "
    "retrospectivamente, favorecer um fator cujo resíduo pareça mais "
    "mean-reverting contra o alvo do que seria observável em tempo real — "
    "um viés otimista que este script NÃO isola nem quantifica.",
]

LIMITATIONS = C1A_LIMITATIONS + COMMON_LIMITATIONS + [RETROSPECTIVE_ONLY_LIMITATION]


def _print_report(report: dict) -> None:
    for target, t in report["targets"].items():
        print(f"\n=== {target} — {t['sessions_replayed']} sessões replayadas "
              f"({t['sessions_discarded']} descartadas, {t['sessions_burn_in']} "
              f"de burn-in excluídas da medição), custo={t['cost_points']} pts ===")
        print(f"  GATE: {t['gate_verdict']} (mínimo {t['min_events_for_gate']} eventos, "
              f"docs/plans/2026-07-13-irai-tactical-layer-win-wdo.md §7.3)")
        if t["pit_mode"]:
            cutoffs = t["pit_cutoffs_used"] or []
            span = f"{cutoffs[0]}..{cutoffs[-1]}" if cutoffs else "NENHUM cutoff viável"
            print(f"  PONTO-NO-TEMPO (achado C1-a): {len(cutoffs)} cutoffs ({span}), "
                  f"{t['sessions_before_first_pit_cutoff']} sessões pré-1º-cutoff excluídas "
                  "(cesta fixa, ver scripts/pit_calibration.py)")
        for label, d in t["by_direction"].items():
            if d["n_events"] == 0:
                print(f"  [{label}] nenhum evento")
                continue
            print(f"  [{label}] {d['n_events']} eventos em {d['n_sessions_with_events']} "
                  f"sessões — MFE médio: {d['mfe_mean']}, MAE médio: {d['mae_mean']}")
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
        print(f"  by_year (h=6, retorno médio líq.): {t['by_year_h6_mean']}")
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
    parser.add_argument("--point-in-time", action="store_true",
                         help="Calibração point-in-time (achado C1-a) em vez dos pesos/cesta "
                              "atuais de produção — ver scripts/pit_calibration.py. Cesta FIXA "
                              "forçada (história longa, sem iShares); --limit precisa ser grande "
                              "o bastante pra alcançar o 1º cutoff do schedule.")
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
    pit_schedule = None
    limitations = LIMITATIONS
    if args.point_in_time:
        import scripts.pit_calibration as pit_calibration
        print("Modo POINT-IN-TIME ativo (achado C1-a) — construindo schedule de calibração "
              "(cesta fixa, sem busca por força bruta)...")
        pit_schedule = pit_calibration.build_schedule(args.db, args.targets)
        limitations = POINT_IN_TIME_LIMITATIONS + COMMON_LIMITATIONS
    report = run(args.db, args.targets, args.limit, args.bootstrap, args.burn_in_sessions,
                 pit_schedule=pit_schedule, limitations=limitations)
    _print_report(report)
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nRelatório salvo em {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
