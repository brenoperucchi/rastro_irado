#!/usr/bin/env python3
"""Compara resultados do NF-01 com e sem janelas de rollover.

Consome somente artefatos versionados: o ledger de eventos produzido por
``build_nf01_artifact.py`` e a auditoria produzida por
``audit_continuous_rollover.py``. Não relê mercado nem recalcula sinais, o que
mantém esta etapa independente do replay caro e torna o recorte auditável.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


SCHEMA_VERSION = "irai.rollover-sensitivity.v1"
HORIZONS = (3, 6, 10, 20)
DEFAULT_BOOTSTRAP_ITERATIONS = 10_000


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = quantile * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _forward_value(event: dict, horizon: int) -> float | None:
    raw = event.get("fwd", {}).get(str(horizon))
    if raw is None:
        raw = event.get("fwd", {}).get(horizon)
    return float(raw) if raw is not None else None


def _summarize(
    events: Iterable[dict],
    horizon: int,
    *,
    bootstrap_iterations: int,
    seed: int,
) -> dict | None:
    by_session: dict[str, list[float]] = defaultdict(list)
    for event in events:
        value = _forward_value(event, horizon)
        if value is not None and math.isfinite(value):
            by_session[event["session_date"]].append(value)
    values = [value for session in by_session.values() for value in session]
    if not values:
        return None

    mean = sum(values) / len(values)
    sessions = sorted(by_session)
    rng = random.Random(seed)
    samples = []
    for _ in range(bootstrap_iterations):
        chosen = rng.choices(sessions, k=len(sessions))
        sample = [value for session in chosen for value in by_session[session]]
        samples.append(sum(sample) / len(sample))

    wins = sum(value > 0 for value in values)
    ci_low = _percentile(samples, 0.025) if samples else mean
    ci_high = _percentile(samples, 0.975) if samples else mean
    return {
        "n_events": len(values),
        "n_sessions": len(by_session),
        "mean_net_points": mean,
        "win_rate_pct": 100.0 * wins / len(values),
        "ci95_low": ci_low,
        "ci95_high": ci_high,
        "significant_vs_zero": ci_low > 0 or ci_high < 0,
    }


def _direction_report(
    events: list[dict],
    excluded_sessions: set[str],
    *,
    bootstrap_iterations: int,
    seed: int,
) -> dict:
    kept = [event for event in events if event["session_date"] not in excluded_sessions]
    horizons = {}
    for horizon in HORIZONS:
        with_rollover = _summarize(
            events,
            horizon,
            bootstrap_iterations=bootstrap_iterations,
            seed=seed + horizon,
        )
        without_rollover = _summarize(
            kept,
            horizon,
            bootstrap_iterations=bootstrap_iterations,
            seed=seed + horizon,
        )
        delta = None
        if with_rollover is not None and without_rollover is not None:
            delta = (
                without_rollover["mean_net_points"]
                - with_rollover["mean_net_points"]
            )
        horizons[str(horizon)] = {
            "with_rollover": with_rollover,
            "without_rollover": without_rollover,
            "delta_mean_points": delta,
        }
    return {
        "events_total": len(events),
        "events_excluded": len(events) - len(kept),
        "events_kept": len(kept),
        "horizons": horizons,
    }


def build_sensitivity(
    nf01_artifact: dict,
    rollover_artifact: dict,
    *,
    target: str,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
) -> dict:
    if nf01_artifact.get("artifact") != "nf01-pair-z-intersection-baselines":
        raise ValueError("artefato NF-01 incompatível")
    rollover_symbol = rollover_artifact.get("symbol")
    if rollover_symbol != target:
        raise ValueError(
            f"auditoria de {rollover_symbol!r} não pode ser aplicada a {target!r}"
        )
    if bootstrap_iterations < 0:
        raise ValueError("bootstrap_iterations não pode ser negativo")

    excluded_sessions = set(
        rollover_artifact.get("audit", {}).get("excluded_sessions", [])
    )
    signals = {}
    for signal_name, signal_report in nf01_artifact.get("signals", {}).items():
        target_report = signal_report.get("targets", {}).get(target)
        if target_report is None:
            continue
        events = list(target_report.get("events", []))
        kept = [event for event in events if event["session_date"] not in excluded_sessions]
        directions = {
            "all": events,
            "buy": [event for event in events if event.get("direction") == "buy"],
            "sell": [event for event in events if event.get("direction") == "sell"],
        }
        signals[signal_name] = {
            "events_total": len(events),
            "events_excluded": len(events) - len(kept),
            "events_kept": len(kept),
            "rollover_exposure_pct": (
                100.0 * (len(events) - len(kept)) / len(events) if events else 0.0
            ),
            "by_direction": {
                direction: _direction_report(
                    subset,
                    excluded_sessions,
                    bootstrap_iterations=bootstrap_iterations,
                    seed=20260716 + index * 100,
                )
                for index, (direction, subset) in enumerate(directions.items())
            },
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": target,
        "bootstrap_iterations": bootstrap_iterations,
        "rollover_window_sessions_each_side": rollover_artifact.get("audit", {}).get(
            "window_sessions_each_side"
        ),
        "excluded_sessions": sorted(excluded_sessions),
        "nf01_source": {
            "schema_version": nf01_artifact.get("schema_version"),
            "git": nf01_artifact.get("git"),
            "command": nf01_artifact.get("command"),
        },
        "rollover_source": {
            "schema_version": rollover_artifact.get("schema_version"),
            "continuous_method": rollover_artifact.get("continuous_method"),
        },
        "signals": signals,
        "interpretation_rule": (
            "Comparar tamanho da exposição, sinal/magnitude da média, IC95% e "
            "win-rate. Não promover edge por mudança isolada após a exclusão."
        ),
    }


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nf01-artifact", required=True)
    parser.add_argument("--rollover-artifact", required=True)
    parser.add_argument("--target", default="WIN$N")
    parser.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP_ITERATIONS)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_sensitivity(
        _load(args.nf01_artifact),
        _load(args.rollover_artifact),
        target=args.target,
        bootstrap_iterations=args.bootstrap,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Sensibilidade gravada em {output}")
    for signal, result in report["signals"].items():
        print(
            f"{signal}: {result['events_excluded']}/{result['events_total']} "
            f"eventos excluídos ({result['rollover_exposure_pct']:.2f}%)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

