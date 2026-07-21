#!/usr/bin/env python3
"""Compara P Dinâmico v1/v2 em walk-forward point-in-time para o WIN.

O experimento é separado do serving: abre o SQLite em modo read-only, injeta
calibrações apenas em memória e nunca grava ``model_params`` ou ``kalman_state``.
O braço ``miqueias_static_disclosed`` usa o disclosure versionado de 2026-06-23
somente a partir de sua vigência. Ele é diagnóstico e não participa de qualquer
veredito OOS: não há série histórica de parâmetros/estado do Kalman do Miqueias
que permita chamar essa curva de réplica dinâmica dele.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.irai.miqueias_static import (
    DEFAULT_CONFIG_PATH,
    MiqueiasStaticConfig,
    build_miqueias_static_rows,
    load_miqueias_static_config,
)
from backend.irai.timezones import brt_to_tickmill_offset_hours
from scripts import calibrate_universal as calibrator
from scripts.measure_d1_inflation import (
    ShiftArm,
    _real_snapshots,
    candidate_sessions,
    readonly_connection,
    readonly_engine,
)
from scripts.measure_pair_signal_value import chronological_replay
from scripts.pit_calibration import PitSchedule, build_schedule


SCHEMA_VERSION = 1
TARGET = "WIN$N"
MIQUEIAS_FACTORS = (
    "WDO$N",
    "DI1$N",
    "BRENT",
    "BTCUSD",
    "US30",
    "USDMXN",
    "CADCHF",
    "iSharesTreasury1-3+",
)
DEFAULT_CUTOFFS = (
    "2025-02-28",
    "2025-06-30",
    "2025-10-31",
    "2026-02-27",
    "2026-06-30",
)
DEFAULT_DECISION_TIME = "10:00"
DEFAULT_BOOTSTRAP_ITERATIONS = 2_000
EPSILON = 1e-6
SESSION_START_TIME = (9, 0)
SESSION_END_TIME = (18, 0)
M5_BAR_DURATION = timedelta(minutes=5)


@dataclass(frozen=True)
class SessionObservation:
    session_date: str
    decision_timestamp: str
    outcome_timestamp: str
    actual_up: bool
    v1_pit: float
    v2_pit: float
    miqueias_static_disclosed: float | None


@dataclass(frozen=True)
class SnapshotFingerprint:
    path: str
    size_bytes: int
    sha256: str


def fingerprint_closed_snapshot(db_path: str) -> SnapshotFingerprint:
    """Retorna a identidade de um SQLite fechado que pode alimentar o replay.

    O replay abre o banco repetidamente durante as calibrações e o encadeamento
    do Kalman. Um arquivo vivo em WAL pode mudar entre duas dessas leituras e
    não constitui uma base point-in-time. O caller confere este fingerprint no
    início e no fim para rejeitar relatórios calculados sobre um snapshot que
    mudou durante a execução.
    """
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"snapshot SQLite inexistente ou inválido: {path}")
    for suffix in ("-wal", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError(
                f"snapshot SQLite possui {sidecar.name} não checkpointado; "
                "gere uma cópia fechada antes do replay"
            )
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return SnapshotFingerprint(
        path=str(path), size_bytes=path.stat().st_size, sha256=digest.hexdigest()
    )


def parse_clock(value: str) -> tuple[int, int]:
    """Parseia HH:MM BRT sem aceitar um horário fora da sessão B3."""
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError("decision-time precisa ser HH:MM") from exc
    result = parsed.hour, parsed.minute
    if not (9 <= result[0] < 18):
        raise ValueError("decision-time precisa cair entre 09:00 e 17:59 BRT")
    return result


def _axis_datetime(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def _brt_datetime(timestamp: str, session_date: str) -> datetime:
    axis = _axis_datetime(timestamp)
    offset = brt_to_tickmill_offset_hours(datetime.fromisoformat(f"{session_date}T12:00:00"))
    return axis - timedelta(hours=offset)


def _real_by_timestamp(snapshots: Iterable) -> dict[str, object]:
    return {
        snapshot.timestamp: snapshot
        for snapshot in _real_snapshots(snapshots)
        if getattr(snapshot, "timestamp", None)
    }


def _is_b3_session_timestamp(timestamp: str, session_date: str) -> bool:
    """Aceita apenas inícios canônicos de barras M5 do pregão B3."""
    brt = _brt_datetime(timestamp, session_date)
    return (
        brt.date() == date.fromisoformat(session_date)
        and SESSION_START_TIME <= (brt.hour, brt.minute) < SESSION_END_TIME
        and brt.minute % 5 == 0
        and brt.second == 0
        and brt.microsecond == 0
    )


def _same_price(left: float, right: float, *, field: str) -> float:
    if not math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-8):
        raise ValueError(f"v1/v2 divergem em {field}: {left} != {right}")
    return float(left)


def _snapshot_static_probability(
    snapshot, config: MiqueiasStaticConfig
) -> float | None:
    """Calcula a curva estática sem extrapolar sua vigência declarada."""
    timestamp = str(snapshot.timestamp)
    if _axis_datetime(timestamp).date() < date.fromisoformat(config.effective_from):
        return None
    rows = build_miqueias_static_rows(
        [{
            "timestamp": timestamp,
            "t_frac": float(snapshot.t_frac),
            "factors": snapshot.factors,
            "is_ghost": bool(getattr(snapshot, "is_ghost", False)),
            "is_preview": bool(getattr(snapshot, "is_preview", False)),
        }],
        config,
    )
    probability = rows[0]["p_up"]
    return None if probability is None else float(probability) / 100.0


def build_observation(
    session_date: str,
    v1_snapshots: Iterable,
    v2_snapshots: Iterable,
    *,
    decision_time: tuple[int, int],
    static_config: MiqueiasStaticConfig,
) -> SessionObservation:
    """Extrai uma observação causal numa barra comum a v1/v2.

    O rótulo vem da primeira abertura e do último fechamento comum às duas
    fontes locais, independentemente de ``p_up``. A previsão é a última barra
    M5 canônica comum que já havia FECHADO até o horário de decisão BRT. Como
    o timestamp M5 identifica o início da barra, ele precisa somado aos cinco
    minutos do período caber no instante de decisão; portanto, nunca lê a
    barra ainda em formação nem um print fora da grade para construir o score.
    """
    v1 = _real_by_timestamp(v1_snapshots)
    v2 = _real_by_timestamp(v2_snapshots)
    common = sorted(
        timestamp
        for timestamp in set(v1) & set(v2)
        if _is_b3_session_timestamp(timestamp, session_date)
    )
    if not common:
        raise ValueError("v1/v2 não compartilham snapshots reais na sessão B3")

    decision_at = datetime.combine(
        date.fromisoformat(session_date),
        time(*decision_time),
        tzinfo=_brt_datetime(common[0], session_date).tzinfo,
    )
    eligible = [
        timestamp
        for timestamp in common
        if _brt_datetime(timestamp, session_date) + M5_BAR_DURATION <= decision_at
    ]
    if not eligible:
        raise ValueError("nenhum snapshot comum antes do horário de decisão")
    decision_timestamp = eligible[-1]
    outcome_timestamp = common[-1]
    first_timestamp = common[0]

    opening = _same_price(
        v1[first_timestamp].win_open, v2[first_timestamp].win_open, field="win_open"
    )
    closing = _same_price(
        v1[outcome_timestamp].win_current,
        v2[outcome_timestamp].win_current,
        field="win_current",
    )
    if opening <= 0 or not math.isfinite(opening) or not math.isfinite(closing):
        raise ValueError("open/close inválido para o outcome")

    v1_probability = float(v1[decision_timestamp].p_up) / 100.0
    v2_probability = float(v2[decision_timestamp].p_up) / 100.0
    if not (0.0 <= v1_probability <= 1.0 and 0.0 <= v2_probability <= 1.0):
        raise ValueError("P_up fora de [0,100]")

    return SessionObservation(
        session_date=session_date,
        decision_timestamp=decision_timestamp,
        outcome_timestamp=outcome_timestamp,
        actual_up=closing > opening,
        v1_pit=v1_probability,
        v2_pit=v2_probability,
        miqueias_static_disclosed=_snapshot_static_probability(
            v2[decision_timestamp], static_config
        ),
    )


def _auc(outcomes: Sequence[bool], probabilities: Sequence[float]) -> float | None:
    positives = [score for outcome, score in zip(outcomes, probabilities) if outcome]
    negatives = [score for outcome, score in zip(outcomes, probabilities) if not outcome]
    if not positives or not negatives:
        return None
    wins = sum(
        float(positive > negative) + 0.5 * float(positive == negative)
        for positive in positives
        for negative in negatives
    )
    return wins / (len(positives) * len(negatives))


def _metrics(observations: Sequence[SessionObservation], arm: str) -> dict:
    values = [
        (bool(observation.actual_up), getattr(observation, arm))
        for observation in observations
        if getattr(observation, arm) is not None
    ]
    if not values:
        return {"sessions": 0, "brier": None, "log_loss": None, "auc": None, "accuracy_pct": None}
    outcomes, probabilities = zip(*values)
    briers = [(probability - float(outcome)) ** 2 for outcome, probability in values]
    log_losses = [
        -(
            float(outcome) * math.log(min(1.0 - EPSILON, max(EPSILON, probability)))
            + (1.0 - float(outcome)) * math.log(min(1.0 - EPSILON, max(EPSILON, 1.0 - probability)))
        )
        for outcome, probability in values
    ]
    return {
        "sessions": len(values),
        "brier": round(statistics.fmean(briers), 8),
        "log_loss": round(statistics.fmean(log_losses), 8),
        "auc": None if (auc := _auc(outcomes, probabilities)) is None else round(auc, 8),
        "accuracy_pct": round(
            100.0 * statistics.fmean((probability >= 0.5) == outcome for outcome, probability in values),
            6,
        ),
    }


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def paired_brier_delta(
    observations: Sequence[SessionObservation],
    *,
    left: str,
    right: str,
    iterations: int,
    seed: int = 20260720,
) -> dict:
    """Delta pareado por sessão; negativo significa que ``left`` é melhor."""
    deltas = []
    for observation in observations:
        left_probability = getattr(observation, left)
        right_probability = getattr(observation, right)
        if left_probability is None or right_probability is None:
            continue
        actual = float(observation.actual_up)
        deltas.append((left_probability - actual) ** 2 - (right_probability - actual) ** 2)
    if not deltas:
        return {"sessions": 0, "delta_brier": None, "ci95": None}
    rng = random.Random(seed)
    bootstrap = [
        statistics.fmean(deltas[rng.randrange(len(deltas))] for _ in range(len(deltas)))
        for _ in range(iterations)
    ]
    return {
        "sessions": len(deltas),
        "delta_brier": round(statistics.fmean(deltas), 8),
        "ci95": [round(_percentile(bootstrap, 0.025), 8), round(_percentile(bootstrap, 0.975), 8)],
    }


def common_history_dates(db_path: str, target: str, factors: Sequence[str]) -> set[str]:
    """Datas com retorno diário observável para o alvo e a cesta inteira."""
    conn = readonly_connection(db_path)
    try:
        row = conn.execute(
            "SELECT session_start_h, session_end_h, data_proxy FROM asset_models WHERE target=?",
            (target,),
        ).fetchone()
        if row is None:
            raise ValueError(f"target não encontrado em asset_models: {target}")
        daily = calibrator.load_daily_returns(
            conn,
            row["session_start_h"] or 0,
            row["session_end_h"] or 24,
            row["data_proxy"] or target,
        )
    finally:
        conn.close()
    required = (target, *factors)
    missing = [symbol for symbol in required if symbol not in daily]
    if missing:
        raise ValueError("fatores sem retornos diários: " + ", ".join(missing))
    return set.intersection(
        *(set(str(index)[:10] for index in daily[symbol].index) for symbol in required)
    )


def split_replay_and_evaluation_dates(
    dates: Sequence[str], *, start_date: str | None, end_date: str | None
) -> tuple[list[str], list[str]]:
    """Mantém o aquecimento anterior ao recorte de medição.

    O estado v2 é path-dependent. Remover sessões antes de ``start_date`` da
    reprodução iniciaria o Kalman frio na primeira observação reportada e
    transformaria um recorte de relatório em mudança de metodologia.
    """
    replay_dates = [value for value in dates if end_date is None or value <= end_date]
    evaluation_dates = [
        value for value in replay_dates if start_date is None or value >= start_date
    ]
    return replay_dates, evaluation_dates


def _replay_v1(
    db_path: str, dates: Sequence[str], schedule: PitSchedule, target: str
) -> tuple[dict[str, list], dict[str, str]]:
    snapshots, discarded = {}, {}
    with readonly_engine(db_path, ShiftArm.FIXED, {}) as engine:
        for session_date in dates:
            valid = schedule.apply_for_session(engine, target, session_date)
            series = engine.compute_from_db(
                session_date, target=target, version="v1", persist_state=False
            )
            if valid:
                snapshots[session_date] = series
    return snapshots, discarded


def _replay_v2(
    db_path: str, dates: Sequence[str], schedule: PitSchedule, target: str
) -> tuple[dict[str, list], dict[str, str]]:
    snapshots, discarded = {}, {}
    with chronological_replay(db_path) as (compute, engine):
        for session_date in dates:
            valid = schedule.apply_for_session(engine, target, session_date)
            series = compute(session_date, target)
            if valid:
                snapshots[session_date] = series
    return snapshots, discarded


def run_walkforward(
    *,
    db_path: str,
    target: str,
    cutoffs: Sequence[str],
    decision_time: tuple[int, int],
    bootstrap_iterations: int,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    if target != TARGET:
        raise ValueError(f"este experimento só tem configuração Miqueias para {TARGET}")
    config_document = json.loads(Path(DEFAULT_CONFIG_PATH).read_text(encoding="utf-8"))
    static_config = load_miqueias_static_config(config_document)
    common_dates = common_history_dates(db_path, target, MIQUEIAS_FACTORS)
    candidates = candidate_sessions(db_path, target, limit=10_000)
    available_dates = [
        value
        for value in candidates.dates
        if value in common_dates
    ]
    replay_dates, evaluation_dates = split_replay_and_evaluation_dates(
        available_dates, start_date=start_date, end_date=end_date
    )
    if not evaluation_dates:
        raise ValueError("não há sessões completas na interseção solicitada")

    schedule = build_schedule(
        db_path,
        [target],
        cutoffs=cutoffs,
        forced_baskets={target: list(MIQUEIAS_FACTORS)},
    )
    v1_snapshots, _ = _replay_v1(db_path, replay_dates, copy.deepcopy(schedule), target)
    v2_snapshots, _ = _replay_v2(db_path, replay_dates, copy.deepcopy(schedule), target)

    observations = []
    discarded = dict(candidates.discarded)
    for session_date in evaluation_dates:
        if session_date not in v1_snapshots or session_date not in v2_snapshots:
            discarded[session_date] = "sem calibração PIT anterior ao cutoff"
            continue
        try:
            observations.append(build_observation(
                session_date,
                v1_snapshots[session_date],
                v2_snapshots[session_date],
                decision_time=decision_time,
                static_config=static_config,
            ))
        except (ValueError, TypeError, KeyError) as exc:
            discarded[session_date] = f"{type(exc).__name__}: {exc}"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "DESCRIPTIVE_ONLY",
        "target": target,
        "decision_time_brt": f"{decision_time[0]:02d}:{decision_time[1]:02d}",
        "methodology": {
            "type": "walk_forward_pit",
            "forced_factors": list(MIQUEIAS_FACTORS),
            "cutoffs": list(cutoffs),
            "calibration": "cada cutoff usa somente dados <= cutoff; replay não escreve no banco",
            "dynamic_state": "v2 encadeia estado Kalman cronologicamente entre sessões",
            "decision_bar": "última barra M5 comum fechada até decision_time_brt",
            "warmup_sessions": len(replay_dates) - len(evaluation_dates),
        },
        "miqueias_static_disclosed": {
            "effective_from": static_config.effective_from,
            "eligible_sessions": sum(
                observation.miqueias_static_disclosed is not None for observation in observations
            ),
            "role": "diagnóstico estático; não é réplica v2 nem critério de promoção",
        },
        "metrics": {
            "v1_pit": _metrics(observations, "v1_pit"),
            "v2_pit": _metrics(observations, "v2_pit"),
            "miqueias_static_disclosed": _metrics(observations, "miqueias_static_disclosed"),
        },
        "paired_brier": {
            "v2_minus_v1": paired_brier_delta(
                observations, left="v2_pit", right="v1_pit", iterations=bootstrap_iterations
            ),
            "miqueias_static_minus_v2": paired_brier_delta(
                observations,
                left="miqueias_static_disclosed",
                right="v2_pit",
                iterations=bootstrap_iterations,
            ),
        },
        "observations": [asdict(observation) for observation in observations],
        "discarded_sessions": discarded,
        "limitations": [
            "O braço Miqueias é a calibração estática divulgada em 2026-06-23; não há parâmetros históricos, Q/R ou estado Kalman para reproduzir o deploy dinâmico.",
            "O score em cada sessão usa somente snapshot no horário BRT solicitado; o outcome usa o fechamento posterior comum a v1/v2.",
            "Resultado descritivo: não troca P_up de produção nem promove campeão contra a série pública do Miqueias.",
        ],
    }


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-db",
        required=True,
        help="cópia SQLite fechada, nunca o banco vivo do collector",
    )
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--target", default=TARGET)
    parser.add_argument("--decision-time", default=DEFAULT_DECISION_TIME)
    parser.add_argument("--cutoffs", default=",".join(DEFAULT_CUTOFFS))
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP_ITERATIONS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.bootstrap <= 0:
        raise ValueError("bootstrap precisa ser positivo")
    decision_time = parse_clock(args.decision_time)
    cutoffs = tuple(sorted(value.strip() for value in args.cutoffs.split(",") if value.strip()))
    if not cutoffs:
        raise ValueError("é necessário informar ao menos um cutoff")
    for cutoff in cutoffs:
        date.fromisoformat(cutoff)
    input_snapshot = fingerprint_closed_snapshot(args.snapshot_db)
    report = run_walkforward(
        db_path=input_snapshot.path,
        target=args.target,
        cutoffs=cutoffs,
        decision_time=decision_time,
        bootstrap_iterations=args.bootstrap,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    if fingerprint_closed_snapshot(args.snapshot_db) != input_snapshot:
        raise RuntimeError(
            "o snapshot mudou durante o replay; descarte o relatório e gere uma cópia fechada"
        )
    report["input_snapshot"] = asdict(input_snapshot)
    _write_json(Path(args.output_json), report)
    print(
        f"P Dinâmico PIT: sessões={len(report['observations'])}; "
        f"v2-v1 Brier={report['paired_brier']['v2_minus_v1']['delta_brier']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
