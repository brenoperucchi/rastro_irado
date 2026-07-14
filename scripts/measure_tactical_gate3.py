#!/usr/bin/env python3
"""Gate 3b: valor incremental do macro sobre preço próprio, por faixa temporal."""

from __future__ import annotations

import argparse
import io
import json
import math
import random
import sqlite3
import sys
import time
import warnings
from collections import defaultdict
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from scipy.special import expit, logit
from scipy.stats import ConstantInputWarning, norm, rankdata, spearmanr
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import roc_auc_score

from backend.irai.timezones import brt_to_tickmill_offset_hours
from scripts import calibrate_universal as calibrator
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
from scripts.measure_nowcast_skill import (
    CALIBRATION_CUTOFF,
    HistoricalBaseline,
    SessionPoint,
    _paired_losses,
    _probability_losses,
    bootstrap_loss_delta,
    fit_historical_baseline,
)


HOURS = tuple(range(9, 19))
HORIZONS = (3, 6, 20)
MOMENTUM_LAGS = (1, 3, 6, 20)
TIME_SCOPES = ("OPEN_3", "OPEN_6", "OPEN_12", "OPEN_20", "09_10", "11_18")
MINIMUM_USEFUL_DELTA_AUC = 0.02
TARGET_COST_POINTS = {"WIN$N": 10.0, "WDO$N": 1.0}
EPSILON = 1e-6


@dataclass(frozen=True)
class GateBar:
    session_id: str
    hour_brt: int
    close: float
    p_up: float
    actual_up: bool
    price_diverge_z: float
    open_price: float | None = None
    high: float | None = None
    low: float | None = None
    atr14_points: float = 0.0
    bar_index: int = 0


@dataclass(frozen=True)
class ForwardRow:
    session_id: str
    horizon: int
    forward_return: float
    actual_up: bool
    momentum: tuple[float, ...]
    macro: tuple[float, ...]
    bar_index: int = 0
    hour_brt: int = 0
    close: float = 0.0
    atr14_points: float = 0.0


@dataclass(frozen=True)
class Estimate:
    value: float
    ci_low: float
    ci_high: float
    n_sessions: int
    significant: bool
    standard_error: float = float("nan")


@dataclass(frozen=True)
class PlattModel:
    coef_: float
    intercept_: float

    def predict(self, probability: float) -> float:
        score = float(logit(np.clip(probability, EPSILON, 1.0 - EPSILON)))
        return float(expit(self.coef_ * score + self.intercept_))


@dataclass(frozen=True)
class StandardizedLogit:
    means: np.ndarray
    scales: np.ndarray
    model: LogisticRegression

    def probabilities(self, values: Iterable[Iterable[float]]) -> np.ndarray:
        matrix = np.asarray(list(values), dtype=float)
        return self.model.predict_proba((matrix - self.means) / self.scales)

    def predict(self, values: Iterable[Iterable[float]]) -> np.ndarray:
        return self.probabilities(values)[:, 1]


@dataclass(frozen=True)
class MacroResidualizer:
    """Projeção macro~preço ajustada somente no treino pré-janela."""

    model: Ridge
    means: np.ndarray
    scales: np.ndarray

    def transform(self, rows: Iterable[ForwardRow]) -> np.ndarray:
        rows = list(rows)
        own_price = np.asarray([row.momentum for row in rows], dtype=float)
        macro = np.asarray([row.macro for row in rows], dtype=float)
        return macro - self.model.predict((own_price - self.means) / self.scales)


def versions_to_replay(version: str) -> tuple[str, ...]:
    if version == "both":
        return ("v1", "v2")
    if version in {"v1", "v2"}:
        return (version,)
    raise ValueError(f"versão inválida: {version}")


def rows_in_scope(rows: Iterable[ForwardRow], scope: str) -> list[ForwardRow]:
    rows = list(rows)
    if scope.startswith("OPEN_"):
        limit = int(scope.split("_", 1)[1])
        return [row for row in rows if row.bar_index < limit]
    if scope == "09_10":
        return [row for row in rows if row.hour_brt in {9, 10}]
    if scope == "11_18":
        return [row for row in rows if 11 <= row.hour_brt <= 18]
    if scope == "ALL":
        return rows
    raise ValueError(f"faixa temporal inválida: {scope}")


def temporal_crossfit_slices(dates: Iterable[str], n_splits: int = 4):
    """Folds contíguas; o cutoff de cada uma é estritamente anterior à fold."""
    dates = sorted(dates)
    if not dates:
        return []
    n_splits = max(1, min(int(n_splits), len(dates)))
    folds = np.array_split(np.asarray(dates, dtype=object), n_splits)
    result = []
    for fold in folds:
        values = [str(value) for value in fold]
        first = datetime.strptime(values[0], "%Y-%m-%d").date()
        result.append(((first - timedelta(days=1)).isoformat(), values))
    return result


def multinomial_label(
    forward_return: float, *, close: float, atr14_points: float, cost_points: float
) -> int:
    threshold_points = max(float(cost_points), 0.10 * float(atr14_points))
    threshold_return = threshold_points / float(close)
    if forward_return > threshold_return:
        return 1
    if forward_return < -threshold_return:
        return -1
    return 0


def required_sessions_for_power(
    *, current_sessions: int, bootstrap_standard_error: float,
    minimum_delta_auc: float = MINIMUM_USEFUL_DELTA_AUC, power: float = 0.80,
    alpha: float = 0.05,
) -> int:
    """Escala a incerteza clusterizada como 1/sqrt(N), prospectivamente."""
    if current_sessions <= 0 or bootstrap_standard_error <= 0 or minimum_delta_auc <= 0:
        return 0
    critical = norm.ppf(1.0 - alpha / 2.0) + norm.ppf(power)
    return int(math.ceil(
        current_sessions * (critical * bootstrap_standard_error / minimum_delta_auc) ** 2
    ))


def _safe_auc(labels: list[bool], scores: list[float]) -> float:
    if len(set(labels)) < 2:
        return float("nan")
    return float(roc_auc_score(labels, scores))


def _bootstrap_sessions(by_session, statistic, iterations: int, seed: int) -> list[float]:
    sessions = sorted(by_session)
    rng = random.Random(seed)
    values = []
    for _ in range(iterations):
        chosen = rng.choices(sessions, k=len(sessions))
        sample = [row for session in chosen for row in by_session[session]]
        value = statistic(sample)
        if math.isfinite(value):
            values.append(value)
    return values


def bootstrap_auc(
    rows: Iterable[tuple[str, float, bool]], *, iterations=BOOTSTRAP_ITERATIONS, seed=20260713
) -> Estimate:
    rows = list(rows)
    by_session = defaultdict(list)
    for session, score, label in rows:
        by_session[session].append((score, label))
    value = _safe_auc([label for _, _, label in rows], [score for _, score, _ in rows])
    samples = _bootstrap_sessions(
        by_session,
        lambda sample: _safe_auc([label for _, label in sample], [score for score, _ in sample]),
        iterations,
        seed,
    )
    low, high = _percentile(samples, 0.025), _percentile(samples, 0.975)
    se = float(np.std(samples, ddof=1)) if len(samples) > 1 else float("nan")
    return Estimate(value, low, high, len(by_session), low > 0.5 or high < 0.5, se)


def hourly_auc_comparison(
    v2_bars: Iterable[GateBar], v1_bars: Iterable[GateBar], *,
    iterations=BOOTSTRAP_ITERATIONS, seed=20260713,
) -> tuple[Estimate, Estimate, Estimate]:
    """Compara o score dinâmico, o exógeno e o retorno do próprio target."""
    v2_bars, v1_bars = list(v2_bars), list(v1_bars)
    if [(b.session_id, b.hour_brt, b.close) for b in v2_bars] != [
        (b.session_id, b.hour_brt, b.close) for b in v1_bars
    ]:
        raise ValueError("replays v1/v2 desalinhados")
    v2 = bootstrap_auc(
        [(b.session_id, b.p_up, b.actual_up) for b in v2_bars],
        iterations=iterations, seed=seed,
    )
    v1 = bootstrap_auc(
        [(b.session_id, b.p_up, b.actual_up) for b in v1_bars],
        iterations=iterations, seed=seed + 100,
    )
    own_return = bootstrap_auc(
        [(b.session_id, b.close / float(b.open_price) - 1.0, b.actual_up) for b in v2_bars],
        iterations=iterations, seed=seed + 200,
    )
    return v2, v1, own_return


def bootstrap_auc_delta(
    rows: Iterable[tuple[str, float, float, bool]], *, iterations=BOOTSTRAP_ITERATIONS,
    seed=20260713,
) -> Estimate:
    rows = list(rows)
    by_session = defaultdict(list)
    for session, macro, baseline, label in rows:
        by_session[session].append((macro, baseline, label))

    def statistic(sample):
        labels = [label for _, _, label in sample]
        return _safe_auc(labels, [macro for macro, _, _ in sample]) - _safe_auc(
            labels, [baseline for _, baseline, _ in sample]
        )

    value = statistic([(macro, baseline, label) for _, macro, baseline, label in rows])
    samples = _bootstrap_sessions(by_session, statistic, iterations, seed)
    low, high = _percentile(samples, 0.025), _percentile(samples, 0.975)
    se = float(np.std(samples, ddof=1)) if len(samples) > 1 else float("nan")
    return Estimate(value, low, high, len(by_session), low > 0 or high < 0, se)


def clustered_spearman(
    by_session: dict[str, list[tuple[float, float]]], *, iterations=BOOTSTRAP_ITERATIONS,
    seed=20260713,
) -> Estimate:
    def statistic(sample):
        if len(sample) < 3:
            return float("nan")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConstantInputWarning)
            value = spearmanr([x for x, _ in sample], [y for _, y in sample]).statistic
        return float(value) if math.isfinite(value) else float("nan")

    rows = [row for session in sorted(by_session) for row in by_session[session]]
    value = statistic(rows)
    samples = _bootstrap_sessions(by_session, statistic, iterations, seed)
    low, high = _percentile(samples, 0.025), _percentile(samples, 0.975)
    se = float(np.std(samples, ddof=1)) if len(samples) > 1 else float("nan")
    return Estimate(value, low, high, len(by_session), low > 0 or high < 0, se)


def fit_hourly_platt(bars: Iterable[GateBar], cutoff: str) -> dict[int, PlattModel]:
    """Platt por hora; a filtragem interna torna o cutoff uma invariante testável."""
    grouped = defaultdict(list)
    for bar in bars:
        if bar.session_id <= cutoff and bar.hour_brt in HOURS:
            grouped[bar.hour_brt].append(bar)
    models = {}
    for hour, values in grouped.items():
        labels = np.asarray([bar.actual_up for bar in values], dtype=int)
        scores = logit(np.clip([bar.p_up for bar in values], EPSILON, 1.0 - EPSILON))
        if len(np.unique(labels)) < 2:
            probability = (labels.sum() + 1.0) / (len(labels) + 2.0)
            models[hour] = PlattModel(0.0, float(logit(probability)))
            continue
        model = LogisticRegression(C=1.0, max_iter=2_000)
        model.fit(scores.reshape(-1, 1), labels)
        models[hour] = PlattModel(float(model.coef_[0, 0]), float(model.intercept_[0]))
    return models


def apply_calibration(engine, target: str, result: dict) -> None:
    """Injeta o dry-run no replay em memória; não escreve model_params."""
    slug = engine.target_slugs[target]
    model = engine.models[slug]
    model["factors"] = list(result["factors"])
    model["factor_labels"] = dict(result["factor_labels"])
    model["weights"] = {f"w_{label}": value for label, value in result["weights"].items()}
    model["sigmas"] = dict(result["sigmas"])
    model["alpha"] = float(result["alpha"])
    model["intercept"] = float(result["intercept"])


def load_target_ohlc(db_path: str, target: str, dates: Iterable[str]):
    dates = sorted(set(dates))
    if not dates:
        return {}
    placeholders = ",".join("?" for _ in dates)
    conn = readonly_connection(db_path)
    try:
        rows = conn.execute(
            f"""SELECT substr(timestamp_utc, 1, 10) AS session_id,
                       timestamp_utc, open, high, low, close
                FROM market_bars
                WHERE symbol=? AND timeframe='M5'
                  AND substr(timestamp_utc, 1, 10) IN ({placeholders})
                ORDER BY timestamp_utc""",
            [target, *dates],
        ).fetchall()
    finally:
        conn.close()
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["session_id"]].append(dict(row))
    return dict(grouped)


def _atr14_points(rows: list[dict]) -> list[float]:
    true_ranges = []
    result = []
    previous_close = None
    for row in rows:
        high, low, close = float(row["high"]), float(row["low"]), float(row["close"])
        components = [high - low]
        if previous_close is not None:
            components.extend((abs(high - previous_close), abs(low - previous_close)))
        true_ranges.append(max(components))
        result.append(float(np.mean(true_ranges[-14:])))
        previous_close = close
    return result


def replay_bars(db_path, target, dates, initial_states, calibration, *, version="v2"):
    sessions = {}
    discarded = {}
    ohlc = load_target_ohlc(db_path, target, dates)
    with readonly_engine(db_path, ShiftArm.FIXED, initial_states) as engine:
        apply_calibration(engine, target, calibration)
        for date in dates:
            snapshots = engine.compute_from_db(
                date, target=target, version=version, persist_state=False
            )
            real = _real_snapshots(snapshots)
            if not real:
                discarded[date] = "sem snapshots reais"
                continue
            raw_bars = ohlc.get(date, [])
            if len(raw_bars) != len(real):
                discarded[date] = (
                    f"OHLC/replay desalinhado ({len(raw_bars)} barras vs {len(real)} snapshots)"
                )
                continue
            atr14 = _atr14_points(raw_bars)
            first_open = float(real[0].win_open)
            last_close = float(real[-1].win_current)
            if first_open <= 0 or not math.isfinite(first_open) or not math.isfinite(last_close):
                discarded[date] = "open/close inválido"
                continue
            actual_up = last_close > first_open
            offset_h = brt_to_tickmill_offset_hours(datetime.fromisoformat(f"{date}T12:00:00"))
            bars = []
            for raw_index, (snapshot, raw_bar) in enumerate(zip(real, raw_bars)):
                timestamp = datetime.fromisoformat(snapshot.timestamp.replace("Z", "+00:00"))
                hour = (timestamp - timedelta(hours=offset_h)).hour
                close = float(snapshot.win_current)
                p_up = float(snapshot.p_up) / 100.0
                price_z = float(snapshot.price_diverge_z or 0.0)
                if hour in HOURS and close > 0 and math.isfinite(p_up):
                    bars.append(GateBar(
                        date, hour, close, p_up, actual_up, price_z, first_open,
                        float(raw_bar["high"]), float(raw_bar["low"]), atr14[raw_index], len(bars),
                    ))
            if bars:
                sessions[date] = bars
            else:
                discarded[date] = "nenhuma barra entre 09h e 18h BRT"
    return sessions, discarded


def crossfit_replay_bars(
    db_path, target, training_dates, initial_states, final_calibration, *,
    versions=("v1", "v2"), n_splits=4, holdout_sessions=20,
):
    """Gera macro de treino com parâmetros calibrados só antes de cada fold."""
    conn = readonly_connection(db_path)
    try:
        daily = calibrator.load_daily_returns(conn, 0, 24, target)
    finally:
        conn.close()
    by_version = {version: {} for version in versions}
    metadata = []
    for fold_index, (fold_cutoff, fold_dates) in enumerate(
        temporal_crossfit_slices(training_dates, n_splits=n_splits), start=1
    ):
        captured = io.StringIO()
        with redirect_stdout(captured):
            fold_calibration = calibrator.calibrate_target(
                None, target, min_factors=len(final_calibration["factors"]),
                max_factors=len(final_calibration["factors"]),
                forced_factors=final_calibration["factors"],
                holdout_sessions=holdout_sessions, as_of=fold_cutoff,
                daily_override=daily,
            )
        if not fold_calibration:
            raise RuntimeError(
                f"{target}: calibração cross-fit falhou na fold {fold_index} ({fold_cutoff})\n"
                f"{captured.getvalue()}"
            )
        metadata.append((fold_index, fold_cutoff, len(fold_dates), fold_calibration["n_sessions"]))
        for version in versions:
            sessions, discarded = replay_bars(
                db_path, target, fold_dates, initial_states, fold_calibration, version=version
            )
            if discarded:
                raise RuntimeError(
                    f"{target}/{version}: sessões cross-fit inválidas: {discarded}"
                )
            by_version[version].update(sessions)
    return by_version, metadata


def _return_at(bars: list[GateBar], index: int, lag: int) -> float:
    return bars[index].close / bars[index - lag].close - 1.0


def select_evaluation_dates(dates, *, cutoff: str, eval_start=None, eval_end=None) -> list[str]:
    """Seleciona uma janela OOS fechada, sempre estritamente posterior ao cutoff."""
    return [
        date for date in dates
        if date > cutoff
        and (eval_start is None or date >= eval_start)
        and (eval_end is None or date <= eval_end)
    ]


def forward_rows(bars: list[GateBar], horizon: int) -> list[ForwardRow]:
    result = []
    p_values = np.asarray([bar.p_up for bar in bars])
    p_changes = np.diff(p_values, prepend=p_values[0])
    # Não existe warm-up global: cada barra usa apenas os lags que já existem.
    # Lags ainda indisponíveis na abertura são 0; retorno-desde-a-abertura segue
    # disponível e impede que OPEN_3/6/12/20 desapareçam do desenho.
    for index in range(max(0, len(bars) - horizon)):
        current, future = bars[index], bars[index + horizon]
        momentum = tuple(
            _return_at(bars, index, lag) if index >= lag else 0.0
            for lag in MOMENTUM_LAGS
        ) + (
            current.close / float(current.open_price) - 1.0,
        )
        macro_lag = min(index, horizon)
        delta = current.p_up - bars[index - macro_lag].p_up if macro_lag else 0.0
        changes = p_changes[max(1, index - horizon + 1):index + 1]
        persistence = float(np.mean(np.sign(changes))) if len(changes) else 0.0
        # Contrapõe direções em escala limitada; é a versão contínua do bool da API.
        divergence = (2.0 * current.p_up - 1.0) - math.tanh(current.price_diverge_z)
        macro = (current.p_up - 0.5, delta, persistence, divergence)
        forward_return = future.close / current.close - 1.0
        result.append(ForwardRow(
            current.session_id, horizon, forward_return, forward_return > 0, momentum, macro,
            index, current.hour_brt, current.close, current.atr14_points,
        ))
    return result


def fit_standardized_logit(values, labels) -> StandardizedLogit:
    matrix = np.asarray(values, dtype=float)
    labels = np.asarray(labels, dtype=int)
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    scales[scales == 0] = 1.0
    model = LogisticRegression(C=1.0, max_iter=2_000)
    model.fit((matrix - means) / scales, labels)
    return StandardizedLogit(means, scales, model)


def fit_residualized_nested(rows: Iterable[ForwardRow]):
    rows = list(rows)
    if not rows:
        raise ValueError("modelo aninhado sem observações")
    labels = [row.actual_up for row in rows]
    residualizer = fit_macro_residualizer(rows, cutoff=max(row.session_id for row in rows))
    residual_macro = residualizer.transform(rows)
    baseline = fit_standardized_logit([row.momentum for row in rows], labels)
    treatment = fit_standardized_logit(
        [row.momentum + tuple(residual) for row, residual in zip(rows, residual_macro)], labels,
    )
    return baseline, treatment, residualizer


def predict_residualized_nested(models, rows: Iterable[ForwardRow]):
    baseline, treatment, residualizer = models
    rows = list(rows)
    residual_macro = residualizer.transform(rows)
    baseline_probability = baseline.predict(row.momentum for row in rows)
    treatment_probability = treatment.predict(
        row.momentum + tuple(residual) for row, residual in zip(rows, residual_macro)
    )
    return baseline_probability, treatment_probability


def _class_probabilities(model: StandardizedLogit, values, classes=(-1, 0, 1)):
    predicted = model.probabilities(values)
    expanded = np.zeros((len(predicted), len(classes)), dtype=float)
    positions = {value: index for index, value in enumerate(classes)}
    for source_index, label in enumerate(model.model.classes_):
        expanded[:, positions[int(label)]] = predicted[:, source_index]
    return expanded


def fit_residualized_multinomial(rows: Iterable[ForwardRow], cost_points: float):
    rows = list(rows)
    labels = [
        multinomial_label(
            row.forward_return, close=row.close, atr14_points=row.atr14_points,
            cost_points=cost_points,
        )
        for row in rows
    ]
    if len(set(labels)) < 3:
        raise ValueError("contrato multinomial exige UP/NEUTRAL/DOWN no treino")
    residualizer = fit_macro_residualizer(rows, cutoff=max(row.session_id for row in rows))
    residual_macro = residualizer.transform(rows)
    baseline = fit_standardized_logit([row.momentum for row in rows], labels)
    treatment = fit_standardized_logit(
        [row.momentum + tuple(residual) for row, residual in zip(rows, residual_macro)], labels,
    )
    return baseline, treatment, residualizer


def predict_residualized_multinomial(models, rows: Iterable[ForwardRow]):
    baseline, treatment, residualizer = models
    rows = list(rows)
    residual_macro = residualizer.transform(rows)
    baseline_probability = _class_probabilities(
        baseline, [row.momentum for row in rows]
    )
    treatment_probability = _class_probabilities(
        treatment,
        [row.momentum + tuple(residual) for row, residual in zip(rows, residual_macro)],
    )
    return baseline_probability, treatment_probability


def _macro_ovr_auc(labels, probabilities, classes=(-1, 0, 1)) -> float:
    labels = np.asarray(labels)
    probabilities = np.asarray(probabilities, dtype=float)
    aucs = []
    for index, label in enumerate(classes):
        binary = labels == label
        if len(np.unique(binary)) == 2:
            aucs.append(roc_auc_score(binary, probabilities[:, index]))
    return float(np.mean(aucs)) if len(aucs) >= 2 else float("nan")


def bootstrap_multiclass_auc_delta(
    rows, *, iterations=BOOTSTRAP_ITERATIONS, seed=20260713,
) -> Estimate:
    rows = list(rows)
    by_session = defaultdict(list)
    for session, macro, baseline, label in rows:
        by_session[session].append((np.asarray(macro), np.asarray(baseline), int(label)))

    def statistic(sample):
        labels = [label for _, _, label in sample]
        return _macro_ovr_auc(labels, [macro for macro, _, _ in sample]) - _macro_ovr_auc(
            labels, [baseline for _, baseline, _ in sample]
        )

    flat = [value for session in sorted(by_session) for value in by_session[session]]
    value = statistic(flat)
    samples = _bootstrap_sessions(by_session, statistic, iterations, seed)
    low, high = _percentile(samples, 0.025), _percentile(samples, 0.975)
    se = float(np.std(samples, ddof=1)) if len(samples) > 1 else float("nan")
    return Estimate(value, low, high, len(by_session), low > 0 or high < 0, se)


def hourly_incremental_delta(
    training: Iterable[GateBar], evaluation: Iterable[GateBar], *,
    iterations=BOOTSTRAP_ITERATIONS, seed=20260713,
):
    """P_up residualizado contra retorno-so-far; tudo ajustado pré-janela."""
    training, evaluation = list(training), list(evaluation)
    train_return = np.asarray([
        bar.close / float(bar.open_price) - 1.0 for bar in training
    ]).reshape(-1, 1)
    return_mean = train_return.mean(axis=0)
    return_scale = train_return.std(axis=0)
    return_scale[return_scale == 0] = 1.0
    train_return_z = (train_return - return_mean) / return_scale
    train_p = np.asarray([bar.p_up for bar in training])
    residualizer = Ridge(alpha=1.0).fit(train_return_z, train_p)
    train_residual = train_p - residualizer.predict(train_return_z)
    labels = [bar.actual_up for bar in training]
    baseline = fit_standardized_logit(train_return, labels)
    treatment = fit_standardized_logit(
        np.column_stack((train_return[:, 0], train_residual)), labels
    )
    eval_return = np.asarray([
        bar.close / float(bar.open_price) - 1.0 for bar in evaluation
    ]).reshape(-1, 1)
    eval_return_z = (eval_return - return_mean) / return_scale
    eval_residual = np.asarray([bar.p_up for bar in evaluation]) - residualizer.predict(
        eval_return_z
    )
    baseline_probability = baseline.predict(eval_return)
    treatment_probability = treatment.predict(
        np.column_stack((eval_return[:, 0], eval_residual))
    )
    delta = bootstrap_auc_delta(
        [(bar.session_id, float(tp), float(bp), bar.actual_up)
         for bar, tp, bp in zip(evaluation, treatment_probability, baseline_probability)],
        iterations=iterations, seed=seed,
    )
    return (
        _safe_auc([bar.actual_up for bar in evaluation], list(baseline_probability)),
        _safe_auc([bar.actual_up for bar in evaluation], list(treatment_probability)),
        delta,
    )


def fit_nested_models(rows: Iterable[ForwardRow], cutoff: str):
    """Ajusta os dois braços usando exclusivamente sessões até o cutoff."""
    training = [row for row in rows if row.session_id <= cutoff]
    if not training:
        raise ValueError("modelo aninhado sem observações pré-janela")
    labels = [row.actual_up for row in training]
    baseline = fit_standardized_logit([row.momentum for row in training], labels)
    macro = fit_standardized_logit([row.momentum + row.macro for row in training], labels)
    return baseline, macro


def fit_macro_residualizer(rows: Iterable[ForwardRow], cutoff: str) -> MacroResidualizer:
    training = [row for row in rows if row.session_id <= cutoff]
    if not training:
        raise ValueError("residualizador sem observações pré-janela")
    own_price = np.asarray([row.momentum for row in training], dtype=float)
    means = own_price.mean(axis=0)
    scales = own_price.std(axis=0)
    scales[scales == 0] = 1.0
    model = Ridge(alpha=1.0)
    model.fit(
        (own_price - means) / scales,
        np.asarray([row.macro for row in training], dtype=float),
    )
    return MacroResidualizer(model, means, scales)


def _loss_metrics(bars, predictor):
    losses = [_probability_losses(predictor(bar), bar.actual_up) for bar in bars]
    return np.mean(losses, axis=0)


def _decile_rows(target, horizon, feature, values):
    if not values:
        return []
    ranks = rankdata([value for value, _, _ in values], method="average")
    deciles = np.minimum(9, ((ranks - 1) * 10 / len(values)).astype(int))
    rows = []
    for decile in sorted(set(deciles)):
        selected = [values[i] for i in range(len(values)) if deciles[i] == decile]
        rows.append((
            target, str(horizon), feature, f"D{decile + 1:02d}", str(len(selected)),
            str(len({session for _, _, session in selected})),
            f"{np.mean([value for value, _, _ in selected]):+.5f}",
            f"{10_000*np.mean([ret for _, ret, _ in selected]):+.3f}",
            f"{100*np.mean([ret > 0 for _, ret, _ in selected]):.2f}%",
        ))
    return rows


def _legacy_parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--calibration-json", required=True)
    parser.add_argument("--target", nargs="+", choices=DEFAULT_TARGETS, default=list(DEFAULT_TARGETS))
    parser.add_argument("--cutoff", default=CALIBRATION_CUTOFF)
    parser.add_argument("--eval-start", default=None, metavar="YYYY-MM-DD")
    parser.add_argument("--eval-end", default=None, metavar="YYYY-MM-DD")
    parser.add_argument(
        "--version", choices=("v1", "v2", "both"), default="both",
        help="Replay principal; 'both' mede v2 e o braço exógeno v1 (default).",
    )
    parser.add_argument("--train-sessions", type=int, default=252)
    parser.add_argument("--bootstrap", type=int, default=BOOTSTRAP_ITERATIONS)
    args = parser.parse_args()
    if args.train_sessions <= 0 or args.bootstrap <= 0:
        parser.error("--train-sessions e --bootstrap devem ser positivos")
    if args.eval_start and args.eval_end and args.eval_start > args.eval_end:
        parser.error("--eval-start deve ser anterior ou igual a --eval-end")
    if args.eval_start and args.eval_start <= args.cutoff:
        parser.error("--eval-start deve ser posterior ao --cutoff")
    return args


def _legacy_main() -> int:
    args = _legacy_parse_args()
    started = time.perf_counter()
    with open(args.calibration_json, encoding="utf-8") as calibration_file:
        artifact = json.load(calibration_file)
    if artifact.get("as_of") != args.cutoff:
        raise ValueError("o cutoff do artefato de calibração difere do cutoff do Gate 3")

    initial_states = load_initial_states(args.db)
    auc_rows, auc_comparison_rows, score_rows, loss_delta_rows = [], [], [], []
    ic_rows, decile_rows, nested_rows, summaries = [], [], [], []
    feature_names = ("P_LEVEL", "DELTA_P", "PERSISTENCIA", "DIVERGENCIA")

    print(f"Gate 3 Tactical Layer — replay {args.version} com calibração pré-janela.")
    print(
        f"Cutoff inclusivo de treino: {args.cutoff}; avaliação: "
        f"{args.eval_start or 'primeira sessão posterior'}..{args.eval_end or 'última disponível'}."
    )
    print("Segurança: snapshot mode=ro + PRAGMA query_only; persist_state=False em TODA chamada.")
    print("Pesos do dry-run são injetados apenas no engine em memória; nenhuma tabela é alterada.")
    print("Baseline aninhado: retornos próprios 1/3/6/20 barras + retorno desde a abertura.")
    print("Macro: nível, Δ no horizonte, persistência e divergência contínua preço-vs-P_up.")
    print("Escopo decisório: horizontes táticos h=3/6; h=20 é reportado, mas não decide a morte.")
    print("NOTA PLATT: os P_up de treino usam os pesos Ridge finais, refitados também no holdout;")
    print("a inclinação pode ser otimista. Platt é monotônico e, portanto, não altera AUC.")
    print("NOTA KALMAN: apply_calibration substitui o dict sigmas inteiro; kalman_trans_cov/")
    print("kalman_obs_cov eventualmente tunados são descartados e o replay usa os defaults do engine.")
    print()

    for target_index, target in enumerate(args.target):
        calibration = artifact["targets"].get(target)
        if not calibration:
            raise ValueError(f"artefato sem calibração para {target}")
        candidates = candidate_sessions(args.db, target, limit=10_000)
        training_dates = [date for date in candidates.dates if date <= args.cutoff][-args.train_sessions:]
        oos_dates = select_evaluation_dates(
            candidates.dates, cutoff=args.cutoff,
            eval_start=args.eval_start, eval_end=args.eval_end,
        )
        primary_version = "v1" if args.version == "v1" else "v2"
        sessions, discarded = replay_bars(
            args.db, target, training_dates + oos_dates, initial_states, calibration,
            version=primary_version,
        )
        comparison_sessions = None
        if args.version == "both":
            comparison_sessions, comparison_discarded = replay_bars(
                args.db, target, oos_dates, initial_states, calibration,
                version="v1",
            )
            discarded = {**discarded, **comparison_discarded}
        if discarded:
            raise RuntimeError(f"{target}: sessões inválidas: {discarded}")
        training = [bar for date in training_dates for bar in sessions.get(date, [])]
        oos = [bar for date in oos_dates for bar in sessions.get(date, [])]
        if not training or not oos:
            raise RuntimeError(f"{target}: treino ou OOS vazio")
        summaries.append(
            f"{target}: calibração n={calibration['n_sessions']} até {calibration['as_of']}; "
            f"treino auxiliar={len(training_dates)} sessões; OOS={len(oos_dates)} sessões "
            f"({min(oos_dates)}..{max(oos_dates)}), barras={len(oos)}, "
            f"ALTA={sum(sessions[d][0].actual_up for d in oos_dates)}"
        )

        for hour in HOURS:
            scoped = [bar for bar in oos if bar.hour_brt == hour]
            estimate = bootstrap_auc(
                [(bar.session_id, bar.p_up, bar.actual_up) for bar in scoped],
                iterations=args.bootstrap, seed=20260713 + target_index * 1000 + hour,
            )
            auc_rows.append((
                target, f"{hour:02d}h", f"{estimate.value:.4f}",
                f"[{estimate.ci_low:.4f}, {estimate.ci_high:.4f}]",
                str(len(scoped)), str(estimate.n_sessions), "SIM" if estimate.significant else "NÃO",
            ))
            if comparison_sessions is not None:
                scoped_v1 = [
                    bar for date in oos_dates for bar in comparison_sessions[date]
                    if bar.hour_brt == hour
                ]
                v2_auc, v1_auc, return_auc = hourly_auc_comparison(
                    scoped, scoped_v1, iterations=args.bootstrap,
                    seed=20260713 + target_index * 1000 + hour,
                )
                auc_comparison_rows.append((
                    target, f"{hour:02d}h", f"{v2_auc.value:.4f}",
                    f"{v1_auc.value:.4f}", f"{return_auc.value:.4f}",
                    str(len(scoped)), str(v2_auc.n_sessions),
                ))

        platt = fit_hourly_platt(training, args.cutoff)
        baseline_points = [
            SessionPoint(bar.session_id, bar.hour_brt,
                         bar.close / float(bar.open_price) - 1.0, bar.p_up, bar.actual_up)
            for bar in training
        ]
        baseline = fit_historical_baseline(
            baseline_points, {bar.session_id: bar.session_id for bar in training}, args.cutoff
        )
        predictors = {
            "P_RAW": lambda bar: bar.p_up,
            "P_PLATT": lambda bar: platt[bar.hour_brt].predict(bar.p_up),
            "TRIVIAL": lambda bar: baseline.probability(
                bar.hour_brt, bar.close / float(bar.open_price) - 1.0
            ),
        }
        scopes = [("TODAS", oos)] + [(f"{h:02d}h", [b for b in oos if b.hour_brt == h]) for h in HOURS]
        for scope_index, (scope, scoped) in enumerate(scopes):
            for name, predictor in predictors.items():
                brier, logloss = _loss_metrics(scoped, predictor)
                score_rows.append((target, scope, name, f"{brier:.5f}", f"{logloss:.5f}", str(len(scoped))))
            for metric_index, metric in enumerate(("BRIER", "LOGLOSS")):
                for model_name in ("P_RAW", "P_PLATT"):
                    paired = defaultdict(list)
                    for bar in scoped:
                        model_loss = _probability_losses(
                            predictors[model_name](bar), bar.actual_up
                        )[metric_index]
                        trivial_loss = _probability_losses(
                            predictors["TRIVIAL"](bar), bar.actual_up
                        )[metric_index]
                        paired[bar.session_id].append((model_loss, trivial_loss))
                    delta = bootstrap_loss_delta(
                        dict(paired), iterations=args.bootstrap,
                        seed=20260713 + target_index * 10_000 + scope_index * 100 + metric_index * 10
                        + int(model_name == "P_PLATT"),
                    )
                    loss_delta_rows.append((
                        target, scope, metric, f"{model_name}-TRIVIAL", f"{delta.delta:+.5f}",
                        f"[{delta.ci_low:+.5f}, {delta.ci_high:+.5f}]",
                    ))

        for horizon_index, horizon in enumerate(HORIZONS):
            train_rows = [row for date in training_dates for row in forward_rows(sessions[date], horizon)]
            eval_rows = [row for date in oos_dates for row in forward_rows(sessions[date], horizon)]
            baseline_model, macro_model = fit_nested_models(train_rows, args.cutoff)
            baseline_prob = baseline_model.predict(row.momentum for row in eval_rows)
            macro_prob = macro_model.predict(row.momentum + row.macro for row in eval_rows)
            baseline_auc = _safe_auc([row.actual_up for row in eval_rows], list(baseline_prob))
            macro_auc = _safe_auc([row.actual_up for row in eval_rows], list(macro_prob))
            delta = bootstrap_auc_delta(
                [(row.session_id, float(mp), float(bp), row.actual_up)
                 for row, mp, bp in zip(eval_rows, macro_prob, baseline_prob)],
                iterations=args.bootstrap,
                seed=20260713 + target_index * 1000 + horizon_index,
            )
            nested_rows.append((
                target, str(horizon), str(len(eval_rows)), str(delta.n_sessions),
                f"{baseline_auc:.4f}", f"{macro_auc:.4f}", f"{delta.value:+.4f}",
                f"[{delta.ci_low:+.4f}, {delta.ci_high:+.4f}]",
                "SIM" if delta.significant else "NÃO",
            ))

            for feature_index, feature_name in enumerate(feature_names):
                grouped = defaultdict(list)
                values = []
                for row in eval_rows:
                    value = row.macro[feature_index]
                    grouped[row.session_id].append((value, row.forward_return))
                    values.append((value, row.forward_return, row.session_id))
                estimate = clustered_spearman(
                    dict(grouped), iterations=args.bootstrap,
                    seed=20260713 + target_index * 10_000 + horizon_index * 100 + feature_index,
                )
                ic_rows.append((
                    target, str(horizon), feature_name, str(len(values)), str(estimate.n_sessions),
                    f"{estimate.value:+.4f}", f"[{estimate.ci_low:+.4f}, {estimate.ci_high:+.4f}]",
                    "SIM" if estimate.significant else "NÃO",
                ))
                decile_rows.extend(_decile_rows(target, horizon, feature_name, values))

    print("SESSÕES:")
    for summary in summaries:
        print(f"  {summary}")
    print("\nAUC DO P_up CRU POR HORA (IC95% bootstrap por sessão; significância vs 0,5):")
    print(_table(("TARGET", "HORA", "AUC", "IC95%", "BARRAS", "SESSÕES", "SIGNIF."), auc_rows))
    if auc_comparison_rows:
        print("\nTESTE DO ESPELHO — AUC HORÁRIA (mesmas barras e rótulos):")
        print(_table(
            ("TARGET", "HORA", "AUC P_UP V2", "AUC P_UP V1", "AUC RETORNO-SO-FAR", "BARRAS", "SESSÕES"),
            auc_comparison_rows,
        ))
    print("\nRECALIBRAÇÃO PRÉ-JANELA (menor é melhor):")
    print(_table(("TARGET", "HORA", "MODELO", "BRIER", "LOGLOSS", "N"), score_rows))
    print("\nGAP PARA O TRIVIAL (loss(modelo)-loss(trivial), IC95% por sessão):")
    print(_table(("TARGET", "HORA", "MÉTRICA", "DELTA", "VALOR", "IC95%"), loss_delta_rows))
    print("\nIC SPEARMAN DAS FEATURES DERIVADAS (IC95% clusterizado por sessão):")
    print(_table(("TARGET", "H", "FEATURE", "N", "SESSÕES", "IC", "IC95%", "SIGNIF."), ic_rows))
    print("\nANÁLISE DE DECIS (retorno forward médio em bp):")
    print(_table(("TARGET", "H", "FEATURE", "DECIL", "N", "SESSÕES", "FEAT MÉDIA", "RET BP", "ALTA"), decile_rows))
    print("\nMODELO ANINHADO — AUC OOS E DELTA PAREADO POR SESSÃO:")
    print(_table(("TARGET", "H", "N", "SESSÕES", "AUC BASE", "AUC MACRO", "DELTA", "IC95%", "SIGNIF."), nested_rows))
    print("\nVEREDITO ESCOPADO:")
    for target in args.target:
        tactical = [row for row in nested_rows if row[0] == target and row[1] in {"3", "6"}]
        positive_tactical = any(row[-1] == "SIM" and float(row[6]) > 0 for row in tactical)
        if positive_tactical:
            print(f"  {target}: h=3/6 contém ganho incremental positivo; a morte tática é contradita.")
        else:
            print(f"  {target}: sem valor incremental detectável nos horizontes táticos (3/6).")
        long_horizon = next(row for row in nested_rows if row[0] == target and row[1] == "20")
        if long_horizon[-1] == "NÃO":
            print(f"  {target}: h=20 inconclusivo; não é evidência de morte.")
        else:
            print(f"  {target}: h=20 tem efeito detectável, reportado fora do escopo tático.")
    print(f"\nTempo total: {time.perf_counter() - started:.1f}s")
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--calibration-json", nargs="+", required=True)
    parser.add_argument("--target", nargs="+", choices=DEFAULT_TARGETS, default=list(DEFAULT_TARGETS))
    parser.add_argument("--cutoff", default=CALIBRATION_CUTOFF)
    parser.add_argument("--eval-start", required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--eval-end", required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--window-name", required=True)
    parser.add_argument("--version", choices=("v1", "v2", "both"), default="both")
    parser.add_argument("--train-sessions", type=int, default=120)
    parser.add_argument("--crossfit-folds", type=int, default=4)
    parser.add_argument(
        "--crossfit-holdout", type=int, default=10,
        help="Holdout diagnóstico dentro de cada calibração pré-fold (default: 10).",
    )
    parser.add_argument("--bootstrap", type=int, default=2_000)
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()
    if min(args.train_sessions, args.crossfit_folds, args.crossfit_holdout, args.bootstrap) <= 0:
        parser.error("treino, folds, holdout e bootstrap devem ser positivos")
    if args.eval_start > args.eval_end:
        parser.error("--eval-start deve ser anterior ou igual a --eval-end")
    if args.eval_start <= args.cutoff:
        parser.error("--eval-start deve ser posterior ao --cutoff")
    return args


def _load_calibrations(paths, cutoff):
    targets = {}
    for path in paths:
        with open(path, encoding="utf-8") as calibration_file:
            artifact = json.load(calibration_file)
        if artifact.get("as_of") != cutoff:
            raise ValueError(f"{path}: cutoff da calibração difere do Gate 3b")
        overlap = set(targets) & set(artifact.get("targets", {}))
        if overlap:
            raise ValueError(f"targets duplicados nos artefatos: {sorted(overlap)}")
        targets.update(artifact.get("targets", {}))
    return targets


def _estimate_dict(estimate: Estimate):
    return {
        "delta_auc": estimate.value,
        "ci95_low": estimate.ci_low,
        "ci95_high": estimate.ci_high,
        "sessions": estimate.n_sessions,
        "standard_error": estimate.standard_error,
        "significant": estimate.significant,
    }


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    calibrations = _load_calibrations(args.calibration_json, args.cutoff)
    initial_states = load_initial_states(args.db)
    versions = versions_to_replay(args.version)
    central, hourly, contract, power_rows, summaries, crossfit_rows = [], [], [], [], [], []

    print(f"Gate 3b — {args.window_name}: macro incremental sobre preço próprio.")
    print(f"Treino <= {args.cutoff}; OOS {args.eval_start}..{args.eval_end}; braços={','.join(versions)}.")
    print("Segurança: SQLite mode=ro + PRAGMA query_only; persist_state=False em TODA chamada.")
    print("Treino macro: cross-fit temporal; cada fold usa Ridge/sigmas/logística calibrados antes dela.")
    print("Teste: modelos separados por faixa; v1/v2 residualizados contra as features de preço.")
    print("Contrato real: regressão logística multinomial com penalidade Ridge (L2), classes")
    print("DOWN/NEUTRAL/UP e neutralidade |ret| < max(custo, 0,10×ATR14).")
    print(
        f"Poder prospectivo: alvo operacional mínimo ΔAUC={MINIMUM_USEFUL_DELTA_AUC:.2f}, "
        "80% de poder, alfa bilateral 5%; SE clusterizado observado em OPEN_20/v1."
    )

    for target_index, target in enumerate(args.target):
        calibration = calibrations.get(target)
        if not calibration:
            raise ValueError(f"artefatos sem calibração para {target}")
        candidates = candidate_sessions(args.db, target, limit=10_000)
        training_dates = [date for date in candidates.dates if date <= args.cutoff][
            -args.train_sessions:
        ]
        evaluation_dates = select_evaluation_dates(
            candidates.dates, cutoff=args.cutoff,
            eval_start=args.eval_start, eval_end=args.eval_end,
        )
        if not training_dates or not evaluation_dates:
            raise RuntimeError(f"{target}: treino ou avaliação sem sessões")

        training_by_arm, fold_metadata = crossfit_replay_bars(
            args.db, target, training_dates, initial_states, calibration,
            versions=versions, n_splits=args.crossfit_folds,
            holdout_sessions=args.crossfit_holdout,
        )
        evaluation_by_arm = {}
        for version in versions:
            sessions, discarded = replay_bars(
                args.db, target, evaluation_dates, initial_states, calibration, version=version
            )
            if discarded:
                raise RuntimeError(f"{target}/{version}: sessões OOS inválidas: {discarded}")
            evaluation_by_arm[version] = sessions

        summaries.append({
            "target": target, "train_sessions": len(training_dates),
            "evaluation_sessions": len(evaluation_dates),
            "evaluation_start": min(evaluation_dates), "evaluation_end": max(evaluation_dates),
            "calibration_sessions": calibration["n_sessions"],
        })
        for fold, fold_cutoff, fold_size, calibration_sessions in fold_metadata:
            crossfit_rows.append((
                target, str(fold), fold_cutoff, str(fold_size), str(calibration_sessions)
            ))

        for arm_index, version in enumerate(versions):
            train_bars = [
                bar for date in training_dates for bar in training_by_arm[version][date]
            ]
            eval_bars = [
                bar for date in evaluation_dates for bar in evaluation_by_arm[version][date]
            ]
            for hour_brt in HOURS:
                train_hour = [bar for bar in train_bars if bar.hour_brt == hour_brt]
                eval_hour = [bar for bar in eval_bars if bar.hour_brt == hour_brt]
                baseline_auc, treatment_auc, delta = hourly_incremental_delta(
                    train_hour, eval_hour, iterations=args.bootstrap,
                    seed=20260713 + target_index * 10_000 + arm_index * 100 + hour_brt,
                )
                hourly.append({
                    "target": target, "arm": version, "hour_brt": hour_brt,
                    "baseline_auc": baseline_auc, "treatment_auc": treatment_auc,
                    **_estimate_dict(delta),
                })

            for horizon_index, horizon in enumerate(HORIZONS):
                train_rows = [
                    row for date in training_dates
                    for row in forward_rows(training_by_arm[version][date], horizon)
                ]
                eval_rows = [
                    row for date in evaluation_dates
                    for row in forward_rows(evaluation_by_arm[version][date], horizon)
                ]
                for scope_index, scope in enumerate(TIME_SCOPES):
                    scoped_train = rows_in_scope(train_rows, scope)
                    scoped_eval = rows_in_scope(eval_rows, scope)
                    models = fit_residualized_nested(scoped_train)
                    baseline_probability, treatment_probability = predict_residualized_nested(
                        models, scoped_eval
                    )
                    labels = [row.actual_up for row in scoped_eval]
                    delta = bootstrap_auc_delta(
                        [(row.session_id, float(tp), float(bp), row.actual_up)
                         for row, tp, bp in zip(
                             scoped_eval, treatment_probability, baseline_probability
                         )],
                        iterations=args.bootstrap,
                        seed=(20260713 + target_index * 100_000 + arm_index * 10_000
                              + horizon_index * 100 + scope_index),
                    )
                    result = {
                        "target": target, "arm": version, "horizon": horizon,
                        "scope": scope, "rows": len(scoped_eval),
                        "baseline_auc": _safe_auc(labels, list(baseline_probability)),
                        "treatment_auc": _safe_auc(labels, list(treatment_probability)),
                        **_estimate_dict(delta),
                    }
                    central.append(result)
                    if version == "v1" and scope == "OPEN_20":
                        power_rows.append({
                            "target": target, "horizon": horizon,
                            "current_sessions": delta.n_sessions,
                            "minimum_delta_auc": MINIMUM_USEFUL_DELTA_AUC,
                            "required_sessions": required_sessions_for_power(
                                current_sessions=delta.n_sessions,
                                bootstrap_standard_error=delta.standard_error,
                            ),
                            "standard_error": delta.standard_error,
                        })

                if version == "v1":
                    for contract_scope_index, scope in enumerate(("OPEN_20", "09_10", "11_18")):
                        scoped_train = rows_in_scope(train_rows, scope)
                        scoped_eval = rows_in_scope(eval_rows, scope)
                        models = fit_residualized_multinomial(
                            scoped_train, TARGET_COST_POINTS[target]
                        )
                        baseline_probability, treatment_probability = (
                            predict_residualized_multinomial(models, scoped_eval)
                        )
                        labels = [
                            multinomial_label(
                                row.forward_return, close=row.close,
                                atr14_points=row.atr14_points,
                                cost_points=TARGET_COST_POINTS[target],
                            )
                            for row in scoped_eval
                        ]
                        delta = bootstrap_multiclass_auc_delta(
                            [(row.session_id, tp, bp, label)
                             for row, tp, bp, label in zip(
                                 scoped_eval, treatment_probability,
                                 baseline_probability, labels,
                             )],
                            iterations=args.bootstrap,
                            seed=(20260713 + target_index * 100_000
                                  + horizon_index * 100 + contract_scope_index),
                        )
                        contract.append({
                            "target": target, "arm": version, "horizon": horizon,
                            "scope": scope, "rows": len(scoped_eval),
                            "class_down": labels.count(-1), "class_neutral": labels.count(0),
                            "class_up": labels.count(1),
                            "baseline_auc": _macro_ovr_auc(labels, baseline_probability),
                            "treatment_auc": _macro_ovr_auc(labels, treatment_probability),
                            **_estimate_dict(delta),
                        })

    print("\nCROSS-FIT TEMPORAL (cutoff estritamente anterior à fold):")
    print(_table(("TARGET", "FOLD", "CUTOFF", "SESSÕES", "N CALIB."), crossfit_rows))
    print("\nTESTE CENTRAL BINÁRIO — ΔAUC=(preço+macro residual)-preço; IC95% por sessão:")
    print(_table(
        ("TARGET", "BRAÇO", "H", "FAIXA", "N", "SESSÕES", "AUC BASE", "AUC +MACRO", "ΔAUC", "IC95%"),
        [(row["target"], row["arm"], str(row["horizon"]), row["scope"],
          str(row["rows"]), str(row["sessions"]), f'{row["baseline_auc"]:.4f}',
          f'{row["treatment_auc"]:.4f}', f'{row["delta_auc"]:+.4f}',
          f'[{row["ci95_low"]:+.4f}, {row["ci95_high"]:+.4f}]') for row in central],
    ))
    print("\nP_up HORÁRIO RESIDUALIZADO CONTRA RETORNO-SO-FAR — ΔAUC incremental:")
    print(_table(
        ("TARGET", "BRAÇO", "HORA", "AUC PREÇO", "AUC +P_RES", "ΔAUC", "IC95%"),
        [(row["target"], row["arm"], f'{row["hour_brt"]:02d}h',
          f'{row["baseline_auc"]:.4f}', f'{row["treatment_auc"]:.4f}',
          f'{row["delta_auc"]:+.4f}', f'[{row["ci95_low"]:+.4f}, {row["ci95_high"]:+.4f}]')
         for row in hourly],
    ))
    print("\nCONTRATO REAL v1 — AUC macro OVR multinomial, neutral por custo/ATR:")
    print(_table(
        ("TARGET", "H", "FAIXA", "D/N/U", "AUC BASE", "AUC +MACRO", "ΔAUC", "IC95%"),
        [(row["target"], str(row["horizon"]), row["scope"],
          f'{row["class_down"]}/{row["class_neutral"]}/{row["class_up"]}',
          f'{row["baseline_auc"]:.4f}', f'{row["treatment_auc"]:.4f}',
          f'{row["delta_auc"]:+.4f}', f'[{row["ci95_low"]:+.4f}, {row["ci95_high"]:+.4f}]')
         for row in contract],
    ))
    print("\nPODER PROSPECTIVO (OPEN_20/v1; ΔAUC mínimo útil=+0,02; 80%; alfa=5%):")
    print(_table(
        ("TARGET", "H", "N ATUAL", "SE BOOT", "N NECESSÁRIO"),
        [(row["target"], str(row["horizon"]), str(row["current_sessions"]),
          f'{row["standard_error"]:.4f}', str(row["required_sessions"]))
         for row in power_rows],
    ))
    print("\nSESSÕES:")
    for row in summaries:
        print(
            f"  {row['target']}: treino={row['train_sessions']} cross-fitted; "
            f"OOS={row['evaluation_sessions']} ({row['evaluation_start']}..{row['evaluation_end']}); "
            f"calibração final n={row['calibration_sessions']}."
        )
    print(f"Tempo total: {time.perf_counter() - started:.1f}s")

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as output_file:
            json.dump({
                "window": args.window_name, "cutoff": args.cutoff,
                "eval_start": args.eval_start, "eval_end": args.eval_end,
                "central": central, "hourly_residualized": hourly,
                "multinomial_contract": contract, "power": power_rows,
                "sessions": summaries,
            }, output_file, indent=2, sort_keys=True)
        print(f"Resultados JSON: {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
