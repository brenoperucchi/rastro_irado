#!/usr/bin/env python3
"""Artefato do challenger Pair fixo WIN-WDO + comparação (IRAI-21 / IRAI-4 AC#3).

Roda o challenger (scripts/measure_pair_fixed_value.py), carrega o Pair
dinâmico + baselines de um artefato de referência (docs/artifacts/irai-4/…,
braço executável do codex) e monta a comparação BRUTA (retorno médio por
evento) e de FREQUÊNCIA EQUIVALENTE (expectativa por sessão = média/evento ×
eventos/sessão), por alvo e horizonte. Grava tudo num artefato SEPARADO
versionado em docs/artifacts/irai-21/, com comando/git-hash/limitações.

Ver metodologia congelada: docs/plans/2026-07-16-challenger-pair-fixo-win-wdo.md

Uso (host com sklearn/pykalman — ryzen5wsl):
  python3 -X utf8 scripts/build_challenger_artifact.py --db data/irai.db \\
    --limit 2000 --dynamic-summary docs/artifacts/irai-4/nf01_executable_pit_summary.json \\
    --output docs/artifacts/irai-21/pair_fixo_challenger.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.measure_pair_fixed_value as pf
from scripts.build_nf01_artifact import _git_state
from scripts.measure_pair_signal_value import (
    BOOTSTRAP_ITERATIONS, FORWARD_HORIZONS, estimate_mean, win_rate,
)

ARTIFACT_SCHEMA_VERSION = 1

# Quais sinais do artefato de referência entram na comparação, além do challenger.
REFERENCE_SIGNALS = ("pair", "baseline_momentum", "baseline_reversao")


class _LiteOutcome:
    """Outcome mínimo reconstruído de um evento serializado (fwd + session_date)
    — o suficiente para estimate_mean/win_rate reprocessarem uma sub-janela."""
    __slots__ = ("fwd", "session_date")

    def __init__(self, event: dict):
        # fwd vem com chaves-string do JSON; estimate_mean indexa por int.
        self.fwd = {int(k): v for k, v in event["fwd"].items()}
        self.session_date = event["session_date"]


def _first_pit_cutoff(reference: dict) -> str | None:
    """1º cutoff point-in-time do artefato de referência (define o início da
    janela do Pair dinâmico). None se a referência não for PIT."""
    for sig in reference.get("signals", {}).values():
        for tr in sig.get("targets", {}).values():
            cutoffs = tr.get("pit_cutoffs_used")
            if cutoffs:
                return cutoffs[0]
    return None


def _sessions_measured(target_report: dict) -> int:
    """Sessões que puderam contribuir eventos = replayadas − burn-in − pré-cutoff."""
    return max(
        0,
        int(target_report.get("sessions_replayed", 0))
        - int(target_report.get("sessions_burn_in", 0))
        - int(target_report.get("sessions_before_first_pit_cutoff", 0) or 0),
    )


def _signal_metrics(target_report: dict) -> dict:
    """Extrai, por horizonte, as métricas comparáveis de um target_report:
    retorno médio/evento (bruto), significância, eventos, e expectativa por
    sessão (frequência equivalente)."""
    measured = _sessions_measured(target_report)
    all_dir = target_report["by_direction"]["all"]
    out = {"sessions_measured": measured, "gate_verdict": target_report.get("gate_verdict"),
           "horizons": {}}
    for h in FORWARD_HORIZONS:
        hr = all_dir["horizons"][str(h)]
        est = hr["estimate"]
        if est is None:
            out["horizons"][str(h)] = None
            continue
        events = est["n_events"]
        mean_per_event = est["value"]
        events_per_session = events / measured if measured else None
        expectancy_per_session = (
            mean_per_event * events_per_session if events_per_session is not None else None
        )
        out["horizons"][str(h)] = {
            "mean_per_event": round(mean_per_event, 4),
            "significant": est["significant"],
            "ci_low": round(est["ci_low"], 4),
            "ci_high": round(est["ci_high"], 4),
            "events": events,
            "events_per_session": round(events_per_session, 4) if events_per_session is not None else None,
            "expectancy_per_session": round(expectancy_per_session, 4) if expectancy_per_session is not None else None,
            "win_rate_pct": round(hr["win_rate_pct"], 2) if hr["win_rate_pct"] == hr["win_rate_pct"] else None,
        }
    return out


def _load_reference(path: str | Path) -> dict:
    """Carrega o artefato de referência (JSON ou .json.gz)."""
    path = Path(path)
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    return json.loads(path.read_text(encoding="utf-8"))


def _windowed_challenger_metrics(challenger_target: dict, cutoff: str,
                                 sessions_measured: int, bootstrap: int) -> dict:
    """Recomputa as métricas do challenger SÓ sobre os eventos com
    `session_date > cutoff` — a MESMA janela do Pair dinâmico PIT (achado do
    /fable-reasoner: o ranking bruto misturava janelas). Reconstrói os
    outcomes dos eventos serializados e re-bootstrapa. `sessions_measured`
    (denominador de eventos/sessão) usa o do dinâmico na mesma janela —
    aproximação documentada: mesma janela temporal, ~mesmo nº de sessões."""
    events = challenger_target.get("events", [])
    windowed = [_LiteOutcome(e) for e in events if e["session_date"] > cutoff]
    out = {"sessions_measured": sessions_measured, "n_events_window": len(windowed),
           "note": ("challenger restrito à janela do dinâmico PIT (session_date > "
                    f"{cutoff}); denominador de sessões = o do dinâmico (aprox.)"),
           "horizons": {}}
    for h in FORWARD_HORIZONS:
        est = estimate_mean(windowed, h, iterations=bootstrap)
        _, total, pct = win_rate(windowed, h)
        if est is None:
            out["horizons"][str(h)] = None
            continue
        events_per_session = est.n_events / sessions_measured if sessions_measured else None
        expectancy = est.value * events_per_session if events_per_session is not None else None
        out["horizons"][str(h)] = {
            "mean_per_event": round(est.value, 4),
            "significant": est.significant,
            "ci_low": round(est.ci_low, 4),
            "ci_high": round(est.ci_high, 4),
            "events": est.n_events,
            "events_per_session": round(events_per_session, 4) if events_per_session is not None else None,
            "expectancy_per_session": round(expectancy, 4) if expectancy is not None else None,
            "win_rate_pct": round(pct, 2) if pct == pct else None,
        }
    return out


def build_comparison(challenger_report: dict, reference: dict | None, targets,
                     bootstrap: int = BOOTSTRAP_ITERATIONS) -> dict:
    """Monta a tabela comparativa por alvo: challenger vs sinais de referência.
    `reference` None -> só o challenger (comparação omitida com nota). Quando a
    referência é PIT, adiciona `pair_fixo_windowed`: o challenger recortado na
    MESMA janela temporal do dinâmico, para um ranking apples-to-apples."""
    comparison = {}
    cutoff = _first_pit_cutoff(reference) if reference is not None else None
    for target in targets:
        row = {"pair_fixo": _signal_metrics(challenger_report["targets"][target])}
        if reference is not None:
            for sig in REFERENCE_SIGNALS:
                tr = reference.get("signals", {}).get(sig, {}).get("targets", {}).get(target)
                if tr is not None:
                    row[sig] = _signal_metrics(tr)
            if cutoff is not None:
                dyn = reference.get("signals", {}).get("pair", {}).get("targets", {}).get(target)
                sessions = _sessions_measured(dyn) if dyn else 0
                row["pair_fixo_windowed"] = _windowed_challenger_metrics(
                    challenger_report["targets"][target], cutoff, sessions, bootstrap)
        comparison[target] = row
    return comparison


def build_artifact(db_path: str, targets, limit: int, bootstrap: int,
                   dynamic_summary: str | None, *, command: str, generated_at: str,
                   run_fixed_fn=None, reference_loader=None) -> dict:
    """`run_fixed_fn`/`reference_loader` injetáveis só pra teste."""
    run_fixed_fn = run_fixed_fn or pf.run_fixed
    challenger = run_fixed_fn(db_path, targets, limit, bootstrap)

    reference = None
    reference_meta = None
    if dynamic_summary:
        loader = reference_loader or _load_reference
        reference = loader(dynamic_summary)
        reference_meta = {
            "path": str(dynamic_summary),
            "generated_at": reference.get("generated_at"),
            "git_commit": reference.get("git", {}).get("commit"),
            "point_in_time": reference.get("parameters", {}).get("point_in_time"),
            "note": ("Pair dinâmico + baselines são point-in-time (~2022-12+); o challenger "
                     "mede toda a base (~2021+). Expectativa por sessão normaliza a "
                     "FREQUÊNCIA, não a janela temporal — use `pair_fixo_windowed` na "
                     "comparação para o ranking apples-to-apples (mesma janela)."),
        }

    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact": "challenger-pair-fixo-win-wdo",
        "generated_at": generated_at,
        "git": _git_state(),
        "command": command,
        "methodology": "docs/plans/2026-07-16-challenger-pair-fixo-win-wdo.md (congelada antes dos resultados)",
        "parameters": {
            "db": db_path, "targets": list(targets), "limit": limit, "bootstrap": bootstrap,
            "beta": "OLS rolling 20 sem intercepto", "threshold": pf.PAIR_THRESHOLD,
            "independent_of_calibration": True,
        },
        "reference": reference_meta,
        "comparison": build_comparison(challenger, reference, targets, bootstrap=bootstrap),
        "challenger": challenger,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=pf.DEFAULT_DB)
    parser.add_argument("--targets", nargs="+", choices=pf.DEFAULT_TARGETS, default=list(pf.DEFAULT_TARGETS))
    parser.add_argument("--limit", type=int, default=pf.DEFAULT_SESSION_LIMIT)
    parser.add_argument("--bootstrap", type=int, default=pf.BOOTSTRAP_ITERATIONS)
    parser.add_argument("--dynamic-summary", default=None,
                         help="Artefato de referência (Pair dinâmico + baselines), JSON ou .gz.")
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command = "python3 -X utf8 " + " ".join([str(Path(sys.argv[0]).as_posix())] + sys.argv[1:])
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"Construindo artefato do challenger Pair fixo — alvos {args.targets}, limite {args.limit}")
    artifact = build_artifact(
        args.db, args.targets, args.limit, args.bootstrap, args.dynamic_summary,
        command=command, generated_at=generated_at,
    )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    n_ev = sum(t["by_direction"]["all"]["n_events"] for t in artifact["challenger"]["targets"].values())
    print(f"Artefato gravado em {out} — challenger com {n_ev} eventos totais")
    print(f"git commit: {artifact['git'].get('commit')} (origin_main={artifact['git'].get('origin_main')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
