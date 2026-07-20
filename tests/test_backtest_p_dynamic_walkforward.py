"""Contratos causais do backtest PIT de P Dinâmico."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_p_dynamic_walkforward import (
    build_observation,
    fingerprint_closed_snapshot,
    paired_brier_delta,
    parse_clock,
    split_replay_and_evaluation_dates,
)
from backend.irai.miqueias_static import (
    DEFAULT_CONFIG_PATH,
    load_miqueias_static_config,
)


def _factors():
    names = (
        "wdo", "di1", "brent", "btcusd", "us30", "usdmxn", "cadchf",
        "isharestreasury1-3+",
    )
    return {
        name: {"ret": 0.0, "z_score": 0.0, "contribution": 0.0}
        for name in names
    }


def _snapshot(timestamp, probability, *, opening=100.0, current=100.0):
    return SimpleNamespace(
        timestamp=timestamp,
        p_up=probability,
        win_open=opening,
        win_current=current,
        t_frac=0.25,
        factors=_factors(),
        is_ghost=False,
        is_preview=False,
    )


def _config():
    return load_miqueias_static_config(
        json.loads(Path(DEFAULT_CONFIG_PATH).read_text(encoding="utf-8"))
    )


def test_observacao_usa_ultimo_snapshot_comum_antes_do_horario_de_decisao():
    """Às 10:00, a M5 iniciada às 10:00 ainda não fechou."""
    v1 = [
        _snapshot("2026-07-16T15:00:00+00:00", 40.0),
        _snapshot("2026-07-16T15:55:00+00:00", 50.0),
        _snapshot("2026-07-16T16:00:00+00:00", 60.0),
        _snapshot("2026-07-16T23:55:00+00:00", 1.0, current=110.0),
    ]
    v2 = [
        _snapshot("2026-07-16T15:00:00+00:00", 45.0),
        _snapshot("2026-07-16T15:55:00+00:00", 55.0),
        _snapshot("2026-07-16T16:00:00+00:00", 65.0),
        _snapshot("2026-07-16T23:55:00+00:00", 99.0, current=110.0),
    ]

    observation = build_observation(
        "2026-07-16", v1, v2, decision_time=(10, 0), static_config=_config()
    )

    assert observation.decision_timestamp == "2026-07-16T15:55:00+00:00"
    assert observation.outcome_timestamp == "2026-07-16T23:55:00+00:00"
    assert observation.v1_pit == pytest.approx(0.50)
    assert observation.v2_pit == pytest.approx(0.55)
    assert observation.actual_up is True


def test_observacao_nao_extrapola_configuracao_estatica_antes_da_vigencia():
    v1 = [
        _snapshot("2026-06-22T15:00:00+00:00", 45.0),
        _snapshot("2026-06-22T16:00:00+00:00", 55.0, current=101.0),
    ]
    v2 = [
        _snapshot("2026-06-22T15:00:00+00:00", 46.0),
        _snapshot("2026-06-22T16:00:00+00:00", 56.0, current=101.0),
    ]

    observation = build_observation(
        "2026-06-22", v1, v2, decision_time=(10, 0), static_config=_config()
    )

    assert observation.miqueias_static_disclosed is None


def test_observacao_recusa_outcome_com_precos_divergentes_entre_v1_e_v2():
    v1 = [
        _snapshot("2026-07-16T15:00:00+00:00", 45.0),
        _snapshot("2026-07-16T16:00:00+00:00", 55.0, current=101.0),
    ]
    v2 = [
        _snapshot("2026-07-16T15:00:00+00:00", 46.0),
        _snapshot("2026-07-16T16:00:00+00:00", 56.0, current=99.0),
    ]

    with pytest.raises(ValueError, match="divergem"):
        build_observation(
            "2026-07-16", v1, v2, decision_time=(10, 0), static_config=_config()
        )


def test_observacao_ignora_barra_posterior_ao_pregao_no_desfecho():
    """Uma barra após 18:00 BRT não pode inverter o rótulo do pregão."""
    v1 = [
        _snapshot("2026-07-16T15:00:00+00:00", 45.0),
        _snapshot("2026-07-16T16:00:00+00:00", 55.0),
        _snapshot("2026-07-16T23:55:00+00:00", 60.0, current=101.0),
        _snapshot("2026-07-17T00:30:00+00:00", 99.0, current=99.0),
    ]
    v2 = [
        _snapshot("2026-07-16T15:00:00+00:00", 46.0),
        _snapshot("2026-07-16T16:00:00+00:00", 56.0),
        _snapshot("2026-07-16T23:55:00+00:00", 61.0, current=101.0),
        _snapshot("2026-07-17T00:30:00+00:00", 1.0, current=99.0),
    ]

    observation = build_observation(
        "2026-07-16", v1, v2, decision_time=(10, 0), static_config=_config()
    )

    assert observation.outcome_timestamp == "2026-07-16T23:55:00+00:00"
    assert observation.actual_up is True


def test_delta_pareado_e_por_sessao_e_reporta_sinal_da_v2():
    observations = [
        SimpleNamespace(actual_up=True, v1_pit=0.55, v2_pit=0.75),
        SimpleNamespace(actual_up=False, v1_pit=0.45, v2_pit=0.25),
    ]

    comparison = paired_brier_delta(
        observations, left="v2_pit", right="v1_pit", iterations=100, seed=7
    )

    assert comparison["sessions"] == 2
    assert comparison["delta_brier"] < 0


def test_parse_clock_recusa_horario_fora_do_pregao():
    assert parse_clock("10:00") == (10, 0)
    with pytest.raises(ValueError, match="09:00"):
        parse_clock("18:00")


def test_recorte_de_medicao_nao_remove_aquecimento_do_kalman():
    replay, evaluation = split_replay_and_evaluation_dates(
        ["2026-06-23", "2026-06-24", "2026-07-01", "2026-07-02"],
        start_date="2026-07-01",
        end_date="2026-07-01",
    )

    assert replay == ["2026-06-23", "2026-06-24", "2026-07-01"]
    assert evaluation == ["2026-07-01"]


def test_snapshot_exige_wal_checkpointado_antes_do_replay(tmp_path):
    snapshot = tmp_path / "irai.db"
    snapshot.write_bytes(b"sqlite-snapshot")
    snapshot.with_name("irai.db-wal").write_bytes(b"transacao-pendente")

    with pytest.raises(ValueError, match="não checkpointado"):
        fingerprint_closed_snapshot(str(snapshot))
