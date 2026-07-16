#!/usr/bin/env python3
"""Compara o P Dinâmico público do Miqueias com IRAI v1/v2 para WIN.

A página pública entrega o valor já calculado no Firebase. O bundle do gráfico
seleciona ``p_up_v1`` quando o campo existe e, caso contrário, usa ``p_up``.
Este script replica essa escolha, busca as duas versões da API local, alinha
somente timestamps que representam exatamente o mesmo instante e mede a
paridade numérica e operacional (regimes venda/neutro/compra em 40/60).

O timestamp retornado pela API do IRAI está no eixo do servidor Tickmill. Para
não esconder problemas de relógio, este comparador não aplica deslocamentos
heurísticos: offsets ISO explícitos são normalizados para UTC, e uma série sem
fuso não pode ser comparada silenciosamente com outra que possua fuso.

Uso normal na máquina Windows onde a API IRAI está ativa::

    python -X utf8 scripts/compare_p_dynamic_parity.py \
      --local-api http://localhost:8888 \
      --output-json p_dynamic_win.json

Também é possível comparar arquivos capturados::

    python -X utf8 scripts/compare_p_dynamic_parity.py \
      --skip-local-api \
      --candidate v1=win_v1.json --candidate v2=win_v2.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.irai.timezones import brt_to_tickmill_offset_hours


DEFAULT_PUBLIC_SOURCE = (
    "https://rastromacro-default-rtdb.firebaseio.com/series/WIN_N.json"
)
DEFAULT_LOCAL_API = "http://localhost:8888"
DEFAULT_TARGET = "WIN$N"
PUBLIC_VALUE_FIELDS = ("p_up_v1", "p_up")
LOCAL_VALUE_FIELDS = ("p_up",)


@dataclass(frozen=True)
class SeriesPoint:
    timestamp: str
    moment: datetime
    aware: bool
    value: float
    value_field: str
    is_ghost: bool
    is_preview: bool

    @property
    def operational(self) -> bool:
        return not self.is_ghost and not self.is_preview


def _extract_rows(payload, *, target_key: str = "WIN_N") -> list[dict]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        series = payload.get("series")
        if isinstance(series, list):
            rows = series
        elif isinstance(series, dict) and isinstance(series.get(target_key), list):
            rows = series[target_key]
        else:
            detail = payload.get("detail") or payload.get("error")
            raise ValueError(f"JSON não contém uma série {target_key}: {detail or 'formato desconhecido'}")
    else:
        raise ValueError("Fonte JSON precisa ser uma lista ou um objeto com `series`")

    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Todas as entradas da série precisam ser objetos JSON")
    return rows


def load_json_document(source: str, *, timeout: float = 10.0):
    """Lê um documento JSON sem descartar o envelope ou metadados da fonte."""
    if source.startswith(("http://", "https://")):
        request = Request(source, headers={"User-Agent": "IRAI-parity-audit/1.0"})
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    else:
        with Path(source).open("r", encoding="utf-8") as source_file:
            return json.load(source_file)


def load_json_source(source: str, *, timeout: float = 10.0) -> list[dict]:
    """Lê lista direta, envelope da API ou payload Firebase completo."""
    return _extract_rows(load_json_document(source, timeout=timeout))


def _parse_timestamp(raw_timestamp: object) -> tuple[str, datetime, bool]:
    if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
        raise ValueError("barra sem timestamp ISO válido")
    raw = raw_timestamp.strip()
    try:
        moment = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError as exc:
        raise ValueError(f"timestamp ISO inválido: {raw!r}") from exc

    aware = moment.utcoffset() is not None
    if aware:
        moment = moment.astimezone(timezone.utc)
    canonical = moment.isoformat(timespec="seconds")
    return canonical, moment, aware


def normalize_series(
    rows: Iterable[Mapping[str, object]],
    *,
    value_fields: Sequence[str],
) -> list[SeriesPoint]:
    """Normaliza uma série sem inventar valores nem casar barras por proximidade."""
    points: list[SeriesPoint] = []
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=1):
        timestamp, moment, aware = _parse_timestamp(row.get("timestamp"))
        if timestamp in seen:
            raise ValueError(f"timestamp duplicado na série: {timestamp}")

        selected_field = None
        selected_value = None
        for field in value_fields:
            value = row.get(field)
            if value is not None:
                selected_field = field
                selected_value = value
                break
        if selected_field is None:
            continue
        try:
            numeric_value = float(selected_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"valor não numérico em {selected_field}, barra {row_number}: {selected_value!r}"
            ) from exc
        if not math.isfinite(numeric_value):
            raise ValueError(f"valor não finito em {selected_field}, barra {row_number}")

        points.append(
            SeriesPoint(
                timestamp=timestamp,
                moment=moment,
                aware=aware,
                value=numeric_value,
                value_field=selected_field,
                is_ghost=bool(row.get("is_ghost", False)),
                is_preview=bool(row.get("is_preview", False)),
            )
        )
        seen.add(timestamp)
    return sorted(points, key=lambda point: point.moment)


def capture_session_status(
    points: Sequence[SeriesPoint],
    *,
    brt_offset_h: int,
    close_not_before: str = "17:50",
) -> dict:
    """Classifica a captura sem tratar pré-mercado ou sessão parcial como fechada."""
    operational = [point for point in points if point.operational]
    if not operational:
        return {
            "closed": False,
            "operational_rows": 0,
            "first_operational_brt": None,
            "last_operational_brt": None,
            "close_not_before_brt": close_not_before,
        }

    def brt_time(point: SeriesPoint) -> str:
        return (point.moment - timedelta(hours=brt_offset_h)).strftime("%H:%M")

    first_brt = brt_time(operational[0])
    last_brt = brt_time(operational[-1])
    return {
        "closed": last_brt >= close_not_before,
        "operational_rows": len(operational),
        "first_operational_brt": first_brt,
        "last_operational_brt": last_brt,
        "close_not_before_brt": close_not_before,
    }


def capture_brt_offset_h(session_date: str, documents: Mapping[str, object]) -> int:
    """Resolve BRT→Tickmill pelo contrato local ou pela regra sazonal causal."""
    for preferred in ("v2", "v1"):
        document = documents.get(preferred)
        if isinstance(document, dict) and document.get("brt_offset_h") is not None:
            return int(document["brt_offset_h"])
    return brt_to_tickmill_offset_hours(datetime.fromisoformat(session_date))


def _regime(value: float, *, buy_threshold: float, sell_threshold: float) -> str:
    if value >= buy_threshold:
        return "buy"
    if value <= sell_threshold:
        return "sell"
    return "neutral"


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _point_detail(reference: SeriesPoint, candidate: SeriesPoint) -> dict:
    difference = candidate.value - reference.value
    return {
        "timestamp": reference.timestamp,
        "reference": reference.value,
        "candidate": candidate.value,
        "difference": _rounded(difference),
        "absolute_difference": _rounded(abs(difference)),
    }


def _compare_subset(
    reference: Sequence[SeriesPoint],
    candidate: Sequence[SeriesPoint],
    *,
    tolerance: float,
    buy_threshold: float,
    sell_threshold: float,
) -> dict:
    if reference and candidate and reference[0].aware != candidate[0].aware:
        raise ValueError(
            "não é seguro alinhar timestamps com e sem fuso; corrija a fonte antes da comparação"
        )
    if any(point.aware != reference[0].aware for point in reference[1:]):
        raise ValueError("a série de referência mistura timestamps com e sem fuso")
    if any(point.aware != candidate[0].aware for point in candidate[1:]):
        raise ValueError("a série candidata mistura timestamps com e sem fuso")

    reference_by_time = {point.timestamp: point for point in reference}
    candidate_by_time = {point.timestamp: point for point in candidate}
    common_timestamps = sorted(set(reference_by_time) & set(candidate_by_time))
    pairs = [
        (reference_by_time[timestamp], candidate_by_time[timestamp])
        for timestamp in common_timestamps
    ]
    differences = [candidate_point.value - reference_point.value for reference_point, candidate_point in pairs]
    absolute_differences = [abs(value) for value in differences]

    correlation = None
    if len(pairs) >= 2:
        reference_values = [pair[0].value for pair in pairs]
        candidate_values = [pair[1].value for pair in pairs]
        if statistics.pstdev(reference_values) > 0 and statistics.pstdev(candidate_values) > 0:
            correlation = statistics.correlation(reference_values, candidate_values)

    confusion: dict[str, Counter] = defaultdict(Counter)
    concordant = 0
    for reference_point, candidate_point in pairs:
        reference_regime = _regime(
            reference_point.value,
            buy_threshold=buy_threshold,
            sell_threshold=sell_threshold,
        )
        candidate_regime = _regime(
            candidate_point.value,
            buy_threshold=buy_threshold,
            sell_threshold=sell_threshold,
        )
        confusion[reference_regime][candidate_regime] += 1
        concordant += reference_regime == candidate_regime

    first_divergence = next(
        (
            _point_detail(reference_point, candidate_point)
            for reference_point, candidate_point in pairs
            if abs(candidate_point.value - reference_point.value) > tolerance
        ),
        None,
    )
    maximum_detail = None
    if pairs:
        maximum_pair = max(
            pairs,
            key=lambda pair: abs(pair[1].value - pair[0].value),
        )
        maximum_detail = _point_detail(*maximum_pair)

    return {
        "reference_rows": len(reference),
        "candidate_rows": len(candidate),
        "common_rows": len(pairs),
        "reference_coverage_pct": _rounded(100 * len(pairs) / len(reference)) if reference else None,
        "candidate_coverage_pct": _rounded(100 * len(pairs) / len(candidate)) if candidate else None,
        "correlation": _rounded(correlation),
        "mae": _rounded(statistics.fmean(absolute_differences)) if pairs else None,
        "rmse": _rounded(math.sqrt(statistics.fmean(value * value for value in differences))) if pairs else None,
        "mean_difference": _rounded(statistics.fmean(differences)) if pairs else None,
        "max_absolute_difference": _rounded(max(absolute_differences)) if pairs else None,
        "max_difference_point": maximum_detail,
        "regime_concordance_pct": _rounded(100 * concordant / len(pairs)) if pairs else None,
        "regime_confusion": {
            reference_regime: dict(sorted(counts.items()))
            for reference_regime, counts in sorted(confusion.items())
        },
        "first_divergence": first_divergence,
    }


def describe_series(points: Sequence[SeriesPoint]) -> dict:
    return {
        "rows": len(points),
        "operational_rows": sum(point.operational for point in points),
        "first_timestamp": points[0].timestamp if points else None,
        "last_timestamp": points[-1].timestamp if points else None,
        "value_fields": dict(sorted(Counter(point.value_field for point in points).items())),
        "timezone_contract": (
            "explicit_offset_normalized_to_utc"
            if points and points[0].aware
            else "naive_provider_axis"
        ),
    }


def build_parity_report(
    reference: Sequence[SeriesPoint],
    candidates: Mapping[str, Sequence[SeriesPoint]],
    *,
    tolerance: float = 0.5,
    buy_threshold: float = 60.0,
    sell_threshold: float = 40.0,
) -> dict:
    if sell_threshold >= buy_threshold:
        raise ValueError("sell_threshold precisa ser menor que buy_threshold")
    if tolerance < 0:
        raise ValueError("tolerance não pode ser negativa")

    candidate_reports = {}
    for name, points in candidates.items():
        candidate_reports[name] = {
            "series": describe_series(points),
            "all_bars": _compare_subset(
                reference,
                points,
                tolerance=tolerance,
                buy_threshold=buy_threshold,
                sell_threshold=sell_threshold,
            ),
            "operational_bars": _compare_subset(
                [point for point in reference if point.operational],
                [point for point in points if point.operational],
                tolerance=tolerance,
                buy_threshold=buy_threshold,
                sell_threshold=sell_threshold,
            ),
        }

    ranking_basis = "operational_bars.mae"
    rankable = [
        name
        for name, report in candidate_reports.items()
        if report["operational_bars"]["mae"] is not None
    ]
    if not rankable:
        ranking_basis = "all_bars.mae"
        rankable = [
            name for name, report in candidate_reports.items() if report["all_bars"]["mae"] is not None
        ]
    ranking = sorted(
        rankable,
        key=lambda name: candidate_reports[name][ranking_basis.split(".")[0]]["mae"],
    )

    return {
        "thresholds": {
            "sell": sell_threshold,
            "buy": buy_threshold,
            "divergence_tolerance_points": tolerance,
        },
        "reference": describe_series(reference),
        "candidates": candidate_reports,
        "ranking_basis": ranking_basis,
        "ranking_by_operational_mae": ranking,
    }


def _parse_named_source(raw: str) -> tuple[str, str]:
    name, separator, source = raw.partition("=")
    if not separator or not name.strip() or not source.strip():
        raise argparse.ArgumentTypeError("use NOME=ARQUIVO_OU_URL")
    return name.strip(), source.strip()


def _local_series_url(base_url: str, *, session_date: str, target: str, version: str) -> str:
    query = urlencode({"session_date": session_date, "target": target, "version": version})
    return f"{base_url.rstrip('/')}/api/irai/series?{query}"


def _local_gex_url(base_url: str, *, target: str) -> str:
    return f"{base_url.rstrip('/')}/api/irai/gex?{urlencode({'target': target})}"


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-source", default=DEFAULT_PUBLIC_SOURCE)
    parser.add_argument("--local-api", default=DEFAULT_LOCAL_API)
    parser.add_argument("--skip-local-api", action="store_true")
    parser.add_argument(
        "--gex-source",
        default=None,
        help="Arquivo/URL de GEX; por padrão usa /api/irai/gex da API local.",
    )
    parser.add_argument(
        "--candidate",
        action="append",
        default=[],
        type=_parse_named_source,
        metavar="NOME=FONTE",
        help="Adiciona candidato de arquivo/URL; pode ser repetido.",
    )
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--sell-threshold", type=float, default=40.0)
    parser.add_argument("--buy-threshold", type=float, default=60.0)
    parser.add_argument("--tolerance", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--capture-dir", default=None)
    parser.add_argument("--output-json", default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        public_document = load_json_document(args.public_source, timeout=args.timeout)
        public_rows = _extract_rows(public_document)
        reference = normalize_series(public_rows, value_fields=PUBLIC_VALUE_FIELDS)
    except Exception as exc:
        print(f"Erro ao carregar referência pública: {exc}", file=sys.stderr)
        return 1
    if not reference:
        print("Erro: a referência pública não contém valores de P Dinâmico", file=sys.stderr)
        return 1

    session_date = args.session_date or reference[0].timestamp[:10]
    candidate_points: dict[str, list[SeriesPoint]] = {}
    candidate_documents: dict[str, object] = {}
    candidate_sources: dict[str, str] = {}
    source_errors: dict[str, str] = {}

    if not args.skip_local_api:
        for version in ("v1", "v2"):
            source = _local_series_url(
                args.local_api,
                session_date=session_date,
                target=args.target,
                version=version,
            )
            try:
                document = load_json_document(source, timeout=args.timeout)
                rows = _extract_rows(document)
                candidate_points[version] = normalize_series(rows, value_fields=LOCAL_VALUE_FIELDS)
                candidate_documents[version] = document
                candidate_sources[version] = source
            except Exception as exc:
                source_errors[version] = f"{type(exc).__name__}: {exc}"

    for name, source in args.candidate:
        if name in candidate_points:
            print(f"Erro: candidato duplicado: {name}", file=sys.stderr)
            return 1
        try:
            document = load_json_document(source, timeout=args.timeout)
            rows = _extract_rows(document)
            candidate_points[name] = normalize_series(rows, value_fields=LOCAL_VALUE_FIELDS)
            candidate_documents[name] = document
            candidate_sources[name] = source
        except Exception as exc:
            source_errors[name] = f"{type(exc).__name__}: {exc}"

    gex_source = args.gex_source
    if gex_source is None and not args.skip_local_api:
        gex_source = _local_gex_url(args.local_api, target=args.target)
    gex_document = None
    gex_error = None
    if gex_source:
        try:
            gex_document = load_json_document(gex_source, timeout=args.timeout)
        except Exception as exc:
            gex_error = f"{type(exc).__name__}: {exc}"

    try:
        comparison = build_parity_report(
            reference,
            candidate_points,
            tolerance=args.tolerance,
            buy_threshold=args.buy_threshold,
            sell_threshold=args.sell_threshold,
        )
    except ValueError as exc:
        print(f"Erro de contrato na comparação: {exc}", file=sys.stderr)
        return 1

    report = {
        "schema_version": 1,
        "generated_at": generated_at,
        "target": args.target,
        "session_date": session_date,
        "reference_source": args.public_source,
        "candidate_sources": candidate_sources,
        "source_errors": source_errors,
        **comparison,
    }
    ranked = comparison["ranking_by_operational_mae"]
    metric_scope = comparison["ranking_basis"].split(".")[0]
    closest_candidates: list[str] = []
    if ranked:
        minimum_mae = comparison["candidates"][ranked[0]][metric_scope]["mae"]
        closest_candidates = [
            name
            for name in ranked
            if math.isclose(
                comparison["candidates"][name][metric_scope]["mae"],
                minimum_mae,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ]
    operational_comparable = any(
        candidate["operational_bars"]["common_rows"] > 0
        for candidate in comparison["candidates"].values()
    )
    report["conclusion"] = {
        "scope": "parity_only",
        "comparable": bool(ranked),
        "operational_comparable": operational_comparable,
        "closest_candidate": closest_candidates[0] if len(closest_candidates) == 1 else None,
        "closest_candidates": closest_candidates,
        "parity_tie": len(closest_candidates) > 1,
        "quality_winner": None,
        "reason": (
            f"menor MAE segundo {comparison['ranking_basis']}"
            if ranked
            else "nenhuma série candidata teve timestamps comuns com a referência"
        ),
        "promotion_warning": (
            "Proximidade com a curva do Miqueias não mede qualidade preditiva. "
            "A versão vencedora deve ser escolhida por avaliação OOS contra outcomes do WIN."
        ),
    }

    if args.capture_dir:
        stamp = generated_at.replace(":", "").replace("+0000", "Z").replace("+00:00", "Z")
        capture_base = Path(args.capture_dir) / session_date / stamp
        capture_paths = {"miqueias": str(capture_base / "miqueias.json")}
        _write_json(Path(capture_paths["miqueias"]), public_document)
        for name, document in sorted(candidate_documents.items()):
            capture_paths[name] = str(capture_base / f"{name}.json")
            _write_json(Path(capture_paths[name]), document)
        capture_paths["gex"] = str(capture_base / "gex.json")
        stored_gex = gex_document if gex_document is not None else {
            "available": False,
            "reason": gex_error or "fonte GEX não configurada",
        }
        _write_json(Path(capture_paths["gex"]), stored_gex)
        brt_offset_h = capture_brt_offset_h(session_date, candidate_documents)
        source_session_status = {
            "miqueias": capture_session_status(reference, brt_offset_h=brt_offset_h),
            **{
                name: capture_session_status(points, brt_offset_h=brt_offset_h)
                for name, points in sorted(candidate_points.items())
            },
        }
        reference_status = source_session_status["miqueias"]
        local_statuses = [
            status for name, status in source_session_status.items()
            if name != "miqueias"
        ]
        session_status = {
            **reference_status,
            "closed": bool(local_statuses)
            and all(status["closed"] for status in source_session_status.values()),
            "closed_requirement": "todas as fontes capturadas e ao menos uma fonte local",
            "sources": source_session_status,
        }
        walls = gex_document.get("walls", []) if isinstance(gex_document, dict) else []
        gex_status = {
            "status": "captured" if gex_document is not None else "unavailable",
            "source": gex_source,
            "error": gex_error,
            "active": gex_document.get("active") if isinstance(gex_document, dict) else None,
            "as_of": gex_document.get("as_of") if isinstance(gex_document, dict) else None,
            "wall_count": sum(wall.get("type") == "wall" for wall in walls),
            "mid_wall_count": sum(wall.get("type") == "mid_wall" for wall in walls),
        }
        capture_paths["report"] = str(capture_base / "report.json")
        capture_paths["manifest"] = str(capture_base / "manifest.json")
        report["capture_paths"] = capture_paths
        report["capture_bundle"] = str(capture_base)
        manifest = {
            "schema_version": 1,
            "captured_at": generated_at,
            "session_date": session_date,
            "target": args.target,
            "objective": {
                "primary": "probabilidade de fechamento da sessão acima da abertura",
                "tactical_gate": "avaliado separadamente após regra econômica determinística",
            },
            "session": {**session_status, "brt_offset_h": brt_offset_h},
            "models": ["miqueias", *sorted(candidate_documents)],
            "sources": {
                "miqueias": args.public_source,
                **candidate_sources,
            },
            "source_errors": source_errors,
            "gex": gex_status,
            "files": {name: Path(path).name for name, path in capture_paths.items()},
        }
        _write_json(Path(capture_paths["report"]), report)
        _write_json(Path(capture_paths["manifest"]), manifest)
    if args.output_json:
        _write_json(Path(args.output_json), report)

    print(f"Referência Miqueias: {len(reference)} barras ({session_date})")
    for name in sorted(candidate_points):
        result = comparison["candidates"][name]
        operational = result["operational_bars"]
        all_bars = result["all_bars"]
        chosen = operational if operational["common_rows"] else all_bars
        scope = "operacionais" if operational["common_rows"] else "todas"
        print(
            f"{name}: {chosen['common_rows']} barras {scope}, "
            f"corr={chosen['correlation']}, MAE={chosen['mae']}, "
            f"regime={chosen['regime_concordance_pct']}%"
        )
    for name, error in sorted(source_errors.items()):
        print(f"{name}: indisponível — {error}")
    if ranked:
        if len(closest_candidates) > 1:
            print(
                f"Empate de paridade: {', '.join(closest_candidates)} "
                f"({comparison['ranking_basis']})"
            )
        else:
            print(f"Mais próximo: {closest_candidates[0]} ({comparison['ranking_basis']})")
        return 0
    print("Paridade ainda não calculável: nenhuma série candidata comparável.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
