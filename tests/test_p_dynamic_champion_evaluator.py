"""Regressões do ledger e torneio champion-challenger do P Dinâmico."""

from __future__ import annotations

import json
import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.compare_p_dynamic_parity import (  # noqa: E402
    capture_session_status,
    main as capture_main,
    normalize_series,
)
from scripts.evaluate_p_dynamic_champions import (  # noqa: E402
    LedgerSession,
    _with_causal_climatology,
    evaluate_champions,
    load_ledger_sessions,
)


def _row(timestamp, p_up, *, win_open=100.0, win_current=110.0, **extra):
    return {
        "timestamp": timestamp,
        "p_up": p_up,
        "win_open": win_open,
        "win_current": win_current,
        "is_ghost": False,
        "is_preview": False,
        **extra,
    }


def test_status_exige_barra_real_ate_1750_brt():
    incomplete = normalize_series(
        [_row("2026-07-16T20:00:00Z", 60.0)], value_fields=("p_up",)
    )
    complete = normalize_series(
        [_row("2026-07-16T23:50:00Z", 60.0)], value_fields=("p_up",)
    )

    assert capture_session_status(incomplete, brt_offset_h=6)["closed"] is False
    status = capture_session_status(complete, brt_offset_h=6)
    assert status["closed"] is True
    assert status["last_operational_brt"] == "17:50"


def test_captura_usa_offset_sazonal_quando_documento_local_nao_informa_fuso(tmp_path):
    """Em janeiro, 22:55 no eixo Tickmill corresponde a 17:55 BRT (+5h)."""
    public = tmp_path / "public.json"
    candidate = tmp_path / "v2.json"
    output = tmp_path / "report.json"
    captures = tmp_path / "captures"
    rows = [_row("2026-01-15T22:55:00Z", 60.0)]
    public.write_text(json.dumps(rows), encoding="utf-8")
    candidate.write_text(json.dumps(rows), encoding="utf-8")

    capture_main([
        "--public-source", str(public),
        "--skip-local-api",
        "--candidate", f"v2={candidate}",
        "--capture-dir", str(captures),
        "--output-json", str(output),
    ])

    report = json.loads(output.read_text(encoding="utf-8"))
    with open(os.path.join(report["capture_bundle"], "manifest.json"), encoding="utf-8") as source:
        manifest = json.load(source)
    assert manifest["session"]["brt_offset_h"] == 5
    assert manifest["session"]["closed"] is True
    assert manifest["session"]["sources"]["v2"]["last_operational_brt"] == "17:55"


def test_captura_e_loader_rejeitam_outcome_local_sem_fechamento(tmp_path):
    public = tmp_path / "public.json"
    candidate = tmp_path / "v2.json"
    output = tmp_path / "report.json"
    captures = tmp_path / "captures"
    public.write_text(
        json.dumps([_row("2026-07-16T23:55:00Z", 60.0)]), encoding="utf-8"
    )
    candidate.write_text(
        json.dumps({
            "brt_offset_h": 6,
            "series": [_row("2026-07-16T23:30:00Z", 60.0)],
        }),
        encoding="utf-8",
    )

    capture_main([
        "--public-source", str(public),
        "--skip-local-api",
        "--candidate", f"v2={candidate}",
        "--capture-dir", str(captures),
        "--output-json", str(output),
    ])

    report = json.loads(output.read_text(encoding="utf-8"))
    with open(os.path.join(report["capture_bundle"], "manifest.json"), encoding="utf-8") as source:
        manifest = json.load(source)
    sessions, audit = load_ledger_sessions(captures)

    assert manifest["session"]["sources"]["miqueias"]["closed"] is True
    assert manifest["session"]["sources"]["v2"]["closed"] is False
    assert manifest["session"]["closed"] is False
    assert sessions == []
    assert audit["incomplete_bundles"] == 1

    # Defesa em profundidade: mesmo um manifesto antigo/corrompido que alegue
    # fechamento não pode fazer o loader aceitar o outcome parcial.
    manifest["session"]["closed"] = True
    with open(os.path.join(report["capture_bundle"], "manifest.json"), "w", encoding="utf-8") as destination:
        json.dump(manifest, destination)
    sessions, audit = load_ledger_sessions(captures)
    assert sessions == []
    assert audit["invalid_bundles"] == 1
    assert "fontes sem fechamento operacional: v2" in audit["invalid_reasons"][0]


def test_captura_cria_manifest_preserva_envelope_e_tolera_gex_ausente(tmp_path):
    public_path = tmp_path / "miqueias.json"
    v1_path = tmp_path / "v1.json"
    v2_path = tmp_path / "v2.json"
    capture_dir = tmp_path / "ledger"
    output_path = tmp_path / "latest.json"
    public_path.write_text(
        json.dumps([_row("2026-07-16T23:50:00Z", 70.0)]), encoding="utf-8"
    )
    for path, probability in ((v1_path, 65.0), (v2_path, 75.0)):
        path.write_text(
            json.dumps(
                {
                    "session_date": "2026-07-16",
                    "target": "WIN$N",
                    "is_b3": True,
                    "brt_offset_h": 6,
                    "series": [
                        _row(
                            "2026-07-16T23:50:00Z",
                            probability,
                            win_bar_open=108.0,
                            win_high=112.0,
                            win_low=107.0,
                            pair_z=2.1,
                            pair_signal="sell",
                            nwe_center_price=109.0,
                            nwe_direction="up",
                        )
                    ],
                }
            ),
            encoding="utf-8",
        )

    status = capture_main(
        [
            "--public-source",
            str(public_path),
            "--skip-local-api",
            "--candidate",
            f"v1={v1_path}",
            "--candidate",
            f"v2={v2_path}",
            "--capture-dir",
            str(capture_dir),
            "--output-json",
            str(output_path),
        ]
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    bundle = tmp_path / report["capture_bundle"].split(str(tmp_path) + os.sep)[-1]
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    stored_v2 = json.loads((bundle / "v2.json").read_text(encoding="utf-8"))

    assert status == 0
    assert manifest["schema_version"] == 1
    assert manifest["session"]["closed"] is True
    assert manifest["gex"]["status"] == "unavailable"
    assert stored_v2["brt_offset_h"] == 6
    assert stored_v2["series"][0]["pair_z"] == 2.1
    assert stored_v2["series"][0]["nwe_center_price"] == 109.0
    assert stored_v2["series"][0]["win_bar_open"] == 108.0
    assert stored_v2["series"][0]["win_high"] == 112.0
    assert stored_v2["series"][0]["win_low"] == 107.0


def test_captura_preserva_gex_mid_e_walls(tmp_path):
    public = tmp_path / "public.json"
    candidate = tmp_path / "v2.json"
    gex = tmp_path / "gex.json"
    output = tmp_path / "report.json"
    captures = tmp_path / "captures"
    row = _row("2026-07-16T23:50:00Z", 60.0)
    public.write_text(json.dumps([row]), encoding="utf-8")
    candidate.write_text(
        json.dumps({"brt_offset_h": 6, "series": [row]}), encoding="utf-8"
    )
    gex.write_text(
        json.dumps(
            {
                "active": True,
                "as_of": "2026-07-15",
                "gamma_flip": 100.0,
                "walls": [
                    {"type": "wall", "price": 99.0},
                    {"type": "mid_wall", "price": 100.0},
                ],
            }
        ),
        encoding="utf-8",
    )

    capture_main(
        [
            "--public-source",
            str(public),
            "--skip-local-api",
            "--candidate",
            f"v2={candidate}",
            "--gex-source",
            str(gex),
            "--capture-dir",
            str(captures),
            "--output-json",
            str(output),
        ]
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    manifest_path = os.path.join(report["capture_bundle"], "manifest.json")
    with open(manifest_path, encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)

    assert manifest["gex"]["status"] == "captured"
    assert manifest["gex"]["wall_count"] == 1
    assert manifest["gex"]["mid_wall_count"] == 1


def test_avaliador_bloqueia_vencedor_abaixo_do_gate():
    session = LedgerSession(
        session_date="2026-07-16",
        actual_up=True,
        forecasts={"miqueias": [0.7], "v1": [0.6], "v2": [0.8]},
    )

    report = evaluate_champions([session], min_sessions=20, bootstrap_iterations=100)

    assert report["status"] == "INCONCLUSIVE"
    assert report["quality_winner"] is None
    assert report["common_sessions"] == 1
    assert report["tactical_gate"]["status"] == "NOT_EVALUATED"


def test_baseline_climatologico_usa_somente_sessoes_anteriores():
    sessions = [
        LedgerSession("2026-07-15", True, {"v2": [0.7]}),
        LedgerSession("2026-07-16", False, {"v2": [0.3]}),
    ]

    augmented = _with_causal_climatology(sessions)

    assert augmented[0].forecasts["baseline_climatology"] == [0.5]
    assert augmented[1].forecasts["baseline_climatology"] == [2 / 3]


def test_avaliador_promove_apenas_vencedor_significante_no_bootstrap():
    sessions = []
    for index in range(60):
        actual_up = index % 2 == 0
        sessions.append(
            LedgerSession(
                session_date=f"s{index:02d}",
                actual_up=actual_up,
                forecasts={
                    "miqueias": [0.6 if actual_up else 0.4],
                    "v1": [0.55 if actual_up else 0.45],
                    "v2": [0.85 if actual_up else 0.15],
                },
            )
        )

    report = evaluate_champions(
        sessions,
        min_sessions=60,
        bootstrap_iterations=1_000,
        seed=7,
    )

    assert report["status"] == "WINNER"
    assert report["quality_winner"] == "v2"
    assert report["ranking_by_brier"][0] == "v2"
    assert all(comparison["ci_high"] < 0 for comparison in report["winner_tests"])


def test_loader_ignora_bundle_de_sessao_incompleta(tmp_path):
    bundle = tmp_path / "2026-07-16" / "capture"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_date": "2026-07-16",
                "session": {"closed": False},
                "files": {},
            }
        ),
        encoding="utf-8",
    )

    sessions, audit = load_ledger_sessions(tmp_path)

    assert sessions == []
    assert audit["incomplete_bundles"] == 1
