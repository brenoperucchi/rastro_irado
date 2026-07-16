"""Specs da sensibilidade NF-01 com/sem sessões de rollover."""

import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.measure_rollover_sensitivity import _load, build_sensitivity


def _event(session_date: str, direction: str, value: float) -> dict:
    return {
        "session_date": session_date,
        "direction": direction,
        "fwd": {"3": value, "6": value, "10": value, "20": value},
    }


def _nf01(events: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "artifact": "nf01-pair-z-intersection-baselines",
        "signals": {
            "pair": {"targets": {"WIN$N": {"events": events}}},
        },
    }


def _rollover(excluded: list[str]) -> dict:
    return {
        "schema_version": "irai.rollover-audit.v1",
        "symbol": "WIN$N",
        "audit": {"excluded_sessions": excluded},
    }


def test_remove_eventos_da_janela_e_recalcula_media_e_win_rate():
    nf01 = _nf01([
        _event("2026-04-14", "buy", -30.0),
        _event("2026-04-15", "buy", -20.0),
        _event("2026-04-16", "sell", -10.0),
        _event("2026-04-20", "buy", 10.0),
        _event("2026-04-21", "sell", 20.0),
    ])

    report = build_sensitivity(
        nf01,
        _rollover(["2026-04-14", "2026-04-15", "2026-04-16"]),
        target="WIN$N",
        bootstrap_iterations=100,
    )

    pair = report["signals"]["pair"]
    assert pair["events_total"] == 5
    assert pair["events_excluded"] == 3
    assert pair["events_kept"] == 2
    h3 = pair["by_direction"]["all"]["horizons"]["3"]
    assert h3["with_rollover"]["mean_net_points"] == -6.0
    assert h3["without_rollover"]["mean_net_points"] == 15.0
    assert h3["without_rollover"]["win_rate_pct"] == 100.0
    assert h3["delta_mean_points"] == 21.0


def test_direcoes_sao_reportadas_separadamente():
    report = build_sensitivity(
        _nf01([
            _event("2026-04-15", "buy", -10.0),
            _event("2026-04-20", "buy", 5.0),
            _event("2026-04-21", "sell", 7.0),
        ]),
        _rollover(["2026-04-15"]),
        target="WIN$N",
        bootstrap_iterations=50,
    )

    by_direction = report["signals"]["pair"]["by_direction"]
    assert by_direction["buy"]["events_total"] == 2
    assert by_direction["buy"]["events_kept"] == 1
    assert by_direction["sell"]["events_total"] == 1
    assert by_direction["sell"]["events_kept"] == 1


def test_rejeita_auditoria_de_outro_ativo():
    rollover = _rollover([])
    rollover["symbol"] = "WDO$N"

    try:
        build_sensitivity(_nf01([]), rollover, target="WIN$N")
    except ValueError as exc:
        assert "WDO$N" in str(exc)
    else:
        raise AssertionError("auditoria WDO não pode ser aplicada a eventos WIN")


def test_carrega_artefato_nf01_compactado_em_gzip(tmp_path):
    artifact = _nf01([_event("2026-04-20", "buy", 10.0)])
    path = tmp_path / "nf01_pit.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(artifact, stream)

    assert _load(str(path)) == artifact
