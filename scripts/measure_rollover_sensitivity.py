#!/usr/bin/env python3
"""Compara resultados do NF-01 com e sem janelas de rollover.

Consome somente artefatos versionados: o ledger de eventos produzido por
``build_nf01_artifact.py`` e a auditoria produzida por
``audit_continuous_rollover.py``. Não relê mercado nem recalcula sinais, o que
mantém esta etapa independente do replay caro e torna o recorte auditável.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import random
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

try:  # Executável pelo caminho do arquivo e importável pela suíte.
    from scripts.audit_continuous_rollover import (
        SCHEMA_VERSION as ROLLOVER_AUDIT_SCHEMA_VERSION,
        infer_continuous_method,
        validate_mt5_capture,
    )
except ModuleNotFoundError:  # pragma: no cover - exercido pelo invocador CLI
    from audit_continuous_rollover import (
        SCHEMA_VERSION as ROLLOVER_AUDIT_SCHEMA_VERSION,
        infer_continuous_method,
        validate_mt5_capture,
    )


SCHEMA_VERSION = "irai.rollover-sensitivity.v1"
HORIZONS = (3, 6, 10, 20)
DEFAULT_BOOTSTRAP_ITERATIONS = 10_000
QUALIFIED_CONTINUOUS_METHOD = "liquidity_continuous_unadjusted"


def _strict_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} deve ser inteiro")
    return value


def _parse_session_date(value: object, *, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field} deve ser data ISO")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} deve ser data ISO válida") from exc


def validate_rollover_artifact(rollover_artifact: Mapping[str, object], target: str) -> dict:
    """Valida a cadeia de evidências antes de excluir eventos de NF-01.

    A sensibilidade não é válida se a auditoria não provar, de forma
    reproduzível, qual banco e qual série contínua MT5 originaram as sessões
    excluídas. A validação deliberadamente falha fechada para não transformar
    um JSON parcial em decisão metodológica.
    """
    if rollover_artifact.get("schema_version") != ROLLOVER_AUDIT_SCHEMA_VERSION:
        raise ValueError("artefato de rollover possui schema_version incompatível")
    rollover_symbol = rollover_artifact.get("symbol")
    if rollover_symbol != target:
        raise ValueError(
            f"auditoria de {rollover_symbol!r} não pode ser aplicada a {target!r}"
        )
    if rollover_artifact.get("continuous_method") != QUALIFIED_CONTINUOUS_METHOD:
        raise ValueError("auditoria não comprova série contínua por liquidez sem ajustes")

    fingerprint = rollover_artifact.get("database_fingerprint")
    if not isinstance(fingerprint, Mapping):
        raise ValueError("auditoria não contém fingerprint do banco")
    size_bytes = _strict_int(fingerprint.get("size_bytes"), field="database_fingerprint.size_bytes")
    sha256 = fingerprint.get("sha256")
    if size_bytes <= 0 or not isinstance(sha256, str) or len(sha256) != 64:
        raise ValueError("fingerprint do banco inválido")
    try:
        int(sha256, 16)
    except ValueError as exc:
        raise ValueError("fingerprint SHA-256 inválido") from exc

    mt5_capture = rollover_artifact.get("mt5_capture")
    if not isinstance(mt5_capture, Mapping):
        raise ValueError("auditoria não contém captura MT5")
    validated_capture = validate_mt5_capture(mt5_capture, target)
    capture_description = validated_capture["symbols"][target]["description"]
    if infer_continuous_method(capture_description) != QUALIFIED_CONTINUOUS_METHOD:
        raise ValueError(
            "captura MT5 não comprova série contínua por liquidez sem ajustes"
        )

    audit = rollover_artifact.get("audit")
    if not isinstance(audit, Mapping):
        raise ValueError("auditoria não contém intervalo de sessões")
    first_session = _parse_session_date(audit.get("first_session"), field="audit.first_session")
    last_session = _parse_session_date(audit.get("last_session"), field="audit.last_session")
    if last_session < first_session:
        raise ValueError("intervalo da auditoria é inválido")
    window = _strict_int(
        audit.get("window_sessions_each_side"),
        field="audit.window_sessions_each_side",
    )
    if window < 0:
        raise ValueError("audit.window_sessions_each_side não pode ser negativo")
    excluded_sessions = audit.get("excluded_sessions")
    if not isinstance(excluded_sessions, list) or not all(
        isinstance(session, str) for session in excluded_sessions
    ):
        raise ValueError("audit.excluded_sessions deve ser lista de datas")
    for session in excluded_sessions:
        session_date = _parse_session_date(session, field="audit.excluded_sessions")
        if not first_session <= session_date <= last_session:
            raise ValueError("sessão excluída está fora do intervalo da auditoria")

    return {
        "database_fingerprint": {"size_bytes": size_bytes, "sha256": sha256},
        "mt5_capture": validated_capture,
        "audit": {
            "first_session": first_session.isoformat(),
            "last_session": last_session.isoformat(),
            "window_sessions_each_side": window,
            "excluded_sessions": list(excluded_sessions),
        },
    }


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
    if bootstrap_iterations < 0:
        raise ValueError("bootstrap_iterations não pode ser negativo")

    validated_rollover = validate_rollover_artifact(rollover_artifact, target)
    audit = validated_rollover["audit"]
    audit_start = _parse_session_date(audit["first_session"], field="audit.first_session")
    audit_end = _parse_session_date(audit["last_session"], field="audit.last_session")
    excluded_sessions = set(audit["excluded_sessions"])
    signals = {}
    for signal_name, signal_report in nf01_artifact.get("signals", {}).items():
        target_report = signal_report.get("targets", {}).get(target)
        if target_report is None:
            continue
        events = list(target_report.get("events", []))
        for event in events:
            session_date = _parse_session_date(
                event.get("session_date"),
                field=f"NF-01 {signal_name}.session_date",
            )
            if not audit_start <= session_date <= audit_end:
                raise ValueError(
                    f"evento NF-01 {session_date.isoformat()} está fora do intervalo "
                    "da auditoria de rollover"
                )
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
        "rollover_window_sessions_each_side": audit["window_sessions_each_side"],
        "excluded_sessions": sorted(excluded_sessions),
        "nf01_source": {
            "schema_version": nf01_artifact.get("schema_version"),
            "git": nf01_artifact.get("git"),
            "command": nf01_artifact.get("command"),
        },
        "rollover_source": {
            "schema_version": ROLLOVER_AUDIT_SCHEMA_VERSION,
            "continuous_method": QUALIFIED_CONTINUOUS_METHOD,
            "database_fingerprint": validated_rollover["database_fingerprint"],
            "audit_interval": {
                "first_session": audit["first_session"],
                "last_session": audit["last_session"],
            },
            "mt5_capture": validated_rollover["mt5_capture"],
        },
        "signals": signals,
        "interpretation_rule": (
            "Comparar tamanho da exposição, sinal/magnitude da média, IC95% e "
            "win-rate. Não promover edge por mudança isolada após a exclusão."
        ),
    }


def _load(path: str) -> dict:
    artifact = Path(path)
    if artifact.suffix == ".gz":
        with gzip.open(artifact, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    return json.loads(artifact.read_text(encoding="utf-8"))


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
