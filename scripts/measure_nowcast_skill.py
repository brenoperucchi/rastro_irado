#!/usr/bin/env python3
"""Mede o skill OOS do P_up v2 como nowcast da direção da sessão."""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.irai.engine import IRAISnapshot
from backend.irai.timezones import brt_to_tickmill_offset_hours
from scripts.measure_d1_inflation import (
    BOOTSTRAP_ITERATIONS,
    DEFAULT_DB,
    DEFAULT_TARGETS,
    ShiftArm,
    _percentile,
    _real_snapshots,
    _table,
    candidate_sessions,
    load_initial_states,
    readonly_connection,
    readonly_engine,
)


CALIBRATION_CUTOFF = "2026-04-30"
EPSILON = 1e-12


@dataclass(frozen=True)
class SessionPoint:
    session_id: str
    hour_brt: int
    return_so_far: float
    p_up: float
    actual_up: bool


@dataclass(frozen=True)
class LossDelta:
    delta: float
    ci_low: float
    ci_high: float
    significant: bool


@dataclass(frozen=True)
class LossMetrics:
    brier: float
    log_loss: float
    n_bars: int
    n_sessions: int


@dataclass(frozen=True)
class HistoricalBaseline:
    by_hour_sign: dict[tuple[int, bool], tuple[int, int]]
    by_sign: dict[bool, tuple[int, int]]
    overall: tuple[int, int]

    @staticmethod
    def _smoothed(counts: tuple[int, int]) -> float:
        up, total = counts
        return (up + 1.0) / (total + 2.0)

    @property
    def climatology(self) -> float:
        return self._smoothed(self.overall)

    def probability(self, hour_brt: int, return_so_far: float) -> float:
        sign = return_so_far > 0.0
        counts = self.by_hour_sign.get((hour_brt, sign))
        if counts and counts[1] > 0:
            return self._smoothed(counts)
        counts = self.by_sign.get(sign)
        if counts and counts[1] > 0:
            return self._smoothed(counts)
        return self.climatology


def label_session(first_open: float, last_close: float) -> bool:
    """Empate pertence a BAIXA, conforme a definição do gate."""
    return last_close > first_open


def select_oos_dates(dates: Iterable[str], cutoff: str = CALIBRATION_CUTOFF) -> tuple[str, ...]:
    ordered = sorted(set(dates))
    if not ordered:
        return ()
    latest = ordered[-1]
    return tuple(date for date in ordered if date > cutoff and date != latest)


def _increment(store: dict, key, actual_up: bool) -> None:
    up, total = store.get(key, (0, 0))
    store[key] = (up + int(actual_up), total + 1)


def fit_historical_baseline(
    points: Iterable[SessionPoint],
    session_dates: dict[str, str],
    cutoff: str = CALIBRATION_CUTOFF,
) -> HistoricalBaseline:
    by_hour_sign: dict[tuple[int, bool], tuple[int, int]] = {}
    by_sign: dict[bool, tuple[int, int]] = {}
    session_labels = {}
    for point in points:
        if session_dates[point.session_id] > cutoff:
            continue
        session_labels[point.session_id] = point.actual_up
        sign = point.return_so_far > 0.0
        _increment(by_hour_sign, (point.hour_brt, sign), point.actual_up)
        _increment(by_sign, sign, point.actual_up)
    overall = (sum(session_labels.values()), len(session_labels))
    if overall[1] == 0:
        raise ValueError("baseline sem observações anteriores ao cutoff")
    return HistoricalBaseline(by_hour_sign, by_sign, overall)


def _probability_losses(probability: float, actual_up: bool) -> tuple[float, float]:
    probability = min(1.0 - EPSILON, max(EPSILON, probability))
    actual = float(actual_up)
    brier = (probability - actual) ** 2
    log_loss = -(actual * math.log(probability) + (1.0 - actual) * math.log(1.0 - probability))
    return brier, log_loss


def bootstrap_loss_delta(
    by_session: dict[str, list[tuple[float, float]]],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = 20260713,
) -> LossDelta:
    """Retorna loss(modelo) - loss(baseline); valor negativo favorece o modelo."""
    sessions = sorted(by_session)
    if not sessions:
        return LossDelta(*(float("nan"),) * 3, significant=False)
    totals = {
        date: (
            sum(model for model, _ in pairs),
            sum(baseline for _, baseline in pairs),
            len(pairs),
        )
        for date, pairs in by_session.items()
    }

    def sampled_delta(chosen: Iterable[str]) -> float:
        chosen = list(chosen)
        count = sum(totals[date][2] for date in chosen)
        model = sum(totals[date][0] for date in chosen)
        baseline = sum(totals[date][1] for date in chosen)
        return (model - baseline) / count

    delta = sampled_delta(sessions)
    rng = random.Random(seed)
    samples = [sampled_delta(rng.choices(sessions, k=len(sessions))) for _ in range(iterations)]
    low = _percentile(samples, 0.025)
    high = _percentile(samples, 0.975)
    return LossDelta(delta, low, high, low > 0.0 or high < 0.0)


def load_historical_points(db_path: str, target: str, cutoff: str) -> tuple[list[SessionPoint], dict[str, str]]:
    conn = readonly_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT timestamp_utc, open, close
               FROM market_bars
               WHERE symbol = ? AND timeframe = 'M5'
                 AND substr(timestamp_utc, 1, 10) <= ?
               ORDER BY timestamp_utc""",
            (target, cutoff),
        ).fetchall()
    finally:
        conn.close()

    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[row["timestamp_utc"][:10]].append(row)
    points = []
    dates = {}
    for date, session_rows in grouped.items():
        last_time = datetime.fromisoformat(session_rows[-1]["timestamp_utc"].replace("Z", "+00:00")).time()
        if (last_time.hour, last_time.minute) < (17, 55):
            continue
        first_open = float(session_rows[0]["open"])
        actual_up = label_session(first_open, float(session_rows[-1]["close"]))
        dates[date] = date
        for row in session_rows:
            close = float(row["close"])
            timestamp = datetime.fromisoformat(row["timestamp_utc"].replace("Z", "+00:00"))
            points.append(
                SessionPoint(date, timestamp.hour, (close - first_open) / first_open, 0.0, actual_up)
            )
    return points, dates


def replay_session_points(date: str, snapshots: list[IRAISnapshot]) -> tuple[list[SessionPoint] | None, str | None]:
    real = _real_snapshots(snapshots)
    if not real:
        return None, "sem snapshots reais"
    first_open = float(real[0].win_open)
    last_close = float(real[-1].win_current)
    if not math.isfinite(first_open) or first_open <= 0 or not math.isfinite(last_close):
        return None, "open/close da sessão ausente ou inválido"
    actual_up = label_session(first_open, last_close)
    offset_h = brt_to_tickmill_offset_hours(datetime.fromisoformat(f"{date}T12:00:00"))
    result = []
    for snapshot in real:
        close = float(snapshot.win_current)
        p_up = float(snapshot.p_up) / 100.0
        timestamp = datetime.fromisoformat(snapshot.timestamp.replace("Z", "+00:00"))
        hour_brt = (timestamp - timedelta(hours=offset_h)).hour
        if not math.isfinite(close) or close <= 0 or not math.isfinite(p_up):
            return None, "barra real com close/P_up inválido"
        result.append(SessionPoint(date, hour_brt, (close - first_open) / first_open, p_up, actual_up))
    return result, None


def replay_oos(
    db_path: str,
    target: str,
    dates: Iterable[str],
    initial_states: dict[str, dict | None],
) -> tuple[dict[str, list[SessionPoint]], dict[str, str]]:
    sessions = {}
    discarded = {}
    with readonly_engine(db_path, ShiftArm.FIXED, initial_states) as engine:
        for date in dates:
            snapshots = engine.compute_from_db(
                date, target=target, version="v2", persist_state=False
            )
            points, reason = replay_session_points(date, snapshots)
            if reason:
                discarded[date] = reason
            else:
                sessions[date] = points
    return sessions, discarded


def _metrics(points: Iterable[SessionPoint], predictor) -> LossMetrics:
    points = list(points)
    losses = [_probability_losses(predictor(point), point.actual_up) for point in points]
    return LossMetrics(
        sum(loss[0] for loss in losses) / len(losses),
        sum(loss[1] for loss in losses) / len(losses),
        len(points),
        len({point.session_id for point in points}),
    )


def _paired_losses(points: Iterable[SessionPoint], predictor_a, predictor_b, metric_index: int):
    result: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for point in points:
        loss_a = _probability_losses(predictor_a(point), point.actual_up)[metric_index]
        loss_b = _probability_losses(predictor_b(point), point.actual_up)[metric_index]
        result[point.session_id].append((loss_a, loss_b))
    return dict(result)


def _reliability_rows(target: str, scope: str, points: Iterable[SessionPoint]) -> list[tuple[str, ...]]:
    buckets: dict[int, list[SessionPoint]] = defaultdict(list)
    for point in points:
        bucket = min(9, max(0, int(point.p_up * 10.0)))
        buckets[bucket].append(point)
    rows = []
    for bucket in sorted(buckets):
        values = buckets[bucket]
        rows.append((
            target,
            scope,
            f"{bucket * 10:02d}-{(bucket + 1) * 10:02d}%",
            str(len(values)),
            str(len({point.session_id for point in values})),
            f"{100.0 * sum(point.p_up for point in values) / len(values):.2f}%",
            f"{100.0 * sum(point.actual_up for point in values) / len(values):.2f}%",
            f"{100.0 * (sum(point.actual_up for point in values) / len(values) - sum(point.p_up for point in values) / len(values)):+.2f} pp",
        ))
    return rows


def _format_delta(result: LossDelta) -> tuple[str, str, str]:
    significance = "SIM" if result.significant else "NÃO (IC cruza zero)"
    return f"{result.delta:+.5f}", f"[{result.ci_low:+.5f}, {result.ci_high:+.5f}]", significance


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB, help="snapshot SQLite aberto somente para leitura")
    parser.add_argument("--target", nargs="+", choices=DEFAULT_TARGETS, default=list(DEFAULT_TARGETS))
    parser.add_argument("--cutoff", default=CALIBRATION_CUTOFF)
    parser.add_argument("--bootstrap", type=int, default=BOOTSTRAP_ITERATIONS)
    args = parser.parse_args()
    if args.bootstrap <= 0:
        parser.error("--bootstrap deve ser maior que zero")
    return args


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    initial_states = load_initial_states(args.db)
    score_rows = []
    delta_rows = []
    reliability_rows = []
    session_summaries = []

    print("Gate 2 Tactical Layer: P_up é avaliado como nowcast da direção FINAL da sessão.")
    print(f"Janela OOS: sessões estritamente posteriores a {args.cutoff}; última sessão descartada.")
    print("Engine: v2 corrigido do HEAD, cestas/pesos incumbentes carregados do snapshot.")
    print("Segurança: SQLite mode=ro + PRAGMA query_only; persist_state=False em TODA chamada.")
    print("Estado v2: SEM memória entre sessões. O kalman_state pré-deploy não tem")
    print("factor_signature e é ignorado pelo engine do HEAD; todas as sessões partem dos pesos estáticos.")
    print("Rótulo: ALTA somente se close final > open da primeira barra real; empate = BAIXA.")
    print("Baseline trivial: frequência histórica de ALTA condicionada a hora BRT + sinal")
    print("do retorno-até-agora, treinada até o cutoff, com suavização Beta(1,1).")
    print("Deltas abaixo = loss(P_up) - loss(baseline): NEGATIVO favorece P_up.")
    print()

    for target_index, target in enumerate(args.target):
        candidates = candidate_sessions(args.db, target, limit=10_000)
        oos_dates = tuple(date for date in candidates.dates if date > args.cutoff)
        historical_points, historical_dates = load_historical_points(args.db, target, args.cutoff)
        baseline = fit_historical_baseline(historical_points, historical_dates, args.cutoff)
        sessions, replay_discarded = replay_oos(args.db, target, oos_dates, initial_states)
        if replay_discarded:
            details = "; ".join(f"{date}: {reason}" for date, reason in replay_discarded.items())
            raise RuntimeError(f"{target}: sessões OOS inválidas no replay: {details}")
        points = [point for date in sorted(sessions) for point in sessions[date]]
        if not points:
            raise RuntimeError(f"{target}: nenhuma sessão OOS após {args.cutoff}")

        eval_up_sessions = sum(session_points[0].actual_up for session_points in sessions.values())
        tie_sessions = sum(
            math.isclose(session_points[-1].return_so_far, 0.0, abs_tol=1e-15)
            for session_points in sessions.values()
        )
        historical_session_labels = {}
        for point in historical_points:
            historical_session_labels[point.session_id] = point.actual_up
        historical_up = sum(historical_session_labels.values())
        historical_n = len(historical_session_labels)
        latest_discard = "; ".join(
            f"{date}: {reason}" for date, reason in candidates.discarded.items()
            if "última data" in reason
        ) or "não identificada"
        session_summaries.append(
            f"{target}: OOS={len(sessions)} sessões ({min(sessions)}..{max(sessions)}), "
            f"ALTA={eval_up_sessions}, BAIXA={len(sessions)-eval_up_sessions}, empates={tie_sessions}; "
            f"taxa-base OOS={100.0*eval_up_sessions/len(sessions):.2f}%; "
            f"treino baseline={historical_n} sessões, ALTA={100.0*historical_up/historical_n:.2f}%, "
            f"climatologia suavizada={100.0*baseline.climatology:.2f}%; descarte={latest_discard}"
        )

        predictors = {
            "P_up": lambda point: point.p_up,
            "TRIVIAL": lambda point, b=baseline: b.probability(point.hour_brt, point.return_so_far),
            "CLIMA": lambda _point, b=baseline: b.climatology,
        }
        scopes = [("TODAS", points)] + [
            (f"{hour:02d}h", [point for point in points if point.hour_brt == hour])
            for hour in range(9, 19)
        ]
        for scope_index, (scope, scoped_points) in enumerate(scopes):
            if not scoped_points:
                continue
            metrics = {name: _metrics(scoped_points, predictor) for name, predictor in predictors.items()}
            for name in ("P_up", "TRIVIAL", "CLIMA"):
                value = metrics[name]
                score_rows.append((
                    target, scope, name, f"{value.brier:.5f}", f"{value.log_loss:.5f}",
                    str(value.n_bars), str(value.n_sessions),
                ))
            for baseline_name in ("TRIVIAL", "CLIMA"):
                for metric_index, metric_name in enumerate(("BRIER", "LOGLOSS")):
                    paired = _paired_losses(
                        scoped_points, predictors["P_up"], predictors[baseline_name], metric_index
                    )
                    result = bootstrap_loss_delta(
                        paired,
                        iterations=args.bootstrap,
                        seed=20260713 + target_index * 10_000 + scope_index * 100 + metric_index * 10
                        + (0 if baseline_name == "TRIVIAL" else 1),
                    )
                    delta, interval, significance = _format_delta(result)
                    delta_rows.append((target, scope, metric_name, f"P_up-{baseline_name}", delta, interval, significance))
            reliability_rows.extend(_reliability_rows(target, scope, scoped_points))

    print("SESSÕES E TAXAS-BASE:")
    for summary in session_summaries:
        print(f"  {summary}")
    print()
    print("SCORES (menor é melhor):")
    print(_table(("TARGET", "HORA", "MODELO", "BRIER", "LOGLOSS", "N BARRAS", "N SESSÕES"), score_rows))
    print()
    print("COMPARAÇÕES | bootstrap pareado por sessão, IC95%:")
    print(_table(("TARGET", "HORA", "MÉTRICA", "DELTA", "VALOR", "IC95%", "SIGNIF."), delta_rows))
    print()
    print("RELIABILITY DO P_up (decis; gap = observado - previsto):")
    print(_table(("TARGET", "HORA", "BUCKET", "N BARRAS", "N SESSÕES", "P MÉDIA", "ALTA OBS.", "GAP"), reliability_rows))
    print()
    print(f"Tempo total: {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
