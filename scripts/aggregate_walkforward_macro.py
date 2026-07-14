#!/usr/bin/env python3
"""Agrega predições OOS dos folds e calcula ΔAUC com bootstrap por sessão."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.measure_tactical_gate3 import bootstrap_auc_delta


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="JSONs gate3b dos folds")
    parser.add_argument("--bootstrap", type=int, default=10_000)
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()
    if args.bootstrap <= 0:
        parser.error("--bootstrap deve ser positivo")
    return args


def main() -> int:
    args = parse_args()
    grouped = defaultdict(list)
    seen = set()
    target_sessions = defaultdict(set)
    windows = []

    for path in args.inputs:
        with open(path, encoding="utf-8") as source:
            artifact = json.load(source)
        fields = artifact.get("pooled_prediction_fields", [])
        expected = [
            "target", "arm", "horizon", "scope", "session_id", "bar_index",
            "actual_up", "baseline_probability", "treatment_probability",
        ]
        if fields != expected:
            raise ValueError(f"{path}: esquema de pooled_predictions incompatível")
        windows.append({
            "path": path, "cutoff": artifact["cutoff"],
            "eval_start": artifact["eval_start"], "eval_end": artifact["eval_end"],
        })
        for values in artifact["pooled_predictions"]:
            row = dict(zip(fields, values))
            key = (
                row["target"], row["arm"], int(row["horizon"]), row["scope"],
                row["session_id"], int(row["bar_index"]),
            )
            if key in seen:
                raise ValueError(f"predição OOS duplicada entre folds: {key}")
            seen.add(key)
            group = key[:4]
            grouped[group].append((
                row["session_id"], float(row["treatment_probability"]),
                float(row["baseline_probability"]), bool(row["actual_up"]),
            ))
            target_sessions[row["target"]].add(row["session_id"])

    results = []
    for index, (group, rows) in enumerate(sorted(grouped.items())):
        estimate = bootstrap_auc_delta(
            rows, iterations=args.bootstrap, seed=20260714 + index
        )
        target, arm, horizon, scope = group
        results.append({
            "target": target, "arm": arm, "horizon": horizon, "scope": scope,
            "rows": len(rows), "sessions": estimate.n_sessions,
            "delta_auc": estimate.value, "ci95_low": estimate.ci_low,
            "ci95_high": estimate.ci_high, "standard_error": estimate.standard_error,
            "significant": estimate.significant,
        })

    print("OOS ACUMULADO — ΔAUC=(preço+macro residual)-preço; bootstrap pareado por sessão")
    print("TARGET ARM H  SESSÕES  LINHAS    ΔAUC          IC95%")
    for row in results:
        print(
            f"{row['target']:6s} {row['arm']:3s} {row['horizon']:2d} "
            f"{row['sessions']:8d} {row['rows']:7d} {row['delta_auc']:+.4f} "
            f"[{row['ci95_low']:+.4f}, {row['ci95_high']:+.4f}]"
        )

    common_sessions = set.intersection(*target_sessions.values()) if target_sessions else set()
    output = {
        "bootstrap": args.bootstrap, "windows": windows, "results": results,
        "sessions_by_target": {
            target: len(sessions) for target, sessions in sorted(target_sessions.items())
        },
        "common_oos_sessions": len(common_sessions),
    }
    print(f"Sessões OOS comuns aos targets: {len(common_sessions)}")
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as destination:
            json.dump(output, destination, indent=2, sort_keys=True)
        print(f"Resultados agregados: {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
