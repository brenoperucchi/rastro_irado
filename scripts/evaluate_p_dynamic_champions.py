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
import gzip
import hashlib
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
    _document_with_session_rows,
    _extract_rows,
    _parse_timestamp,
    _session_rows,
    METHODOLOGY_VERSION,
    PUBLIC_MODEL,
    TOURNAMENT_MODELS,
    build_source_statuses,
    canonical_session_slots,
    capture_brt_offset_h,
    in_session_brt,
    normalize_series,
    session_intersection_stats,
    session_operational_points,
)
from backend.irai.runtime_revision import (
    prediction_revision_fingerprint,
    validate_engine_revision,
)


DEFAULT_MIN_SESSIONS = 60
DEFAULT_BOOTSTRAP_ITERATIONS = 10_000
EPSILON = 1e-6
LOCAL_TOURNAMENT_MODELS = ("v1", "v2")


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


def _engine_revision_from_manifest(manifest: dict) -> tuple[str, dict[str, str]]:
    """Valida a identidade do motor que produziu um bundle do ledger.

    ``methodology_version`` protege a regra de apuração. Ela não diz qual
    implementação gerou p_up. Sem este contrato, um restart após alterar o
    Kalman poderia acumular sessões incompatíveis no mesmo torneio.
    """
    try:
        normalized = validate_engine_revision(manifest.get("engine_revision"))
    except ValueError as exc:
        raise ValueError(f"manifesto sem revisão verificável do motor: {exc}") from exc

    fingerprint = prediction_revision_fingerprint(normalized)
    return fingerprint, normalized


def _outcome_rows(document, *, brt_offset_h: int) -> list[dict]:
    """Desfecho do WIN sobre as barras EM SESSÃO das fontes locais.

    Base deliberadamente distinta da interseção usada na pontuação: o rótulo é
    propriedade do MERCADO, não dos modelos. Ancorá-lo na última barra pontuada
    tornaria o alvo endógeno à disponibilidade do feed de terceiro e vazaria o
    preço quase-determinante para dentro do próprio rótulo. O que as duas bases
    partilham é a janela de pregão -- e é isso que importa.

    Sem essa janela, a barra de after-market (mesma data BRT, logo dentro do
    bundle, mas fora da pontuação) fixava o rótulo com um preço que nenhuma
    barra pontuada viu. Medido em data/irai.db: 40 de 1253 sessões (3,2%; 4,7%
    entre as 844 que têm barra após 18:00). O filtro é indispensável no regime
    de inverno, quando brt_offset_h=5 faz o payload cobrir até 18:55 BRT; com
    offset 6 quem carrega é o piso de 09:00, porque as barras de pré-mercado
    trazem o win_open da sessão ANTERIOR.
    """
    rows = [
        row
        for row in _extract_rows(document)
        if not row.get("is_ghost", False)
        and not row.get("is_preview", False)
        and row.get("win_open") is not None
        and row.get("win_current") is not None
        and in_session_brt(row.get("timestamp"), brt_offset_h=brt_offset_h)
    ]
    return rows


def _actual_outcome(local_documents, *, brt_offset_h: int) -> tuple[bool, str]:
    """Rótulo sobre a última barra comum às fontes locais.

    Preferir v2 incondicionalmente fazia o rótulo depender de qual documento
    estava mais completo: com v2 fechando 17:50 e v1 17:55, ambos elegíveis,
    a mesma sessão rendia desfechos diferentes -- 8 de 1253 sessões no banco.
    """
    by_source = {
        name: {
            _parse_timestamp(row["timestamp"])[0]: row
            for row in _outcome_rows(document, brt_offset_h=brt_offset_h)
        }
        for name, document in local_documents.items()
    }
    by_source = {name: rows for name, rows in by_source.items() if rows}
    missing_sources = sorted(set(LOCAL_TOURNAMENT_MODELS) - set(by_source))
    if missing_sources:
        raise ValueError(
            "fontes locais sem preço operacional para formar o outcome: "
            + ", ".join(missing_sources)
        )
    common = sorted(set.intersection(*(set(rows) for rows in by_source.values())))
    if not common:
        raise ValueError("fontes locais não compartilham barra em sessão para o outcome")
    def price(timestamp: str, field: str) -> float:
        values = {
            float(rows[timestamp][field])
            for rows in by_source.values()
            if timestamp in rows
        }
        if len(values) > 1:
            raise ValueError(
                f"fontes locais divergem em {field} na barra {timestamp}: "
                + ", ".join(str(value) for value in sorted(values))
            )
        return values.pop()

    return (
        price(common[-1], "win_current") > price(common[0], "win_open"),
        common[-1],
    )


def _validate_raw_archive(manifest: dict, bundle: Path) -> None:
    """Garante que o bundle elegível ainda possui os bytes auditáveis do trio.

    O capturador já marca falhas de arquivo como inelegíveis. Esta validação no
    leitor evita que manifesto corrompido ou forjado revogue essa decisão.
    """
    session = manifest.get("session")
    if not isinstance(session, dict) or session.get("raw_archive_complete") is not True:
        raise ValueError("cru não arquivado, sessão não é reprodutível")
    raw_entries = manifest.get("raw")
    if not isinstance(raw_entries, dict):
        raise ValueError("manifesto sem entradas de cru auditáveis")

    bundle_root = bundle.resolve()
    for name in TOURNAMENT_MODELS:
        entry = raw_entries.get(name)
        if not isinstance(entry, dict) or "error" in entry:
            raise ValueError(f"cru ausente ou falho para {name}")
        relative_path = entry.get("file")
        expected_sha256 = entry.get("sha256")
        expected_size = entry.get("bytes")
        if (
            not isinstance(relative_path, str)
            or not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
        ):
            raise ValueError(f"metadado de cru inválido para {name}")
        raw_path = (bundle / relative_path).resolve()
        try:
            raw_path.relative_to(bundle_root)
        except ValueError as exc:
            raise ValueError(f"caminho de cru fora do bundle para {name}") from exc

        digest = hashlib.sha256()
        actual_size = 0
        try:
            with gzip.open(raw_path, "rb") as source:
                while chunk := source.read(64 * 1024):
                    digest.update(chunk)
                    actual_size += len(chunk)
        except OSError as exc:
            raise ValueError(f"cru ilegível para {name}: {exc}") from exc
        if digest.hexdigest() != expected_sha256 or actual_size != expected_size:
            raise ValueError(f"integridade do cru inválida para {name}")


def _normalized_model_series(documents) -> dict[str, list]:
    """Normaliza cada documento uma vez, com o contrato de campo correto."""
    return {
        model: normalize_series(
            _extract_rows(document),
            value_fields=(
                PUBLIC_VALUE_FIELDS if model == PUBLIC_MODEL else LOCAL_VALUE_FIELDS
            ),
        )
        for model, document in documents.items()
    }


def _aligned_forecasts(
    normalized_models, *, brt_offset_h: int, minimum_rows: int
) -> dict[str, list[float]]:
    """Pontua todos os modelos exatamente nas MESMAS barras.

    Média sobre as barras que cada modelo por acaso tem não é comparação: as
    barras da manhã valem Brier ~0,25 (P≈0,5) e as do fim valem quase zero,
    então quem perde manhã ganha score de graça. Sob degradação simulada (perder
    as 10 piores barras) o ganho chega a +0,066 de Brier, várias vezes a margem
    que decide o torneio; nos dois bundles preservados, onde a divergência real
    era de uma barra, o efeito antigo->novo é de apenas +0,00015 e o ranking não
    muda. Alinhar por timestamp elimina a exposição na origem, em vez de tentar
    contê-la com limiar de elegibilidade.
    """
    by_model: dict[str, dict[str, float]] = {}
    for model, normalized_points in normalized_models.items():
        points = session_operational_points(
            normalized_points,
            brt_offset_h=brt_offset_h,
        )
        series = {}
        for point in points:
            probability = point.value / 100.0
            if not 0.0 <= probability <= 1.0:
                raise ValueError(
                    f"P_up fora de [0,100] em {point.timestamp}: {point.value}"
                )
            series[point.timestamp] = probability
        if not series:
            raise ValueError(f"modelo {model} sem forecasts operacionais na sessão")
        by_model[model] = series

    if not by_model:
        raise ValueError("bundle sem modelos para alinhar")
    common = set.intersection(*(set(series) for series in by_model.values()))
    ordered = sorted(common)
    covered = canonical_session_slots(ordered, brt_offset_h=brt_offset_h)
    if len(covered) < minimum_rows:
        raise ValueError(
            "interseção pontuável insuficiente: "
            f"{len(covered)} slots M5 < {minimum_rows} exigidos"
        )
    return {
        model: [series[timestamp] for timestamp in ordered]
        for model, series in by_model.items()
    }


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
        "superseded_bundles": 0,
        "foreign_version_bundles": 0,
        "mixed_engine_revision_bundles": 0,
        "engine_revision_groups": {},
        "outcome_timestamps": {},
        "invalid_reasons": [],
        "dropped_models": [],
    }
    latest_by_session: dict[str, tuple[str, Path, dict]] = {}
    for manifest_path in manifests:
        try:
            manifest = _read_json(manifest_path)
            captured_methodology = manifest.get("methodology_version", 1)
            if not isinstance(captured_methodology, int) or isinstance(
                captured_methodology, bool
            ):
                raise ValueError(
                    f"methodology_version precisa ser inteiro: {captured_methodology!r}"
                )
            if captured_methodology != METHODOLOGY_VERSION:
                # Futuro também é incompatível: um rollback só do avaliador
                # agregaria bundles de régua mais nova como se fossem desta.
                key = (
                    "superseded_bundles"
                    if captured_methodology < METHODOLOGY_VERSION
                    else "foreign_version_bundles"
                )
                audit[key] += 1
                continue
            if not manifest.get("session", {}).get("closed", False):
                audit["incomplete_bundles"] += 1
                continue
            _engine_revision_from_manifest(manifest)
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
    revisions_by_session: dict[str, tuple[str, dict[str, str]]] = {}
    for session_date, (_, manifest_path, manifest) in sorted(latest_by_session.items()):
        try:
            revision_fingerprint, revision = _engine_revision_from_manifest(manifest)
            bundle = manifest_path.parent
            files = manifest.get("files", {})
            documents = {
                model: _read_json(bundle / files[model])
                for model in manifest.get("models", [])
                if model in files
            }
            missing_core = sorted(set(TOURNAMENT_MODELS) - set(documents))
            if missing_core:
                raise ValueError(
                    "bundle sem os participantes obrigatórios do torneio: "
                    + ", ".join(missing_core)
                )
            _validate_raw_archive(manifest, bundle)
            brt_offset_h = capture_brt_offset_h(session_date, documents)
            # Defesa em profundidade: bundles gravados antes do filtro por
            # sessão BRT carregam a cauda da sessão anterior. Hoje ela é toda
            # ghost/preview e não pontua, mas depender desse flag para a
            # integridade do ledger é frágil -- barra de outra sessão não pode
            # entrar em Brier/log-loss nem definir o outcome do WIN.
            # Isola por modelo: um challenger esporádico sem barras da sessão
            # (ex.: rodada manual com --miqueias-static-config no mesmo
            # capture-dir) não pode derrubar miqueias/v1/v2 válidos e custar
            # uma sessão do gate de 60.
            session_documents = {}
            for model, document in documents.items():
                try:
                    session_documents[model] = _document_with_session_rows(
                        document,
                        _session_rows(
                            _extract_rows(document),
                            session_date=session_date,
                            brt_offset_h=brt_offset_h,
                            label=f"{model} no bundle de {session_date}",
                        ),
                    )
                except Exception as exc:
                    audit["dropped_models"].append(
                        f"{manifest_path}: {model}: {type(exc).__name__}: {exc}"
                    )
            missing_essential = sorted(
                set(TOURNAMENT_MODELS) - set(session_documents)
            )
            if missing_essential:
                raise ValueError(
                    "fontes essenciais sem barras da sessão: "
                    + ", ".join(missing_essential)
                )
            official_documents = {
                name: session_documents[name] for name in TOURNAMENT_MODELS
            }
            normalized_models = _normalized_model_series(official_documents)
            source_statuses = build_source_statuses(
                normalized_models,
                brt_offset_h=brt_offset_h,
            )
            incomplete_sources = sorted(
                model for model, status in source_statuses.items() if not status["closed"]
            )
            if incomplete_sources:
                raise ValueError(
                    "fontes sem fechamento operacional: " + ", ".join(incomplete_sources)
                )
            intersection = session_intersection_stats(
                normalized_models,
                brt_offset_h=brt_offset_h,
            )
            if not intersection["sufficient"]:
                raise ValueError(
                    "interseção pontuável insuficiente: "
                    f"{intersection['canonical_slots_covered']} slots M5 cobertos "
                    f"< {intersection['min_rows']} exigidos "
                    f"(gap máximo {intersection['max_gap_minutes']}min)"
                )
            forecasts = _aligned_forecasts(
                normalized_models,
                brt_offset_h=brt_offset_h,
                minimum_rows=intersection["min_rows"],
            )
            if len(forecasts) < 2:
                raise ValueError("bundle precisa de pelo menos dois modelos comparáveis")
            actual_up, outcome_timestamp = _actual_outcome(
                {
                    name: official_documents[name]
                    for name in LOCAL_TOURNAMENT_MODELS
                },
                brt_offset_h=brt_offset_h,
            )
            audit["outcome_timestamps"][session_date] = outcome_timestamp
            sessions.append(
                LedgerSession(
                    session_date=session_date,
                    actual_up=actual_up,
                    forecasts=forecasts,
                )
            )
            revisions_by_session[session_date] = (revision_fingerprint, revision)
        except Exception as exc:
            audit["invalid_bundles"] += 1
            audit["invalid_reasons"].append(
                f"{manifest_path}: {type(exc).__name__}: {exc}"
            )
    revision_groups: dict[str, dict] = {}
    for session_date, (fingerprint, revision) in revisions_by_session.items():
        group = revision_groups.setdefault(
            fingerprint,
            {"revision": revision, "session_dates": []},
        )
        group["session_dates"].append(session_date)
    audit["engine_revision_groups"] = revision_groups
    if len(revision_groups) > 1:
        audit["mixed_engine_revision_bundles"] = len(sessions)
        audit["invalid_reasons"].append(
            "ledger contém múltiplas revisões do motor; não mistura sessões no torneio"
        )
        sessions = []
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
        "methodology_version": METHODOLOGY_VERSION,
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
