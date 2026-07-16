#!/usr/bin/env python3
"""Avalia Miqueias, IRAI v1/v2 e challengers no objetivo diário do P Dinâmico.

O torneio usa somente bundles fechados produzidos por
``scripts/compare_p_dynamic_parity.py --capture-dir ...``. Cada sessão tem o
mesmo peso: Brier e log-loss são calculados por barra operacional e primeiro
agregados dentro da sessão, evitando que dias com mais prints dominem o placar.

Um primeiro lugar não basta para promoção. O default exige pelo menos 60
sessões comuns e IC95% bootstrap pareado por sessão estritamente favorável ao
melhor Brier contra todos os concorrentes. A utilidade como gate da estratégia
manual é outra pergunta e permanece explicitamente ``NOT_EVALUATED`` aqui.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.compare_p_dynamic_parity import (
    LOCAL_VALUE_FIELDS,
    PUBLIC_VALUE_FIELDS,
    _extract_rows,
    capture_brt_offset_h,
    capture_session_status,
    normalize_series,
)


DEFAULT_MIN_SESSIONS = 60
DEFAULT_BOOTSTRAP_ITERATIONS = 10_000
EPSILON = 1e-6


@dataclass(frozen=True)
class LedgerSession:
    session_date: str
    actual_up: bool
    forecasts: Mapping[str, Sequence[float]]


def _read_json(path: Path):
    with path.open("r", encoding="utf-8") as source_file:
        return json.load(source_file)


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _actual_outcome(document) -> bool:
    rows = [
        row
        for row in _extract_rows(document)
        if not row.get("is_ghost", False)
        and not row.get("is_preview", False)
        and row.get("win_open") is not None
        and row.get("win_current") is not None
    ]
    if not rows:
        raise ValueError("série local sem WIN operacional para formar o outcome")
    return float(rows[-1]["win_current"]) > float(rows[0]["win_open"])


def _forecast_probabilities(document, *, public: bool) -> list[float]:
    fields = PUBLIC_VALUE_FIELDS if public else LOCAL_VALUE_FIELDS
    points = normalize_series(_extract_rows(document), value_fields=fields)
    probabilities = []
    for point in points:
        if not point.operational:
            continue
        probability = point.value / 100.0
        if not 0.0 <= probability <= 1.0:
            raise ValueError(f"P_up fora de [0,100] em {point.timestamp}: {point.value}")
        probabilities.append(probability)
    if not probabilities:
        raise ValueError("modelo sem forecasts operacionais")
    return probabilities


def load_ledger_sessions(root: str | Path) -> tuple[list[LedgerSession], dict]:
    """Seleciona a captura fechada mais recente de cada sessão."""
    root = Path(root)
    manifests = sorted(root.glob("**/manifest.json"))
    audit = {
        "manifest_bundles": len(manifests),
        "incomplete_bundles": 0,
        "closed_bundles": 0,
        "invalid_bundles": 0,
        "selected_sessions": 0,
        "invalid_reasons": [],
    }
    latest_by_session: dict[str, tuple[str, Path, dict]] = {}
    for manifest_path in manifests:
        try:
            manifest = _read_json(manifest_path)
            if not manifest.get("session", {}).get("closed", False):
                audit["incomplete_bundles"] += 1
                continue
            audit["closed_bundles"] += 1
            session_date = str(manifest["session_date"])
            captured_at = str(manifest.get("captured_at", ""))
            previous = latest_by_session.get(session_date)
            if previous is None or captured_at > previous[0]:
                latest_by_session[session_date] = (captured_at, manifest_path, manifest)
        except Exception as exc:
            audit["invalid_bundles"] += 1
            audit["invalid_reasons"].append(f"{manifest_path}: {type(exc).__name__}: {exc}")

    sessions = []
    for session_date, (_, manifest_path, manifest) in sorted(latest_by_session.items()):
        try:
            bundle = manifest_path.parent
            files = manifest.get("files", {})
            documents = {
                model: _read_json(bundle / files[model])
                for model in manifest.get("models", [])
                if model in files
            }
            outcome_source = documents.get("v2") or documents.get("v1")
            if outcome_source is None:
                raise ValueError("bundle sem v1/v2 para formar o outcome do WIN")
            forecasts = {
                model: _forecast_probabilities(document, public=model == "miqueias")
                for model, document in documents.items()
            }
            brt_offset_h = capture_brt_offset_h(session_date, documents)
            source_statuses = {
                model: capture_session_status(
                    normalize_series(
                        _extract_rows(document),
                        value_fields=(
                            PUBLIC_VALUE_FIELDS if model == "miqueias" else LOCAL_VALUE_FIELDS
                        ),
                    ),
                    brt_offset_h=brt_offset_h,
                )
                for model, document in documents.items()
            }
            incomplete_sources = sorted(
                model for model, status in source_statuses.items() if not status["closed"]
            )
            if incomplete_sources:
                raise ValueError(
                    "fontes sem fechamento operacional: " + ", ".join(incomplete_sources)
                )
            if len(forecasts) < 2:
                raise ValueError("bundle precisa de pelo menos dois modelos comparáveis")
            sessions.append(
                LedgerSession(
                    session_date=session_date,
                    actual_up=_actual_outcome(outcome_source),
                    forecasts=forecasts,
                )
            )
        except Exception as exc:
            audit["invalid_bundles"] += 1
            audit["invalid_reasons"].append(
                f"{manifest_path}: {type(exc).__name__}: {exc}"
            )
    audit["selected_sessions"] = len(sessions)
    return sessions, audit


def _probability_losses(probability: float, actual_up: bool) -> tuple[float, float]:
    actual = 1.0 if actual_up else 0.0
    clipped = min(1.0 - EPSILON, max(EPSILON, probability))
    brier = (probability - actual) ** 2
    log_loss = -(actual * math.log(clipped) + (1.0 - actual) * math.log(1.0 - clipped))
    return brier, log_loss


def _session_scores(session: LedgerSession, model: str) -> dict:
    probabilities = list(session.forecasts[model])
    losses = [_probability_losses(probability, session.actual_up) for probability in probabilities]
    mean_probability = statistics.fmean(probabilities)
    return {
        "brier": statistics.fmean(loss[0] for loss in losses),
        "log_loss": statistics.fmean(loss[1] for loss in losses),
        "accuracy": float((mean_probability >= 0.5) == session.actual_up),
        "mean_probability": mean_probability,
        "observations": len(probabilities),
    }


def _roc_auc(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float | None:
    positives = [p for p, outcome in zip(probabilities, outcomes) if outcome]
    negatives = [p for p, outcome in zip(probabilities, outcomes) if not outcome]
    if not positives or not negatives:
        return None
    wins = 0.0
    for positive in positives:
        for negative in negatives:
            wins += positive > negative
            wins += 0.5 * (positive == negative)
    return wins / (len(positives) * len(negatives))


def _calibration_error(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float:
    bins: dict[int, list[tuple[float, bool]]] = {}
    for probability, outcome in zip(probabilities, outcomes):
        bucket = min(9, int(probability * 10.0))
        bins.setdefault(bucket, []).append((probability, outcome))
    total = len(probabilities)
    return sum(
        len(values) / total
        * abs(
            statistics.fmean(value[0] for value in values)
            - statistics.fmean(float(value[1]) for value in values)
        )
        for values in bins.values()
    )


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _bootstrap_delta(
    deltas: Sequence[float],
    *,
    iterations: int,
    seed: int,
) -> dict:
    rng = random.Random(seed)
    n_sessions = len(deltas)
    samples = [
        statistics.fmean(deltas[rng.randrange(n_sessions)] for _ in range(n_sessions))
        for _ in range(iterations)
    ]
    return {
        "delta_brier": round(statistics.fmean(deltas), 8),
        "ci_low": round(_percentile(samples, 0.025), 8),
        "ci_high": round(_percentile(samples, 0.975), 8),
    }


def _with_causal_climatology(sessions: Sequence[LedgerSession]) -> list[LedgerSession]:
    """Acrescenta baseline Beta(1,1) usando somente sessões anteriores."""
    up_count = 1
    total_count = 2
    augmented = []
    for session in sorted(sessions, key=lambda item: item.session_date):
        forecasts = dict(session.forecasts)
        forecasts["baseline_climatology"] = [up_count / total_count]
        augmented.append(
            LedgerSession(
                session_date=session.session_date,
                actual_up=session.actual_up,
                forecasts=forecasts,
            )
        )
        up_count += int(session.actual_up)
        total_count += 1
    return augmented


def evaluate_champions(
    sessions: Sequence[LedgerSession],
    *,
    min_sessions: int = DEFAULT_MIN_SESSIONS,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    seed: int = 42,
) -> dict:
    if min_sessions <= 0 or bootstrap_iterations <= 0:
        raise ValueError("min_sessions e bootstrap_iterations precisam ser positivos")
    sessions = _with_causal_climatology(sessions)
    common_models = sorted(
        set().union(*(set(session.forecasts) for session in sessions))
        if sessions else set()
    )
    comparable_sessions = [
        session
        for session in sessions
        if all(session.forecasts.get(model) for model in common_models)
    ]
    scores = {
        model: [_session_scores(session, model) for session in comparable_sessions]
        for model in common_models
    }
    outcomes = [session.actual_up for session in comparable_sessions]
    metrics = {}
    for model in common_models:
        model_scores = scores[model]
        session_probabilities = [score["mean_probability"] for score in model_scores]
        auc = _roc_auc(session_probabilities, outcomes)
        metrics[model] = {
            "sessions": len(model_scores),
            "observations": sum(score["observations"] for score in model_scores),
            "brier": round(statistics.fmean(score["brier"] for score in model_scores), 8)
            if model_scores else None,
            "log_loss": round(statistics.fmean(score["log_loss"] for score in model_scores), 8)
            if model_scores else None,
            "directional_accuracy_pct": round(
                100.0 * statistics.fmean(score["accuracy"] for score in model_scores), 6
            ) if model_scores else None,
            "session_mean_auc": round(auc, 8) if auc is not None else None,
            "session_mean_calibration_error": round(
                _calibration_error(session_probabilities, outcomes), 8
            ) if model_scores else None,
        }

    ranking = sorted(
        common_models,
        key=lambda model: metrics[model]["brier"] if metrics[model]["brier"] is not None else math.inf,
    )
    winner_tests = []
    if ranking and len(ranking) >= 2 and len(comparable_sessions) >= min_sessions:
        best = ranking[0]
        for opponent_index, opponent in enumerate(ranking[1:], start=1):
            deltas = [
                scores[best][index]["brier"] - scores[opponent][index]["brier"]
                for index in range(len(comparable_sessions))
            ]
            winner_tests.append({
                "candidate": best,
                "opponent": opponent,
                **_bootstrap_delta(
                    deltas,
                    iterations=bootstrap_iterations,
                    seed=seed + opponent_index,
                ),
            })

    quality_winner = None
    status = "INCONCLUSIVE"
    reasons = []
    if len(common_models) < 2:
        reasons.append("menos de dois modelos comuns")
    if len(comparable_sessions) < min_sessions:
        reasons.append(
            f"amostra abaixo do gate: {len(comparable_sessions)}/{min_sessions} sessões"
        )
    if winner_tests and all(test["ci_high"] < 0 for test in winner_tests):
        quality_winner = ranking[0]
        status = "WINNER"
    elif len(comparable_sessions) >= min_sessions and len(common_models) >= 2:
        reasons.append("IC95% pareado não separa o primeiro colocado de todos os concorrentes")

    return {
        "status": status,
        "quality_winner": quality_winner,
        "common_sessions": len(comparable_sessions),
        "minimum_sessions_gate": min_sessions,
        "common_models": common_models,
        "ranking_by_brier": ranking,
        "metrics": metrics,
        "winner_tests": winner_tests,
        "reasons": reasons,
        "objective": "nowcast da direção final da sessão WIN (close > open)",
        "tactical_gate": {
            "status": "NOT_EVALUATED",
            "reason": (
                "utilidade como filtro da regra GEX/MID/Pair/NWE exige regra de execução, "
                "fill, alvo, stop e custos separados"
            ),
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-dir", default="data/p_dynamic_parity")
    parser.add_argument("--min-sessions", type=int, default=DEFAULT_MIN_SESSIONS)
    parser.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP_ITERATIONS)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-json", default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    sessions, audit = load_ledger_sessions(args.ledger_dir)
    report = {
        "schema_version": 1,
        "ledger_dir": str(args.ledger_dir),
        "audit": audit,
        **evaluate_champions(
            sessions,
            min_sessions=args.min_sessions,
            bootstrap_iterations=args.bootstrap,
            seed=args.seed,
        ),
    }
    if args.output_json:
        _write_json(Path(args.output_json), report)
    print(
        f"Champion-challenger: {report['status']} — "
        f"sessões={report['common_sessions']}/{report['minimum_sessions_gate']}, "
        f"winner={report['quality_winner']}"
    )
    for model in report["ranking_by_brier"]:
        metrics = report["metrics"][model]
        print(
            f"{model}: Brier={metrics['brier']}, log-loss={metrics['log_loss']}, "
            f"AUC={metrics['session_mean_auc']}"
        )
    for reason in report["reasons"]:
        print(f"- {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
