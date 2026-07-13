"""Regressões da calibração robusta e do holdout temporal."""

import importlib.util
import os

import numpy as np
import pandas as pd
import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "calibrate_universal_robustness",
    os.path.join(ROOT, "scripts", "calibrate_universal.py"),
)
calibrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calibrator)


def test_ridge_estabiliza_coeficientes_de_cesta_colinear():
    rng = np.random.default_rng(42)
    base = rng.normal(size=180)
    x = np.column_stack(
        [base, base + rng.normal(scale=1e-9, size=180), rng.normal(size=180)]
    )
    y = 0.8 * base + 0.2 * x[:, 2] + rng.normal(scale=0.02, size=180)
    y_perturbado = y.copy()
    y_perturbado[-1] += 1e-5

    beta_ols = np.linalg.lstsq(np.column_stack([x, np.ones(len(x))]), y, rcond=None)[0]
    beta_ols_perturbado = np.linalg.lstsq(
        np.column_stack([x, np.ones(len(x))]), y_perturbado, rcond=None
    )[0]
    ridge = calibrator.fit_ridge(x, y, alpha=1.0)
    ridge_perturbado = calibrator.fit_ridge(x, y_perturbado, alpha=1.0)

    assert np.linalg.norm(beta_ols[:3] - beta_ols_perturbado[:3]) > 10
    assert np.linalg.norm(ridge.coef - ridge_perturbado.coef) < 1e-4
    assert np.isfinite(ridge.coef).all()


def test_guard_usa_condicao_da_matriz_original_nao_das_equacoes_normais():
    rng = np.random.default_rng(7)
    base = rng.normal(size=120)
    colinear = np.column_stack([base, base + rng.normal(scale=1e-5, size=120)])
    independente = np.column_stack([base, rng.normal(size=120)])

    assert calibrator.design_condition_number(colinear) > calibrator.MAX_CONDITION_NUMBER
    assert calibrator.design_condition_number(independente) < calibrator.MAX_CONDITION_NUMBER


def test_descarta_ultima_sessao_antes_de_limitar_a_janela():
    dates = pd.date_range("2025-01-01", periods=260, freq="B").date
    frame = pd.DataFrame({"target": np.arange(260)}, index=dates)

    complete, discarded = calibrator.discard_latest_session(frame)

    assert discarded == dates[-1]
    assert dates[-1] not in complete.index
    assert complete.index[-1] == dates[-2]
    assert len(complete.iloc[-252:]) == 252


def test_alpha_e_escolhido_sem_consultar_holdout(monkeypatch):
    rng = np.random.default_rng(9)
    x_train = rng.normal(size=(120, 3))
    y_train = x_train[:, 0] + rng.normal(scale=0.1, size=120)
    x_holdout = np.full((20, 3), 999_999.0)
    y_holdout = np.full(20, -999_999.0)

    alpha_a = calibrator.choose_ridge_alpha(x_train, y_train)
    alpha_b = calibrator.choose_ridge_alpha(x_train, y_train)

    assert alpha_a == alpha_b
    assert x_holdout.max() == pytest.approx(999_999.0)
    assert y_holdout.min() == pytest.approx(-999_999.0)
