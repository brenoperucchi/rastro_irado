"""Specs da sensibilidade NF-01 com/sem sessões de rollover."""

import gzip
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.measure_rollover_sensitivity import (
    _load,
    build_sensitivity,
    validate_rollover_artifact,
)


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
        "continuous_method": "liquidity_continuous_unadjusted",
        "database_fingerprint": {"size_bytes": 1, "sha256": "a" * 64},
        "mt5_capture": {
            "schema_version": "irai.mt5-continuous-metadata.v1",
            "captured_at": "2026-07-20T22:55:35+00:00",
            "terminal": {
                "connected": True,
                "requested_executable": r"E:\MetaTradersWSL\wdowin\irai\terminal64.exe",
                "data_path": r"E:\MetaTradersWSL\wdowin\irai",
                "path": r"E:\MetaTradersWSL\wdowin\irai",
            },
            "symbols": {
                "WIN$N": {
                    "name": "WIN$N",
                    "description": "IBOVESPA MINI - Por Liquidez - Sem Ajustes",
                }
            },
        },
        "audit": {
            "first_session": "2026-04-01",
            "last_session": "2026-04-30",
            "window_sessions_each_side": 1,
            "excluded_sessions": excluded,
        },
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


def test_cli_e_executavel_a_partir_da_raiz_do_repositorio():
    result = subprocess.run(
        [sys.executable, "scripts/measure_rollover_sensitivity.py", "--help"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--rollover-artifact" in result.stdout


def test_artefato_win_versionado_atende_contrato_de_proveniencia():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    artifact = _load(os.path.join(root, "docs/artifacts/irai-5/win-rollover-audit-v1.json"))

    validated = validate_rollover_artifact(artifact, "WIN$N")

    assert validated["audit"]["first_session"] <= validated["audit"]["last_session"]
    assert validated["mt5_capture"]["symbols"]["WIN$N"]["name"] == "WIN$N"


def test_recusa_auditoria_sem_metodo_continuo_qualificado():
    rollover = _rollover([])
    rollover["continuous_method"] = "unknown"

    try:
        build_sensitivity(_nf01([]), rollover, target="WIN$N")
    except ValueError as exc:
        assert "liquidez" in str(exc)
    else:
        raise AssertionError("método contínuo desconhecido não pode alimentar sensibilidade")


def test_recusa_auditoria_sem_captura_mt5_reproduzivel():
    rollover = _rollover([])
    del rollover["mt5_capture"]

    try:
        build_sensitivity(_nf01([]), rollover, target="WIN$N")
    except ValueError as exc:
        assert "captura MT5" in str(exc)
    else:
        raise AssertionError("sensibilidade exige proveniência MT5")


def test_recusa_metodo_qualificado_quando_descricao_mt5_nao_prova_sem_ajustes():
    rollover = _rollover([])
    rollover["mt5_capture"]["symbols"]["WIN$N"]["description"] = (
        "IBOVESPA MINI - Por Liquidez (WINQ26)"
    )

    try:
        build_sensitivity(_nf01([]), rollover, target="WIN$N")
    except ValueError as exc:
        assert "captura MT5" in str(exc)
    else:
        raise AssertionError(
            "continuous_method externo não pode substituir a descrição MT5"
        )


def test_recusa_evento_nf01_fora_do_intervalo_da_auditoria():
    rollover = _rollover([])
    rollover["audit"]["last_session"] = "2026-04-20"

    try:
        build_sensitivity(
            _nf01([_event("2026-04-21", "buy", 10.0)]),
            rollover,
            target="WIN$N",
        )
    except ValueError as exc:
        assert "fora do intervalo" in str(exc)
    else:
        raise AssertionError("evento fora da auditoria não pode ser mensurado")
