"""Regressões do Gate 3 discriminante do Tactical Layer."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import scripts.measure_tactical_gate3 as gate3
from scripts.measure_tactical_gate3 import (
    GateBar,
    ForwardRow,
    bootstrap_auc,
    bootstrap_auc_delta,
    clustered_spearman,
    fit_hourly_platt,
    fit_nested_models,
    hourly_auc_comparison,
    select_evaluation_dates,
)


def test_bootstrap_auc_reamostra_sessoes_inteiras_e_e_deterministico():
    rows = [
        ("s1", 0.9, True),
        ("s1", 0.8, True),
        ("s2", 0.2, False),
        ("s2", 0.1, False),
        ("s3", 0.6, True),
        ("s4", 0.4, False),
    ]

    first = bootstrap_auc(rows, iterations=500, seed=7)
    second = bootstrap_auc(rows, iterations=500, seed=7)

    assert first == second
    assert first.value == pytest.approx(1.0)
    assert first.ci_low == pytest.approx(1.0)
    assert first.ci_high == pytest.approx(1.0)


def test_delta_auc_preserva_pareamento_dos_dois_modelos():
    rows = [
        ("s1", 0.9, 0.6, True),
        ("s2", 0.4, 0.8, False),
        ("s3", 0.7, 0.7, True),
        ("s4", 0.1, 0.3, False),
    ]

    result = bootstrap_auc_delta(rows, iterations=1_000, seed=11)

    assert result.value > 0
    assert result.n_sessions == 4


def test_ic_clusterizado_nao_trata_barras_da_mesma_sessao_como_independentes():
    rows = {
        "s1": [(1.0, 1.0)] * 80,
        "s2": [(2.0, -2.0)] * 80,
        "s3": [(3.0, 3.0)] * 80,
    }

    result = clustered_spearman(rows, iterations=500, seed=19)

    assert result.n_sessions == 3
    assert result.ci_low < result.value < result.ci_high


def test_platt_por_hora_ignora_explicitamente_sessoes_pos_cutoff():
    train = [
        GateBar("2026-04-29", 9, 100.0, 0.2, False, 0.0),
        GateBar("2026-04-30", 9, 101.0, 0.8, True, 0.0),
    ]
    futuro = [
        GateBar("2026-05-04", 9, 102.0, 0.99, False, 0.0),
        GateBar("2026-05-05", 9, 103.0, 0.01, True, 0.0),
    ]

    original = fit_hourly_platt(train, cutoff="2026-04-30")
    envenenado = fit_hourly_platt(train + futuro, cutoff="2026-04-30")

    assert original[9].coef_ == pytest.approx(envenenado[9].coef_)
    assert original[9].intercept_ == pytest.approx(envenenado[9].intercept_)


def test_modelos_aninhados_ignoram_sessoes_pos_cutoff():
    treino = [
        ForwardRow(f"2026-04-{day:02d}", 3, ret, ret > 0, (mom,), (macro, 0.0, 0.0, 0.0))
        for day, ret, mom, macro in (
            (25, -0.01, -1.0, -0.8),
            (26, 0.02, 1.0, 0.9),
            (27, -0.02, -0.5, -0.4),
            (28, 0.01, 0.5, 0.6),
        )
    ]
    futuro = [
        ForwardRow("2026-05-04", 3, -1.0, False, (1e9,), (1e9, 1e9, 1e9, 1e9)),
        ForwardRow("2026-05-05", 3, 1.0, True, (-1e9,), (-1e9, -1e9, -1e9, -1e9)),
    ]

    base_a, macro_a = fit_nested_models(treino, "2026-04-30")
    base_b, macro_b = fit_nested_models(treino + futuro, "2026-04-30")

    assert base_a.means == pytest.approx(base_b.means)
    assert base_a.model.coef_ == pytest.approx(base_b.model.coef_)
    assert macro_a.means == pytest.approx(macro_b.means)
    assert macro_a.model.coef_ == pytest.approx(macro_b.model.coef_)


def test_selecao_da_segunda_janela_nao_vaza_maio_em_diante():
    dates = (
        "2026-02-27", "2026-03-02", "2026-04-30", "2026-05-04",
    )

    assert select_evaluation_dates(
        dates, cutoff="2026-02-27", eval_start="2026-03-01", eval_end="2026-04-30"
    ) == ["2026-03-02", "2026-04-30"]


def test_replay_v1_preserva_versao_e_nunca_persiste_estado(monkeypatch):
    calls = []
    snapshots = [
        SimpleNamespace(
            timestamp="2026-03-02T14:00:00+00:00", win_open=100.0,
            win_current=100.0, p_up=45.0, price_diverge_z=0.0, is_ghost=False,
        ),
        SimpleNamespace(
            timestamp="2026-03-02T15:00:00+00:00", win_open=100.0,
            win_current=101.0, p_up=55.0, price_diverge_z=0.0, is_ghost=False,
        ),
    ]

    class FakeEngine:
        target_slugs = {"WIN$N": "win"}
        models = {"win": {"sigmas": {"kalman_trans_cov": 9.0}}}

        def compute_from_db(self, date, **kwargs):
            calls.append((date, kwargs))
            return snapshots

    @contextmanager
    def fake_readonly_engine(*_args, **_kwargs):
        yield FakeEngine()

    monkeypatch.setattr(gate3, "readonly_engine", fake_readonly_engine)
    monkeypatch.setattr(gate3, "load_target_ohlc", lambda *_args: {
        "2026-03-02": [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
            {"open": 100.0, "high": 102.0, "low": 100.0, "close": 101.0},
        ]
    })
    calibration = {
        "factors": [], "factor_labels": {}, "weights": {}, "sigmas": {},
        "alpha": 1.0, "intercept": 0.0,
    }

    sessions, discarded = gate3.replay_bars(
        "snapshot.db", "WIN$N", ["2026-03-02"], {}, calibration, version="v1"
    )

    assert not discarded
    assert sessions["2026-03-02"]
    assert calls == [("2026-03-02", {
        "target": "WIN$N", "version": "v1", "persist_state": False,
    })]


def test_auc_horaria_compara_v2_v1_e_retorno_desde_abertura():
    v2 = [
        GateBar("s1", 16, 110.0, 0.90, True, 0.0, 100.0),
        GateBar("s2", 16, 90.0, 0.10, False, 0.0, 100.0),
        GateBar("s3", 16, 105.0, 0.80, True, 0.0, 100.0),
        GateBar("s4", 16, 95.0, 0.20, False, 0.0, 100.0),
    ]
    v1 = [
        GateBar("s1", 16, 110.0, 0.10, True, 0.0, 100.0),
        GateBar("s2", 16, 90.0, 0.20, False, 0.0, 100.0),
        GateBar("s3", 16, 105.0, 0.40, True, 0.0, 100.0),
        GateBar("s4", 16, 95.0, 0.30, False, 0.0, 100.0),
    ]

    auc_v2, auc_v1, auc_return = hourly_auc_comparison(
        v2, v1, iterations=100, seed=31
    )

    assert auc_v2.value == pytest.approx(1.0)
    assert auc_v1.value == pytest.approx(0.5)
    assert auc_return.value == pytest.approx(1.0)


def _opening_bars(count=30):
    return [
        GateBar(
            "2026-05-04", 9 + index // 12, 100.0 + index,
            0.45 + index / 1_000, True, 0.1, 100.0,
        )
        for index in range(count)
    ]


def test_horizonte_curto_preserva_as_primeiras_barras_da_abertura():
    """h=3 não pode herdar o warm-up de h=20 da mesma rodada."""
    rows = gate3.forward_rows(_opening_bars(), horizon=3)

    assert [row.bar_index for row in rows[:3]] == [0, 1, 2]
    assert len(rows) == 27


def test_both_roda_o_teste_decisorio_nos_dois_bracos():
    assert gate3.versions_to_replay("both") == ("v1", "v2")
    assert gate3.versions_to_replay("v1") == ("v1",)


def test_faixas_da_abertura_sao_cumulativas_e_incluem_09h_10h():
    rows = gate3.forward_rows(_opening_bars(), horizon=3)

    assert len(gate3.rows_in_scope(rows, "OPEN_3")) == 3
    assert len(gate3.rows_in_scope(rows, "OPEN_6")) == 6
    assert len(gate3.rows_in_scope(rows, "OPEN_12")) == 12
    assert len(gate3.rows_in_scope(rows, "OPEN_20")) == 20
    assert len(gate3.rows_in_scope(rows, "09_10")) == 24
    assert len(gate3.rows_in_scope(rows, "11_18")) == 3


def test_crossfit_temporal_nunca_calibra_com_a_propria_fold():
    folds = gate3.temporal_crossfit_slices(
        [f"2026-01-{day:02d}" for day in range(1, 13)], n_splits=3
    )

    assert len(folds) == 3
    for cutoff, dates in folds:
        assert cutoff < min(dates)
    assert [date for _, dates in folds for date in dates] == [
        f"2026-01-{day:02d}" for day in range(1, 13)
    ]


def test_residualizacao_e_ajustada_sem_observar_a_janela_oos():
    train = [
        ForwardRow(f"2026-04-{day:02d}", 3, ret, ret > 0, (mom,), (mom + noise,))
        for day, ret, mom, noise in (
            (25, -0.01, -2.0, -0.1),
            (26, 0.02, -1.0, 0.1),
            (27, -0.02, 1.0, -0.1),
            (28, 0.01, 2.0, 0.1),
        )
    ]
    future = ForwardRow("2026-05-04", 3, 1.0, True, (1e9,), (-1e9,))

    original = gate3.fit_macro_residualizer(train, cutoff="2026-04-30")
    poisoned = gate3.fit_macro_residualizer(train + [future], cutoff="2026-04-30")

    assert original.model.coef_ == pytest.approx(poisoned.model.coef_)
    assert original.model.intercept_ == pytest.approx(poisoned.model.intercept_)


def test_rotulo_multinomial_aplica_custo_e_atr_em_pontos():
    assert gate3.multinomial_label(0.00005, close=100_000, atr14_points=20, cost_points=10) == 0
    assert gate3.multinomial_label(0.00020, close=100_000, atr14_points=20, cost_points=10) == 1
    assert gate3.multinomial_label(-0.00020, close=100_000, atr14_points=20, cost_points=10) == -1


def test_poder_prospectivo_cresce_quando_o_efeito_minimo_encolhe():
    larger_effect = gate3.required_sessions_for_power(
        current_sessions=40, bootstrap_standard_error=0.03, minimum_delta_auc=0.03
    )
    smaller_effect = gate3.required_sessions_for_power(
        current_sessions=40, bootstrap_standard_error=0.03, minimum_delta_auc=0.02
    )

    assert larger_effect < smaller_effect
