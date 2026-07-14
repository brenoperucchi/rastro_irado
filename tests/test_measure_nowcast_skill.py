"""Regressões do Gate 2: skill do nowcast de direção da sessão."""

import pytest

from scripts.measure_nowcast_skill import (
    SessionPoint,
    bootstrap_loss_delta,
    fit_historical_baseline,
    label_session,
    select_oos_dates,
)


def test_janela_oos_e_estritamente_posterior_ao_cutoff_e_descarta_a_ultima():
    dates = ["2026-04-29", "2026-04-30", "2026-05-04", "2026-05-05"]

    assert select_oos_dates(dates, cutoff="2026-04-30") == ("2026-05-04",)


def test_rotulo_usa_open_da_primeira_barra_e_empate_e_baixa():
    assert label_session(100.0, 101.0)
    assert not label_session(100.0, 100.0)
    assert not label_session(100.0, 99.0)


def test_baseline_e_ajustado_so_com_sessoes_anteriores_ao_cutoff():
    points = [
        SessionPoint("train-up", 9, 0.01, 0.7, True),
        SessionPoint("train-down", 9, 0.01, 0.7, False),
        SessionPoint("future", 9, 0.01, 0.7, True),
    ]
    session_dates = {
        "train-up": "2026-04-29",
        "train-down": "2026-04-30",
        "future": "2026-05-04",
    }

    baseline = fit_historical_baseline(points, session_dates, cutoff="2026-04-30")

    # Beta(1,1): (1 ALTA + 1) / (2 observações + 2).
    assert baseline.probability(9, 0.01) == pytest.approx(0.5)
    assert baseline.climatology == pytest.approx(0.5)


def test_climatologia_conta_cada_sessao_uma_vez_mesmo_com_barras_desiguais():
    points = [
        SessionPoint("up", 9, 0.01, 0.7, True),
        SessionPoint("up", 9, 0.02, 0.8, True),
        SessionPoint("up", 9, 0.03, 0.9, True),
        SessionPoint("down", 9, -0.01, 0.3, False),
    ]
    dates = {"up": "2026-04-29", "down": "2026-04-30"}

    baseline = fit_historical_baseline(points, dates, cutoff="2026-04-30")

    assert baseline.climatology == pytest.approx(0.5)


def test_bootstrap_de_loss_reamostra_sessoes_e_preserva_pareamento():
    # O modelo ganha 0,2 em uma sessão e perde 0,2 na outra. Com duas sessões,
    # o IC precisa refletir N=2, não as 200 barras correlacionadas.
    by_session = {
        "s1": [(0.1, 0.3)] * 100,
        "s2": [(0.3, 0.1)] * 100,
    }

    first = bootstrap_loss_delta(by_session, iterations=2_000, seed=7)
    second = bootstrap_loss_delta(by_session, iterations=2_000, seed=7)

    assert first == second
    assert first.delta == pytest.approx(0.0)
    assert first.ci_low == pytest.approx(-0.2)
    assert first.ci_high == pytest.approx(0.2)
    assert not first.significant
