#!/usr/bin/env python3
"""Constrói o artefato NF-01 versionado (backlog IRAI-2, comentário #3, item 4).

Roda os 5 geradores de evento — Pair, divergência Z, interseção Pair∩Z e os 2
baselines (momentum, reversão) — sob o MESMO schedule de calibração
point-in-time e os MESMOS parâmetros, com `emit_events=True`, e consolida tudo
num único JSON versionado/localizável no repositório (não em /tmp) contendo:
  - comando executado e hash do git (reprodutibilidade);
  - parâmetros;
  - sessões e eventos por sinal (com os 4 timestamps causais por evento);
  - limitações (incluindo as políticas PROVISÓRIAS abaixo);
  - resultados agregados Pair, Z, interseção e baselines.

POLÍTICAS ECONÔMICAS deste artefato (IRAI-4/VAL-04):
  - `entry_price` = OPEN da barra M5 seguinte ao sinal — primeiro tick agregado
    pelo MT5 em/apos `signal_available_at`, proxy do primeiro preço executável;
  - MFE/MAE usam HIGH/LOW M5 desde a barra de entrada; a ordem intrabarra entre
    stop e alvo continua ambígua sem ticks;
  - custos completos, slippage e sensibilidade pertencem ao restante do
    IRAI-4/VAL-04 e ao shadow live.

Uso (rodar no host com sklearn/pykalman — ex.: ryzen5wsl):
  python3 -X utf8 scripts/build_nf01_artifact.py --db data/irai.db --point-in-time \\
    --limit 2000 --output docs/artifacts/irai-2/nf01_pit.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.measure_pair_signal_value as pair
import scripts.measure_price_divergence_value as zdiv
import scripts.measure_intersection_value as inter
import scripts.measure_baseline_value as base


ARTIFACT_SCHEMA_VERSION = 1

PROVISIONAL_POLICIES = {
    "entry_price": (
        "OPEN da PRÓXIMA barra M5 após o sinal — primeiro tick agregado pelo "
        "MT5 em/apos signal_available_at e proxy do primeiro preço executável. "
        "Slippage/latência de ticks ainda exigem sensibilidade e shadow live."
    ),
    "mfe_mae": (
        "calculados com HIGH/LOW M5 desde a barra de entrada. Se stop e alvo "
        "forem tocados na mesma barra, OHLC não revela qual ocorreu primeiro; "
        "essa ambiguidade exige ticks ou política conservadora."
    ),
    "costs": (
        "TARGET_COST_POINTS (WIN$N=10, WDO$N=1) é custo único aproximado, "
        "nunca derivado de P&L executável real (ADR-002). Custos completos e "
        "análise de sensibilidade são IRAI-4/VAL-04."
    ),
    "significance": (
        "cada sinal testa até 24 combinações horizonte×direção; um `***` "
        "isolado NÃO é confirmatório — ler consistência, não `***` isolado."
    ),
}


def _git_state() -> dict:
    """Estado do git pra localizar EXATAMENTE o código que gerou o artefato.
    Registra o HEAD do host de execução E o hash de origin/main — o segundo é
    localizável por quem clonar o repo, mesmo que o host tenha commits locais
    por cima (ex.: registros de deployment que não tocam os scripts do NF-01).
    `head_in_origin_main` diz se o HEAD é ancestral de origin/main (i.e. o
    código que rodou já está publicado). Tolera ausência de git."""
    root = str(Path(__file__).resolve().parents[1])
    def _run(args):
        return subprocess.run(["git", "-C", root, *args],
                              capture_output=True, text=True, timeout=10)
    try:
        commit = _run(["rev-parse", "HEAD"])
        if commit.returncode != 0:
            return {"commit": None, "dirty": None, "note": "git indisponível"}
        head = commit.stdout.strip()
        status = _run(["status", "--porcelain"])
        origin = _run(["rev-parse", "origin/main"])
        origin_main = origin.stdout.strip() if origin.returncode == 0 else None
        # HEAD é ancestral de origin/main? (código que rodou está publicado)
        head_in_origin = None
        if origin_main:
            anc = _run(["merge-base", "--is-ancestor", head, "origin/main"])
            head_in_origin = anc.returncode == 0
        return {
            "commit": head,
            "origin_main": origin_main,
            "head_in_origin_main": head_in_origin,
            "dirty": bool(status.stdout.strip()),
        }
    except Exception as exc:  # noqa: BLE001 — reprodutibilidade não pode derrubar o build
        return {"commit": None, "dirty": None, "note": f"{type(exc).__name__}: {exc}"}


def _pair_limitations(point_in_time: bool) -> list:
    if point_in_time:
        return pair.POINT_IN_TIME_LIMITATIONS + pair.COMMON_LIMITATIONS
    return pair.LIMITATIONS


def _z_limitations(point_in_time: bool) -> list:
    if point_in_time:
        return pair.POINT_IN_TIME_LIMITATIONS + pair.COMMON_LIMITATIONS + zdiv.EXTRA_LIMITATIONS
    return zdiv.LIMITATIONS


def _inter_limitations(point_in_time: bool) -> list:
    if point_in_time:
        return pair.POINT_IN_TIME_LIMITATIONS + pair.COMMON_LIMITATIONS + inter.EXTRA_LIMITATIONS
    return inter.LIMITATIONS


def _signal_specs(point_in_time: bool) -> dict:
    """Mapeia cada sinal aos argumentos de `run()` — mesma escolha que o
    main() de cada módulo faz, num só lugar testável."""
    return {
        "pair": dict(direction_of=None, preprocess=None,
                     limitations=_pair_limitations(point_in_time)),
        "z": dict(direction_of=zdiv._divergence_direction, preprocess=None,
                  limitations=_z_limitations(point_in_time)),
        "intersection": dict(direction_of=inter._intersection_direction,
                             preprocess=inter._mark_intersection,
                             limitations=_inter_limitations(point_in_time)),
        "baseline_momentum": dict(direction_of=base._baseline_direction,
                                  preprocess=base._mark_momentum,
                                  limitations=base._limitations("momentum", point_in_time)),
        "baseline_reversao": dict(direction_of=base._baseline_direction,
                                  preprocess=base._mark_reversao,
                                  limitations=base._limitations("reversao", point_in_time)),
    }


def build_artifact(db_path: str, targets, limit: int, bootstrap: int,
                   burn_in_sessions: int, point_in_time: bool,
                   *, command: str, generated_at: str,
                   pit_builder=None, run_fn=None) -> dict:
    """Monta o artefato. `pit_builder`/`run_fn` são injetáveis só pra teste
    (produção usa pit_calibration.build_schedule e pair.run reais)."""
    run_fn = run_fn or pair.run
    pit_schedule = None
    if point_in_time:
        if pit_builder is None:
            import scripts.pit_calibration as pit_calibration
            pit_builder = pit_calibration.build_schedule
        pit_schedule = pit_builder(db_path, targets)

    signals = {}
    for name, spec in _signal_specs(point_in_time).items():
        signals[name] = run_fn(
            db_path, targets, limit, bootstrap, burn_in_sessions,
            direction_of=spec["direction_of"], preprocess=spec["preprocess"],
            limitations=spec["limitations"], pit_schedule=pit_schedule,
            emit_events=True,
        )

    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact": "nf01-pair-z-intersection-baselines",
        "generated_at": generated_at,
        "git": _git_state(),
        "command": command,
        "parameters": {
            "db": db_path,
            "targets": list(targets),
            "limit": limit,
            "bootstrap": bootstrap,
            "burn_in_sessions": burn_in_sessions,
            "point_in_time": point_in_time,
            "min_events_for_gate": pair.MIN_EVENTS_FOR_GATE,
            "cost_multipliers": list(pair.COST_MULTIPLIERS),
        },
        "provisional_policies": PROVISIONAL_POLICIES,
        "signals": signals,
    }


def summarize_artifact(artifact: dict, *, events_moved_to: str) -> dict:
    """Copia o artefato sem o ledger pesado e preserva contagens auditáveis."""
    summary = json.loads(json.dumps(artifact))
    counts = {}
    for signal_name, signal in summary["signals"].items():
        counts[signal_name] = {}
        for target_name, target in signal["targets"].items():
            counts[signal_name][target_name] = len(target.pop("events", []))
    summary["events_moved_to"] = events_moved_to
    summary["event_counts"] = counts
    return summary


def write_json_artifact(path: Path, payload: dict) -> None:
    """Grava JSON puro ou gzip determinístico quando o nome termina em .gz."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.suffix == ".gz":
        with path.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
                compressed.write(serialized)
        return
    path.write_bytes(serialized)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=pair.DEFAULT_DB)
    parser.add_argument("--targets", nargs="+", choices=pair.DEFAULT_TARGETS,
                         default=list(pair.DEFAULT_TARGETS))
    parser.add_argument("--limit", type=int, default=pair.DEFAULT_SESSION_LIMIT)
    parser.add_argument("--bootstrap", type=int, default=pair.BOOTSTRAP_ITERATIONS)
    parser.add_argument("--burn-in-sessions", type=int, default=pair.DEFAULT_BURN_IN_SESSIONS)
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--output", required=True,
                         help="Caminho versionado do artefato (ex.: docs/artifacts/irai-2/nf01_pit.json).")
    parser.add_argument(
        "--summary-output",
        help="JSON legível sem eventos; registra contagens e aponta para --output.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command = "python3 -X utf8 " + " ".join([str(Path(sys.argv[0]).as_posix())] + sys.argv[1:])
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"Construindo artefato NF-01 — modo={'point-in-time' if args.point_in_time else 'retrospectivo'}")
    print(f"Alvos: {args.targets} · limite: {args.limit} · bootstrap: {args.bootstrap}")
    artifact = build_artifact(
        args.db, args.targets, args.limit, args.bootstrap, args.burn_in_sessions,
        args.point_in_time, command=command, generated_at=generated_at,
    )
    out = Path(args.output)
    write_json_artifact(out, artifact)
    if args.summary_output:
        summary_out = Path(args.summary_output)
        write_json_artifact(
            summary_out,
            summarize_artifact(artifact, events_moved_to=out.name),
        )
    total_events = sum(
        sum(t.get("events", []).__len__() for t in sig["targets"].values())
        for sig in artifact["signals"].values()
    )
    print(f"Artefato gravado em {out} — {len(artifact['signals'])} sinais, {total_events} eventos totais")
    if args.summary_output:
        print(f"Resumo sem eventos gravado em {args.summary_output}")
    print(f"git commit: {artifact['git'].get('commit')} (dirty={artifact['git'].get('dirty')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
